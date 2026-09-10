# Changelog

All notable changes to FactorySemantics MES. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
SemVer, where `0.x` means the API may change with a note here.

Sections: **Added**, **Changed**, **Fixed**, and **Honesty** — anything that
changed what a number *means* (a KPI formula, a state mapping, a counter rule)
goes under Honesty with a migration line, so plant people can find it.

## [Unreleased]

### Added
- **A connector contract, so the next ERP is not the first one all over
  again.** The ERP port had three methods — fetch, acknowledge, confirm —
  and they said nothing about the three things that actually bit the
  ERPNext connector: what has to exist on the far side, how a write is
  proved to have landed, and how a person checks it before trusting it.
  Each was fixed in ERPNext-specific code, so Odoo or SAP or Oracle would
  have rediscovered all three. The port now carries **`requirements()`**
  (what this connector needs on the ERP side, as data a person can act on),
  **`setup()`** (create or verify it, idempotently) and **`check()`**
  (connectivity, credentials and requirements in plain language, non-zero
  when something is wrong). All three have defaults, so a transport that
  needs nothing — and a connector written against the older three-method
  port — keeps working. **`fsmes.integrations.erp.conformance`** ships
  inside the package: eight obligations any connector can be run against
  without vendoring this project's tests, each of them something a
  connector got wrong once. Every adapter that ships passes it.
  [Writing an ERP connector](docs/develop/erp-connectors.md) is the page
  for the person who would write the next one, and decision record
  [0020](docs/decisions/0020-what-supported-means-for-an-erp-connector.md)
  says what this project requires before it calls one supported. Odoo, SAP
  and Oracle are still not written; the contract and the suite exist, the
  connectors do not.
- **`fsmes erp requirements`.** What the configured connector needs on the
  ERP side, and which of it the MES can create itself — the list to send
  whoever administers the ERP, who is usually not the person running the
  MES.
- **The outbox is a domain event log.** It carried ERP confirmations and
  nothing else, because the ERP sync read every pending row as one. It now
  selects the kinds its contract can parse, which leaves room for the plant
  events no ERP asked for: **equipment state changes** (the state entered,
  the reason, the state left and how long that had been open) and **order
  holds and resumes**, with the reason a hold always carries. Each is
  written in the same transaction as the fact it describes, so an event
  cannot exist without its fact or a fact without its event. The
  unified-namespace publisher picks them up with no change: a machine going
  down reaches
  `umh/v1/<enterprise>/<site>/…/<machine>/_mes/equipment_state_change`, and
  a hold reaches the site. `MES_OUTBOX_DOMAIN_EVENTS=false` keeps the log to
  what the ERP is owed. See [the unified namespace](docs/operate/uns.md).
- **`fsmes erp setup` and `fsmes erp check`.** The ERPNext connector needs
  five custom fields on Work Order (`custom_mes_synced`,
  `custom_mes_good_qty`, `custom_mes_scrap_qty`, `custom_mes_over_qty`,
  `custom_mes_lot`). Until now the only thing that created them was a demo
  seeding script in `labs/`, which is not in the wheel — so nobody who
  installed the package could create them at all. The definitions moved into
  the package, `fsmes erp setup` creates any that are missing and is safe to
  run twice, and `fsmes erp check` says in words whether the URL, the
  credentials, all five fields and the company are each in order, exiting
  non-zero if the connector would not work. `fsmes run-erp-sync` runs the field check when it
  starts and says so on the console; it still starts, because an ERP that is
  briefly unreachable is not a reason to refuse to run. The `labs/` seeder
  now uses the same definitions.
  See [the ERPNext connector](docs/operate/erpnext.md).
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
- **The ERPNext connector is tested against a real ERPNext.** A new
  `ERPNext (live)` job stands up ERPNext v15.120.0 in containers pinned to
  image digests (`labs/erpnext/`), seeds the plant, and runs the whole round
  trip: a Work Order submitted in ERPNext, pulled by the connector,
  acknowledged, confirmed, and then checked by reading ERPNext's own
  documents back — the custom fields, `produced_qty`, one submitted
  Manufacture stock entry, the comment. A retried confirmation is asserted to
  leave exactly one stock entry. The old live test fetched orders, asserted a
  list, and was deselected by CI's own settings, so it had never run. The job
  is not on every pull request: it takes about three and a half minutes and
  runs only when the connector, its tests or its fixture change. See
  [the ERPNext connector](docs/operate/erpnext.md).
- CI builds the wheel and runs `fsmes demo` from it in a fresh virtual
  environment in an empty directory, on every pull request and every push to
  `main`. The same check runs against the exact wheel a tag is about to
  publish, before it reaches PyPI. This is the clean-machine install that
  0.1.0 needed and did not get.

### Fixed
- **`fsmes --version` told you the wrong version.** A 0.1.2 install answered
  `0.1.0`, because the number was written down twice — in `pyproject.toml`
  and again in `src/fsmes/__init__.py` — and only one copy was bumped for
  either release. It is written down once now: `src/fsmes/__init__.py` holds
  it, and hatchling stamps the wheel, the sdist and the container tag from
  that line, so a release bumps one file. The first question a new user asks
  their install now gets a true answer. A test compares what the CLI prints
  with the installed distribution's metadata, and the wheel check that runs
  on every pull request asks the built artifact the same question two ways
  and fails if the answers differ, so a mismatch cannot reach PyPI.
  `CITATION.cff` still carries a hand-typed version — the citation format has
  no way to read one from the package — so it was corrected from the stale
  `0.1.0` to `0.1.2`, and the release workflow now refuses a tag that
  disagrees with it.
