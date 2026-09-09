---
title: Name Report production what it is, and say which order
status: built
conversation: 2
turns: [3, 4]
route: /dashboard
plant: machining.db
created: 2026-09-01
updated: 2026-09-01
branch: 
tags: [fsmes, design, backlog]
---

# Name Report production what it is, and say which order

**approved** — It is a booking, not a scrap; and the order is inferred from the machine, so it can book to the wrong one.
## The idea

> "What is 'Report production'? Is it scrapping or dropping a production order
> or what?"

## Assessment

It is a production booking — "this machine made N good and M scrap" — and has
nothing to do with cancelling an order. That the question had to be asked at
all is the finding: the label names the act rather than the outcome.

There is a real defect underneath it. The form submits only `equipment`, `good`
and `scrap` (`src/fsmes/web/index.html`, `src/fsmes/web/app.js`).
`POST /execution/report` *accepts* an explicit `order`, but the form never
sends one, so `execution.report` (`src/fsmes/services/execution.py`) infers it:
the first not-done operation on that machine belonging to a released or running
order, ordered by priority, then id, then seq. With two orders queued on one
machine it books to whichever sorts first and says nothing at all. On a floor
where machines change orders mid-shift, that is quantity booked against the
wrong order and discovered at month end.

Approved rather than built, for one specific reason: the form carries
`data-assist` anchors, and `tests/test_assistant.py` checks every authored
guide step against the page it names. Renaming this section breaks guides that
must be re-authored deliberately — not patched until a test goes green.

Sketch: rename to **Book output**; add an Order select filled from the chosen
machine's active operations and defaulting to the one the backend would have
inferred; send `order` explicitly; re-author the affected guide steps.

## Built

Renamed to **Book output**; an Order select fills from
`/workorders/dispatch?equipment=…` and defaults to exactly the operation the
backend would have inferred, so the honest path costs no extra clicks; the
form sends `order` and `seq` explicitly. The book-production guide gained a
"Say which order" step anchored to the new control.
