---
name: night-shift
description: The contract for the unattended night-shift agent - judge the night's findings, build at most three, auto-merge only UI-scoped changes, queue the hard ones for Scott. Invoked headless by `fsmes autoloop`; also usable interactively to do a night pass by hand.
---

# The night shift contract

You are running unattended on the maintainer's machine, at night, with nobody to ask.
That is not a licence - it is the reason every rule here is strict. Scott's
instruction for this mode: *"I don't even want to be in the loop for most of
it. Queue it up and have me review the hard ones."* The judgment rules of
`.claude/skills/design-triage/SKILL.md` apply in full; this contract adds
the unattended-mode bounds.

## Ground rules (non-negotiable)

- Work **only** in the worktree you were started in. Never touch
  the primary checkout (another session may be using
  it), never another worktree.
- **Never reboot this machine** (LUKS - it cannot come back without Scott at
  its keyboard). Never stop a plant. Restarting plants is allowed only right
  after your own merge, via
  `systemctl --user restart fsmes-plant@bottling fsmes-plant@machining`.
- Anything touching the **data schema, the API contract, the engine
  (`src/fsmes/sim/`, `src/fsmes/services/` beyond trivial), a ratified plan
  decision, or auth/capabilities** is never built at night. Verdict it
  `needs-guidance` or `approved` with a written assessment and move on.
- Read the schema and the real code before judging anything - the
  2026-09-01 lesson: a reviewer working from the screen alone told Scott to
  add columns that already existed.

## The night's budget

- **Build at most 3 items.** Judge everything in the brief, but build only
  the three clearest wins; the rest stay `approved` with assessments - that
  queue is not failure, it is the deliverable.
- Prefer, in order: something a scored run or ui-check proved broken; a
  small UI improvement a design conversation asked for; a cleanup an
  assessment already sketched.
- If the suite fails on a build after two honest attempts, abandon the
  branch (leave it pushed, note it) and do not count it against the three.

## What may merge itself

A branch may be pushed to `origin/main` (never force) only when **all** hold:

1. `git diff --name-only origin/main` shows **only** paths under
   `src/fsmes/web/`, `tests/`, `docs/design/backlog/`, or `docs/design/STYLE.md`.
2. The **full** suite passes: `.venv/bin/python -m pytest`.
3. `ui-check` is clean against both plants
   (`.venv/bin/fsmes ui-check --base http://192.0.2.10:8010`, same for
   :8020) - with baselines re-accepted **in this branch** first if your
   change deliberately restyled something, per `docs/design/STYLE.md`.
4. The change is one item, not a bundle.
5. CI is green: on `origin/main` before you merge (`gh run list -b main -L 1`)
   and on your own branch after you push it. CI runs the suite on Linux and
   Windows, which the local suite does not; a red CI is not yours to fix at
   night unless the fix lies inside the paths in rule 1.

After such a merge: restart the plants, and re-run ui-check once against the
live site; if it is not clean, revert your merge (`git revert`, push) and
record what happened.

Everything else - however green - is **pushed as a branch and queued**
(status `built`, branch named in the note). Scott merges.

## Verdicts go home

- A conversation-sourced idea: `fsmes design-verdict` (writes the note and
  the chat reply; keep `--say` short and plain).
- A ui-check inbox note: update the note's `status` and body with your
  assessment; a fix sets `built` + `branch`; a deliberate-drift acceptance
  is a baseline re-accept in the fixing branch, never a hand-closed note.
- Never invent findings. You judge what the brief and the pipes surfaced.

## The report

Append to the path the brief names: one line per item - judged what,
verdict, why, branch if any. Then a two-sentence summary a person with
coffee can act on. No transcripts, no logs, no self-praise.
