from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from app.core.phone import normalize_e164

# Pure parsing of a WestFax Comprehend webhook body into column values for the
# cc_fp_ tables. No database access here, so every quirk is unit-testable.
#
# Comprehend sends each section as parallel arrays. A row's idx is its
# position in the array, which is what the cross-reference keys point at
# ("patient Index", "patient index", "Patient index", "provider index").
# See docs/schema-README.md section 3.

SMALLINT_MAX = 32767

# Roles that mark the person who referred / ordered the exam, best first.
# "Referring" and "Treating" appear in real captures; "Ordering" and
# "Authoring" in the original sample.
REFERRER_ROLE_PRIORITY = ("referring", "ordering", "authoring", "primary care", "treating")

# Insurance keys that are not first-class columns. Stored in the benefits
# JSONB under the snake_case of the Comprehend key (docs/schema.sql).
INSURANCE_BENEFIT_KEYS = (
    "PCP copay",
    "Specialist copay",
    "ER copay",
    "Urgent care copay",
    "RX copay(generic)",
    "RX copay (generic)",
    "RX copay(brand)",
    "RX copay (brand)",
    "Plan website",
    "providers name",
    "RX BIN",
    "RX PCN",
    "RX group",
    "RX ID",
    "PHI Plan Disclaimer",
    "Deductible",
    "Out of pocket max",
    "Coinsurance",
)

_PHONE_IN_TEXT = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


# ---------------------------------------------------------------------
# Scalar coercion
# ---------------------------------------------------------------------


def to_str(value: Any) -> str | None:
    """Blank and null become None; numbers become their string form."""
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, int | float):
        return str(value)

    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None

    return None


def to_flag(value: Any) -> bool | None:
    """Comprehend flags arrive as "0", "1" or "" (sometimes real booleans)."""
    if isinstance(value, bool):
        return value

    if isinstance(value, int | float):
        return bool(value)

    text = to_str(value)

    if text is None:
        return None

    lowered = text.lower()

    if lowered in {"1", "true", "yes", "y"}:
        return True

    if lowered in {"0", "false", "no", "n"}:
        return False

    return None


def to_small_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, float) and value.is_integer():
        value = int(value)

    if isinstance(value, int):
        number = value
    else:
        text = to_str(value)

        if text is None or not re.fullmatch(r"-?\d+", text):
            return None

        number = int(text)

    return number if abs(number) <= SMALLINT_MAX else None


def to_date(value: Any) -> date | None:
    text = to_str(value)

    if text is None:
        return None

    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def to_datetime(value: Any) -> datetime | None:
    text = to_str(value)

    if text is None:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    # A naive timestamp is treated as UTC (WestFax sends +00:00 explicitly).
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    return parsed


def to_str_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]

    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    return []


