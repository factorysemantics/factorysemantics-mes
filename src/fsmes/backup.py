"""Backup and restore — everything a plant owns that it cannot recreate.

Four things are lost if the plant PC is lost, and only one of them is the
database:

* the database, which is the whole production record;
* the tag map, which is what every number on every screen means;
* the line layout, if somebody drew one;
* the OPC UA client certificate, which the server's owner trusted by hand.

The certificate is the one people forget. Regenerating it means going back
to whoever administers the OPC UA server and asking them to trust a new one,
which is a conversation and a change window, not a command.

What this module deliberately does **not** copy is `.env`: it holds the OPC
password, the ERP credentials and the token signing key. A backup folder is
copied to a laptop and mailed to a colleague; secrets belong wherever the
plant already keeps secrets. The manifest names every setting whose value
was not copied, so a restore knows what it still has to be told.

On SQLite the copy uses SQLite's own online backup, so a running agent does
not have to be stopped and a write mid-copy cannot produce a torn file. On a
server database there is nothing here to copy: PostgreSQL has `pg_dump`, and
pretending otherwise would hand somebody a backup folder that does not
contain their production record.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from fsmes import __version__
from fsmes.config import Settings
from fsmes.db import utcnow

FORMAT = 1
MANIFEST = "manifest.json"

#: Settings that point at a file this backup carries, and the environment
#: variable that names each. `restore` writes each file back to whatever the
#: setting says *on the machine being restored to* — config, not code, at the
#: plant boundary: a restore onto a differently laid out machine is a
#: different `.env`, not a different backup.
CONFIG_FILES = (
    ("tag_map_file", "MES_TAG_MAP_FILE", "the tag map: what every subscribed tag means"),
    ("line_layout_file", "MES_LINE_LAYOUT_FILE", "the 3D line view's geometry, if anyone drew one"),
)

NOT_COPIED = (
    (".env", "MES_*", "the OPC password, the ERP credentials and MES_SECRET_KEY. "
                      "Keep it where this plant already keeps secrets."),
    ("logs/", "MES_LOG_DIR", "evidence of what the software did, not of what the plant did. "
                             "Rotated, and large. Copy them if an incident is open."),
    ("erp_exchange/", "MES_ERP_INBOX, MES_ERP_OUTBOX, MES_ERP_ARCHIVE",
     "the file exchange with the ERP. The archive is history the ERP also has; "
     "an outbox with confirmations still in it is worth copying by hand."),
)


class BackupError(Exception):
    """Something about the backup or the machine makes the operation unsafe."""


def sqlite_path(url: str) -> Path | None:
    """The file behind a SQLite URL, or None for a server database."""
    if not url.startswith("sqlite:///"):
        return None
    return Path(url[len("sqlite:///"):])


def redacted(url: str) -> str:
    """A database URL with the password taken out, safe to write into a file."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}" if ":" in creds else url


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def config_target(settings: Settings, field: str) -> Path:
    """Where a restored config file goes.

    `get_settings()` swaps an unset path for the copy that ships inside the
    wheel, so that a fresh install runs with no setup at all. Restoring on
    top of that would put a plant's tag map inside `site-packages`, where the
    next upgrade quietly replaces it. So an unset path restores to its
    declared default under the working directory instead, which is where the
    MES looks first.
    """
    if field in settings.model_fields_set:
        return Path(getattr(settings, field))
    return Path(Settings.model_fields[field].default)


def _revision(db: Path) -> str | None:
    """The Alembic revision in a SQLite file, or None if it has no version table."""
    try:
        with sqlite3.connect(db) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.DatabaseError:
        return None


def _row_counts(db: Path) -> dict[str, int]:
    """Every table in a SQLite file and how many rows it holds."""
    with sqlite3.connect(db) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: conn.execute(f"SELECT count(*) FROM '{t}'").fetchone()[0] for t in sorted(tables)}


