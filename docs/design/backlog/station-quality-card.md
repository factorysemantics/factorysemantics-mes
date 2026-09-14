---
title: Record and see quality results on the station page
status: built
conversation: 9
turns: [25]
route: /dashboard/station
plant: machining.db
created: 2026-09-14
updated: 2026-09-14
branch: feat/station-quality-and-ncr
tags: [fsmes, design, backlog]
---

# Record and see quality results on the station page

**built** — The station screen now records an inspection and shows the recent results for what the machine is running.
## What was asked

Standing on `/dashboard/station`, looking at SAW01's state and queue: quality
results should be recordable and visible here, not only on the Quality screen.

## The judgement

Right, and it was the obvious gap. `/dashboard/station` is the one screen an
operator actually stands at, and it could book output, issue material and
complete a maintenance job — but the inspection they take every half hour
sent them to a supervisor's screen with a plant-wide chart on it.

Nothing new was needed underneath. `POST /quality/checks` already existed and
already required `quality.record`; `GET /quality/specs?material=` and
`GET /quality/checks?material=&characteristic=` already filtered and paged.
The only thing missing on the API was that `GET /workorders/dispatch` did not
say what each operation was making, and a specification is held against a
material, not against a machine — so a screen that only knows the machine
cannot tell which characteristics judge the job in front of it.

## What was built

A Quality card on the station screen, gated on `quality.record`:

- the characteristics that have a specification **for the material this
  machine is running now** — the running operation if there is one, otherwise
  the first thing queued, which is the same choice the Issue panel makes;
- the spec band in words underneath, so the operator sees what they are being
  judged against before they type;
- the last six results with in-spec / out-of-spec marks, the time and who
  recorded them, and the envelope saying how many results there are in all;
- one field and a Record button.

An out-of-spec reading raises the non-conformance exactly as it did before —
that is the MES working — and the card now names it by code and says a
supervisor decides what happens to the material. Before, the operator got a
red toast and no idea whether anything had been created.

## What it deliberately does not do

Only characteristics with a specification are offered. A measurement with
nothing to judge it against cannot pass or fail, and offering one would
invite a reading the MES then has no verdict for. If the machine is running
nothing, the card says so rather than falling back to the whole plant's
specification list.

No gauge is asked for yet. `QualityCheck.gauge_id` exists and the gauge
register is real, but tying a station reading to an instrument is its own
question (which gauges live at which station is master data nobody has
entered), and guessing it would put a gauge on a record that never touched it.

## Verified

The full suite, plus a browser against a seeded bottling plant: an in-spec
reading and an out-of-spec one taken from the station page, both appearing in
the card's recent results and in the Quality screen's inspection history and
chart, with the non-conformance named on both screens. No console errors.
