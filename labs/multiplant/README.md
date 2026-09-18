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
fsmes plant all init             # schema to head, pack, master data, accounts (once)
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
| Order book | 10 orders, 151,600 | 9 orders, 27,600 |

## Each plant has an order book, and the floor works it

Each pack carries a **schedule**, not one order: enough released and planned
work to cover more than twenty-four hours of its own line's rated output, one
order released and the rest planned, due dates in sequence.

| Plant | Rated by | Book | That is |
|---|---|---|---|
| bottling | palletiser, 0.588 s → 6,122/h | 10 orders, 151,600 bottles | 24.8 h |
| machining | mill, 3.333 s → 1,080/h | 9 orders, 27,600 brackets | 25.6 h |
| finewire | wrapper, 3.6 s → 1,000 kg/h | 5 orders, 26,000 kg | 26.0 h |

A real hour makes less than the rating — scrap, micro-stops, a changeover — so
a fresh plant runs **longer** than those figures before its book is empty,
never less. Each pack's `masterdata/README.md` shows the arithmetic and says
why the first order keeps its old code and quantity.

`fsmes run-operations` — the simulated floor, started with every plant that
simulates — now has the supervisor's half of the job as well as the
operator's. When the line has made the order's quantity, `FLOOR-SUP` finishes
the order over the API and releases the next one in the book, and the audit
row carries their name. The MES still does not finish an order by itself
(decision 0029): reaching a quantity and being finished are different facts,
and only a person knows the second.

**When the book runs out** the floor says so once and invents nothing. What
the line counts after that is unassigned production, listed with its total —
which is the true answer, and the one you can see on `fsmes fleet status` and
the fleet console, both of which now show how many orders a plant has left.

To reseed a book, build the plant again: `fsmes fleet create` or `crew lab-up
--fresh`. A plant built before 2026-09-18 keeps the book it was given —
`fsmes pack apply` never rewrites an order that already exists, because a pack
that rewrote history would be rewriting production.

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
bottling/masterdata/        this plant's whole definition, as data
machining/plant.toml        the pack
machining/tag_map.json
machining/masterdata/       this plant's whole definition, as data
machining/line.json         LineSim config -> the plant's physics
machining/out/              generated per-second CSVs (regenerate, never commit)
finewire/                   the invented third pack; see its README
.data/                      databases and PID files (gitignored)
```

**Why bottling's `init.py` is gone.** It carried the argument that a line
shipping *inside* the product — bottling's six stations are the product's own
reference line, seeded by `fsmes seed-kepsim` — should not be copied into a
pack, because a copy can drift from what it copied. The argument was sound and
the cost was worse. Measured on 2026-09-14: `fsmes fleet create` and `fsmes
plant bottling init` both left this plant with **zero equipment**, and nothing
on the dashboard, in `fsmes fleet list` or on the console said so; `fsmes score
bottling` and `fsmes sweep bottling` died on their first read, because the
ephemeral plant a scored run builds applies the pack and nothing else; and the
one step that did seed the line was a script the registry had stopped naming
on 2026-09-13, so a person had to know to run it. A plant that needs a step
nobody can see is house rule 4's own example of a bug.

So bottling's line is `bottling/masterdata/` now — generated once from
`seed_kepsim` itself, and pinned against it by
`tests/test_bottling_pack.py`, which seeds one database each way and compares
them. The drift the old argument feared is a test failure rather than a
surprise. `fleet create`, `plant init`, `score` and `sweep` all build the same
plant the same way.

Carrying the whole of that line needed three more kinds in the master-data
format — `bom`, `maintenance_plans` and `shifts` — because `seed_kepsim`
builds a bill of materials, five maintenance plans and two shift patterns as
well as the equipment and the routing. Extending the format was the honest
half of the trade; the alternative was a bottling plant that quietly lost its
BOM and its calendar the day it moved into a pack.

Regenerate the machining line after editing its config:

```bash
python ../../../LineSim/generate.py machining/line.json --out machining/out
```
