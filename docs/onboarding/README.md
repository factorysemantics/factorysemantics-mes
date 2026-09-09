# Taking the MES to a real plant

The Kepware lab (`labs/kepsim`) looks complicated for one reason: on a laptop there
is no plant, so most of the lab exists to **fake one**. At a real plant the plant is
real, Kepware is already running, and the PLCs are already feeding it tags —
so all of that faking disappears. Here is every piece of the lab and what
happens to it at a real plant:

| kepsim piece | what it does | at a real plant? |
|---|---|---|
| the line generator | invents an hour of fake machine data | **not needed** — the plant makes real data |
| `setup_dsn.ps1` | plumbs the fake data into Kepware via ODBC | **not needed** |
| `kepware_setup.py` | creates fake devices inside Kepware | **not needed** — Engineering already built the project |
| `verify_readback.py` | proves the tags are live before trusting them | **kept**, generalized: `fsmes opc-verify` |
| `config/tag_map_*.json` | which tags mean what | **kept** — yours comes from a one-page worksheet |
| the agent, dashboards, OEE | the actual MES | **kept, unchanged** |

What is left is small: **a config file, two conversations, and four commands.**

## The posture: read-only observer

At a real plant the MES starts as a *shadow MES*. It subscribes to tags and computes what it
sees — states, downtime, OEE, trends. It writes **nothing** to Kepware or any
PLC: every machine in a worksheet-generated tag map is marked read-only
(`order_tag: null`), so there is no code path that writes. That single fact is
what makes the IT and Engineering conversations easy, and it is the honest
answer to "what happens if it misbehaves?" — nothing, on the plant side.

## The two conversations

Everything you need from other people fits on one page each:

- **[GUIDE-IT.md](GUIDE-IT.md)** — for whoever owns the Kepware server.
  You need five things: the endpoint URL, confirmation the OPC UA interface is
  on, the security policy, a read-only account, and one certificate trusted.
  The guide has a copy-paste request and a troubleshooting table.

- **[GUIDE-ENGINEERING.md](GUIDE-ENGINEERING.md)** — for whoever knows the
  machines. They fill one CSV row per machine: four tag addresses, what the
  state values mean, and the rated cycle time. The guide explains why each
  column matters and what happens when one is blank.
  Template: [worksheet-template.csv](worksheet-template.csv) ·
  filled example: [worksheet-example.csv](worksheet-example.csv)

## The path

**1. Engineering fills the worksheet** (send them the guide + template).
Then turn it into config:

```bash
fsmes make-tag-map worksheet.csv --out config/tag_map_plant.json
```

It refuses anything the agent could not honestly interpret, and prints a note
for every blank that has a consequence (no scrap counter → quality reads
unknown, and so on).

**2. IT grants access** (send them the guide). Put their answers in `.env`:

```bash
MES_TAG_MAP_FILE=config/tag_map_plant.json
MES_OPC_ENDPOINT=opc.tcp://their-server:49320
MES_OPC_SECURITY=Basic256Sha256,SignAndEncrypt
MES_OPC_USER=the-read-only-account
MES_OPC_PASSWORD=...
```

**3. Look before you trust.** Browse what the server actually exposes — this is
where worksheet typos show up:

```bash
fsmes opc-browse --contains Filler
```

**4. Prove the map.** Reads every mapped tag twice through the agent's own
resolution code and reports per machine — readable? changing? do the State
values map?

```bash
fsmes opc-verify --seconds 15
```

Expect the **first attempt to fail with a certificate rejection** — that is
normal, one-time, and explained in the IT guide. Run it while the line is
running for the strongest answer.

**5. Give the MES its equipment.** The agent can only attach facts to
equipment rows that exist:

```bash
fsmes init-db
fsmes seed-line LINE1 --line-name "Filling Line 1" --site HTK --site-name "HTK Plant"
```

Stations, names and cycle times come from the tag map — the same file, so
nothing can disagree. Add `--no-routing` to observe only (states, downtime,
trends, but no production booking).

**6. Turn it on.**

```bash
fsmes run-opc-agent
fsmes run-api
```

Dashboard at `/dashboard`, shift analysis at `/dashboard/analysis`, the 3D
line at `/dashboard/line`. To book production against an order, create and
release a work order for `FG-LINE1` (API or dashboard) — machine counters do
the rest.

## Rehearse first

The whole path can be practised end-to-end at home before doing it for real:
the kepsim lab **is** this path with a fake plant behind it. `labs/kepsim`'s
README runs the same verify → seed → agent sequence against simulated data,
including a real local KEPServerEX if you have one installed.

## Day one honesty

Three things will look "wrong" on the first day and are actually correct:

- **OEE says unknown, not a number.** The MES refuses to compute from history
  it does not have. Numbers appear as observation accumulates.
- **The downtime pareto says ~100% unlabelled.** Nothing on an OPC-fed line
  labels a stop. That is the finding, not a bug — reason codes are an operator
  workflow you add later.
- **A machine with no scrap counter shows no quality figure.** Blank on the
  worksheet means unknown on the dashboard. Nothing is invented.
