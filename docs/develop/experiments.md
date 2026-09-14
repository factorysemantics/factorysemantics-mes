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
# vocabulary is closed — see below.
[scenario.machining]
events = [ { type = "down", station = "Mill", start = 600, end = 720 } ]

# A pack whose plant is seeded by a script rather than by master data — the
# scale labs, whose master data is generated rather than written. A pack may
# never name a script (decision 0022); a plan is not a pack. A plant with
# neither is refused before anything is started.
[init.megafactory]
script = "../megafactory/init.py"

# The on-screen design panel, on for every lab plant so a note can be left
# while you are looking at the thing you want to complain about. Claude is off
# unless a plan asks for it: taking a note is work the on-device model does for
# nothing, and a run left going while you make coffee should not bill an API.
[feedback]
chat   = true
claude = false
```

`duration`, `seed` and `speed` are the whole of the determinism story: **the
same seed twice is the same run**, and the report states the seed it used. A
plan that shortens `duration` past a scripted event is refused rather than run
with half a script.

## What a scenario can script

The vocabulary is closed, and a plan that names anything else is refused
before a directory is made, with the list printed:

| Event | What the line does |
|---|---|
| `down` | the machine has broken: it makes nothing and reports DOWN |
| `changeover` | the whole line is changing over: planned, and never downtime |
| `micro_stops` | short random stops, pre-rolled from the seed so a run repeats |
| `drift` | an analog ramps from its base to `to` across the window |
| `scrap_burst` | the station's scrap rate is `scrap_pct` for the window |
| `counter_reset` | the station's counters go back to zero at `at` |
| `starve` | nothing arrives: the machine is willing and has nothing to work on |
| `block` | nowhere to put it: the machine is willing and downstream is full |

`starve` and `block` script the **cause**, not the symptom, and the rest of
the line follows on its own: starve the first station and every station after
it starves in turn as its buffer drains, the way it would on the floor. So the
run's total starved seconds are mostly knock-on, and the table in the report
shows the scripted windows separately from the line's own total.

### Why these two are worth a measurement of their own

A machine with nothing to work on, or nowhere to put what it has made, is
making nothing and **there is nothing wrong with it**. Booking those minutes as
downtime invents a breakdown that never happened and reports an availability
figure that is wrong in the direction nobody checks — it looks *worse*, so it
is believed. It is the changeover fault from the other side, and
`labs/experiments/starved-and-blocked.toml` is the starter that looks for it.

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
| `feedback/conversations.jsonl` | the run's copy of what was said at its screens |

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

*Orders, and how far past them the line ran.* The line publishes the order it
is running, and the tag map's `line` block says where and how that value names
an order in this MES ([the tag map's side of it](../operate/opc-readonly.md#if-the-line-publishes-the-order-it-is-running)).
With that read rather than guessed, each order the line published gets a row:
what the line made under it, what the MES booked against it, what the order was
for, and how far past it each side says the line ran.

Three things about that row are worth knowing before you read one:

* **The ordered quantity is the MES's own.** An order is for what the plant was
  told it was for; a second copy of that number in the line description would
  be a second place for it to be wrong, and a disagreement between the two
  would read as a fault in a plant that had none. What is compared is
  *production*.
* **The join is read, not asserted.** It comes out of the plant's own wiring
  file. A pack whose map has no `line` block leaves every order *unknown* and
  the report says which block would end that, rather than quietly matching on
  a resemblance between two numbers.
* **The truth side is a range and the MES's is not.** The replay loops, so
  units made in the overlap are production the MES was right to book. The
  over-run the MES reports about itself owes the band nothing — which is what
  makes it the sharp reading for the sixteen-against-fifteen class of fault.

An order the line published that the MES never held is *unknown* with the
reason, not a difference. An order the MES holds that the line never published
is listed and is not one either: a plant holds orders that are not running.

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

*Lags.* The shortest event a run could notice at all is one sampling interval
of line time — thirty line seconds at 60×. A detection lag smaller than that is
quantisation, so it is printed as *within resolution*, with the resolution
beside it, rather than as a signed number somebody could put in a trend. A
sweep once reported a breakdown detected one second *before* it was scripted.

*Starved and blocked.* Every scripted `starve` and `block` window is checked
against what the MES recorded **for that machine**: unlike a changeover, which
is the whole line stopping together, having nothing to work on is a fact about
one station, so a neighbour that really did break in the same minutes is not
counted against it. A window the MES was not watching scores *unknown*, never a
pass. Beside the table, the line's own total seconds starved and blocked —
most of which is the knock-on nobody scripted.

*What it cannot tell you.* Nothing in a run labels a stop — no downtime labels
arrive by file, no operator names one — so the unlabelled share the MES reports
has no truth beside it and says so. An event too brief to survive the sampling
interval at the speed you chose is scored *unknown*, not *missed*: that would
blame the MES for a limit of the harness. The report says the shortest event
this run could have resolved.

### `oee` — does the MES's OEE match the hour the line actually had?

Availability, performance and quality per station, against the script. Three
things stop this being a subtraction, and all three are stated rather than
quietly corrected for:

* **The windows differ.** The MES measures over the window it was watching; the
  script is exactly `duration` long. An availability difference smaller than
  that mismatch is not evidence, and the page does not mark it as one.
* **The clocks differ, and only performance notices.** Availability and quality
  are each a ratio of two things measured the same way, so the replay speed
  cancels out of both. Performance does not cancel: its numerator is priced in
  the line's own seconds — the rated cycle somebody wrote down — and its
  denominator is run time the MES measured on the wall clock. Replay an hour at
  20x and the MES's run time is a twentieth of the line's, so the figure it
  reports is twenty times the line's. The report's **P MES** column is
  therefore the MES's own performance put back on the line's clock: its rating,
  its counts, its run time multiplied by the replay speed. At speed 1 it is the
  reported figure unchanged. Each station also carries its own band — both
  sides divide by run time, and the MES drains past the end of the script — and
  a difference inside that band is not called a finding.
* **The rated cycle may differ.** Performance prices units against what the
  machine could have made, and the MES uses its master data's
  `ideal_cycle_seconds` while the script uses the line's `rate_per_min`. Where
  the two disagree the row says *not like for like* instead of reporting the
  difference as a fault.

Neither side caps performance. A figure above 1.0 means the machine beat the
cycle it was rated at, which is a finding about the rating rather than a score
above physics — see [reading OEE](../plant/reading-oee.md).

*Every number says which clock it is on.* In `scores.json` the MES's block has
no plain `performance`, `oee`, `runtime_seconds` or `downtime_seconds` at all.
It has `performance_as_reported` and `performance_line_clock`,
`runtime_wall_seconds` and `runtime_line_seconds`, and so on, and the one the
difference was computed from is the line-clock one. Availability and quality
keep their plain names, because they are the two the replay speed cancels out
of. A file that kept the wall-clock figure under the plain name while the
comparison beside it used the line-clock one was one object answering two ways,
and nobody reading it later could tell which number the difference came from.

*When the MES's own two numbers do not agree.* Separately from any comparison
with the script, a station can report more units than its own recorded run time
holds at the cycle it rates the machine at. Where the script prices the machine
the same way and fitted its units inside its running seconds, neither the
replay speed nor the master data explains that, and the report names the
station under **Counts and run time that do not agree**. It is the one row on
the page that survives any argument about the truth — and it still does not say
which of the two numbers is the wrong one.

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

## What you said while watching it

Reviewing a product means noticing something on a screen. A note typed into a
terminal an hour later is a memory; a note typed into the screen while the line
is stopped is evidence. So the design panel is **on for every lab plant**, and
the run ties what you say to itself.

Four tags make a remark into evidence:

| Tag | Where it comes from |
|---|---|
| `lab_run` | the plant's own environment, set by `fsmes lab run` |
| `lab_plant` | the same |
| `screen` | the panel, which knows which screen you are on |
| the moment | the run, which knows its own `t0` and the script it played |

The run id is deliberately **not** taken from the browser. A run id that
arrived in a request body would let any page claim any run, and the whole value
of a tagged note is that the tag is not a guess. A body that disagrees with the
plant's environment is filed against no run rather than against the one it
claims.

The moment is resolved by the run, for the same reason: a plant does not know
when the replay's first tick was — that instant is decided by the runner, which
waits for the log line rather than guessing it. So every note carries the
**line second** it was made at and the scripted events live at that second:

> — SCOTT · `2026-09-14-one-line-bad-hour` · bottling · line second 3,100,
> during down at LD (3,000 to 3,180 s)

A note made before the first tick says so rather than being rounded to second
zero, and one made after the script ran out says that: a remark about a plant
that had stopped is a different remark.

### From the terminal

For a run watched without a browser:

```console
$ fsmes lab note 2026-09-14-one-line-bad-hour \
      "the orders list does not say how many there are" \
      --screen /dashboard/orders --plant bottling
