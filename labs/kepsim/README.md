# KepSim — a six-station line, replayed or served from Kepware

An HTK-shaped line (**LD → RD → Washer → QI → Refill → Palletiser**) as live OPC
tags, so everything the MES claims to do — OEE, downtime ripple, counter resets,
quality bursts — can be practised and tested against something that behaves like
a real plant.

> **Looks complicated?** Most of this lab exists only to *fake* a plant — the
> CSV generator, the ODBC plumbing, the device creation all disappear when the
> plant is real. The generalized, take-it-to-work version of this path is
> [docs/onboarding](../../docs/onboarding/README.md): a one-page worksheet, two
> hand-out guides (IT and Engineering), and `fsmes opc-verify`. This lab is the
> rehearsal; that folder is the real thing.

It runs two ways:

| | how | needs |
|---|---|---|
| **Replay** (default) | MES-TWIN serves the generated CSVs itself: `fsmes run-opc-sim --replay` | nothing — works anywhere |
| **Kepware** | KEPServerEX's **Advanced Simulator** driver steps through the same CSVs over ODBC | Kepware, an elevated shell, a licence window |

Both feed the same MES through the same agent. The only thing that changes is
the tag map, which is the whole point: swapping the machine layer is a config
change, not a rewrite.

## Replay — the short path

```bash
fsmes sim-generate labs/kepsim/line.json
```

Writes `out\*.csv` (7 tables × 3600 rows), `out\schema.ini`, and
[scenario.md](scenario.md) — the timeline of everything scripted to go wrong.
Then, from the repo root:

```bash
set MES_TAG_MAP_FILE=config/tag_map_kepsim.json
fsmes init-db
fsmes seed-kepsim
fsmes run-opc-sim --replay
```

and in a second shell (same variable set):

```bash
fsmes run-opc-agent
```

Create and release a work order for `FG-BOTTLE`, and the line's counters start
booking against it. `fsmes run-api` then shows it on the dashboard, with OEE per
station. The whole path is covered by `pytest -m slow`.

## Kepware — the full path

**1. Generate the data** — as above.

