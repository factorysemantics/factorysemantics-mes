# The cutlery plant: sixty million ids a day, every one observed

A disposable-cutlery manufacturer makes ten million forks, ten million
spoons and ten million knives a day. Every piece gets its own id; three
pieces become a stack with its own id; a stack and a plate become a wrap
with its own id; 240 wraps become a pallet. A vision station judges every
piece and every stack on four attributes, quality measures five dimensions
per utensil every fifteen minutes, and every pallet leaves with a
certificate of analysis: the Cpk of those dimensions and the list of every
wrap, stack, plate and piece on it. This lab is that plant, and the answer
to "can the MES handle it, and how far can it go".

## The plant

| Line                     | What it is                                                  | Rate                         |
|--------------------------|-------------------------------------------------------------|------------------------------|
| FORK1-3, SPOON1-3, KNIFE1-3 | moulding lines: Mold, Degate, Mark (the vision station)   | 2,315 pieces/min each        |
| STACK01..STACK32         | stackers: a fork, a spoon and a knife into a stack, judged  | 2 per wrapper                |
| WRAP01..WRAP16           | wrappers: a stack onto a plate, sealed, judged              | twice a stacker's rate       |
| PAL1..PAL4               | palletizers: 240 wraps to a pallet                          |                              |

83 stations. A stack takes twice as long as a wrap, which is why two
stackers feed every wrapper. Every number derives from
`PIECES_PER_TYPE_PER_DAY` in `build_config.py`:

| Stream                  | Per day    | Per second | Enters the MES as                                        |
|-------------------------|-----------:|-----------:|----------------------------------------------------------|
| forks, spoons, knives   | 30,000,000 | 347        | one OPC UA group per piece: serial, 4 attributes, a pass word |
| stacks                  | 10,000,000 | 116        | one group per stack: serial, its three members, 4 attributes |
| plates                  | 10,000,000 | 116        | named by the wrap's group                                |
| wraps                   | 10,000,000 | 116        | one group per wrap: serial, the stack and the plate, 4 attributes |
| pallets                 | 41,666     | 0.5        | one group per pallet: serial and its 240 wraps           |
| **ids**                 | **60,041,666** | **695** | 50,041,666 inspection groups a day                       |

## How an id enters

Nothing is minted from a count. The station is the source of truth: its
vision system decides whether the piece is good, stamps the serial, the
four readings, the pass word and (for a stack or a wrap) the members with
one source time, and publishes them as one OPC UA group. The agent
subscribes to every inspection tag at full rate, assembles the tags that
share a source time into one event, and writes it in bulk - the unit, its
`unit_inspections` row and its containment - with no HTTP in the path.

Two things about OPC UA shape the agent:

- It notifies a value only when it changes. A pass word of zero, an empty
  members field on a piece, an attribute the station reads the same every
  time: these arrive once and never again. A group is therefore complete
  once its serial and sequence are in and a publish cycle has passed
  (`GROUP_GRACE_S`), and the tags that did not change are filled from the
  last value the station sent - which is exactly what an unchanged value
  means.
- A group still without its serial after `GROUP_TIMEOUT_S` is taken as
  *partial* and counted, never dropped in silence.

A piece that fails any attribute is recorded with the attribute it failed
on and is never offered to a stacker. A stack claims only pieces a marker
judged good, in the order they were made (`csv_replay.Flow`); the number of
stacks containing a failed piece is a query with one right answer, zero.

## The certificate

Every fifteen minutes of line time an inspector records five
characteristics per utensil - length, width, thickness, weight and a shape
dimension - through the same API a browser uses (`fsmes run-operations`,
`inspect_every = 900`, `inspect_all = true` in the registry). A pallet's
certificate (`POST /coa/pallet/{serial}`, the `pallet_certificate` tools,
the Certificates screen) states, per characteristic, the checks on record
up to the pallet's close - those inside the window the contents were made
in, extended back to the most recent twelve where the window holds fewer,
with the span stated - the Cpk over them with sigma from the mean moving
range, whether the process was stable, and *no capability* outright when
even that is short. Under it: every wrap, the stack and the plate in each,
every piece in each stack, rendered from the containment record and issued
as an immutable document that a correction supersedes by name.

## Storage is a module

The same models and migrations run on one SQLite file (the default, what
a small plant needs) and on PostgreSQL when a registry names a
`database_url` and a `database_password_file`. Nothing in the core imports
PostgreSQL; a plant this size switches it on. On this machine PostgreSQL 18
runs as a user service on port 5433; a scored run makes its own database
(`fsmes_run_<stamp>_<speed>x`) and drops it afterwards unless `--keep`.

## Files

- `build_config.py` writes `line.json` (83 stations, the inspection block
  per station: attributes, limits, sigma); `make_tag_map.py` derives
  `tag_map.json`; `fsmes sim-generate` (or the runner) writes the CSVs
  under `out/`.
- `init.py` seeds the master data, the dimensional specifications per
  utensil, and one released order per line with its resin or plate lot
  issued, so every piece traces back to a lot.
