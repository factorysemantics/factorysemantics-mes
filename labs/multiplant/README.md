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

## Bottling plays a shift; machining plays an hour

Bottling's line data is **eight hours** long — `bottling/line.json`, seed
20261107 — and machining's is one hour. That difference is deliberate and it
is worth knowing before you read any chart on either.

A CSV replay wraps at the end of the file. A plant replaying an hour therefore
puts the same changeover at the same minute of every hour, the same stops at
the same second, and fill-height sample means that cycle through the same four
values all night. Over a window longer than the file the plant is *perfectly
periodic*, which is the one thing no real plant is, and a window of a plant is
what the analysis screens, the control chart and the state timeline all show.
Bottling's shift has each planted story happening once, at a time that does
not divide the hour, with ordinary running in between — so a chart of it reads
like a shift.

`labs/kepsim/line.json`, the reference hour, is **unchanged and still the
reference**: it is what CI replays, what `fsmes score` is measured against and
what the published honesty numbers are quoted from. Bottling's shift is the
same six stations, rates, buffers and physics with a different duration, a
different seed, the events placed once each, the four-link chain below, and
one extra switch. Machining keeps its hour because nothing about it needs a
shift yet.

Regenerate either after editing its config:

```bash
fsmes sim-generate labs/multiplant/bottling/line.json     # ~9.6 MB, 8 h
fsmes sim-generate labs/multiplant/machining/line.json
```

Each writes `out/` beside its own `line.json`, which is where the product
looks for a plant's ground truth (`fsmes score`, `fsmes sweep`, `fsmes lab`
all read `line.json` from the replay directory's parent). `out/` is generated
and never committed.

### `state_filter_s` — why a bottleneck is not a stop

Bottling's shift sets `state_filter_s: 20`. A station whose downstream buffer
is pinned at capacity blocks for two seconds, runs for three, blocks for one,
all shift — and with a one-second tick the generator published every one of
them. Measured on the reference hour: **124 emergent idle intervals across the
six machines, most of them one to seventeen seconds long.** The MES recorded
each as an idle interval nobody could name, which is how a quiet sample's
context panel came to list "25 other stops" around it.

They are also not how a machine reports itself. A state tag has a filter timer
on it, because a drive waiting a moment for the conveyor ahead to move has not
stopped. `state_filter_s` is that timer: an idle the *buffers* produced is
published only once it has held that long. Scripted stops are never filtered —
the loader's 12-second jam and the palletiser's 8-to-18-second micro-stops are
the stories, and they are all still there. The price, stated in the
generator's own comment and in the generated `scenario.md`: a genuinely long
block is published from the filter's end, so it loses its first twenty seconds
to *performance* rather than availability — which is where a full buffer
belongs anyway.

### Replay speed changes what a *sample* is, and nothing else

A sample of five pieces is five **stored** readings, and this product stores an
analog every `opc_history_ratio` publish intervals with `opc_min_history_ms` as
the floor under it — here 500 ms x 10, so **one reading every five seconds of
wall clock, whatever the replay speed**
(`integrations/opc/agent.py` `history_interval_ms`). Five bottles therefore
span 25 seconds of wall clock, which is 25 x speed line-seconds: 25 line
seconds at 1x, five line minutes at 12x, twenty-five line minutes at 60x. And
the floor will not post a sample until it has five readings it has not measured
before, so above about 36x the 900-line-second due time arrives with only three
of them and the sample slips to the next one — 1,800 line seconds apart instead
of 900.

Neither is a fault. A historian samples on its own clock, and a floor that
measured four bottles and called it five would be inventing one. But it means a
compressed replay dilutes any story narrower than a sample's own span, and the
same is true of the "rest of the line" block on a sample's panel, whose window
is ten wall minutes — two line hours at 12x, ten line minutes at 1x.

Measured on a scratch plant on 2026-10-08: at 60x the half-hour cold window
held **one** sample mean, drawn from bottles spread over 25 line minutes, and
it read 141.18 mm against neighbours near 142 — low, but diluted, and no rule
fired. At 12x it held **two**, each drawn from five line minutes: 140.58 mm and
141.12 mm against a centre line of 142.05, and rule 1 fired on both. **The
fleet runs this file at 1x**, which is the speed it is written for; a replay
compressed for a quick look wants 12x or less if the fill-height chart is the
thing being looked at.

### The chain — four links, four record books, and the answer key

The cold-product story used to start in the middle: the fill height ran low
and the only thing behind it was a tag that stepped down. Since 2026-10-08 the
shift plants the whole chain, so the low chart has a reason, and the reason has
a reason:

