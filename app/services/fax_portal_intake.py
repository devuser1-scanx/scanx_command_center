from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.phone import format_phone, normalize_e164
from app.integrations import gcs_faxes
from app.models.auth import CCUser
from app.models.fax_portal import (
    CCFpAcuityAppointmentType,
    CCFpAcuityCalendar,
    CCFpFax,
    CCFpFaxDocument,
    CCFpFaxPatient,
    CCFpReferralBoard,
    CCFpReferralExam,
    CCFpStatus,
)
from app.schemas.fax_portal import (
    AssigneeResponse,
    ClinicChoice,
    ClinicSuggestion,
    ExamChoice,
    IntakeDetailResponse,
    IntakeDocument,
    IntakeFax,
    IntakeFields,
    IntakeQueueItem,
    IntakeQueueResponse,
    IntakeSuggestions,
    IntakeUpdateRequest,
)
from app.services.fax_portal_activity import log_activity
from app.services.fax_portal_board import _full_name, _initials
from app.services.fax_portal_geo import MAX_SUGGEST_MILES, rank_clinics
from app.services.fax_portal_matching import suggest_clinic, suggest_exams
from app.services.fax_portal_referrals import (
    REVIEW,
    InvalidMoveError,
    ReferralNotFoundError,
    RowVersionConflictError,
)

R = CCFpReferralBoard

# Fields an intake reviewer can correct on the card. The extracted rows
# (cc_fp_fax_patient and friends) are never edited: they stay as evidence of
# what the fax said.
TEXT_FIELDS = (
    "first_name",
    "last_name",
    "gender",
    "email",
    "reason",
    "reason_other",
    "physician_name",
    "physician_fax_no",
    "physician_email",
    "address",
)


# ---------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------


DEFAULT_QUEUE_PAGE_SIZE = 10


