# Compatibility

*Explanation. What has actually been tested against what, as of 2026-09-09. "Untested" is a fact, not a warning label.*

| Neighbour | Status | Evidence |
|---|---|---|
| **KEPServerEX** (OPC UA) | tested in the lab | `labs/kepsim`: a trial server fed by ODBC from generated line data; `fsmes opc-verify` against it; certificate exchange and SignAndEncrypt |
| **The bundled OPC UA replay server** | tested continuously | every scored run and CI |
| Other OPC UA servers (Ignition, Siemens, Prosys) | untested | the client is `asyncua`; the tag map is the only server-specific part |
| **ERPNext v15.120.0** | tested continuously | the `ERPNext (live)` job: a clean container from `labs/erpnext/docker-compose.yml`, pinned to image digests, seeded and put through the whole round trip in `tests/test_erpnext_live.py`, every pull request that touches the connector (2026-09-09) |
| ERPNext v16 | untested | nothing claims anything about it; the live job pins v15 |
| Odoo, SAP, Oracle | not written | the contract exists (`contract.py`); the modules do not |
| **PostgreSQL 16** | tested | Compose file; the cutlery plant's public demo |
| **SQLite** | tested continuously | the default; every test |
| **Claude Code** as an MCP client | tested | the agent evals drive it headless |
| Claude Desktop, other MCP clients | untested | the server is streamable HTTP over the standard SDK |
| Node-RED, United Manufacturing Hub, Grafana | untested; nothing built | planned bridges, in that order |
| Windows (plant PCs) | tested continuously | CI runs the suite on Windows and Python 3.12/3.13 |
| Python | 3.12, 3.13 | CI |

When a row changes, it changes here first, with the date and what was run.
