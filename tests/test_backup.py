"""`fsmes backup` and `fsmes restore`.

What is guarded here is that a backup folder contains what a person will
believe it contains. A backup that silently leaves out the database, or that
copies a WAL database without its uncheckpointed transactions, is worse than
no backup, because the plant finds out on the day it restores.
"""

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from fsmes.backup import MANIFEST, BackupError, back_up, config_target, redacted, restore
from fsmes.config import Settings


def _plant(tmp_path, monkeypatch, *, url: str | None = None) -> Settings:
    """A plant on disk: a database with rows in it, a tag map, a certificate."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "fsmes.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('abc123')")
        conn.execute("CREATE TABLE work_orders (id INTEGER)")
        conn.executemany("INSERT INTO work_orders VALUES (?)", [(1,), (2,), (3,)])
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "tag_map.json").write_text('{"machines": []}', encoding="utf-8")
    (tmp_path / "certs").mkdir()
    (tmp_path / "certs" / "mes_twin_client.der").write_bytes(b"a certificate somebody trusted")
    (tmp_path / ".env").write_text("MES_OPC_PASSWORD=hunter2\n", encoding="utf-8")
    return Settings(
        database_url=url or f"sqlite:///{db.as_posix()}",
        tag_map_file=tmp_path / "config" / "tag_map.json",
        line_layout_file=tmp_path / "config" / "line_layout.json",  # deliberately absent
        opc_cert_dir=tmp_path / "certs",
    )


# ------------------------------------------------------------------ backing up


def test_a_backup_carries_the_database_the_tag_map_and_the_certificate(tmp_path, monkeypatch):
    """Three things are lost with the plant PC and only one is the database.
    The certificate matters most in practice: minting a new one means asking
    the OPC server's owner to trust it again, which is a change window."""
    settings = _plant(tmp_path, monkeypatch)

    manifest = back_up(settings, tmp_path / "backups")

    folder = tmp_path / "backups" / sorted(p.name for p in (tmp_path / "backups").iterdir())[0]
    assert (folder / manifest["database"]["file"]).is_file()
    carried = {entry["file"] for entry in manifest["files"]}
    assert carried == {"config/tag_map.json", "certs/mes_twin_client.der"}
    assert manifest["totals"]["files"] == 3  # the database and the two files


def test_the_backup_folder_never_holds_the_env_file(tmp_path, monkeypatch):
    """A backup folder is copied to a laptop and mailed to a colleague. The
    OPC password, the ERP credentials and the token signing key must not
    travel with it — and the manifest has to say so, or the person restoring
    will not know they are missing."""
    settings = _plant(tmp_path, monkeypatch)

    manifest = back_up(settings, tmp_path / "backups")
    folder = tmp_path / "backups" / sorted(p.name for p in (tmp_path / "backups").iterdir())[0]

    assert not list(folder.rglob(".env"))
    assert "hunter2" not in (folder / MANIFEST).read_text(encoding="utf-8")
    assert ".env" in {entry["what"] for entry in manifest["not_copied"]}


def test_a_backup_states_its_own_totals(tmp_path, monkeypatch):
    """House rule: every list states its total. The manifest's totals are the
    only thing a person checks when comparing two backup folders."""
    settings = _plant(tmp_path, monkeypatch)

    manifest = back_up(settings, tmp_path / "backups")
    folder = tmp_path / "backups" / sorted(p.name for p in (tmp_path / "backups").iterdir())[0]

    on_disk = [p for p in folder.rglob("*") if p.is_file() and p.name != MANIFEST]
    assert manifest["totals"]["files"] == len(on_disk)
    assert manifest["totals"]["bytes"] == sum(p.stat().st_size for p in on_disk)


def test_a_missing_file_is_left_out_rather_than_recorded_as_empty(tmp_path, monkeypatch):
    """Nobody drew a line layout on this plant. Unknown is not zero: the
    manifest does not list a file that was never there."""
    settings = _plant(tmp_path, monkeypatch)

    manifest = back_up(settings, tmp_path / "backups")

    assert not any("line_layout" in entry["file"] for entry in manifest["files"])


