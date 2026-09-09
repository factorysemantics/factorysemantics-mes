---
title: Simulate sixty machines to see the dashboard break
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

# Simulate sixty machines to see the dashboard break

**needs-guidance** — Right question, wrong plant: machining is deliberately small and that is what it exists to prove.
## The idea

> "Can we add 10 more machines to simulation to see how card expands? … can
> more options be simulated to see how it scales?"

## Assessment

Right question, wrong plant.

`labs/multiplant/machining/line.json` opens by declaring what it is for:
*"Northgate Machining — a deliberately SMALL plant. Three stations … It exists
to sit beside the six-station bottling line and prove the same MES serves both
without a code change."* Growing it to sixty stations destroys the only thing
it was built to prove, and that proof is load-bearing for the product's central
claim.

Adding machines is otherwise config, not code (principle 7) — stations, a tag
map, generated line data — so the work itself is small. It is the *where* that
needs a decision, which is why this stops here.

The dashboard genuinely will break at sixty, and the specifics are worth
having. `renderMachines` (`src/fsmes/web/app.js`) builds one card per machine
carrying four progress bars — 240 bars at sixty machines — and renders them in
whatever order the server returns (`order_by(Equipment.code)` in
`src/fsmes/api/routers/dashboard.py`). There is no grouping, sorting or
filtering of machines anywhere in the page. The audit feed is a straight 1:1
render capped at twelve rows with no collapsing of repeats, so one chatty
machine's state changes will bury every human action in it.

## Questions

1. **A third plant pack** — say `largeplant`, sixty machines across six work
   centres — or does Northgate stop being the small one? A third pack is the
   answer that keeps the two-plant proof intact, and it is also a third
   demonstration that adding a plant needs no code change.
2. **Scored, or a pressure test only?** Scoring against ground truth means
   events and a `line.json` the scorer can read. A UI pressure test could be
   seeded straight through the API for a fraction of the effort. These want
   different artifacts, so it is worth deciding before building either.
