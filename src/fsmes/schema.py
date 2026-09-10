"""Bringing a database to the current schema, from wherever the software was installed.

Until 2026-09-10 `fsmes init-db` ran Alembic only when there was an
`alembic.ini` in the working directory, and the wheel shipped neither the
configuration nor the migration scripts. Outside a source checkout — which is
every plant that installs from PyPI — it fell back to `create_all`: new tables
appeared, no `ALTER` ever ran, and it printed "Database schema is up to date"
either way. A plant upgrading from one release to the next would have run new
code against an old schema and been told everything was fine.

So the migrations ship *inside the package* (`fsmes/migrations/`), the script
location is resolved from the package rather than from the working directory,
and this module is the one way the schema is created or moved.

The awkward case is a database that predates all of this: created by
`create_all` from a 0.1.x wheel, so it has tables but no `alembic_version` row
saying which revision they correspond to. Guessing there would be exactly the
kind of confident wrong answer the house rules exist to prevent, so this
module does not guess. It rebuilds the schema each revision in the chain would
have produced, in a throwaway SQLite database, and stamps the database only
when its tables and columns match one of them exactly. Nothing matches, and it
says what it found and stops.
"""

from __future__ import annotations

import tempfile
from importlib import resources
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect


class SchemaError(RuntimeError):
    """The database cannot be moved to the current schema without guessing."""


def script_location() -> Path:
    """Where the migration scripts are, asked of the package, not the cwd.

    In a checkout and in an installed wheel this is the same directory inside
    `fsmes`, which is the point: `fsmes init-db` behaves identically whether
    or not anyone happens to be standing in the source tree.
    """
    return Path(str(resources.files("fsmes") / "migrations"))


def alembic_config(url: str | None = None) -> Config:
    """An Alembic configuration built in memory, with no `alembic.ini` needed.

    `alembic.ini` still exists for `python -m alembic` in a checkout — writing
    a migration is a developer's job and developers have the repository — but
    nothing at runtime reads it.
    """
    config = Config()
    config.set_main_option("script_location", str(script_location()))
    if url is not None:
        # env.py prefers this over the deployment setting, which is how the
        # reconstruction below can run the chain against a scratch database.
        config.set_main_option("sqlalchemy.url", url)
    return config


def head_revision() -> str:
    """The revision the shipped migrations end at."""
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    if len(heads) != 1:
        raise SchemaError(f"the shipped migrations have {len(heads)} heads: {', '.join(heads) or 'none'}")
    return heads[0]


def _database_url(url: str | None = None) -> str:
    if url is not None:
        return url
    from fsmes.config import get_settings

    return get_settings().database_url


def current_revision(url: str | None = None) -> str | None:
    """What the database says it is at, or None if nothing has stamped it."""
    engine = create_engine(_database_url(url))
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def _shape(engine: Engine) -> dict[str, list[str]]:
    """Table names and their column names. Not types, not indexes.

    Names are what survives every dialect: the same chain run on SQLite and on
    PostgreSQL produces different type spellings and different index sets, and
    comparing those would report a difference that is not one.
    """
    inspector = inspect(engine)
    return {
        table: sorted(column["name"] for column in inspector.get_columns(table))
        for table in inspector.get_table_names()
        if table != "alembic_version"
    }


def database_shape(url: str | None = None) -> dict[str, list[str]]:
    engine = create_engine(_database_url(url))
    try:
        return _shape(engine)
    finally:
        engine.dispose()


def _shape_at(revision: str, directory: Path) -> dict[str, list[str]]:
    """The schema this revision produces, built from the migrations themselves.

    A scratch SQLite file per revision rather than one database walked
    forward: the chain has a merge in it, so "the schema after the migrations
    up to X" is only unambiguous when X is upgraded to from empty.
    """
    scratch = directory / f"{revision}.db"
    scratch.unlink(missing_ok=True)
    url = f"sqlite:///{scratch}"
    command.upgrade(alembic_config(url), revision)
    engine = create_engine(url)
    try:
        return _shape(engine)
    finally:
        engine.dispose()
        scratch.unlink(missing_ok=True)


