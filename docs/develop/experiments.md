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

# The simulated shift supervisor. On everywhere: a plant whose order book is
# worked — an order finished when the line has made its quantity, the next one
# released — is what a plant looks like. Turn it off for a plan whose subject
# is a line running past its order, so the over-run is scripted rather than an
# accident of cadence. `labs/experiments/over-run.toml` is the one that does.
[floor]
finish_orders = false
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
| `disconnect` | the OPC endpoint closes: the line runs on and the MES cannot see it |

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

### A quality excursion with a process cause

The washer runs three degrees hot; scrap climbs, and downstream the filler
underweights every bottle by nine grams — warm, wet bottles fill short — with
no alarm of its own. The failed fill-weight checks land on the filler and the
cause is at the washer, which is the whole difficulty of the thing.

Since the control chart's rules are judged when a reading is recorded
(decision [0027](../decisions/0027-an-spc-signal-raises-a-hold.md)), a run now
shows the hold being raised while the line is still running the thing that
caused it: look for `spc signal` in the log, with the rule and the NC it
raised. `labs/experiments/scrap-burst.toml` is the starter, and it runs at 2× — much
slower than the others — for a reason it states. What sets how fast readings
reach the chart is not the floor's cadence but how often the MES stores a new
value for the tag the floor is reading, and a control chart draws no limits
until it has twelve of them. At 20× half an hour of line time is nine
readings and a run in which nothing could fire however wrong the line went.
The cost is a quarter of an hour of wall clock: the other starters ask what
the MES counted, and counting is quick; this one asks about a judgement over a
series.

The distance worth measuring — from the first underweight bottle to the moment
the hold exists — is not measured yet: `quality` is a planned measurement, not
a built one. The starter exists first so the measurement has a run to be built
against.

### `disconnect` names no station, because it belongs to no machine

A `disconnect` is a fault in the **observer**, not in the plant. The replay
stops its OPC server outright for the window and starts it again afterwards;
the machines keep running, the counters keep counting, and the generated
tables are byte-for-byte what they would have been. What changes is that
nothing is there to read them, which is exactly what a switch losing power
costs a plant.

So it takes no `station` — asking which machine the network outage happened to
has no answer — and it can be scripted over a `down` on purpose, which is the
sharpest case there is: a real breakdown the MES genuinely could not see. A
fault inside a scripted outage is scored **unknown**, never missed;
`labs/experiments/lost-connection.toml` is the starter that uses it.

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
| `watched/<plant>.json` | what each screen was showing, look by look, while the hour played |
| `fleet/phases.json` | what `fsmes fleet console` said at each phase, and the fleet it watched |

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
  20x and the MES's run time is a twentieth of the line's.

    Since 2026-09-18 **the MES settles this itself** when it is told the speed:
    `fsmes fleet start --speed` puts `MES_SIM_SPEED` in the environment of
    every process of that plant, the API included, and the API restates the run
    time on the line's clock before dividing — and says so, on `/health`, in
    `/metrics` and in a bar across every screen. The report's **P MES** column
    is the same arithmetic done here, from the MES's own three numbers, which
    is what makes it a check on the MES rather than a repetition of it. Each
    station also carries its own band — both sides divide by run time, and the
    MES drains past the end of the script — and a difference inside that band
    is not called a finding.
* **The rated cycle may differ.** Performance prices units against what the
  machine could have made, and the MES uses its master data's
  `ideal_cycle_seconds` while the script uses the line's `rate_per_min`. Where
  the two disagree the row says *not like for like* instead of reporting the
  difference as a fault.

Neither side caps performance, and neither invents one. Where the MES's counted
work will not fit inside its run time it reports no performance figure at all,
and the ratio it would have been sits on `performance_ratio` — which is what
this report reads, because above 1.0 there is no reported figure to read. See
[reading OEE](../plant/reading-oee.md).