def _copy_sqlite(source: Path, target: Path) -> None:
    """SQLite's own online backup: safe while the agent and the API are running.

    A plain file copy of a database in WAL mode copies the `.db` without the
    `-wal` beside it, which loses every committed transaction that has not
    been checkpointed yet. This copies through SQLite, which resolves the WAL
    as it goes and yields one self-contained file.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _copy_tree(source: Path, target: Path) -> list[Path]:
    """Copy a directory, returning the files copied, deepest path first."""
    copied: list[Path] = []
    for item in sorted(source.rglob("*")):
        if not item.is_file():
            continue
        destination = target / item.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        copied.append(destination)
    return copied


def back_up(settings: Settings, out: Path, *, now: datetime | None = None) -> dict:
    """Write one backup folder and return its manifest.

    The folder is `out/<timestamp>`; it is created, and refused if it is
    already there, because two backups sharing a name is how somebody
    restores the wrong one.
    """
    stamp = (now or utcnow()).strftime("%Y%m%d-%H%M%S")
    folder = out / stamp
    if folder.exists():
        raise BackupError(f"{folder} already exists; a backup never writes over one that is there")
    folder.mkdir(parents=True)

    files: list[dict] = []

    def record(source: Path, inside: Path, field: str, relative: str, what: str, setting: str) -> None:
        files.append({
            "file": inside.as_posix(),
            # The settings field this file belongs to, and where inside it.
            # `restore` resolves the target from these against the settings of
            # the machine being restored to, so a restore onto a machine laid
            # out differently is a different `.env`, not a different backup.
            "field": field,
            "relative": relative,
            "restore_to_here": str(source),
            "sha256": sha256(folder / inside),
            "bytes": (folder / inside).stat().st_size,
            "setting": setting,
            "what": what,
        })

    # --- the database --------------------------------------------------
    url = settings.database_url
    live_db = sqlite_path(url)
    if live_db is None:
        database = {
            "kind": "server",
            "copied": False,
            "url": redacted(url),
            "why_not": "a server database is backed up by the server's own tools, not by copying a file",
        }
    elif not live_db.exists():
        database = {"kind": "sqlite", "copied": False, "url": url,
                    "why_not": f"{live_db} does not exist yet"}
    else:
        inside = Path("database") / live_db.name
        _copy_sqlite(live_db, folder / inside)
        database = {
            "kind": "sqlite",
            "copied": True,
            "url": url,
            "file": inside.as_posix(),
            "restore_to": live_db.as_posix(),
            "sha256": sha256(folder / inside),
            "bytes": (folder / inside).stat().st_size,
            "revision": _revision(folder / inside),
            "rows": _row_counts(folder / inside),
        }

    # --- the config files ----------------------------------------------
    for field, env, what in CONFIG_FILES:
        source = Path(getattr(settings, field))
        if not source.is_file():
            continue
        inside = Path("config") / source.name
        (folder / inside).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, folder / inside)
        record(source, inside, field, "", what, env)

    # --- the OPC UA client certificate ---------------------------------
    certs = Path(settings.opc_cert_dir)
    if certs.is_dir():
        for copied in _copy_tree(certs, folder / "certs"):
            inside = copied.relative_to(folder)
            relative = inside.relative_to("certs")
            record(certs / relative, inside, "opc_cert_dir", relative.as_posix(),
                   "the OPC UA client certificate the server's owner trusted",
                   "MES_OPC_CERT_DIR")

    manifest = {
        "format": FORMAT,
        "fsmes_version": __version__,
        "taken": (now or utcnow()).isoformat(timespec="seconds"),
        "database": database,
        "files": files,
        "totals": {
            "files": len(files) + (1 if database.get("copied") else 0),
            "bytes": sum(f["bytes"] for f in files) + int(database.get("bytes", 0)),
        },
        "not_copied": [{"what": what, "setting": setting, "why": why} for what, setting, why in NOT_COPIED],
    }
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["folder"] = str(folder)
    return manifest


def read_manifest(folder: Path) -> dict:
    path = folder / MANIFEST
    if not path.is_file():
        raise BackupError(f"{folder} is not a backup: no {MANIFEST} in it")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise BackupError(
            f"{folder} was written by backup format {manifest.get('format')}; this fsmes reads {FORMAT}")
    return manifest


def verify(folder: Path, manifest: dict) -> list[str]:
    """Every file the manifest lists, checked against its recorded hash.

    Returns one plain sentence per problem, so a caller can print them all
    rather than stopping at the first.
    """
    problems: list[str] = []
    entries = list(manifest["files"])
    database = manifest["database"]
    if database.get("copied"):
        entries.append(database)
    for entry in entries:
        path = folder / entry["file"]
        if not path.is_file():
            problems.append(f"{entry['file']} is named in the manifest and is not in the folder")
            continue
        if sha256(path) != entry["sha256"]:
            problems.append(f"{entry['file']} does not match the hash the manifest recorded")
    return problems



def _target_for(settings: Settings, entry: dict) -> Path:
    """Where one backed-up file goes on the machine being restored to.

    The manifest records which setting a file belongs to, not an absolute
    path, so a plant restored onto a new PC lands where that PC's settings
    say — config, not code, at the plant boundary.
    """
    field = entry["field"]
    if field == "opc_cert_dir":
        return Path(settings.opc_cert_dir) / entry["relative"]
    return config_target(settings, field)


def restore(settings: Settings, folder: Path, *, force: bool = False, dry_run: bool = False) -> dict:
    """Put a backup back, onto the paths this machine's settings name.

    Refuses to write over a database that is already there unless `force` is
    given: restoring on top of a live plant is how a shift disappears.
    """
    manifest = read_manifest(folder)
    problems = verify(folder, manifest)
    if problems:
        raise BackupError("this backup does not verify:\n  " + "\n  ".join(problems))

    planned: list[tuple[Path, Path]] = []
    database = manifest["database"]
    db_target: Path | None = None
    if database.get("copied"):
        live = sqlite_path(settings.database_url)
        if live is None:
            raise BackupError(
                "this backup holds a SQLite database and MES_DATABASE_URL names a server database; "
                "point MES_DATABASE_URL at a file, or load the dump with the server's own tools")
        if live.exists() and not force:
            raise BackupError(
                f"{live} already exists. Move it aside, or pass --force to write over it.")
        db_target = live
        planned.append((folder / database["file"], live))

    for entry in manifest["files"]:
        planned.append((folder / entry["file"], _target_for(settings, entry)))

    if not dry_run:
        for source, target in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    receipt = {
        "folder": str(folder),
        "dry_run": dry_run,
        "restored": [{"file": str(s.relative_to(folder)), "to": str(t)} for s, t in planned],
        "totals": {"files": len(planned)},
        "database": None,
        "not_copied": manifest["not_copied"],
    }
    if db_target is not None:
        receipt["database"] = {
            "to": str(db_target),
            "revision": database.get("revision"),
            "rows": database.get("rows", {}) if dry_run else _row_counts(db_target),
        }
    return receipt
