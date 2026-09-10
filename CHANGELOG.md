# Changelog

All notable changes to FactorySemantics MES. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
SemVer, where `0.x` means the API may change with a note here.

Sections: **Added**, **Changed**, **Fixed**, and **Honesty** — anything that
changed what a number *means* (a KPI formula, a state mapping, a counter rule)
goes under Honesty with a migration line, so plant people can find it.

## [Unreleased]

### Added
- **The test suite runs on PostgreSQL in CI.** Every test ran on in-memory
  SQLite; PostgreSQL was documented, configured and shipped in the Compose
  file, and nothing exercised it. A `postgres` cell now runs
  `alembic upgrade head` against an empty PostgreSQL 16.15 — pinned by
  digest — and then the whole suite against the same server, on every pull
  request. `MES_TEST_DATABASE_URL` points the suite at any database;
  unset, the default is still in-memory SQLite and nothing about running
  `python -m pytest` changes. A test in the suite fails if it is not on the
  database that variable names, so a typo cannot leave the cell green.
  See [compatibility](docs/operate/compatibility.md).
- **`fsmes backup` and `fsmes restore`.** A plant that cannot restore has no
  backup, and until now the only copy anything made was the one
  `fsmes plant <name> migrate` takes before a schema change. `fsmes backup`
  writes one timestamped folder holding the database, the tag map, the line
  layout and the **OPC UA client certificate** — the last is the one people
  forget, because minting a new one means asking whoever administers the OPC
  server to trust it again. On SQLite the copy goes through SQLite's own
  online backup, so it is safe while the agent is running and cannot lose a
  transaction still sitting in the write-ahead log, which a plain file copy
  of the `.db` does. It never copies `.env`: that holds the OPC password, the
  ERP credentials and the token signing key, and a backup folder gets mailed
  around. The manifest states its own totals and names everything it did not
  copy and why. On a server database it copies no database at all and says
  so in those words, rather than handing somebody a folder that looks like a
  backup and has no production record in it. `fsmes restore` checks every
  file against its recorded hash before writing anything, refuses to write
  over a database that is already there without `--force`, has a `--dry-run`
  that proves a backup is restorable without touching the plant, and reads
  the row counts back out of the restored file rather than repeating the
  manifest. Files land where the settings of the machine being restored to
  say — a restore onto a new PC is a different `.env`, not a different
  backup. [Backup and restore](docs/operate/backup.md).
- **The confirmation handoff: a published schema, worked example files and
  a validator.** A plant running this MES in shadow mode has no live ERP
  link, and its people still have to answer whether what it *would* send
  is correct. That answer used to live in Pydantic models, an XML renderer
  and a symmetry test, none of which an ERP analyst can read. Now the
  contract is published as a **JSON Schema, generated from the same models
  that write the files**, with every field carrying what it means on the
  floor, where the MES gets it, and when it is null and why null is the
  honest value ([the contract](docs/reference/erp-confirmations.md)); six
  **worked example files** — a clean operation, one with scrap and consumed
  lots, an over-run, a completion with a lot, one carrying an over-run, one
  with no lot — generated from a run of the demo plant rather than typed,
  as JSON and B2MML side by side, and pinned by a test that regenerates
  them; and **`fsmes erp validate <path>`**, which checks a file or a whole
  outbox against the contract and the house rules with no ERP, no connector
  and no database, exiting non-zero so it can gate a deployment. Problems
  and notes are kept apart: a negative work in progress, a completion with
  no lot and a missing cost centre are reported and do not fail, because
  every one of them is a fact the MES states on purpose.
  [The confirmation handoff](docs/operate/confirmation-files.md) is the page
  for the ERP team, and it says plainly that the contract is SAP-*shaped*
  and that no SAP has consumed one of these.
