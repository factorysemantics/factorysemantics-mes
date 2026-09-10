# Upgrading between versions

*How-to. The procedure, the rollback, and the one case this project cannot
migrate for you yet.*

This is `0.x`. Between minors the database schema changes, and the
[CHANGELOG](https://github.com/factorysemantics/factorysemantics-mes/blob/main/CHANGELOG.md)
carries an **Honesty** section for anything that changed what a number
*means* — a KPI formula, a state mapping, a counter rule. Read that section
before upgrading a plant that anyone reads numbers off, because a number that
changes meaning without anyone noticing is worse than one that breaks.

## Before anything

```bash
fsmes backup --out /mnt/nas/fsmes
```

[Backup and restore](backup.md). Do this even though `fsmes plant <name>
migrate` takes its own copy: that copy lands in the same directory, on the
same disk, as the database it is protecting.

## A plant from the registry

If your plants come from a registry ([Plants from a registry](registry.md)),
migration is one command per plant, or `all` for every one of them:

```bash
systemctl --user stop fsmes-plant@bottling      # or: fsmes plant bottling stop
pip install --upgrade factorysemantics-mes      # however you installed it
fsmes plant bottling migrate
systemctl --user start fsmes-plant@bottling
```

`fsmes plant <name> migrate` (`src/fsmes/plant.py`) does four things, and
prints all of them:

1. **Refuses a plant that is still running.** It tries the plant's own API
   port for up to 15 seconds; if anything answers it stops and tells you to
   stop the plant first. Migrating a live SQLite file under a running agent
   is how a database ends up half-upgraded.
2. **Copies the database first**, to
   `<data_dir>/<name>.db.pre-migrate-<timestamp>`, beside the live file.
3. **Migrates**, and counts every table's rows before and after.
4. **Prints a receipt** — the Alembic revision before and after, and every
   table whose row count moved. A table that did not move is not listed, and
   a new table appears as `None -> 0`; when nothing moved at all it says so
   in one line:

```text
  bottling: f2a8d31c6b74 -> a3f6c81d09e2; backup bottling.db.pre-migrate-20260910-094224
      maintenance_plans: None -> 0
      maintenance_orders: None -> 0
```

A database already at the current revision is reported as such and its
backup is **deleted** — there is nothing to roll back to, because nothing
happened.

On PostgreSQL the same command runs the same migration and takes **no file
copy**: a server database is backed up by the server's own tools, and this
command does not pretend to have done it for you.

### `all`, and what to watch

```bash
fsmes plant all migrate
```

Each plant is migrated in turn and prints its own receipt. Read them: the
command's exit code does not currently report a plant that refused because
it was still running, so a plant you forgot to stop is a line in the output,
not a failure you will be told about.

## Rolling back

There is no `fsmes plant rollback`. If a migration fails, the pre-migration
copy is on disk untouched and the live database is wherever Alembic stopped;
nothing puts it back for you. By hand:

```bash
systemctl --user stop fsmes-plant@bottling
cp <data_dir>/bottling.db.pre-migrate-20260910-094224 <data_dir>/bottling.db
pip install factorysemantics-mes==<the version you were on>
systemctl --user start fsmes-plant@bottling
```

The version you were on matters: the restored file carries the old schema,
and the new code expects the new one.

`deploy/promote.sh` in the repository does exactly this automatically for a
pinned-tag deployment — stop, migrate, and on any failure restore each
plant's newest `pre-migrate` copy and check the tree back out at the previous
tag. If your plant is deployed from a checkout at a tag, use that script
rather than these steps by hand.

## A single install, not from the registry

One plant, one database, no registry — the ordinary first install:

```bash
fsmes backup --out /mnt/nas/fsmes
# stop the agent and the API
pip install --upgrade factorysemantics-mes
fsmes init-db
# start them again
```

`fsmes init-db` runs the migrations the package itself carries, so it does
the same thing on a PyPI install as it does in a clone. In 0.1.2 and earlier it did
not: the wheel shipped no migration scripts, `init-db` outside a checkout
created whatever tables were missing, ran no `ALTER` at all, and said
"Database schema is up to date" either way. The workaround was to run from a
checkout at a tag. That is gone — there is nothing to work around.

### If your database was made by 0.1.2 or earlier

A database created by one of those wheels has tables but no Alembic stamp,
so nothing in it says which revision its tables correspond to. `init-db`
works that out rather than assuming: it rebuilds the schema each revision in
the chain produces, compares table names and column names against yours, and
stamps only on an exact match. You will see one of these:

```text
This database was created before the migrations shipped. Its schema matches
revision 153379d6cf19 exactly, so it is stamped there and the migrations
since then now run.
Database schema upgraded: 153379d6cf19 -> a3f6c81d09e2.
```

```text
This database was created before the migrations shipped. Its schema matches
revision a3f6c81d09e2, which is the current one; it is now stamped and
nothing else was changed.
```

If your database matches no revision — someone added a table by hand, or it
came from something other than a release — it says so, names the nearest
revision, lists every difference, and **changes nothing**. Restore your
backup and open a
[Discussion](https://github.com/factorysemantics/factorysemantics-mes/discussions)
with what it printed. A stamp that is not true of the database is worse than
no stamp: every later migration would be skipped or applied twice on the
strength of it.

## Checking the upgrade landed

```bash
fsmes info          # the version you meant to be on
fsmes db-status     # the schema revision, and whether it is the current one
fsmes erp check     # the ERP connector still reaches what it needs
fsmes opc-verify    # every mapped tag still readable, run against the live server
```

`fsmes db-status` prints the revision the database is at and the revision the
installed version expects, and **exits non-zero when they differ** — so a
deployment script can gate on it rather than on someone reading the output:

```text
Current: a3f6c81d09e2
Head:    a3f6c81d09e2
The database is at the current schema.
```

Then open the dashboard and look at a shift from before the upgrade. History
that was there before and is not there now is the thing to catch, and it is
caught by looking, not by a command.

## See also

- [Backup and restore](backup.md)
- [Plants from a registry](registry.md)
- [Connecting read-only to an OPC UA server](opc-readonly.md)