- `run_cutlery.py --speed N [--keep]` runs a scored hour with the floor
  inside it, on PostgreSQL per the registry (`--sqlite` for a file), and
  writes `out/results/run-<stamp>-<speed>x.json`: what the stations
  published and what the agent recorded, the database's rows and bytes per
  table, the recall questions timed, the processes' CPU and memory
  (PostgreSQL's backends summed as one), the scorecard.
- `analysis/build_notebook.py` builds and executes the notebook from the
  kept evidence; `analysis/build_report.py` makes the public page from its
  figures. `analysis/viz.py` is the evidence-and-palette layer.
- `registry.toml` runs it as the standing plant on port 8040
  (`FSMES_PLANT_REGISTRY=labs/cutlery/registry.toml fsmes plant cutlery init|start|stop`).

## Measured

Eleven scored runs on the night of 2026-09-06, each an hour of line time on its own
PostgreSQL database, each speed at least twice. *Groups published* is the replay's last
periodic tally (a few seconds before the end, so it can sit either side of the agent's);
*sequence gaps* is coverage read from the record itself - every station numbers its
groups, and a gap is a published group that was never written. Results files:
`out/results/run-<stamp>-<speed>x.json`; the runs a harness fix replaced are under
`out/results/superseded/` with the reason.

| Speed | Run (UTC) | Groups published | Groups recorded | Recorded / published | Sequence gaps | Unknown members | Agent CPU | PostgreSQL CPU | Replay CPU | Agent tag lag (s) | Breakdowns seen | MB after the hour |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1x | 204224 | 1,985,840 | 1,985,840 | 100.00% | 0 | 0 | 23.2% | 5.0% | 12.8% | 0.212 | 15/15 | 1,254 |
| 1x | 214300 | 1,981,758 | 1,982,340 | 100.03% | 0 | 0 | 22.9% | 5.5% | 12.8% | 0.529 | withheld | 1,248 |
| 1x | 233823 | 1,982,899 | 1,981,758 | 99.94% | 0 | 0 | 22.5% | 5.5% | 12.5% | 0.205 | 15/15 | 1,237 |
| 3x | 003928 | 2,007,806 | 2,001,439 | 99.68% | 0 | 0 | 56.4% | 15.7% | 37.6% | 0.377 | 15/15 | 1,177 |
| 3x | 010005 | 1,999,700 | 2,003,187 | 100.17% | 0 | 0 | 56.5% | 15.7% | 37.6% | 0.321 | 15/15 | 1,165 |
| 5x | 020033 | 2,024,591 | 2,021,439 | 99.84% | 0 | 0 | 72.9% | 24.8% | 62.4% | 1.738 | withheld | 1,112 |
| 5x | 021309 | 2,018,233 | 2,006,580 | 99.42% | 0 | 0 | 72.9% | 25.5% | 62.3% | 2.39 | withheld | 1,106 |
| 8x | 022544 | 2,004,923 | 606,833 | 30.27% | 0 | 0 | 64.3% | 19.1% | 93.9% | 3.06 | withheld | 333 |
| 8x | 023347 | 1,999,141 | 319,476 | 15.98% | 0 | 0 | 46.6% | 15.7% | 93.9% | 2.402 | withheld | 190 |
| 10x | 014656 | 1,603,803 | 773,646 | 48.24% | 0 | 1932 | 71.8% | 22.8% | 92.9% | 179.269 | withheld | 593 |
| 10x | 015329 | 1,636,425 | 787,364 | 48.11% | 0 | 1278 | 68.8% | 22.1% | 93.0% | 145.177 | withheld | 431 |

- **At the real rate** (three runs): every group recorded, no sequence gap, no partial
  group, no unknown member; breakdowns 15 of 15 in the first and third - the second's
  verdict was withheld by the scorer as it then was, over a 529 ms lag 58 s into the
  run, the agent's subscription burst, which the warm-up rule now states beside the
  verdict instead; the agent at 23% of one core,
  PostgreSQL at 5%, the replay at 13%, the API at 1%. 1,254 MB an hour, 30 GB a day.
- **At 3x** the same, at 56% and 16%.
- **At 5x** every inspection group is still recorded exactly, but the agent's counter
  and state readings lag about two seconds behind their half-second sample, so the
  breakdown verdict is withheld: one agent process bends here.
- **At 8x and 10x** the Python OPC UA replay is itself past its limit (it sustains
  about 8x) and the agent's single ingest thread writes about 3,000 groups a second
  while 4,600 to 5,800 arrive; the backlog grows, the verdicts are withheld, and the
  numbers say so.
- **The fit** (`out/results/scaling.json`): agent CPU against groups per second through
  the origin over the runs that kept up gives one agent process a ceiling of about
  3,490 groups a second, 6.0 times the
  customer's rate. The next step past it is a second agent process over a partition
  of the stations, not a bigger box.
- **The recall questions** against an hour's database: a piece in 2 ms, a pallet's
  contents (1,440 units) in 9 ms, its trace-back in 21 ms, the certificate's data in
  40 ms, a cascading quarantine in 30-60 ms, where a plate lot went in 175 ms, and
  where a resin lot went - a third of everything made, 131,584 pieces in 3,671
  packages - in 1.5 s. The last is the one that grows with the plant.

## What the simulator cannot say

- Its stations' vision readings are drawn around a nominal with a declared
  sigma; a real station's distribution has the shape the process gives it.
  The method - limits, a pass word, Cpk over a window - does not depend on
  the shape.
- The OPC UA server is a Python replay. It publishes what the plant would at
  1x; at higher speeds the emitted count against the expected count says
  how much of the load it could produce, and only what was published can be
  scored.
- The first cutlery plant (three lines, serials minted through
  `POST /trace/units/batch` from what the counters booked) is superseded by
  this one; its results are kept under `out/results/v1-minted/`.
