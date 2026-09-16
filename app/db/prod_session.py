from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


@lru_cache
def get_prod_engine() -> Engine:
    if not settings.prod_database_url:
        raise RuntimeError(
            "PROD_DATABASE_URL is not configured. It is required to read "
            "appointment/clinic data from the production database."
        )

    return create_engine(settings.prod_database_url, pool_pre_ping=True)


def get_prod_session_factory() -> sessionmaker[Session]:
    return sessionmaker(autocommit=False, autoflush=False, bind=get_prod_engine())


def get_prod_db() -> Generator[Session, None, None]:
    """
    Session bound to the production ScanX database. Treat as read-only:
    repository functions using this session must never call
    add()/commit()/delete() - Command Center only ever SELECTs from this
    connection - EXCEPT the two explicitly-approved writes in
    app/repositories/production_writes.py (recording sent SMS into
    `messages`, and sent form links into `form_tracking`), which exist so
    Command Center's own sends show up in the same history production's
    other systems already write to. Do not add further writes here without
    the same explicit sign-off.
    """
    session_factory = get_prod_session_factory()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