def get_intake_queue(
    db: Session,
    *,
    duplicates_only: bool = False,
    page: int = 1,
    page_size: int = DEFAULT_QUEUE_PAGE_SIZE,
) -> IntakeQueueResponse:
    """One page of the cards waiting in Review Required, newest first. `total`
    counts every card in the chosen tab so the caller can page through them.

    Sorted by arrival only, not by NEW: opening a fax clears its NEW tag, and
    a NEW-first order would make it jump to a later page while someone is
    reviewing it. Each card still carries its own is_new flag.
    """
    base = select(R, CCFpFax.page_count).join(CCFpFax, CCFpFax.id == R.fax_id)
    base = base.where(R.status == REVIEW)

    waiting = db.scalar(select(func.count()).select_from(R).where(R.status == REVIEW)) or 0
    duplicates = (
        db.scalar(
            select(func.count())
            .select_from(R)
            .where(R.status == REVIEW, R.duplicate_of_referral_id.is_not(None))
        )
        or 0
    )

    if duplicates_only:
        base = base.where(R.duplicate_of_referral_id.is_not(None))

    total = duplicates if duplicates_only else waiting
    page = max(page, 1)

    rows = db.execute(
        base.order_by(R.created_at.desc(), R.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()

    people = {
        row.id: row
        for row in db.execute(
            select(CCUser.id, CCUser.first_name, CCUser.last_name, CCUser.email).where(
                CCUser.id.in_({card.assigned_user_id for card, _ in rows if card.assigned_user_id})
            )
        )
    }

    items = []

    for card, page_count in rows:
        person = people.get(card.assigned_user_id) if card.assigned_user_id else None

        items.append(
            IntakeQueueItem(
                id=card.id,
                fax_id=card.fax_id,
                display_name=_full_name(card.first_name, card.last_name) or "Unknown patient",
                created_at=card.created_at,
                is_new=card.first_viewed_at is None,
                is_urgent=card.is_urgent,
                possible_duplicate=card.duplicate_of_referral_id is not None,
                page_count=page_count,
                assignee=(
                    AssigneeResponse(
                        user_id=person.id,
                        name=_full_name(person.first_name, person.last_name) or person.email,
                        initials=_initials(person.first_name, person.last_name),
                    )
                    if person
                    else None
                ),
            )
        )

    return IntakeQueueResponse(
        waiting_count=waiting,
        duplicate_count=duplicates,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, -(-total // page_size)),
        items=items,
    )


# ---------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------


def _load(db: Session, referral_id: int) -> R:
    card = db.get(R, referral_id)

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    return card


def _exam_choices(db: Session, referral_id: int) -> dict[int, ExamChoice]:
    return {
        exam.slot: ExamChoice(
            acuity_appointment_type_id=exam.acuity_appointment_type_id,
            name=exam.appointment_type,
            fax_text=exam.fax_text,
        )
        for exam in db.scalars(
            select(CCFpReferralExam).where(CCFpReferralExam.referral_id == referral_id)
        )
    }


def _fax_texts(db: Session, card: R, fax: CCFpFax, documents: list[CCFpFaxDocument]) -> list[str]:
    patient_idx = (
        db.scalar(select(CCFpFaxPatient.idx).where(CCFpFaxPatient.id == card.fax_patient_id))
        if card.fax_patient_id
        else None
    )

    texts = [fax.summary, fax.diagnostic_studies_raw, fax.diagnoses_raw, fax.job_name]

    for document in documents:
        if (
            document.patient_idx is None
            or patient_idx is None
            or document.patient_idx == patient_idx
        ):
            texts.extend([document.name, document.summary])

    return [t for t in texts if t]


def _suggestions(
    db: Session,
    card: R,
    fax: CCFpFax,
    documents: list[CCFpFaxDocument],
    exams: dict[int, ExamChoice],
) -> IntakeSuggestions:
    texts = _fax_texts(db, card, fax, documents)

    clinic: ClinicSuggestion | None = None

    calendars = [
        (c.acuity_calendar_id, c.name, c.location)
        for c in db.scalars(
            select(CCFpAcuityCalendar).where(CCFpAcuityCalendar.is_active.is_(True))
        )
    ]

    # How far each clinic is from the patient's address (ZIP code to ZIP code),
    # worked out on this server: the address is not sent anywhere.
    distances = rank_clinics(card.address, calendars) or []

    if card.acuity_calendar_id is None:
        # A clinic the fax itself names wins; otherwise the nearest one.
        match = suggest_clinic(texts, [(cid, name) for cid, name, _ in calendars])

        if match:
            clinic = ClinicSuggestion(
                acuity_calendar_id=match.calendar_id, name=match.name, reason=match.reason
            )
        elif distances and distances[0].miles <= MAX_SUGGEST_MILES:
            nearest = distances[0]
            clinic = ClinicSuggestion(
                acuity_calendar_id=nearest.acuity_calendar_id,
                name=nearest.name,
                reason=f"Nearest to the patient's address (about {round(nearest.miles)} mi)",
            )

    chosen_ids = {e.acuity_appointment_type_id for e in exams.values()}
    free_slots = [slot for slot in (1, 2) if slot not in exams]
    suggested: dict[int, ExamChoice] = {}

    if free_slots:
        types = [
            (t.acuity_appointment_type_id, t.name)
            for t in db.scalars(
                select(CCFpAcuityAppointmentType).where(
                    CCFpAcuityAppointmentType.is_active.is_(True)
                )
            )
        ]
        matches = [m for m in suggest_exams(texts, types, limit=4) if m.type_id not in chosen_ids]

        for slot, match in zip(free_slots, matches, strict=False):
            suggested[slot] = ExamChoice(
                acuity_appointment_type_id=match.type_id,
                name=match.name,
                fax_text=match.fax_text or None,
            )

    return IntakeSuggestions(
        clinic=clinic,
        exam_1=suggested.get(1),
        exam_2=suggested.get(2),
        clinic_distances=distances,
    )


def get_intake_detail(db: Session, referral_id: int) -> IntakeDetailResponse:
    card = _load(db, referral_id)
    fax = db.get(CCFpFax, card.fax_id)
    documents = list(
        db.scalars(
            select(CCFpFaxDocument)
            .where(CCFpFaxDocument.fax_id == card.fax_id)
            .order_by(CCFpFaxDocument.idx)
        )
    )
    exams = _exam_choices(db, card.id)
    patient_idx = (
        db.scalar(select(CCFpFaxPatient.idx).where(CCFpFaxPatient.id == card.fax_patient_id))
        if card.fax_patient_id
        else None
    )

    return IntakeDetailResponse(
        id=card.id,
        row_version=card.row_version,
        status=card.status,
        is_new=card.first_viewed_at is None,
        is_urgent=card.is_urgent,
        duplicate_of_referral_id=card.duplicate_of_referral_id,
        fields=IntakeFields(
            first_name=card.first_name,
            last_name=card.last_name,
            dob=card.dob,
            phone=card.phone,
            gender=card.gender,
            email=card.email,
            physician_name=card.physician_name,
            physician_fax_no=card.physician_fax_no,
            physician_email=card.physician_email,
            address=card.address,
            reason=card.reason,
            reason_other=card.reason_other,
        ),
        clinic=(
            ClinicChoice(
                acuity_calendar_id=card.acuity_calendar_id,
                name=card.acuity_calendar_name or "",
            )
            if card.acuity_calendar_id is not None
            else None
        ),
        exam_1=exams.get(1),
        exam_2=exams.get(2),
        suggestions=_suggestions(db, card, fax, documents, exams),
        fax=IntakeFax(
            id=fax.id,
            job_id=fax.job_id,
            page_count=fax.page_count,
            received_at=fax.received_at,
            originating_number=fax.originating_number,
            summary=fax.summary,
            has_pdf=fax.gcs_uri is not None,
            documents=[
                IntakeDocument(
                    idx=d.idx,
                    name=d.name,
                    doc_type=d.doc_type,
                    page_start=d.page_start,
                    page_end=d.page_end,
                    summary=d.summary,
                    for_patient=(
                        d.patient_idx is None or patient_idx is None or d.patient_idx == patient_idx
                    ),
                )
                for d in documents
            ],
        ),
    )


# ---------------------------------------------------------------------
# Opening a card
# ---------------------------------------------------------------------


def mark_viewed(
    db: Session,
    referral_id: int,
    *,
    user: CCUser,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Clears the NEW tag the first time anyone opens the card, and records
    every open in the audit log.

    Does not bump row_version: looking at a card must not make someone else's
    drag on the board conflict. updated_at moves, so board polls pick up the
    cleared tag.
    """
    card = _load(db, referral_id)

    if card.first_viewed_at is None:
        card.first_viewed_at = func.now()
        card.first_viewed_by_user_id = user.id
        card.updated_at = func.now()

    log_activity(
        db,
        section="intake_review",
        action="open_card",
        user_id=user.id,
        referral_id=card.id,
        fax_id=card.fax_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.flush()


# ---------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()

    return stripped or None


def update_intake(
    db: Session,
    referral_id: int,
    payload: IntakeUpdateRequest,
    *,
    user: CCUser,
    ip_address: str | None = None,
    user_agent: str | None = None,
    require_review: bool = True,
) -> tuple[R, list[str]]:
    """Applies corrections to the card (never to the extracted rows).
    Optimistic lock on row_version; before/after values go to the audit log.

    From Intake Review (require_review=True) only cards still in Review
    Required can be edited. From the Referral Case (require_review=False) any
    open card can; a closed one must be reopened by a manager first.
    """
    card = db.scalar(select(R).where(R.id == referral_id).with_for_update())

    if card is None:
        raise ReferralNotFoundError("Referral not found.")

    if card.row_version != payload.row_version:
        raise RowVersionConflictError("This referral was changed by someone else. Reload it.")

    if require_review:
        if card.status != REVIEW:
            raise InvalidMoveError(
                "Only referrals in Review Required can be edited in Intake Review."
            )
    else:
        status_row = db.get(CCFpStatus, card.status)

        if status_row is not None and not status_row.is_open:
            raise InvalidMoveError("This referral is closed. A manager can reopen it to edit it.")

    provided = payload.model_fields_set
    changes: dict[str, dict[str, Any]] = {}

    def record(field: str, old: Any, new: Any) -> None:
        if old != new:
            changes[field] = {"from": _jsonable(old), "to": _jsonable(new)}

    for field in TEXT_FIELDS:
        if field in provided:
            new = _clean(getattr(payload, field))
            record(field, getattr(card, field), new)
            setattr(card, field, new)

    if "phone" in provided:
        # Stored as +1XXXXXXXXXX when valid so the column is uniform; typing the
        # same number in another format is not an edit.
        typed = _clean(payload.phone)
        new_phone = format_phone(typed)
        record("phone", card.phone, new_phone)
        card.phone = new_phone
        card.phone_e164 = normalize_e164(typed)

    if "dob" in provided:
        record("dob", card.dob, payload.dob)
        card.dob = payload.dob

    if "acuity_calendar_id" in provided:
        _set_clinic(db, card, payload.acuity_calendar_id, record)

    for slot in (1, 2):
        if f"exam_{slot}_id" in provided:
            _set_exam(
                db,
                card,
                slot,
                getattr(payload, f"exam_{slot}_id"),
                _clean(getattr(payload, f"exam_{slot}_fax_text")),
                record,
            )

    _apply_reason_rules(db, card, provided, record)

    if not changes:
        return card, []

    card.row_version += 1
    card.updated_at = func.now()

    log_activity(
        db,
        section="intake_review" if require_review else "referral_case",
        action="edit_field",
        user_id=user.id,
        referral_id=card.id,
        fax_id=card.fax_id,
        changes=changes,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.flush()

    return card, sorted(changes)


def _exam_1_reason_options(db: Session, referral_id: int) -> list[str]:
    """The "Reason for the appointment" options of the referral's Exam 1 (empty
    when there is no Exam 1 or Acuity gives it none)."""
    options = db.scalar(
        select(CCFpAcuityAppointmentType.reason_options)
        .join(
            CCFpReferralExam,
            CCFpReferralExam.acuity_appointment_type_id
            == CCFpAcuityAppointmentType.acuity_appointment_type_id,
        )
        .where(CCFpReferralExam.referral_id == referral_id, CCFpReferralExam.slot == 1)
    )

    return list(options or [])


def _apply_reason_rules(db: Session, card: R, provided: set[str], record: Any) -> None:
    """The reason must be one of Exam 1's Acuity options; "Other" needs the text
    beside it. The text box (`reason_other`) is always open and is kept whatever
    the reason is. Changing Exam 1 drops a reason the new exam does not offer."""
    exam_changed = "exam_1_id" in provided
    reason_edited = "reason" in provided

    if not (exam_changed or reason_edited or "reason_other" in provided):
        return

    # The exam change above is read back below.
    db.flush()
    options = _exam_1_reason_options(db, card.id)

    if card.reason is not None:
        offered = next((o for o in options if o.casefold() == card.reason.casefold()), None)

        if offered is not None:
            card.reason = offered
        elif exam_changed and not reason_edited:
            record("reason", card.reason, None)
            card.reason = None
        elif not options:
            has_exam = db.scalar(
                select(CCFpReferralExam.id).where(
                    CCFpReferralExam.referral_id == card.id, CCFpReferralExam.slot == 1
                )
            )
            raise InvalidMoveError(
                "That exam has no reason options."
                if has_exam
                else "Choose Exam 1 before choosing a reason."
            )
        else:
            raise InvalidMoveError("Choose one of the reasons offered for this exam.")

    if (card.reason or "").casefold() == "other" and _clean(card.reason_other) is None:
        raise InvalidMoveError("Say what the Other reason is.")


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def _set_clinic(db: Session, card: R, calendar_id: int | None, record: Any) -> None:
    if calendar_id is None:
        record("clinic", card.acuity_calendar_name, None)
        card.acuity_calendar_id = None
        card.acuity_calendar_name = None

        return

    calendar = db.get(CCFpAcuityCalendar, calendar_id)

    # An already-chosen clinic stays valid even if Acuity later drops it.
    if calendar is None or (not calendar.is_active and calendar_id != card.acuity_calendar_id):
        raise InvalidMoveError("That clinic is not available. Refresh from Acuity and try again.")

    record("clinic", card.acuity_calendar_name, calendar.name)
    card.acuity_calendar_id = calendar.acuity_calendar_id
    card.acuity_calendar_name = calendar.name[:150]


def _set_exam(
    db: Session,
    card: R,
    slot: int,
    type_id: int | None,
    fax_text: str | None,
    record: Any,
) -> None:
    current = db.scalar(
        select(CCFpReferralExam).where(
            CCFpReferralExam.referral_id == card.id, CCFpReferralExam.slot == slot
        )
    )

    if type_id is None:
        if current is not None:
            record(f"exam_{slot}", current.appointment_type, None)
            db.execute(delete(CCFpReferralExam).where(CCFpReferralExam.id == current.id))

        return

    exam_type = db.get(CCFpAcuityAppointmentType, type_id)
    keeps_current = current is not None and current.acuity_appointment_type_id == type_id

    if exam_type is None or (not exam_type.is_active and not keeps_current):
        raise InvalidMoveError("That exam is not available. Refresh from Acuity and try again.")

    if current is None:
        db.add(
            CCFpReferralExam(
                referral_id=card.id,
                slot=slot,
                acuity_appointment_type_id=type_id,
                appointment_type=exam_type.name[:255],
                fax_text=fax_text,
            )
        )
        record(f"exam_{slot}", None, exam_type.name)
    else:
        record(f"exam_{slot}", current.appointment_type, exam_type.name)
        current.acuity_appointment_type_id = type_id
        current.appointment_type = exam_type.name[:255]

        if fax_text is not None or not keeps_current:
            current.fax_text = fax_text


# ---------------------------------------------------------------------
# The fax PDF
# ---------------------------------------------------------------------


def get_fax_pdf(
    db: Session,
    fax_id: int,
    *,
    user: CCUser,
    referral_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> bytes:
    """Returns the PDF bytes for a fax and records the view (action view_pdf).

    The PDF is linked from the bucket on demand, by Job ID, the first time it
    is needed (it may land in the bucket after the webhook). Raises
    gcs_faxes.FaxPdfNotFoundError if it is not there. The audit entry is
    written before the bytes are returned and never contains PHI.
    """
    fax = db.get(CCFpFax, fax_id)

    if fax is None:
        raise ReferralNotFoundError("Fax not found.")

    if fax.gcs_uri is None:
        object_name = gcs_faxes.find_fax_pdf(fax.job_id)

        if object_name is None:
            raise gcs_faxes.FaxPdfNotFoundError("The PDF has not arrived yet.")

        fax.gcs_uri = gcs_faxes.to_gcs_uri(object_name)
        fax.gcs_received_at = func.now()
        db.flush()

    data = gcs_faxes.download_fax_pdf(fax.gcs_uri)

    log_activity(
        db,
        section="intake_review",
        action="view_pdf",
        user_id=user.id,
        referral_id=referral_id,
        fax_id=fax.id,
        details={"bytes": len(data)},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()

    return data
