# 0042 — A plant is its database, and a node is a process that asks the database whether it may write

- **Status:** accepted (2026-10-15, by the maintainer)
- **Date:** 2026-10-15
- **Deciders:** maintainer
- **Builds on:** [0021](0021-one-database-per-plant.md), [0006](0006-sqlite-laptop-postgresql-plant.md), [0034](0034-a-count-is-owed-until-it-is-booked.md)

## Context
A plant engineer was told, at work, that his site could not make its server
redundant *because of their MES*. Asked whether this one could, the honest
answer on 2026-10-15 was no, and the reason was specific rather than
architectural.

The API has been stateless about logins since the beginning: the session is
an HMAC token and the role is re-read per request, so two API processes
sharing one database already share their users — provided the signing key is
the same on both, which until today happened by luck (a lab default) and not
by design. `docker/docker-compose.yml` has scaled the API behind nginx for
months.

What could not be doubled was everything else. The reader kept its counter
baselines in memory, so two readers each held their own idea of where a
machine's counter had got to and each booked the whole delta: a plant that
made 1,000 bottles would report 2,000. Nothing anywhere in `src/` took a
lease, a leader or a row lock — the only lock in the product was an O_EXCL
file, local to one filesystem. Two supervisors both ran the migrations at
start, against the same empty database, at the same moment. The simulated
floor would have raised two of every inspection.

## Options considered
| Option | For | Against |
|---|---|---|
| A lease row per role in the plant's own database | The plant *is* its database (0021), so the thing that decides who writes is already shared and already backed up; no new dependency; works the same on a plant PC and in Kubernetes | Liveness is bounded by a timeout, so a failover costs seconds; needs the database clock, never the box's |
| `SELECT … FOR UPDATE SKIP LOCKED` on a work table | No timeout at all | Holds a transaction open for the lifetime of a reader, which is hours; a reader is not a queue consumer |
| An external coordinator (etcd, ZooKeeper, Consul) | The textbook answer, sub-second failover | A second thing to install, secure and back up on a plant PC that has one database and no platform team; refused by 0006's premise |
| Active/standby by hand (the operator starts the second box) | No code at all | The failure happens at 02:00 and the operator is the thing that failed |

## Decision
A *plant* is its database. A *node* is one process tree running
`fsmes plant <name> run --node <id>`; every node serves the API, and
`--api-port` lets two of them run on one box. Three *roles* run on exactly
one node at a time — `reader` (everything the OPC agent does), `floor` (the
simulated floor, which a real plant does not have) and `housekeeping` (the
tag-history prune) — and a node may write under a role only while it holds
that role's lease.

The lease is one row per role in `plant_leases`, taken and renewed by a
single statement:

```sql
UPDATE plant_leases SET holder = :me, renewed_at = :now, expires_at = :then
 WHERE role = :role AND (holder = :me OR expires_at <= :now)
```

Every time in it comes from `SELECT now()` on the database, read in the same
transaction — never from the box's clock, which is the one thing two boxes
are guaranteed to disagree about. Renewal is every `lease_seconds / 3`. A
holder that loses its lease stops writing at once and goes back to standing
by; losing a lease is not an error and does not exit the process.

The counter baselines move out of the reader's memory into
`counter_baselines`, written in the same transaction as the booking they
belong to. That is what makes "exactly once" true rather than hoped for: the
booking and the record of where the counter had got to either both commit or
neither does, so a node taking over continues the count instead of restarting
it. It also makes a single-box restart honest, which it was not before.

`[redundancy]` requires PostgreSQL and refuses a SQLite URL in one sentence —
a file cannot be shared by two boxes — and requires `[serve] secret_key_env`
to name a variable that is actually set, because two nodes that sign
sessions with different keys are two plants that look like one. On
PostgreSQL the migrations run inside a session-scoped
`pg_advisory_lock`; transaction-scoped would hold for the first revision of
a chain and nothing after it, because Alembic runs a transaction per
revision.

## Consequences
Easier: a plant survives losing a box. `/health` and `fsmes plant <name>
status` say which node answered and which node is reading the machines, and
the dashboard carries one line saying the same, so "which one is live" is a
question a person can answer from a browser.

Harder, and deliberately so:

- **A failover costs a few lease units.** Measured on this machine, two
  bottling nodes against one PostgreSQL with `lease_seconds = 4`: the role
  moved 1.00 and 1.19 lease units after the holder was terminated. The bound
  the test holds is 3. Counts across the failover were exact.
- **The standby reader does not connect to the OPC server.** It could, and
  then a takeover would not pay for a connection. It does not, because a
  reader that subscribes is a reader one bug away from booking, and the
  cost it saves is under a second.
- **`housekeeping` stays inside the API process**, guarded by its lease,
  rather than moving to its own command. It is one hourly prune.
- **The simulated machine layer is not a node.** A node of a simulated plant
  starts no replay: the OPC server is the floor, there is one of it, and a
  second one would restart every counter — which the reader would read as a
  PLC reset.
- **The same lease, later:** ERP sync, the UNS publisher and the inbound
  watchers are separate commands and still single by assumption. They take
  the same lease when they are made redundant; nothing about them changes
  here.
- **The PLC write path stays single by design.** Write-back is not built; it
  runs under the reader's lease and goes no further in this decision.
- **A conversation on the AI tab starts over after a failover.** The store
  is per process and only finished turns are in the database. The record of
  who asked what survives; the thread does not.
- **Known and not fixed:** code allocation is `COUNT(*)+1` and `next_serial`
  is a read-modify-write. Both write to unique columns, so two nodes racing
  fail loudly rather than producing a wrong number. They want a sequence,
  which is its own change.
- **The front door is yours.** Two addresses, or nginx as in the compose
  file. The database's own standby is PostgreSQL's job and this product has
  no opinion about it.

Revisit when a plant runs more than two nodes, or when a site asks for a
failover faster than a lease.

## House rules touched
**Never invent production** is the rule a second reader would break, and the
one this exists for: the baseline moves in the booking's own transaction, and
a reading no higher than the baseline books nothing. **Every figure carries
its condition** — the takeover numbers above name the machine, the lease
length and the replay speed, and the test that produces them measures in
lease units rather than seconds, because a shared runner makes every absolute
bound flaky. **Config, not code, at plant boundaries**: `lease_seconds` and
the node's name and port are a pack's and a command's, not a constant.
