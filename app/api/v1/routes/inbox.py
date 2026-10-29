from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies.auth import require_permission
from app.api.dependencies.fax_portal import request_ip
from app.db.session import get_db
from app.models.auth import CCUser
from app.schemas.inbox import (
    ConversationsResponse,
    GenericSalesCardRequest,
    InboxCatalogResponse,
    InboxMessagesResponse,
    MissedCallCardRequest,
    NewConversationRequest,
    RcsCardRequest,
    ReplyRequest,
    SendResponse,
)
from app.services import inbox

# The Patient Inbox (Messages): reading needs messages.view; sending texts and
# cards, and deleting a conversation, needs messages.send.
router = APIRouter(prefix="/inbox", tags=["inbox"])


def _context(request: Request, db: Session, user: CCUser) -> inbox._Context:
    return inbox.context(db, user, request_ip(request), request.headers.get("user-agent"))


# ---------------------------------------------------------------------
# Reading (messages.view)
# ---------------------------------------------------------------------


@router.get("/conversations", response_model=ConversationsResponse)
def list_conversations(
    current_user: CCUser = Depends(require_permission("messages.view")),
) -> ConversationsResponse:
    del current_user

    return ConversationsResponse(items=inbox.list_conversations())


@router.get("/conversations/{conversation_id}/messages", response_model=InboxMessagesResponse)
def list_messages(
    conversation_id: str,
    current_user: CCUser = Depends(require_permission("messages.view")),
) -> InboxMessagesResponse:
    del current_user

    return InboxMessagesResponse(items=inbox.list_messages(conversation_id))


@router.post("/conversations/{conversation_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_conversation_read(
    conversation_id: str,
    current_user: CCUser = Depends(require_permission("messages.view")),
) -> Response:
    del current_user
    inbox.mark_read(conversation_id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/catalog", response_model=InboxCatalogResponse)
def get_catalog(
    current_user: CCUser = Depends(require_permission("messages.view")),
) -> InboxCatalogResponse:
    del current_user

    return inbox.catalog()


# ---------------------------------------------------------------------
# Sending and deleting (messages.send)
# ---------------------------------------------------------------------


@router.post("/conversations/new", response_model=SendResponse)
def start_conversation(
    payload: NewConversationRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("messages.send")),
) -> SendResponse:
    return inbox.send_new_conversation(
        _context(request, db, current_user), payload.to_phone, payload.message
    )


@router.post("/conversations/{conversation_id}/reply", response_model=SendResponse)
def reply(
    conversation_id: str,
    payload: ReplyRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("messages.send")),
) -> SendResponse:
    return inbox.send_reply(_context(request, db, current_user), conversation_id, payload.body)


@router.post("/send-rcs-card", response_model=SendResponse)
def send_rcs_card(
    payload: RcsCardRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("messages.send")),
) -> SendResponse:
    return inbox.send_rcs_card(
        _context(request, db, current_user),
        exam=payload.exam,
        title=payload.title,
        patient_name=payload.patient_name,
        doctor_name=payload.doctor_name,
        patient_phone=payload.patient_phone,
    )


@router.post("/send-missed-call-card", response_model=SendResponse)
def send_missed_call_card(
    payload: MissedCallCardRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("messages.send")),
) -> SendResponse:
    return inbox.send_missed_call_card(
        _context(request, db, current_user),
        patient_phone=payload.patient_phone,
        language=payload.language,
    )


@router.post("/send-generic-sales-card", response_model=SendResponse)
def send_generic_sales_card(
    payload: GenericSalesCardRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("messages.send")),
) -> SendResponse:
    return inbox.send_generic_sales_card(
        _context(request, db, current_user),
        exam=payload.exam,
        patient_phone=payload.patient_phone,
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CCUser = Depends(require_permission("messages.send")),
) -> Response:
    inbox.delete_conversation(_context(request, db, current_user), conversation_id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
