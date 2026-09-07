---
title: Filter orders by material, status and due date
status: built
conversation: 1
turns: [1, 2]
route: /dashboard/orders
plant: bottling.db
created: 2026-09-01
updated: 2026-09-01
branch: design/orders-filters
tags: [fsmes, design, backlog]
---

# Filter orders by material, status and due date

**built** — The API had these filters since the scale pass; the screen never surfaced them, and orders already had due dates.
## The idea

> "How would a supervisor actually use this work orders screen? They need to
> filter to their materials and pick a time range."

Raised again from the machining dashboard the same evening: *"Work Orders card
needs filters and don't order's have dates?"*

## Assessment

Two findings, and the second is the interesting one.

**The filters existed and were never surfaced.** The scale pass gave
`GET /workorders` `status`, `material` and `q` filters plus `limit`/`offset`
paging (`src/fsmes/api/routers/workorders.py`). `orders.js` already declared
`filterStatus` and `filterText` and already built them into its query string —
but nothing in `orders.html` could set either, and `offset` was never advanced.
Every load was `?limit=50&offset=0`, forever. This is the "Surface the filters
in the UI" item already open in the convergence plan, so the idea was
independently ratified before it was raised.

**Orders already have dates.** `WorkOrder` carries `due_date`, `priority`,
`created_at`, `released_at`, `started_at`, `completed_at` and `closed_at`
(`src/fsmes/domain/workorders.py`), and `WorkOrderOut` was already sending
`due_date` and `priority` to this very screen, which discarded both. The answer
to "don't orders have dates?" was never a migration — it was a column nobody
rendered. Worth recording, because a review conducted from the screen alone
concluded the opposite and recommended adding `due_date`, `priority` and
`qty_ordered` as new fields. Reading the schema is not optional.

The one real gap was a **date filter on the API**, which did not exist.

## What was done

Branch `design/orders-filters` — 385 tests pass, 4 new.

- Filter bar: debounced code search, status, material (from
  `/masterdata/materials`, fetched once), due-from and due-to, and Clear.
- A **Due** column, with an *open* order past its date marked late. A completed
  order that ran late is history; an open one is this afternoon's problem.
- Pager: "Showing 1–50 of 18,347", buttons disabled at the ends.
- `due_after` / `due_before` on the API. An order with no due date is excluded
  from a date range rather than swept into it — it is not due before anything,
  and answering as though it were would invent a fact about the plant
  (principle 4).
- A date input means the whole of that day, so the screen sends `23:59:59` for
  the upper bound. "Due by the 3rd" must not drop an order due at nine that
  morning.

The web test asserts both halves — the control exists, *and* something listens
to it. A filter nobody wired is indistinguishable from no filter at all, which
is exactly the state this screen was in.

## Known and not fixed here

The four KPI tiles are computed from the page rather than the plant, so they
now describe the filtered view. That was true before filters existed, and it
deserves its own change rather than being smuggled into this one.
