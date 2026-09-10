"""`fsmes plant <name> migrate`.

A schema change is only merged once every plant's database file carries it.
Until this command existed that step was done by hand, twice, and logged as a
trap both times.
"""

import sqlite3
from pathlib import Path

import pytest

from fsmes import plant as plants_mod


def _make_db(path: Path, revision: str = "aaa") -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        conn.execute("CREATE TABLE work_orders (id INTEGER)")
        conn.execute("INSERT INTO work_orders VALUES (1), (2)")


def _cfg(port: int) -> dict:
    return {"api_host": "127.0.0.1", "api_port": port, "opc_port": port + 1,
            "tag_map": "unused.json", "replay_dir": "unused", "init": "unused.py"}


@pytest.fixture()
def quiet_accounts(monkeypatch):
    monkeypatch.setattr(plants_mod, "ensure_lab_users", lambda *a, **k: None)


def test_migrate_backs_up_upgrades_and_reports(tmp_path, quiet_accounts):
    db = plants_mod.data_dir(tmp_path) / "demo.db"
    _make_db(db)

    def upgrade() -> None:
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE alembic_version SET version_num = 'bbb'")
            conn.execute("CREATE TABLE gauges (id INTEGER)")

    said: list[str] = []
    receipt = plants_mod.migrate("demo", _cfg(1), tmp_path, echo=said.append, upgrade=upgrade)

    assert receipt["migrated"] is True
    assert (receipt["revision_before"], receipt["revision_after"]) == ("aaa", "bbb")
    assert receipt["rows_changed"] == {"gauges": (None, 0)}
    backup = Path(receipt["backup"])
    assert backup.exists() and backup.name.startswith("demo.db.pre-migrate-")
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "aaa"
    assert any("aaa -> bbb" in line for line in said)


def test_migrate_at_head_leaves_no_backup(tmp_path, quiet_accounts):
    db = plants_mod.data_dir(tmp_path) / "demo.db"
    _make_db(db, revision="head0")

    receipt = plants_mod.migrate("demo", _cfg(1), tmp_path, echo=lambda s: None, upgrade=lambda: None)

    assert receipt["migrated"] is False and receipt["backup"] is None
    assert receipt["revision_after"] == "head0"
    assert not list(db.parent.glob("demo.db.pre-migrate-*"))


def test_migrate_refuses_a_running_plant(tmp_path, quiet_accounts, monkeypatch):
    db = plants_mod.data_dir(tmp_path) / "demo.db"
    _make_db(db)
    # A plant that keeps answering for the whole grace period is refused.
    monkeypatch.setattr(plants_mod, "_answers", lambda cfg, timeout=1.0: True)
    monkeypatch.setattr(plants_mod.time, "sleep", lambda s: None)  # the wait, without the waiting
    touched = []
    receipt = plants_mod.migrate("demo", _cfg(1), tmp_path, echo=lambda s: None,
                                 upgrade=lambda: touched.append(1))
    assert receipt == {"plant": "demo", "migrated": False, "reason": "running"}
    assert touched == []
    assert not list(db.parent.glob("demo.db.pre-migrate-*"))


def test_migrate_without_a_database_points_at_init(tmp_path, quiet_accounts):
    said: list[str] = []
    receipt = plants_mod.migrate("demo", _cfg(1), tmp_path, echo=said.append, upgrade=lambda: None)
    assert receipt["reason"] == "no database"
    assert "fsmes plant demo init" in said[0]


def test_migrate_says_a_database_had_no_stamp_rather_than_printing_none(tmp_path, quiet_accounts):
    """A database made by a wheel that shipped no migrations has no
    `alembic_version` row at all. The receipt used to read
    `None -> a3f6c81d09e2`, which looks like a bug rather than the
    recognition it is."""
    db = plants_mod.data_dir(tmp_path) / "demo.db"
    with sqlite3.connect(db) as conn:  # create_all's work: tables, no stamp
        conn.execute("CREATE TABLE work_orders (id INTEGER)")
        conn.execute("INSERT INTO work_orders VALUES (1)")

    def upgrade() -> None:
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
            conn.execute("INSERT INTO alembic_version VALUES ('bbb')")

    said: list[str] = []
    receipt = plants_mod.migrate("demo", _cfg(1), tmp_path, echo=said.append, upgrade=upgrade)

    assert (receipt["revision_before"], receipt["revision_after"]) == (None, "bbb")
    assert any("unstamped (made before the migrations shipped) -> bbb" in line for line in said)
