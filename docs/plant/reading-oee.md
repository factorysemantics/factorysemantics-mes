# Reading OEE

*How-to. What each segment of the OEE screen means and what to do about the grey one.*

The OEE screen follows ISO 22400 in its arithmetic and house rule 2 in its
honesty: **a KPI that cannot be computed returns *unknown* and says why**.
That is the grey segment.

## The segments

| Segment | Source | Becomes *unknown* when |
|---|---|---|
| Availability | machine state history from the tag map's state tag | the MES was not subscribed for part of the window (it started later, the connection dropped, the machine went stale) |
| Performance | counter deltas against the rated cycle time in the tag map | the rated cycle time is blank, or a counter reset happened inside the window |
| Quality | good and scrap counters | there is no scrap counter in the tag map — quality reads *unknown*, not 100 % |

The screen states its data source in a line under the number; a screenshot
without that line is not a screenshot of this product.

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

## See also

- [Never invent production](never-invent-production.md)
- [Engineering guide — the worksheet](../onboarding/GUIDE-ENGINEERING.md)
- Decision record [0004](../decisions/0004-never-invent-production.md)
