from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.prod_session import get_prod_session_factory
from app.integrations import inbox_store, signed_urls, twilio_client
from app.integrations.twilio_client import TwilioApiError
from app.models.auth import CCUser
from app.repositories import inbox as inbox_repository
from app.schemas.inbox import (
    CatalogOption,
    ConversationResponse,
    InboxCatalogResponse,
    InboxMessageResponse,
    SendResponse,
)
from app.services import inbox_catalog
from app.services.audit import record_user_activity

# The Patient Inbox (Messages). Conversations live in Firestore, shared with the
# standalone Inbox service (which also receives the patients' replies from
# Twilio); Command Center sends through its own Twilio settings, records what it
# sends in the same conversations, and keeps its own audit trail (who sent or
# deleted what; message text and full numbers are never audited).

logger = logging.getLogger(__name__)

LIST_LIMIT = 100
_RESERVED_ID = re.compile(r"^__.*__$")


class _Context:
    """Who is acting, for the audit log."""

    def __init__(self, db: Session, user: CCUser, ip: str | None, user_agent: str | None) -> None:
        self.db = db
        self.user = user
        self.ip = ip
        self.user_agent = user_agent


def context(db: Session, user: CCUser, ip: str | None, user_agent: str | None) -> _Context:
    return _Context(db, user, ip, user_agent)


def _full_name(user: CCUser) -> str:
    return f"{user.first_name} {user.last_name}".strip()[:100]


def normalize_patient_phone(raw: str) -> tuple[str, str]:
    """(E.164 number, conversation id) from what a person typed. The id is the
    number's digits, the same key the standalone Inbox files a conversation under.
    Ten digits without a "+" are taken as a US number."""
    digits = re.sub(r"\D", "", raw)

    if len(digits) == 10 and not raw.strip().startswith("+"):
        digits = f"1{digits}"

    if not 8 <= len(digits) <= 15:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter a valid phone number, with the country code.",
        )

    return f"+{digits}", digits


