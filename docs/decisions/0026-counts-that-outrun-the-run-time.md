# 0026 — Counted work that will not fit inside the run time names the disagreement, not a culprit

- **Status:** accepted, amended 2026-09-18 (below)
- **Date:** 2026-09-14, amended 2026-09-18
- **Deciders:** maintainer
- **Amends:** [0025](0025-performance-is-measured-not-capped.md)

!!! warning "Amended on 2026-09-18 — the figure is withheld"

    Everything below still holds about *what the MES knows* and *what it
    says*. What changed four days later is what it prints: where the counted
    work will not fit inside the run time there is now **no performance
    figure and no OEE**, only the sentence and the numbers behind it. The
    ratio is kept and published, under a name that says what it measures.
    See [the amendment](#the-amendment-2026-09-18-the-ratio-is-published-the-figure-is-not)
    at the end of this record.

## Context

[0025](0025-performance-is-measured-not-capped.md) took the `min(1.0, ...)`
cap off OEE performance, earlier on 2026-09-14, and it was right to. It also
decided what a figure above 1.0 *means*:

> **Above 1.0** — the machine ran faster than its rating. Reported as it is,
> with the note that says what it means: *the rating is slower than the
> machine*, a master-data finding, not a score above physics.

Later the same day the lab ran two plants and caught that sentence out.
Northgate Machining's Deburr: the MES reported **826 units** and **1,878
line-seconds** of run time for a machine its own master data rates at **2.4 s
a unit** — 1,982 seconds of work recorded inside 1,878 seconds of running, so
performance read 1.056 and the MES announced the rating was slow.

The script that generated the data rated the machine at the same 2.4 s and
fitted its own **831 units inside its own 1,996 running seconds**. The rating
was right to within a tenth of a percent. What was wrong was the MES's run
time, and there is a reason it was: Deburr changes state every 2.7 seconds on
average — 1,112 changes in 3,000 seconds, a median running burst of 3 s — and
at the replay speed the agent saw the machine about every 6 seconds. It could
not resolve the intervals, so it recorded less running than there was.

One further fact came out of reading the code: `performance > 1.0` and "the
counted work will not fit inside the run time" are the **same inequality**,
`cycle × units > runtime`. So the note on that branch was never describing a
measurement of the master data. It was naming one of at least three causes and
calling it the answer.

## Options considered

| Option | For | Against |
|---|---|---|
| Leave 0025's note as it is | No churn on a decision one day old | It states a cause the MES cannot know, and the lab has already shown it stating it wrongly. A confident wrong explanation is worse than no explanation |
| Take the units counted outside run time out of performance | The arithmetic reconciles; performance never passes 1.0 again | Invents a claim about the plant: the MES knows only *where it counted* those units, not where they were *made*. It would also move a real plant's manual confirmations, which carry no observation at all |
| Report *unknown* above 1.0 | Honest that the figure cannot be trusted | Already rejected in 0025, for the same reason: it throws away a real measurement |
| **Name the disagreement and measure the one candidate cause the MES holds evidence for (chosen)** | Nothing is moved, nothing is capped, and the screen stops asserting a cause; the one measurable candidate becomes a number a plant can act on | The note is longer, and a plant reading "one of these is wrong" has to do the next step itself |

## Decision

A performance figure above 1.0 is reported exactly as 0025 decided — uncapped,
with the true number, and the OEE above 1.0 that follows. What changes is the
sentence beside it. It states the disagreement and both of its numbers, and
says the MES cannot tell which of the two is wrong: the rating may be slower
than the machine, or the run time may be short of what the machine really ran.

Beside it the MES reports one measured quantity, `counted_outside_run_time` —
how many units, good and scrap together, it booked at an instant its own state
history did not have the machine running. That is a fact about this MES's two
records of the same machine, and it is the only one of the three candidate
causes the MES holds evidence for. **Nothing is moved and nothing is dropped
because of it** (house rule 1): the units stay in `good_qty`, in `scrap_qty`,
in quality, and in the performance numerator. They are named, and that is all.

Both series stay on **one clock** — the instant the MES booked the reading —
and that is what makes the new quantity mean anything: a booking and a state
interval can be compared because the same clock stamped them. See *what was
tried and rejected*, below, for why they are not on the OPC server's clock.

## What was tried and rejected: the server's own clock

The obvious next move was to stamp both series with the server's
`SourceTimestamp` instead: the agent already reads it for tag history, and a
batch of readings is a queue, so booking them all at the instant the batch
reached the database looks like exactly the kind of smearing that would cost
run time. It was built, and then measured against the same two-plant plan on
the same machine within the hour, `public/main` against the branch:

| station | availability before | after | the line's own |
|---|---|---|---|
| Deburr | 0.617 | 0.758 | **0.606** |
| Saw | 0.714 | 0.809 | **0.708** |
| Mill | 0.872 | 0.877 | **0.858** |
| all six bottling stations | — | within 0.005 of before | — |

It made the two numbers it was meant to fix **worse**, and the reason is
worth keeping. The subscription holds one value per item per publish, so what
arrives is a *sample*, and its `SourceTimestamp` says when the value last
changed — which for a machine flipping faster than the publish interval is an
instant well before the sample was taken. Closing the previous interval there
hands every flip nobody saw to whichever state the sample found, and the
staleness is proportional to the state's own duration: the long state is
stretched, the short one is squeezed. Deburr runs 3 s and starves 1 s, so
running grew. Booking at the flush instant splits the unobserved flips at the
sample boundaries instead, which on this data is much closer to unbiased —
not because it is right, but because its error does not lean.

So the MES keeps one clock, and the honest statement about run time under a
coarse subscription stays what it is: an estimate whose resolution is the
publish interval.

One thing that experiment did settle, and it matters for reading
`counted_outside_run_time`: on the server's clock the quantity nearly
vanished — Deburr went from 163 units to 102, Saw from 105 to 12, the six
bottling stations from 33–126 to 0–4. So a large part of what it counts is
the agent's own batch putting its state changes ahead of its counts, not the
plant. That is not a reason to suppress it. It is the number saying *my two
records of this machine do not line up*, which is exactly the question a
reader who sees performance over 100 % needs answered, and it points at the
right place: what the MES saw, not what somebody typed.

## Consequences

**Easier.** A plant that sees performance over 100 % is told what the MES
actually observed rather than what it guessed, and the one candidate it can
check — production booked while the machine was not running — is a number on
the same screen rather than a query somebody has to think of.

**Harder.** The note is a sentence and a half rather than a phrase. The row on
the analysis screen reads *counted work outruns the run time* rather than
*rating slower than the machine*; a plant that had learned the old wording has
to learn the new one, which is the price of it having been wrong.

**To revisit.** The residual on Deburr is a sampling limit, not a bug: no
timestamp fixes intervals nobody observed, as the rejected option above
measured. If a plant needs run time finer than its subscription, that is a
conversation about publish intervals and deadbands at the machine, and it
should produce its own decision record rather than a correction factor in
here. What the MES could do, and does not yet, is **say what its run-time
resolution is** — the publish interval it subscribed at — beside the number,
so a reader can tell a real 3 % from quantisation. That is the next thing to
write down.

## House rules touched

- **1 — never invent production.** `counted_outside_run_time` names units; it
  never moves or drops them.
- **2 — unknown is a valid answer; zero is not.** Extended once more: *which
  of my two numbers is wrong* is also allowed to be unknown, said out loud,
  rather than guessed.
- **6 — charts get checked by looking at them.** The row was re-rendered and
  looked at after the wording changed, and the test that pins it now also
  pins that the old accusation is gone. The rejected option above was
  measured the same way, which is the only reason it is in this file rather
  than in the product.

## The amendment, 2026-09-18: the ratio is published, the figure is not

### What happened

A bottling plant stood up overnight in the lab, replaying a recorded line at
ten times real time. In the morning its dashboard showed **OEE 980 %** and
**performance 881 %**, and one machine's page showed OEE around 800 %. The
maintainer's reading of it was one line: *"why are oee and performance
numbers at 980% and 881%? that's clearly an error."*

He was right, and there were two errors under it.

**The clock.** A replayed plant counts at the line's pace and measures every
duration on the wall's. Performance is the one OEE factor that divides a count
by a duration, so at 10x it comes out ten times too big — availability and
quality are ratios of things measured the same way and were unaffected. The
MES is *told* the replay speed (`MES_SIM_SPEED`, which `fsmes fleet start
--speed` puts into the environment of every process of that plant, the API
included) and now restates the run time on the line's clock before dividing,
and says everywhere that it did: a **Replay 10×** bar on every screen, `replay`
on `/health`, `mes_replay_factor` in `/metrics`, a pill on the fleet console,
`clock` beside the figures. **A plant meant to be looked at runs at 1×**; above
1× it is a test harness and the screens say so.

**The figure.** The rest of it is this record's own subject. `performance >
1.0` is the same inequality as *the counted work will not fit inside the run
time*, and this record already said the MES cannot tell which of its two
numbers is wrong. It printed the ratio between them anyway, as a percentage,
on every surface except the analysis waterfall — where [#62] had put the
sentence and nowhere else. A percentage on a screen is read as a measurement
of the machine. That one measures the gap between two of the MES's own
records.

### What changes

Where `cycle × units > runtime` (run time on the line's clock):

- **`performance` is `None`, and so is the `oee` built on it.** The same
  *unknown* the product already uses for a number it will not state — house
  rule 2, and the same shape as a figure withheld below a pack's coverage
  floor.
- **The sentence is printed on every surface the figure used to reach**, not
  only on the analysis screen and not only in a tooltip: the dashboard tile
  and machine card, the machine page, `/analysis/oee`, `/kpis/oee/{code}`.
  It still names the disagreement, both its numbers and none of the three
  culprits.
- **The ratio is kept**, as `performance_ratio`, with `counts_outrun_run_time`
  beside it. Nothing measured is discarded — that was 0025's objection to
  reporting *unknown*, and it stands. What the ratio loses is the name
  `performance`, which is the claim it could not support.
- **Nothing is capped.** A station reading exactly 1.0 still means a station
  that made exactly what its rating allows. Capping at 100 % would be the same
  lie pointing the other way, and 0025 settled that.
- `/metrics` exports no performance and no OEE series, and now says so in
  writing: a scrape is read without the sentence beside it by definition.

### Why this is not the option 0025 rejected

0025 rejected "report *unknown* above 1.0" because it throws away a real
measurement. This does not throw it away: the ratio, both its inputs, the
units counted outside run time and the run time itself are all in the payload
and all on the screen. The figure is not a measurement being suppressed; it is
a name being corrected. A plant that wants the ratio has it, under a name that
tells it what it has.

### What it costs

A plant that had learned to read performance over 100 % as "look at the
rating" now reads a dash and a sentence, and has to read the sentence. That is
the price of the number having been unreadable without it — and of one reader,
on one morning, reading 980 % and having to ask whether his plant or his MES
was broken.

[#62]: https://github.com/factorysemantics/factorysemantics-mes/pull/62
