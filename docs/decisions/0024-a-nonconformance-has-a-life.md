# 0024 — A non-conformance is worked through review and a disposition, and cannot be closed without one

- **Status:** accepted
- **Date:** 2026-09-14
- **Deciders:** scottkalw

## Context

Until now a `NonConformance` had two states, `open` and `closed`, and one
action: close it. An out-of-spec measurement opened one automatically
(`fsmes.services.quality.record_check`), and a supervisor made it go away.
Nothing recorded who raised it, and nothing at all recorded what happened to
the material.

Scott, looking at `/dashboard/quality` on 2026-09-04 (design chat
conversation 13): *"ncrs should have more functionality then just close
right?"*

That is the whole gap. A plant's non-conformance answers one question —
**what happened to the affected material** — and "closed" does not answer it.
Rework, scrap and return are three different things that happened to three
different piles of stock, and *use as is* is a concession somebody put their
name to. A record that cannot tell those apart is a record a plant cannot
use in an audit, a customer complaint, or a yield number.

The MES was already storing the decision's *effect* nowhere: scrap booked
against the order and a concession look identical in the database.

## Options considered

| Option | For | Against |
|---|---|---|
| Leave `open`/`closed`, add a free-text note on close | One nullable column, no state machine, nothing to migrate | The four dispositions are not prose — they are what a plant reports on, and a text box cannot be counted. The question "how much did we scrap on concession this month" stays unanswerable |
| Status becomes `open → under_review → dispositioned → closed`, with the disposition and a reason as their own fields, and close refused until the material has been decided about | Matches what a plant does and what an auditor asks for; each step carries who and when; the four dispositions can be counted | A behaviour change: `POST /quality/nonconformances/{code}/close` now refuses on an undispositioned record. A state machine is more to get wrong |
| A separate `nc_actions` table, one row per step, status derived | Extensible to steps nobody has thought of; natural history | The history is then only ever queryable, never constrainable — nothing stops two dispositions. And the audit log already holds the append-only record; a second one would drift from it |

## Decision

A non-conformance has four states: **open → under review → dispositioned →
closed**. Review is a step, not a gate: a supervisor who already knows the
answer may disposition an open record without taking it under review first.
Closing is refused until a disposition exists, and the refusal names the step
that is missing rather than the rule that was broken.

The **disposition** is one of `use_as_is`, `rework`, `scrap` or `return`, and
it **requires a reason**. Who took each step and when is stored on the record
(`raised_by`, `reviewed_by`/`reviewed_at`, `disposition_by`/`disposition_at`,
`closed_by`/`closed_at`), and `GET /quality/nonconformances` returns that as
a `history` list alongside the row.

Migration `b1f4c73a9e08` adds eight nullable columns and changes no data.
Rows that already exist keep the status they had and carry null in the new
columns — which is the truth: this MES did not record who reviewed them,
because it did not ask. The screen shows such a step as *not recorded*, never
as `system`.

The disposition records **what was decided**, not what was booked. It does
not scrap stock, move a lot or book production; those are their own actions
with their own audit rows. Deciding and doing are different events and the
MES does not get to conflate them.

## Consequences

Easier: a plant can count its dispositions, and every step on a quality
record has a name and a time against it. The Quality screen drives the
lifecycle, and the agent has `review_nonconformance` and
`disposition_nonconformance` beside `close_nonconformance`.

Harder: anything that closed a non-conformance in one call now takes two.
That is the point, but it is a breaking change to the API in a `0.x` release
and is written up under **Honesty** in the changelog.

To revisit: whether a disposition should be able to *act* — scrap the lot,
raise a rework order — rather than only record. It should not until somebody
has asked for it against a real plant, because a disposition that silently
books stock is exactly the kind of invented production house rule 1 forbids.

## House rules touched

**Never invent production** (1) and **unknown is a valid answer, zero is
not** (2): the migration leaves old rows' new columns null and the screen
says "not recorded" rather than naming a person who did not act. **Config,
not code** (4) is untouched — the four dispositions are the vocabulary of the
model, not a plant's setting. **Tests are prose** (5): `tests/test_quality.py`
names each rule after the behaviour it pins.
