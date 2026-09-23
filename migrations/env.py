"""Alembic environment — URL and metadata come from the application."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401 - register models on Base.metadata
from app.config import get_settings
from app.database import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False: fileConfig()'s default (True) disables
    # every logger that already existed and isn't declared in alembic.ini's
    # [loggers] section - harmless when `alembic` runs as its own CLI
    # process (entrypoint.sh), but when `alembic.command.upgrade/downgrade`
    # is invoked programmatically inside the same process as the app (as
    # tests/integration/conftest.py's `_schema` fixture does), it silently
    # and permanently disabled the app's own loggers (e.g. "sales.cdc") for
    # the rest of that process's lifetime. Found via a real test failure,
    # not theoretical.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
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
