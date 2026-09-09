---
title: Expand simulation and make the analysis cards scale
status: approved
conversation: 6
turns: [14, 15]
route: /dashboard/analysis
plant: bottling.db
created: 2026-09-02
updated: 2026-09-02
branch: 
tags: [fsmes, design, backlog]
---

# Expand simulation and make the analysis cards scale

**approved** — Folded into the OPC plan: the tag fabric provides the volume, then the cards are redesigned against it.
## The idea

> "We need to expand the simulation to see it's impact on these cards. OEE
> by station doesn't seem like it'll scale."

## Assessment

Approved, folded into the OPC plan
(`2026-09-02-opc-integration-suite.md`): Phase 1 multiplies simulated
signals (~15–25 tags/machine), which is exactly the expansion that will
stress the analysis screen, and Phase 2 carries "tag-count-aware rendering"
for these cards. The concern is validated in advance — OEE-by-station as a
flat list has the same shape the machine-card grid had before the 60-machine
critique. Building the fix before the data exists would be guessing at the
break point; the tag fabric lands first, then this screen is redesigned
against real volume.
