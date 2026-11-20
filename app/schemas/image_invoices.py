from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ImageInvoiceResponse(BaseModel):
    """The latest "Send Image Invoice" attempt for an appointment.

    status: pending | sent | paid | failed | void | uncollectible
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    appointment_id: str
    status: str
    amount_cents: int
    currency: str
    customer_email: str
    hosted_invoice_url: str | None
    error_message: str | None
    sent_at: datetime | None
    paid_at: datetime | None
    created_at: datetime
