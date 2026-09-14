---
title: A non-conformance should record what was decided about the parts
status: needs-guidance
conversation: 13
turns: [37]
route: /dashboard/quality
plant: megafactory.db
created: 2026-09-14
updated: 2026-09-14
branch: 
tags: [fsmes, design, backlog]
---

# A non-conformance should record what was decided about the parts

**needs-guidance** — Right, and every version of it adds columns: disposition, quantity, containment, root cause. Question 1 is the one that needs Scott.
## What he asked

"ncrs should have more functionality then just close right?"

Right — and this is the one idea in the conversation that cannot be built
without a decision, because every version of it adds columns to
`non_conformances`. A schema change is always this verdict.

## What exists today

```
code, description, severity, work_order_id, lot_id,
status (open | closed), created_at, closed_at
```

That is it. A non-conformance is opened automatically when a measurement
falls outside spec, and the only thing anyone can do to it is close it, which
records who and when in the audit trail and nothing else. There is no record
of **what was decided about the affected material**, which is the entire
point of raising one in a plant.

## The questions

1. **Disposition.** A closed non-conformance in a real plant carries a
   decision about the parts: use as is, rework, scrap, return to vendor,
   deviate under concession. Is that a required field on close, an optional
   one, or its own object with its own approver? Making it required is the
   honest answer and it means no non-conformance can be closed the way they
   are closed today.
2. **Quantity.** Is a non-conformance about a *measurement* or about a
   *quantity of parts*? Today it is neither — it points at an order and a
   lot. If a disposition scraps material, does closing it book scrap against
   the order, or does somebody book that separately? Booking it automatically
   makes the MES change a production number from a quality screen, which is a
   much bigger decision than it looks.
3. **Containment.** Between raising and dispositioning, is the affected
   material held? There is a hold mechanism on orders already
   (`/workorders/{code}/hold`). Should a non-conformance be able to hold, and
   should closing it release?
4. **Root cause and corrective action.** A cause code and a CAPA reference
   are the usual next two columns. Are they in scope for this product, or is
   that the QMS's job and the MES's job is to hand it the facts? D8 says ERP
   connectors are modules inside the package; a QMS connector would be the
   same shape of answer and a much smaller one.
5. **Who may do which.** Closing is already a supervisor capability
   (`quality.close_nc`). Does dispositioning need its own, and does a
   concession need a second person?

## What was built instead

Nothing, on purpose. What the screen did gain in this pass is the honest
list: the non-conformances card is the server's page, filtered by status and
searchable by code, order or text, and it states its total. That is the
groundwork any of the answers above would sit on, and none of it presumes an
answer.

## Recommendation

Take question 1 on its own first, as a decision record. Disposition is the
piece that makes the object worth having, and questions 2 and 3 only become
answerable once "what did you decide about the parts" has a home.
