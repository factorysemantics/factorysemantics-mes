---
title: A detail drawer so a supervisor can inspect an entity
status: approved
conversation: 2
turns: [3, 4]
route: /dashboard
plant: machining.db
created: 2026-09-01
updated: 2026-09-01
branch: 
tags: [fsmes, design, backlog]
---

# A detail drawer so a supervisor can inspect an entity

**approved** — Nothing on the floor screen is clickable; the data exists and needs no schema change.
## The idea

> "How can a superviser inspect the data?"

## Assessment

Correct, and entirely unaddressed. Nothing on the floor dashboard is clickable
except the action buttons. A supervisor asking "why is this machine at 96 %
quality with five open non-conformances" has no path forward from that screen.

The data is all there and **needs no schema change**: `ProductionLog` for
bookings, `LotConsumption` for genealogy, `QualityCheck` against `QualitySpec`
for the trend, `EquipmentState` for downtime, `AuditLog` filtered by entity.
What is missing is somewhere to put it and endpoints scoped to one entity.

Approved rather than built: it is a new UI pattern (a slide-over, so the
four-second poll keeps running behind it), several new endpoints, and a
decision about which entities open one. That is more than one unattended change
should attempt.

Sketch:

- opens on a machine row, an order row, or a non-conformance line
- tabs: Bookings · Material · Quality · Downtime · Audit
- each tab reads an existing model through a new entity-scoped endpoint
- quality as a trend against the spec band, not five rows of text

Two smaller defects were reported in the same conversation and are worth
confirming while building this — **not re-verified here**: a lot dropdown
rendering a raw float (`LOT-BAR-001 (74.20000000000171)`) where it wants
rounding and a unit, and a machine sitting RUNNING with no order, which should
read as an exception on the floor view rather than as a dash.
