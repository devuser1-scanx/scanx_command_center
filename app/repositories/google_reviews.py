from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.google_reviews import CCGoogleReview


def get_google_review_url(db: Session, clinic_id: int) -> str | None:
    statement = select(CCGoogleReview.google_review_url).where(
        CCGoogleReview.clinic_id == clinic_id
    )

    return db.scalar(statement)
