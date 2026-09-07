# 0006 — SQLite on a laptop, PostgreSQL in a plant, no historian product

- **Status:** accepted
- **Date:** 2026-08-28
- **Deciders:** @kalwei

## Context
Principle 5: runs anywhere, from a laptop to a fleet. Tag values arrive at hundreds per second on a large line; a time-series product would be the obvious add-on and the obvious operational burden.

## Options considered
| Option | For | Against |
|---|---|---|
| SQLAlchemy over SQLite and PostgreSQL; tag values in the same database with retention | one schema, one migration path, zero-setup laptop mode, the plant's numbers next to the plant's orders | not a historian; long-range trends belong in an analytics node (factorysemantics.com's job) |
| Add TimescaleDB/InfluxDB for tags | purpose-built | a second system to install, back up and secure in a five-person shop |

## Decision
One database per plant: SQLite by default, PostgreSQL by setting `MES_DATABASE_URL`. Tag values are retained for `MES_TAG_RETENTION_DAYS` (14 by default) and pruned hourly by the API process; the latest value per tag is indexed. Nothing in the product is a historian.

## Consequences
Easy: install and operate. Hard: a plant that wants a year of tag history gets told to export events to an analytics node. The cutlery lab measured the approach at ten million serialised pieces a day on PostgreSQL; the numbers are in the lab's README with their conditions.

## House rules touched
Rule 4: the database choice is configuration.
