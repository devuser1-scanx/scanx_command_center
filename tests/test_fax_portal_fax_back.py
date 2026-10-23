from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fax_portal_helpers import auth_headers, make_card, make_fax, make_user
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations import gcs_faxes
from app.integrations.gmail_client import GmailApiError
from app.models.fax_portal import (
    CCFpActivityLog,
    CCFpCallLog,
    CCFpFax,
    CCFpMessageLog,
    CCFpReferralBoard,
)
from app.services import fax_portal_fax_back

KAREN_SUBJECT = (
    "Referral Update Karen Quinonez: Unable to reach patient Karen Quinonez after multiple attempts"
)

SECRET_TEXT = "Hola, su cita de ultrasonido de la vesicula es el viernes"


class FakeEmail:
    """Stands in for the Gmail send used by the patient profile's Fax button."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def __call__(self, **kwargs: Any) -> str:
        self.sent.append(kwargs)

        if self.error is not None:
            raise self.error

        return "gmail-msg-1"


@pytest.fixture
def email(monkeypatch: pytest.MonkeyPatch) -> FakeEmail:
    fake = FakeEmail()
    monkeypatch.setattr(fax_portal_fax_back, "send_email", fake)

    return fake


class FakeBucket:
    """Stands in for the fax bucket: the original fax PDF (or none)."""

    def __init__(self) -> None:
        self.pdf: bytes | None = b"%PDF-original"
        self.error: Exception | None = None

    def find(self, job_id: str) -> str | None:
        return None if self.pdf is None else f"faxes/{job_id}.pdf"

    def download(self, gcs_uri: str) -> bytes:
        if self.error is not None:
            raise self.error

        if self.pdf is None:
            raise gcs_faxes.FaxPdfNotFoundError("missing")

        return self.pdf


@pytest.fixture(autouse=True)
def bucket(monkeypatch: pytest.MonkeyPatch) -> FakeBucket:
    fake = FakeBucket()
    monkeypatch.setattr(gcs_faxes, "find_fax_pdf", fake.find)
    monkeypatch.setattr(gcs_faxes, "download_fax_pdf", fake.download)

    return fake


@pytest.fixture
def agent(db_session: Session) -> Any:
    return make_user(db_session, "front_desk")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    # October 2026 is daylight time in Chicago: 22:00 UTC is 5 PM CT.
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def unreachable(db: Session, agent: Any, **kwargs: Any) -> CCFpReferralBoard:
    """A referral in Fax Provider Name Pending, with calls and a text on record."""
    fax = make_fax(db)
    fax.originating_e164 = "+19725550100"
    kwargs.setdefault("status", "fax_provider_name_pending")
    kwargs.setdefault("first_name", "Karen")
    kwargs.setdefault("last_name", "Quinonez")
    kwargs.setdefault("physician_name", "Dr. Fatema Dabdoub")
    kwargs.setdefault("physician_fax_no", "(214) 555-0199")
    card = make_card(db, fax, **kwargs)
    db.flush()

    for when, result in [
        (at(16, 22, 14), "voicemail"),
        (at(19, 2, 10), "callback_requested"),
        (at(21, 1, 51), "no_answer"),
    ]:
        db.add(
            CCFpCallLog(
                referral_id=card.id,
                user_id=agent.id,
                to_number="+12145550100",
                result=result,
                started_at=when,
            )
        )

    db.add(
        CCFpMessageLog(
            referral_id=card.id,
            direction="outbound",
            channel="sms",
            user_id=agent.id,
            to_number="+12145550100",
            body=SECRET_TEXT,
            status="delivered",
            created_at=at(19, 2, 12),
        )
    )
    db.commit()

    return card


def draft(client: TestClient, user: Any, card: CCFpReferralBoard) -> dict[str, Any]:
    response = client.get(
        f"/fax-portal/referrals/{card.id}/fax-back", headers=auth_headers(client, user)
    )

    assert response.status_code == 200, response.text

    return response.json()


def send(client: TestClient, user: Any, card: CCFpReferralBoard, **body: Any):
    body.setdefault("destination_number", "(972) 555-0100")
    body.setdefault("body", "We could not reach the patient.")

    return client.post(
        f"/fax-portal/referrals/{card.id}/fax-back", json=body, headers=auth_headers(client, user)
    )


# ---------------------------------------------------------------------
# The draft
# ---------------------------------------------------------------------


def test_the_draft_goes_to_the_number_the_referral_was_received_from(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    card = unreachable(db_session, agent)

    result = draft(client, agent, card)

    assert result["destination_number"] == "9725550100@westfax.com"
    assert "received from" in result["destination_source"]
    assert result["subject"] == KAREN_SUBJECT
    assert result["can_mark_didnt_respond"] is True


def test_without_the_senders_number_the_physicians_number_on_the_card_is_used(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    card = unreachable(db_session, agent)
    db_session.get(CCFpFax, card.fax_id).originating_e164 = None
    db_session.commit()

    result = draft(client, agent, card)

    assert result["destination_number"] == "2145550199@westfax.com"
    assert "referring physician's fax number" in result["destination_source"]


def test_with_no_number_at_all_the_field_is_blank_and_says_so(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    card = unreachable(db_session, agent, physician_fax_no=None)
    db_session.get(CCFpFax, card.fax_id).originating_e164 = None
    db_session.commit()

    result = draft(client, agent, card)

    assert result["destination_number"] == "" and "enter it" in result["destination_source"]


def test_the_body_lists_each_attempt_oldest_first_with_the_result(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    card = unreachable(db_session, agent, dob=datetime(1981, 3, 14).date())
    body = draft(client, agent, card)["body"]

    assert body.startswith("Re: Referral for Karen Quinonez (date of birth 03/14/1981)")
    assert "To Dr. Fatema Dabdoub:" in body
    assert "3 phone calls and 1 text message" in body
    lines = [line for line in body.splitlines() if line[:2] in {"1.", "2.", "3.", "4."}]
    assert lines == [
        "1. Oct 16, 2026 5:14 PM CT - Phone call - Left voicemail - Agent",
        "2. Oct 18, 2026 9:10 PM CT - Phone call - Callback requested - Agent",
        "3. Oct 18, 2026 9:12 PM CT - Text message - Sent, delivered - Agent",
        "4. Oct 20, 2026 8:51 PM CT - Phone call - No answer - Agent",
    ]
    assert body.rstrip().endswith("Thank you,\nScanX Health")


def test_the_body_never_carries_the_message_text_or_an_agents_name(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    card = unreachable(db_session, agent)

    body = draft(client, agent, card)["body"]

    assert SECRET_TEXT not in body and "vesicula" not in body
    assert agent.first_name not in body and agent.last_name not in body
    assert body.count("- Agent") == 4


def test_a_patient_reply_is_listed_without_its_content(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    card = unreachable(db_session, agent)
    db_session.add(
        CCFpMessageLog(
            referral_id=card.id,
            direction="inbound",
            channel="sms",
            to_number="+12145550100",
            body="Please stop texting me about my gallbladder",
            status="received",
            created_at=at(20, 3),
        )
    )
    db_session.commit()

    body = draft(client, agent, card)["body"]

    assert "Patient replied" in body and "gallbladder" not in body


def test_calls_still_waiting_for_a_result_are_not_listed(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    card = unreachable(db_session, agent)
    db_session.add(CCFpCallLog(referral_id=card.id, user_id=agent.id, to_number="+12145550100"))
    db_session.commit()

    assert "3 phone calls" in draft(client, agent, card)["body"]


def test_a_referral_not_in_fax_provider_pending_cannot_be_marked_didnt_respond(
    client: TestClient, db_session: Session, agent: Any
) -> None:
    card = unreachable(db_session, agent, status="follow_up_due")

    assert draft(client, agent, card)["can_mark_didnt_respond"] is False


# ---------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------


def test_the_fax_goes_through_the_westfax_email_gateway_like_the_patient_fax(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent)

    response = send(
        client,
        agent,
        card,
        destination_number="(972) 555-0100",
        subject=KAREN_SUBJECT,
        body="Line one\nLine <two> & more",
    )

    assert response.status_code == 200, response.text
    [sent] = email.sent
    assert sent["to"] == ["9725550100@westfax.com"]
    assert sent["cc"] == [settings.fax_feedback_email] and sent["bcc"] == []
    assert sent["subject"] == KAREN_SUBJECT
    assert sent["attachments"] == []
    # The message as a simple HTML body: escaped, line breaks kept.
    assert "Line one<br>\nLine &lt;two&gt; &amp; more" in sent["html_body"]
    assert response.json()["destination_number"] == "9725550100@westfax.com"
    assert (
        response.json()["email_message_id"] == "gmail-msg-1" and response.json()["moved_to"] is None
    )


def test_a_destination_typed_as_an_email_address_still_uses_its_digits(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent)

    send(client, agent, card, destination_number="9725550100@westfax.com")

    assert email.sent[0]["to"] == ["9725550100@westfax.com"]


def test_a_blank_subject_falls_back_to_the_default(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent)

    send(client, agent, card, subject="   ")

    assert email.sent[0]["subject"] == KAREN_SUBJECT


def test_the_send_is_audited_and_shows_on_the_timeline_without_the_number(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent)

    send(client, agent, card)

    entry = db_session.scalar(select(CCFpActivityLog).where(CCFpActivityLog.action == "fax_back"))
    assert entry.user_id == agent.id and entry.referral_id == card.id
    assert entry.details == {
        "destination_last4": "0100",
        "email_message_id": "gmail-msg-1",
        "moved_to": None,
    }
    assert "972" not in str(entry.details)

    timeline = client.get(
        f"/fax-portal/referrals/{card.id}/case", headers=auth_headers(client, agent)
    ).json()["timeline"]
    assert [e["title"] for e in timeline if e["kind"] == "edit"] == ["Faxed the referring provider"]


def test_it_can_also_move_the_referral_to_didnt_respond(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent)

    response = send(client, agent, card, mark_didnt_respond=True)

    assert response.status_code == 200, response.text
    assert response.json()["moved_to"] == "didnt_respond"
    db_session.refresh(card)
    assert card.status == "didnt_respond"


def test_moving_to_didnt_respond_is_refused_before_anything_is_sent_unless_pending(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent, status="follow_up_due")

    response = send(client, agent, card, mark_didnt_respond=True)

    assert response.status_code == 422 and email.sent == []
    db_session.refresh(card)
    assert card.status == "follow_up_due"


@pytest.mark.parametrize(
    ("changes", "text"),
    [
        ({"destination_number": "555-0100"}, "full fax number"),
        ({"destination_number": "no number"}, "full fax number"),
        ({"body": "   "}, "needs a message"),
    ],
)
def test_incomplete_requests_are_refused(
    client: TestClient,
    db_session: Session,
    agent: Any,
    email: FakeEmail,
    changes: dict[str, Any],
    text: str,
) -> None:
    card = unreachable(db_session, agent)

    response = send(client, agent, card, **changes)

    assert response.status_code == 422 and text in response.json()["detail"]
    assert email.sent == []


@pytest.mark.parametrize("status", ["review_required", "appointment_booked", "not_referral"])
def test_referrals_in_review_or_closed_cannot_be_faxed_back(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail, status: str
) -> None:
    card = unreachable(db_session, agent, status=status)

    response = send(client, agent, card)

    assert response.status_code == 422 and email.sent == []


def test_a_gmail_failure_is_reported_and_recorded_but_nothing_is_moved(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent)
    email.error = GmailApiError("quota exceeded")

    response = send(client, agent, card, mark_didnt_respond=True)

    assert response.status_code == 502 and "quota exceeded" in response.json()["detail"]
    db_session.refresh(card)
    assert card.status == "fax_provider_name_pending"
    actions = db_session.scalars(select(CCFpActivityLog.action)).all()
    assert "fax_back_failed" in actions and "fax_back" not in actions


def test_unconfigured_email_fails_cleanly(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent)
    email.error = RuntimeError("GMAIL_SENDER_EMAIL is not configured")

    response = send(client, agent, card)

    assert response.status_code == 502 and "not set up" in response.json()["detail"]
    assert "GMAIL" not in response.text


def test_faxing_back_needs_portal_access(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent)
    sonographer = make_user(db_session, "sonographer")

    assert send(client, sonographer, card).status_code == 403
    assert (
        client.get(
            f"/fax-portal/referrals/{card.id}/fax-back", headers=auth_headers(client, sonographer)
        ).status_code
        == 403
    )
    assert email.sent == []


@pytest.mark.parametrize(
    "typed",
    [
        "(972) 555-0100",
        "972-555-0100",
        "972.555.0100",
        "+1 (972) 555-0100",
        "1-972-555-0100",
        " 9725550100 ",
    ],
)
def test_any_way_of_typing_the_number_is_sent_as_plain_digits_at_westfax(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail, typed: str
) -> None:
    card = unreachable(db_session, agent)

    response = send(client, agent, card, destination_number=typed)

    assert response.status_code == 200, response.text
    assert email.sent[0]["to"] == ["9725550100@westfax.com"]
    assert response.json()["destination_number"] == "9725550100@westfax.com"


def test_the_original_fax_pdf_is_attached_by_default(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent)

    assert draft(client, agent, card)["original_fax_available"] is True

    response = send(client, agent, card)

    assert response.status_code == 200, response.text
    [attachment] = email.sent[0]["attachments"]
    assert attachment.content == b"%PDF-original"
    assert attachment.content_type == "application/pdf"


def test_the_original_fax_can_be_left_off(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent)

    response = send(client, agent, card, attach_original=False)

    assert response.status_code == 200, response.text
    assert email.sent[0]["attachments"] == []


def test_a_missing_original_blocks_the_send_until_it_is_unticked(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail, bucket: FakeBucket
) -> None:
    card = unreachable(db_session, agent)
    bucket.pdf = None

    blocked = send(client, agent, card)

    assert blocked.status_code == 422
    assert "original fax" in blocked.json()["detail"]
    assert email.sent == []
    assert send(client, agent, card, attach_original=False).status_code == 200


def test_a_bucket_failure_is_reported_and_nothing_is_sent(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail, bucket: FakeBucket
) -> None:
    card = unreachable(db_session, agent)
    bucket.error = RuntimeError("boom")

    response = send(client, agent, card)

    assert response.status_code == 502
    assert email.sent == []


def test_a_referral_closed_as_didnt_respond_can_still_be_faxed_back(
    client: TestClient, db_session: Session, agent: Any, email: FakeEmail
) -> None:
    card = unreachable(db_session, agent, status="didnt_respond")

    assert draft(client, agent, card)["can_mark_didnt_respond"] is False

    response = send(client, agent, card)

    assert response.status_code == 200, response.text
    assert response.json()["moved_to"] is None
    assert len(email.sent) == 1
