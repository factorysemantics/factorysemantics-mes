"""Alembic environment — wired to MES-TWIN settings and metadata."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from fsmes import domain  # noqa: F401  (importing registers every table)
from fsmes.config import get_settings
from fsmes.db import Base

target_metadata = Base.metadata


def _url() -> str:
    """The database to migrate.

    A URL set on the configuration wins, which is how `fsmes.schema` runs the
    chain against a scratch database to work out what an unstamped one is.
    Otherwise it is the deployment's own setting - never `alembic.ini`, which
    a plant that installed from PyPI does not have.
    """
    return context.config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config = context.config.get_section(context.config.config_ini_section, {})
    config["sqlalchemy.url"] = _url()
    connectable = engine_from_config(config, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        # render_as_batch lets future ALTERs work on SQLite too
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
