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

!!! warning "`fsmes init-db` only migrates inside a checkout"
    `fsmes init-db` runs the Alembic migrations when there is an
    `alembic.ini` in the working directory — that is, when you are running
    from a clone of the repository. **Installed from PyPI there is no
    `alembic.ini` and no migration scripts in the wheel**, so `init-db`
    creates whatever tables are missing and stops there. It does not alter a
    table that already exists, and it says "Database schema is up to date"
    either way.

    For a **new** install that is exactly right: the schema it creates is the
    current one. For an **upgrade** of a PyPI install across a version where
    an existing table changed, it is not enough, and nothing warns you.

    Until the wheel carries its migrations, a plant that must upgrade in
    place should run from a checkout at a tag:

    ```bash
    git clone https://github.com/factorysemantics/factorysemantics-mes
    cd factorysemantics-mes && git checkout v0.1.2
    python -m venv .venv && .venv/bin/pip install -e .
    .venv/bin/fsmes init-db          # alembic.ini is here, so this migrates
    ```

    This is a known gap, written down rather than papered over. If it is in
    your way, say so in
    [Discussions](https://github.com/factorysemantics/factorysemantics-mes/discussions)
    — it moves up the list.

## Checking the upgrade landed

```bash
fsmes info          # the version you meant to be on
fsmes erp check     # the ERP connector still reaches what it needs
fsmes opc-verify    # every mapped tag still readable, run against the live server
```

Then open the dashboard and look at a shift from before the upgrade. History
that was there before and is not there now is the thing to catch, and it is
caught by looking, not by a command.

## See also

- [Backup and restore](backup.md)
- [Plants from a registry](registry.md)
- [Connecting read-only to an OPC UA server](opc-readonly.md)
