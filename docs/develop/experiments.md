# Explanation — experiments in the lab

An experiment is a **plant**, a **scenario** and a **set of measurements**, run
as one command, leaving one directory somebody else can read.

```console
$ fsmes lab run labs/experiments/one-line-bad-hour.toml
```

The pieces this joins all existed first: packs describe plants, the generator
turns a line description into the data a replay obeys, the scorer reads the MES
through its own HTTP API, and the runner builds an ephemeral plant and tears it
down again. What was missing was the join — and a result that is still readable
next week.

Nothing starts by itself. A person types the command. The plant it builds is
ephemeral: its own database, its own ports on loopback, gone when the questions
have been asked, so it never disturbs a plant you already have running.

## The plan

A plan is TOML, because trying a different plant should be an edit and not a
patch. Paths are relative to the plan file, so a plan and the packs it names
travel together.

```toml
name    = "one-line-bad-hour"
packs   = ["../multiplant/bottling"]

duration = 3600          # line seconds; omitted keeps each line's own
speed    = 20            # 20 replays a scripted hour in three minutes
seed     = 42            # omitted keeps each line's own
measure  = ["booking", "downtime", "oee"]

# Vary a pack without editing it. The copy the run builds from lands in the
# results directory; the pack on disk is never touched. `modules` and `words`
# only — anything more is a different plant, and belongs in its own pack.
[overlay.machining.modules]
serialization = false

# A plant named here plays this script instead of its pack's own. The
# vocabulary is the generator's: down, drift, scrap_burst, changeover,
# counter_reset, micro_stops.
[scenario.machining]
events = [ { type = "down", station = "Mill", start = 600, end = 720 } ]

# A pack whose plant is seeded by a script rather than by master data. A pack
# may never name a script (decision 0022); a plan is not a pack.
[init.bottling]
script = "../multiplant/bottling/init.py"
```

`duration`, `seed` and `speed` are the whole of the determinism story: **the
same seed twice is the same run**, and the report states the seed it used. A
plan that shortens `duration` past a scripted event is refused rather than run
with half a script.

## What a run writes

One directory per run, under `lab-results/` unless `--results` says otherwise:

| In the directory | What it is |
|---|---|
| `plan.toml` | the plan as it was read |
| `line/<plant>.json` | the line description each plant actually played |
| `replay/<plant>/` | the data generated from it — the replay's own input |
| `packs/<plant>/` | the pack each plant was built from, overlay applied |
| `recorded/<plant>.json` | what the MES answered, view by view |
| `recorded/<plant>-scorecard.json` | the scorecard `fsmes score` writes |
| `truth.json` | what the line actually did, per station and per order |
| `scores.json` | every measurement, with the truth beside each number |
| `report.html` | one self-contained page — no network, no build step |
| `notes.md` | what a person saw that no number caught |

**The directory is the unit.** Hand somebody that folder and they have the
plan, the script, the data, the answers, the scores and the page, with nothing
to install. `fsmes lab list` shows the runs you have; `fsmes lab open <run>`
re-renders the report, which is how notes written after the run reach the page.

## The measurements, and what each cannot tell you

### `booking` — did the MES book what the line made?

Per station and in total, because a plant fixes one machine at a time and
confirms an order against the total.

*What it cannot tell you.* A replay **loops**: when the file runs out the
counters wrap to zero and the hour starts again. A run is always left playing a
little past the end, because the agent needs time to book what it has already
read — so the MES is right to book whatever the line made in that overlap. The
measurement states the overlap in line seconds and prints an **expected range**
rather than a single number.

The consequence is worth knowing before you plan a run: **the band grows with
speed.** At 20× an eight-second drain is 160 line seconds of production; at
120× it is nearly a thousand. So

* **under-booking is sharp at any speed** — the overlap can only ever add, so a
  single unit fewer than the truth is a real finding;
* **over-booking is blurred by the band** — a run at 120× cannot see a plant
  booking 16 units against an order for 15.

For that class of fault read the **over-run the MES reports itself**, which is
in the orders table and owes nothing to the band. And run slowly when the
question is booking.

*Orders are not tied to the script.* These plants' tag maps name no order tag —
a CSV replay cannot be written to — so which order a unit belongs to is the
MES's own inference, and the script's order numbers cannot be matched to it.
Both sides are printed; the per-order comparison says *unknown* and why.

### `downtime` — were the scripted stops seen, and the planned ones kept out?

The first two questions are the scorer's, read off the run's own scorecard
rather than asked a second time: was a scripted breakdown detected, and how
late, and was a changeover misclassified as downtime. Calling a planned stop
downtime destroys every availability figure a plant reports, silently, which is
why it is the first row of the section.

Added beside them: how many seconds of downtime the MES reports against how
many the line actually spent down. The MES records **wall** seconds and the
script is written in **line** seconds, so the MES's figure is multiplied by the
replay speed to compare. Both are labelled.

*What it cannot tell you.* Nothing in a run labels a stop — no downtime labels
arrive by file, no operator names one — so the unlabelled share the MES reports
has no truth beside it and says so. An event too brief to survive the sampling
interval at the speed you chose is scored *unknown*, not *missed*: that would
blame the MES for a limit of the harness. The report says the shortest event
this run could have resolved.

### `oee` — does the MES's OEE match the hour the line actually had?

Availability, performance and quality per station, against the script. Two
things stop this being a subtraction, and both are stated rather than corrected
for:

* **The windows differ.** The MES measures over the window it was watching; the
  script is exactly `duration` long. An availability difference smaller than
  that mismatch is not evidence, and the page does not mark it as one.
* **The rated cycle may differ.** Performance prices units against what the
  machine could have made, and the MES uses its master data's
  `ideal_cycle_seconds` while the script uses the line's `rate_per_min`. Where
  the two disagree the row says *not like for like* instead of reporting the
  difference as a fault.

### Not measured yet

`latency`, `console`, `quality` and `agent-eval` are named in the design and
are not built. A plan that asks for one is **refused by name**: silently
dropping a measurement is how a report comes to say less than its plan asked
for without anybody noticing.

## When a run's numbers are withheld

If the replay could not keep the speed it was asked for, or the agent booked
readings long after they arrived, the comparison is about the harness and not
about the MES. The runner already withholds the scorecard's headline numbers in
that case; the lab withholds every measurement for the same reason and prints
it at the top of the plant's section. The per-event detail is still worth
reading. The numbers are not worth trending.

The fix is a slower replay. The report prints the speed the replay actually
sustained, so the next run can ask for one it can keep.

## Notes, and handing a run to somebody else

`notes.md` is written from a template with three headings — *what I saw on the
screens*, *what was wrong*, *what I want next* — and rendered at the end of the
report, so a person's observations travel with the numbers. Write it, then:

```console
$ fsmes lab open 2026-09-14-one-line-bad-hour
```

which re-renders the page from `scores.json` and the notes. Nothing is
recomputed; the numbers are the ones the run measured.

To hand a run over, copy the directory. That is the whole procedure.

## The two starters

| Plan | What it is |
|---|---|
| `labs/experiments/one-line-bad-hour.toml` | the six-station bottling line playing the six classic faults in an hour at 20× |
| `labs/experiments/two-plants-two-zones.toml` | Kansas City and Northgate, different products, clocks, modules and words, one after another at 30× |

Both run in CI on every pull request, which is what stops the instrument
rotting between the times anybody uses it.
