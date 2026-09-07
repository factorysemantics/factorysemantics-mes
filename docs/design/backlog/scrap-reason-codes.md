---
title: Scrap needs a reason code
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

# Scrap needs a reason code

**needs-guidance** — ProductionLog has no reason column, so every scrap figure is unattributed. Schema change.
## The idea

Split out of the "Report production" message: scrap is recorded as a bare
number.

## Assessment

`ProductionLog` has `good_qty` and `scrap_qty` and **no reason column**
(`src/fsmes/domain/execution.py`). `WorkOrderOperation.scrap_qty` is likewise a
float with nothing attached. So every scrap figure this MES holds is
unattributed, and scrap without a reason is a number nobody can act on — it
tells a supervisor that something is wrong and nothing about what.

It is probably the highest-value single field missing from the product. It is
also a schema change, so by rule it stops here for a decision rather than
becoming a branch.

## Questions

1. A reason-code list as **master data per plant** (principle 7 — config, not
   code), or a fixed enum in the product? Per-plant is consistent with
   everything else here and is more work.
2. Required when scrap > 0, or optional? Required is right and will annoy
   operators on day one. That is your call, not mine.
3. What happens to scrap already booked — a null reason, or an explicit
   "not recorded" code? Principle 4 argues for the explicit code: a blank reads
   as an omission, where the truth is that the field did not exist.
