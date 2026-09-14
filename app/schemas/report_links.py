from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ReportLinkResponse(BaseModel):
    url: str
    expires_at: datetime
