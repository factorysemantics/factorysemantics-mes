---
title: The floor's machine grid pages and filters on the server
status: built
conversation: 12
turns: [33]
route: /dashboard
plant: megafactory.db
created: 2026-09-14
updated: 2026-09-14
branch: feat/ui-lists-filter
tags: [fsmes, design, backlog]
---

# The floor's machine grid pages and filters on the server

**built** — The card had a filter bar already; the filtering was the browser's, over a copy of the whole plant fetched twice a second.
## What he was looking at

The mega-factory's Floor screen with 108 stations. The Machines card drew a
card for every one of them, every refresh, with no way to find one.

## What it did

The card already had a filter bar and a pager — and both were the browser's.
`/dashboard/summary` returned every machine in the plant with its OEE and its
process value, and `renderMachines` sliced that list. On a two-machine demo
that is invisible. On a thousand machines it is a thousand statements a
second, per screen watching, to draw twenty-four cards: the analog reading is
one query per machine and there is no batched form of it.

So the complaint was exactly right and the filter bar was not the fix.

## What was built

`/dashboard/summary` takes `machine_q`, `machine_state`, `machine_limit` and
`machine_offset`, and answers with `machines_page` beside `machines` —
`total`, `scope_total`, `limit`, `offset`, `has_more`. Left out,
`machine_limit` returns every machine as before, so the `machines` agent tool
did not change.

`line` stays what it was — a *scope*, which is what the Line screen asks for,
and the tiles follow it. `machine_q` and `machine_state` filter the grid
alone, so a floor narrowed to the six machines that are down never reads as a
six-machine plant.

Three tiles were counting their own page and now count the plant: machines
running, plant OEE, and active orders (which said 25 on a plant with 60
released orders). OEE is three grouped queries whatever the count, so it is
computed for the whole scope; only the per-machine analog read follows the
page.

The card's "on LINE1" label came from downloading the whole equipment tree
once a minute and walking it in the browser. Each machine now says which line
it is on, and the line dropdown asks for the work centres alone.

A test seeds a thousand machines and counts the statements the database is
asked to run: a page of twenty-four costs under sixty, the unpaged answer
costs over a thousand. That number is the difference between "returns
twenty-four rows" and "reads a thousand rows to return twenty-four", and
nothing else in the suite could tell them apart.
