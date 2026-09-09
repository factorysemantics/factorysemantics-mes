# Landscape

*Research snapshot 2026-08-28 (web-verified: GitHub API star/activity pulls, vendor pages, release notes). This is the "why does this project deserve to exist" file. Refresh it roughly quarterly; the watch list at the bottom is the part that goes stale first.*

## The benchmark: what "Opcenter-level" actually means

Siemens Opcenter is the MOM umbrella: **Execution** (sold as industry variants — Discrete, Process, Electronics, Pharma, Medical Device, Semiconductor — plus the new low-code Execution Foundation), **APS** (ex-Preactor finite-capacity scheduling), **Quality** (ex-IBS CAQ: APQP, FMEA, inspection planning, SPC, **gauge management**, CAPA, audits), **Intelligence** (analytics), **RD&L** (LIMS/formulation), **Intra Plant Logistics**, and **Opcenter X** (the modular SaaS edition — Siemens itself now validates "start small, modular MES" as the direction). Pricing is quote-only; third-party signals put subscriptions around $1k+/user/month and implementations at $20k–$100k+ (understated for multi-line; €200k–1.5M is the commonly cited full-project range). That's the parity checklist and the price umbrella this project lives under.

## The open-source field (verified 2026-08-28)

| Project | License | Stack | Real scope | Health | Verdict |
|---|---|---|---|---|---|
| [United Manufacturing Hub](https://github.com/united-manufacturing-hub/united-manufacturing-hub) | Apache-2 (console proprietary) | Go | Unified-namespace data infra — **no MES apps anymore** | 394★, €5M Series A 01/2026, active | Pivoted out of the MES lane; complement, not competitor |
| [Rhize / libremfg](https://github.com/libremfg) | Open schemas (AGPL); hub not published | Go/Dgraph/GraphQL | Headless ISA-95 data hub; BYO apps | Docs active, core quiet | Best ISA-95 architecture to *study*; not reusable OSS |
| [Qcadoo MES](https://github.com/qcadoo/MES) | AGPL-3 | Java/Spring | Orders, BOM/routing, basic planning | 932★, still committed | 15-year survivor; dated monolith; zero machine layer; scheduling paywalled |
| [Odoo MRP](https://github.com/odoo/odoo) | LGPL-3 community | Python | MRP core + Maintenance app | 54k★ | Quality, PLM, **IoT box, Shop Floor UI, MPS all Enterprise-only** — MES value deliberately paywalled |
| [ERPNext](https://github.com/frappe/erpnext) | GPL-3 | Python/Frappe | BOM/WO/job cards; v16 added MRP, reservations | 38.6k★, very active | **Maintainer-opened issue [#50827](https://github.com/frappe/erpnext/issues/50827) (Dec 2025) lists the missing MES**: machine monitoring, downtime, OEE, PLC. Best ERP neighbor + the demand doc |
| [frePPLe](https://github.com/frePPLe/frepple) | **MIT** (community, since 2023; was AGPL) | C++/Django | Forecasting + finite-capacity APS | 725★, company-backed | Reuse for real APS; don't rebuild |
| [Apache PLC4X](https://github.com/apache/plc4x) | Apache-2 | Java (+alpha Python) | Multi-protocol PLC drivers | 1.7k★, active | Solid in Java; **PLC4Py is alpha — unusable** for production Python |
| [FUXA](https://github.com/frangoteam/FUXA) | MIT | Node.js | Web SCADA/HMI, many protocols | 4.9k★, active | Best OSS HMI neighbor |
| [Node-RED](https://github.com/node-red/node-red) | Apache-2 | JS | Flow glue; FlowFuse sells "MES in Node-RED" | 23.6k★ | The de-facto stitching tool people use *instead of* an MES |
| [OpenMes](https://github.com/Mes-Open/OpenMes) | AGPL-3 | PHP/Laravel | WO, Gantt, operator UI, andon, QMS-lite, MQTT | Born 2026-02, 107★, daily commits | **Closest new rival in spirit** — young, PHP, solo-led; watch |
| [WebErpMesv2](https://github.com/SMEWebify/WebErpMesv2) | MIT | Laravel/React | Quote→order→quality→CMMS for job shops | 211★, active; past RCE advisories | Niche ERP+MES hybrid, not a platform |
| [iPlusMES](https://github.com/iplus-framework/iPlusMES) | GPL-3 | C#/.NET desktop | Broad (incl. quality, maintenance) | 24★, vendor-backed claims | Real features, dead community, desktop-era |
| ScadaBR | GPL | legacy Java | Legacy SCADA | **CISA KEV 12/2025 — actively exploited** | Do not touch; also a security cautionary tale for web-facing plant software |

**The verdict: the integrated lane is empty.** Nobody open-source ships orders+routing+PLC+quality+OEE+scheduling as one modular system, and *nobody at all* in OSS does gauge/calibration management or CAQ-grade quality. What plants actually do today: Node-RED/benthos ingest → MQTT/Kafka → TimescaleDB/Influx → **Grafana dashboards pretending to be an MES** → ERPNext/Odoo for orders → spreadsheets for quality/maintenance. Notably, Hacker News has essentially zero "open source MES" threads — this niche is invisible to the mainstream OSS crowd, which is both why the lane is empty and a warning that community won't arrive on its own; it lives in ERPNext/Odoo forums, r/PLC, and integrator channels.

## The agentic race (why timing matters)

- **Siemens**: Industrial Copilot in production (thyssenkrupp), CES 2026 announced nine industrial copilots + an "Industrial AI OS" with NVIDIA.
- **Bosch**: Shopfloor Agent in own plants since late 2025 (~€850k/plant/yr claimed), now sold externally. **Decisyon** literally markets "Agentic MES". ABI frames Hannover Messe 2026 as agentic-MES's coming-out party.
- **MCP layer**: Tulip shipped an official MCP server; Ignition's module lands later in 2026; small OSS OPC UA/Modbus MCP servers exist ([kukapay/opcua-mcp](https://github.com/kukapay/opcua-mcp), [OPCUA4MCP](https://github.com/mikakaraila/OPCUA4MCP) — same asyncua+FastMCP stack as this project); the OPC Foundation has its own UA-for-AI prototype.

**Every incumbent bolts agents onto a closed platform; every OSS project predates the agent era. No open-source "MES-as-MCP" exists.** The MCP *wrapper* is commoditizing within ~18 months — the durable position is the agent-operable *core* (permissioned writes, on-behalf-of identity, event-sourced audit), which is a data-model decision, not a wrapper.

## Standards adopted (and how far)

| Standard | Adoption here | Not adopted |
|---|---|---|
| **ISA-95** (2025 edition exists) | Parts 2/3/4 nouns as naming law; Part 3's activity model *is* the module map | B2MML internally (boundary-only, XML+JSON); the libremfg JSON schemas are AGPL — validate against, don't vendor |
| **PackML** (ISA-TR88.00.02) | The 17-state vocabulary as the internal machine-state enum + per-driver mapping; [PackML-MQTT-Simulator](https://github.com/libremfg/PackML-MQTT-Simulator) (MIT) as a test double | PackTags conformance certification |
| **ISO 22400** | Time-element model + OEE/TEEP/FPY/MTBF/MTTR with exact formulas | The 34-KPI full catalog (inventory/throughput KPIs wait for their modules) |
| **ISA-88** | Two ideas now: procedure≠equipment, master vs. control recipe | Batch/recipe module (later family) |
| **Sparkplug B 3.0** (ISO/IEC 20237) | Second transport, after OPC UA | Sparkplug 4.0 (unreleased as of 08/2026) |
| **OPC UA companion specs** (Machinery, PackML, MachineTools) | Semantics inform the equipment interface; asyncua can import NodeSet2 for simulators | Depending on them — brownfield is flat tags through Kepware, and that's who this serves |
| CESMII SM Profiles | Watch | Align — alive on DOE funding, not a de-facto standard |

## Licensing evidence (for the open decision in README)

Comparable projects: **Odoo** AGPL→LGPL+Enterprise (huge, permanent community tension); **ERPNext** GPLv3-everything-free, monetizes hosting (works, needs an ops business); **frePPLe** AGPL→**MIT** 2023 ("grow adoption"); **UMH** AGPL→**Apache-2** 2023 (verbatim reason: integrators must build on it "without any further permission"; cloud-provider risk "no longer" real); **Grafana** Apache→AGPL (survived because it's an end-user tool with a dominant brand); **n8n** fair-code (commercially fine, forfeits the "open source" label that wins engineer champions). AGPL corporate-ban friction is real but lands on the **integrator channel** — which in this industry *is* the distribution. The two closest comparables both left copyleft for exactly that reason. Hence the Apache-2.0 proposal, with copyright kept concentrated (DCO) so every future option stays open.

## Reuse shortlist (adopt, don't build)

frePPLe (APS integration), OR-Tools CP-SAT + PyJobShop (embedded scheduling), FUXA (HMI neighbor), benthos-umh (protocol ingest if ever needed at scale), Atlas CMMS (full-CMMS API integration), Grafana provisioned-not-embedded, segno/python-barcode (symbologies), ERPNext/Odoo (the ERP boundary). Python drivers: asyncua, pymodbus, pylogix, python-snap7 3.x, aiomqtt, pysparkplug.

## Watch list (staleness risk)

- **OpenMes** velocity (the spirit-rival; PHP, AGPL, born 2026-02).
- **ERPNext #50827** — if Frappe ships real MES features, the wedge changes shape (from "fills the hole" to "does what theirs can't: machine layer + quality depth").
- **Ignition's MCP module** (late 2026) — sets the commercial bar for agent access.
- **Sparkplug 4.0** release; **python-snap7 v4** (S7-1200/1500 optimized access); **asyncua** major versions.
- **Timescale (TigerData) licensing** drift; QuestDB enterprise split.
- Hannover Messe 2026 agentic-MES announcements.
