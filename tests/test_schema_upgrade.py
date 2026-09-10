"""`fsmes init-db` on a database made by a release that shipped no migrations.

The bug this pins: the wheel carried no `alembic.ini` and no migration
scripts, so outside a source checkout `init-db` fell back to `create_all` -
new tables, no `ALTER` - and printed "Database schema is up to date" either
way. A plant that installed 0.1.2 from PyPI and upgraded would have run new
code against an old schema and been told it was fine.

Everything here works on real SQLite files and runs the real migrations, so
what is being tested is the chain that ships, not a description of it.
"""
from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

import fsmes.domain  # noqa: F401  (importing registers every table)
from fsmes import schema as schema_mod
from fsmes.db import Base


def _url(path) -> str:
    return f"sqlite:///{path}"


def _created_by_create_all(path) -> None:
    """A database the way a 0.1.x wheel made one: the model's tables, no stamp."""
    engine = create_engine(_url(path))
    Base.metadata.create_all(engine)
    engine.dispose()


def _shape_of(path, revision: str) -> dict[str, list[str]]:
    """The tables and columns a revision produces, in a scratch database."""
    command.upgrade(schema_mod.alembic_config(_url(path)), revision)
    return schema_mod.database_shape(_url(path))


def _older_revision(steps_back: int) -> str:
    revisions = [script.revision for script in
                 ScriptDirectory.from_config(schema_mod.alembic_config()).walk_revisions()]
    return revisions[steps_back]


def test_an_empty_database_is_created_at_the_current_revision(tmp_path):
    """The plain case, and the one that has to keep working: nothing there,
    so run the whole chain rather than create_all, because a database built
    by create_all is the unstamped database the rest of this file is about."""
    db = tmp_path / "fresh.db"
    said: list[str] = []

    receipt = schema_mod.upgrade_database(_url(db), echo=said.append)

    assert receipt["created"] is True
    assert receipt["revision_before"] is None
    assert receipt["revision_after"] == schema_mod.head_revision()
    assert schema_mod.current_revision(_url(db)) == schema_mod.head_revision()
    assert any("created" in line for line in said)


def test_a_database_already_at_head_is_left_alone_and_says_so(tmp_path):
    db = tmp_path / "current.db"
    schema_mod.upgrade_database(_url(db), echo=lambda line: None)
    said: list[str] = []

    schema_mod.upgrade_database(_url(db), echo=said.append)

    assert any("up to date" in line for line in said)


def test_a_database_made_before_the_migrations_shipped_is_recognised_and_stamped(tmp_path):
    """The 0.1.2 plant, upgrading. Its tables came from create_all, so there
    is no alembic_version row to say what they are. It is recognised by its
    shape, stamped, and only then are the migrations since then applied."""
    db = tmp_path / "from-0-1-x.db"
    _created_by_create_all(db)
    assert schema_mod.current_revision(_url(db)) is None
    said: list[str] = []

    receipt = schema_mod.upgrade_database(_url(db), echo=said.append)

    assert receipt["stamped"] == schema_mod.head_revision()
    assert schema_mod.current_revision(_url(db)) == schema_mod.head_revision()
    assert any("created before the migrations shipped" in line for line in said)


def test_an_unstamped_database_from_an_older_release_is_stamped_there_and_then_migrated(tmp_path):
    """Not every unstamped database matches the current schema. One several
    revisions back is stamped where it really is - not at head, which would
    skip exactly the ALTERs that make the upgrade honest.

    The stamp is checked by *shape* and not by name, because more than one
    revision can be a true answer. `identify_unstamped` compares table and
    column names and nothing else, deliberately, so that the same chain run
    on SQLite and on PostgreSQL agrees - and a revision that only widens a
    column to nullable, or only adds an index, leaves those names untouched.
    Where two adjacent revisions are the same shape, either is an honest
    stamp and the upgrade from either runs the same ALTERs. Asserting one
    name would be asserting an accident of where the chain happens to end,
    and would break on the next migration anybody adds.
    """
    older = _older_revision(3)
    db = tmp_path / "older.db"
    command.upgrade(schema_mod.alembic_config(_url(db)), older)
    with sqlite3.connect(db) as conn:  # forget the stamp, the way create_all never wrote one
        conn.execute("DROP TABLE alembic_version")
    said: list[str] = []

    receipt = schema_mod.upgrade_database(_url(db), echo=said.append)

    assert receipt["stamped"] != schema_mod.head_revision(), (
        "stamping an older database at head is the bug this whole file is about")
    assert _shape_of(tmp_path / "stamped.db", receipt["stamped"]) == _shape_of(tmp_path / "again.db", older)
    assert schema_mod.current_revision(_url(db)) == schema_mod.head_revision()
    assert any(receipt["stamped"] in line for line in said)


def test_the_rows_an_unstamped_database_already_held_survive_the_stamp(tmp_path):
    """Stamping is metadata; a plant's production is not. If this ever fails,
    the upgrade path is destroying the very history it exists to preserve."""
    db = tmp_path / "with-rows.db"
    _created_by_create_all(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO equipment (code, name, level) VALUES ('L1', 'Line One', 'line')")
    schema_mod.upgrade_database(_url(db), echo=lambda line: None)

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM equipment").fetchone()[0] == 1


def test_a_database_matching_no_revision_is_refused_rather_than_guessed_at(tmp_path):
    """Unknown is a valid answer; a stamp that is not true of the database is
    not. Nothing here can tell what wrote such a file, so it says what it
    found, names the nearest revision, and changes nothing."""
    db = tmp_path / "strange.db"
    _created_by_create_all(db)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE somebody_elses_table (id INTEGER)")

    with pytest.raises(schema_mod.SchemaError) as raised:
        schema_mod.upgrade_database(_url(db), echo=lambda line: None)

    message = str(raised.value)
    assert "somebody_elses_table" in message
    assert "Nothing has been changed" in message
    assert schema_mod.current_revision(_url(db)) is None
