# Multi-plant lab — several MES systems, one machine

Two completely separate MES instances running side by side on this laptop:

| Plant | Shape | Dashboard | OPC UA | Database |
|---|---|---|---|---|
| **bottling** | ACME Beverages, Kansas City — 6-station bottling line | http://127.0.0.1:8010/dashboard | `:4841` | `.data/bottling.db` |
| **machining** | Northgate Machining, Cell A — 3-station machining cell | http://127.0.0.1:8020/dashboard | `:4842` | `.data/machining.db` |

Sign in as `SCOTT` / `operator`, or `ADMIN` / `admin`.

```powershell
.\fsplant.ps1 all init     # create both schemas and seed both plants (once)
.\fsplant.ps1 all start    # bring both up
.\fsplant.ps1 all status   # who is alive and answering
.\fsplant.ps1 all stop     # shut both down
```

Swap `all` for `bottling` or `machining` to act on one.

## What this proves

**There is no multi-tenant code in the MES, and there does not need to be.**

Each plant is its own set of processes with its own database. Isolation is *by construction*: the bottling API has no code path that could reach Northgate's data, because it was never told Northgate exists. That is a much stronger guarantee than a `WHERE site_id = ?` somebody can forget, and it is the same model a real deployment uses — a node per plant, on the plant's own hardware.

Every difference between the two plants is an environment variable:

```
MES_DATABASE_URL   which database        -> data isolation
MES_API_PORT       which dashboard       -> two UIs at once
MES_OPC_ENDPOINT   which OPC UA server   -> two machine layers at once
MES_TAG_MAP_FILE   which machines exist  -> different plants entirely
MES_REPLAY_DIR     which line data       -> different physics
MES_SECRET_KEY     per-plant token key   -> neither accepts the other's logins
```

No product code was added to run a second plant. Adding a third is a block in `fsplant.ps1`'s registry, a tag map, and generated line data.

## The two plants disagree about everything

That is deliberate. If any plant-specific assumption has leaked into MES code rather than config, running these two together is where it shows:

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

## What to look for once they are running

Both lines replay a scripted hour, so there is something real to find in each:

- **A planned stop must not count as downtime.** Both plants change over (bottling at t+2400s, machining at t+1500s). If either plant's availability drops during its changeover, the state mapping is wrong. This is the single mapping decision with real consequences — calling a planned stop downtime silently destroys every availability figure you have.
- **A breakdown ripples.** Machining's mill fails at t+2700s with buffers of only 6, so the saw blocks and the deburr cell starves almost immediately. Bottling's RD fails at t+1500s with buffers of 20 and takes far longer to propagate. Same MES, two different plant physics.
- **A precursor before the failure.** Both drift an analog upward before the stop — the mill's spindle to 74 °C, the RD's motor to 88 °C. This is what a predictive agent would be watching.
- **Micro-stops are invisible in availability.** Bottling's palletiser and machining's saw both stutter all shift. They cost OEE *performance*, not availability — which is exactly why an availability-only number flatters a struggling line.
- **A counter reset must book zero units.** Bottling's RD counter snaps to zero at t+3000s. The agent should re-baseline and book nothing, never book a phantom 100,000 units.
- **Downtime is honestly unlabelled.** Nothing on an OPC-fed line labels a stop, so the pareto reports ~100% unlabelled. That is the correct answer, and the gap M2 of FactorySemanticsMES closes with reason codes.

## Where this is going

This lab is the manual version of what becomes **M8 (plant packs + fleet)** in FactorySemantics MES. There, the registry inside `fsplant.ps1` becomes a `plant.toml` per plant, and `status` becomes a console that watches a fleet. The lesson to carry forward is the one this lab demonstrates: **a plant is data, not a code branch.**

## Files

```
fsplant.ps1              the launcher and the plant registry
bottling/init.py         seeds the product's own reference line + releases an order
machining/line.json      LineSim config -> the plant's physics
machining/tag_map.json   which tags exist, and what its State integers mean
machining/seed.py        the whole plant definition, deliberately NOT in src/
machining/out/           generated per-second CSVs (regenerate, never commit)
.data/                   databases and PID files (gitignored)
```

Regenerate the machining line after editing its config:

```powershell
python ..\..\..\LineSim\generate.py machining\line.json --out machining\out
```

**Why `machining/seed.py` is here and not in `src/fsmes/`:** seeding a specific customer's plant is plant data, not product code. The moment a second plant needs a `seed_northgate.py` shipped inside the product, the product has a tenant literal in it. `bottling/init.py` is thin precisely because its line *is* the product's reference line and legitimately ships with it.
