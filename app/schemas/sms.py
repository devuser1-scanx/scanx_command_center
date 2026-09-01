from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SmsPrefillResponse(BaseModel):
    phone: str | None
    directions_link: str | None


class SendSmsResponse(BaseModel):
    id: int
    destination_number: str
    status: str
    twilio_message_sid: str | None
    error_message: str | None
    created_at: datetime
