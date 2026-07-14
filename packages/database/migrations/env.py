"""Alembic environment. Uses a sync driver URL derived from DATABASE_URL."""
from alembic import context
from sqlalchemy import create_engine

from packages.database.models import Base
from packages.shared.config import get_settings

target_metadata = Base.metadata


def _sync_url() -> str:
    return get_settings().database_url.replace("+asyncpg", "+psycopg2").replace(
        "+aiosqlite", ""
    )


def run_migrations_offline() -> None:
    context.configure(url=_sync_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_sync_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
