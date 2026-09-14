---
title: The floor's order search box matches a material, as it always said it did
status: built
conversation: 12
turns: [35]
route: /dashboard
plant: megafactory.db
created: 2026-09-14
updated: 2026-09-14
branch: feat/ui-lists-filter
tags: [fsmes, design, backlog]
---

# The floor's order search box matches a material, as it always said it did

**built** — Work orders were already the server's page; the box said 'Order or material code' and matched only an order code.
## What he was looking at

The same screen: the Work orders card under the Machines card.

## What it did

Better than the machines card — the work-order card has read the paged
`/workorders` endpoint, with the server's filters, since it was written. What
it did *not* do was what its own search box promised. The placeholder says
"Order or material code" and the endpoint's `q` only ever matched an order
code, so typing a material found nothing and the card said "No order matches
these filters" — which reads as an empty plant rather than a box that does
not do what it says.

## What was built

`q` on `/workorders` matches an order code or the code of the material the
order makes. Nothing else about the card changed, because nothing else needed
to: the filters, the pager and the count were already the server's.

The scale test now covers it — sixty orders, filtered by status, by material,
by order code and by material code through the same box — so the card's
behaviour at scale is pinned rather than assumed.
