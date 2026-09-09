# 0007 — The public repository starts from a clean first commit

- **Status:** accepted
- **Date:** 2026-09-06
- **Deciders:** @kalwei

## Context
The project was developed privately from 2026-08-28. Its history contained personal paths, a lab password, and wording that identified where the maintainer works — none of it catastrophic, all of it permanent once public.

## Options considered
| Option | For | Against |
|---|---|---|
| Squash to a fresh first commit; keep the private repo as the archive | zero risk of a missed string; the private history is not lost, only not published | `git blame` and the 34 pull-request links do not carry over |
| Rewrite history with `git filter-repo` | keeps the narrative | a missed variant string is public forever |
| Publish as-is | nothing to do | the findings above stay public forever |

## Decision
The public repository's first commit is the cleaned tree at 0.1.0. The private repository stays intact as the archive. The private era's pull requests and tags are listed in `docs/history/private-era.md` so the narrative survives without the diffs.

## Consequences
Easy: a clean start under the organisation. Hard: nothing; a solo author's 146 commits were not the audience's concern.

## House rules touched
None. The provenance rule and the conduct file's no-employer-names clause are the same protection, applied to contributors.
