from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest

from app.integrations import gcs_reports


@dataclass
class FakeBlob:
    name: str


@dataclass
class FakePrefixIterator:
    """Mimics the google-cloud-storage HTTPIterator returned by
    bucket.list_blobs(..., delimiter="/"): iterating it yields no blobs
    (nothing sits directly under tricefy/), and .prefixes is populated with
    one entry per immediate "subfolder" once the iterator's been exhausted.
    """

    folder_prefixes: list[str]

    def __iter__(self):
        return iter(())

    @property
    def prefixes(self) -> list[str]:
        return self.folder_prefixes


@dataclass
class FakeBucket:
    folder_prefixes: list[str]
    files_by_folder: dict[str, list[str]] = field(default_factory=dict)

    def list_blobs(self, prefix: str, delimiter: str | None = None):
        if delimiter == "/":
            return FakePrefixIterator(self.folder_prefixes)

        return [
            FakeBlob(name=f"{prefix}{file_name}")
            for file_name in self.files_by_folder.get(prefix, [])
        ]


def _patch_bucket(monkeypatch: pytest.MonkeyPatch, bucket: FakeBucket) -> None:
    monkeypatch.setattr(gcs_reports, "_get_bucket", lambda: bucket)


def test_locate_tricefy_blob_matches_all_caps_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = "tricefy/DOE^JANE^_20260101/"
    _patch_bucket(
        monkeypatch,
        FakeBucket(
            folder_prefixes=[folder],
            files_by_folder={
                folder: [
                    "1.3.6.1.4.1.35190.1.1.pdf",
                    "Jane_Doe_Report_2026-01-01.pdf",
                ]
            },
        ),
    )

    blob = gcs_reports.locate_tricefy_blob(
        first_name="Jane",
        last_name="Doe",
        exam_date=date(2026, 1, 1),
    )

    assert blob is not None
    assert blob.name == f"{folder}Jane_Doe_Report_2026-01-01.pdf"


def test_locate_tricefy_blob_matches_titlecase_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: some tricefy exports (and the appointment_id-infixed
    folder naming variant) don't uppercase the folder name, e.g.
    "Tester^Tester^_1770849983_20260914". This used to be missed entirely
    because the match was case-sensitive against an uppercased marker.
    """
    folder = "tricefy/Tester^Tester^_1770849983_20260914/"
    _patch_bucket(
        monkeypatch,
        FakeBucket(
            folder_prefixes=[folder],
            files_by_folder={
                folder: [
                    "1.3.6.1.4.1.35190.1.1.pdf",
                    "Tester_Tester_Abdominal_Ultrasound_(Complete)_Report_2026-09-14.pdf",
                ]
            },
        ),
    )

    blob = gcs_reports.locate_tricefy_blob(
        first_name="Tester",
        last_name="Tester",
        exam_date=date(2026, 9, 14),
    )

    assert blob is not None
    assert blob.name == (
        f"{folder}Tester_Tester_Abdominal_Ultrasound_(Complete)_Report_2026-09-14.pdf"
    )


def test_locate_tricefy_blob_no_matching_folder_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_bucket(
        monkeypatch,
        FakeBucket(folder_prefixes=["tricefy/SMITH^JOHN^_20260101/"]),
    )

    blob = gcs_reports.locate_tricefy_blob(
        first_name="Jane",
        last_name="Doe",
        exam_date=date(2026, 1, 1),
    )

    assert blob is None


def test_locate_tricefy_blob_ambiguous_folders_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never guess: two folders for the same name+date is treated as no
    match rather than picking one arbitrarily.
    """
    _patch_bucket(
        monkeypatch,
        FakeBucket(
            folder_prefixes=[
                "tricefy/DOE^JANE^_20260101/",
                "tricefy/DOE^JANE^_APT2_20260101/",
            ]
        ),
    )

    blob = gcs_reports.locate_tricefy_blob(
        first_name="Jane",
        last_name="Doe",
        exam_date=date(2026, 1, 1),
    )

    assert blob is None


def test_locate_tricefy_blob_no_human_readable_file_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = "tricefy/DOE^JANE^_20260101/"
    _patch_bucket(
        monkeypatch,
        FakeBucket(
            folder_prefixes=[folder],
            files_by_folder={folder: ["1.3.6.1.4.1.35190.1.1.pdf"]},
        ),
    )

    blob = gcs_reports.locate_tricefy_blob(
        first_name="Jane",
        last_name="Doe",
        exam_date=date(2026, 1, 1),
    )

    assert blob is None


# ---------------------------------------------------------------------
# Choosing the folder by the exam's appointment id
# ---------------------------------------------------------------------