*Every number says which clock it is on.* In `scores.json` the MES's block has
no plain `performance`, `oee`, `runtime_seconds` or `downtime_seconds` at all.
It has `performance_as_reported`, `performance_ratio_as_reported` and
`performance_line_clock`,
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

### `latency` — how long after the line did the screen say it?

Every other measurement asks the plant one question at the end. This one is
built out of what the run saw **while the hour played**, because by the time a
run is over a stop that took four minutes to appear and one that appeared at
once have left the same trace.

So a run that asks for `latency` **watches**. Twice a second, or however often
the plan says, it asks two screens what they are showing:

| Screen | What it answers |
|---|---|
| `/equipment/states` | the operations screen's own feed: what each machine is doing now |
| `/line/events` | the line view's feed: station status, and the units booked since the last look |
| `/health` | that the plant is alive — and **nothing about any machine**, so how long an event took to reach it is not a question it can answer. It is asked, and the report says so rather than leaving the route out |

```toml
measure = ["downtime", "latency"]

# How often the run looks, in WALL seconds. At 20x, half a second is ten
# seconds of line time — and that is the resolution of every lag reported.
[watch]
every = 0.5
```

*The resolution is the polling interval.* A screen is only ever known to have
shown something **by** the look that saw it, so a lag smaller than one interval
of line time is quantisation and prints as *within resolution* — the same rule
as a detection lag, and the same reason. A plan whose interval is so coarse
that every lag would land inside it is refused with the arithmetic, rather than
running for six minutes to produce a page of shrugs.

*Not seen is not the same as late.* An event no look caught is *unknown* with
which of the reasons it was: nobody was looking yet, the screen did not answer,
the run ended. A zero or a maximum standing in for silence is exactly what this
lab exists to stop being reported.

*Each screen is asked separately.* Two screens showing one machine two
different states at one instant is a finding nothing else here would catch, so
they are never merged into one answer.

*Watching must not change what it is measuring.* These are HTTP requests
against the same API the run is scored through, on a machine that is also
replaying a line and running an agent. The default interval is a wall-clock
second, not the agent's publishing interval — which can be fifty milliseconds —
and the routes are the cheap ones. A run whose harness was perturbed into
falling behind has its verdict withheld anyway, which is the safety net; the
interval is what keeps it from being needed.

Beside the events, **how far behind the line's own count the line view ran**:
the machine the line ends at, against the line's own good count, look by look.
Counted in *units*, because turning a backlog into seconds needs a rate and a
line that is starved, blocked or down has not got one — the seconds would be
invented at exactly the moments worth measuring.

The raw looks are kept in `watched/<plant>.json`, whatever this version of the
measurement made of them. A version that asked the wrong question is worth
re-running against an hour somebody already paid for.

*What it cannot tell you.* Nothing configures a broker in these runs, so how
long an event took to reach the unified namespace is *unknown* and says so.

### `console` — did the fleet console count the plants the run actually had?

The console's whole promise is
[decision 0023](../decisions/0023-the-fleet-console-observes.md)'s: a plant that
did not answer is **unknown**, never healthy and never down. Nothing tested that
against plants that really start and really stop, because a console needs
several plants and a lab run has always had exactly one at a time.

Which turns out to be the fixture. The lab runs its plants **one after
another** — two replaying at speed on one laptop compete for the same cores, and
a run whose harness fell behind has its numbers withheld — so at any moment
during a several-plant experiment exactly one plant is answering and the rest
are not. The console's hardest case, arriving for free, on real ports and over
real HTTP.

A run that asks for `console` starts one, tells it each plant's address the
moment that plant comes up, and asks `/fleet.json` at each phase:

```
before any plant had started        0 plants
while bottling was running          1 plant,  1 answered, 0 unknown
after bottling had been torn down   1 plant,  0 answered, 1 unknown
while machining was running         2 plants, 1 answered, 1 unknown
after every plant had stopped       2 plants, 0 answered, 2 unknown
```

Three things about how it is run:

* **A real console on a real port**, started as `fsmes fleet console` and asked
  over HTTP — not the same code called in process. What a person opens is a page
  served by a route, and a measurement that skipped the route would not notice
  the day the route broke.
