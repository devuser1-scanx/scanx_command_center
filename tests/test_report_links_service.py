from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services import report_links as report_links_service


@dataclass
class FakeBlob:
    name: str


def test_create_report_link_for_appointment_returns_proxy_short_url(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blob = FakeBlob(name="tricefy/DOE^JANE^_20260101/Jane_Doe_Report_2026-01-01.pdf")

    monkeypatch.setattr(
        report_links_service,
        "locate_report_blob_for_appointment",
        lambda prod_db, appointment_id: blob,
    )

    captured: dict[str, str] = {}

    def fake_create_short_url(*, gcs_blob_name: str) -> str:
        captured["gcs_blob_name"] = gcs_blob_name
        return "https://reports.scanx.care/aB3xY9zQ"

    monkeypatch.setattr(report_links_service, "create_short_url", fake_create_short_url)

    result = report_links_service.create_report_link_for_appointment(
        db_session,
        appointment_id="APT-1",
    )

    assert captured["gcs_blob_name"] == blob.name
    assert result.url == "https://reports.scanx.care/aB3xY9zQ"
    assert result.expires_at > datetime.now(UTC) + timedelta(days=6)


def test_create_report_link_for_appointment_404_when_no_report_found(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        report_links_service,
        "locate_report_blob_for_appointment",
        lambda prod_db, appointment_id: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        report_links_service.create_report_link_for_appointment(
            db_session,
            appointment_id="APT-404",
        )

    assert exc_info.value.status_code == 404


def test_create_report_link_for_appointment_502_when_proxy_fails(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blob = FakeBlob(name="fibroscan/device123_APT-2.pdf")

    monkeypatch.setattr(
        report_links_service,
        "locate_report_blob_for_appointment",
        lambda prod_db, appointment_id: blob,
    )

    def failing_create_short_url(*, gcs_blob_name: str) -> str:
        raise report_links_service.PdfProxyApiError("File not found")

    monkeypatch.setattr(report_links_service, "create_short_url", failing_create_short_url)

    with pytest.raises(HTTPException) as exc_info:
        report_links_service.create_report_link_for_appointment(
            db_session,
            appointment_id="APT-2",
        )

    assert exc_info.value.status_code == 502
