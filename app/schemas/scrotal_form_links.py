from __future__ import annotations

from pydantic import BaseModel


class ScrotalFormLinkResponse(BaseModel):
    url: str
