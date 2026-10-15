# One plant on two boxes

A plant can run on more than one machine against one PostgreSQL. Kill either
machine and the plant keeps going: the dashboard answers on the other
machine's address, the machines keep being read, production is booked once,
and a person signed in on one is signed in on the other.

The decision behind this, and what it deliberately does not do, is
[0042](../decisions/0042-one-plant-many-nodes.md).

## The words

A **plant** is its database. Everything a plant knows is in there, which is
why the thing that decides who may write is in there too.

A **node** is one supervisor and its children, started by
`fsmes plant <name> run --node <id>`. Every node serves the API, so every
node can answer a browser. Nodes are equal; none of them is the primary.

Three **roles** run on exactly one node at a time:

| Role | What it is | On a real plant |
|---|---|---|
| `reader` | everything `run-opc-agent` does: reading the machines, booking counts, the order-code downlink, the connection rows | yes |
| `floor` | the simulated floor | no — a simulated plant only |
| `housekeeping` | the hourly tag-history prune | yes |

A node holds a role by holding its **lease**: one row in `plant_leases`,
renewed every third of `lease_seconds`. A node that holds nothing is a
standby — it serves the dashboard and waits.

## Turning it on

In the plant's pack:

```toml
[serve]
# Both nodes must sign sessions with the same key, or a person signed in
# on one is a stranger on the other. The pack names the variable; the
# variable holds the secret.
secret_key_env = "MES_PLANT_SECRET"

[storage]
database_url          = "postgresql+psycopg://fsmes@db.example.internal/kc1"
database_password_file = "/etc/fsmes/kc1-password"

[redundancy]
enabled       = true
lease_seconds = 10
```

Then the schema, once, from any one box — `run` serves a plant, it does not
create one, and a node whose database has no lease table stands by and says
so with this command in it:

```bash
fsmes plant kc1 init          # or `fsmes plant kc1 migrate` on an existing plant
```

Then on each box, with `MES_PLANT_SECRET` set to the same value:

```bash
fsmes plant kc1 run --node box-one
fsmes plant kc1 run --node box-two
```

Starting both nodes at once is safe: `run` migrates nothing. And if you do
run the schema command on both boxes at the same time, that is safe too — the
first one into the database holds the migration lock while the second waits
for it and finds the head.

`--node` names the node. Give it a name you will recognise in a log at
02:00 — the host name is the usual answer. Without `--node` a node calls
itself `<host>:<port>`.

### A real plant: the reader is its own service

`fsmes plant <name> run` serves the API. On a plant whose pack says
`simulate = false` it has always served only the API — the reader that
subscribes to your OPC server is a service of its own ([connecting
read-only](opc-readonly.md)), and on a redundant plant you run one per box:

```bash
MES_REDUNDANCY=true MES_NODE_ID=box-one MES_LEASE_SECONDS=10 fsmes run-opc-agent
```

`fsmes plant run` reads the pack's `[redundancy]` block and hands it to its
own children; a service you start by hand reads the same three settings from
its environment. Give `MES_NODE_ID` the name you gave `--node`, so the lease
row, the log and the dashboard agree about which box is which.

Both boxes run an agent. Both wait on the lease; the one holding it reads the
machines and books, the other books nothing until the holder stops renewing.
A simulated plant — the bottling lab, `fsmes demo` — starts its reader inside
the node, so for those the two commands above are the whole thing.

Two nodes on one box, for a rehearsal, differ by port as well:

```bash
fsmes plant kc1 run --node a --api-port 8111
fsmes plant kc1 run --node b --api-port 8112
```

Each node writes its logs in its own directory under the plant's, so two
nodes on one box never append to one file.

### What it refuses, and why

- **A SQLite URL.** A file database cannot be shared by two boxes. The
  single-box SQLite demo is unchanged and needs none of this.
- **A signing key nothing set.** `[redundancy]` with no
  `secret_key_env`, or with a variable that is not set in the environment
  that starts the plant, refuses to start in one sentence. The lab's own
  default key is refused too: it is a convenience for a demo, and two boxes
  agreeing by luck is not redundancy.
- **`lease_seconds` under 2.** A lease shorter than the time a busy box can
  take to renew one is a plant that fails over because it was busy.

## Seeing which node is which

`/health` carries a `node` block: the node's name, whether it was named or
is calling itself after its host and port, `lease_seconds`, the roles this
node holds, and who holds each role.

`fsmes plant kc1 status --api-port 8112` prints the same as a line a person
can read:

```
  kc1          healthy            supervisor       http://127.0.0.1:8112
               ACME Beverages / Kansas City
               node b, holding floor, housekeeping
```

The dashboard says it in its header: **served by *b* · machines read by
*a***. If the line says *machines read by nobody yet*, no node holds the
reader lease — which is what you see in the seconds after a failover, and
what you would see if the database were unreachable from every node.

## What a failover costs

One lease, plus a renewal interval to notice, plus whatever the new holder
needs to connect to the machines. With `lease_seconds = 10` that is on the
order of fifteen seconds in which no counts are booked — and then they are
all booked, because the counters are absolute and the baseline the dead node
left behind is in the database, committed in the same transaction as the
booking it belongs to. Nothing in the gap is lost; it arrives late.

Measured on a developer's machine on 2026-10-15, two bottling nodes against
one PostgreSQL with `lease_seconds = 4` and the line replayed at 6x: the
`reader` role moved 1.00 lease units after its holder was terminated and the
`floor` role 1.19, and production booked across both failovers equalled the
counters' own movement exactly, per machine. Your numbers will differ; the
shape will not.

A node that comes back comes back as a standby. It does not take a role the
other node is holding.

## What this does not do

- **The front door is yours.** Two nodes are two addresses. Give people one
  of them with a DNS name they can repoint, a floating address, or a proxy —
  nginx in front of the API, as `docker/docker-compose.yml` already does.
  This product does not pick for you.
- **The database is not made redundant by this.** One PostgreSQL is one
  PostgreSQL. Its own standby, its own backups and its own failover are
  PostgreSQL's job, and well-documented there. See
  [Backups](backup.md) for what this product expects you to keep.
- **The PLC write path is single by design.** Writing a setpoint back to a
  machine runs under the reader's lease and goes no further. Write-back is
  not a built feature of this product.
- **ERP sync, the UNS publisher and the inbound watchers are still single by
  assumption.** They are separate commands; run one of each. They take the
  same lease when they are made redundant.
- **A conversation on the AI tab starts over after a failover.** The thread
  lives in the process that started it. What was asked and what was answered
  is in the database; the thread is not.

## If it goes wrong

| What you see | What it means |
|---|---|
| Both nodes say *standby* and nobody holds the reader | No node can write to the database. Look at the database, not at the nodes. |
| A node exits at start with one sentence about `MES_SECRET_KEY` | `secret_key_env` names a variable that is not set in the environment that started the plant. |
| A node exits at start with one sentence about `MES_DATABASE_URL` | `[redundancy]` with a SQLite URL. |
| The dashboard says *machines read by* a node that is gone | The lease has not expired yet. Wait `lease_seconds`. |
| Counts stop and do not resume | Check the surviving node's log for `standby: another node holds this role` — if it says that forever, the dead node's lease is being renewed by something, which means the node is not actually dead. |

## See also

- [Deploying a plant](deploy.md)
- [Plant packs](packs.md)
- [Backups](backup.md)
- [Decision 0042 — a plant is its database, and a node asks the database
  whether it may write](../decisions/0042-one-plant-many-nodes.md)