def _differences(found: dict[str, list[str]], expected: dict[str, list[str]]) -> list[str]:
    lines = []
    for table in sorted(set(found) - set(expected)):
        lines.append(f"the database has a table {table} that no migration creates")
    for table in sorted(set(expected) - set(found)):
        lines.append(f"the database is missing the table {table}")
    for table in sorted(set(found) & set(expected)):
        extra = sorted(set(found[table]) - set(expected[table]))
        missing = sorted(set(expected[table]) - set(found[table]))
        if extra:
            lines.append(f"{table} has columns no migration creates: {', '.join(extra)}")
        if missing:
            lines.append(f"{table} is missing columns: {', '.join(missing)}")
    return lines


def identify_unstamped(url: str | None = None) -> str:
    """Which revision an unstamped database's tables correspond to, exactly.

    Walks the chain newest first, rebuilding each revision's schema in a
    throwaway database, and returns the first that matches table for table and
    column for column. Raises `SchemaError`, naming the nearest revision and
    every difference, when none does — because the alternative is stamping a
    plant's database with a number that is not true of it.

    Newest first, and not by accident. A migration that changes only a
    constraint or adds an index leaves a schema this cannot tell from its
    parent's, so several revisions can match; the newest is the safe one,
    because the shape-invisible migrations between them are then skipped
    rather than re-run against a database that already has their index.
    """
    found = database_shape(url)
    revisions = [script.revision for script in ScriptDirectory.from_config(alembic_config()).walk_revisions()]
    nearest: tuple[str, list[str]] | None = None
    with tempfile.TemporaryDirectory(prefix="fsmes-schema-") as scratch:
        for revision in revisions:
            expected = _shape_at(revision, Path(scratch))
            differences = _differences(found, expected)
            if not differences:
                return revision
            if nearest is None or len(differences) < len(nearest[1]):
                nearest = (revision, differences)
    assert nearest is not None
    revision, differences = nearest
    raise SchemaError(
        "This database has tables but no Alembic stamp, and its schema does not match any revision "
        f"in the chain. The nearest is {revision}, and it differs:\n  "
        + "\n  ".join(differences)
        + "\nNothing has been changed. A database in this state was not made by a release of this "
        "software; say what made it in an issue and it can be handled properly, or restore the "
        "backup and stay on the version that wrote it."
    )


def upgrade_database(url: str | None = None, echo=print) -> dict:
    """Create or upgrade the schema, and say what was done.

    Returns a receipt: `revision_before` (None when the database was empty or
    unstamped), `stamped` (the revision an unstamped database was recognised
    as), `revision_after`, and `created` for a database that did not exist.
    """
    target = _database_url(url)
    head = head_revision()
    before = current_revision(target)
    shape = database_shape(target)
    receipt: dict = {"revision_before": before, "stamped": None, "created": False, "revision_after": head}

    if before is None and shape:
        stamped = identify_unstamped(target)
        command.stamp(alembic_config(target), stamped)
        receipt["stamped"] = stamped
        if stamped == head:
            echo(f"This database was created before the migrations shipped. Its schema matches revision "
                 f"{stamped}, which is the current one; it is now stamped and nothing else was changed.")
            return receipt
        echo(f"This database was created before the migrations shipped. Its schema matches revision "
             f"{stamped} exactly, so it is stamped there and the migrations since then now run.")
        before = stamped
    elif not shape:
        receipt["created"] = True

    if before == head:
        echo(f"Database schema is up to date ({head}).")
        return receipt

    command.upgrade(alembic_config(target), "head")
    if receipt["created"]:
        echo(f"Database schema created at {head}.")
    else:
        echo(f"Database schema upgraded: {before} -> {head}.")
    return receipt