**2. Create the ODBC data source — needs an ELEVATED PowerShell**

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File labs\kepsim\setup_dsn.ps1
```

System DSNs are machine-wide, so this one step needs "Run as administrator". It
creates the 32-bit System DSN `KepSimCSV` pointing at `out\`. Both halves
matter: the Kepware runtime is a **32-bit service**, so a 64-bit or per-user DSN
is invisible to it — the classic silent failure.

**3. Tell Kepware about it**

```bash
python labs/kepsim/kepware_setup.py --discover   # look first: the driver's real property names
python labs/kepsim/kepware_setup.py              # creates channel SimLine + 7 devices
```

Needs `.env` beside this file (copy `.env.example`, add your Kepware admin
password). The script asks the Config API for the Advanced Simulator's property
schema and builds its payload from that, rather than hardcoding guessed field
names.

**4. Prove it's live**

```bash
.venv\Scripts\python.exe labs\kepsim\verify_readback.py
```

Prints a per-station table — state, counters advancing, analog values — and
PASS/FAIL. Which stations and tags it expects come from
`config/tag_map_kepware.json`, the same file the MES agent reads, so this check
and the MES cannot drift apart. Or watch it in **OPC Quick Client** from the
Kepware config (Tools → Launch OPC Quick Client).

**5. Point the MES at it**

```bash
set MES_TAG_MAP_FILE=config/tag_map_kepware.json
set MES_OPC_ENDPOINT=opc.tcp://127.0.0.1:49320
set MES_OPC_SECURITY=Basic256Sha256,SignAndEncrypt
set MES_OPC_USER=Administrator
set MES_OPC_PASSWORD=...
fsmes run-opc-agent
```

The agent presents the **same certificate** `verify_readback.py` minted, so
Kepware only has to be told to trust one client.

## What's in the data

7 tables, ~34 tags. Per station: `State` (0 stopped · 1 running · 2 starved ·
3 blocked · 4 down · 5 changeover), `GoodCount`, `ScrapCount`, `CycleTimeMs`,
plus one process value each (`MotorTemp`, `WashTemp`, `FillWeight`…). Plus a
`Line` table with `OrderId` / `OrderActive` / `LineGoodCount`.

Stations sit behind finite buffers, so they genuinely **starve** and **block** —
when RD breaks down at t=1500, LD backs up while everything downstream runs dry
one buffer at a time. See [scenario.md](scenario.md) for the full timeline and
the lesson each event stages.

### How those tags become MES facts

`State` is a PLC integer, and mapping it is a site decision with teeth
(`config/tag_map_kepsim.json`):

| line state | MES state | why |
|---|---|---|
| 1 running | `running` | |
| 0 stopped · 2 starved · 3 blocked | `idle` | the machine is healthy, it just has nothing to do |
| 4 down | `down` | the only thing that counts against availability |
| 5 changeover | `setup` | a **planned** stop — booking it as downtime would silently wreck OEE |

Every station is read-only (`order_tag: null`): a CSV replay cannot be written
to, and the MES has no business commanding someone else's Kepware. Orders still
flow, because OPC counter deltas auto-start a released operation.

Rated cycle times live in the tag map and `fsmes seed-kepsim` reads them from
there, so OEE performance is measured against the rate the data was actually
generated at.

## Gotchas worth knowing

- **The Config API is not the OPC UA endpoint.** 57412 (HTTP) / 57512 (HTTPS) for the
  Configuration API; 49320 for `opc.tcp`. Putting 49320 in `.env` sends HTTP at the binary
  UA port and the only symptom is `ConnectionResetError 10054`, which reads like a dead server.
- **The Config API content-negotiates and defaults to HTML.** Without `Accept: application/json`
  the `/doc/drivers/...` endpoints return a web page, and asking for a driver by name 404s -
  which looks exactly like the driver not being installed.
- **The table name includes the extension.** `Table Selection` must be `LD.csv`, not `LD`.
  Getting it wrong is near-silent: the channel and all devices are created happily, then every
  device logs *"not responding"* and generates zero tags.
- **Generated tag names carry the table prefix.** The Advanced Simulator names tags
  `<table>_<column>` with the dot sanitised, so `State` on `LD.csv` is addressed as
  `SimLine.LD.LD_csv_State` - which is why `tag_map_kepware.json` uses
  `ns=2;s=SimLine.LD.LD_csv_{tag}` rather than the bare column name.
- **The first UA connection is always rejected.** The client certificate lands in Kepware's
  `UA/Server/RejectedCertificates` folder under ProgramData; trust it once in the OPC UA
  Configuration Manager and both `verify_readback.py` and the MES agent are in.
- **Unlicensed Kepware runs 2-hour demo windows.** Data stops until you restart the runtime service. That rhythm (works, dies ~2 h later, works again after restart) is itself a lesson — it's in the MES vault's Kepware notes. The replay path has no such limit.
- **Regenerating CSVs is safe while running** — the driver re-reads rows; it's the same seed, so identical data.
- **`out\` is git-ignored** (regenerable), as are `.env` and `certs\` (secret).
- **The counters wrap every hour** when the file loops back to row 0. That is a free counter-reset drill, and a genuine test of the agent's reset handling: it rebaselines rather than booking a phantom hour of production.

## Files

| File | Role |
|---|---|
| `line.json` | the line, described as data — stations, rates, buffers, and the script of things going wrong |
| _(the model)_ | `fsmes.sim.generate` — the discrete-event line model → CSVs (seeded, deterministic). It lives in `src/` because the simulator is a product component, not test scaffolding |
| `setup_dsn.ps1` | 32-bit System DSN (elevated) |
| `kepware_setup.py` | Config API: channel + devices (`--discover` / `--delete`) |
| `verify_readback.py` | OPC UA read-back proof, driven by the MES tag map |
| `scenario.md` | what breaks, when, and why it's interesting |

The MES-side pieces live in the main package:
`src/fsmes/integrations/opc/csv_replay.py` (the replay server),
`tag_map.py` (the wiring), `security.py` (the certificate handshake),
`src/fsmes/seed_kepsim.py` (master data), `tests/test_kepsim.py`.