- **Shadow mode: `MES_SHADOW=true`.** One setting that lets this MES watch a
  real plant and change nothing in it. It reads the OPC UA tags and books
  production exactly as it would in charge; every path by which it could
  reach past its own database is shut. No setpoint reaches a machine (the
  order-code write-back included, which was never approval-gated); no live
  ERP is contacted; nothing is published to a broker; the ERP file adapter
  reads its inbox and moves nothing, because that folder may be the
  incumbent's; the optional cloud model is refused, so the plant's numbers
  stay on the box; `fsmes demo` refuses to run a fake plant beside a real
  one. `MES_ERP_MODE` may only be `off` or `file` and `MES_UNS_MODE` only
  `off` or `log` — anything else refuses to start, in one sentence naming
  the variable. A mode you never set is settled rather than refused.

  Every outbound path in the package is listed in one place,
  `fsmes/shadow.py`, with what shadow mode does to each and why; a test
  walks each closed path and holds the refusal, and a second test scans the
  source for outbound primitives so a new path cannot be added without the
  register hearing about it. It shows on every screen as a bar that cannot
  be dismissed, in `fsmes info`, at `GET /health` and `GET /shadow`, in the
  MCP server's description and per plant in `list_plants()`, and in the
  audit trail as `shadow.on` / `shadow.off` at start-up. There is no runtime
  toggle: leaving shadow mode is a restart.
  See [running beside an existing MES](docs/operate/shadow-mode.md).


- **A second front door: events other systems tell this MES.** The OPC agent
  is how the MES sees a plant; it is not how it learns why a machine stopped,
  what an inspector measured, or how many units somebody counted by hand.
  Those are typed into whatever system the plant already has, and until now
  none of it could reach the MES. `fsmes.integrations.inbound.contract` is
  three typed shapes — **`DowntimeLabel`**, **`QualityResult`**,
  **`ManualCount`** — each carrying who supplied it (`source`, a free name
  such as `replay:incumbent-mes`, plus a `source_kind` category), the
  supplier's own id (`external_key`), and when the *supplier* recorded it
  (`recorded_at`). `fsmes.services.inbound` writes them through the services
  that already own the rules, deduplicating on
  `(source, kind, external_key)`, so the same file delivered twice changes
  nothing. It matters most for a shadow run: technicians label stops in the
  incumbent, and a shadow that never hears those shows an unlabelled stop
  where the incumbent shows a reason code, which reads as a data problem in
  the shadow when it is a plumbing problem.
- **The first inbound driver: files in a folder.** `fsmes inbound watch`
  reads CSV — or the same rows as JSON — from one inbox per event type,
  through a column mapping that is *your* configuration and not code. No
  input file is ever deleted: it moves to `processed/`, or to `rejected/`
  when nothing could be taken from it, and a rejects report beside it states
  the file's totals and the first reason for each row refused. `fsmes
  inbound check` says whether the mapping and the folders are usable before
  any file exists. [Feeding the MES what people typed
  elsewhere](docs/operate/inbound.md) is the page. A SQL poller and an MQTT
  subscriber are the same three shapes over a different transport, and are
  not written.
- `tzdata` is now a dependency **on Windows only**. Windows ships no IANA
  time-zone database, so `zoneinfo` there cannot resolve `America/Chicago`
  — or even `UTC` — without it, and the inbound driver reads a plant's
  exports in the plant's own local time. Linux and macOS have a database
  already and gain nothing.
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
- **The session cookie is marked HTTPS-only behind TLS.** Writing the
  [TLS page](docs/operate/tls.md) turned this up: the dashboard's session
  cookie was `HttpOnly` and `SameSite=Lax` but never `Secure`, so a plant
  that had put a reverse proxy in front of the API could still have a live
  session sent back in clear over one stray `http://` link. The flag now
  follows the request's own scheme, which behind a proxy is the scheme in
  `X-Forwarded-Proto`. A laptop on `http://127.0.0.1:8000` gets no `Secure`
  flag, because there it is a cookie the browser silently drops.

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

### Changed
- **The file connector's outbound folder is deterministic.** Names lead with
  a six-digit sequence number, so sorting the folder by name replays the
  order the confirmations happened; the number is read back from the folder
  at start-up, so a restart continues rather than collides; each document is
  written to a `.part` file and renamed into place, so a collector never
  reads half a document; and anything outside `A-Za-z0-9_-` in an order code
  becomes a dash, so an order code cannot decide where a file lands. A
  collector that globbed `confirmation_<order>_*.xml` must now glob
  `*_<order>_op10.xml` or `*_<order>_completion.xml`.

### Fixed
- **A list search ignored case on SQLite and not on PostgreSQL.** Every list
  search in the API — equipment, materials, routings, people, work orders,
  lots, maintenance plans and orders, specifications, non-conformances,
  certificates, the audit trail — is built on `LIKE`. SQLite's `LIKE`
  ignores case for ASCII and PostgreSQL's does not, so a plant on PostgreSQL
  got nothing back for a code typed in lower case where the same search on a
  laptop found it. They all say `ILIKE` now. Case-insensitive is what the
  product already meant: the document catalogue, the trigger list and the
  gauge register filter in Python on `.lower()`, and the SQL-side searches
  only agreed with them by SQLite's accident. Found by the new PostgreSQL
  cell.
