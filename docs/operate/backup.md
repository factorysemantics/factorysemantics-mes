# Backup and restore

*How-to. What to copy, how to copy it while the plant is running, and how to
prove the copy is any good.*

## The four things

Losing the plant PC loses four things, and only one of them is the database.

| What | Where it is by default | What losing it costs |
|---|---|---|
| The database | `fsmes.db`, or your `MES_DATABASE_URL` | every booking, state change, hold and audit record the MES ever made |
| The tag map | `config/tag_map.json`, or `MES_TAG_MAP_FILE` | what every number on every screen means. Rebuildable from the worksheet, if the worksheet still exists |
| The line layout | `config/line_layout.json`, or `MES_LINE_LAYOUT_FILE` | whatever somebody drew for the 3D view. Nothing else |
| The OPC UA client certificate | `certs/`, or `MES_OPC_CERT_DIR` | **a conversation and a change window.** A new certificate is rejected by the OPC server until whoever administers it trusts it again |

The certificate is the one people forget. `fsmes backup` copies all four.

## Taking a backup

```bash
fsmes backup                 # writes backups/<timestamp>/
fsmes backup --out /mnt/nas/fsmes
```

It prints what it copied, the totals, and — the part worth reading — what it
did **not** copy:

```text
Backup written to backups/20260910-094224
  database   database/fsmes.db  revision a3f6c81d09e2  (36 tables, 41822 rows)
  file       config/tag_map.json  (1204 bytes)
  file       certs/mes_twin_client.der  (912 bytes)
  3 files, 442426 bytes in total.
Not copied, and why:
  .env            the OPC password, the ERP credentials and MES_SECRET_KEY. Keep it where this plant already keeps secrets.
  logs/           evidence of what the software did, not of what the plant did. Rotated, and large. Copy them if an incident is open.
  erp_exchange/   the file exchange with the ERP. The archive is history the ERP also has; an outbox with confirmations still in it is worth copying by hand.
```

Safe while the plant is running. On SQLite the copy goes through SQLite's own
online backup rather than copying the file, which matters more than it
sounds: this MES runs SQLite in WAL mode, and a plain `cp` of the `.db`
without the `-wal` beside it silently loses every transaction that has not
been checkpointed yet — which on a busy line is the last stretch of
production.

The folder is self-describing:

```text
backups/20260910-094224/
  manifest.json              what is here, its hashes, and what was left out
  database/fsmes.db          a consistent copy, not a file copy
  config/tag_map.json
  certs/mes_twin_client.der
```

`manifest.json` records the Alembic revision and the row count of every
table, so two backups can be compared without opening either database.

!!! warning "`.env` is not in the backup, on purpose"
    It holds `MES_OPC_PASSWORD`, the ERP credentials and `MES_SECRET_KEY`. A
    backup folder gets copied to a laptop and mailed to a colleague; those
    belong wherever this plant already keeps secrets. Whoever restores has to
    be given the `.env` separately, and the manifest says so in the folder
    itself.

### On PostgreSQL

`fsmes backup` copies **no database at all** and says so in those words:

```text
  database   NOT IN THIS BACKUP — a server database is backed up by the
             server's own tools, not by copying a file
  ...
This backup does not contain your production record. Back the database up separately.
```

It still copies the tag map, the layout and the certificate, which is worth
having. The database is `pg_dump`:

```bash
pg_dump --format=custom --file=fsmes-$(date +%Y%m%d-%H%M%S).dump \
        "postgresql://mes:...@your-server:5432/mes"
```

and back with `pg_restore --clean --if-exists -d ...`. Nothing here wraps
those: your DBA has a backup regime already, and a second one that only this
MES knows about is a second one nobody tests.

## Restoring

```bash
fsmes restore backups/20260910-094224
```

Before it writes anything it checks every file against the hash the manifest
recorded — a half-copied backup folder from an interrupted network copy is
the ordinary failure, and it has to be caught before the first byte lands,
not halfway through. Then:

- it **refuses** if the database it would write is already there. Pass
  `--force` only when you mean it; restoring on top of a running plant is how
  a shift disappears. Stop the plant first.
- files go where **this machine's** settings say, not where the machine that
  took the backup kept them. Restoring onto a new PC is a different `.env`,
  not a different backup.
- afterwards it reads the row counts back out of the restored database and
  prints them, rather than repeating the manifest — the manifest is the thing
  being checked.

Then bring the schema to the version of `fsmes` you are running now:

```bash
fsmes init-db
```

`init-db` runs the migrations the package carries, wherever `fsmes` was
installed from, so restoring an older backup into a newer `fsmes` brings the
restored database up to the current schema. A database from 0.1.2 or earlier
carries no Alembic stamp; `init-db` recognises it, says what it recognised it
as, and refuses to guess if it cannot — see
[Upgrading](upgrade.md#if-your-database-was-made-by-012-or-earlier).

Put the `.env` back from wherever you keep secrets. Until you do, the
MES has no OPC password and will not connect.

## Proving a restore works

A backup nobody has restored is a hypothesis. Two levels, both cheap:

**Every backup: verify it in place.** `--dry-run` checks every hash and says
exactly what it would write, and touches nothing:

```bash
fsmes restore backups/20260910-094224 --dry-run
```

**Once a quarter, and before any upgrade: restore it somewhere else.** On any
machine with `fsmes` installed, into an empty directory:

```bash
mkdir /tmp/restore-test && cd /tmp/restore-test
MES_DATABASE_URL=sqlite:///restored.db fsmes restore /mnt/nas/fsmes/20260910-094224
MES_DATABASE_URL=sqlite:///restored.db fsmes init-db
MES_DATABASE_URL=sqlite:///restored.db MES_API_PORT=8099 fsmes run-api
```

Open `http://127.0.0.1:8099/dashboard` and look at yesterday's shift. If the
orders and the counts are there, the backup is real. That is the whole test,
and it is the only one that counts — the row counts `fsmes restore` prints
tell you the tables are populated, not that the plant's history is readable.

## How often

There is no schedule this project can set for you, because the answer depends
on what the record is worth. Honestly:

- **In shadow mode, in the first weeks**, the MES is not the plant's system
  of record — the system already in charge is. Losing a week costs you the
  comparison, not the production. Daily is plenty.
- **Once anybody reads a number off it to make a decision**, treat it like
  the record it has become: daily at least, kept off the plant PC, and
  restored somewhere else once a quarter.
- **Always before an upgrade.** See [Upgrading](upgrade.md); `fsmes plant
  <name> migrate` takes its own pre-migration copy, but that copy sits in the
  same directory on the same disk as the database it is protecting, which is
  no help at all if the disk is the thing that failed.

A nightly copy to somewhere else, on Linux, is one systemd timer:

```ini
# ~/.config/systemd/user/fsmes-backup.service
[Service]
Type=oneshot
WorkingDirectory=%h/fsmes
ExecStart=%h/fsmes/.venv/bin/fsmes backup --out /mnt/nas/fsmes
```

```ini
# ~/.config/systemd/user/fsmes-backup.timer
[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

`systemctl --user enable --now fsmes-backup.timer`. On a Windows plant PC the
same command under Task Scheduler does the same job.

Nothing prunes old backup folders. `fsmes backup` refuses to write over one
that is already there, so they accumulate until somebody deletes them; delete
by date, and keep at least one that is older than the last upgrade.

## See also

- [Upgrading](upgrade.md) — the other time a backup matters
- [Plants from a registry](registry.md) — where a multi-plant install keeps
  each plant's database and certificates
- [Running beside your existing MES](first-plant.md)
