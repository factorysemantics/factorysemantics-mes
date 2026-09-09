---
title: The station view shows SPC relevant to downstream quality
status: approved
conversation: 4
turns: [10, 11]
route: /dashboard/station
plant: machining.db
created: 2026-09-02
updated: 2026-09-02
branch: 
tags: [fsmes, design, backlog]
---

# The station view shows SPC relevant to downstream quality

**approved** — Data model supports it today; queued pending the relevance rule; lands with OPC Phase 2.
## The idea

> "the station should show SPC for quality inspections resulting downstream
> and relevant to their work. Could this be modeled, simulated, and
> implemented?"

## Assessment

Approved. SPC shipped 2026-09-02 (M3 complete) and the station view exists —
this idea connects them: an operator at one machine seeing the control chart
for the characteristics *their* operation influences downstream. The data
model supports it today; the work is UI plus one scoped endpoint, no schema.
Queued rather than built tonight because the right presentation (which
characteristics count as "relevant to their work" — routing-downstream, or
control-plan-anchored?) deserves a decision, and it naturally lands with
the OPC plan's Phase 2 station/hover work
(`2026-09-02-opc-integration-suite.md`). A strong early candidate for a
night-shift build once the relevance rule is chosen.
