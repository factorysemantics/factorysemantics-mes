# 0019 — Count everything the machine counted: over-runs are booked, surplus is unassigned production

- **Status:** accepted
- **Date:** 2026-09-09
- **Deciders:** @kalwei

## Context
On 2026-09-09 the release check ran `fsmes demo` from a built wheel and printed
`Pack on PACK01: 16/15 good`, then `machine counted with no active order
equipment=PACK01 good=1`, then crashed.

Three facts sat behind that. Counter readings coalesce to the latest value per
machine per batch, so one booking can carry a delta of more than one unit — 14
booked plus a delta of 2 is 16 against an order for 15. `execution.report` books
the delta without reference to the order quantity and only afterwards asks
whether the operation is finished, so the order completes at 16. The next delta
then finds no operation that is not `DONE`, and the unit the machine really made
existed only as a warning line.

Sixteen units were made. The MES kept a record of fifteen of them.

The crash was separate and is fixed alongside: the demo printed
`confirmations[-1]['lot']`, and the message that happened to land last was an
operation confirmation, which has no finished-goods lot because an operation
does not make one.

## Options considered
| Option | For | Against |
|---|---|---|
| Book it and mark it; surplus counted with no order open becomes unassigned production | every unit a machine counted survives, and nothing is attributed to an order that cannot be shown to own it | a nullable `work_order_id`, and a list a plant has to look at |
| Book to the order, then keep counting as unassigned production, but cap the order at its quantity | the ERP's order never exceeds what was asked for | the order's own good count would then differ from what the machine counted, which is the invented number this project exists not to print |
| Cap and refuse: book 15, raise the surplus as an exception a person resolves | cleanest for the ERP; a person looks at every over-run | a real unit exists and the MES says it does not, until somebody clears a queue. That is inventing production with the sign reversed |
| Attribute the surplus to the order that just closed on that machine | tidy; usually right | usually is not a fact. A machine between orders, jogged by hand, or running a second order would be booked to the wrong one, and nothing downstream could tell |

## Decision
The MES books every unit a machine counts, and never guesses which order a unit
belongs to.

A counter delta that straddles the ordered quantity books in full. The operation
completes as before, and the order reports `over_qty` — how far past the ordered
quantity the line actually ran — on `GET /workorders/{code}` and in the ERP order
completion, so an over-run reaches the ERP as its own number rather than as a
good quantity that happens to be larger than the order.

A count arriving when no operation is open is recorded as **unassigned
production**: a production log row against the equipment with no work order.
`production_logs.work_order_id` becomes nullable to hold it. These units are
listed, with their totals, at `GET /execution/unassigned` and on the machine
page's Operate tab. They are never rolled into an order.

This applies to counter deltas only. A person typing a quantity against a machine
with no order open still gets an error: a typed count with nothing to book it
against is a mistake worth stopping, not a fact worth keeping.

## Consequences
Easier: the number on the screen is the number the machine said, at every
quantity, and an over-run is visible instead of being a 16 next to a 15 that a
reader has to notice.

Harder: `production_logs` no longer means "production against an order". Anything
summing it for order-attributed production must filter `work_order_id IS NOT
NULL`. OEE and the machine rollups sum by equipment and are unaffected — and
they are now more right, because the units they count are all there.

To revisit: unassigned production is currently only listed. Whether a person
should be able to assign it to an order after the fact — with the assignment
recorded as a decision a person made, not as a count a machine reported — is a
real question, and one to answer once a plant has looked at a list of these and
said what it wants to do with them. Nothing here forecloses it.

## House rules touched
Rules 1 and 2. Never invent production: no unit is attributed to an order that
cannot be shown to own it, and no unit is deleted for want of one. Unknown is a
valid answer: "these units exist and we do not know which order they belong to"
is the honest record, and it is a list with a total rather than a zero.
