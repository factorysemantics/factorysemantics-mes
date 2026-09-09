---
title: The SPC head prints Cp/Cpk while the verdict withholds capability
status: done
route: /dashboard/spc
branch: main
created: 2026-09-03
updated: 2026-09-03
tags: [fsmes, design, backlog]
---

# The SPC head prints Cp/Cpk while the verdict withholds capability

**approved** — small, UI-only, and the screen contradicts itself.

## The idea

On `/dashboard/spc` the facts row shows Cp 1.51 and Cpk 1.50 for
FG-BOTTLE fill_weight while the verdict beneath reads "out of control -
capability is not meaningful until this is settled". The API returns the
capability numbers regardless; the screen should not.

## Assessment

`services/spc.chart` computes `capability` whenever both spec limits exist
and sigma > 0, and `_verdict` withholds it in words when `stable` is false.
The screen (`src/fsmes/web/spc.js`, `load()`) fills `#f-cp`, `#f-cpk`,
`#f-pp` from `data.capability` without consulting `data.stable`. Principle 4:
a number shown beside a sentence saying it is meaningless is a misleading
number. Fix: when `data.stable === false`, render those three cells as
"withheld"; keep the numbers in a `title` so an engineer who wants them can
hover. UI-only; within the night-shift self-merge scope.

## What was done

Shipped 2026-09-03: `spc.js` renders Cp, Cpk and Pp as "withheld" whenever
`data.stable === false`, with the computed value in the cell's tooltip. The
API is unchanged - it still returns the numbers, and the verdict still says
why they are withheld.
