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
somebody typed into master data. So performance is a measurement *against a
rating*, and when it comes out over 100 % the rating is what is wrong:
somebody entered a cycle time slower than the machine, or the counter is
counting something other than what the rating rates.

That figure is printed, above 100 % and all, with the sentence that says so
underneath. It is **not** capped at 100 %. It used to be, in both places that
computed it, and on 2026-09-14 a lab run showed what that cost: nine stations
across two simulated plants all reported performance of exactly 100 % while
the script that generated their data said between 94.3 % and 99.9 %. A factor
that reads 100 % whatever the line does is not a measurement, and the OEE
built on it overstates the plant.

The OEE that follows can also pass 100 % for the same reason. Read it as *the
rating on this machine is wrong*, and fix the rating; the number will come
back down on its own. Decision record
[0025](../decisions/0025-performance-is-measured-not-capped.md) has the
arithmetic and the options that lost.

**What it is not.** A machine cannot make more than it made. Over 100 %
performance never means extra units were invented — that rule is house rule 1
and lives in [never invent production](never-invent-production.md). It means
the yardstick is short.

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
