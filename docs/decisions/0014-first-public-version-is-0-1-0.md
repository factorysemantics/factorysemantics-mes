# 0014 — The first public version is 0.1.0, cut only when a readiness checklist is green

- **Status:** accepted
- **Date:** 2026-09-06
- **Deciders:** @kalwei

## Context
The project had been developed privately since 2026-08-28 and carried no public version. Two questions were tangled together: what to call the first release, and when it may be cut. The private era's own tags had run past `0.3`, and the tree still contained personal paths and wording that would be permanent once published.

## Options considered
| Option | For | Against |
|---|---|---|
| `0.1.0`, gated on a written checklist | the number matches what a first public release of anything is; the gate is a list of facts, so "ready" is checkable rather than a mood | resets the version the private history had reached, so the private tags and the public ones do not line up |
| `1.0.0` | signals confidence to a plant evaluating it | a promise about API stability the project cannot keep yet, and it can only be made once |
| Continue the private tag sequence | one continuous history | the public repository starts from a clean first commit (0007); a `0.3.1` with one commit behind it invites the wrong question |

## Decision
The first public release is `0.1.0`. It is cut only when a readiness checklist is green in three parts: what must be fixed by a commit (no employer or workplace reference anywhere, no personal paths, no stale provenance claims, lab-only credentials named as such and logged when in force), what must exist as a file (security policy, conduct, governance, maintainers, changelog, citation, issue forms, CI workflows, and the documentation site), and what needs a click nobody else can make (the package index publisher, repository visibility, branch protection, the documentation domain). Readiness is not permission: publication is a separate, explicit go-ahead.

## Consequences
Easy: the argument about whether the project is ready becomes a list somebody can read. Hard: the checklist is only as good as the checking, and one line of it — build the wheel and run it from a clean install before tagging — was skipped on release day and cost three tags in one evening (0018).

## House rules touched
None.
