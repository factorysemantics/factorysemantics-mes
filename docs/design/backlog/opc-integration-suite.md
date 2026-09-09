---
title: An OPC integration and analysis suite
status: approved
conversation: 7
turns: [16, 17]
route: /dashboard/line
plant: bottling.db
created: 2026-09-02
updated: 2026-09-02
branch: 
tags: [fsmes, design, backlog]
---

# An OPC integration and analysis suite

**approved** — Approved as its own plan of record: tag fabric, engineering screen + line hover, triggers, gated write-back, CoA.
## The idea

> "could OPC tags be created and simulated for each machine and a whole OPC
> analysis suite be provided? I'd think this line view would be an amazing
> place to be able to hover over each machine and see the health of it by
> reading in different opc signals."

Expanded the same day into the full direction statement: rich tag
simulation, triggers acting on tags, human-gated PLC write-back, AI-proposed
setpoint adjustments, and a certificate of analysis fired by an end-of-line
tag.

## Assessment

Approved — as the centerpiece of its own plan of record:
`~/Documents/Vault/Plans/2026-09-02-opc-integration-suite.md` (laptop).
Six phases; the acceptance test is the washer story end to end (drift →
correlation → recommendation → human approval → bounded write → physics
respond → audit tells it all).

On the on-device model's flat "No" to this conversation: half right, half
wrong. Right that deep tag browsing/trending belongs on a dedicated screen
(`/dashboard/tags`, Phase 2). Wrong that the line view should stay
OPC-free — hover-health showing a machine's key signals is exactly what a
wall display wants, and is in the same phase. A useful reminder that the
chat's reply is capture context, never the judgment.

Too large and too schema-heavy to build from triage: Phases 3 (triggers)
and 4 (write-back) add tables and an approval capability, which always
stops for Scott — ratified here by the planning interview of 2026-09-02.
