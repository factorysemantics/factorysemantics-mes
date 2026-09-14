# Reading OEE

*How-to. What each segment of the OEE screen means and what to do about the grey one.*

The OEE screen follows ISO 22400 in its arithmetic and house rule 2 in its
honesty: **a KPI that cannot be computed returns *unknown* and says why**.
That is the grey segment.

## The segments

| Segment | Source | Becomes *unknown* when |
|---|---|---|
| Availability | machine state history from the tag map's state tag | the MES was not subscribed for part of the window (it started later, the connection dropped, the machine went stale) |
| Performance | counter deltas against the rated cycle time in master data | the rated cycle time is blank, the machine was not seen running, or nothing was counted |
| Quality | good and scrap counters | there is no scrap counter in the tag map — quality reads *unknown*, not 100 % |

The screen states its data source in a line under the number; a screenshot
without that line is not a screenshot of this product.

## Performance can read over 100 %, and that is the point

Availability and quality are each a share of something the MES watched
itself, so neither can pass 100 %. **Performance is not a share.** It is

    rated cycle time × units counted ÷ time the machine was seen running

and only the second half of that is measured. The first half is a number
somebody typed into master data.

That figure is printed, above 100 % and all. It is **not** capped at 100 %. It
used to be, in both places that computed it, and on 2026-09-14 a lab run
showed what that cost: nine stations across two simulated plants all reported
performance of exactly 100 % while the script that generated their data said
between 94.3 % and 99.9 %. A factor that reads 100 % whatever the line does is
not a measurement, and the OEE built on it overstates the plant. The OEE that
follows can pass 100 % for the same reason.

## What over 100 % actually tells you

Performance over 100 % is the same statement as **the work the MES counted
will not fit inside the run time the MES recorded**: units × rated cycle came
out larger than the running seconds. Two of the MES's own numbers disagree,
and the screen says so in those words. It does **not** tell you which of them
is wrong, because the MES cannot tell. Three things produce it:

1. **The rating is slower than the machine.** Somebody entered a cycle time
   the machine beats, or the counter counts something other than what the
   rating rates. Fix it in the worksheet and the number comes back down.
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
run time** is reported beside the figure, and named in the sentence when there
are any. Those units are not taken out of anything — they are units the plant
made, they stay in good, scrap, quality and performance (house rule 1). They
are a clue about which of the two numbers to distrust, nothing more.

Where to start: if *units counted outside run time* is near zero, suspect the
rating. If it is a large share of the count, suspect what the MES saw — the
machine's publish interval and the deadband on its state tag — because a
machine whose state the MES samples too coarsely produces exactly this: counts
that are right, landing beside a state history that is not. Decision records
[0025](../decisions/0025-performance-is-measured-not-capped.md) and
[0026](../decisions/0026-counts-that-outrun-the-run-time.md) have the
arithmetic and the options that lost.

**What it is not.** A machine cannot make more than it made. Over 100 %
performance never means extra units were invented — that rule is house rule 1
and lives in [never invent production](never-invent-production.md). It means
two of the MES's numbers do not agree, and it says so rather than choosing one.

## Stale is not stopped

A machine whose tags stopped changing is **stale**, not down. Stale means
the MES stopped hearing, which is a different fact from the machine
stopping. Downtime is only booked from a state tag that says so; stale time
is reported as stale and excluded from availability with a reason.

## Unlabelled is reported as unlabelled

A downtime pareto that files unlabelled stops under "other" convinces a
plant it has data it does not have. Here the unlabelled bar is named
*unlabelled*. Reason codes are configuration; the how-to for shifts and
reason codes is not written yet (as of 2026-09-07) — ask in Discussions and
it moves up the list.

## To shrink the grey segment

1. Fill the rated cycle time for every machine in the worksheet.
2. Give every machine a scrap counter, or accept that quality is unknown.
3. Keep the agent running: the window it was not watching cannot be
   reconstructed later, by design.

A rated cycle time that is wrong does not make grey; it makes a performance
figure over 100 % with a note beside it. Both are worth a walk to the
machine, and only one of them looks like a problem at first glance.

## See also

- [Never invent production](never-invent-production.md)
- [Engineering guide — the worksheet](../onboarding/GUIDE-ENGINEERING.md)
- Decision record [0004](../decisions/0004-never-invent-production.md)
- Decision record [0025](../decisions/0025-performance-is-measured-not-capped.md)
