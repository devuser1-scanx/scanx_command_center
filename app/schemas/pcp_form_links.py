from __future__ import annotations

from pydantic import BaseModel


class PcpFormLinkResponse(BaseModel):
    url: str