* **Its own fleet, and only this run's plants in it.** The console's root is a
  directory under the results directory with its own empty fleet file, so an
  experiment's console can never pick up the plants somebody already has running
  on this machine — which would make the totals meaningless and, worse,
  plausible.
* **It is told, never discovered.** An ephemeral plant claims its ports when it
  starts, so each is written into the console's own ownership file as *observed*
  at the moment the run first looks at it. A plant the run has not reached yet
  is not in the file, and the measurement counts it as such: not yet built and
  not answering are different facts.

Two numbers come out. **How many phases the console's count of answering plants
matched the truth**, and **how many stopped plants it read as anything other
than unknown** — which is a defect at any value above zero, and the same fault
as calling a changeover downtime, at fleet scale.

*What it cannot tell you.* Whether a console would still be right about a plant
that is up but wedged, or about twelve plants rather than two. The phases are
the ones this run happened to live through.

### `connection` — what did the MES say about the minutes it could not see?

The measurement this exists for, and the only one that **has** to be read from
during the run. By the time the hour is over the plant has reconnected and
every screen says so; the question is what they said while the link was down.

It is [decision 0030](../decisions/0030-a-lost-connection-is-unknown-time.md)
put to a plant that is really losing a real socket. Per scripted outage:

* **Did the machines read as disconnected?** Not down, not idle, and not the
  state they were in when the link died.
* **Did any single look disagree with itself?** The plant saying it cannot see
  a machine, while a screen in the same instant says what that machine is
  doing. Per look and not across the window — the looks before the agent's
  health check came round are legitimately still showing the last state it
  heard, and counting those would report the detection lag as a lie.
* **Did `/health` say how many machines were dark?** The number a monitor
  reads.
* **Did the window come back as unknown time?** Read off the OEE answer at the
  end, which is where a plant's numbers actually come from.

Two resolutions bound it, and both are printed rather than assumed. The
**polling interval** is the same one `latency` states. The **agent's own
health check** is the new one: it asks its server whether the session is alive
every `opc_health_periods` publish intervals, so an outage shorter than a
couple of those, restated on the line's clock, could not have been seen by
anything. Such a window is reported as *unknown* with the arithmetic beside
it, never as missed.

*What it cannot tell you.* Whether a real OPC server fails the way a stopped
one does. A socket that closes is one failure mode; a server that accepts
connections and answers nothing, a certificate that expires mid-session, a
switch that drops half the packets are others, and none of them is scripted
here yet.

*The direction of the difference matters.* The MES almost always records
**more** unknown time than was scripted, because it takes up to one health
check to notice the link has gone and up to one retry to find it back. That
overhang is honest — it is the MES saying it does not know, when it does not
know. **Less** unknown time than was scripted is the fault: the MES
accounting for minutes it could not see. The report names which of the two it
found rather than printing a difference the reader has to interpret.

### Not measured yet

`quality` and `agent-eval` are named in the design and are not built. A plan
that asks for one is **refused by name**: silently dropping a measurement is how
a report comes to say less than its plan asked for without anybody noticing.

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
| `labs/experiments/two-plants-two-zones.toml` | Kansas City and Northgate, different products, clocks, modules and words, one after another at 30× — and what a fleet console made of the pair |
| `labs/experiments/starved-and-blocked.toml` | twenty minutes in which nothing breaks and nothing is made: the empties run out, then the palletiser stops taking cases — and how long each took to reach a screen |
| `labs/experiments/scrap-burst.toml` | half an hour at 2× in which the washer runs hot, the filler underweights, and the control chart raises the hold |

Both run in CI on every pull request, which is what stops the instrument
rotting between the times anybody uses it. CI also proves the feedback loop
with no model anywhere: it leaves a note against the first run, checks that the
note appears in that run's report beside the booking table it is about, and
rolls both runs up into a `findings.md` that cites each of them and quotes the
note.
