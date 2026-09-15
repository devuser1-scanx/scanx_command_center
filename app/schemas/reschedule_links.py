from __future__ import annotations

from pydantic import BaseModel


class RescheduleLinkResponse(BaseModel):
    url: str
