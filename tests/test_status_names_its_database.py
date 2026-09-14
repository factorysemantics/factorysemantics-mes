"""The status commands say which database they are looking at, and look at it.

On 2026-09-14 a plant was promoted to 0.2.0. `fsmes pack apply` migrated its
PostgreSQL to head and said so; `fsmes db-status`, the next line of the same
script, said *"There is no database yet"* - about the process's default
SQLite, which nobody had asked about. The script read that as failure and
rolled a healthy plant back. Afterwards `fsmes pack status` said that plant's
schema had never been migrated while `GET /pack` on the plant itself said it
was at head.

Every test here pins one half of the fix: the database a command reports on
is the one it was pointed at, the first line says which one that is, and a
database nobody could reach is never reported as one that is empty.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fsmes import storage
from fsmes.cli import app
from fsmes.pack import apply as applier

runner = CliRunner()

PACK = '''
[pack]
format = 1
requires = ">=0.1.2"

[plant]
name = "acceptance"
label = "A plant written for this test"
timezone = "UTC"
profile = "laptop"

[storage]
database_url = "sqlite:///{url}"
'''


@pytest.fixture()
def pack_at_head(tmp_path, monkeypatch):
    """A pack naming a SQLite file of its own, brought to head - while this
    process's own default database does not exist at all.

    The shape of the incident: the plant's database is fine and the process's
    is not, so a command that reads the wrong one cannot accidentally be
    right.
    """
    from fsmes.config import get_settings
    from fsmes.db import get_engine, get_sessionmaker
    from fsmes.schema import upgrade_database

    directory = tmp_path / "acceptance"
    directory.mkdir()
    database = tmp_path / "plant.db"
    (directory / "plant.toml").write_text(PACK.format(url=database.as_posix()), encoding="utf-8")

    upgrade_database(f"sqlite:///{database.as_posix()}", echo=lambda _: None)

    # Nothing has said which database this process is for, and there is no
    # file where its default would be. A wrong answer now has to look wrong.
    monkeypatch.delenv("MES_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()
    yield directory, database, tmp_path
    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()


def test_pack_status_reads_the_database_the_pack_names_and_says_which_one(pack_at_head):
    directory, database, _ = pack_at_head
    result = runner.invoke(app, ["pack", "status", str(directory)])
    assert result.exit_code == 0, result.output
    assert database.as_posix() in result.output
    assert "(head)" in result.output
    assert "never been migrated" not in result.output


def test_db_status_with_a_pack_reads_that_pack_s_database_and_not_the_process_default(pack_at_head):
    directory, database, _ = pack_at_head
    result = runner.invoke(app, ["db-status", "--pack", str(directory)])
    assert result.exit_code == 0, result.output
    first = result.output.splitlines()[0]
    assert first.startswith("Looking at") and database.as_posix() in first
    assert "The database is at the current schema." in result.output
    assert "There is no database yet" not in result.output


def test_bare_db_status_names_the_database_and_says_it_is_only_the_default(pack_at_head):
    """The one line that would have caught the incident in the promote log."""
    result = runner.invoke(app, ["db-status"])
    assert result.exit_code == 1, result.output
    first = result.output.splitlines()[0]
    assert first.startswith("Looking at sqlite:///")
    assert "the process default" in first
    assert "--pack" in first and "--plant" in first
    # It is still allowed to say the default database is empty - it is. What
    # it may not do is say that without saying whose database it is.
    assert "There is no database here yet" in result.output


def test_a_database_that_did_not_answer_is_never_reported_as_one_that_was_never_migrated(tmp_path):
    """"Nobody reached it" and "it has never been migrated" are different
    facts about a plant, and merging them is what rolled one back."""
    missing = tmp_path / "no-such-directory" / "plant.db"
    reading = storage.look(f"sqlite:///{missing.as_posix()}", "from this test")
    assert reading.answered is False
    assert reading.at_head is None, "a database nobody reached is not behind, and not at head"
    assert "did not answer" in reading.sentence()
    assert "no database" not in reading.sentence().lower()
    assert reading.payload()["answered"] is False


def test_the_cli_and_the_plant_s_own_endpoint_give_the_same_schema_answer(pack_at_head, monkeypatch):
    """One function, asked twice. `fsmes pack status` reads the pack's
    database from outside; `GET /pack` reads the same database from inside
    the plant. The two used to be able to disagree, and did."""
    from fsmes.config import get_settings

    directory, database, into = pack_at_head
    from_outside = applier.status(directory, into=into)

    monkeypatch.setenv("MES_DATABASE_URL", f"sqlite:///{database.as_posix()}")
    monkeypatch.setenv("MES_PLANT_NAME", "acceptance")
    monkeypatch.setenv("FSMES_DATA_DIR", str(into))
    get_settings.cache_clear()
    from_inside = applier.what_this_plant_runs()["schema"]

    assert from_outside.revision == from_inside["revision"]
    assert from_outside.head == from_inside["head"]
    assert from_outside.at_head is from_inside["at_head"] is True


def test_a_pack_that_keeps_its_password_in_a_file_is_read_with_the_password_and_prints_it_to_nobody(tmp_path):
    """A pack names the file holding the password and never the password
    (decision 0022), so a command that reads the pack's database has to put
    it back - and must not then print it."""
    secret = tmp_path / "pg-password"
    secret.write_text("hunter2\n", encoding="utf-8")
    url = storage.with_password("postgresql+psycopg://fsmes@db.example:5432/plant", secret)
    assert url == "postgresql+psycopg://fsmes:hunter2@db.example:5432/plant"
    assert storage.redacted(url) == "postgresql+psycopg://fsmes:***@db.example:5432/plant"
    assert "hunter2" not in storage.redacted(url)


def test_a_pack_that_does_not_say_which_database_it_uses_is_reported_as_unknown(tmp_path):
    """A pack with no `[storage]` leaves the choice to the fleet. Answering
    about this process's database instead is the original mistake."""
    from fsmes.pack import format as fmt

    directory = tmp_path / "quiet"
    directory.mkdir()
    (directory / "plant.toml").write_text(
        PACK.split("[storage]")[0], encoding="utf-8")
    with pytest.raises(storage.Unknown) as raised:
        fmt.database_url(fmt.read(directory))
    assert "--plant" in str(raised.value)

    state = applier.status(directory, into=tmp_path)
    lines = "\n".join(state.render())
    assert "database  unknown" in lines and "schema    unknown" in lines
    assert state.at_head is None


def test_init_db_says_which_database_it_is_about_before_it_creates_one(tmp_path, monkeypatch):
    """The same first line, for the same reason: a stray SQLite file created
    beside a real database is the mistake this sentence stops."""
    from fsmes.config import get_settings
    from fsmes.db import get_engine, get_sessionmaker

    monkeypatch.delenv("MES_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    for cache in (get_settings, get_engine, get_sessionmaker):
        cache.cache_clear()
    try:
        result = runner.invoke(app, ["init-db"])
        assert result.exit_code == 0, result.output
        first = result.output.splitlines()[0]
        assert first.startswith("Looking at sqlite:///")
        assert "the process default" in first
        assert Path(os.getcwd(), "fsmes.db").exists()
    finally:
        for cache in (get_settings, get_engine, get_sessionmaker):
            cache.cache_clear()
