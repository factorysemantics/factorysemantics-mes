---
title: Scrap reason codes as master data, with a Pareto that rolls up
status: approved
conversation: 
turns: []
route: /dashboard/quality
plant: bottling.db
created: 2026-10-11
updated: 2026-10-11
branch: 
tags: [fsmes, design, backlog]
---

# Scrap reason codes as master data, with a Pareto that rolls up

**approved** — the companion to
[0041](../../decisions/0041-a-characteristic-the-plant-counts.md) and
deliberately out of its scope. Named here so nobody builds it by accident
inside something else.

## The idea

A counted characteristic now carries a breakdown: `quality_counts.defects`
holds `[{code, count}]` in **the plant's own words** — `low_fill`,
`cap_skew`, whatever its inspectors actually sort rejects into. The product
does not police the vocabulary, does not keep a list of the codes, and does
not refuse one it has not seen. The lot dossier prints the codes it was
given, worst first, with its three totals.

That is enough to chart a count honestly. It is not enough to run a scrap
programme. A scrap programme needs the codes to be a *list*.

## Assessment

What exists: `QualityCount.defects` (JSON, nullable) written by
`POST /quality/counts` and read by `services.spc_point._defects_block`,
which already distinguishes the three facts a breakdown can carry — a null
breakdown is *not broken down*, an empty list is *nothing coded against the
rejects*, and a coded total differing from `rejected` is a unit with two
defects or an inspector who coded some and not all.

What is missing:

- **A managed list.** Codes as master data, in the pack
  (`masterdata/scrap_reasons.json`, beside `downtime_reasons.json` which is
  the precedent) and on a screen, so two inspectors coding the same defect
  use the same word. Today a typo is a new defect.
- **A hierarchy.** `fill/low`, `fill/high`, `closure/skew` — so a Pareto can
  be read at the level somebody is working at and rolled up for the weekly
  review. `downtime_reasons` is flat and has not needed one; a defect list
  will.
- **A Pareto that rolls up.** Across lots, orders, shifts, stations and
  weeks. Every list states its total, and the uncoded remainder is named
  rather than dropped — all three of the facts above have to survive the
  rollup, or it is a chart that quietly rounds the unknown to nought.
- **Where the reason sits on a non-conformance.** A hold raised by a rule
  firing (0027) and a reject coded by an inspector are two different records
  about the same bottles. Which one carries the reason, and whether a
  disposition changes it, needs deciding before anything is written —
  `nonconformance-lifecycle.md` is next door and touches the same question.

## What was done

Nothing. The questions above are the work, and the first of them is not a
schema question: **ask a plant for its list.** A reason-code hierarchy
invented in this repository would be the first thing a plant replaced, and
inventing it would put a vocabulary nobody uses into the master data of
every plant that installs this.
