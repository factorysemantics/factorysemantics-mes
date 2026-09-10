# Compatibility

*Explanation. What has actually been tested against what, as of 2026-09-10. "Untested" is a fact, not a warning label.*

| Neighbour | Status | Evidence |
|---|---|---|
| **KEPServerEX** (OPC UA) | tested in the lab | `labs/kepsim`: a trial server fed by ODBC from generated line data; `fsmes opc-verify` against it; certificate exchange and SignAndEncrypt |
| **The bundled OPC UA replay server** | tested continuously | every scored run and CI |
| Other OPC UA servers (Ignition, Siemens, Prosys) | untested | the client is `asyncua`; the tag map is the only server-specific part |
| **ERP connectors** | see the table below | they have rules of their own |
| **PostgreSQL 16** | tested | Compose file; the cutlery plant's public demo |
| **SQLite** | tested continuously | the default; every test |
| **Claude Code** as an MCP client | tested | the agent evals drive it headless |
| Claude Desktop, other MCP clients | untested | the server is streamable HTTP over the standard SDK |
| Node-RED, United Manufacturing Hub, Grafana | untested; nothing built | planned bridges, in that order |
| Windows (plant PCs) | tested continuously | CI runs the suite on Windows and Python 3.12/3.13 |
| Python | 3.12, 3.13 | CI |

When a row changes, it changes here first, with the date and what was run.

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
| **ERPNext** | supported | v15.120.0 (Frappe v15, MariaDB 10.6) | 2026-09-09, and on every pull request that touches it since | the `ERPNext (live)` job: a clean container from `labs/erpnext/docker-compose.yml`, images pinned by digest, seeded and put through the whole round trip in `tests/test_erpnext_live.py`, asserting against ERPNext's own documents. Also passes the conformance suite |
| ERPNext v16 | experimental | **no live test** | — | nothing here claims anything about it; the live job pins v15 |
| **file exchange** (B2MML-lite) | supported | not applicable — the far side is a folder | 2026-09-10 | `tests/test_file_erp.py` and the conformance suite |
| **REST** (against the bundled mock ERP) | supported | not applicable — the far side ships with it | 2026-09-10 | `tests/test_erp_contract.py` and the conformance suite. Against a *real* ERP's REST API it is **untested**, and the three endpoints it needs are in `fsmes erp requirements` |
| Odoo, SAP, Oracle, NetSuite | not written | — | — | the contract exists (`contract.py`), the conformance suite exists (`conformance.py`), the connectors do not. [How to write one](../develop/erp-connectors.md) |
