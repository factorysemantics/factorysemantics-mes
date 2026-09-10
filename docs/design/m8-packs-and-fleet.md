# M8 — plant packs and the fleet console

*Design. Written 2026-09-10 against the code at `2474a8b`, before anything is
built. It states what exists, proposes what a pack should be and what the
console should do, and breaks the work into four pieces. It decides nothing:
the decisions it turns on are
[0021](../decisions/0021-one-database-per-plant.md),
[0022](../decisions/0022-what-a-plant-pack-may-contain.md) and
[0023](../decisions/0023-the-fleet-console-observes.md), and all three are
**proposed** — the person who accepts them has not read them yet.*

M8 is the last milestone on the
[roadmap](https://github.com/factorysemantics/factorysemantics-mes/blob/main/ROADMAP.md)
and the vaguest. Its scope line asks for six things: site scoping on kernel
tables; plant packs as a `plant.toml`; an adversarial second demo pack; two
guard tests named `no_tenant_literals` and `core_purity`; deployment profiles;
and an observe-don't-push console. Its *done when* is **two packs with
different modules enabled running from one codebase, and the console showing
both.**

Two things make it worth designing now rather than later.

A **first real plant** will need a pack. It is the one plant-specific
artefact — the thing [the engineers' front door](../operate/first-plant.md)
has a controls engineer draft on site — and the product has no crisp
definition of what it is, what may live in it, or what may not.

And an MES someone runs at more than one site needs the console, or they run
one copy per site and see nothing across them. That is the position today:
`fsmes plant all status` is a command on one laptop, not a view of a fleet.

---

## 1. What a plant is, today

There is **no `plant.toml`** in this repository and no `packs/` directory.
`grep -rn "plant.toml"` finds two hits, both aspirational: the roadmap's M8
line, and `labs/multiplant/README.md` saying that is where the lab is going.
`docs/ARCHITECTURE.md` proposes a `packs/` directory in a layout that was
written before the first line of code and does not match the tree.

What exists instead is **five separate places** a plant's identity lives, none
of which knows about the others.

| # | Artefact | Where | Read by | Validated? |
|---|---|---|---|---|
| 1 | The **registry** — one TOML file describing several plants | `labs/multiplant/plants.toml`, or `$FSMES_PLANT_REGISTRY` | `src/fsmes/plant.py`, `src/fsmes/mcp_server.py`, `src/fsmes/sim/*` | No. `tomllib` into a plain dict |
| 2 | The **tag map** — which machines exist and what their tags mean | `config/tag_map*.json`, `labs/*/tag_map.json` | `src/fsmes/services/tags.py`, `src/fsmes/integrations/opc/*` | Partly — an unmapped state value raises |
| 3 | The **seed script** — this plant's equipment, materials and routings | `labs/*/init.py`, `labs/multiplant/machining/seed.py` | run as a subprocess by `fsmes plant <name> init` | No. It is arbitrary Python |
| 4 | The **settings** — 66 `MES_*` variables | environment, or a `.env` file | `src/fsmes/config.py` | Types only, by pydantic |
| 5 | The **boundary mappings** — inbound columns, inbound SQL, line geometry, scorecard columns | `config/inbound_mapping.json`, `config/inbound_sql.json`, `config/line_layout.json`, the scorecard's own mapping file | `src/fsmes/integrations/inbound/*`, the line view, `fsmes shadow scorecard` | Per-file, ad hoc |

The registry is the nearest thing to a pack, and it is worth being precise
about what it is: **a way to run several plants on one machine**. Every key it
holds becomes an `MES_*` variable at spawn time (`plant_env`,
`src/fsmes/plant.py`), which is exactly why two plants can run side by side
with no multi-tenant code path — the bottling API cannot reach the machining
database because it was never told machining exists.

### What the registry actually reads

The code reads seventeen keys: `label`, `api_host`, `api_port`, `opc_port`,
`tag_map`, `replay_dir`, `init`, `post_boot`, `simulate`, `speed`,
`secret_key`, `accounts`, `database_url`, `database_password_file`,
`inspect_every`, `issue_every`, `inspect_all`, plus `[environment] data_dir`.

[The registry how-to](../operate/registry.md) lists fifteen. The two lists do
not match, in both directions:

- `inspect_every`, `issue_every` and `inspect_all` are read by `plant_env`;
  `labs/cutlery/registry.toml` sets two of the three. The documented list
  mentions none of them.
- `agent` is in the documented list. Nothing in `src/` reads it.

Neither is a bug anyone has hit. Both are the same fact: **the registry has no
schema, so nothing can tell a key from a typo**, and the file and its
documentation drifted apart inside two weeks without a build failing. A
plant-specific artefact a controls engineer drafts on site cannot work that
way.

Two more things the registry is not:

- `plant_env` hard-codes `MES_ERP_MODE="off"` with the comment that every lab
  plant would otherwise fight over the same mock ERP. That is right for a lab
  and wrong for a plant: **a registry plant cannot talk to an ERP at all.**
- Nothing in the registry says which modules a plant runs, what time zone it
  is in, or what it calls things.

### What cannot be configured per plant at all

- **Modules.** `src/fsmes/api/app.py` includes all twenty-three routers
  unconditionally. `src/fsmes/mcp_server.py` registers all ten tool files at
  import. `fsmes info` reports one entry-point module (`erpnext`), because
  every other module ships inside the kernel wheel and is always on.
  `src/fsmes/mcp/__init__.py` already carries the sentence *"a module a plant
  pack disables takes its tools with it by not being registered"* — the
  mechanism it describes does not exist.
- **Time zone.** `Settings` has no time-zone field. The only time zone in the
  product is per inbound stream, in `config/inbound_mapping.json` and
  `config/inbound_sql.json`, and it means *what zone this external system's
  naive timestamps are in* — not *what zone this plant works in*. Shifts,
  OEE windows and every screen use the process's own clock.
- **Vocabulary.** Nothing anywhere lets a plant rename a domain term. The
  word "vocabulary" appears in the code for fixed, product-wide things — the
  PackML state names, the capability names, the role ladder.

---

## 2. The two packs, held against the roadmap's own *done when*

`labs/multiplant/` runs two plants side by side: **bottling** (six stations,
`:8010`) and **machining** (three stations, `:8020`). They disagree
deliberately — station count, station names, analog names, rates, buffer
sizes, product, routing, quality specification — and the lab's README is
right that this proves something real: *no product code was added to run a
second plant.*

It does not prove what the milestone asks for.

| The roadmap's *done when* | State |
|---|---|
| Two packs | Two **plants**, in one shared registry file. Not packs; there is no pack |
| **with different modules enabled** | Not met. Both plants run every module, because no plant can disable one |
| run from one codebase | Met, and worth keeping. Same wheel, same schema, same migrations |
| and the console shows both | Not met. `fsmes plant all status` is a CLI on one machine |

The roadmap's own M8 heading, corrected on 2026-09-09, says *"two plant packs
prove the boundary; packs as a product and the console are open"*. Being
exact: **one of the four clauses is met.** The mark stays 🟡, and the reason
is the modules clause as much as the console.

The second pack is also not yet *adversarial by design* in the sense the
roadmap means. It disagrees about the plant — geometry, physics, products —
which is what shakes out a hard-coded station name. It agrees about
everything the pack format would carry: same modules, same clock, same words,
same ERP setting (`off`), same everything a `plant.toml` would hold. A pack
format is proved by a plant that disagrees with the *format*, not with the
line.

---

## 3. The two guard tests

`no_tenant_literals` and `core_purity` **do not exist.** A repository-wide
search finds one hit for either name: the roadmap line that names them.

What does exist is a family of tests that read the source rather than run it,
and they are the pattern to copy, because they were each written after
something got past review:

| Test | File | How | What it forbids |
|---|---|---|---|
| the shadow ratchet — two tests | `tests/test_shadow_mode.py` | `ast.parse` every file under `src/fsmes`, walk `ast.Call`, match against a list of outbound primitives | A call that reaches past the database — OPC write, HTTP, MQTT, mail, socket, model API — that `fsmes.shadow.REGISTER` does not name |
| `test_a_transaction_is_not_held_open_across_a_network_call` | `tests/test_sqlite_write_locks.py` | AST: an `await` inside a `with session_scope()` block | Holding a SQLite write lock across a network call |
| `test_every_write_route_has_a_tool_or_a_reason` | `tests/test_mcp_parity.py` | Text: OpenAPI write routes against the MCP tool sources | A write route with neither a tool nor a stated reason |
| `test_every_route_has_a_screen_or_a_reason` | `tests/test_route_coverage.py` | Text: routes against the screen scripts | An endpoint no screen mentions and nobody excused |
| `test_the_product_never_imports_playwright` | `tests/test_ui_check.py` | Substring | Browser tooling at module scope in the product |
| `test_the_migration_chain_has_exactly_one_head` | `tests/test_migrations_have_one_head.py` | Reads the Alembic graph | Two heads |

So the shape is established and the two named tests are missing. What each
should forbid, proposed:

- **`no_tenant_literals`** — no file under `src/fsmes` may contain a plant's
  own name, code or address. Concretely: the plant names in every registry the
  repository ships (`bottling`, `machining`, `cutlery`, `megafactory`), the
  labs' invented company names, the equipment codes and material codes that
  appear only in a `labs/` seed, and any URL or host that is not a documented
  default. It reads the registries and the seeds to build its own forbidden
  list, so a new lab plant extends the guard instead of escaping it. The rule
  it pins is house rule 4, and the failure it catches is the one
  `labs/multiplant/README.md` already names: *"the moment a second plant needs
  a `seed_northgate.py` shipped inside the product, the product has a tenant
  literal in it."*
- **`core_purity`** — the layering rule that today lives only as a docstring
  in `src/fsmes/kernel/__init__.py`: `domain <- services <- {api, connect,
  modules, cli}`, no lower layer importing a higher one. An AST import walk
  proves it. Decision
  [0002](../decisions/0002-kernel-and-modules.md) says the module boundary is
  "a discipline, not a wall"; this is the test that makes the wall, and it is
  a precondition for a pack disabling a module, because a module that the
  kernel imports cannot be disabled.

Note what `core_purity` will find on the day it is written: `src/fsmes/kernel/`
holds `common.py` and `tags.py`, `src/fsmes/core/` holds `oplock.py`, and the
kernel the architecture page describes — master data, orders, dispatch, audit,
auth — actually lives in `domain/` and `services/`. There is no `modules/`
directory. The test's first job is to say what the layers *are*.

---

## 4. Site scoping — the question is half-answered already

The roadmap asks for "site scoping on kernel tables". Measured against the
schema, that is a bigger change than it sounds, and decision
[0006](../decisions/0006-sqlite-laptop-postgresql-plant.md) has already
answered most of it: **one database per plant.**

The numbers, from the models at `2474a8b`:

- **38 tables.** **Zero** carry a site, plant or tenant column.
- **25 tables** carry at least one uniqueness rule. Of those, **five** are
  already scoped through a foreign key and would survive a shared database
  (`bom_items`, `quality_specs`, `routing_operations`,
  `work_order_operations`, `uns_publications`). The other **twenty** are
  plant-scoped natural keys that a second plant in the same database would
  collide on: every `code` — equipment, materials, routings, roles,
  personnel, work orders, lots, gauges, triggers, non-conformances,
  maintenance plans and orders, shift patterns, adjustments, documents —
  plus `serial_units.serial`, `erp_messages.message_key`, and the three
  operational keys `idempotency_keys(actor, key)`,
  `inbound_events(source, kind, external_key)` and
  `inbound_watermarks(source, stream)`.
- **Four tables are global cursors or counters** with no plant dimension:
  `serial_sequences` (whose primary key *is* the serial prefix),
  `inbound_watermarks`, `erp_messages`, `uns_publications`.
- **30 migrations, one head** (`c8b1e40d7a92`).

Adding a site column would therefore touch twenty uniqueness rules, four
cursor tables, every query in `src/fsmes/services/`, and every migration
after it. `src/fsmes/plant.py`'s own docstring is the counter-argument and it
is a good one: *"there is no multi-tenant code path anywhere in the MES, and
that is the point — isolation here is by construction, not by a `WHERE`
clause somebody might forget."* A recall that returns another plant's serials
is not a bug you find in review.

The proposal, in [0021](../decisions/0021-one-database-per-plant.md): **a
fleet is many databases, and "site scoping" is re-read as identity rather
than partitioning.** What kernel tables actually lack is not a foreign key to
a `sites` table but an answer to *which plant am I* that leaves the process —
a plant identity every emitted record and every endpoint carries, so a
console, a broker and a shadow scorecard can tell two plants apart without
being told out of band. Today `MES_PLANT_NAME` exists, defaults to empty, and
surfaces in exactly two places: the UNS envelope's `plant` field, and the
assistant's greeting. `/health` does not say it. `/shadow` does not say it.

That is a small, cheap change, and it is the whole of the site scoping this
milestone needs.

---

## 5. What a plant already emits that a console could read

Enough for a first console, and this is the pleasant surprise of writing this
page. Every row below exists at `2474a8b`.

| Surface | Where | Auth | What a console gets |
|---|---|---|---|
| `GET /health` | `src/fsmes/api/routers/system.py` | public | alive, and `shadow` true/false |
| `GET /shadow` | same | public | the full outbound register: **32 paths — 16 allowed, 11 refused, 5 restricted** — plus the ERP and UNS modes actually in force |
| `GET /metrics` | same | public | Prometheus text: work orders per status, pending ERP messages, max tag-value and audit ids |
| `GET /ops/services` | `src/fsmes/api/routers/ops.py` | `audit.read` | liveness of the plant's own processes: api, opc-agent, opc-replay, opc-sim, operations |
| `GET /equipment/{code}/oee`, `GET /analysis/oee` | `equipment.py`, `analysis.py` | signed in | OEE per machine and the loss breakdown, with `null` where it cannot be computed honestly |
| `GET /erp/outbox` | `erp.py` | signed in | what the ERP is owed, and what died |
| `fsmes db-status` | `src/fsmes/cli.py` | local | current Alembic revision against head. **Text only, exits non-zero when behind** |
| `fsmes info` | same | local | version, install path, entry-point modules, shadow summary |
| the UNS publisher | `src/fsmes/integrations/uns/` | broker | every outbox event under `<prefix>/<enterprise>/<site>/…`, envelope carrying `plant` |
| `list_plants()` | `src/fsmes/mcp_server.py` | agent account | **the console in miniature** — reads the registry, calls each plant's `/health`, and reports `shadow: None` for a plant that did not answer |

`list_plants` is the shape to grow. It already refuses to guess: a plant that
does not answer gets `None`, and the docstring says why — *"None means the
plant did not answer, which is not the same as not shadow."* That is house
rule 2 in a fleet, and it is the hardest part of a console to get right.

Held against the four words in the roadmap's control-plane phrase:

- **Enrol** — does not exist. There is no machine credential of any kind: no
  API key, no device identity, no shared secret. A console authenticates as a
  person or as the `AGENT` account, with a password, per plant.
- **Telemetry** — exists, but only as pull. Nothing in the product pushes
  status anywhere. There is no heartbeat and no webhook.
- **Desired state as intent** — does not exist. There is no config checksum,
  no doctor command, nothing that compares what a plant is running against
  what it was meant to run. `fsmes db-status` is the only desired-versus-
  actual check in the product, and only for the schema.
- **Drift display** — does not exist, and cannot until a pack exists, because
  drift is measured against a pack.

---

## 6. Proposal — what a plant pack is

**A plant pack is the complete, versioned, validated answer to "which plant is
this?", in one directory, owned by the plant and not by this repository.**

```text
packs/<name>/
  plant.toml          identity, profile, modules, clock, words   (required)
  tag_map.json        which machines exist and what their tags mean
  masterdata/         equipment, materials, routings — data, not a script
  mappings/           inbound columns, inbound SQL, scorecard columns
  line_layout.json    optional geometry for the line view
  README.md           what this plant is, for the next engineer
```

`plant.toml` carries, and only carries:

| Section | Holds | Why here |
|---|---|---|
| `[plant]` | `name`, `label`, `timezone`, `enterprise`, `site`, `area` | Identity. `timezone` becomes a real setting; `enterprise`/`site` already exist as `MES_UNS_*` and belong to the plant, not to the broker config |
| `[modules]` | one boolean per optional module | The milestone's *done when*. Requires `core_purity` first |
| `[words]` | domain term → this plant's term | The roadmap's "vocabulary mappings". **Display only** — see below |
| `[storage]` | `database_url`, `database_password_file` | Already registry keys |
| `[serve]` | `api_host`, `api_port`, `opc_endpoint` | Already registry keys |
| `[erp]`, `[uns]`, `[inbound]` | mode plus the file each mapping lives in | Today these are `MES_*` variables with no per-plant home, and `plant_env` hard-codes ERP off |
| `[pack]` | `format` version, `requires` a version range of the MES | So a pack can be refused rather than half-understood |

What a pack **may not** contain, and this is the load-bearing half:

1. **No code.** Not a `.py`, not a hook, not an expression to evaluate. Today
   a plant's `init` key names a Python file that `fsmes plant init` runs as a
   subprocess with the plant's environment; `labs/multiplant/machining/seed.py`
   is 156 lines of it, and `labs/cutlery/init.py` is 191. Master data becomes data a validator can read, and
   `fsmes pack apply` seeds from it. A pack that can run code is a pack nobody
   can review before it touches a plant.
2. **No secrets.** Passwords stay in the environment or in a file the pack
   *names* — the pattern `database_password_file` and `password_env` already
   use, applied everywhere. A pack should be safe to attach to a support
   thread, and today `secret_key` sits in the registry in plain text.
3. **Nothing that changes what a number means.** `[words]` renames a label on
   a screen and in a report. It may not rename a state, a KPI, a capability,
   an audit action, an MCP tool, an event `kind`, or anything a topic or an
   API field is built from. Two plants' events must remain comparable, or the
   fleet console is comparing dialects. The guard is a test, not a rule in
   prose.
4. **No plant may extend the schema.** A pack cannot add a table or a column.
   That is a module, and modules are code.

**How it is validated.** `fsmes pack check <dir>` — no database, no network,
exit non-zero — proves the format version is understood, that the MES version
satisfies `requires`, that every key is one the product knows (an unknown key
is an error, which is what the registry cannot say today), that every file it
names exists, that the time zone is a real IANA zone, that every module named
exists and its dependencies are enabled, that no `[words]` entry renames a
protected term, and that the tag map's state values all map. It reports
`unknown` for what it cannot prove without a plant — that the OPC endpoint
answers, that the database is reachable — rather than passing them.

**How it is versioned and upgraded.** The pack carries `format` and
`requires`; the product carries a pack-format head the way it carries an
Alembic head. `fsmes pack migrate` rewrites a pack forward one format version
at a time, backing it up first and printing what it changed, in the shape
`fsmes plant migrate` already uses for databases — refuse if the plant is
answering, copy first, migrate, print a receipt. The two must run in one
order and say so: **pack first, then database**, because a database migration
may need a value the pack now carries. `fsmes db-status` grows a sibling,
`fsmes pack status`, and both are what the console reads for drift.

---

## 7. Proposal — the deployment profiles

Three, named in `plant.toml` as `profile`, so a plant states which one it is
and the product can refuse what does not fit.

| Profile | Shape | What states it | What it refuses |
|---|---|---|---|
| `laptop` | one process, SQLite, replay driver | `fsmes demo`, the labs | nothing; it is the evaluation profile |
| `plant` | Compose or systemd: PostgreSQL, api replicas, connect, one-shot migrate | `docker/docker-compose.yml`, `deploy/` | SQLite with more than one writer process; well-known passwords; starting behind the schema head |
| `fleet` | N `plant` nodes plus one console | does not exist | pushing anything to a node |

The first two exist and are documented in [deploy](../operate/deploy.md); the
profile name does not. Making it explicit is what lets a plant node say *I am
a plant, not a laptop* in one word, to the console and to its own start-up
checks.

---

## 8. Proposal — the console

**A read-only web page that polls a list of plants and shows what each one
says about itself.** One process, no database of its own beyond a cache, no
credentials to a machine, no path to change anything anywhere.

For a first version, per plant, one row: name and label; reachable or not, and
when it last answered; shadow or live; MES version; schema revision against
head; pack version against head; whether the ERP outbox has anything dead;
whether the UNS backlog is draining; and the plant's line OEE for the current
shift. Clicking a row opens that plant's own dashboard — the console does not
re-implement a screen the product already has.

What it deliberately does not do, and this is the decision in
[0023](../decisions/0023-the-fleet-console-observes.md):

- **It never writes to a plant.** Not a setting, not a pack, not an order, not
  a restart. Every M8 verb — enrol, telemetry, desired state, drift — is a
  read or a comparison. A console that can push is a console that can push to
  the wrong plant, and the blast radius of that is a factory.
- **A plant that did not answer is `unknown`, never healthy and never down.**
  `list_plants` already does this and the console inherits it. A console that
  renders silence as green is worse than no console.
- **It states its total.** "12 plants, 11 answered, 1 unknown" — rule 4 of
  [the style contract](STYLE.md) applied to a fleet. The number of plants
  configured is never the number of plants seen.
- **It aggregates nothing across plants that would be a lie.** A fleet OEE is
  a lie unless every plant is the same shape; the console shows twelve
  numbers, not one. This is the same rule the
  [shadow scorecard](../operate/shadow-scorecard.md) follows when it refuses
  to add operations into an order total.
- **It holds no plant data.** It caches the last answer for display and
  nothing else. Orders, serials, people and events stay in the plant that
  made them.

"Enrol", then, means: *a person adds a plant to the console's list and gives
it a credential the plant already understands.* Not a mutual handshake, not a
certificate authority, not a bootstrap token — those are a control plane, and
a control plane implies a plane that controls. What the product genuinely
needs first is a **read-only machine credential**: an account that can call
`/health`, `/shadow`, `/metrics`, `/ops/services` and the OEE endpoints and
literally nothing else, so a console does not run as a person and does not
run as `AGENT`. That is a capability set and a role, both of which
`src/fsmes/services/capabilities.py` already knows how to express.

---

## 9. The order of work

Four pieces. Each is a handoff, each ends in something a person can see, and
each is useful alone if the next one never happens.

### Piece 1 — a plant knows its own name

`MES_PLANT_NAME` becomes required for a non-laptop profile and reaches
everything a reader can see: `/health`, `/shadow`, `/metrics` (as a label),
the dashboard header, `fsmes info`, the backup manifest. Add
`MES_PLANT_TIMEZONE` as a real setting, used by shifts, OEE windows and every
screen, defaulting to the process zone and saying so.

**Done when:** two plants running side by side answer `/health` with different
`plant` values, and a screenshot of either dashboard says which plant it is.

### Piece 2 — the guard tests, and the layers they need

Write `core_purity` and `no_tenant_literals`. Move whatever they find into
place. This piece is first among the pack work because a module cannot be
disabled while the kernel imports it, and a pack cannot be trusted while a
plant's name can hide in `src/`.

**Done when:** both tests are in the suite and fail on a deliberate
violation — a `from fsmes.services import …` added to a `domain/` module, and
a lab plant's name pasted into `src/fsmes/services/line.py`.

### Piece 3 — the pack format, its validator, and an adversarial second pack

`plant.toml`, `fsmes pack check`, `fsmes pack apply`, `fsmes pack status`,
`fsmes pack migrate`. The registry becomes a list of packs; each of its
seventeen keys either moves into `plant.toml` or is deleted with a reason,
including the three nobody documented. `labs/multiplant`'s two plants are converted, and a **third pack is
invented to disagree with the format** — a plant in a different time zone,
with several modules off, with its own words for two labels, on PostgreSQL,
with an ERP mode set, whose master data is data rather than a script.

**Done when:** `fsmes pack check` refuses a pack with an unknown key, a bad
time zone, a renamed protected term, and a `requires` this version does not
satisfy — each with one sentence naming the problem; and the third pack runs
from the same wheel with two modules off, proven by its own API answering 404
on those routes and its MCP tool list being shorter.

### Piece 4 — the console

The page from §8, over a read-only role, against a list of packs.

**Done when:** it shows three plants, one of which is switched off, as
"2 answered, 1 unknown" — and a reviewer can read the code and see there is no
call in it that writes.

Pieces 1 and 2 are independent of each other and of the rest. Piece 3 needs
piece 2. Piece 4 needs pieces 1 and 3, and is the only one that closes the
milestone's *done when*.

---

## 10. Risks, named

| Risk | What it looks like | What to do about it |
|---|---|---|
| **The pack becomes a language.** `[words]`, then conditions, then an expression, then a hook, then a plugin. Every configuration format ends up here | A plant asks for "just one small computed field" | Rule 1 of §6, held by a test, not by prose. The escape hatch is a module, and modules are code and are reviewed |
| **`[words]` corrupts comparability.** A plant renames a state, and its events no longer mean what another plant's mean | The console compares two plants that use the same word for different things | Protected terms are enumerated and tested. `[words]` reaches screens and reports only, never a topic, a `kind`, an enum or an API field |
| **The console grows a button.** "It only restarts the OPC agent." | One write path, then a second | Decision [0023](../decisions/0023-the-fleet-console-observes.md), plus the shadow ratchet: the console is code under `src/fsmes`, so a new outbound call must be registered, and a registered write to a plant is visible in review |
| **Migrating in two places.** Pack format and database schema both move, and a plant ends up half-upgraded | A pack written for 0.3 meets a 0.2 database | One order, stated and enforced: pack first, then database; each refuses a plant that is answering; each prints a receipt |
| **The third pack is a costume.** It disagrees on paper and is really the demo plant again | It passes on the first try | The test is whether writing it *forced a code change*. If adding the third pack changes nothing under `src/`, the format is proved; if it changes something, that change is the finding |
| **Nobody has run two plants for real.** The lab is two processes on one laptop | Everything above is designed against a lab | The first real plant is one plant. The console's value starts at the second, and there is no second yet — which is an argument for pieces 1–3 landing well before piece 4 |
| **The dashboard breaks before the fleet does.** A single plant of sixty machines already breaks the screens | Sixty cards, 240 progress bars, no grouping | Recorded already in `docs/design/backlog/sixty-machine-plant.md`; it is a prerequisite for a large plant, not for a fleet |

---

## 11. Findings, not fixed

Noticed while reading. None was changed — this branch carries no product code.

1. `plant_env` (`src/fsmes/plant.py`) hard-codes `MES_ERP_MODE="off"`, so no
   plant started from the registry can reach an ERP. Correct for the lab it
   was written for; wrong the moment the registry describes a real plant.
2. [The registry how-to](../operate/registry.md) documents an `agent` key that
   nothing reads, and omits `inspect_every`, `issue_every` and `inspect_all`,
   which are read.
3. `MES_PLANT_NAME` defaults to `""`, and an empty plant name reaches the UNS
   envelope as `"plant": null` — an event that cannot say which plant made it.
4. `docs/ARCHITECTURE.md`'s proposed layout names `packs/`, `kernel/` and
   `modules/`. `packs/` does not exist, `modules/` does not exist, and
   `kernel/` holds two files while the kernel it describes lives in `domain/`
   and `services/`. The page says up front that it is intent rather than
   description, and it is still the first thing a new contributor reads.
5. `README.md` principle 7 says *"A second, deliberately-different demo pack
   exists purely to prove that adding a plant never requires a code change."*
   A second *plant* exists and does prove that. A pack does not exist. The
   sentence is ahead of the code by one noun.
