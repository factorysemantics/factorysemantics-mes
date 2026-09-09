# Compatibility

*Explanation. What has actually been tested against what, as of 2026-09-07. "Untested" is a fact, not a warning label.*

| Neighbour | Status | Evidence |
|---|---|---|
| **KEPServerEX** (OPC UA) | tested in the lab | `labs/kepsim`: a trial server fed by ODBC from generated line data; `fsmes opc-verify` against it; certificate exchange and SignAndEncrypt |
| **The bundled OPC UA replay server** | tested continuously | every scored run and CI |
| Other OPC UA servers (Ignition, Siemens, Prosys) | untested | the client is `asyncua`; the tag map is the only server-specific part |
| **ERPNext** | tested against a development bench (2026-08) | not yet against a current stable release in a clean container |
| Odoo, SAP, Oracle | not written | the contract exists (`contract.py`); the modules do not |
| **PostgreSQL 16** | tested | Compose file; the cutlery plant's public demo |
| **SQLite** | tested continuously | the default; every test |
| **Claude Code** as an MCP client | tested | the agent evals drive it headless |
| Claude Desktop, other MCP clients | untested | the server is streamable HTTP over the standard SDK |
| Node-RED, United Manufacturing Hub, Grafana | untested; nothing built | planned bridges, in that order |
| Windows (plant PCs) | tested continuously | CI runs the suite on Windows and Python 3.12/3.13 |
| Python | 3.12, 3.13 | CI |

When a row changes, it changes here first, with the date and what was run.