# The same patient, as in the bucket: the export misspells her first name in the
# 10-17 folder (DURMANA), and each exam has its own appointment id.
ECHO = "tricefy/OMONOVA^DURMANA^_1790167726_20261017/"
ABDOMINAL = "tricefy/OMONOVA^DURMAMA^_1790197523_20261020/"
REPORT = "Durmama_Omonova_Report_2026-10-17.pdf"


def _omonova_bucket(*folders: str) -> FakeBucket:
    return FakeBucket(
        folder_prefixes=list(folders),
        files_by_folder={
            folder: ["1.3.6.1.4.1.35190.1.1.pdf", f"report_for_{index}_2026-10-17.pdf"]
            for index, folder in enumerate(folders)
        },
    )


def _locate(appointment_id: str | None, day: date):
    return gcs_reports.locate_tricefy_blob(
        first_name="Durmama",
        last_name="Omonova",
        exam_date=day,
        appointment_id=appointment_id,
    )


def test_the_folder_is_found_by_appointment_id_even_when_the_name_is_misspelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_bucket(monkeypatch, _omonova_bucket(ABDOMINAL, ECHO))

    blob = _locate("1790167726", date(2026, 10, 17))

    assert blob is not None and blob.name.startswith(ECHO)


def test_each_exam_gets_its_own_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_bucket(monkeypatch, _omonova_bucket(ABDOMINAL, ECHO))

    abdominal = _locate("1790197523", date(2026, 10, 20))
    echo = _locate("1790167726", date(2026, 10, 17))

    assert abdominal is not None and abdominal.name.startswith(ABDOMINAL)
    assert echo is not None and echo.name.startswith(ECHO)


def test_another_exam_on_the_same_day_does_not_get_this_exams_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two exams on 10-20 (abdominal and carotid): only the abdominal one has a
    folder. The carotid exam has none yet, and must not be handed the abdominal
    report just because the name and day match."""
    _patch_bucket(monkeypatch, _omonova_bucket(ABDOMINAL, ECHO))

    carotid = _locate("1790197999", date(2026, 10, 20))

    assert carotid is None


def test_the_date_does_not_matter_when_the_appointment_id_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_bucket(monkeypatch, _omonova_bucket(ECHO))

    # The exam was moved to another day after the folder was named.
    assert _locate("1790167726", date(2026, 10, 25)) is not None


def test_an_older_folder_with_no_appointment_id_is_still_used_by_name_and_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = "tricefy/OMONOVA^DURMAMA^_20261017/"
    _patch_bucket(monkeypatch, _omonova_bucket(folder, ABDOMINAL))

    blob = _locate("1790167726", date(2026, 10, 17))

    assert blob is not None and blob.name.startswith(folder)


def test_a_folder_with_a_different_appointment_id_is_never_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same patient, same day, but the folder is for appointment 1790197523.
    _patch_bucket(monkeypatch, _omonova_bucket(ABDOMINAL))

    assert _locate("1790167726", date(2026, 10, 20)) is None


def test_two_folders_with_the_same_appointment_id_are_not_guessed_between(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_bucket(
        monkeypatch,
        _omonova_bucket(ECHO, "tricefy/OMONOVA^DURMAMA^_1790167726_20261018/"),
    )

    assert _locate("1790167726", date(2026, 10, 17)) is None


def test_the_appointment_id_must_match_whole_not_part_of_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_bucket(monkeypatch, _omonova_bucket("tricefy/OMONOVA^DURMAMA^_17901677260_20261017/"))

    assert _locate("1790167726", date(2026, 10, 17)) is None


def test_the_appointment_id_matches_whatever_the_case(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = "tricefy/Tester^Tester^_apt123_20260914/"
    _patch_bucket(monkeypatch, _omonova_bucket(folder))

    blob = gcs_reports.locate_tricefy_blob(
        first_name="Tester",
        last_name="Tester",
        exam_date=date(2026, 9, 14),
        appointment_id="APT123",
    )

    assert blob is not None and blob.name.startswith(folder)


def test_a_blank_appointment_id_falls_back_to_name_and_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = "tricefy/OMONOVA^DURMAMA^_20261017/"
    _patch_bucket(monkeypatch, _omonova_bucket(folder))

    assert _locate("   ", date(2026, 10, 17)) is not None


def test_the_report_search_passes_the_selected_exams_appointment_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime
    from types import SimpleNamespace

    _patch_bucket(monkeypatch, _omonova_bucket(ABDOMINAL, ECHO))
    exam = SimpleNamespace(
        appointment_type="Echocardiogram (Heart Ultrasound)",
        appointment_id="1790167726",
        first_name="Durmama",
        last_name="Omonova",
        appointment_datetime=datetime(2026, 10, 17, 10, 0),
        date=None,
    )

    blob = gcs_reports.locate_report_blob(exam)  # type: ignore[arg-type]

    assert blob is not None and blob.name.startswith(ECHO)
