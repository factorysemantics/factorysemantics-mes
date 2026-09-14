---
title: The measurements card needs filtering — too many characteristics
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

# The measurements card needs filtering — too many characteristics

**built** — Already done by the plant-scale pass: material then characteristic, with the series read from the server.
## What was asked

On `/dashboard/quality`, looking at the megafactory plant: *"measurements card
definitely needs filtering, there are way too many tags."*

## The judgement

Right, and already done — by the plant-scale pass, before this triage read the
conversation. Worth recording anyway, because the note is the answer to "what
happened to that."

At 127 specifications the strip of characteristic tabs was a wall, and the
chart's series was a slice of the last 200 checks across the whole plant,
which is not a measurement of anything. The screen now picks a material first
and then a characteristic, and the chart reads its own series from
`GET /quality/spc/{material}/{characteristic}` rather than filtering a shared
list in the browser. The tab strip is only the characteristics of the chosen
material, and the count line says how many that is out of the plant's total.

## What is still true

It scales to the megafactory plant this was raised on. If the *material* list
itself becomes the wall — a plant with thousands of finished goods — the
material picker will need a search rather than a dropdown. Nobody has hit that
yet, so it is not being built on a guess.
