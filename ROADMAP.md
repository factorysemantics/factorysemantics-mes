# Roadmap

*Written 2026-08-28. Milestones are sequenced by dependency, not by ambition — each one ends in a runnable demo and a set of named tests, because a milestone that doesn't demo isn't done. Effort is in evenings (the real unit of work here: ~1 focused hour + Claude doing the heavy lifting). Estimates are honest guesses, not promises.*

## The strategy in one paragraph

Start at the core and work outward — production orders bound to routings, routings executing against PLCs — because every other module hangs off the events that core loop produces: downtime needs equipment states *with order context*, quality needs operations to attach checks to, maintenance needs runtime meters that fall out of execution, scheduling needs routings and calendars to schedule against. The core loop ports from MES-TWIN (working, tested code), which means the first end-to-end demo is weeks away, not months. After that, modules land in the order that each one feeds the next, with the agent layer growing continuously rather than as a phase at the end.

## Dependency spine

```
M0 bootstrap
└─ M1 core loop (orders → routing → dispatch → OPC → booking)
   ├─ M2 downtime + shifts + OEE          (needs: states w/ order context, calendar)
   │  └─ M4 maintenance-lite              (needs: meters, downtime reasons)
   ├─ M3 quality + SPC + gauges           (needs: operations, lots)
   ├─ M5 scheduling                       (needs: routings, calendars from M2)
   ├─ M6 traceability + serialization     (needs: lots, consumption)
   └─ M7 ERP connectors hardened          (needs: order lifecycle complete)
      └─ M8 multi-site + plant packs + fleet
Agent track A0–A3 runs alongside every milestone (see below).
```

---

## M0 — Bootstrap ✅ *(done 2026-08-28)*

Repo hygiene before any feature.

**Delivered:** Apache-2.0 `LICENSE` (verbatim upstream text, verified by hash against three independent copies) + `NOTICE` carrying the copyright and the provenance statement, in this repo **and** in MES-TWIN and LineSim so their code can legally move; `CONTRIBUTING.md` with the DCO sign-off and the six house rules; `pyproject.toml` (hatchling, `src/fsmes/`, ruff + pytest, dependencies limited to what M0 actually imports); a `.gitignore` whose data and secret rules are wholesale rather than enumerated; GitHub Actions CI across Python 3.12/3.13 on **Ubuntu and Windows** (a plant PC is a first-class target); the two ported primitives — `kernel/common.py::str_enum` and `core/oplock.py::single_writer` — with 11 behaviour-named tests; `fsmes --version` / `fsmes info`, the latter reading installed modules from the `fsmes.modules` entry-point group rather than a hardcoded list; registration in AI_Tracker `_system\projects.json` so the nightly verifier watches this repo from tonight.

**Verified:** `ruff check .` clean, `pytest` 11 passed, `fsmes info` reports `none (kernel only — M0)`. PyPI names `factorysemantics-mes` and `fsmes` both confirmed free.

**One deviation from plan, recorded:** `str_enum` was described as zero-dependency; it imports SQLAlchemy. So `sqlalchemy>=2.0` and `typer>=0.12` are declared from the first commit — both are load-bearing for M1 anyway, and a dependency declared before its first import is a promise the build cannot check.

## M1 — The core loop: one order, one line ✅ *(landed 2026-08-31, adopted from MES-TWIN)*

The milestone that proves the thesis, and the one Scott already named: **orders set to a routing, routing integrating with PLCs.**

**Scope (ported from MES-TWIN, restructured per [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)):**
- Kernel master data: Equipment hierarchy (ISA-95: enterprise → site → area → work center → work unit), Material, Person; **Routing + RoutingOperation** with the copy-onto-order-at-creation discipline (later routing edits never rewrite history).
- Work orders: create / release / dispatch / start / book / complete, with the completion side-effects converted from hardcoded calls into **kernel hooks** (the seam every future module plugs into).
- Execution: production logs (good/scrap, source manual|opc), material lots, consumption.
- Connectivity service: the OPC UA agent (asyncua) with tag maps, the **counter-delta discipline** ("never invent production" — ported exactly, tests and all), order-code writeback, reconnect loop, provenance audit entry; the commissioning toolchain (`make-tag-map`, `seed-line`, `opc-browse`, `opc-verify`, worksheet → tag map).
- The CI plant: LineSim CSVs + the CSV-replay OPC UA server as a **default (not slow) CI job**; the six KepSim scripted events become named regression tests (changeover must not book downtime, counter reset must book zero units, a breakdown at RD must starve the washer...).
- Event emission: every kernel action emits a FactorySemantics `CanonicalEvent` through a transactional outbox — the wire between the MES and the semantic layer that has never existed before.
- Minimal operator surface: auth (port PBKDF2/HMAC roles), order list, start/complete, live line status. Plain HTML/JS, no build step.

