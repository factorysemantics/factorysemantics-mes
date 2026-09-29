"""The migration chain has one head, and it is found from the package."""
from __future__ import annotations

from alembic.script import ScriptDirectory

from fsmes.schema import alembic_config, head_revision, script_location


def test_the_migration_chain_has_exactly_one_head():
    """Two branches each adding a migration merge into two heads, and a
    plant's `migrate` then refuses to run at all - which is how a release
    failed to promote. A merge migration joins them; this keeps it joined."""
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, f"migration heads: {heads}"
    assert head_revision() == heads[0]


def test_the_migrations_are_found_inside_the_package_not_the_working_directory():
    """A plant that installed from PyPI has no checkout and no alembic.ini.
    Until 2026-09-10 that meant `fsmes init-db` ran no migrations at all and
    said the schema was up to date, so where the scripts are resolved from is
    the whole bug: the package, never the directory anyone happens to be in."""
    location = script_location()
    assert location.name == "migrations"
    assert location.parent.name == "fsmes"
    assert (location / "env.py").is_file()
    assert list((location / "versions").glob("*.py")), "no migration scripts alongside the package"


def test_the_newest_revision_goes_down_again_and_back_up(tmp_path):
    """A downgrade is the step a plant reaches for at the worst moment.

    Run on a scratch SQLite file rather than on the suite's own database,
    because the suite builds its schema from the model and emptying it
    between tests is not the same thing as walking the chain. CI's
    `postgres` job runs the same three commands against PostgreSQL 16,
    where a batch rewrite behaves differently and an unnamed constraint
    cannot be dropped at all.

    The URL goes on the Alembic configuration and **not** in the
    environment: `migrations/env.py` falls back to `get_settings()`, which
    is cached for the life of the process, so a `MES_DATABASE_URL` set here
    is ignored the moment any earlier test has read the settings - and the
    chain then runs against whatever `sqlite:///fsmes.db` resolves to in the
    working directory. This test passed alone and failed in the suite until
    the URL moved onto the configuration, which is the difference.
    """
    from alembic import command

    scratch = tmp_path / "chain.db"
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", f"sqlite:///{scratch}")

    command.upgrade(config, "head")
    command.downgrade(config, "-1")
    command.upgrade(config, "head")

    import sqlite3
    with sqlite3.connect(scratch) as conn:
        at = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert at == head_revision()
