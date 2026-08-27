from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MailTransmissionItem(BaseModel):
    id: int
    file_name: str
    status: str
    gmail_message_id: str | None
    error_message: str | None


class SendMailResponse(BaseModel):
    to_addresses: str
    gmail_message_id: str | None
    transmissions: list[MailTransmissionItem]
    created_at: datetime
