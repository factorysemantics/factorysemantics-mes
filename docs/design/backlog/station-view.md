---
title: A per-machine station view for operators
status: approved
conversation: 2
turns: [3, 4]
route: /dashboard
plant: machining.db
created: 2026-09-01
updated: 2026-09-01
branch: 
tags: [fsmes, design, backlog]
---

# A per-machine station view for operators

**approved** — The floor forms are a supervisor tool; an operator must not pick their machine from a list of sixty.
## The idea

> "And shopfloor decisions, can more options be simulated to see how it
> scales?"

## Assessment

Correct, and it is an architecture point rather than a screen tweak. The floor
dashboard's stacked forms are a *supervisor's* tool: they identify the machine
with a dropdown. In a 300-person plant an operator stands at one machine for a
shift and must never pick it out of a list of sixty.

No per-machine route exists. The app serves exactly nine dashboard pages
(`src/fsmes/api/app.py`) and none is parameterised by machine.

Approved rather than built: a new route, a new page, a new interaction model,
and a capability question about who sees it — plus it depends on the explicit
-order fix in the Book output note, since a station view books production
constantly.

Sketch — `/station/<code>`, scoped to one machine, large touch targets, no
dropdown for identity:

- clock in and out of an operation
- start / pause with a mandatory downtime reason / complete
- book output on a numeric keypad with a scrap-reason grid
- issue material by scanning a lot, not by choosing from a list
- a quality check **prompted by the control plan** rather than chosen by the
  operator. The current Quality form has the operator pick the characteristic
  from a `specs` dropdown, which is backwards: the system knows when a check is
  due and should ask for it, and block booking until it is recorded.

Related and separate: `refresh()` re-fetches the whole dashboard summary every
two seconds per open browser, and every station tablet is another client. Worth
measuring before a sixty-machine demo, though the scale pass's shared
computation may already have absorbed it.
