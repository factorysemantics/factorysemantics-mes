# 0008 — ERP connectors are modules inside the package

- **Status:** accepted
- **Date:** 2026-09-06
- **Deciders:** @kalwei

## Context
The ERPNext maintainers published the MES features ERPNext lacks (erpnext#50827, 2025-12, closed with nothing shipped). Frappe's Marketplace lists only Frappe-framework apps. The maintainer wants each ERP — ERPNext, SAP, Oracle — to be a module of this package.

## Options considered
| Option | For | Against |
|---|---|---|
| MES-side module per ERP, in this package, Apache-2.0; a thin Frappe app later only for the Marketplace listing | nothing to install on the ERP side; one install path; the connector is tested with the MES | the Marketplace shop window needs a second, tiny repository |
| A Frappe app that *is* the integration | Marketplace-native | puts MES logic in GPL-3 Frappe code, split across two projects |
| One generic connector with mappings | fewer modules | every ERP's API is different enough that the mapping becomes a module anyway |

## Decision
Each ERP connector is a module in `fsmes.integrations.erp.<system>`, registered as a `fsmes.modules` entry point named after its `MES_ERP_MODE` value, transport-only against the typed contract. ERPNext exists; SAP and Oracle do not (2026-09-07) — only the SAP-shaped contract does, and the docs say so. A GPL-3 Frappe app `erpnext_fsmes` will exist only to list the connector on the Marketplace.

## Consequences
Easy: a plant installs one package. Hard: the connector must be verified against a current stable ERPNext in a clean container before it is called supported — that is the open item.

## House rules touched
Rule 4: which ERP a plant talks to is a setting.
