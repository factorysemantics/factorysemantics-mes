# 0016 — No trademark registration for the name

- **Status:** accepted
- **Date:** 2026-09-06
- **Deciders:** @kalwei

## Context
The project publishes under a product name that also appears as a domain, an organisation, a distribution and an MCP namespace. Registration costs money and attention that a pre-1.0 project run in evenings has little of. A search on 2026-09-06 found no conflicting mark, and a direct registry search on 2026-09-07 was clear.

## Options considered
| Option | For | Against |
|---|---|---|
| No registration; rely on common-law rights from public use | free; using the name in public already establishes rights in the jurisdictions that matter here; nothing to renew | no presumption of ownership in a dispute, and someone else could register it first |
| Register a word mark | strongest position if a company ever forms around the name | fees, classes and a multi-year process for a project with no revenue and no competitor for the name |
| Publish a `TRADEMARK.md` usage policy | tells forks what they may call themselves | a policy for a mark nobody is contesting reads as a warning to the first fork, which is the opposite of the licence's intent |

## Decision
No registration and no `TRADEMARK.md`. Public use of the name is the claim. This is revisited only if a company forms around the project or a competitor adopts the name, and either of those is a new decision record rather than a quiet filing.

## Consequences
Easy: nothing to file, nothing to renew, and a fork is governed by the licence (0001) rather than by a naming policy. Hard: if someone else registers the name first, the project's position rests on evidence of prior public use — which is why the public repository, the package index entry and the documentation site all carry dates.

## House rules touched
None.