def _check_id(conversation_id: str) -> str:
    # Firestore document ids: not "." / "..", not "__name__", not too long.
    if (
        not conversation_id
        or len(conversation_id) > 100
        or conversation_id in {".", ".."}
        or _RESERVED_ID.match(conversation_id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    return conversation_id


def _last4(phone: str) -> str:
    return re.sub(r"\D", "", phone)[-4:]


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------


def _patient_names(phones: list[str]) -> dict[str, str]:
    """Names are a nicety: if the production database cannot be reached the
    inbox still works, with numbers only."""
    try:
        prod_db = get_prod_session_factory()()
    except RuntimeError:
        logger.warning("Production database not configured; inbox shows numbers only.")
        return {}

    try:
        return inbox_repository.patient_names_by_last_ten(
            prod_db, [inbox_repository.last_ten_digits(phone) for phone in phones]
        )
    except SQLAlchemyError:
        logger.exception("Patient name lookup failed; inbox shows numbers only.")
        return {}
    finally:
        prod_db.close()


def _conversation_response(raw: dict[str, Any], names: dict[str, str]) -> ConversationResponse:
    phone = str(raw.get("patientPhone") or raw["id"])

    return ConversationResponse(
        id=raw["id"],
        patient_phone=phone,
        patient_name=names.get(inbox_repository.last_ten_digits(phone)),
        business_phone=raw.get("businessPhone"),
        status=str(raw.get("status") or "open"),
        assigned_to=raw.get("assignedTo"),
        last_message=raw.get("lastMessage"),
        last_message_at=raw.get("lastMessageAt"),
        created_at=raw.get("createdAt"),
        unread_count=int(raw.get("unreadCount") or 0),
    )


def list_conversations() -> list[ConversationResponse]:
    raw = inbox_store.list_conversations(None, LIST_LIMIT)
    phones = [str(item.get("patientPhone") or item["id"]) for item in raw]
    names = _patient_names(phones)

    return [_conversation_response(item, names) for item in raw]


def list_messages(conversation_id: str) -> list[InboxMessageResponse]:
    _check_id(conversation_id)

    return [
        InboxMessageResponse(
            id=item["id"],
            sid=item.get("sid"),
            direction=str(item.get("direction") or ""),
            body=str(item.get("body") or ""),
            from_number=item.get("from"),
            to_number=item.get("to"),
            status=item.get("status"),
            created_at=item.get("createdAt"),
            sent_by=item.get("sentBy"),
            type=item.get("type"),
            media_url=item.get("mediaUrl"),
            meta=item.get("meta"),
        )
        for item in inbox_store.list_messages(conversation_id)
    ]


def mark_read(conversation_id: str) -> None:
    inbox_store.mark_read(_check_id(conversation_id))


def catalog() -> InboxCatalogResponse:
    return InboxCatalogResponse(
        rcs_exams=[CatalogOption(value=exam.key, label=exam.name) for exam in inbox_catalog.EXAMS],
        generic_sales_exams=[
            CatalogOption(value=exam.key, label=exam.name)
            for exam in inbox_catalog.EXAMS
            if exam.generic_sales
        ],
        languages=[
            CatalogOption(value=code, label=label)
            for code, label in inbox_catalog.MISSED_CALL_LANGUAGES
        ],
        titles=list(inbox_catalog.CARD_TITLES),
    )


# ---------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------


def rcs_card_content_sid(exam_key: str) -> str | None:
    """The Content Template of an exam's RCS card: the exam's own
    (TWILIO_RCS_CARD_CONTENT_SID_<EXAM>) when one is set, else the default."""
    return (
        getattr(settings, f"twilio_rcs_card_content_sid_{exam_key}", None)
        or settings.twilio_rcs_card_content_sid
    )


def _deliver(to_phone: str, content_sid: str, variables: dict[str, str]) -> str:
    """Sends through Twilio; the message sid."""
    try:
        return twilio_client.send_content_message(
            to=to_phone, content_sid=content_sid, content_variables=variables
        ).message_sid
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Messaging is not configured.",
        ) from exc
    except TwilioApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc


def _media_url(image_path: str) -> str:
    try:
        return signed_urls.get_signed_media_url(image_path)
    except signed_urls.SignedUrlError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _finish_send(
    ctx: _Context,
    *,
    conversation_id: str,
    patient_phone: str,
    sid: str,
    kind: str,
    last_message: str,
    reopen: bool,
    extra: dict[str, Any] | None = None,
) -> SendResponse:
    """Records a sent message in its conversation and in the audit log."""
    message: dict[str, Any] = {
        "sid": sid,
        "direction": "outbound",
        "body": last_message,
        "to": patient_phone,
        "status": "sent",
        "sentBy": _full_name(ctx.user),
        "sentByCcUserId": ctx.user.id,
        **(extra or {}),
    }

    if settings.twilio_voice_from_number:
        message["from"] = settings.twilio_voice_from_number

    try:
        inbox_store.record_outbound(
            conversation_id,
            patient_phone=patient_phone,
            last_message=last_message,
            message=message,
            reopen=reopen,
            business_phone=settings.twilio_voice_from_number,
        )
    except Exception as exc:
        logger.exception("Message %s was sent but could not be saved to the inbox.", sid)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The message was sent, but it could not be saved to the inbox.",
        ) from exc

    record_user_activity(
        ctx.db,
        actor_user_id=ctx.user.id,
        action="messages.sent",
        resource_type="inbox_message",
        resource_id=sid,
        details={"kind": kind, "destination_last4": _last4(patient_phone)},
        ip_address=ctx.ip,
        user_agent=ctx.user_agent,
    )
    ctx.db.commit()

    return SendResponse(conversation_id=conversation_id, sid=sid)


def send_new_conversation(ctx: _Context, to_phone: str, message: str) -> SendResponse:
    phone, conversation_id = normalize_patient_phone(to_phone)
    text = message.strip()

    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Message required."
        )

    sid = _deliver(phone, inbox_catalog.TEXT_CONTENT_SID, {"1": text})

    return _finish_send(
        ctx,
        conversation_id=conversation_id,
        patient_phone=phone,
        sid=sid,
        kind="text",
        last_message=text,
        reopen=True,
    )


