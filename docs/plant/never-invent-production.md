# Never invent production

*Explanation. The first house rule, why it exists, and how it is tested.*

An MES has one job that no other system does: say what the plant actually
made. Every other number — OEE, scrap rate, schedule adherence, the ERP
confirmation — is derived from it. If that number is ever invented, every
report downstream is fiction with a confident face.

Most ways an MES invents production are small and reasonable-looking:

- **Booking the order quantity when the order completes.** The line made
  what the line made; the order says what was wanted.
- **Stopping the books at the order quantity.** The same fault with the sign
  reversed: the line kept running, the MES stopped counting it against the
  order, and the over-run a plant reads is three where the line ran two
  thousand past.
- **Treating a counter that dropped to zero as negative production**, or
  worse, as a huge positive delta when it wraps.
- **Filling a gap.** The agent was down for twenty minutes; the machine was
  probably running; interpolate.
- **Reporting OEE as 0 %** for a window the MES was not watching. Zero is a
  number. It goes into an average. Somebody gets asked about it.
- **Filing unlabelled downtime under "other"** so the pareto adds up.

## The rule

1. **Counter deltas book production.** The MES reads the good and scrap
   counters over OPC UA and books the difference since the last reading,
   against the operation the machine is running.
2. **A counter falling toward zero is a reset.** It re-baselines and books
   nothing. The scenario harness stages a reset in every scored hour to make
   sure this stays true.
3. **Stale and duplicate notifications are ignored.** A value the MES has
   already seen, or one older than the last, books nothing.
4. **Unknown is a valid answer; zero is not.** A KPI that cannot be computed
   honestly returns `null` with a reason.
5. **Unlabelled data is reported as unlabelled.**
6. **An order's quantity is not a gate.** A quantity is what the plant was
   asked for. A line that has made its number and not been stopped is still
   making units against that order, so booking continues past the quantity
   and the order reports how far past it ran. Finishing an order is an act —
   a person, or the ERP — never a number being reached.

## How it is tested

Tests are named after the behaviour they pin, so the suite reads as a list
of promises. Some of them, by name (2026-09-07):

- `test_a_line_never_observed_reports_unknown_not_zero`
- `test_oee_says_unknown_rather_than_zero_without_history`
- `test_a_machine_that_slept_reads_stale_not_dead`
- `test_unlabelled_downtime_is_named_not_hidden`
- `test_counters_that_disagree_with_the_route_are_reported_not_clamped`
- `test_the_recording_keeps_the_counter_reset_that_the_scenario_stages`
- `test_reaching_the_ordered_quantity_does_not_finish_the_order`
- `test_a_line_that_makes_half_again_the_order_reports_an_over_run_of_half`
- `test_unknown_scores_are_counted_separately_never_averaged_in`

And the scoring harness: `fsmes score <plant>` replays a scripted hour with
a known truth — how many pieces the line made, a planned stop, a breakdown,
a counter reset — and reports whether the MES booked what was made, whether
the planned stop was misbooked as downtime, and whether the breakdown was
detected. The score is kept per run so honesty is a trend, not a claim.

## What it costs

A plant that runs this MES will see *unknown* on screens where its previous
system showed a number. That is the product working. The fix is always
upstream — a cycle time in the worksheet, a scrap counter on the machine,
an agent that stays up — never a default in the code.

## See also

- [Reading OEE](reading-oee.md)
- Decision records [0004](../decisions/0004-never-invent-production.md),
  [0019](../decisions/0019-count-everything-the-machine-counted.md) and
  [0028](../decisions/0028-an-order-does-not-finish-itself.md)
- The house rules in [CONTRIBUTING](https://github.com/factorysemantics/factorysemantics-mes/blob/main/CONTRIBUTING.md)