def test_the_copy_carries_writes_that_are_still_only_in_the_wal(tmp_path, monkeypatch):
    """SQLite runs in WAL mode here, and a plain file copy of the `.db` leaves
    behind every transaction not yet checkpointed — an hour of production, on
    a busy line. The copy goes through SQLite itself so it cannot."""
    settings = _plant(tmp_path, monkeypatch)
    live = tmp_path / "fsmes.db"
    keep_open = sqlite3.connect(live)
    keep_open.execute("PRAGMA journal_mode=WAL")
    keep_open.execute("INSERT INTO work_orders VALUES (4)")
    keep_open.commit()  # committed, and still in the -wal file

    manifest = back_up(settings, tmp_path / "backups")
    keep_open.close()

    folder = tmp_path / "backups" / sorted(p.name for p in (tmp_path / "backups").iterdir())[0]
    with sqlite3.connect(folder / manifest["database"]["file"]) as copy:
        assert copy.execute("SELECT count(*) FROM work_orders").fetchone()[0] == 4
    assert manifest["database"]["rows"]["work_orders"] == 4
    assert manifest["database"]["revision"] == "abc123"


def test_the_folder_name_and_the_manifest_name_the_same_instant(tmp_path, monkeypatch):
    """The backup reads the clock once. The folder stamp and the manifest's
    `taken` are the same instant written two ways, so a restore that trusts
    `taken` names the time the folder carries. The clock here moves a second
    on every read: only a backup that reads it once can agree with itself."""
    settings = _plant(tmp_path, monkeypatch)
    ticks = iter(datetime(2026, 9, 10, 11, 32, 19) + timedelta(seconds=n) for n in range(100))
    monkeypatch.setattr("fsmes.backup.utcnow", lambda: next(ticks))

    manifest = back_up(settings, tmp_path / "backups")

    folder = tmp_path / "backups" / sorted(p.name for p in (tmp_path / "backups").iterdir())[0]
    assert datetime.fromisoformat(manifest["taken"]).strftime("%Y%m%d-%H%M%S") == folder.name


def test_a_backup_never_writes_over_one_that_is_already_there(tmp_path, monkeypatch):
    """Two backups sharing a folder name is how somebody restores the wrong
    one, and the loser is the older, better copy."""
    settings = _plant(tmp_path, monkeypatch)
    manifest = back_up(settings, tmp_path / "backups")

    with pytest.raises(BackupError, match="already exists"):
        back_up(settings, tmp_path / "backups", now=datetime.fromisoformat(manifest["taken"]))


# --------------------------------------------------------- a server database


def test_a_server_database_is_declared_missing_rather_than_quietly_left_out(tmp_path, monkeypatch):
    """PostgreSQL is backed up with the server's own tools. What must never
    happen is a folder that looks like a backup and has no production record
    in it, with nothing saying so."""
    settings = _plant(tmp_path, monkeypatch, url="postgresql+psycopg://mes:pa55word-in-the-url@db:5432/mes")

    manifest = back_up(settings, tmp_path / "backups")

    assert manifest["database"]["copied"] is False
    assert "server" in manifest["database"]["why_not"] or "tools" in manifest["database"]["why_not"]
    assert "pa55word-in-the-url" not in json.dumps(manifest)  # the password never reaches the manifest
    assert manifest["totals"]["files"] == 2  # the two files, and it does not count a database


def test_a_password_is_taken_out_of_a_url_before_it_is_written_down():
    """A manifest is a file people mail around."""
    assert redacted("postgresql+psycopg://mes:pa55word@db:5432/mes") == "postgresql+psycopg://mes:***@db:5432/mes"
    assert redacted("sqlite:///fsmes.db") == "sqlite:///fsmes.db"


# ------------------------------------------------------------------ restoring


def test_a_restore_puts_every_file_back_and_the_rows_with_it(tmp_path, monkeypatch):
    """The row counts printed after a restore are read from the restored file,
    not repeated from the manifest, because the manifest is what is being
    checked."""
    settings = _plant(tmp_path, monkeypatch)
    back_up(settings, tmp_path / "backups")
    folder = tmp_path / "backups" / sorted(p.name for p in (tmp_path / "backups").iterdir())[0]

    fresh = tmp_path / "elsewhere"
    fresh.mkdir()
    monkeypatch.chdir(fresh)
    onto = Settings(
        database_url=f"sqlite:///{(fresh / 'fsmes.db').as_posix()}",
        tag_map_file=fresh / "config" / "tag_map.json",
        line_layout_file=fresh / "config" / "line_layout.json",
        opc_cert_dir=fresh / "certs",
    )

    receipt = restore(onto, folder)

    assert receipt["totals"]["files"] == 3
    assert receipt["database"]["rows"]["work_orders"] == 3
    assert receipt["database"]["revision"] == "abc123"
    assert (fresh / "config" / "tag_map.json").is_file()
    assert (fresh / "certs" / "mes_twin_client.der").read_bytes() == b"a certificate somebody trusted"


