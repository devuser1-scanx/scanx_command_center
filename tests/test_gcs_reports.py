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
