from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    Session,
    sessionmaker,
)

from app.core.config import settings


@lru_cache
def get_prod_engine() -> Engine:
    if not settings.prod_database_url:
        raise RuntimeError(
            "PROD_DATABASE_URL is not configured. "
            "It is required to access appointment/"
            "clinic data from the production database."
        )

    return create_engine(
        settings.prod_database_url,
        pool_pre_ping=True,
    )


def get_prod_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=get_prod_engine(),
    )


def get_prod_db() -> Generator[
    Session,
    None,
    None,
]:
    """
    Session bound to the existing production ScanX database.

    Treat this connection as read-only except for the explicitly-approved
    operations isolated in app/repositories/production_writes.py:

    1. Mirror successful Command Center SMS sends into `messages`.
    2. Record sent form links in `form_tracking`.
    3. Persist Manual Check-in in `appointment` and `checkins`.

    Command Center never runs migrations against this production schema.
    """
    session_factory = get_prod_session_factory()

    db = session_factory()

    try:
        yield db
    finally:
        db.close()
