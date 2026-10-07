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

## A planner planning is not the floor inventing

On a plant that simulates, the simulated floor used to make up an order code
and a quantity when it ran out of work, and the line went on looking busy
against a number nobody had asked for. That stopped on 2026-09-18 (#83): the
lab packs got a book of ten real orders, and `Floor.release_next` was left
with no way to invent one. What it does when the book is empty is say so,
once, and let the line's output be reported as unassigned production with its
total — which is rule 5 and
[decision 0019](../decisions/0019-count-everything-the-machine-counted.md)
doing exactly what they are for.

A pack's book is finite, though, and both lab plants emptied theirs about
forty hours after they were built — bottling at 04:21 UTC on 2026-10-07 —
and then measured into no order at all. The answer is not to let the floor
invent one again. It is that **a plant has three people in it and a lab plant
only had two**: an operator, a shift supervisor, and a production planner, who
in a real plant is where orders come from (or the ERP — this MES is not an
ERP). So `fsmes run-operations` has a third identity, `FLOOR-PLAN`, which
holds `orders.create` and `plant.read` and nothing else, and a pack that asks
for it with
`planning.keep_planned` gets its book kept that deep.

The distinction is not a technicality:

- The planner creates an order **as itself**, over the public `POST
  /workorders`, and the audit row says `FLOOR-PLAN`. Nothing appears in the
  book with `system` against it.
- It copies what the plant **already makes** — material, quantity, priority
  and code sequence read back out of the plant's own book over the API. It
  invents no product and no size; a plant whose book has never held an order
  gets no first order, because what a plant makes is not something a
  simulator knows.
- It **plans and does not release.** `orders.release` is the supervisor's, so
  the order it writes is *planned* until the simulated supervisor puts it on
  the line, and the sequence on a lab plant is the sequence in a real one.
- Booking is still the counters'. Nothing here books a unit, and
  `release_next` still never invents an order.
- **The default is no planner** (`keep_planned` 0). A pack that says nothing
  behaves exactly as it did before — including the scripted over-run
  experiment, whose whole point is a book that runs out.

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
- `test_when_the_book_runs_out_the_floor_says_so_once_and_invents_nothing`
- `test_a_plant_whose_book_has_never_held_an_order_plans_nothing_and_says_so`
- `test_a_planned_order_copies_this_plants_own_book_and_nothing_else`
- `test_the_planner_plans_and_does_not_release`

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
  [0029](../decisions/0029-an-order-does-not-finish-itself.md)
- The house rules in [CONTRIBUTING](https://github.com/factorysemantics/factorysemantics-mes/blob/main/CONTRIBUTING.md)
