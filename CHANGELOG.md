# Changelog

All notable changes to FactorySemantics MES. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
SemVer, where `0.x` means the API may change with a note here.

Sections: **Added**, **Changed**, **Fixed**, and **Honesty** — anything that
changed what a number *means* (a KPI formula, a state mapping, a counter rule)
goes under Honesty with a migration line, so plant people can find it.

## [Unreleased]

### Added
- **A unified-namespace publisher.** `fsmes uns publish` relays the MES's
  own event stream — the transactional outbox the ERP connector already
  delivers from — to an MQTT broker as JSON, under an ISA-95 topic tree
  read out of the equipment model (`umh/v1/<enterprise>/<site>/<area>/<line>/<machine>/_mes/<kind>`).
  Broker, credentials, prefix and the enterprise and site names are
  settings; every rung between them comes from the plant's own equipment
  codes, at whatever depth it modelled them. Delivery is at-least-once with
  the outbox's retry, backoff and dead-letter behaviour, and every payload
  carries the event id a consumer dedupes on. The MQTT client is the new
  `[mqtt]` extra, so the core install does not grow a dependency;
  `MES_UNS_MODE=log` prints the whole namespace without a broker.
  `fsmes uns topics` and `fsmes uns queue` show the tree and the backlog.
  Off by default. See [the unified namespace](docs/operate/uns.md).
- CI builds the wheel and runs `fsmes demo` from it in a fresh virtual
  environment in an empty directory, on every pull request and every push to
  `main`. The same check runs against the exact wheel a tag is about to
  publish, before it reaches PyPI. This is the clean-machine install that
  0.1.0 needed and did not get.

### Changed
- `fsmes demo` exits non-zero, with the reason, when its loop does not close:
  the order never completed, the ERP was never told, or no finished lot was
  booked. It used to exit 0 either way, which is why a wheel that booked
  nothing looked like a success. A final line now says which happened. An OEE
  component reported as null is still a closed loop — that is an honest
  answer, not a failure.

### Fixed
- `database is locked` under concurrent writes on SQLite. WAL and
  `busy_timeout` were set and their comment claimed that prevented it; they
  do not. A transaction already open when it first writes has to upgrade to
  SQLite's single write lock, and SQLite refuses that upgrade outright
  instead of waiting — `busy_timeout` covers waiting, not a refusal. The OPC
  agent's booking is that shape: it opens a savepoint per state change and
  writes inside it, which is why one CI run of `fsmes demo` lost two batches
  of plant readings to it. Transactions on SQLite now begin with
  `BEGIN IMMEDIATE`, so the refusal becomes a wait that `busy_timeout` does
  cover. `busy_timeout` is also set before the WAL switch rather than after
  it, so a new connection meeting a lock waits rather than failing. SQLite
  now serialises transactions rather than only writes, so no session may be
  held open across a network call; the OPC agent's adjustment loop was the
  one place doing that, and a test now keeps it that way. PostgreSQL is
  untouched — the change is gated on the SQLite dialect.

## [0.1.2] — 2026-09-08

### Fixed
- `pipx install factorysemantics-mes && fsmes demo` did not run: the wheel
  carried no `config/tag_map.json` or `config/line_layout.json`, so the
  simulator had no line to run and nothing was ever booked. The four
  default config files now ship as package data, and the settings fall
  back to them when the relative path is absent (a path you set yourself is
  never replaced). The release workflow refuses a wheel without them.
- The release workflow's SBOM step asked for an image tag that does not
  exist (#5); the docs workflow serialises pushes to `gh-pages` (#5).

## [0.1.1] — 2026-09-08

### Fixed
- The container image did not build: the Dockerfile copied `pyproject.toml`
  without `LICENSE` and `NOTICE`, and the build backend refuses metadata
  without the licence file. The 0.1.0 release reached PyPI but no image and
  no GitHub Release; this version carries the same code with the image built.
- The Scorecard workflow pinned an action version whose image had moved
  registries (#3).
- The cutlery walkthrough installer failed lint (#3).

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
