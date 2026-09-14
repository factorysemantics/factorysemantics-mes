---
title: "Submit a fill weight inspection for 495 on WASH01" said to the assistant
status: built
conversation: 10
turns: [27]
route: /dashboard
plant: bottling.db
created: 2026-09-14
updated: 2026-09-14
branch: feat/station-quality-and-ncr
tags: [fsmes, design, backlog]
---

# "Submit a fill weight inspection for 495 on WASH01" said to the assistant

**built** — The agent already did this under propose/confirm; what was missing was a test saying so, and it is here.
## What was asked

Typed at `/dashboard` as an instruction, not a question: *submit a fill weight
inspection for 495 on WASH01.*

## The judgement

This is the assistant's job, and the plumbing for it was already there — but
it was not the qwen chat that had to answer it, which is why the reply Scott
got was a lecture about REST endpoints. The local model routes "how do I"
questions to guides; an *action* goes to `POST /assist/agent`, which works the
plant's own tools under the propose/confirm discipline.

Note what the sentence actually demands. WASH01 is a **machine**, and a
specification is held against a **material** — so the agent cannot act on the
sentence as written. It has to look the station up first, find what it is
running, and only then propose. That is exactly the shape the tool registry
supports: reads run free, and the one write waits.

## What was built

Nothing in the loop needed changing, and nothing in it was changed. What was
missing was a test saying the sentence works, so there is one now
(`tests/test_agent.py`), with the model scripted so the discipline is what is
under test rather than the model's judgement:

- the reads (`machines`, `quality`) run at once, as the agent;
- `record_check` runs `dry_run=True` and the preview waits;
- nothing is written until the person confirms;
- the confirmed write runs **on their behalf** with the proposal id as its
  idempotency key, so a double click cannot double-record;
- and it reaches `POST /quality/checks` exactly once.

`record_check`'s walkthrough surface now also lists `/dashboard/station`,
since that screen has the control as of conversation 9.

## What it deliberately does not do

The agent never writes directly, and this is not the place to make an
exception for "small" writes. A quality record is somebody's name against a
number; it does not get created because a model was fairly confident.

The walkthrough steps still point at the floor page's form rather than the
station card. The surface is a single authored list of steps and selectors,
and one wrong selector is worse than a walk to a screen that definitely has
the control. Making surfaces page-dependent is its own piece of work.
