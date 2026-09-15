# 0028 — An order does not finish itself at its quantity; the over-run it reports is the true one

- **Status:** accepted
- **Date:** 2026-09-14
- **Deciders:** @kalwei

## Context
`labs/experiments/over-run.toml` runs the bottling line for an uninterrupted
hour against `WO-ACME-4711`, an order for 4,000, with the line publishing the
order it is running the whole time. The line made **6,104** under that order.
The MES booked **4,003** against it, reported an over-run of **3**, and kept
**15,351 counts** across the six stations as production with no order open.

Nothing was lost and nothing was invented — per-station booking was inside the
replay's band at all six machines — but the plant reads an over-run of 3 where
the line ran 2,104 past. Decision 0019 made 16-against-15 visible; this is the
same rule failing at scale.

The cause is one line in `execution.report`: a machine-counted booking that
brought an operation to the ordered quantity completed that operation, which
completed the order when it was the last one. After that the equipment has no
open operation, so every further count became unassigned production — correct
under 0019, and yet the wrong answer, because an order really was open. The
order's own figures stopped at its quantity while the line kept going.

Underneath it is an assumption worth naming: that reaching the ordered quantity
and being finished are the same fact. On a floor they are not. A quantity is
what the plant was asked for. The line stops when somebody stops it, when the
material runs out, or at a changeover — and an order that has made its number
is routinely still running.

## Options considered
| Option | For | Against |
|---|---|---|
| The order does not complete at its quantity; booking continues and `over_qty` grows | the over-run is the true figure, and the units stay attached to the order that made them | an order now needs somebody to finish it, and one left open reports no completion to the ERP |
| Keep completing at quantity, and roll the surplus into the completed order afterwards | the ERP still gets a tidy completion the moment the number is reached | a completed order whose numbers keep moving is a record that cannot be trusted at any moment, and the confirmation has already been sent |
| Keep completing at quantity, and count the rest as unassigned production | what is there today; no code changes | it is the finding: the plant reads 3 where the line ran 2,104 past, and 15,351 units sit with no order beside an order that was open |
| Complete at quantity but reopen the operation on the next count | the loop closes on its own and the over-run is still counted | a completion that un-happens, and an ERP confirmation sent for a number that then changed |

## Decision
**An operation does not complete itself on reaching the ordered quantity.**
Completing an operation is an act: a person on the floor
(`POST /workorders/{code}/operations/{seq}/complete`), or the ERP. Until then
the operation stays open and every unit the machine counts is booked to it.

**Units made past the quantity are booked to the order that made them**, and
the order reports how far past it ran as `over_qty` — on
`GET /workorders/{code}`, on the orders screen, in the ERP order completion and
in the B2MML confirmation. That figure is now the whole over-run rather than
whatever one coalesced delta happened to carry across the line.

**Which order they are booked to is read, never guessed.** The evidence is,
in order: the order the line itself publishes, where the tag map carries a
`line.publishes_order` block (#60); otherwise the released or running order
whose routing puts an operation on that machine. Where there is no such order,
nothing changes — the units are unassigned production under 0019, listed with
their total and never rolled into an order.

## Consequences
Easier: the over-run a plant reads is the over-run the line ran. Units made
past a quantity stay attached to the order that made them instead of arriving
in a list with the units a machine made between orders, which are a different
fact with a different fix.

Harder, and the real cost of this: **an order now stays open until somebody
closes it.** The ERP order completion, the finished-goods lot and the
certificate of analysis are all issued on completion, so an order nobody
finishes confirms nothing. Where a machine has two released orders on the same
routing, completing the first is also what moves the machine onto the second —
previously the quantity did that by itself. `fsmes demo` therefore completes
its own operations, as the person it is standing in for would.

Not done here: the OPC agent does not yet subscribe to the line's own
`publishes_order` tag, so today's evidence is the released order on the
routing. The tag map has carried the block since #60 and the lab reads it; the
agent reading it for booking is the next step, and until it does, a plant with
two orders released on one routing is still choosing by priority rather than by
what the line said. That is an inference, and it is named here so it is not
mistaken for a fact.

To revisit: whether an order should be *allowed* to run indefinitely past its
quantity, or whether there is a point at which the MES should ask. An ERPNext
order has an over-production allowance and refuses a confirmation beyond it
(see `tests/test_erpnext_live.py`); this MES states the over-run and lets the
ERP judge. That is the right split today. If a plant wants the line warned at
120 % it is config, not code.

## House rules touched
Rules 1 and 2. Never invent production: the order a unit is booked to is read
from the line or from the one released order on the routing, never inferred
from what recently closed. Unknown is a valid answer: where no order is open
the units stay unassigned, named and totalled, rather than being swept into
the order that happened to be there last.
