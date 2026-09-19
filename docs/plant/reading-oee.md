# Reading OEE

*How-to. What each segment of the OEE screen means and what to do about the grey one.*

The OEE screen follows ISO 22400 in its arithmetic and house rule 2 in its
honesty: **a KPI that cannot be computed returns *unknown* and says why**.
That is the grey segment.

## The segments

| Segment | Source | Becomes *unknown* when |
|---|---|---|
| Availability | machine state history from the tag map's state tag | the MES was not subscribed for part of the window (it started later, the connection dropped, the machine went stale) |
| Performance | counter deltas against the rated cycle time in master data | the rated cycle time is blank, the machine was not seen running, nothing was counted, or the counted work will not fit inside the run time |
| Quality | good and scrap counters | there is no scrap counter in the tag map — quality reads *unknown*, not 100 % |

The screen states its data source in a line under the number; a screenshot
without that line is not a screenshot of this product.

## Performance is not a share, and it is never capped

Availability and quality are each a share of something the MES watched
itself, so neither can pass 100 %. **Performance is not a share.** It is

    rated cycle time × units counted ÷ time the machine was seen running

and only the second half of that is measured. The first half is a number
somebody typed into master data.

There is no cap on it and there never will be. There used to be, in both
places that computed it, and on 2026-09-14 a lab run showed what that cost:
nine stations across two simulated plants all reported performance of exactly
100 % while the script that generated their data said between 94.3 % and
99.9 %. A factor that reads 100 % whatever the line does is not a
measurement, and the OEE built on it overstates the plant.

## When the counted work will not fit inside the run time, there is no figure

Divide those two numbers and you can get an answer above 1.0. That answer is
the same statement as **the work the MES counted will not fit inside the run
time the MES recorded**: units × rated cycle came out larger than the running
seconds. Two of the MES's own numbers disagree.

