# The design backlog

One file per idea. Ideas arrive from the **Design** button on the screens
(`fsmes.services.design`), where the on-device model answers and every
conversation is kept. `/design-triage` reads the conversations nobody has
judged yet, splits them into distinct ideas, judges each one, and writes it
here.

They live in the repository rather than in the Obsidian vault because the
chat database, the code and the agent are all on `main`; a laptop-side
backlog would need two-way sync for every status change. Here they are
versioned, they travel to the private remote, and a note lands in the same
commit as the work it caused. `fsmes-reports` mirrors this folder into
`~/Documents/Vault/Design/Backlog/` one way, for reading in Obsidian.

## The shape of a note

```markdown
---
title: Filter orders by material and date range
status: built
conversation: 2
turns: [7, 8]
route: /dashboard/orders
plant: machining.db
created: 2026-08-31
updated: 2026-08-31
branch: design/orders-filters
tags: [fsmes, design, backlog]
---

# Filter orders by material and date range

**built** — the API already had the filters; the screen never surfaced them.

## The idea

What Scott actually asked for, in his terms.

## Assessment

Judged against the architecture, the data schema and the ratified decisions.
Names real files, real columns, real endpoints — an assessment that could
have been written without reading the code is not an assessment.

## What was done

The branch, what changed, what the tests say. Or, for `needs-guidance`, the
specific questions. Or, for `rejected`, the decision it conflicts with.
```

## Statuses

| Status | Means |
|---|---|
| `inbox` | extracted from a conversation, not yet judged |
| `approved` | worth building, not built yet |
| `in-progress` | being built now |
| `built` | implemented on a branch with the suite green — **awaiting review and merge** |
| `needs-guidance` | sound but underspecified, or it touches the schema; the questions are in the note |
| `rejected` | conflicts with something already ratified; the note says what |
| `done` | merged into `main` |

`built` is deliberately not `done`. An agent may put work on a branch; only a
person merges it, so a backlog must never claim unreviewed work is finished.

## Commands

```bash
fsmes design-pending          # conversations nobody has judged yet
fsmes design-pending --all    # re-read judged ones too (an interrupted run)
fsmes design-backlog          # every idea and where it got to
fsmes design-backlog --status needs-guidance
```

`fsmes design-verdict` writes a note **and** appends the verdict into the
conversation it came from, so opening that screen's Design panel shows what
happened to what you said. It is normally run by `/design-triage` rather than
by hand.

The plan of record is
`~/Documents/Vault/Plans/2026-08-31-design-feedback-pipeline.md` on the laptop.
