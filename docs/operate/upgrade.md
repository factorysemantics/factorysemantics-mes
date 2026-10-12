# Upgrading between versions

*How-to. The procedure, the rollback, and the one case this project cannot
migrate for you yet.*

This is `0.x`. Between minors the database schema changes, and the
[CHANGELOG](https://github.com/factorysemantics/factorysemantics-mes/blob/main/CHANGELOG.md)
carries an **Honesty** section for anything that changed what a number
*means* — a KPI formula, a state mapping, a counter rule. Read that section
before upgrading a plant that anyone reads numbers off, because a number that
changes meaning without anyone noticing is worse than one that breaks.

## 0.2.0 to 0.3.0

Fifteen migrations, all of them additive in shape — twelve new tables and a
set of nullable columns — and two of them write to rows that already exist.
In order:

```bash
fsmes backup --out /mnt/nas/fsmes                 # first, always
# stop the agent and the API
pip install --upgrade factorysemantics-mes
fsmes db-status --pack packs/yourplant            # what you are at, and what 0.3.0 expects
fsmes init-db   --pack packs/yourplant            # runs the migrations the wheel carries
fsmes db-status --pack packs/yourplant            # should now agree; it exits non-zero if not
# start them again
```

**Pass the pack** (or `--plant <name>`). Everything the
[0.1.2 to 0.2.0](#012-to-020) section says about that first line — read it,
it is about *this process's* database until you name a plant's — is true here
unchanged, and so is `fsmes plant <name> migrate` for a plant that comes from
a fleet file.

`c8b1e40d7a92` is the 0.2.0 schema and `d4e8a1c93b70` is head.

| Revision | What it adds |
|---|---|
| `b1f4c73a9e08` | eight nullable columns on `non_conformances`: who reviewed it, what was decided, who closed it |
| `a9c4e17b3d60` | `spc_signals` — one row per Western Electric rule firing with the window it judged — and `non_conformances.evidence` |
| `c6a4b81f39d7` | `shift_code` and `shift_day` on `production_logs`, `equipment_states`, `quality_checks` and `non_conformances`. **This one writes to existing rows** — see below |
| `c3d7e91a4b28` | `equipment_connections`: whether the MES can see a machine, as a second interval history beside what the machine is doing |
| `b1f4c7a2e903` | two indexes — `production_logs (equipment_id, ts)` and `tag_values (equipment_id, id)` — that the floor screen's own questions ask for |
| `c1e7a04b93d5` | `downtime_reasons`, the plant's own downtime vocabulary, and `equipment_states.reason_code` beside the free text, never in place of it |
| `d2b7e51c9a04` | `nc_severities`, the plant's non-conformance severity vocabulary. The existing `severity` column keeps every value already in it |
| `e3c8a17d5b46` | `gauges.warn_days` and `materials.counted_in_pieces`. **This one writes to existing rows** — see below |
| `a1c5e70b2d94` | `plant_settings`, empty on every existing plant, so a plant goes on running on the values it was running on |
| `b3f9c07e51ad` | `ai_turns`: one row per turn of every conversation the assistant and the design chat have had on this plant |
| `c7a2e94b16d3` | five nullable columns the plant already had and threw away — `ai_turns.screen`, and `shift_code`/`shift_day` on `ai_turns` and `audit_log` |
| `b9f31c7a4e50` | `quality_specs.sample_size` and `quality_samples`: a characteristic inspected n pieces at a time, which is an X̄/R chart instead of an I-MR one |
| `d7b4e92a1c58` | `skills`, `personnel_skills`, `roster`, `dispatch_rules`, and nine nullable columns across the two maintenance tables: which trade a job needs, how much it matters, who has it |
| `a1c47f8b2d93` | `needs_stop` and `window` on both maintenance tables: whether the machine has to be stopped, and when the job may be done |
| `d4e8a1c93b70` | `quality_specs.counted` and `quality_counts`: a characteristic an inspector counts rather than measures, drawn as a p chart |

That is fifteen, in that order. **The two that write:**

- `c6a4b81f39d7` **backfills `shift_code` and `shift_day`** on the four floor
  tables from the shift patterns **as they stand on the day you upgrade**,
  read on the plant's clock (`MES_PLANT_TIMEZONE`). There is no history of
  shift patterns in this product, so a plant that has changed its roster
  since those rows were written will have some of them attributed to the
  pattern it runs now rather than the one it ran then. An instant no pattern
  covers is left null, which is the honest answer and not a failure. A plant
  with no shift patterns at all gets nulls everywhere and nothing changes.
- `e3c8a17d5b46` **sets `gauges.warn_days` to 30** on every gauge that
  already exists. Thirty is exactly what the gauges screen had been inventing
  for itself in JavaScript, so nothing anybody looks at changes; from here it
  is the gauge's own number to set.

### What was actually tried, and on what

On 2026-10-12, before this release was tagged: `pip install
factorysemantics-mes==0.2.0` into a scratch virtual environment, `fsmes
init-db`, `fsmes seed`, then `fsmes demo --duration 180` — a real 0.2.0
database with an order pushed through the full loop in it, 38 tables and 152
rows, stamped `c8b1e40d7a92`. A copy of that file, upgraded with the 0.3.0
wheel:

```text
Database schema upgraded: c8b1e40d7a92 -> d4e8a1c93b70.
Current: d4e8a1c93b70
Head:    d4e8a1c93b70
The database is at the current schema.
```

Twelve tables appeared — `ai_turns`, `dispatch_rules`, `downtime_reasons`,
`equipment_connections`, `nc_severities`, `personnel_skills`,
`plant_settings`, `quality_counts`, `quality_samples`, `roster`, `skills`,
`spc_signals` — **no table was lost, and all 152 rows were still there, none
of them moved.** Then `fsmes demo` from the 0.3.0 wheel against that same
upgraded file closed the loop: the software carried on over the data, which is
a different claim from "the migrations ran".

**What that run did not exercise**, because the built-in demo plant has
neither: the shift backfill attributed nothing — 0 of 29 production bookings
got a `shift_code`, correctly, because the plant defines no shift patterns —
and `gauges` was empty, so the `warn_days` write had no row to touch. Those
two are covered by the suite and by the lab's own plants; they have not been
run against a 0.2.0 file on a plant that has shift patterns and gauges in it,
and nobody should read this section as saying they have. It is also SQLite:
nobody has upgraded a populated PostgreSQL plant.

### Read this before anyone reads numbers off the plant

[CHANGELOG 0.3.0 → Honesty](https://github.com/factorysemantics/factorysemantics-mes/blob/main/CHANGELOG.md)
is the whole list, and every entry carries its own migration line. The ones
that change what something already deployed will see:

- **A performance or OEE figure above 100 % is no longer printed anywhere.**
  Where the counted work will not fit inside the run time, `performance` and
  `oee` come back `null` with a sentence naming both numbers. The ratio is
  still published, as `performance_ratio` with `counts_outrun_run_time` beside
  it. Anything consuming `performance` as a number reads `performance_ratio`
  now.
- **Availability's denominator changed meaning.** It is the time this MES
  watched, all causes together. Availability figures go **up** on a window
  with a hole in the state history that no disconnection covered, and are
  unchanged where the history is contiguous. Read `coverage` beside every
  figure from now on.
- **An order no longer finishes itself when the count reaches its quantity.**
  Somebody completes it — a person, the orders screen, or the ERP — and the
  ERP completion, the finished-goods lot and the certificate of analysis are
  all issued then. A plant that relied on an order closing itself now needs
  someone to close it.
- **A non-conformance cannot be closed without a disposition.**
  `POST /quality/nonconformances/{code}/close` returns **409** on a record
  nobody has dispositioned; anything that closed one in a single call takes
  two now. The Quality tile is "Non-conformances not closed" and counts three
  unclosed states, so it reads higher than the old "Open" tile did.
- **Every floor row now says which shift it fell in**, and `/analysis/*` can
  be asked for a shift. See the backfill note above for what that means about
  rows written before the upgrade.
- **An upgraded plant starts recording moving-range (rule 5) SPC signals on
  every plant**, from its next reading, whatever `[quality] hold_rules` says.
  No hold and no non-conformance is raised unless that list names `5`, and
  nothing is backfilled — so what is new in the records is `spc_signals` rows
  with `rule = 5` and a null `nonconformance_id`.
- **The fleet console's default port is 8090**, not 8100, because 8100 is the
  first port `fsmes score` hands an ephemeral plant. Anything pointing at
  8100 moves.
- **`fsmes pack apply` refuses to seed master data into a database that is
  not at head**, rather than writing rows through the ORM into a half-migrated
  file. Migrate first, then apply.
- **Screen numbers that were wrong are now right**, so some tiles move without
  the plant changing: "Active orders" counted more than the orders listed
  under it, the downtime pareto now states both totals, and production a
  machine counted is no longer lost when the write that books it fails.

### What the gate proves for this release

`upgrade_from_previous_release.sh` runs on every pull request and installs
**whatever PyPI serves today**, which for this release is 0.2.0: it makes a
database with that wheel, upgrades it with the wheel under test, asks the
database whether it is at head, counts the rows before and after, and then
runs the demo on the upgraded file. So the 0.2.0 → 0.3.0 path above is proven
in CI on the bytes that ship, not only by hand.

The limits are the same ones the [0.2.0 section](#what-the-gate-proves-and-what-it-does-not)
states, and they still apply: one plant's shape, on SQLite. If `init-db`
refuses on yours it names the nearest revision, lists every difference and
changes nothing. Take the backup first.

## 0.1.2 to 0.2.0

The first upgrade this project has had to describe. In order:

```bash
fsmes backup --out /mnt/nas/fsmes                 # first, always
# stop the agent and the API
pip install --upgrade factorysemantics-mes
fsmes db-status --pack packs/yourplant            # what you are at, and what 0.2.0 expects
fsmes init-db   --pack packs/yourplant            # runs the migrations the wheel carries
fsmes db-status --pack packs/yourplant            # should now agree; it exits non-zero if not
# start them again
```

**Pass the pack** (or `--plant <name>`, if you run plants from a fleet file).
Without it these commands are about *this process's* database — usually a
SQLite file beside wherever you are standing — and not about your plant's.
They say so on their first line, every time:

```text
Looking at postgresql+psycopg://fsmes:***@db:5432/plant (from the pack at packs/yourplant).
```

Read that line. On 2026-09-14 a promote script ran a bare `fsmes db-status`
straight after a successful migration, read "There is no database yet" about
a default nobody had asked about, and rolled a healthy plant back.

Your database was made by a 0.1.2 wheel, which shipped no migration scripts,
so it has tables and **no Alembic stamp**. `fsmes init-db` works out which
revision those tables correspond to rather than assuming: it rebuilds the
schema each revision in the chain produces, compares table and column names
against yours, and **stamps only on an exact match** — then runs the
migrations since. The exact-match case is the one below; the no-match case is
[further down](#if-your-database-was-made-by-012-or-earlier), and it changes
nothing at all.

**Five migrations** have landed since 0.1.2 — `153379d6cf19` is the 0.1.2
schema and `c8b1e40d7a92` is the 0.2.0 one — and every one of them is
additive. (Head moved on from `c8b1e40d7a92` in 0.3.0; the section above
carries the fifteen since.)
Nothing existing is rewritten and no row is rebuilt:

| Revision | What it adds |
|---|---|
| `a4d9c2e70f18` | `uns_publications`, so the namespace publisher can be a second reader of the outbox |
| `a3f6c81d09e2` | makes `production_logs.work_order_id` nullable, so a booking may have no work order |
| `b5c1d09e73af` | the source-attribution columns (`production_logs.source_system`, `equipment_states.reason_source`, `quality_checks.source_system` and `.supplied_result`) and the `inbound_events` ledger. Every column is nullable: a row from before the migration was observed by this MES, and null is the right answer |
| `d9a3f61c48e0` | `inbound_watermarks`, empty for a plant that never runs the SQL poller |
| `c8b1e40d7a92` | an index on `erp_messages (direction, id)` — the question both readers of the outbox ask |

That is five, in that order. **What to check afterwards:** any report that
groups production by source now has a third `ProductionSource` value,
`external`, and any query reading `SUM(production_logs.good_qty)` as
order-attributed production should filter on `work_order_id IS NOT NULL`.

### If you run plants from a registry

`plants.toml` is now `fleet.toml`, and everything it used to say about each
plant lives in that plant's own `plant.toml` — a [pack](packs.md). The fleet
loader names the command when it meets a file that still describes plants
directly:

```bash
fsmes pack migrate /etc/fsmes/plants.toml --plant bottling --out /etc/fsmes/packs/bottling
fsmes pack check /etc/fsmes/packs/bottling
```

`migrate` says what it moved, what it dropped and why, and the one value it
will not guess: a registry never held a time zone, so you type
`MES_PLANT_TIMEZONE` — or the pack's `timezone` — yourself. Read the whole
output. `secret_key` is dropped because a pack holds no secret; `init` and
`post_boot` are dropped because a pack carries no code, so a plant whose
master data came from an `init` script either carries it as data under
`[files] masterdata` or keeps its generator as a tool a person runs.

### Read the Honesty section before you start

[CHANGELOG 0.2.0 → Honesty](https://github.com/factorysemantics/factorysemantics-mes/blob/main/CHANGELOG.md).
It is long for this release, and each entry carries a migration line. The
ones that change what something already deployed will see:

- **Outbound confirmation file names changed.** A collector that globbed
  `confirmation_<order>_*.xml` must glob `*_<order>_op10.xml` or
  `*_<order>_completion.xml` instead.
- **`/metrics` series carry a `plant` label.** Add it to any dashboard,
  alert or recording rule written against the old series.
- **Shift patterns are read on the plant's clock.** A plant whose server does
  not run in the plant's own zone should set `MES_PLANT_TIMEZONE` and then
  check the shift boundaries on the dashboard.
- **`fsmes erp outbox` counts the ERP's own queue only.** Anything reading
  its totals as *all* pending outbound work needs `other_outbound` added.
- **`MES_ERPNEXT_COMPANY` now filters inbound orders**, and its default is
  empty. A bench holding more than one company's books must set it.
- **`MES_MODULES` exists and defaults to `all`**, so an install that sets
  nothing serves exactly what it served before.

### What the gate proves, and what it does not

Every pull request runs `upgrade_from_previous_release.sh`: it installs the
release that is on PyPI, makes a database with it, upgrades that database
with the wheel under test, and asks the database whether it is at head. So
the upgrade path is proven on the bytes that ship, not on a checkout.

**What it proves:** that a database with the *demo plant's* schema, made by
the previous release's wheel, reaches head under this one, and that the row
counts are read back out of it afterwards.

**What it does not prove:** that *your* plant's database does. The gate runs
one plant's shape. If yours has a table added by hand, or came from
something other than a release, `init-db` will say so, name the nearest
revision, list every difference and change nothing — which is the honest
answer, not a failure of the upgrade. Take the backup first, and if
`init-db` refuses, restore it and open a
[Discussion](https://github.com/factorysemantics/factorysemantics-mes/discussions)
with what it printed.

It also does not prove anything about a **PostgreSQL** plant's upgrade: the
gate's database is SQLite. The migrations themselves run against an empty
PostgreSQL 16.15 in the `postgres` cell on every pull request, which is a
different claim — the schema builds there; nobody has upgraded a populated
PostgreSQL plant.

## Before anything

```bash
fsmes backup --out /mnt/nas/fsmes
```

[Backup and restore](backup.md). Do this even though `fsmes plant <name>
migrate` takes its own copy: that copy lands in the same directory, on the
same disk, as the database it is protecting.

## A plant from the registry

If your plants come from a fleet file ([Plants from a fleet file](registry.md)),
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
pinned-tag deployment — stop every plant, back each one up, migrate, and on
any failure restore the backups *that run made* and check the tree back out
at the previous tag. It covers a PostgreSQL plant too, with `pg_dump -Fc` and
`pg_restore`, and every call it makes to PostgreSQL turns the statement
timeout off, because a restore a timeout can cancel is not one. If your plant
is deployed from a checkout at a tag, use that script rather than these steps
by hand; `deploy/README.md` says what it does in order.

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
fsmes info                              # the version you meant to be on
fsmes db-status --pack packs/yourplant  # the schema revision, and whether it is the current one
fsmes erp check                         # the ERP connector still reaches what it needs
fsmes opc-verify                        # every mapped tag still readable, run against the live server
```

`fsmes db-status` names the database on its first line, then prints the
revision that database is at and the revision the installed version expects,
and **exits non-zero when they differ** — so a deployment script can gate on
it rather than on someone reading the output:

```text
Looking at postgresql+psycopg://fsmes:***@db:5432/plant (from the pack at packs/yourplant).
Current: a3f6c81d09e2
Head:    a3f6c81d09e2
The database is at the current schema.
```

Three things it will not do. It will not report on a default database as
though you had named one — with neither `--pack` nor `--plant` the first line
says *the process default*. It will not say a database is empty when it could
not reach it; a database that did not answer is reported as one that did not
answer, and the exit code is still non-zero. And the password never appears:
the pack names the file the password is in, and the URL is printed with the
password replaced.

Then open the dashboard and look at a shift from before the upgrade. History
that was there before and is not there now is the thing to catch, and it is
caught by looking, not by a command.

## See also

- [Backup and restore](backup.md)
- [Plants from a fleet file](registry.md)
- [Connecting read-only to an OPC UA server](opc-readonly.md)