```

It goes into the same store with the same tags. A note made while the run is
going is picked up when the run exports its feedback at the end; one made
afterwards is picked up by `fsmes lab open`, which re-exports before it
re-renders.

### Where it lands

At the end of the run the conversations tagged to it are **copied** into
`feedback/conversations.jsonl` and rendered in `report.html` beside the section
for the screen they name — a note about the work orders screen sits under the
booking table, not in an appendix. Notes about a screen with no measurement
behind it yet are still printed, in the plant's own block.

The store itself (`~/.local/share/fsmes/design.db`, or wherever
`MES_DESIGN_STORE` points) is never moved, emptied or written into a plant's
database. It is one person's notes going back weeks; a run takes a copy of the
part that belongs to it.

Everything is printed **verbatim**, with who said it and when. Nothing here
summarises or scores: a remark that turns out to be wrong is still what
somebody said while they were looking at the screen.

## Reading several runs together

One run is an instrument reading. A finding is a thing that happened twice.

```console
$ fsmes lab review --out findings.md
$ fsmes lab review 2026-09-13-one-line-bad-hour 2026-09-14-one-line-bad-hour --out findings.md
```

It clusters three things across the runs you name — every note left at a
screen, every row where the MES and the script differed, and every question a
run could not answer — by the screen and the measurement they belong to. Each
cluster names its runs, its plants, its stations and its numbers, and quotes
the notes verbatim.

Three rules:

* **It never says which side is right.** A run states the truth the generator
  obeyed and the answer the MES gave; putting several of those beside each
  other adds no authority to either. A test forbids the words.
* **Every line is cited.** A roll-up nobody can trace back is a rumour.
* **It works with no model at all.** Clustering is by screen and measurement,
  which are facts in the files, so CI runs it and so can a machine with no
  Ollama. When the local model is there it is asked for one thing: a short
  heading for a cluster somebody has to skim. `--no-model` is the plain
  version; a heading that comes back as a verdict is dropped for the plain one.

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

## The starters

| Plan | What it is |
|---|---|
| `labs/experiments/one-line-bad-hour.toml` | the six-station bottling line playing the six classic faults in an hour at 20× |
| `labs/experiments/two-plants-two-zones.toml` | Kansas City and Northgate, different products, clocks, modules and words, one after another at 30× |
| `labs/experiments/starved-and-blocked.toml` | twenty minutes in which nothing breaks and nothing is made: the empties run out, then the palletiser stops taking cases |

Both run in CI on every pull request, which is what stops the instrument
rotting between the times anybody uses it. CI also proves the feedback loop
with no model anywhere: it leaves a note against the first run, checks that the
note appears in that run's report beside the booking table it is about, and
rolls both runs up into a `findings.md` that cites each of them and quotes the
note.
