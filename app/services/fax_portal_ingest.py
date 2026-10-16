from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.fax_portal import (
    CCFpComprehendPayload,
    CCFpFax,
    CCFpFaxDocument,
    CCFpFaxInsurance,
    CCFpFaxPatient,
    CCFpFaxProvider,
    CCFpProvider,
    CCFpReferralBoard,
    CCFpReferralStatusHistory,
    CCFpStatus,
)
from app.services.comprehend_parser import (
    ParsedFax,
    ReferrerGuess,
    parse_payload,
    pick_referrer,
    split_patient_name,
)
from app.services.fax_portal_activity import log_activity
from app.services.fax_portal_assignment import assign_new_referral

# Never log payload contents, names, DOBs or phone numbers here: only ids,
# counts and exception class names. The payload lives in cc_fp_comprehend_payload.
logger = logging.getLogger(__name__)

INITIAL_STATUS = "review_required"
MAX_ERROR_CHARS = 2000


@dataclass
class IngestResult:
    payload_id: int
    fax_id: int | None
    referral_ids: list[int]
    created: bool
    parse_error: str | None = None


# ---------------------------------------------------------------------
# Step 1: store the raw delivery (always, before any parsing)
# ---------------------------------------------------------------------


def store_payload(db: Session, raw_body: bytes) -> CCFpComprehendPayload:
    """Writes the delivery to cc_fp_comprehend_payload and commits.

    Runs before any parsing so nothing is ever lost: a body that is not valid
    JSON (or not a JSON object) is kept wrapped under "_unparsed_body" with
    parse_error set. Every delivery gets its own row, retries included; the
    sha256 of the exact bytes shows identical redeliveries.
    """
    sha256 = hashlib.sha256(raw_body).hexdigest()
    parse_error: str | None = None
    payload: Any

    text = raw_body.decode("utf-8", errors="replace")

    try:
        payload = json.loads(text)
    except ValueError:
        payload = None

    if not isinstance(payload, dict):
        parse_error = "Request body is not a JSON object."
        payload = {"_unparsed_body": text}

    payload = _strip_nul(payload)

    job_id = _job_id_of(payload)

    row = CCFpComprehendPayload(
        job_id=job_id,
        payload=payload,
        payload_sha256=sha256,
        parse_error=parse_error,
    )

    db.add(row)
    db.commit()

    return row


# ---------------------------------------------------------------------
# Step 2: parse into the cc_fp_ tables
# ---------------------------------------------------------------------


def process_payload(db: Session, payload_id: int) -> IngestResult:
    """Parses a stored delivery into fax / patient / provider / document /
    insurance rows and one board card per patient, in a single transaction.

    Idempotent on the Comprehend Job ID: a retry of an already-ingested fax
    links the delivery to the existing fax and creates nothing new. If
    parsing fails, the delivery keeps its parse_error and a bare fallback card
    is still created in Review Required, so a reviewer sees the fax instead
    of it vanishing silently.
    """
    row = db.get(CCFpComprehendPayload, payload_id)

    if row is None:
        raise ValueError(f"Comprehend payload {payload_id} does not exist.")

    payload = row.payload if isinstance(row.payload, dict) else {}

    if row.parse_error and "_unparsed_body" in payload:
        return _ingest_fallback(db, row, row.parse_error)

    try:
        return _ingest(db, row, payload)
    except Exception as exc:
        db.rollback()

        # Class name only: driver messages can echo field values (PHI).
        logger.error(
            "Comprehend payload %s failed to parse (%s).",
            payload_id,
            type(exc).__name__,
        )

        return _ingest_fallback(db, row, _describe(exc))


def _ingest(db: Session, row: CCFpComprehendPayload, payload: dict[str, Any]) -> IngestResult:
    parsed = parse_payload(payload)
    job_id = _job_id_of(payload) or _synthetic_job_id(row.payload_sha256)

    _lock_job(db, job_id)

    existing = db.scalar(select(CCFpFax).where(CCFpFax.job_id == job_id))

    if existing is not None:
        row.fax_id = existing.id
        row.parsed_at = func.now()
        row.parse_error = None
        db.commit()

        return IngestResult(
            payload_id=row.id,
            fax_id=existing.id,
            referral_ids=_referral_ids(db, existing.id),
            created=False,
        )

    fax = CCFpFax(
        job_id=job_id,
        **_fit(
            CCFpFax, {**parsed.fax, "received_at": parsed.fax["received_at"] or row.received_at}
        ),
    )
    db.add(fax)
    db.flush()

    patient_ids = _insert_extracted_rows(db, fax.id, parsed)
    referral_ids = _create_cards(db, fax, parsed, patient_ids)

    log_activity(
        db,
        section="system",
        action="fax_ingested",
        fax_id=fax.id,
        details={
            "job_id": job_id,
            "payload_id": row.id,
            "patients": len(parsed.patients),
            "cards": len(referral_ids),
        },
    )

    row.fax_id = fax.id
    row.parsed_at = func.now()
    row.parse_error = None

    db.commit()

    return IngestResult(
        payload_id=row.id,
        fax_id=fax.id,
        referral_ids=referral_ids,
        created=True,
    )