### Honesty
- **What a missing ERPNext custom field does is now measured, not assumed.**
  Against ERPNext v15.120.0: a `PUT` to a submitted Work Order naming a field
  the doctype does not have returns `200`, and the value is neither stored nor
  returned. A site missing one of the MES fields therefore accepts a
  confirmation and silently loses whichever number that field carried. An
  HTTP success is not proof a value landed. The experiment runs on every live
  job, so a change of behaviour in ERPNext shows up there.
- `docs/operate/compatibility.md` says ERPNext v15.120.0 is tested
  continuously and v16 is untested, in place of "tested against a development
  bench, not yet against a current stable release in a clean container".
- **`fsmes erp outbox` counts the ERP's own queue, not the whole log.** The
  same table now holds plant events waiting for a different reader, and
  counting those as pending ERP work would report a backlog that does not
  exist. The status counts cover the confirmation kinds only; the rest are
  stated as `other_outbound` and broken down by kind, so nothing is hidden
  either.

### Changed
- **`fsmes erp setup` and `fsmes erp check` no longer know that ERPNext
  exists.** They are the port's `setup()` and `check()`, so they act on
  whatever `MES_ERP_MODE` names — including a connector published on its
  own. They used to refuse every mode but `erpnext`, which was exactly
  backwards. `fsmes run-erp-sync` runs the configured connector's `check()`
  at start-up instead of ERPNext's field check, and still starts, because
  an ERP that is briefly unreachable is not a reason to refuse to run.
  `check` reports what it could not verify as **unknown** rather than
  counting it as working: the REST connector can prove it reached the ERP's
  order list and cannot prove the confirmation endpoint works without
  posting a confirmation, so a green check that skipped something says
  `Ready, as far as anything above was checked.`
- **[Compatibility](docs/operate/compatibility.md) has a rule for
  connectors.** Every connector row states the exact version tested and the
  date it was tested, and a connector with no live test says so in those
  words. Three words are defined there and nothing uses others: supported,
  contributed, experimental.
- `MES_ERPNEXT_COMPANY` now does something. It was defined and documented and
  nothing read it, so a shared Frappe bench handed this MES every company's
  work orders. Inbound orders are filtered by it. Its default changed from
  the demo's company (`ACME Beverages`) to empty, which means every company
  on the site — the right answer for a single-company ERPNext, and better
  than a default that silently imports nothing on a stranger's site. A bench
  holding more than one company's books must now set it.
- `fsmes demo` exits non-zero, with the reason, when its loop does not close:
  the order never completed, the ERP was never told, or no finished lot was
  booked. It used to exit 0 either way, which is why a wheel that booked
  nothing looked like a success. A final line now says which happened. An OEE
  component reported as null is still a closed loop — that is an honest
  answer, not a failure.

### Fixed
- The ERPNext connector never acknowledged an order. Its `fetch_orders`
  returned plain dicts while the sync worker reads `request.code` off each
  one, so every inbound order raised `AttributeError` immediately after
  being imported, `custom_mes_synced` was never set, and the same order was
  re-imported on every poll. It now returns the `ProductionRequest` the
  adapter contract declares, and a test acknowledges what `fetch_orders`
  returned so the two cannot drift apart again.

### Honesty
- **`docs/operate/erpnext.md` said the connector worked "with nothing
  installed on the ERPNext side". It did not.** It has always needed four
  custom fields on Work Order. The page now names them, says what creates
  them, and says what happens when they are absent.
- **A confirmation to an ERPNext missing a field was recorded as delivered.**
  Frappe answers `200` to a `PUT` naming a field its doctype does not have
  and drops the value, so the MES marked the outbox message sent and the
  plant's counted quantity existed nowhere. Every write to a Work Order is
  now read back and compared with what was sent; a value that did not
  survive raises, so the confirmation stays in the outbox and retries and
  the log names the field. Rounding to the site's float precision is not
  treated as a loss. That Frappe drops the field silently is what its
  document layer does when read, but is **not verified against a live
  ERPNext** — see the connector page.

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
### Honesty
- **A unit a machine counted is never discarded.** A counter delta can carry
  more than one unit, so a booking can straddle the ordered quantity: the
  release check saw `16/15 good` on an order for fifteen, and the next unit
  the machine counted became a warning line and nothing else. All sixteen
  are booked, as before — the machine made them — and the order now says how
  far past it ran (`over_qty`, in `GET /workorders/{code}` and in the ERP
  order completion). Units counted when no operation is open are recorded as
  **unassigned production**: kept against the machine, with no guess about
  which order they belonged to, and listed with their totals at
  `GET /execution/unassigned` and on the machine page's Operate tab.
  See [decision 0019](docs/decisions/0019-count-everything-the-machine-counted.md).
  Migration: `a3f6c81d09e2` makes `production_logs.work_order_id` nullable.
  Existing rows are untouched. A plant that has been reading
  `SUM(production_logs.good_qty)` as order-attributed production should now
  filter on `work_order_id IS NOT NULL`, or read the wider number knowing
  what it includes.

### Fixed
- `fsmes demo` crashed with `KeyError: 'lot'` at the end of a run. It printed
  whichever ERP confirmation happened to arrive last, and an operation
  confirmation has no finished-goods lot because an operation does not make
  one. It now looks for the order completion for its own order, and says so
  plainly if the completion has not arrived rather than crashing.

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