**Done when:** `fsmes demo` releases an order against the six-station line, real OPC UA traffic books honest production, the order completes, and the event lake contains the canonical story of the run. Target: ~60+ ported/new tests green.

## M2 — Where the hours went: downtime, shifts, OEE ✅ *(landed 2026-08-31; shift calendar 2026-09-01)*

The first pure module, and the highest-demand feature in every "why is there no open-source MES" thread.

**Scope:** downtime **reason-code table** (hierarchical, planned/unplanned flag — replaces MES-TWIN's free-text reasons); operator "attach reason to open stop" action (closes MES-TWIN's known ~100%-unlabelled gap); **shift calendar** — shifts, breaks, holidays, planned production time (the ISO 22400 time model, absent from MES-TWIN entirely); machine states normalized to the **PackML state vocabulary** with per-driver mapping; OEE/TEEP with a *scheduled-time* denominator (ports the null-not-zero, window-clamped math); the analysis screens (OEE waterfall, state timeline, downtime pareto) ported and made shift-aware; MTBF/MTTR counters (feeds M4).

**Done when:** a shift report names the constraint station, planned stops provably don't count as downtime (LineSim's scripted changeover is the test), and an unlabelled stop shows as unlabelled until an operator or agent labels it.

## M3 — Prove it's good: quality, SPC, gauges ✅ *(landed 2026-08-31)*

