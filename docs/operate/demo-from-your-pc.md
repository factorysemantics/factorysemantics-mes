# A ten-minute demo from your own PC

One Windows PC, one plant on PostgreSQL, two MES servers of it; you stop one
and the other carries on. This is the page for showing somebody the product
from your desk, in the order you type and click. Nothing here needs a key or
a network: the plant is simulated, the database is on this PC.

!!! note "Which version this describes"

    **0.5.0.** The two-server start, hold and stop on Windows is what the
    `two-nodes-on-windows` job in CI proves on every change. What is not
    proven on Windows is a server *killed* mid-shift — see
    [one plant on two boxes](redundancy.md); this page stops a server, it
    does not kill one.

## Before the day: three installs, once (about twenty minutes)

1. **PostgreSQL.** The Windows installer from postgresql.org, defaults, port
   5432; it asks you for a password for the `postgres` user — pick one and
   write it into a file, one line, no quotes: `C:\fsmes-demo\pg-password`.
   Then one database, in the *SQL Shell (psql)* it installs:
   `CREATE DATABASE demo;`
2. **The product.** In PowerShell:
   `py -m pip install --upgrade "factorysemantics-mes[postgres]"`, then
   `py -m fsmes --version` — it says 0.5.0. (If `fsmes` alone is "not
   recognized", that is PATH, not the install: every line below works as
   `py -m fsmes …`; [run it on your own box](run-it-on-your-own-box.md)
   says how to fix PATH if you want to.)
3. **The plant.** `fsmes demo bottling` once, then `fsmes demo bottling
   stop`. That leaves a copy of the bottling pack you can edit at
   `%LOCALAPPDATA%\fsmes\packs\bottling`. Copy that folder to
   `C:\fsmes-demo\bottling` and open its `plant.toml`.

## The pack: one plant, two servers, one database

In `C:\fsmes-demo\bottling\plant.toml`, make `[serve]` and `[storage]` read:

```toml
[serve]
api_host       = "127.0.0.1"
api_port       = 8010
opc_endpoint   = "opc.tcp://127.0.0.1:4841/fsmes/bottling"
simulate       = true
secret_key_env = "MES_DEMO_SIGNER"

[[serve.nodes]]
id       = "a"
api_port = 8010

[[serve.nodes]]
id       = "b"
api_port = 8011

[storage]
database_url           = "postgresql+psycopg://postgres@127.0.0.1:5432/demo"
database_password_file = "C:/fsmes-demo/pg-password"

[redundancy]
enabled       = true
lease_seconds = 6
```

Then, in one PowerShell window you keep open:

```powershell
$env:MES_DEMO_SIGNER = "any-long-phrase-both-servers-share"
cd C:\fsmes-demo
fsmes pack check bottling                      # says the pack is sound, names two nodes
fsmes sim-generate bottling\line.json --out bottling\out   # the shift the machines will play
fsmes fleet create bottling --root C:\fsmes-demo           # builds the plant, once
fsmes fleet start bottling --root C:\fsmes-demo            # the machines, then server a, then server b
fsmes fleet status bottling --root C:\fsmes-demo           # 2 nodes, 2 answered, who holds what
```

Sign in at <http://127.0.0.1:8010> and <http://127.0.0.1:8011> as the
account `fleet create` printed. Both are the same plant: a person signed in
on one is signed in on the other.

## The ten minutes

| Minute | Where | What you show |
|---|---|---|
| 0–1 | `fleet status` in the window | Two servers, one plant. Each of the three jobs (reading the machines, running the floor, housekeeping) is held by exactly one of them. |
| 1–3 | **Floor**, *lowest OEE first* | The line's six stations as cards; the worst one on top, and why: availability, performance, quality, and the hours the Plant OEE tile covers. |
| 3–4 | A station's card → its stop | A machine that stopped says why in the plant's own words. |
| 4–5 | **Orders** → an open order | What it makes, how far along, and the work instruction for it. |
| 5–7 | **Quality** | Record a check on a station (it names the machine); raise a non-conformance by hand; **Export xlsx** — the file opens in Excel. |
| 7–8 | **Engineering** → shift analysis | OEE by station for the shift, and the stops behind the downtime. |
| 8–9 | The window | `fsmes fleet stop bottling --node b --root C:\fsmes-demo`. Then reload the page on 8010: the plant is still running; `fleet status` says one node answered and holds all three jobs. |
| 9–10 | The window | `fsmes fleet start bottling --root C:\fsmes-demo` — only the server that is down is started; `fleet status` says two again. |

What to say when asked: every number comes from a simulated line replaying
one shift through a real OPC UA server, the same path a PLC would take; the
database is one PostgreSQL on this PC, and two servers of the MES share it;
what carried on when server b stopped was the other server picking up its
jobs in a few seconds, with no count booked twice. Nothing on this PC talks
to a real tag server yet.

## Afterwards

```powershell
fsmes fleet stop bottling --root C:\fsmes-demo    # both servers, then the machines
```

The database keeps the shift; the next `fleet start` carries on from it. To
start over from nothing: `DROP DATABASE demo; CREATE DATABASE demo;` in
psql, delete `C:\fsmes-demo\data`, and `fleet create` again.

## See also

- [One plant on two boxes](redundancy.md) — why PostgreSQL, the lease, what
  a failover costs, and what the Windows job does and does not prove.
- [Run it on your own box](run-it-on-your-own-box.md) — PATH, `status`,
  and a plant that is alive but silent.
