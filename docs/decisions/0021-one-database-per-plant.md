# 0021 — A fleet is many databases, and site scoping is identity, not partitioning

- **Status:** proposed
- **Date:** 2026-09-10
- **Deciders:** @kalwei

## Context
[M8](../design/m8-packs-and-fleet.md) opens with "site scoping on kernel
tables". Read literally that means every kernel table gains a site or plant
column and every query gains a filter. Before proposing anything, the schema
was measured at `2474a8b`.

**38 tables. Zero carry a site, plant or tenant column.**

**25 tables carry at least one uniqueness rule.** Five of those are already
scoped through a foreign key and would survive two plants sharing a database
(`bom_items`, `quality_specs`, `routing_operations`, `work_order_operations`,
`uns_publications`). The other **twenty are plant-scoped natural keys that
would collide**: `equipment.code`, `materials.code`, `routings.code`,
`roles.code`, `personnel.code`, `work_orders.code`, `material_lots.code`,
`gauges.code`, `triggers.code`, `non_conformances.code`,
`maintenance_plans.code`, `maintenance_orders.code`, `shift_patterns.code`,
`recommended_adjustments.code`, `documents(code, revision)`,
`serial_units.serial`, `erp_messages.message_key`,
`idempotency_keys(actor, key)`, `inbound_events(source, kind, external_key)`
and `inbound_watermarks(source, stream)`.

**Four tables are global cursors or counters** with no plant dimension:
`serial_sequences` — whose primary key *is* the serial prefix —
`inbound_watermarks`, `erp_messages` and `uns_publications`.

There are **30 migrations and one head** (`c8b1e40d7a92`).

Two things are already true and were decided earlier. Decision
[0006](0006-sqlite-laptop-postgresql-plant.md) says "one database per plant:
SQLite by default, PostgreSQL by setting `MES_DATABASE_URL`". And
`src/fsmes/plant.py` opens with the argument for it: *"there is no
multi-tenant code path anywhere in the MES, and that is the point — isolation
here is by construction, not by a `WHERE` clause somebody might forget."*
`labs/multiplant/` has run two plants that way since the private era.

What is *not* true is that a plant can say which plant it is.
`MES_PLANT_NAME` exists, defaults to `""`, and surfaces in two places: the
`plant` field of the UNS envelope, and the assistant's greeting. `/health`
does not carry it. `/shadow` does not carry it. `/metrics` does not label
with it. A console polling twelve plants can only tell them apart by the URL
it dialled.

## Options considered
| Option | For | Against |
|---|---|---|
| **One database per plant; "site scoping" becomes a plant identity that every reader can see** | keeps the isolation that already exists and that a recall depends on; the change is a handful of fields, not a schema migration; matches how every deployment already runs (a node per plant, on the plant's own hardware) | a fleet-wide query means asking N plants and joining outside the MES; a plant that genuinely wants two sites in one database is told no |
| Add `site_id` to kernel tables and filter everywhere | one database to back up; cross-site queries are SQL | twenty uniqueness rules to rewrite, four cursor tables to re-key, and a filter that must be right in every query in `src/fsmes/services/` forever. The failure mode is a recall or a genealogy that returns another plant's units, and it is invisible until it matters |
| Both: scope the tables, but keep separate databases the default | flexibility | the worst of it — the filter must still be right everywhere, and it is exercised by nobody, so it rots. An untested `WHERE` is a lie with a schema |
| Add `site_id` only to the four cursor tables | small | it buys the ability to share a database without the safety of sharing one. Half a partition is not a partition |

## Decision
A fleet is **many databases**. One plant, one database, one set of processes,
one pack — as decision [0006](0006-sqlite-laptop-postgresql-plant.md) already
says and as `labs/multiplant/` already runs. No kernel table gains a site,
plant or tenant column, and no query gains a tenant filter.

"Site scoping on kernel tables" is therefore re-read, for M8, as **plant
identity**: a plant states which plant it is, and that answer reaches every
reader outside the process. `MES_PLANT_NAME` is required for any profile but
`laptop`, and it appears in `/health`, `/shadow`, `/metrics` as a label, the
dashboard header, `fsmes info`, the backup manifest and every event envelope.
`MES_PLANT_TIMEZONE` joins it as a real setting, because "which plant" and
"whose clock" are the same question asked twice.

Cross-plant questions are answered by asking N plants and comparing the
answers outside them — which is what the fleet console does
([0023](0023-the-fleet-console-observes.md)), and what the analytics node in
the family has always been for.

## Consequences
Easier: nothing to migrate; the isolation guarantee stays the strong kind. A
plant's database can be handed to somebody, restored somewhere else, or
deleted, without touching another plant. Backup and restore stay one-plant
operations, which is what `fsmes backup` already assumes.

Harder: there is no SQL that spans plants, and there will never be one from
inside the MES. A group that wants "OEE across all sites" gets twelve
numbers and a page that refuses to add them up
([0023](0023-the-fleet-console-observes.md)). A plant with two genuinely
separate sites behind one firewall runs two nodes, and if that turns out to
be a real burden this decision is the one to revisit — with the plant that
found it, not before.

To revisit when somebody runs more than about twenty plants, or when a plant
asks for one database on purpose and can say why.

## House rules touched
Rule 4, config not code at plant boundaries: which database a plant uses is a
key in its pack, and the plant's own name becomes config it must state rather
than a default it can silently inherit.

Rule 2, unknown is a valid answer: an empty `MES_PLANT_NAME` today produces
`"plant": null` on a published event — an event that cannot say where it came
from. Requiring the name outside the laptop profile is that rule applied to
identity.