def _insert_extracted_rows(db: Session, fax_id: int, parsed: ParsedFax) -> dict[int, int]:
    """Inserts the patient, provider, document and insurance rows in
    dependency order. Returns {patient idx: cc_fp_fax_patient.id}."""
    patient_ids: dict[int, int] = {}

    for data in parsed.patients:
        patient = CCFpFaxPatient(fax_id=fax_id, **_fit(CCFpFaxPatient, data))
        db.add(patient)
        db.flush()
        patient_ids[data["idx"]] = patient.id

    for data in parsed.providers:
        db.add(CCFpFaxProvider(fax_id=fax_id, **_fit(CCFpFaxProvider, data)))

    db.flush()

    for data in parsed.documents:
        db.add(CCFpFaxDocument(fax_id=fax_id, **_fit(CCFpFaxDocument, data)))

    for data in parsed.insurance:
        db.add(CCFpFaxInsurance(fax_id=fax_id, **_fit(CCFpFaxInsurance, data)))

    db.flush()

    return patient_ids


def _create_cards(
    db: Session,
    fax: CCFpFax,
    parsed: ParsedFax,
    patient_ids: dict[int, int],
) -> list[int]:
    """One card per patient on the fax; a fax with no patient still gets one
    card so a reviewer can mark it Not a referral."""
    referral_ids: list[int] = []

    patients: list[dict[str, Any] | None] = list(parsed.patients) or [None]

    for patient in patients:
        patient_idx = patient["idx"] if patient else None
        guess = pick_referrer(parsed, patient_idx)
        directory_id = _upsert_directory_provider(db, guess)

        if directory_id is not None and guess.provider is not None:
            db.execute(
                CCFpFaxProvider.__table__.update()
                .where(
                    CCFpFaxProvider.fax_id == fax.id,
                    CCFpFaxProvider.idx == guess.provider["idx"],
                )
                .values(provider_id=directory_id)
            )

        first_name, last_name = split_patient_name(patient["full_name"] if patient else None)

        card = CCFpReferralBoard(
            fax_id=fax.id,
            fax_patient_id=patient_ids.get(patient_idx) if patient_idx is not None else None,
            provider_id=directory_id,
            status=INITIAL_STATUS,
            is_urgent=bool(fax.is_urgent),
            **_fit(
                CCFpReferralBoard,
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "phone": patient["phone"] if patient else None,
                    "phone_e164": patient["phone_e164"] if patient else None,
                    "gender": patient["gender"] if patient else None,
                    "dob": patient["dob"] if patient else None,
                    "address": patient["address"] if patient else None,
                    "physician_name": guess.provider["name"] if guess.provider else None,
                    "physician_fax_no": guess.fax_number,
                    "physician_email": guess.email,
                },
            ),
        )

        card.duplicate_of_referral_id = _find_possible_duplicate(db, fax.id, card)

        db.add(card)
        db.flush()

        db.add(
            CCFpReferralStatusHistory(
                referral_id=card.id,
                from_status=None,
                to_status=INITIAL_STATUS,
                changed_by_user_id=None,
                source="ingest",
            )
        )

        assigned_to = assign_new_referral(db, card)

        log_activity(
            db,
            section="system",
            action="referral_created",
            referral_id=card.id,
            fax_id=fax.id,
            details={
                "assignment": "auto" if assigned_to is not None else "unassigned",
                "possible_duplicate": card.duplicate_of_referral_id is not None,
            },
        )

        referral_ids.append(card.id)

    return referral_ids


def _upsert_directory_provider(db: Session, guess: ReferrerGuess) -> int | None:
    """Finds or creates the deduplicated cc_fp_provider row for the referrer.

    Dedupe key is the NPI when present, otherwise the fax number plus the
    name (a practice's single fax line serves several doctors), otherwise the
    name alone when the provider has neither.
    """
    person = guess.provider

    if person is None:
        return None

    sender = guess.sender if guess.sender is not person else None

    def pick(field: str) -> Any:
        return person.get(field) or (guess.sender or {}).get(field)

    values = _fit(
        CCFpProvider,
        {
            "npi": person["npi"],
            "name": person["name"],
            "organization": sender["name"] if sender else None,
            "specialty": pick("specialty"),
            "phone": pick("phone"),
            "fax_e164": pick("fax_e164"),
            "email": pick("email"),
            "address1": pick("address1"),
            "address2": pick("address2"),
            "city": pick("city"),
            "state": pick("state"),
            "zip": pick("zip"),
        },
    )

    existing: CCFpProvider | None = None

    if values.get("npi"):
        # Concurrent faxes from the same NPI must not race on the unique key.
        db.execute(
            pg_insert(CCFpProvider).values(**values).on_conflict_do_nothing(index_elements=["npi"])
        )
        existing = db.scalar(select(CCFpProvider).where(CCFpProvider.npi == values["npi"]))

        if existing is not None:
            return existing.id

    name_matches = func.lower(CCFpProvider.name) == values["name"].lower()

    if values.get("fax_e164"):
        existing = db.scalar(
            select(CCFpProvider)
            .where(CCFpProvider.fax_e164 == values["fax_e164"], name_matches)
            .order_by(CCFpProvider.id)
            .limit(1)
        )
    else:
        existing = db.scalar(
            select(CCFpProvider)
            .where(
                CCFpProvider.npi.is_(None),
                CCFpProvider.fax_e164.is_(None),
                name_matches,
            )
            .order_by(CCFpProvider.id)
            .limit(1)
        )

    if existing is not None:
        # Fill gaps only; never overwrite what an earlier fax (or a person) set.
        for key, value in values.items():
            if getattr(existing, key, None) is None and value is not None:
                setattr(existing, key, value)

        db.flush()

        return existing.id

    provider = CCFpProvider(**values)
    db.add(provider)
    db.flush()

    return provider.id


