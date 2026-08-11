from sqlalchemy.orm import DeclarativeBase


class ProdBase(DeclarativeBase):
    """
    Declarative base for read-only models mapped onto the existing
    production ScanX database.

    This is deliberately a separate base from `app.db.base.Base`. Alembic's
    `target_metadata` (migrations/env.py) only ever points at `Base.metadata`,
    so anything declared against `ProdBase` is structurally invisible to
    migrations - autogenerate can never propose altering or dropping these
    tables.
    """
