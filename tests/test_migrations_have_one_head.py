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