> The 4:40 changeover brings the second order and the filler is run **faster**
> for it → the chiller it loads has a **condenser clean overdue in the
> backlog that nobody starts** → the chilled water leaving it **climbs** from
> 6:20, the chiller's control overshoots, and the product **ramps two degrees
> cold** over twenty minutes → the bottles filled in that half hour come out
> **a millimetre and a half low**, a control rule fires and a finding opens.

Each link is a real record in a different one of this plant's books, which is
the point: a person walking it back changes screens three times, and so will an
assistant. And beside the shift there is an **answer key** — `_chain` in
`bottling/line.json` — naming the four links, their windows in line seconds and
the record each one leaves. It is read by a person and by
`fsmes.sim.score.score_chain`, which marks a replay against it; `fsmes score
bottling` prints the result as its *chain* section.

**Where each link shows up in the records**

| # | link | line time | the record | where to look |
|---|------|-----------|------------|---------------|
| 1 | production — the changeover brings order 4712 and the filler runs it faster | 4:40 → end of shift | a labelled `setup` interval on FILL01, then a cycle time ~35 ms shorter for the rest of the shift | State Timeline; `/analysis/timeline?equipment=FILL01`; trend `FILL01.CycleTimeMs` |
| 2 | maintenance — the chiller condenser clean is overdue and nobody starts it | all shift (raised in the first minutes) | a preventive order against `PM-FILL-CHILLER`, still at `due` eight hours later, with no start on it | Maintenance page; `/maintenance/orders?equipment=FILL01&status=due` |
| 3 | tags — the chiller outlet climbs, its control overshoots, the product ramps cold | 6:20 → end (outlet); 6:40 → 7:00 ramp, cold to 7:30 (product) | `FILL01.ChillerOutletTemp` leaving 3.2 °C and climbing; `FILL01.ProductTemp` walking 8.4 → 6.4 °C; AlarmWord bit 1 set on FILL01 while either drifts | trend graph for FILL01; `/analysis/tag/FILL01?tag=ChillerOutletTemp` and `?tag=ProductTemp` |
| 4 | quality — the fill-height means run low, a rule fires, a finding opens | 7:00 → 7:30 | two or three X̄ points near 140.5 mm, below the lower two-sigma line at 140.84, range chart flat; one non-conformance | SPC panel; `/quality/spc/FG-BOTTLE/fill_height`; `/quality/nonconformances` |

**Rule 2 fires first, not rule 1.** A millimetre and a half is 2.9 sigma of a
sample mean on this chart, which is inside the three-sigma line by a whisker,
so the rule that catches the dip reliably is *two of three points beyond two
sigma*; rule 1 catches only the lowest point, and only on a replay slow enough
to put several samples in the window. The key says so in `_first_rule`, because
a reader — and an agent — assumes a low mean must be rule 1.

**What the key deliberately does not claim** is in `_chain`
`_what_this_key_does_not_claim`, and the honest one to know is link 1: the
order *number* changing at 4:40 is not provable from the replay. The MES's
order book advances on the simulated floor's own clock (the planner releases,
the supervisor books), while the CSV's `OrderId` column is written *to* the
machine, so the key credits link 1 only with the labelled setup interval and
the faster cycle time — both of which are the line's own records.

**The marking is honest in three directions, not two.** Each link scores
*recorded*, *not recorded*, or `null` — nobody looked — and the third is the
one that matters: a tag the MES holds nothing for, a window with no sample in
it, and a list that came back one page of are all "no answer", never a zero.
`src/fsmes/sim/score.py` and
`tests/test_the_bottling_shift_leaves_a_chain_a_person_can_walk_back.py`.

### What an eight-hour shift actually looked like

One pass at 12x, 2026-10-08, read in a browser rather than out of the database:

- **State timeline, all six machines:** solid running, one red stripe on RD01
  with an orange stripe either side of it stepping down the line (the breakdown
  and its ripple), one purple stripe across all six (the changeover), and
  PAL01's own micro-stop hatching throughout. Nothing repeats.
- **Downtime for the pass: 16 s wall = 192 line seconds, 0% unlabelled** —
  which is the RD breakdown's 180 s plus the loader's 12 s jam, exactly, with
  nothing else in it. The pareto has two reasons and says so: "2 of 2 reasons
  across 6 machines".
- **Non-running intervals recorded over the whole shift:** five `idle`
  stretches of 11–14 wall seconds, one each on FILL01, LD01, QI01 and WASH01
  plus LD01's named jam — the breakdown's ripple — and 238 on PAL01, which are
  its scripted micro-stops and are meant to be there. Before `state_filter_s`
  every machine looked like PAL01 does.
