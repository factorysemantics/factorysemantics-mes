# 0013 — Governance is one page: one maintainer, proposals in public, and what happens if he stops

- **Status:** accepted
- **Date:** 2026-09-06
- **Deciders:** @kalwei

## Context
The project has one maintainer and expects to for some time. A repository with no governance file makes integrators nervous, because the question they are really asking is what happens to the code if the author disappears. A repository with a governance file describing committees that do not exist answers nothing.

## Options considered
| Option | For | Against |
|---|---|---|
| One page that says one person decides, and what happens if he stops | true today; answers the only question an adopter actually has; nothing to maintain | offers no seat at the table, so a company wanting influence has to ask for one |
| Stages, councils and a contributor ladder | ready for growth; familiar from larger projects | theatre for a solo project, and a contributor discovers it is theatre on their first proposal |
| No governance file | nothing to write | reads as abandoned-in-advance to anyone evaluating the project for a plant |

## Decision
`GOVERNANCE.md` is roughly 200 words and says six things: the maintainer decides; small changes are a pull request and design changes are an Ideas discussion using `docs/decisions/TEMPLATE.md`, open at least a week, whose accepted *or declined* outcome is merged here with its reasons; the licence, the six house rules and the provenance rule change only through a decision record with a 30-day comment period; contributors are people with a merged pull request and any additional maintainers are listed in `MAINTAINERS.md` with what they own; if the maintainer stops, the code stays Apache-2.0, anyone in `MAINTAINERS.md` carries on, and if nobody is listed anyone may fork and continue under the same name; conduct is `CODE_OF_CONDUCT.md`. The role definitions for a larger project are kept outside the repository and stay there until somebody holds a role.

## Consequences
Easy: a proposal has one obvious front door, and the reasoning for every answer lands in this directory whether the answer was yes or no. Hard: decisions are one person's throughput. If two other people become maintainers, one line is added — simple majority, the maintainer breaks ties — and nothing else changes.

## House rules touched
The house rules are named here as one of the three things a pull request cannot change on its own.