- **The B2MML confirmation carries its idempotency key.** `message_key` — the
  only thing that stops one confirmation being posted twice — was never
  written into the XML, so a folder of operation confirmations gave a
  collector no way to tell a re-sent file from a second confirmation. It is
  the first element of both documents now, and a reader rebuilds it for
  files written before today with the same rule that made it.
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
- **What ERPNext does with an over-run is now measured, and a refusal is
  never recorded as delivered.** The MES books every unit a machine counted,
  so an order for 400 that ran to 420 is confirmed as 420 good with 20 over.
  ERPNext has an over-production allowance of its own (Manufacturing
  Settings, zero out of the box), and nobody had asked it what it does.
  Measured against v15.120.0: inside the allowance it takes the whole
  quantity — `produced_qty` 420, one Manufacture entry of 420. Beyond it, it
  **refuses the entry whole** with HTTP 417 `For quantity 500.0 should not be
  greater than allowed quantity 440.0`, books nothing, and leaves
  `produced_qty` at 0. The connector no longer lets that pass as a transport
  failure: the confirmation is not delivered, and because a retry sends the
  identical entry and gets the identical answer, the message goes straight to
  `dead` in the outbox with ERPNext's own sentence as its error instead of
  spending eight attempts and an hour on it. A comment on the Work Order says
  what was refused, what the MES counted and what ERPNext said, so the person
  who has to decide can see all of it; `custom_mes_good_qty` and
  `custom_mes_over_qty` still carry what the machines counted, beside a
  `produced_qty` of 0. Nothing partial is posted in place of the refused
  entry. Once somebody raises the allowance or agrees what the ERP should
  hold, `POST /erp/outbox/{id}/retry` sends the same confirmation again.
- **A new production source, `external`, because "we counted it" and "we were
  told" are different facts.** `ProductionSource` had `manual` and `opc`; a
  count that reached the MES from another system had nowhere honest to sit,
  and calling it `manual` would have claimed somebody typed it *here*.
  `external` is now the third value, with **`ProductionLog.source_system`**
  naming the system that supplied it. Null there means this MES counted it
  itself — there is no other system to name, and naming one would be a guess.
  **Migration `b5c1d09e73af`** adds `production_logs.source_system`,
  `equipment_states.reason_source`, `quality_checks.source_system` and
  `quality_checks.supplied_result`, and the `inbound_events` ledger. Every
  column is nullable and nothing existing is rewritten: a row from before the
  migration was observed by this MES, and null is the right answer.
  **What to check after upgrading:** any report that groups production by
  source, or that assumes `ProductionSource` has two values, now has a third.
- **A supplied downtime label goes on a stop this MES observed, and never
  creates one.** The interval stays this MES's own observation; `reason` and
  `reason_source` record who named it. A label for a stop the MES never saw
  is refused and reported, because manufacturing an interval from another
  system's claim would put seconds into availability that nothing here ever
  watched, with no way afterwards to tell them from the real ones. A supplied
  label never overwrites one given here.
- **A quality result keeps both verdicts when they disagree.** The `result`
  on a check is always this MES's own, from this MES's spec.
  `supplied_result` keeps the verdict the other system sent. Two systems
  disagreeing about the same reading is a finding about the two systems, and
  storing only one of them would hide it. A reading for a characteristic this
  MES has no spec for is refused rather than measured against an invented
  spec, and a gauge code this MES does not know is reported as untraceable
  rather than created.
- **The downtime pareto says who named each stop.** Every bucket gains
  `labelled_by`, seconds by labeller: `here` is this MES's own, the rest are
  named by supplier. Supplied labels are counted the same as local ones —
  they are real evidence — but they are not the same claim, and a pareto that
  cannot separate them cannot be audited.
- **A count supplied by another system with no order open here is kept, not
  refused.** It is booked against an open operation when there is one, and
  otherwise recorded as unassigned production against the machine with the
  supplying system named — the same rule the OPC path already had, for the
  same reason: the system that took the count had the order, and a unit the
  plant made may not disappear because this MES had nowhere tidy to put it. A
  count typed *into this MES* with no order open is still an error.
- **A timestamp with no time zone, in a stream whose mapping does not say
  which zone that system writes, is rejected row by row.** Reading a local
  timestamp as UTC would move every stop in a shift by hours, silently.
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