def send_reply(ctx: _Context, conversation_id: str, body: str) -> SendResponse:
    _check_id(conversation_id)
    text = body.strip()

    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Message body required."
        )

    conversation = inbox_store.get_conversation(conversation_id)

    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    phone = str(conversation.get("patientPhone") or "")
    sid = _deliver(phone, inbox_catalog.TEXT_CONTENT_SID, {"1": text})

    return _finish_send(
        ctx,
        conversation_id=conversation_id,
        patient_phone=phone,
        sid=sid,
        kind="text",
        last_message=text,
        reopen=False,
    )


def send_rcs_card(
    ctx: _Context,
    *,
    exam: str,
    title: str,
    patient_name: str,
    doctor_name: str,
    patient_phone: str,
) -> SendResponse:
    card = inbox_catalog.find_exam(exam)

    if card is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown exam type: {exam}"
        )

    if title not in inbox_catalog.CARD_TITLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown title."
        )

    content_sid = rcs_card_content_sid(card.key)

    if not content_sid:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Messaging is not configured."
        )

    phone, conversation_id = normalize_patient_phone(patient_phone)
    sid = _deliver(
        phone,
        content_sid,
        {
            "1": title,
            "2": patient_name.strip(),
            "3": doctor_name.strip(),
            "4": _media_url(card.image),
            "5": card.booking_link,
            "6": card.name,
        },
    )

    return _finish_send(
        ctx,
        conversation_id=conversation_id,
        patient_phone=phone,
        sid=sid,
        kind="rcs_card",
        last_message=f"RCS Card Sent: {title} {patient_name.strip()} — {card.key}",
        reopen=False,
        extra={
            "type": "rcs-card",
            "meta": {
                "exam": card.key,
                "title": title,
                "patientName": patient_name.strip(),
                "doctorName": doctor_name.strip(),
            },
        },
    )


def send_missed_call_card(ctx: _Context, *, patient_phone: str, language: str) -> SendResponse:
    code = language.strip().lower()
    content_sid = inbox_catalog.MISSED_CALL_CONTENT_SIDS.get(code)

    if content_sid is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown language: {language}"
        )

    phone, conversation_id = normalize_patient_phone(patient_phone)
    sid = _deliver(phone, content_sid, {"1": _media_url(inbox_catalog.MISSED_CALL_IMAGE)})

    return _finish_send(
        ctx,
        conversation_id=conversation_id,
        patient_phone=phone,
        sid=sid,
        kind="missed_call_card",
        last_message=f"RCS Card Sent: Missed Call ({code.upper()})",
        reopen=False,
        extra={"type": "rcs-card", "meta": {"card": "missed_call", "language": code}},
    )


def send_generic_sales_card(ctx: _Context, *, exam: str, patient_phone: str) -> SendResponse:
    card = inbox_catalog.find_exam(exam, generic_sales=True)

    if card is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown exam type: {exam}"
        )

    phone, conversation_id = normalize_patient_phone(patient_phone)
    sid = _deliver(
        phone,
        inbox_catalog.GENERIC_SALES_CONTENT_SID,
        {"1": _media_url(card.image), "2": card.booking_link},
    )

    return _finish_send(
        ctx,
        conversation_id=conversation_id,
        patient_phone=phone,
        sid=sid,
        kind="generic_sales_card",
        last_message=f"Generic Sales Card Sent: {card.key}",
        reopen=False,
        extra={"type": "rcs-card", "meta": {"card": "generic_sales", "exam": card.key}},
    )


# ---------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------


def delete_conversation(ctx: _Context, conversation_id: str) -> None:
    _check_id(conversation_id)
    conversation = inbox_store.get_conversation(conversation_id)

    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")

    inbox_store.delete_conversation(conversation_id)

    record_user_activity(
        ctx.db,
        actor_user_id=ctx.user.id,
        action="messages.conversation_deleted",
        resource_type="inbox_conversation",
        details={
            "destination_last4": _last4(str(conversation.get("patientPhone") or conversation_id))
        },
        ip_address=ctx.ip,
        user_agent=ctx.user_agent,
    )
    ctx.db.commit()