Until 2026-09-18 the screen printed that answer as a percentage. It stopped,
because a percentage on a screen is read as a measurement of the machine and
this one measures the gap between two records. A plant replaying a recorded
line overnight showed **performance 881 %** and **OEE 980 %** on its
dashboard, and the person reading it called it an error; he was right, twice
over — see *[two clocks](#a-replayed-plant-has-two-clocks)* below for the
other half.

So where the counts outrun the run time, the screen shows:

- **no performance figure, and no OEE** — a dash, hatched bar, and the same
  *unknown* the rest of the product uses for a number it will not state;
- **the sentence**, naming both numbers: how many units at what rated cycle
  is how many seconds of work, inside how many seconds of running, and that
  the MES cannot tell which of the two is wrong;
- **units counted outside run time**, where there are any (below);
- **the ratio itself**, kept in the API answer as `performance_ratio` and in
  `counts_outrun_run_time`. Nothing is thrown away. It is simply not called
  performance, because it is not a measurement of the machine.

Neither is it capped at 100 %, which would be the same lie pointing the other
way: a station reading exactly 1.0 is a station that made exactly what its
rating allows, and that has to keep meaning what it says.

## What that disagreement actually tells you

It does **not** tell you which of the two numbers is wrong, because the MES
cannot tell. Three things produce it:

1. **The rating is slower than the machine.** Somebody entered a cycle time
   the machine beats, or the counter counts something other than what the
   rating rates. Fix it in the worksheet and the figure comes back.
2. **The run time is short of what the machine really ran.** State comes from
   a tag the MES samples, and a machine that changes state faster than the
   publish interval is running and stopping in gaps nobody saw. A lab run on
   2026-09-14 found exactly this: a deburring cell changing state every 2.7
   seconds, watched about every 15, reported 1,878 seconds of running where
   the line had 1,996 — and the MES blamed the master data, which was right
   to a tenth of a percent. **Nothing in the MES can recover this**; the fix
   is at the machine, in the publish interval and the deadband on its state
   tag.
3. **Units were counted outside the run time.** A counter catching up after a
   stop books units at an instant the machine was not running.

The third is the one the MES can measure, so it does: **units counted outside
run time** is reported beside the sentence, and named in it when there are
any. Those units are not taken out of anything — they are units the plant
made, they stay in good, scrap, quality and in the ratio (house rule 1). They
are a clue about which of the two numbers to distrust, nothing more.

Where to start: if *units counted outside run time* is near zero, suspect the
rating. If it is a large share of the count, suspect what the MES saw — the
machine's publish interval and the deadband on its state tag — because a
machine whose state the MES samples too coarsely produces exactly this: counts
that are right, landing beside a state history that is not. Decision records
[0025](../decisions/0025-performance-is-measured-not-capped.md) and
[0026](../decisions/0026-counts-that-outrun-the-run-time.md) have the
arithmetic and the options that lost.

**What it is not.** A machine cannot make more than it made. A missing
performance figure never means extra units were invented — that rule is house
rule 1 and lives in [never invent production](never-invent-production.md). It
means two of the MES's numbers do not agree, and it says so rather than
choosing one.

## A replayed plant has two clocks

A plant that replays a recorded line faster than it was recorded counts at the
line's pace and measures every duration on the wall's. Performance is the one
OEE factor that divides a count by a duration, so at ten times real time it
comes out ten times too big; availability and quality are ratios of things
measured the same way, and are unaffected.

The MES is told the speed rather than guessing it. `fsmes fleet start --speed
10` puts `MES_SIM_SPEED=10` into the environment of every process of that
plant, the API included, and the API restates the run time on the line's clock
before it divides. Where it has done so it says so, everywhere a reader can
be:

- a **Replay 10×** bar across the top of every screen, the way shadow mode
  puts one there — it is a fact about the installation, not a notification;
- `replay` on `/health`, which is public, so a fleet console shows the plant
  as **replay 10×** rather than as *live*;
- `mes_replay_factor` in `/metrics`, present only on a plant that is
  replaying;
- `clock` beside the figures in `/analysis/oee` and `/kpis/oee/{code}`.

Durations stay on the clock the MES measured — `runtime_seconds` is wall
seconds — with the factor beside them, so a reader converts rather than
having numbers silently restated under them.

> **A plant meant to be looked at runs at 1×.** Above 1× a plant is a test
> harness: its window is an hour of the line in six minutes of yours, its
> shift boundaries are not the line's, and its figures are only readable with
> the factor in hand. Replay fast to test the MES; stand a plant up at 1× to
> look at one.

Restating the run time is not a cap by another name. A replayed plant whose
counts still will not fit inside its line-clock run time has a real
disagreement, and it is named exactly as above.

## Stale is not stopped, and unseen is neither

A machine whose tags stopped changing is **stale**, not down. Stale means
the MES stopped hearing, which is a different fact from the machine
stopping. Downtime is only booked from a state tag that says so.

A machine the MES cannot **reach** is a third thing again, and since
2026-09-14 it is recorded as one. For the minutes the connection is gone the
machine has no state at all — not `down`, which would invent a breakdown, and
not the state it was last seen in, which would invent availability. Those
seconds leave availability's **denominator** rather than its numerator, and
every OEE answer says how many of them there were:

| Figure | What it means |
|---|---|
| `runtime_seconds` | seconds the MES saw the machine running |
| `observed_seconds` | seconds the MES could see the machine at all |
| `unknown_seconds` | seconds inside the window a recorded disconnection covered |
| `unknown_share` | that, as a share of the window |
| `coverage` | **how much of the window anybody watched, all causes together** |
| `ledger` | every second of the window, in one disposition, with the unwatched ones named |

So 92 % availability over forty observed minutes of an eight-hour window is
still 92 %, and `coverage` says it was 8 %. Read both. A shift whose coverage
is two thirds is not a shift to make a decision from, however good its OEE
looks — and a plant that would rather be told *unknown* than shown such a
figure sets a floor in its pack and is.

Since 2026-09-17 availability is **derived from** that ledger rather than
computed beside it, so the two can never disagree, and the ledger's seconds
are checked against the window: one that does not add up is a bug, not a
rounding difference. [How much of the window did the MES
see](../operate/coverage.md) is the whole story — the four causes, the floor,
and `fsmes oee explain`, which prints the ledger as a table you can argue
with.

[Losing sight of a machine](../operate/opc-disconnections.md) is the
connection half of it, including the one case that does not cover.

## Unlabelled is reported as unlabelled

A downtime pareto that files unlabelled stops under "other" convinces a
plant it has data it does not have. Here the unlabelled bar is named
*unlabelled*.

Reason codes are configuration, and since 2026-09-19 a plant can name its
own: a list somebody drafts and somebody else puts in force, which the
station screen then offers instead of a text box. See
[who names the reasons](../operate/who-names-the-reasons.md). The pareto
groups by code where there is one and by typed text where there is not — the
two never merge — and `GET /analysis/downtime` says how many seconds of the
window came from each, beside the unlabelled share. The three account for
every second of downtime in the window.

## To shrink the grey segment

1. Fill the rated cycle time for every machine in the worksheet.
2. Give every machine a scrap counter, or accept that quality is unknown.
3. Keep the agent running: the window it was not watching cannot be
   reconstructed later, by design. `/health` says how many machines this
   plant can currently see, which is the thing to alert on.

A rated cycle time that is wrong does not make grey either; it makes a machine
with no performance figure and a sentence saying its counted work will not fit
inside its run time. Both are worth a walk to the machine, and neither looks
like a problem at first glance — one of them used to look like a triumph.

## See also

- [Never invent production](never-invent-production.md)
- [How much of the window did the MES see](../operate/coverage.md)
- [Losing sight of a machine](../operate/opc-disconnections.md)
- [Engineering guide — the worksheet](../onboarding/GUIDE-ENGINEERING.md)
- Decision record [0004](../decisions/0004-never-invent-production.md)
- Decision record [0025](../decisions/0025-performance-is-measured-not-capped.md)
- Decision record [0026](../decisions/0026-counts-that-outrun-the-run-time.md)
- Decision record [0033](../decisions/0033-availability-is-a-share-of-what-was-watched.md)
