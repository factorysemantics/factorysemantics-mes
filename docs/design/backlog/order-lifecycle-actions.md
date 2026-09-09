---
title: Order actions beyond release and close
status: needs-guidance
conversation: 2
turns: [3, 4]
route: /dashboard
plant: machining.db
created: 2026-09-01
updated: 2026-09-01
branch: 
tags: [fsmes, design, backlog]
---

# Order actions beyond release and close

**needs-guidance** — Hold, reschedule and split all need schema; and is a concern its own object?
## The idea

> "Also, what about actions? What if there's a concern on an Order. Or an order
> needs to be moved?"

## Assessment

The API already has more than the screen shows: release, close **and cancel**
all exist (`src/fsmes/api/routers/workorders.py`), and the orders screen offers
none of them. Surfacing cancel is small and could be done on its own.

Everything else lands on the schema, which is why this is a question rather
than a branch:

- **Hold / resume** needs a status the `OrderStatus` enum does not have, plus a
  reason code.
- **Reschedule** needs planned start and end. `due_date` exists; planned dates
  do not — and a scheduling model (`ScheduledSlot`) is being built right now on
  another branch. This should wait for it rather than invent a parallel one.
- **Split** needs a parent/child link between orders.
- **Concern** is a new table, if it is its own object.

The concern question is the one only you can answer. A non-conformance is a
product conformance record with a disposition workflow that locks quantity;
"this fixture feels loose" is not that. Folding the second into the first would
be rejected by anyone who has run a quality system. Making it a flag on the
order is cheap and probably wrong; making it its own object is right, and is a
module.

## Questions

1. Is a concern its own object — assignable, threaded, resolvable, no quantity
   impact — or a flag on the order?
2. Should hold and reschedule wait for the scheduling model now in flight?
3. Reason codes: master data configured per plant (principle 7), or a fixed
   list in the product?
