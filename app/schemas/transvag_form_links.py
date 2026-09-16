from __future__ import annotations

from pydantic import BaseModel


class TransvagFormLinkResponse(BaseModel):
    url: str
