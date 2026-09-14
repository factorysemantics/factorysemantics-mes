---
title: The specifications card needs filtering
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

# The specifications card needs filtering

**built** — Already done by the plant-scale pass: server-side search, material filter and paging that states its total.
## What was asked

Same message: *"specifications needs filtering."*

## The judgement

Right, and already done by the plant-scale pass. The Specifications card has a
free-text box matching a material code or a characteristic, a material
dropdown, server-side paging, and a count line saying which slice of the whole
is on screen (`1–25 of 127`, not `25`). The filtering happens in
`GET /quality/specs`, not in the browser, so it is the same at 127
specifications and at 12,000.

## What is still true

There is no form on this screen for *creating* a specification — that is
`POST /quality/specs`, which the API has and no screen calls. It is on the
surfaces plan (phase 5) and is not part of this idea.
