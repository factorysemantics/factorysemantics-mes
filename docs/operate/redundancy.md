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

### Two nodes on one box

Two boxes is the point of all this. Two nodes on *one* box is the cheap way
to see it work before you own the second box — and it is how the lab on the
author's desk runs — so a pack may name the nodes that run on this machine:

```toml
[serve]
api_host     = "127.0.0.1"
api_port     = 8010          # the plant's address: one of its nodes serves here
secret_key_env = "MES_BOTTLING_SECRET"

[[serve.nodes]]
id       = "a"
api_port = 8010

[[serve.nodes]]
id       = "b"
api_port = 8011

[redundancy]
enabled       = true
lease_seconds = 6
```

Then the fleet commands act on every node the pack names:

```bash
fsmes fleet start bottling      # starts node a and node b, each with its own log
fsmes fleet status bottling     # 2 nodes, 2 answered, and what each one holds
fsmes fleet stop bottling       # stops both
```

Each node keeps its own pid file and its own log directory
(`logs/bottling/a/`, `logs/bottling/b/`), so stopping the plant stops both
and a failover is readable in one node's log without the other's lines in
it. `fsmes fleet start` on a plant with one node already up starts only the
one that is down, which is what you want after killing one on purpose.

What `fsmes pack check` refuses here, so that you find it before half a
plant is running: two nodes sharing a port (one of them would fail to bind
and the plant would look healthy on the other), two nodes sharing a name
(they would take each other's lease and both believe they hold it), nodes
named by a pack whose `[redundancy]` is off (both would read the machines
and book every count twice), and nodes with no `secret_key_env` between
them (whoever signed in on one would be signed out the moment the other
answered).

A plant that names no nodes is the ordinary plant on one address. That is
almost every plant, and nothing above changes it.

**Two nodes on one box, on Windows:** CI starts two nodes of one plant on a
Windows runner, checks that both answer `/health`, that exactly one of them
holds all three roles, and that `fleet stop` stops both. What is **not**
proven on Windows is the kill: the Windows runners ship a PostgreSQL that is
installed and not started, and a failover without a shared PostgreSQL is not
a failover. The lease, the takeover and the baselines are proven on Linux —
in CI and by hand on the author's box — and the node-starting, node-stopping
and role-holding half is proven on both.

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

## Run it with docker compose

If you would rather see it than read about it, there is a compose file that
is one plant on two nodes: one PostgreSQL, one machine layer, two nodes of
the lab's bottling plant, and nginx in front of them so there is one address.

```bash
docker compose -f docker/docker-compose.redundant.yml up --build   # 1
# open http://localhost:8000/dashboard                             # 2
docker compose -f docker/docker-compose.redundant.yml kill node-a  # 3
docker compose -f docker/docker-compose.redundant.yml start node-a # 4
```

What you see:

1. Four or five minutes the first time, while the image builds and the line's
   replay data is generated. Then the log settles into two nodes talking to
   one database, and one of them saying it took the three roles.
2. The dashboard, on one address, with the header saying **served by *a* ·
   machines read by *a***. The counts move while you read it. Sign in as
   `ADMIN` / `admin`.
3. A power cut, as far as the plant is concerned: `kill` sends no shutdown
   and waits for nothing. Leave the page open and do not reload it. Within
   about a lease the header says **served by *b* · machines read by *b***
   and the counts carry on from where they were — not from zero, because the
   counter baselines the dead node left behind are in the database. You are
   not asked to sign in again: both nodes sign sessions with the same key.
4. Node a comes back as a standby. It serves the dashboard again and holds
   nothing, because b is holding the roles and a node that comes back does
   not take work off a node that is doing it.

One command does all four and checks every claim in them:

```bash
bash scripts/kill-a-node.sh
```

It prints the takeover in lease units with the bound beside it, how many
requests the one address refused while a node died (it should be none), what
each machine booked against what its counters moved, and how many readings
were stored twice (it should be none). It is a shell script because
everything in it is `docker compose` and SQL; the MES itself knows nothing
about containers. The same script runs on every pull request in the
`two-nodes-in-compose` job, so this page cannot quietly stop being true.

When you are done: `docker compose -f docker/docker-compose.redundant.yml
down -v` throws the containers and the data away.

Two things about this compose file worth knowing before you copy it:

- **The machine layer is one service, shared.** A simulated OPC server
  started by a node would die with that node, and the surviving reader would
  see the replay restart from the beginning — which reads as a PLC reset, so
  nothing would be booked. A real plant has one PLC and points both nodes at
  it; that is what this file imitates. It is also why a redundant plant's
  nodes never start `run-opc-sim` themselves.
- **The pack is the lab's bottling line with three things changed**, in
  `docker/redundant/plant.toml`: a PostgreSQL URL, a `secret_key_env`, and
  `[redundancy] enabled = true`. Nothing else about the plant differs. The
  other compose file, `docker/docker-compose.yml`, is untouched and is a
  different demo: one plant, N stateless API replicas, one of everything
  else.

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
what you would see if the database were unreachable from every node. **The
header follows a failover on the page's own poll** — within five seconds,
with no reload — so a page left open on a phone shows the takeover as it
happens.

The [fleet console](fleet.md) shows a plant whose pack names its nodes once,
with its nodes under it: how many there are and how many answered, then one
line each saying where that node is and what it holds in words — *reads the
machines*, *runs the floor*, *prunes the history*, or *nothing yet* for a
standby. A node that did not answer is **unknown** there and never a node
holding nothing. Its `fleet.json` carries the same, node by node, for
anything else that wants to read it.

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
  nginx in front of both nodes, as `docker/docker-compose.redundant.yml`
  does. This product does not pick for you.
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
