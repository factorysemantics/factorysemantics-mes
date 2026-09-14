# Multi-plant lab — several MES systems, one machine

Three completely separate MES instances, described by three **plant packs**,
running side by side from one wheel:

| Plant | Shape | Dashboard | Database |
|---|---|---|---|
| **bottling** | ACME Beverages, Kansas City — 6-station bottling line | http://127.0.0.1:8010/dashboard | `.data/bottling.db` |
| **machining** | Northgate Machining, Cell A — 3-station machining cell | http://127.0.0.1:8020/dashboard | `.data/machining.db` |
| **finewire** | Fine Wire Drawing Works, Hall 2 — invented, never started | http://127.0.0.1:8050/dashboard | PostgreSQL, per its pack |

Sign in as `SCOTT` / `operator`, or `ADMIN` / `admin`.

```bash
fsmes pack check bottling        # refuse a pack before it touches anything
fsmes plant all init             # apply every pack, create every schema  (once)
python bottling/init.py          # bottling's line: the product's own, see below
fsmes plant all start            # bring them up
fsmes plant all status           # who is alive and answering
fsmes plant all stop
```

Swap `all` for a plant's name to act on one. `fleet.toml` is the list.

## What this proves

**There is no multi-tenant code in the MES, and there does not need to be.**

Each plant is its own set of processes with its own database. Isolation is *by
construction*: the bottling API has no code path that could reach Northgate's
data, because it was never told Northgate exists. That is a much stronger
guarantee than a `WHERE site_id = ?` somebody can forget, and it is the same
model a real deployment uses — a node per plant, on the plant's own hardware.

Every difference between the plants is in its pack, and compiles to an
environment variable:

```
MES_DATABASE_URL   which database        -> data isolation
MES_API_PORT       which dashboard       -> several UIs at once
MES_OPC_ENDPOINT   which OPC UA server   -> several machine layers at once
MES_TAG_MAP_FILE   which machines exist  -> different plants entirely
MES_REPLAY_DIR     which line data       -> different physics
MES_MODULES        what this plant serves
MES_PLANT_TIMEZONE what clock it keeps
MES_WORDS          what it calls things
```

No product code was added to run a second plant, a third, or a plant with two
modules switched off. Adding one is a directory and a line in `fleet.toml`.

## The two plants disagree about the line

Deliberate. If any plant-specific assumption has leaked into MES code rather
than config, running these two together is where it shows:

|  | bottling | machining |
|---|---|---|
| Stations | 6 | 3 |
| Names | LD, RD, Washer, QI, Refill, Palletiser | Saw, Mill, Deburr |
| Analogs | FeedRate, MotorTemp, WashTemp, RejectPct, FillWeight, AirPressure | BladeLoad, SpindleTemp, CycleForce |
| Rates | 102–115/min | 18–25/min |
| Buffers | 20 | 6 (nowhere to hide) |
| Product | Filled Bottle 500ml | Machined Bracket |
| Routing | RT-BOTTLE, 6 ops | RT-BRACKET, 3 ops |
| Quality spec | fill_weight 494–506 g | spindle_temp 50–70 °C |

## The third plant disagrees with the format

They agree about everything a `plant.toml` carries, which is why there is a
third one. `finewire/` is invented — clock, profile, modules, words, storage,
ERP mode, namespace mode, inbound feed and tag-map addressing style all
different — and it is what proves the pack format is not the demo plant's
shape with another name on it. It has never been started and there is no line
data for it. Its own README says what it is and what it proved.

## What to look for once the two are running

Both lines replay a scripted hour, so there is something real to find in each:

- **A planned stop must not count as downtime.** Both plants change over
  (bottling at t+2400s, machining at t+1500s). If either plant's availability
  drops during its changeover, the state mapping is wrong. This is the single
  mapping decision with real consequences — calling a planned stop downtime
  silently destroys every availability figure you have.
- **A breakdown ripples.** Machining's mill fails at t+2700s with buffers of
  only 6, so the saw blocks and the deburr cell starves almost immediately.
  Bottling's RD fails at t+1500s with buffers of 20 and takes far longer to
  propagate. Same MES, two different plant physics.
- **A precursor before the failure.** Both drift an analog upward before the
  stop — the mill's spindle to 74 °C, the RD's motor to 88 °C. This is what a
  predictive agent would be watching.
- **Micro-stops are invisible in availability.** Bottling's palletiser and
  machining's saw both stutter all shift. They cost OEE *performance*, not
  availability — which is exactly why an availability-only number flatters a
  struggling line.
- **A counter reset must book zero units.** Bottling's RD counter snaps to
  zero at t+3000s. The agent should re-baseline and book nothing, never book a
  phantom 100,000 units.
- **Downtime is honestly unlabelled.** Nothing on an OPC-fed line labels a
  stop, so the pareto reports ~100% unlabelled. That is the correct answer.

## Files

```
fleet.toml                  the list of packs, and where the data goes
bottling/plant.toml         the pack
bottling/tag_map.json       which tags exist, and what its State integers mean
bottling/init.py            a LAB TOOL: releases an order on the product's own line
machining/plant.toml        the pack
machining/tag_map.json
machining/masterdata/       this plant's whole definition, as data
machining/line.json         LineSim config -> the plant's physics
machining/out/              generated per-second CSVs (regenerate, never commit)
finewire/                   the invented third pack; see its README
.data/                      databases and PID files (gitignored)
```

**Why bottling's `init.py` is still a script.** A pack carries no code
([decision 0022](../../docs/decisions/0022-what-a-plant-pack-may-contain.md)),
and master data belongs in `masterdata/` as data — which is exactly what
machining's did with the 156-line `seed.py` it used to have. Bottling's line
is not this plant's, though: it is the product's own reference line, seeded by
`fsmes seed-kepsim`, and duplicating it here as data would be one copy that
could drift from another. So bottling's pack carries no master data, its pack
says so, `fsmes pack apply` says it seeded nothing, and `init.py` stays a
script a person runs to release an order.

Regenerate the machining line after editing its config:

```bash
python ../../../LineSim/generate.py machining/line.json --out machining/out
```