- **Fill height, 32 samples:** centre 142.07 mm, R-bar 1.55 mm, limits 141.17
  to 142.96. Two high (144.08, 143.74) after the changeover, two low (140.58,
  141.12) in the cold window, one gap where no sample was taken at all —
  FillWeight had stopped arriving and the floor would not invent five bottles.
- **FILL01's ProductTemp trend over the shift:** flat at 8.4 degC with one
  clean step down to 6.4 degC and back, thirty line minutes wide.

That pass was read **before the chain was planted later the same day**, and two
of its lines have moved since: ProductTemp now *ramps* down over the twenty
minutes before that window instead of stepping, and FILL01 publishes a
process-drift alarm (AlarmWord bit 1) through the ramp and through the
chiller's own climb. The rest of the pass still holds; nothing in the chain
touched the breakdown, the changeover, the micro-stops or the state filter.

## What to look for once the two are running

- **A planned stop must not count as downtime.** Both plants change over
  (bottling once at t+16800s, machining at t+1500s). If either plant's
  availability drops during its changeover, the state mapping is wrong. This
  is the single mapping decision with real consequences — calling a planned
  stop downtime silently destroys every availability figure you have.
- **A breakdown ripples.** Machining's mill fails at t+2700s with buffers of
  only 6, so the saw blocks and the deburr cell starves almost immediately.
  Bottling's RD fails for three minutes at t+8220s with buffers of 20 and
  takes far longer to propagate. Same MES, two different plant physics. On
  bottling, with `state_filter_s` set, that ripple is now the **only**
  unlabelled idle on the whole shift: six intervals, all of them between
  t+8241 and t+8403, starting ten seconds apart down the line — LD blocked
  159s, then Washer 152s, QI 143s, Refill 133s, Palletiser 7s and 87s. That
  is `starved` and `blocked` folded into `idle` by the tag map, which the
  floor correctly refuses to guess between.
- **A tag moves a quality measurement.** Bottling's product runs 2 °C cold
  from t+25200s to t+27000s and the fill-height sample means in that window
  sit about 1.5 mm *low*, while the sample ranges stay flat — the process
  moved, its spread did not. The filler is not silent about it any more:
  since the chain was planted, the twenty-minute ramp into that window and the
  chiller's own climb both set AlarmWord bit 1 on FILL01 — "a process value is
  drifting" — and nothing is published for the settled half hour itself, which
  is what a drift alarm does. Click one of those low samples and
  `FILL01.ProductTemp` is down at 6.4 °C in the station's tags block; the
  trend graph for that tag shows the dip lining up with the dip in height. The
  arithmetic is in `bottling/line.json` (`_how_low_is_low`), re-measured
  against the generated CSVs after the chain went in: 500.04 g → 494.25 g is
  −1.51 mm (142.01 mm settled against 140.50 mm cold), which is 2.9 sigma of a
  sample mean, so rule 2 fires and rule 1 catches the lowest point. Where the
  cold came from is the chain — see *The chain* above.
- **A precursor before the failure.** Both drift an analog upward before the
  stop — the mill's spindle to 74 °C, the RD's motor to 88 °C. This is what a
  predictive agent would be watching.
- **Micro-stops are invisible in availability.** Bottling's palletiser and
  machining's saw both stutter all shift. They cost OEE *performance*, not
  availability — which is exactly why an availability-only number flatters a
  struggling line.
- **A counter reset must book zero units.** Bottling's RD counter snaps to
  zero once a shift, at t+23400s. The agent should re-baseline and book nothing, never book a
  phantom 100,000 units.
- **Downtime is honestly unlabelled.** Nothing on an OPC-fed line labels a
  stop, so the pareto reports ~100% unlabelled. That is the correct answer.

## Files

```
fleet.toml                  the list of packs, and where the data goes
bottling/plant.toml         the pack
bottling/tag_map.json       which tags exist, and what its State integers mean
bottling/masterdata/        this plant's whole definition, as data
bottling/line.json          this plant's physics, as an eight-hour shift
bottling/out/               generated per-second CSVs (regenerate, never commit)
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
builds a bill of materials, six maintenance plans and two shift patterns as
well as the equipment and the routing. Extending the format was the honest
half of the trade; the alternative was a bottling plant that quietly lost its
BOM and its calendar the day it moved into a pack.

Regenerate either line after editing its config — see *Bottling plays a
shift* above for the command, and `fsmes sim-generate --help` for the rest.
