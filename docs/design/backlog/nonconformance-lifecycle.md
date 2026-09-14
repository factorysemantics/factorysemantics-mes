---
title: A non-conformance should do more than close
status: built
conversation: 13
turns: [37]
route: /dashboard/quality
plant: megafactory.db
created: 2026-09-14
updated: 2026-09-14
branch: feat/station-quality-and-ncr
tags: [fsmes, design, backlog]
---

# A non-conformance should do more than close

**built** — Open, under review, a disposition on the material with a written reason, then closed — and it cannot be closed without one. Decision record 0024.
## What was asked

Same message: *"ncrs should have more functionality then just close right?"*

## The judgement

Right, and it was the most serious of the three. A `NonConformance` had two
states — `open` and `closed` — and one action. Nothing recorded who raised it,
and nothing at all recorded what happened to the material.

That last part is the whole point of the record. A plant's non-conformance
answers one question: **what happened to the affected stock.** Rework, scrap
and return are three different things that happened to three different piles,
and *use as is* is a concession somebody put their name to. "Closed" cannot
tell those apart, which means the record is no use in an audit, a customer
complaint, or a yield number.

This touches the data schema, so by the rules of this triage it would normally
be `needs-guidance`. It was written up as a decision record instead and
decided in the open: **docs/decisions/0024**, with the options that lost.

## What was built

States are now **open → under review → dispositioned → closed**.

- **Review** is a step, not a gate. A supervisor who already knows the answer
  may disposition an open record without taking it under review first;
  making them click twice would teach them to click twice.
- **Disposition** is one of `use_as_is`, `rework`, `scrap`, `return`, and
  **requires a reason**. The reason is the thing somebody reads a year later.
- **Closing is refused** until a disposition exists, and the refusal names the
  step that is missing rather than the rule that was broken.
- Who took each step and when is on the record, and the list endpoint returns
  it as a `history` beside each row, with `next_steps` so the screen does not
  re-derive the rules.

The Quality screen drives all of it: *Take under review*, *Decide…* (a form
that will not save without both a disposition and a reason), *Close*, and the
history indented under each row. The status filter became "Still open" —
three states, not one — because a supervisor who takes a record under review
must not watch it vanish out of the list they are working. The tile is now
"Non-conformances not closed" for the same reason: one counting only the
untouched ones would have read *lower* every time somebody started work.

The agent got `review_nonconformance` and `disposition_nonconformance` beside
`close_nonconformance`, all three as proposals.

## Rejected from this idea

The qwen reply suggested assignees, comment threads and file attachments.
None of those were built. Assignment is a workflow question that needs a real
plant's org chart to get right; comments and attachments are a document store,
which this product does not have and should not grow by accident on a
quality screen. If Scott wants any of them they are their own ideas.

## What it deliberately does not do

A disposition records what was **decided**, not what was booked. It scraps no
stock, moves no lot and raises no rework order. Deciding and doing are
different events with different audit rows, and a disposition that silently
booked stock would be inventing production.

## Migration

`b1f4c73a9e08` adds eight nullable columns and changes no data. Existing rows
keep their status and carry null in the new columns — which is the truth: this
MES did not record who reviewed them, because it did not ask. The screen shows
such a step as "not recorded", never as `system`.

## Verified

The full suite; the migration proved up and back down on a SQLite database;
and the whole walk driven from the screen in a browser against a seeded
bottling plant — raised by an out-of-spec reading taken at the station, taken
under review, dispositioned *use as is* with a written reason, then closed,
with every step showing who and when. No console errors.
