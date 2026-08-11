from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401
from app.core.config import settings
from app.db.base import Base

config = context.config

# The application safely URL-encodes special password characters.
# For example, "@" becomes "%40".
database_url = str(settings.sqlalchemy_database_uri)

# Alembic stores this value through ConfigParser, where "%" is used for
# interpolation. Escape it so encoded values such as "%40" remain valid.
alembic_database_url = database_url.replace("%", "%%")

config.set_main_option(
    "sqlalchemy.url",
    alembic_database_url,
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without creating a database connection."""

    context.configure(
        # This value is passed directly to Alembic, so use the normal URL.
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using a live database connection."""

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()