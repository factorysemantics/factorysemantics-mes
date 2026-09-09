# The ERPNext connector

*How-to. Work orders in, confirmations out, against an ERPNext site, with nothing installed on the ERPNext side.*

The connector is a module inside this package, registered as the
`fsmes.modules` entry point `erpnext`, and selected by `MES_ERP_MODE=erpnext`.
It speaks ERPNext's REST API as a user or API key; it does not install a
Frappe app. (A thin Frappe-side app for the Marketplace is planned and not
built, as of 2026-09-07.)

## Configure

```bash
MES_ERP_MODE=erpnext
MES_ERPNEXT_BASE_URL=http://erpnext.example:8080
MES_ERPNEXT_SITE=mes.example          # the Host header; required on a multi-site bench
MES_ERPNEXT_API_KEY=...               # User → API Access in ERPNext; preferred for a worker
MES_ERPNEXT_API_SECRET=...
MES_ERPNEXT_COMPANY="Your Company"
MES_ERPNEXT_POST_STOCK_ENTRY=true     # false records what the MES counted without moving stock
MES_ERP_POLL_SECONDS=5
```

Username and password (`MES_ERPNEXT_USER`, `MES_ERPNEXT_PASSWORD`) work too;
the defaults are the Frappe development bench's.

Run the worker: `fsmes run-erp-sync`. In Compose it is the `erp-sync`
service.

## What crosses the boundary

| Direction | ERPNext object | MES object |
|---|---|---|
| in | submitted **Work Order** (docstatus 1) for the company | production request → work order with the routing's operations |
| out, per operation | a comment on the Work Order: operation, equipment, cost centre, good and scrap, machine minutes, lots consumed | operation confirmation from the outbox |
| out, on completion | quantities recorded on the Work Order; a **Manufacture stock entry** when `POST_STOCK_ENTRY` is on | order confirmation |

Machine-counted scrap is recorded on the order itself, not only in a
comment, because it is the number the plant argues about and ERPNext has
nowhere native for it.

Confirmations go through an outbox with retry and backoff; an ERPNext that
is down for an hour gets the hour's confirmations when it returns, in order.

## Verified against

A local Frappe bench during development (2026-08). **Not yet verified
against a current stable ERPNext release in a clean container** — that test
is on the list before the connector is called supported. See
[compatibility](compatibility.md). If you run it against a real site, an
issue with the versions of both sides is the most useful thing you can send.

## See also

- [Settings reference](../reference/settings.md) — every `MES_ERPNEXT_*` value
- Decision record [0008](../decisions/0008-erp-connectors-are-modules.md)
- The typed contract: `src/fsmes/integrations/erp/contract.py`