**Scope:** inspection plans (characteristics per routing operation: nominal, tolerances, sampling frequency); quality checks recording *who measured what with which gauge*; holds and non-conformances with disposition workflow (use-as-is / rework / scrap) and e-signature records (the 21 CFR Part 11 pattern: re-auth + meaning + record hash, chained into the audit spine); **SPC engine built in-repo** (~500 lines: X̄-R, I-MR, EWMA, p-charts, Cp/Cpk, Nelson rules — no maintained OSS library exists and the valuable part is the wiring: a rule violation auto-holds the lot and flags the machine); **gauge registry** — gauges, calibration schedules, due/overdue states, an overdue gauge blocks checks that reference it. Gauge management has *zero* open-source competition; this small module is a genuine first. The Keyence VR-6200 CSVs in `Data Analysis\mes_keyence\` become the gauge-import test fixture.

**Done when:** LineSim's scripted scrap-burst trips a control chart rule, the affected lot goes on hold automatically, and the demo shows a check refused because its gauge is out of calibration.

## M4 — Keep it running: maintenance-lite ✅ *(landed 2026-08-31)*

**Scope:** runtime/cycle meters accumulated from execution events (free, once M1 exists); PM schedules — calendar-based and meter-based; maintenance work orders with simple states; failure codes linked to downtime reasons (M2), so the downtime pareto and MTBF/MTTR feed maintenance priorities. Deliberately lean: full CMMS (spares, purchasing, crews) stays out; integrate Atlas CMMS by API for plants that need it.

**Done when:** a runtime meter crossing its threshold generates a PM work order, and completing it stamps the equipment history.

## M5 — Promise dates: scheduling ✅ *(landed 2026-08-31)*

**Scope:** planned start/end on order operations; finite-capacity scheduling via **OR-Tools CP-SAT** modeled through PyJobShop (MIT) — machine eligibility, sequence-dependent setups, due dates; schedule vs. actual view; what-if ("insert this hot order, what slips?"). Solver behind a request/response seam so frePPLe (now MIT) can be swapped in for plants needing real APS. Uses M2's calendars as capacity.

**Done when:** twenty orders schedule across the six-station line, the bottleneck is visible, and a what-if answers in seconds.

## M6 — Trace everything: serialization + genealogy ✅ *(landed 2026-08-31)*

**Scope:** units/serials below lot level (FactorySemantics already has UNIT/BATCH/PALLET entity types); parent-child containment (pack → pallet); consumption genealogy ("this unit contains material from lots X, Y"); recall query ("which pallets contain lot X" in one call); label printing (thin Jinja2 → ZPL service + raw :9100 spooling, DataMatrix via printer-native symbology).

**Done when:** the demo prints (to file) a serialized label at the palletiser and answers a recall query across the run's genealogy.

## M7 — Speak ERP: connectors hardened 🟡 *(the typed per-operation contract and its outbox landed 2026-09-03; proving it end to end against a live ERPNext is what remains)*

**Scope:** port the 3-method ERP adapter protocol (`fetch_orders / acknowledge / send_confirmation`), the transactional inbox/outbox with retry, the ERPNext adapter (orders in, real Manufacture stock entries back), the B2MML-flavored file adapter, and the REST adapter. Add B2MML-JSON at the boundary. The ERPNext connector is also the **distribution wedge**: it makes this project the answer to ERPNext's own open MES gap ([#50827](https://github.com/frappe/erpnext/issues/50827)).

**Done when:** an ERPNext work order round-trips through a full simulated production run, and the file adapter passes B2MML symmetry tests.

## M8 — As big or as small as the customer requires: packs + fleet 🟡 *(two plant packs prove the boundary; packs as a product and the console are open)*

**Scope:** site scoping on kernel tables; **plant packs** (`plant.toml`: which modules enabled, timezone, vocabulary mappings) with the adversarial second demo pack and the two guard tests (`no_tenant_literals`, `core_purity`) adopted from factorysemantics.com on day one of this milestone; deployment profiles (laptop SQLite / plant compose / fleet); adopt the observe-don't-push control plane pattern (enroll, telemetry, desired-state-as-intent, drift display) for multi-plant visibility.

**Done when:** two packs with different modules enabled run from one codebase, and the console shows both.

---

## The agent track (continuous, not a phase)

| Stage | Lands with | What it adds |
|---|---|---|
| **A0** | M1 | Kernel MCP server (streamable HTTP + stdio), **read-only**: order status, line state, "explain this order's history" from the audit spine. One server per module from the start — tool-count ceilings are real (~40/server). |
| **A1** | M2–M3 | Command tools with the write discipline: idempotent, `dry_run`, approval gates (auto-allow reads; require human approval for dispatch overrides, booking corrections, holds). On-behalf-of identity in every audit event — agent governance and Part 11 ride the same spine. |
| **A2** | M2–M5 | Module agents: downtime narrator ("your 6am–2pm story, worst 3 losses named"), SPC watchdog, PM planner, schedule negotiator. Each is prompts + the module's own MCP tools — no new infrastructure. |
| **A3** | M7+ | Runbook agents: triage flows (the MES vault's phone-first runbook pattern, executable), commissioning copilot (reads `opc-verify` output, drafts the tag-map worksheet). |

Development itself stays agentic too: this repo joins the nightly verifier at M0, CI runs the simulated plant, and Claude sessions do the porting with the test suite as the referee.

## Out of scope (deliberately, with exits)

- **Not an ERP** — orders, BOMs-as-truth, costing, purchasing live in ERPNext/Odoo/SAP; we integrate (M7).
- **Not a SCADA/HMI** — FUXA and Ignition are neighbors, not competitors; we consume tags, we don't draw faceplates.
- **Not an APS** — CP-SAT dispatch-level scheduling in M5; real multi-plant APS = frePPLe integration.
- **Not a historian product** — Postgres partitioning behind a thin interface; TimescaleDB Community as an opt-in profile; QuestDB as the scale escape hatch.
- **No cloud SaaS in v1** — on-prem first, like the plants themselves. The fleet console is on-prem too.
- **Process/batch (ISA-88 recipes), LIMS, warehouse logistics** — later module families, after the discrete suite stands. Two ISA-88 ideas are stolen early (procedure/equipment separation, master vs. control recipe); the rest waits.

## Risks, named

| Risk | Mitigation |
|---|---|
| **Solo scale.** Opcenter is thousands of engineer-years. | Modules ship independently; port don't rewrite; 20%-that-serves-80% depth; agents as force multiplier; integrators as eventual contributors. |
| **Engine-not-car.** (The known pattern: finishing engines, never shipping the car.) | Every milestone *ends in a demo*, and each module's `Done when` is a visible behaviour, not an internal refactor. Publication was the third leg of this mitigation and it happened on 2026-09-08, so the repository is a forcing function again — but the demo discipline carries the same weight it did while publication was still gated. |
| **Scope creep via standards.** ISA-95 can swallow a year. | Nouns, not schemas. B2MML only at the ERP border. PackML as an enum, not a certification. |
| **Clean-room discipline.** | The provenance rule in README, kept current: nothing from any commercial MES's internals, schemas or documentation, and no real plant's data or configuration, ever. Domain understanding comes in; proprietary material does not. |
| **A funded competitor claims the lane** (OpenMes is 6 months old and moving; MCP wrappers are commoditizing). | Speed to M1/M2 (the demanded features), the unclaimed differentiators (gauges, agent-native core, honest-data discipline), and the ERPNext wedge. |
| **Burnout / competing ventures.** | The board decides. This roadmap spends evenings only when FactorySemantics MES is the chosen focus; milestones are sized so one can pause cleanly at any boundary. |

## Where this stands

**As of 2026-09-09.** M0 bootstrapped 2026-08-28. M1–M6 landed 2026-08-31 by adopting MES-TWIN wholesale as the engine and building the missing modules on it (the decisions are recorded in `docs/decisions/`; the private-era history is summarised in `docs/history/private-era.md`). The repository went public on the evening of 2026-09-08, and `factorysemantics-mes` 0.1.0, 0.1.1 and 0.1.2 reached PyPI the same evening, with the container image on GHCR and the documentation at [docs.factorysemantics.com](https://docs.factorysemantics.com). CI runs lint and the test suite on Ubuntu and Windows across Python 3.12 and 3.13, on every push to `main` and every pull request, and `main` is green. Simulated plants run from a registry (`fsmes plant`), and the repository ships a templated systemd unit so a plant can run as a service; `fsmes score` measures the MES against the simulator's scripted truth on every change; the product exposes 85 agent tools and the simulator 13.

**Next, in order** (ratified 2026-09-02; each item's state checked against the code on 2026-09-09):

1. **Foundation** — **done.** CI runs `ruff` and `pytest` across the whole matrix on every push and pull request (`.github/workflows/ci.yml`), and the documentation builds strictly — a broken link fails the job — and publishes to [docs.factorysemantics.com](https://docs.factorysemantics.com). `fsmes plant migrate` is real (`src/fsmes/plant.py`): it backs the database up, refuses a plant that is still answering, runs the migration, and counts the rows either side. Simulated activity is signed by simulated accounts — `FLOOR-SIM` and `FLOOR-SUP` — so nothing invented lands in the audit trail under a person's name.
2. **Agent-first, mechanised** — **mostly done.** One `fsmes` MCP server with a tool file per area (`src/fsmes/mcp/`, [decision 0003](docs/decisions/0003-one-mcp-server.md)) carries all 85 tools. `tests/test_mcp_parity.py` fails when an HTTP write route has neither a tool nor a stated reason; three routes are excluded with a reason today (operation start, operation complete, document revise), alongside the approvals that are permanently human. The `agent` role is not admin (`src/fsmes/services/capabilities.py`), `on_behalf_of` reaches every audit record, and idempotency keys are enforced in middleware. Open: the agent evals exist and their scoring runs in CI (`tests/test_agent_eval.py`), but the run that puts a real model against the live tool surface is a command a person types (`fsmes agent-eval`), not a gate.
3. **M7 proper** — **partly.** The typed port and its contract are in (`src/fsmes/integrations/erp/`): `fetch_orders` / `acknowledge` / `send_confirmation` over typed models, with per-operation confirmations that carry cost centre, setup, machine and labour time, and the components consumed. The outbox backs off, gives up after eight attempts into a `DEAD` state, and agents can both see it and retry it (`erp_outbox`, `erp_retry`). Not done: the round trip against a live ERPNext is a `slow` test excluded from the default run, so it is proven by hand rather than by the gate.
4. **The OPC suite** — **done, except that the tag browser is a command rather than a screen.** `fsmes opc-browse` walks the address space; triggers are rows with an approval lifecycle and an action catalogue rather than code (`src/fsmes/domain/triggers.py`); PLC write-back is a recommendation queue where a person approves and the setpoint is bounds-checked when it is proposed and again when it is approved (`src/fsmes/services/adjustments.py`); the certificate of analysis is issued at the end of the line (`src/fsmes/services/coa.py`); and the washer story runs in CI (`tests/test_washer_story.py`).
5. **Scale hardening before any real plant** — **partly.** Tag retention prunes on a policy (`src/fsmes/services/retention.py`, 14 days by default); the latest-value index landed (`src/fsmes/migrations/versions/e5f6a7b8c9d0_tag_values_latest_index.py`); one OPC agent per line runs from the endpoint list (`src/fsmes/integrations/opc/agent.py`). PostgreSQL is configuration and a Compose service, but every test runs on SQLite (`tests/conftest.py`), so *runs on PostgreSQL* is checked by hand and not by CI. The heading still holds: no real plant.
6. **Then stop and ask** — **done; the answer was yes.** Publication was gated on the maintainer's explicit word (decision of 2026-08-29), and he gave it: the repository went public on 2026-09-08. The public history starts from a single commit ([decision 0007](docs/decisions/0007-fresh-public-history.md)) and the private era, 2026-08-28 to 2026-09-07, is summarised in [docs/history/private-era.md](docs/history/private-era.md).

**Carried decision for M1, settled by not taking the dependency:** `pyproject.toml` names nine runtime dependencies and FactorySemantics is not among them, nothing under `src/` imports it, and there is no canonical-event table or emission anywhere in the code. The append-only audit log (`src/fsmes/domain/audit.py`) carries the story of every state change instead, and the only outbox that exists is the ERP one (`src/fsmes/domain/integration.py`). The choice between a path reference, a git reference and publishing that repository therefore never had to be made; it comes back the day the semantic-layer outbox is actually built.
