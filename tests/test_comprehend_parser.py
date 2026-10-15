from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.core.phone import format_phone, normalize_e164
from app.services.comprehend_parser import (
    parse_payload,
    pick_referrer,
    split_patient_name,
    to_date,
    to_flag,
    to_small_int,
)

FIXTURE = Path(__file__).parent / "fixtures" / "comprehend_sample.json"


@pytest.fixture
def sample() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------
# Phone normalisation
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("(214) 555-0187", "+12145550187"),
        ("214-555-0187", "+12145550187"),
        ("214.555.0187", "+12145550187"),
        ("1 (214) 555-0187", "+12145550187"),
        ("+1 214 555 0187", "+12145550187"),
        ("(214) 555-0187 x12", "+12145550187"),
        ("+44 20 7946 0958", "+442079460958"),
        # Masked, blank, or not a phone number.
        ("(214) 648-3XXX", None),
        ("XXX-XXX-XXXX", None),
        ("", None),
        (None, None),
        ("555-0187", None),
        ("(014) 555-0187", None),
        ("(214) 155-0187", None),
        ("not a number", None),
    ],
)
def test_normalize_e164(raw: str | None, expected: str | None) -> None:
    assert normalize_e164(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("(214) 555-0187", "+12145550187"),
        (" 214.555.0187 ", "+12145550187"),
        ("1 (214) 555-0187", "+12145550187"),
        ("+12145550187", "+12145550187"),
        # What cannot be normalised is kept as written, not lost.
        ("(214) 648-3XXX", "(214) 648-3XXX"),
        ("XXX-XXX-XXXX", "XXX-XXX-XXXX"),
        ("555-0187", "555-0187"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_format_phone_stores_plus_one_and_ten_digits_when_valid(
    raw: str | None, expected: str | None
) -> None:
    assert format_phone(raw) == expected


def test_phones_from_the_webhook_are_stored_as_plus_one_and_ten_digits(
    sample: dict[str, Any],
) -> None:
    payload = json.loads(json.dumps(sample))
    payload["PHI Phone"][0] = "(214) 555-0187"
    payload["Phone"][0] = "972-555-0144 x12"

    parsed = parse_payload(payload)

    assert parsed.patients[0]["phone"] == "+12145550187"
    assert parsed.patients[0]["phone_e164"] == "+12145550187"
    assert parsed.providers[0]["phone"] == "+19725550144"


# ---------------------------------------------------------------------
# Scalar coercion
# ---------------------------------------------------------------------


def test_flags_arrive_as_strings() -> None:
    assert to_flag("1") is True
    assert to_flag("0") is False
    assert to_flag("") is None
    assert to_flag(None) is None
    assert to_flag(True) is True
    assert to_flag("maybe") is None


def test_numbers_arrive_as_strings() -> None:
    assert to_small_int("5") == 5
    assert to_small_int(3) == 3
    assert to_small_int("") is None
    assert to_small_int("five") is None
    assert to_small_int("99999") is None


def test_dates() -> None:
    assert to_date("1970-01-31") == date(1970, 1, 31)
    assert to_date("2026-10-12T21:44:06+00:00") == date(2026, 10, 12)
    assert to_date("01/31/1970") == date(1970, 1, 31)
    assert to_date("0?/21/1968") is None
    assert to_date("") is None


# ---------------------------------------------------------------------
# Whole payload
# ---------------------------------------------------------------------


def test_fax_level_fields(sample: dict[str, Any]) -> None:
    parsed = parse_payload(sample)

    assert parsed.job_id == "7765d711-7c00-4b92-8c72-5884fc356bd9"
    assert parsed.fax["page_count"] == 5
    assert parsed.fax["patient_count"] == 1
    assert parsed.fax["is_urgent"] is False
    assert parsed.fax["is_spam"] is None  # "" -> NULL
    assert parsed.fax["is_multi_patient"] is False
    assert parsed.fax["received_at"].isoformat() == "2026-10-12T21:44:06+00:00"
    assert parsed.fax["summary"].startswith("This document package includes")
    # Blank custom AI fields become NULL, not "".
    assert parsed.fax["diagnoses_raw"] is None
    # The originating number is masked, but the job name has the full one.
    assert parsed.fax["originating_e164"] == "+12145550100"


def test_sections_are_split_by_array_position(sample: dict[str, Any]) -> None:
    parsed = parse_payload(sample)

    assert len(parsed.patients) == 1
    assert len(parsed.providers) == 11
    assert len(parsed.documents) == 4
    assert len(parsed.insurance) == 1

    patient = parsed.patients[0]
    assert patient["full_name"] == "John Doe"
    assert patient["dob"] == date(1970, 1, 1)
    assert patient["dob_raw"] is None
    # "XXX-XXX-XXXX" is masked: kept as received, no E.164.
    assert patient["phone"] == "XXX-XXX-XXXX"
    assert patient["phone_e164"] is None


def test_three_differently_cased_patient_index_keys(sample: dict[str, Any]) -> None:
    parsed = parse_payload(sample)

    # "patient Index" (provider section): [null,null,null,0,0,0,null,0,0,0,0]
    assert [p["patient_idx"] for p in parsed.providers] == [
        None,
        None,
        None,
        0,
        0,
        0,
        None,
        0,
        0,
        0,
        0,
    ]
    # "patient index" (document section): [null,0,0,0]
    assert [d["patient_idx"] for d in parsed.documents] == [None, 0, 0, 0]
    # "provider index" (document section): [0,1,3,null]
    assert [d["provider_idx"] for d in parsed.documents] == [0, 1, 3, None]
    # "Patient index" (insurance section): [0]
    assert [i["patient_idx"] for i in parsed.insurance] == [0]


def test_provider_roles_and_scanx_flag(sample: dict[str, Any]) -> None:
    parsed = parse_payload(sample)
    by_name = {p["name"]: p for p in parsed.providers}

    assert by_name["Doctor Order, MD"]["roles"] == ["Ordering", "Authoring"]
    assert by_name["Doctor Order, MD"]["npi"] == "1234567890"
    assert by_name["Example Medical Center"]["roles"] == []

    # ScanX's own line (fax == "Term number") is the recipient, not a provider.
    assert [p["name"] for p in parsed.providers if p["is_scanx"]] == ["ScanX Health"]


def test_insurance_columns(sample: dict[str, Any]) -> None:
    insurance = parse_payload(sample).insurance[0]

    assert insurance["payer_name"] == "BCBS"
    assert insurance["plan_name"] == "MY BLUE HEALTH EXCH"
    assert insurance["subscriber_name"] == "DOE, JOHN"
    assert insurance["effective_date"] == date(2026, 9, 1)
    assert insurance["member_id"] is None
    assert insurance["benefits"] == {}


def test_insurance_extras_go_to_benefits() -> None:
    parsed = parse_payload(
        {
            "Job ID": "j",
            "PHI Payer Name": ["ACME"],
            "PCP copay": ["$20"],
            "Specialist copay": [None],
            "RX BIN": ["610014"],
        }
    )

    assert parsed.insurance[0]["benefits"] == {"pcp_copay": "$20", "rx_bin": "610014"}


def test_cross_reference_to_missing_row_is_dropped() -> None:
    parsed = parse_payload(
        {
            "Job ID": "j",
            "PHI Name": ["A B"],
            "Name": ["Dr X"],
            "patient Index": [7],  # no patient 7
            "PHI Document Name": ["Doc"],
            "patient index": [0],
            "provider index": [4],  # no provider 4
        }
    )

    assert parsed.providers[0]["patient_idx"] is None
    assert parsed.documents[0]["patient_idx"] == 0
    assert parsed.documents[0]["provider_idx"] is None


def test_unparseable_dob_is_kept_raw() -> None:
    patient = parse_payload(
        {"Job ID": "j", "PHI Name": ["A B"], "PHI DOB": ["0?/21/1968"]}
    ).patients[0]

    assert patient["dob"] is None
    assert patient["dob_raw"] == "0?/21/1968"


def test_arrays_shorter_than_name_do_not_break() -> None:
    parsed = parse_payload({"Job ID": "j", "Name": ["Dr A", "Dr B", "Dr C"], "NPI": ["1234567890"]})

    assert len(parsed.providers) == 3
    assert parsed.providers[0]["npi"] == "1234567890"
    assert parsed.providers[2]["npi"] is None


def test_empty_payload_parses_to_nothing() -> None:
    parsed = parse_payload({})

    assert parsed.job_id is None
    assert parsed.patients == []
    assert parsed.providers == []
    assert parsed.fax["received_at"] is None


# ---------------------------------------------------------------------
# Names and referrer
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("full", "expected"),
    [
        ("Julia Ann Compton", ("Julia Ann", "Compton")),
        ("Compton, Julia Ann", ("Julia Ann", "Compton")),
        ("John Doe", ("John", "Doe")),
        ("Madonna", (None, "Madonna")),
        ("Martin Luther King Jr.", ("Martin Luther", "King Jr.")),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_split_patient_name(full: str | None, expected: tuple[str | None, str | None]) -> None:
    assert split_patient_name(full) == expected


def test_referrer_from_ordering_role(sample: dict[str, Any]) -> None:
    parsed = parse_payload(sample)

    guess = pick_referrer(parsed, 0)

    # Ordering outranks Authoring / Primary Care; ScanX is never the referrer.
    assert guess.provider is not None
    assert guess.provider["name"] == "Doctor Order, MD"
    # The person has no fax; the Referral document's practice supplies it.
    assert guess.sender is not None
    assert guess.sender["name"] == "Example Urology Clinic"
    assert guess.fax_number == "214-555-0102"


def test_referrer_from_referring_role_as_in_real_captures() -> None:
    parsed = parse_payload(
        {
            "Job ID": "j",
            "Term number": "(469) 429-7432",
            "PHI Name": ["Jane Roe"],
            "Name": [
                "ScanX Health LLC",
                "Digestive Health Associates",
                "Stuart Akerman, MD",
                "Carlos T",
            ],
            "type (roles)": [[], [], ["Treating", "Authoring"], ["Referring"]],
            "patient Index": [None, None, 0, 0],
            "FAX": ["(469) 429-7432", "(972) 867-7785", None, None],
            "PHI Document Name": ["Cover", "Cover 2"],
            "Document type": ["Cover Page", "Cover Page"],
            "provider index": [0, 1],
        }
    )

    guess = pick_referrer(parsed, 0)

    assert guess.provider is not None
    assert guess.provider["name"] == "Carlos T"
    assert guess.sender is not None
    assert guess.sender["name"] == "Digestive Health Associates"
    assert guess.fax_number == "(972) 867-7785"


def test_referrer_falls_back_to_unmasked_originating_number() -> None:
    parsed = parse_payload(
        {
            "Job ID": "j",
            "Term number": "(469) 429-7432",
            "Originating Number": "(972) 555-0199",
            "PHI Name": ["Jane Roe"],
            "Name": ["Dr Who"],
        }
    )

    guess = pick_referrer(parsed, 0)

    assert guess.provider is not None
    assert guess.fax_number == "(972) 555-0199"


def test_no_referrer_when_only_scanx_present() -> None:
    parsed = parse_payload(
        {
            "Job ID": "j",
            "Term number": "(469) 429-7432",
            "Name": ["ScanX Health"],
            "FAX": ["(469) 429-7432"],
        }
    )

    guess = pick_referrer(parsed, None)

    assert guess.provider is None
    assert guess.fax_number is None