def test_a_restore_refuses_a_backup_whose_files_have_changed(tmp_path, monkeypatch):
    """A half-copied backup folder is the ordinary failure — an interrupted
    copy over the network, a full disk. It must be caught before anything is
    written, not halfway through."""
    settings = _plant(tmp_path, monkeypatch)
    back_up(settings, tmp_path / "backups")
    folder = tmp_path / "backups" / sorted(p.name for p in (tmp_path / "backups").iterdir())[0]
    (folder / "config" / "tag_map.json").write_text("truncated", encoding="utf-8")

    fresh = tmp_path / "elsewhere"
    fresh.mkdir()
    onto = Settings(database_url=f"sqlite:///{(fresh / 'fsmes.db').as_posix()}")

    with pytest.raises(BackupError, match="does not verify"):
        restore(onto, folder)
    assert not (fresh / "fsmes.db").exists()  # nothing was written


def test_a_restore_will_not_write_over_a_database_that_is_there(tmp_path, monkeypatch):
    """Restoring on top of a running plant is how a shift disappears."""
    settings = _plant(tmp_path, monkeypatch)
    back_up(settings, tmp_path / "backups")
    folder = tmp_path / "backups" / sorted(p.name for p in (tmp_path / "backups").iterdir())[0]

    with pytest.raises(BackupError, match="--force"):
        restore(settings, folder)

    receipt = restore(settings, folder, force=True)
    assert receipt["database"]["rows"]["work_orders"] == 3


def test_a_dry_run_verifies_the_backup_and_writes_nothing(tmp_path, monkeypatch):
    """The only way to prove a backup is restorable without a second machine
    is to check it in place and say what it would do."""
    settings = _plant(tmp_path, monkeypatch)
    back_up(settings, tmp_path / "backups")
    folder = tmp_path / "backups" / sorted(p.name for p in (tmp_path / "backups").iterdir())[0]

    fresh = tmp_path / "elsewhere"
    fresh.mkdir()
    onto = Settings(
        database_url=f"sqlite:///{(fresh / 'fsmes.db').as_posix()}",
        tag_map_file=fresh / "config" / "tag_map.json",
        opc_cert_dir=fresh / "certs",
    )

    receipt = restore(onto, folder, dry_run=True)

    assert receipt["dry_run"] is True
    assert receipt["totals"]["files"] == 3
    assert not (fresh / "fsmes.db").exists()
    assert not (fresh / "config").exists()


def test_a_sqlite_backup_is_not_restored_onto_a_server_database(tmp_path, monkeypatch):
    """Copying a SQLite file where MES_DATABASE_URL names PostgreSQL would
    leave the plant reading an empty server and a file nothing opens."""
    settings = _plant(tmp_path, monkeypatch)
    back_up(settings, tmp_path / "backups")
    folder = tmp_path / "backups" / sorted(p.name for p in (tmp_path / "backups").iterdir())[0]

    onto = Settings(database_url="postgresql+psycopg://mes:pa55word-in-the-url@db:5432/mes")
    with pytest.raises(BackupError, match="server database"):
        restore(onto, folder)


# --------------------------------------------------------- where files land


def test_an_unset_tag_map_restores_beside_the_plant_not_inside_the_wheel(tmp_path, monkeypatch):
    """A path nobody set resolves to the copy shipped inside the package, so
    that a fresh install runs with no setup. Restoring there would put a
    plant's tag map in site-packages, where the next upgrade replaces it."""
    monkeypatch.chdir(tmp_path)
    unset = Settings()
    assert config_target(unset, "tag_map_file").as_posix() == "config/tag_map.json"

    chosen = Settings(tag_map_file=tmp_path / "elsewhere" / "mine.json")
    assert config_target(chosen, "tag_map_file") == tmp_path / "elsewhere" / "mine.json"
