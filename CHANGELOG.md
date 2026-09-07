# Changelog

All notable changes to FactorySemantics MES. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
SemVer, where `0.x` means the API may change with a note here.

Sections: **Added**, **Changed**, **Fixed**, and **Honesty** — anything that
changed what a number *means* (a KPI formula, a state mapping, a counter rule)
goes under Honesty with a migration line, so plant people can find it.

## [Unreleased]

## [0.1.0] — first public release

The first public version. The code was developed privately from 2026-08-28
to 2026-09-07 in 34 pull requests; [docs/history/private-era.md](docs/history/private-era.md)
lists them. Everything below is what a stranger gets on day one, and every
number in it comes from a simulated plant.

### Added
- Kernel: master data, routings, work orders, dispatch, execution with lot
  genealogy, an append-only audit trail, users and roles with capabilities.
- OPC UA connectivity: tag maps from an engineering worksheet, `opc-browse`
  and `opc-verify`, a replay server for simulated lines, triggers as data, a
  write-back *recommendation* queue with three guards, and certificates of
  analysis at the end of the line.
- Downtime and OEE with an explicit *unknown* segment; shift calendars.
- Quality: inspections, holds, non-conformances, SPC with capability withheld
  when the data cannot support it, a gauge register with calibration.
- Maintenance-lite, finite-capacity scheduling with promised dates,
  serialisation and genealogy at ten million pieces a day (the cutlery lab).
- ERP: a typed per-operation contract with an outbox, a REST adapter, a
  B2MML-flavoured file adapter, and an ERPNext connector registered as a
  module (`fsmes.modules` entry point `erpnext`).
- The agent surface: an MCP server with 85 tools that go through the HTTP API
  as the `AGENT` role, dry-run on every write, `on_behalf_of` and idempotency
  keys; the floor assistant in the operator UI; recorded walkthroughs; agent
  evals that ask whether an agent given only the tools can answer what the
  plant knows.
- Operations: plants from a registry (`fsmes plant`), scored runs
  (`fsmes score`) against the simulator's scripted truth, sweeps, a Docker
  image and Compose file, systemd units for test and promoted environments.
- Documentation site (MkDocs), decision records, security policy, code of
  conduct, governance.

### Honesty
- Counters book production by delta only; a counter falling toward zero is a
  reset and books nothing. OEE reports *unknown* rather than zero when the
  MES was not watching. Unlabelled downtime is reported as unlabelled. These
  are the house rules and they are tested.

[Unreleased]: https://github.com/factorysemantics/factorysemantics-mes/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/factorysemantics/factorysemantics-mes/releases/tag/v0.1.0
