from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

MESSAGE_MAX = 1600
PHONE_MAX = 40


class ConversationResponse(BaseModel):
    id: str
    patient_phone: str
    patient_name: str | None
    business_phone: str | None
    status: str
    assigned_to: str | None
    last_message: str | None
    last_message_at: datetime | None
    created_at: datetime | None
    unread_count: int


class ConversationsResponse(BaseModel):
    items: list[ConversationResponse]


class InboxMessageResponse(BaseModel):
    id: str
    sid: str | None
    direction: str
    body: str
    from_number: str | None
    to_number: str | None
    status: str | None
    created_at: datetime | None
    sent_by: str | None
    type: str | None
    media_url: str | None
    meta: dict[str, Any] | None


class InboxMessagesResponse(BaseModel):
    items: list[InboxMessageResponse]


class NewConversationRequest(BaseModel):
    to_phone: str = Field(min_length=1, max_length=PHONE_MAX)
    message: str = Field(min_length=1, max_length=MESSAGE_MAX)


class ReplyRequest(BaseModel):
    body: str = Field(min_length=1, max_length=MESSAGE_MAX)


class RcsCardRequest(BaseModel):
    exam: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=20)
    patient_name: str = Field(min_length=1, max_length=100)
    doctor_name: str = Field(min_length=1, max_length=100)
    patient_phone: str = Field(min_length=1, max_length=PHONE_MAX)


class MissedCallCardRequest(BaseModel):
    patient_phone: str = Field(min_length=1, max_length=PHONE_MAX)
    language: str = Field(min_length=1, max_length=10)


class GenericSalesCardRequest(BaseModel):
    exam: str = Field(min_length=1, max_length=50)
    patient_phone: str = Field(min_length=1, max_length=PHONE_MAX)


class SendResponse(BaseModel):
    conversation_id: str
    sid: str


class CatalogOption(BaseModel):
    value: str
    label: str


class InboxCatalogResponse(BaseModel):
    """What the card forms offer, so the frontend keeps no copy of the lists."""

    rcs_exams: list[CatalogOption]
    generic_sales_exams: list[CatalogOption]
    languages: list[CatalogOption]
    titles: list[str]