def _find_possible_duplicate(db: Session, fax_id: int, card: CCFpReferralBoard) -> int | None:
    """Flags (never blocks) a card whose phone and last name match an open
    card from an earlier fax."""
    if not card.phone_e164 or not card.last_name:
        return None

    return db.scalar(
        select(CCFpReferralBoard.id)
        .join(CCFpStatus, CCFpStatus.code == CCFpReferralBoard.status)
        .where(
            CCFpReferralBoard.fax_id != fax_id,
            CCFpReferralBoard.phone_e164 == card.phone_e164,
            func.lower(CCFpReferralBoard.last_name) == card.last_name.lower(),
            CCFpStatus.is_open.is_(True),
        )
        .order_by(CCFpReferralBoard.id.desc())
        .limit(1)
    )


# ---------------------------------------------------------------------
# Fallback when parsing fails
# ---------------------------------------------------------------------


def _ingest_fallback(db: Session, row: CCFpComprehendPayload, error: str) -> IngestResult:
    """Keeps the failure on the delivery and still puts one bare card in
    Review Required, so the fax is visible to a reviewer."""
    job_id = row.job_id or _synthetic_job_id(row.payload_sha256)

    _lock_job(db, job_id)

    fax = db.scalar(select(CCFpFax).where(CCFpFax.job_id == job_id))
    created = fax is None

    if fax is None:
        fax = CCFpFax(job_id=job_id, received_at=row.received_at)
        db.add(fax)
        db.flush()

        card = CCFpReferralBoard(fax_id=fax.id, status=INITIAL_STATUS)
        db.add(card)
        db.flush()

        db.add(
            CCFpReferralStatusHistory(
                referral_id=card.id,
                from_status=None,
                to_status=INITIAL_STATUS,
                source="ingest",
                note="Created without extracted data: the Comprehend payload could not be parsed.",
            )
        )

        assign_new_referral(db, card)

        log_activity(
            db,
            section="system",
            action="fax_ingest_failed",
            referral_id=card.id,
            fax_id=fax.id,
            details={"job_id": job_id, "payload_id": row.id},
        )

    # parsed_at stays NULL so unparsed deliveries are easy to find and re-parse.
    row.fax_id = fax.id
    row.parse_error = error[:MAX_ERROR_CHARS]

    db.commit()

    return IngestResult(
        payload_id=row.id,
        fax_id=fax.id,
        referral_ids=_referral_ids(db, fax.id),
        created=created,
        parse_error=row.parse_error,
    )


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _lock_job(db: Session, job_id: str) -> None:
    """Serialises concurrent deliveries of the same Job ID (a retry racing
    the original) for the rest of the transaction."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(job_id, 0))))


def _referral_ids(db: Session, fax_id: int) -> list[int]:
    return list(
        db.scalars(
            select(CCFpReferralBoard.id)
            .where(CCFpReferralBoard.fax_id == fax_id)
            .order_by(CCFpReferralBoard.id)
        )
    )


def _fit(model: type, data: dict[str, Any]) -> dict[str, Any]:
    """Truncates string values to their VARCHAR length so one over-long
    extracted value cannot fail the whole fax."""
    fitted: dict[str, Any] = {}

    for key, value in data.items():
        length = getattr(model.__table__.c[key].type, "length", None)

        if isinstance(value, str) and length is not None:
            value = value[:length]

        fitted[key] = value

    return fitted


def _strip_nul(value: Any) -> Any:
    """JSONB cannot store U+0000, which would make the insert (and so every
    retry of the same delivery) fail."""
    if isinstance(value, str):
        return value.replace("\x00", "")

    if isinstance(value, list):
        return [_strip_nul(item) for item in value]

    if isinstance(value, dict):
        return {_strip_nul(key): _strip_nul(item) for key, item in value.items()}

    return value


def _job_id_of(payload: dict[str, Any]) -> str | None:
    value = payload.get("Job ID")

    if isinstance(value, str | int) and not isinstance(value, bool):
        text = str(value).strip()

        return text[:64] or None

    return None


def _synthetic_job_id(sha256: str) -> str:
    """Stand-in dedupe key for a delivery that has no Job ID, so identical
    redeliveries still collapse into one fax."""
    return f"missing-{sha256[:32]}"


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"
