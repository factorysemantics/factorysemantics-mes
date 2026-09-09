---
name: design-triage
description: Judge design feedback captured by the in-app Design chat and turn it into work — build the small clear wins on a branch, ask about the underspecified ones, reject what conflicts with a ratified decision. Use when the user says "/design-triage", "triage the design feedback", or asks what to do with ideas raised in the Design panel.
---

# Design triage

The Design button on every screen opens a chat answered by qwen3:8b on this
machine. It is a **capture** surface — it records what Scott noticed while
looking at a screen, and it is not where design gets decided. Deciding is this
skill's job, because it needs the architecture, the data schema and the
ratified plan in view at once.

Run this from a Claude Code session on `main`, in
the primary checkout.

## 1. Read what is waiting

```bash
.venv/bin/fsmes design-pending
```

Nothing pending means nothing to do — say so and stop. Otherwise you get each
unjudged conversation as it was held: the screen, the plant, and every turn in
order with its id.

Add `--all` only when a previous run was interrupted and you need to re-read a
conversation whose ideas were partly written up. `design-pending` tells you
when a conversation already has a backlog note.

## 2. Split each conversation into distinct ideas

One message often carries several. Scott's second conversation held five:
more machines, order filters and dates, supervisor drill-down, order actions,
and what "Report production" means. **Each becomes its own note** — a single
note covering five ideas cannot carry five statuses, and four of them get lost.

Ignore the qwen replies except as context. They are a weaker design partner
and you are re-judging the question, not reviewing its answer.

## 3. Judge each idea

Read before deciding — an assessment that could have been written without
opening the code is not an assessment:

- `docs/ARCHITECTURE.md` and `ROADMAP.md` — what this product is and is not.
- `src/fsmes/domain/` — the data schema. Whether an idea needs a new column,
  a new table or a migration is the single most important question you answer.
- The screen's own source in `src/fsmes/web/`, and the endpoints in
  `src/fsmes/api/routers/`. Very often the API can already do what the screen
  cannot — that difference decides how big the work is.
- `~/Documents/Vault/Plans/2026-08-30-factorysemantics-mes-convergence.md`
  (laptop, readable over `ssh laptop-hacker` if reachable; otherwise its
  ratified decisions are summarised in `docs/ARCHITECTURE.md`). Its
  **Decisions** section is the strategic direction, and its **Next actions**
  list may already contain the idea — say so when it does.

Judge against the plant this is really for: **300 people, 60 machines, two
shifts, seven days a week.** An idea that only works for a demo is not a good
idea, and neither is one that only a supervisor could use on a screen an
operator has to stand at.

Then pick exactly one verdict.

### `built` — do the work now

Only when **all** of these hold:

- it is clear what "right" looks like, without asking Scott anything;
- it needs **no schema change** — no new table, no new column, no migration;
- it does not contradict a ratified decision;
- it is roughly two files of real change or fewer, plus tests.

Then: branch from `main` as `design/<slug>`, implement it, run the **full**
suite (`.venv/bin/python -m pytest`), commit, and push. Never merge, never
push to `main` — a person merges.

If the suite does not pass, the verdict is not `built`. Fix it or downgrade
the idea to `needs-guidance` and say what went wrong.

### `needs-guidance` — write the questions

The idea is sound but you cannot build it correctly without a decision only
Scott can make, **or** it touches the data schema, **or** it is cross-cutting
architecture. Schema and architecture changes are *always* this verdict, no
matter how obvious they look: those are the changes that hurt to undo.

Write the specific questions. "How should this work?" is not a question;
"should a concern be its own object with its own lifecycle, or a flag on the
order?" is.

### `approved` — right, but too big for one run

Clearly worth doing, no schema question, but larger than a couple of files.
Write the assessment and the implementation sketch, and leave it for a later
run or a planning session. Do not half-build it.

### `rejected` — say what it conflicts with

Name the ratified decision, the principle, or the 300-person reality it breaks.
Be respectful and concrete: Scott is the one who had the idea, and a rejection
he cannot check is just a refusal. If you are rejecting only *part* of an idea,
split it — reject that part, and judge the rest on its own.

## 4. Record it

For each idea, write the assessment to a file, then:

```bash
.venv/bin/fsmes design-verdict \
  --conversation 2 \
  --slug orders-filters \
  --title "Filter orders by material and date range" \
  --status built \
  --summary "The API already had the filters; the screen never surfaced them." \
  --turns 7,8 \
  --branch design/orders-filters \
  --body-file /tmp/assessment.md \
  --say /tmp/reply.md
```

This writes `docs/design/backlog/<slug>.md` **and** appends the verdict into
the conversation, so opening that screen's Design panel shows what happened.
It then advances the mark for that conversation.

`--say` is what Scott reads in the little chat panel. Keep it short — a few
sentences, plain language, saying what happened and what he needs to do, if
anything. The reasoning belongs in the note, which he reads in Obsidian. If a
conversation produced several ideas, write **one** `--say` covering all of them
on the last verdict and pass `--no-chat` on the others, so the panel gets one
readable reply rather than five.

Commit the backlog notes on the branch you built, or on `design/backlog` if
nothing was built.

## 5. Tell Scott

Finish with, in plain language: what came in, what you built and on which
branch, what you need him to decide, and what you turned down and why. He has
not read the notes.

## Rules

- Never merge to `main`; never push to `main`.
- Never write into a plant database. `design.db` is dev tooling and may be
  written through `fsmes.services.triage`, never by hand.
- A schema change is always `needs-guidance`.
- Do not invent ideas Scott did not raise. Triage judges what was said.
- The full suite must pass before any verdict is `built`.
