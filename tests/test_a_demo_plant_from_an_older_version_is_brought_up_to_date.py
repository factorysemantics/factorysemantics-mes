"""`fsmes demo <plant>` brings a database an older version made up to date.

On 2026-11-12 the 0.5.0 release found that a bottling plant made on 0.3.0 or
0.4.2 - the plant on the first Windows user's PC - answered every request 500
under 0.5.0 (`no such column: equipment.retired_at`), because the start ran
the new code against the old schema. These tests hold the start to what it
does now: copy the file, migrate, say so in one line; say nothing when there
is nothing to do; refuse a database a newer version wrote, and one it cannot
migrate, with the copy's path in the sentence.

The whole start, from a real wheel on Linux and Windows, is
`.github/scripts/plant_from_wheel.py`'s last step.
"""

from __future__ import annotations

import re
import shutil
import socket
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from fsmes import plant as plants
from fsmes import schema, standalone, storage

#: The lab's pack, which the wheel ships byte for byte.
LAB_PACK = Path(__file__).resolve().parents[1] / "labs" / "multiplant" / "bottling"

#: The head 0.4.2 shipped: the schema a plant made by that release is at.
OLDER = "2c6e817b9faf"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A packaged home with the shipped bottling pack and its fleet file, and
    no processes: the start's check runs before any process does.

    The pack's port is moved to one nothing holds, so a plant this machine
    happens to run on 8010 cannot make these look like a running plant."""
    where = tmp_path / "home"
    standalone.data_dir(where).mkdir(parents=True)
    pack = standalone.packs_dir(where) / "bottling"
    shutil.copytree(LAB_PACK, pack, ignore=shutil.ignore_patterns("out"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    toml = pack / "plant.toml"
    toml.write_text(re.sub(r"(?m)^api_port\s*=\s*\d+", f"api_port = {free}",
                           toml.read_text(encoding="utf-8")), encoding="utf-8")
    monkeypatch.setenv(plants.REGISTRY_ENV, str(standalone.write_fleet(where)))
    plants.environment.cache_clear()
    yield where
    plants.environment.cache_clear()


def the_database(where: Path) -> Path:
    return standalone.data_dir(where) / "bottling.db"


def url_of(db: Path) -> str:
    return f"sqlite:///{db.as_posix()}"


def backups_of(db: Path) -> list[Path]:
    return sorted(db.parent.glob(f"{db.name}.pre-migrate-*"))


def test_a_database_an_older_version_made_is_backed_up_migrated_and_said_in_one_line(home):
    db = the_database(home)
    command.upgrade(schema.alembic_config(url_of(db)), OLDER)
    said: list[str] = []

    assert standalone.bring_up_to_date("bottling", home, echo=said.append) == 0

    copies = backups_of(db)
    assert len(copies) == 1
    head = schema.head_revision()
    assert said == [f"  bottling: database backed up to {copies[0]}, schema {OLDER} -> {head}"]
    assert storage.look(url_of(db), "test").at_head
    # The copy is the database as it was, not the one after.
    assert schema.current_revision(url_of(copies[0])) == OLDER
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(equipment)")}
    assert "retired_at" in columns


def test_a_second_start_says_nothing_about_the_schema_and_copies_nothing(home):
    db = the_database(home)
    command.upgrade(schema.alembic_config(url_of(db)), OLDER)
    standalone.bring_up_to_date("bottling", home, echo=lambda _line: None)
    said: list[str] = []

    assert standalone.bring_up_to_date("bottling", home, echo=said.append) == 0

    assert said == []
    assert len(backups_of(db)) == 1


def test_a_database_a_newer_version_made_is_refused_with_the_version_to_install(home):
    db = the_database(home)
    command.upgrade(schema.alembic_config(url_of(db)), "head")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE alembic_version SET version_num = 'ffffffffffff'")
    said: list[str] = []

    assert standalone.bring_up_to_date("bottling", home, echo=said.append) == 1

    sentence = " ".join(said)
    assert "not starting bottling" in sentence
    assert "ffffffffffff" in sentence
    assert "a newer version made it" in sentence
    assert "pip install --upgrade factorysemantics-mes" in sentence
    assert backups_of(db) == []


def test_a_migration_that_fails_keeps_the_plant_down_and_names_the_backup(home):
    db = the_database(home)
    # Tables and no stamp, matching no revision: the migrator refuses to guess.
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE nothing_we_made (id INTEGER PRIMARY KEY)")
    said: list[str] = []

    assert standalone.bring_up_to_date("bottling", home, echo=said.append) != 0

    copies = backups_of(db)
    assert len(copies) == 1
    assert said[0].startswith("  not starting bottling: its database could not be brought")
    assert str(copies[0]) in said[0]
    assert any("does not match any revision" in line for line in said[1:])
