# Compatibility

*Explanation. What has actually been tested against what, as of 2026-09-14 — the state of `main` that 0.2.0 is cut from. "Untested" is a fact, not a warning label.*

**Every row states the date it was last checked and what was run on that
date.** A row with no date is a row nobody can age.

| Neighbour | Status | Last checked | Evidence |
|---|---|---|---|
| **KEPServerEX** (OPC UA) | tested in the lab | **before 2026-09-08, exact date not recorded** | `labs/kepsim`: a trial server fed by ODBC from generated line data; `fsmes opc-verify` against it; certificate exchange and SignAndEncrypt. It was run in the private era and has not been run since the repository went public — nothing in CI touches it |
| **The bundled OPC UA replay server** | tested continuously | 2026-09-14 | every scored run, and CI on every pull request |
| Other OPC UA servers (Ignition, Siemens, Prosys) | **untested** | never | nothing here has been on the other end of one. The client is `asyncua`; the tag map is the only server-specific part |
| **ERP connectors** | see the table below | see below | they have rules of their own, and each row carries its own date |
| **PostgreSQL 16.15** | tested continuously | 2026-09-14 | the `postgres` cell in CI: `alembic upgrade head` from an empty database and then the whole suite, against a PostgreSQL 16.15 service container pinned by digest, on every pull request. Also the Compose file and the cutlery plant's public demo |
| **SQLite** | tested continuously | 2026-09-14 | the default. The whole suite runs on in-memory SQLite on every pull request, on Ubuntu and Windows, Python 3.12 and 3.13; `wheel-demo` runs `fsmes demo` on a SQLite file from the built wheel, and `upgrade_from_previous_release.sh` upgrades a database made by the 0.1.2 wheel from PyPI. Version: whatever CPython ships |
| **Claude Code** as an MCP client | tested | scoring 2026-09-14; **the live-model run's date is not recorded** | the agent evals drive it headless. `tests/test_agent_eval.py` scores them on every pull request; the run that puts a real model against the live tool surface is `fsmes agent-eval`, a command a person types, and nothing records when it last ran |
| Claude Desktop, other MCP clients | **untested** | never | the server is streamable HTTP over the standard SDK |
| **Jev** (TypeSafe), as a judgment model | **untested**; nothing in this repository calls it | never called; the vendor's answers in this row are dated **2026-09-17** | No request has been made to it from this repository and no accuracy number has been produced here, so there is nothing measured to report. What is known is what TypeSafe answered in writing to the maintainer on 2026-09-17, and it is the vendor's statement rather than this project's measurement: **hosted API only** — no on-premises, VPC or edge option now or planned; **United States only** — no public EU region, and no subprocessor list was given; **a version can be pinned**, with no fixed forced-retirement window; **250,000 tokens per second and 1,200 requests per minute**, with no availability commitment beyond those limits; **zero data retention is available on the enterprise tier only**, and no retention period was given for any other tier. The consequence for a plant is on the survey's preconditions ([`docs/ai/JEV.md`](../ai/JEV.md)): every state class above `catalogue` in decision [0032](../decisions/0032-a-hosted-judgment-and-the-shadow.md) is gated on the enterprise tier or on that plant accepting retention it has no stated period for, and an air-gapped site cannot use it at any tier |
| **MQTT brokers** (Mosquitto, HiveMQ, EMQX) | **untested** | never | **no broker has been on the other end of either direction.** Both directions are built — `fsmes uns publish` and `fsmes inbound subscribe` — and both are driven by a fake transport in the suite. No broker has run against either; this repository does not start brokers on the machine it is developed on. The publisher has one opt-in test against a real broker (`MES_UNS_TEST_BROKER`) |
| Node-RED, United Manufacturing Hub, Grafana | **untested**; the MQTT halves exist | never | the namespace is published and subscribed to over plain MQTT-JSON under an ISA-95 tree; no UMH or Node-RED deployment has been on the other end of it |
| Windows (plant PCs) | tested continuously | 2026-09-14 | CI runs `ruff` and the whole suite on `windows-latest`, Python 3.12 and 3.13, on every pull request. `tzdata` is a Windows-only dependency so `zoneinfo` can resolve a plant's zone. No plant PC in a real plant has run it |
| Python | 3.12, 3.13 | 2026-09-14 | CI, both on Ubuntu and on Windows. 3.11 and earlier are not supported; 3.14 is untested |

When a row changes, it changes here first, with the date and what was run.

Nothing in this table has been run in a real plant. Every "tested
continuously" row means a CI runner or a lab bench on the maintainer's own
machine, and the [roadmap](https://github.com/factorysemantics/factorysemantics-mes/blob/main/ROADMAP.md)
says so too.

## ERP connectors

Three words, defined in decision
[0020](../decisions/0020-what-supported-means-for-an-erp-connector.md), and
nothing here uses any others:

- **supported** — implements the ERP port, passes the conformance suite, and
  is tested against a stated version of a real system by a job somebody
  maintains.
- **contributed** — implements the port and passes the conformance suite.
  Nobody here has run it against a real system.
- **experimental** — exists. The row says what is missing.

**The rule: every row states the exact version tested and the date it was
tested.** A connector with no live test says so, in those words, in its own
row. A connector nobody has run against a real system is not a connector
anybody should trust with a shift, and a table that lets that be inferred
rather than read is a table that misleads.

| Connector | Standing | Version tested | Date | Evidence |
|---|---|---|---|---|
| **ERPNext** | supported | v15.120.0 (Frappe v15, MariaDB 10.6) | 2026-09-10, and on every pull request that touches the connector, its tests or its fixture since | the `ERPNext (live)` job: a clean container from `labs/erpnext/docker-compose.yml`, images pinned by digest, seeded and put through the whole round trip in `tests/test_erpnext_live.py`, asserting against ERPNext's own documents. Includes what ERPNext does with an over-run, measured 2026-09-10: inside its own over-production allowance it books every unit, beyond it it refuses the stock entry whole and books nothing ([what the connector does about it](erpnext.md#when-the-line-made-more-than-the-order-asked-for)). Also passes the conformance suite |
| ERPNext v16 | experimental | **no live test** | — | nothing here claims anything about it; the live job pins v15 |
| **file exchange** (B2MML-lite) | supported | not applicable — the far side is a folder | 2026-09-14 | `tests/test_file_erp.py`, `tests/test_erp_confirmation_files.py` and the conformance suite. The folder layout, the published schema and `fsmes erp validate` are on [the confirmation handoff](confirmation-files.md) |
| **REST** (against the bundled mock ERP) | supported | not applicable — the far side ships with it | 2026-09-14 | `tests/test_erp_contract.py` and the conformance suite. Against a *real* ERP's REST API it is **untested**, and the three endpoints it needs are in `fsmes erp requirements` |
| Odoo, SAP, Oracle, NetSuite | not written | — | — | the contract exists (`contract.py`), the conformance suite exists (`conformance.py`), the connectors do not. The confirmations are SAP-*shaped* and now [published with a schema and worked example files](confirmation-files.md), but no SAP has consumed one. [How to write one](../develop/erp-connectors.md) |
