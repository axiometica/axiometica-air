"""
Alembic migration environment.

Reads DATABASE_URL from the environment so `alembic upgrade head` works inside
Docker without hard-coding credentials.  Falls back to the value in alembic.ini
for local CLI use.
"""

import logging
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the application importable from this file's location
# (migrations/ → backend/src/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentic_os.db.models import Base  # noqa: E402  (must come after sys.path tweak)

config = context.config

# Override sqlalchemy.url with DATABASE_URL env var when running inside Docker
database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# Only apply Alembic's logging config when running as a standalone CLI tool.
# When called from within the application (root logger already has handlers),
# skip fileConfig so we don't overwrite the app's JSON formatter or suppress
# INFO-level log lines by resetting the root logger to WARN.
if config.config_file_name is not None and not logging.root.handlers:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL script)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # each migration run gets a fresh connection
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