def snake_case(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


# ---------------------------------------------------------------------
# Array access
# ---------------------------------------------------------------------


def _array(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)

    if isinstance(value, list):
        return value

    # A section with a single row occasionally arrives as a bare scalar.
    if value is None or value == "":
        return []

    return [value]


def _at(values: list[Any], index: int) -> Any:
    return values[index] if index < len(values) else None


def _row_count(payload: dict[str, Any], keys: tuple[str, ...]) -> int:
    return max((len(_array(payload, key)) for key in keys), default=0)


def _valid_ref(value: Any, count: int) -> int | None:
    """A cross-reference is only usable if it points at a row that exists;
    otherwise the composite foreign key would fail the whole fax."""
    index = to_small_int(value)

    return index if index is not None and 0 <= index < count else None


# ---------------------------------------------------------------------
# Parsed shapes
# ---------------------------------------------------------------------


@dataclass
class ParsedFax:
    job_id: str | None
    fax: dict[str, Any]
    patients: list[dict[str, Any]] = field(default_factory=list)
    providers: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    insurance: list[dict[str, Any]] = field(default_factory=list)


PATIENT_KEYS = (
    "PHI Name",
    "PHI DOB",
    "PHI Address",
    "PHI Gender",
    "PHI MRN",
    "PHI Phone",
)
PROVIDER_KEYS = (
    "Name",
    "type (roles)",
    "Role Legacy String",
    "patient Index",
    "NPI",
    "Phone",
    "Address 1",
    "Address 2",
    "City",
    "State",
    "ZIP",
    "FAX",
    "Email",
    "Specialty",
)
DOCUMENT_KEYS = (
    "PHI Document Name",
    "Document type",
    "Document Date",
    "PHI Summary",
    "Page start",
    "page end",
    "patient index",
    "provider index",
)
INSURANCE_KEYS = (
    "PHI Payer Name",
    "PHI Plan name",
    "Plan type",
    "PHI Network Name",
    "PHI Group name",
    "Group number",
    "Member ID",
    "PHI Subscriber Name",
    "PHI Patient name on card",
    "Relationship to subscriber",
    "PHI PCP name",
    "Effective Date",
    "Termination Date",
    "PHI Payer information(raw)",
    "PHI Notes",
    "Patient index",
)


def parse_payload(payload: dict[str, Any]) -> ParsedFax:
    job_id = to_str(payload.get("Job ID"))
    term_e164 = normalize_e164(to_str(payload.get("Term number")))

    originating = to_str(payload.get("Originating Number"))
    job_name = to_str(payload.get("PHI Job Name"))

    # Originating Number can arrive masked ("(214) 648-3XXX") while the job
    # name still shows the full number.
    originating_e164 = normalize_e164(originating)

    if originating_e164 is None and job_name:
        match = _PHONE_IN_TEXT.search(job_name)
        originating_e164 = normalize_e164(match.group(0)) if match else None

    barcodes = payload.get("[PHI] Page barcodes")

    fax = {
        "product_id": to_str(payload.get("Product ID")),
        "term_number": to_str(payload.get("Term number")),
        "originating_number": originating,
        "originating_e164": originating_e164,
        "result": to_str(payload.get("Result")),
        "page_count": to_small_int(payload.get("Page Count")),
        "received_at": to_datetime(payload.get("Date")),
        "job_name": job_name,
        "contact_name": to_str(payload.get("Contact name")),
        "is_spam": to_flag(payload.get("isSpam")),
        "is_urgent": to_flag(payload.get("isUrgent")),
        "summary": to_str(payload.get("PHI Summary of Docs")),
        "is_multi_patient": to_flag(payload.get("Multi patient")),
        "patient_count": to_small_int(payload.get("Patient count")),
        "diagnoses_raw": _joined(payload.get("Diagnoses")),
        "diagnostic_studies_raw": _joined(payload.get("Diagnostic Studies")),
        "page_barcodes": to_str_list(barcodes) or None,
    }

    parsed = ParsedFax(job_id=job_id, fax=fax)

    # Patients ---------------------------------------------------------
    patient_count = _row_count(payload, PATIENT_KEYS)

    for i in range(patient_count):
        dob_raw = to_str(_at(_array(payload, "PHI DOB"), i))
        dob = to_date(dob_raw)
        phone = to_str(_at(_array(payload, "PHI Phone"), i))

        parsed.patients.append(
            {
                "idx": i,
                "full_name": to_str(_at(_array(payload, "PHI Name"), i)),
                "dob": dob,
                "dob_raw": dob_raw if dob is None else None,
                "address": to_str(_at(_array(payload, "PHI Address"), i)),
                "gender": to_str(_at(_array(payload, "PHI Gender"), i)),
                "mrn": to_str(_at(_array(payload, "PHI MRN"), i)),
                "phone": phone,
                "phone_e164": normalize_e164(phone),
            }
        )

    # Providers --------------------------------------------------------
    provider_count = _row_count(payload, PROVIDER_KEYS)

    for i in range(provider_count):
        name = to_str(_at(_array(payload, "Name"), i))
        fax_number = to_str(_at(_array(payload, "FAX"), i))
        fax_e164 = normalize_e164(fax_number)
        roles = to_str_list(_at(_array(payload, "type (roles)"), i))

        parsed.providers.append(
            {
                "idx": i,
                "name": name,
                "roles": roles,
                "role_legacy": to_str(_at(_array(payload, "Role Legacy String"), i)),
                "patient_idx": _valid_ref(_at(_array(payload, "patient Index"), i), patient_count),
                "npi": _npi(_at(_array(payload, "NPI"), i)),
                "phone": to_str(_at(_array(payload, "Phone"), i)),
                "address1": to_str(_at(_array(payload, "Address 1"), i)),
                "address2": to_str(_at(_array(payload, "Address 2"), i)),
                "city": to_str(_at(_array(payload, "City"), i)),
                "state": to_str(_at(_array(payload, "State"), i)),
                "zip": to_str(_at(_array(payload, "ZIP"), i)),
                "fax": fax_number,
                "fax_e164": fax_e164,
                "email": to_str(_at(_array(payload, "Email"), i)),
                "specialty": to_str(_at(_array(payload, "Specialty"), i)),
                # Recipient side: ScanX itself or its own line.
                "is_scanx": bool(
                    (term_e164 is not None and fax_e164 == term_e164)
                    or (name is not None and "scanx" in name.lower())
                ),
            }
        )

    # Contained documents ---------------------------------------------
    document_count = _row_count(payload, DOCUMENT_KEYS)

    for i in range(document_count):
        parsed.documents.append(
            {
                "idx": i,
                "name": to_str(_at(_array(payload, "PHI Document Name"), i)),
                "doc_type": to_str(_at(_array(payload, "Document type"), i)),
                "doc_date": to_date(_at(_array(payload, "Document Date"), i)),
                "summary": to_str(_at(_array(payload, "PHI Summary"), i)),
                "page_start": to_small_int(_at(_array(payload, "Page start"), i)),
                "page_end": to_small_int(_at(_array(payload, "page end"), i)),
                "patient_idx": _valid_ref(_at(_array(payload, "patient index"), i), patient_count),
                "provider_idx": _valid_ref(
                    _at(_array(payload, "provider index"), i), provider_count
                ),
            }
        )

    # Insurance --------------------------------------------------------
    insurance_count = _row_count(payload, INSURANCE_KEYS)

    for i in range(insurance_count):
        benefits: dict[str, Any] = {}

        for key in INSURANCE_BENEFIT_KEYS:
            value = _at(_array(payload, key), i)

            if value is not None and value != "":
                benefits[snake_case(key)] = value

        parsed.insurance.append(
            {
                "idx": i,
                "patient_idx": _valid_ref(_at(_array(payload, "Patient index"), i), patient_count),
                "payer_name": to_str(_at(_array(payload, "PHI Payer Name"), i)),
                "plan_name": to_str(_at(_array(payload, "PHI Plan name"), i)),
                "plan_type": to_str(_at(_array(payload, "Plan type"), i)),
                "network_name": to_str(_at(_array(payload, "PHI Network Name"), i)),
                "group_name": to_str(_at(_array(payload, "PHI Group name"), i)),
                "group_number": to_str(_at(_array(payload, "Group number"), i)),
                "member_id": to_str(_at(_array(payload, "Member ID"), i)),
                "subscriber_name": to_str(_at(_array(payload, "PHI Subscriber Name"), i)),
                "patient_name_on_card": to_str(_at(_array(payload, "PHI Patient name on card"), i)),
                "relationship_to_subscriber": to_str(
                    _at(_array(payload, "Relationship to subscriber"), i)
                ),
                "pcp_name": to_str(_at(_array(payload, "PHI PCP name"), i)),
                "effective_date": to_date(_at(_array(payload, "Effective Date"), i)),
                "termination_date": to_date(_at(_array(payload, "Termination Date"), i)),
                "payer_info_raw": to_str(_at(_array(payload, "PHI Payer information(raw)"), i)),
                "notes": to_str(_at(_array(payload, "PHI Notes"), i)),
                "benefits": benefits,
            }
        )

    return parsed


def _joined(value: Any) -> str | None:
    if isinstance(value, list):
        parts = [part for part in (to_str(item) for item in value) if part]

        return "; ".join(parts) or None

    return to_str(value)


def _npi(value: Any) -> str | None:
    text = to_str(value)

    return text if text and re.fullmatch(r"\d{10}", text) else None


# ---------------------------------------------------------------------
# Referral card derivation
# ---------------------------------------------------------------------


def split_patient_name(full_name: str | None) -> tuple[str | None, str | None]:
    """Splits "Julia Ann Compton" or "Compton, Julia Ann" into (first, last)."""
    if not full_name:
        return None, None

    name = full_name.strip()

    if "," in name:
        last, _, first = name.partition(",")

        return (first.strip() or None), (last.strip() or None)

    tokens = name.split()

    if len(tokens) == 1:
        return None, tokens[0]

    if tokens[-1].lower() in _NAME_SUFFIXES and len(tokens) > 2:
        return " ".join(tokens[:-2]), f"{tokens[-2]} {tokens[-1]}"

    return " ".join(tokens[:-1]), tokens[-1]


@dataclass
class ReferrerGuess:
    """Best guess at who referred this patient, from the extracted providers."""

    provider: dict[str, Any] | None
    sender: dict[str, Any] | None
    fax_number: str | None
    email: str | None


def pick_referrer(parsed: ParsedFax, patient_idx: int | None) -> ReferrerGuess:
    """Chooses the referring provider for one patient.

    Heuristic, to be tuned against more real captures:
      * the person: first non-ScanX provider holding a referrer role
        (Referring, Ordering, Authoring, Primary Care, Treating, in that
        order) who is tied to this patient or to no patient; otherwise the
        first non-ScanX provider.
      * the sending practice: the provider row of the Referral document (else
        any document) that is not ScanX and has a fax number; otherwise the
        first non-ScanX row with a fax number.
      * physician fax: the person's own fax, else the sending practice's, else
        the fax's (unmasked) originating number when it is not ScanX's line.
    """
    candidates = [
        p
        for p in parsed.providers
        if not p["is_scanx"]
        and p["name"]
        and (p["patient_idx"] is None or patient_idx is None or p["patient_idx"] == patient_idx)
    ]

    person: dict[str, Any] | None = None

    for role in REFERRER_ROLE_PRIORITY:
        person = next(
            (p for p in candidates if role in {r.lower() for r in p["roles"]}),
            None,
        )

        if person is not None:
            break

    if person is None and candidates:
        person = candidates[0]

    providers_by_idx = {p["idx"]: p for p in parsed.providers}
    documents = sorted(
        parsed.documents,
        key=lambda d: 0 if (d["doc_type"] or "").lower() == "referral" else 1,
    )

    sender: dict[str, Any] | None = None

    for document in documents:
        row = providers_by_idx.get(document["provider_idx"])

        if row and not row["is_scanx"] and row["fax_e164"]:
            sender = row
            break

    if sender is None:
        sender = next(
            (p for p in parsed.providers if not p["is_scanx"] and p["fax_e164"]),
            None,
        )

    fax_number = (person or {}).get("fax") or (sender or {}).get("fax")

    if not fax_number:
        originating = parsed.fax.get("originating_number")
        originating_e164 = parsed.fax.get("originating_e164")
        term_e164 = normalize_e164(parsed.fax.get("term_number"))

        if originating_e164 and originating_e164 != term_e164:
            fax_number = originating

    email = (person or {}).get("email") or (sender or {}).get("email")

    return ReferrerGuess(provider=person, sender=sender, fax_number=fax_number, email=email)
