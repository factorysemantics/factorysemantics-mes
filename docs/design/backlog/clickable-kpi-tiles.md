---
title: KPI tiles filter the list, and yield links to analysis
status: built
conversation: 3
turns: [7, 8]
route: /dashboard/orders
plant: machining.db
created: 2026-09-01
updated: 2026-09-01
branch: design/clickable-kpis
tags: [fsmes, design, backlog]
---

# KPI tiles filter the list, and yield links to analysis

**built** — Both built; making them clickable forced the tile counts to become plant-wide instead of page-scoped.
## The idea

> "I should be able to click on the status cards up top. OPEN ORDERS should
> link to actual open orders. Yield should probably go to some analytics."

## Assessment

Both halves are right, and the second one is nearly free: `/dashboard/analysis`
already carries Good, Scrap, scrap-% and a Production chart, so yield has an
analytics screen to go to. It just was not linked. (The on-device model
proposed `/analytics/yield`, which this app does not serve — a good reminder
that the chat's suggestion is not the judgement.)

The interesting part is what making the tiles clickable exposed. They were
counted from `orders`, the current page of at most fifty rows. Harmless while
they were decoration; a plain contradiction the moment they became filters —
click a tile reading **7** and land on a list reading *"Showing 1–50 of 340"*.
Principle 4 does not allow shipping that, so the counts had to become
plant-wide in the same change. This was already recorded as a known limitation
in [[orders-supervisor-filters]]; Scott's idea is what forced it.

A second thing surfaced: `status` accepted exactly one value, and "open" means
*released or running*. The tile could not ask the API for what it displays.

## What was done

Branch `design/clickable-kpis` — 442 tests pass, 7 new.

- **Open orders**, **On the floor** and **Completed** are `<button>`s that set
  the filter; **Yield** is an `<a>` to `/dashboard/analysis`. Buttons for
  actions, an anchor for navigation — the keyboard and screen readers get the
  behaviour for free.
- **`GET /workorders/summary`** — counts by status plus good, scrap and yield
  for the whole plant, in one grouped query. Placed before `/{code}` in the
  router, or FastAPI goes looking for an order called "summary".
- Good is the quantity off the **end** of each route, not the sum of every
  operation — a part is not made three times for passing three stations. A test
  holds the SQL aggregate and `WorkOrder.good_qty` to the same answer, because
  two ways of computing one number is two ways of being wrong.
- Yield is `null`, never `0`, when nothing has been booked (principle 4).
- `status` is repeatable (`?status=released&status=running`), and the dropdown
  gained matching grouped options, so a tile and the dropdown cannot end up
  describing different lists.
- A summary that fails to arrive renders a dash. A number counted from fifty
  rows is worse than no number at all.

## Scope note

This exceeded the skill's "roughly two files" guide — four source files plus
two test files — because the honest version of "make the tiles clickable" is
inseparable from fixing what the click reveals. Splitting it would have shipped
a contradiction on purpose.
