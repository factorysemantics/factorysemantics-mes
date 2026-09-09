# 0001 — Apache-2.0 with the DCO, not copyleft, not a CLA

- **Status:** accepted
- **Date:** 2026-08-28
- **Deciders:** @kalwei

## Context
The project needed a licence before the first line of code. Integrators are the adoption channel for plant software; two of the closest industrial open-source comparables (United Manufacturing Hub, frePPLe) both moved *away* from copyleft. The feared failure mode — a closed SaaS strip-mining a niche on-prem MES — has no examples in this niche.

## Options considered
| Option | For | Against |
|---|---|---|
| Apache-2.0 + DCO | permissive, patent grant, integrator-friendly, one line per commit | a competitor may fork and close (accepted) |
| AGPL-3 | protects against closed SaaS forks | integrators and plant IT refuse it; ERP connectors for GPL-adjacent ecosystems get complicated |
| Apache-2.0 + CLA | copyright assignment keeps relicensing trivial | paperwork kills first contributions; the DCO keeps the record clean anyway |

## Decision
The whole repository is Apache-2.0 with a `NOTICE`; contributions carry a `Signed-off-by` line under the Developer Certificate of Origin. Copyright stays with the authors. Changing the licence needs a decision record with a 30-day comment period (GOVERNANCE).

## Consequences
Easy: adoption by integrators and by GPL-licensed neighbours' connectors (a Frappe-side app can be GPL-3 while the MES-side module stays Apache-2.0). Hard: nothing stops a closed fork; the mitigation is the community, not the licence.

## House rules touched
None directly. The provenance rule in CONTRIBUTING is what keeps the licence honest.
