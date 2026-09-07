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

## M7 — Speak ERP: connectors hardened 🟡 *(ERPNext order-level round trip inherited; the per-operation contract is next)*

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
| **Engine-not-car.** (The known pattern: finishing engines, never shipping the car.) | Every milestone *ends in a demo*, and each module's `Done when` is a visible behaviour, not an internal refactor. Publication was originally the third leg of this mitigation and is now deliberately gated on Scott's own timing — which puts *more* weight on the demo discipline, not less: if the repo isn't the forcing function, the working demo has to be. |
| **Scope creep via standards.** ISA-95 can swallow a year. | Nouns, not schemas. B2MML only at the ERP border. PackML as an enum, not a certification. |
| **Clean-room discipline.** | The provenance rule in README, kept current: nothing from any commercial MES's internals, schemas or documentation, and no real plant's data or configuration, ever. Domain understanding comes in; proprietary material does not. |
| **A funded competitor claims the lane** (OpenMes is 6 months old and moving; MCP wrappers are commoditizing). | Speed to M1/M2 (the demanded features), the unclaimed differentiators (gauges, agent-native core, honest-data discipline), and the ERPNext wedge. |
| **Burnout / competing ventures.** | The board decides. This roadmap spends evenings only when FactorySemantics MES is the chosen focus; milestones are sized so one can pause cleanly at any boundary. |

## Where this stands

**As of 2026-09-02.** M0 bootstrapped 2026-08-28. M1–M6 landed 2026-08-31 by adopting MES-TWIN wholesale as the engine and building the missing modules on it (the decisions are recorded in `docs/decisions/`; the private-era history is summarised in `docs/history/private-era.md`). Two simulated plants run continuously as systemd services; `fsmes score` measures the MES against the simulator's scripted truth on every change; the product exposes 27 agent tools and the simulator 13.

**Next, in order** (ratified 2026-09-02):

1. **Foundation** — CI green on every matrix cell (lint had failed on every push since the adoption, so the suite had never run in CI), the docs telling the truth, `fsmes plant migrate`, simulated activity signed by a simulated account.
2. **Agent-first, mechanised** — tool files per module registered into the one `fsmes` server; a parity test that fails when a write route has neither a tool nor a stated reason; an `agent` role that is not admin, `on_behalf_of` on every command, idempotency keys; a first agent eval (can an agent find a scripted fault through the tools alone?).
3. **M7 proper** — the SAP-shaped contract: a typed port, per-operation confirmations carrying cost center, time and component consumption, an outbox with bounded retry and a dead-letter state, all of it visible to agents. ERPNext on the lab machine as the acceptance test.
4. **The OPC suite** — tag browser, triggers as data, the human-approved recommendation queue for PLC write-back, the certificate of analysis, and the washer story as the acceptance test.
5. **Scale hardening before any real plant** — PostgreSQL, tag retention, the missing index, one OPC agent per line.
6. **Then stop and ask.** M1 makes publication *possible*; it does not authorise it. Nothing goes **public** — no public repository, no PyPI, no announcement — until Scott says so explicitly, because he has conversations to have first (decision of 2026-08-29). A **private** remote is not publication and is explicitly permitted: single-copy code on one disk is a backup risk, not a strategic position (decision of 2026-08-31). The line is at anything becoming visible to anyone else.

**Carried decision for M1:** FactorySemantics isn't on PyPI, so the event-model dependency needs a path or git reference (or a publish of that repo) the first time the outbox lands. Worth settling deliberately rather than defaulting to a path dependency that only works on this machine.
