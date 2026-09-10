# The ERPNext connector

*How-to. Work orders in, confirmations out, against an ERPNext site. One
command prepares the ERPNext side; no Frappe app is installed.*

The connector is a module inside this package, registered as the
`fsmes.modules` entry point `erpnext`, and selected by `MES_ERP_MODE=erpnext`.
It speaks ERPNext's REST API as a user or API key; it does not install a
Frappe app. (A thin Frappe-side app for the Marketplace is planned and not
built, as of 2026-09-07.)

What it does need is five custom fields on ERPNext's Work Order doctype. They
carry the two things ERPNext has nowhere to put — whether the MES has taken
an order, and what the machines actually counted — and `fsmes erp setup`
creates them (see [below](#prepare-the-erpnext-side)). Until it has run, the
connector cannot work. **Before 2026-09-09 this page said nothing was needed
on the ERPNext side. That was wrong.**

## Configure

```bash
MES_ERP_MODE=erpnext
MES_ERPNEXT_BASE_URL=http://erpnext.example:8080
MES_ERPNEXT_SITE=mes.example          # the Host header; required on a multi-site bench
MES_ERPNEXT_API_KEY=...               # User → API Access in ERPNext; preferred for a worker
MES_ERPNEXT_API_SECRET=...
MES_ERPNEXT_COMPANY="Your Company"    # optional; empty imports every company on the site
MES_ERPNEXT_POST_STOCK_ENTRY=true     # false records what the MES counted without moving stock
MES_ERP_POLL_SECONDS=5
```

Username and password (`MES_ERPNEXT_USER`, `MES_ERPNEXT_PASSWORD`) work too;
the defaults are the Frappe development bench's.

## Prepare the ERPNext side

| Field on Work Order | Type | What it carries |
|---|---|---|
| `custom_mes_synced` | Check | The MES has imported this order. Clear it to re-send. |
| `custom_mes_good_qty` | Float | Good quantity the machines counted. |
| `custom_mes_scrap_qty` | Float | Machine-counted scrap. ERPNext has no native field for it. |
| `custom_mes_over_qty` | Float | How far past the ordered quantity the line ran. |
| `custom_mes_lot` | Data | The finished lot the MES booked. |

Five fields, all `allow_on_submit`, because a submitted work order is
precisely when they change. With the connection configured above:

```bash
fsmes erp requirements   # the five fields, and why each one exists — the list to send whoever runs the site
fsmes erp setup          # creates any of the five that are missing; safe to run twice
fsmes erp check          # says whether URL, credentials, fields and company are all in order
```

These three commands are the ERP port's own `requirements()`, `setup()` and
`check()`. They act on whatever `MES_ERP_MODE` names, so they read the same
for a connector somebody else publishes — see
[writing an ERP connector](../develop/erp-connectors.md).

`fsmes erp check` exits non-zero if anything would stop the connector
working, so it can gate a deployment. It names the field that is wrong rather
than saying the configuration is bad.

The account you configure needs permission to create a Custom Field —
System Manager, in a stock ERPNext — for `setup` only. The sync worker itself
needs no more than read and write on Work Order and Stock Entry.

### If a field is missing

Frappe accepts a `PUT` naming a field its doctype does not have. It answers
`200` and drops the value. So a missing field does not look like an error at
the HTTP level — it looks like success with the number gone. That is
[measured against a live ERPNext](#what-a-missing-custom-field-actually-does),
not inferred from Frappe's source.

The MES therefore reads back every write and compares it against what it
sent. A field that did not survive raises, the confirmation stays in the
outbox and retries, and the log names the field. The MES cannot end up
believing a number reached ERPNext when it did not. `fsmes run-erp-sync`
also checks all five when it starts and says on the console if any are
missing; it starts anyway, because an ERP that is briefly unreachable is not
a reason to refuse to run.

## Run it

`fsmes run-erp-sync`. In Compose it is the `erp-sync` service.

## What crosses the boundary

| Direction | ERPNext object | MES object |
|---|---|---|
| in | submitted **Work Order** (docstatus 1), not yet taken by the MES, of `MES_ERPNEXT_COMPANY` if one is set | production request → work order with the routing's operations |
| out, per operation | a comment on the Work Order: operation, equipment, cost centre, good and scrap, machine minutes, lots consumed | operation confirmation from the outbox |
| out, on completion | quantities recorded on the Work Order; a **Manufacture stock entry** when `POST_STOCK_ENTRY` is on | order confirmation |

Machine-counted scrap is recorded on the order itself, not only in a
comment, because it is the number the plant argues about and ERPNext has
nowhere native for it.

Confirmations go through an outbox with retry and backoff; an ERPNext that
is down for an hour gets the hour's confirmations when it returns, in order.
A confirmation ERPNext *refuses* is a different thing and is not retried —
see [when the line made more than the order asked
for](#when-the-line-made-more-than-the-order-asked-for).

Leaving `MES_ERPNEXT_COMPANY` empty imports the work orders of every company
on the site, which is what a single-company ERPNext wants. A bench that holds
more than one company's books must set it, or this MES will run another
business's orders.

## Verified against

**ERPNext v15.120.0** — Frappe v15, MariaDB 10.6 — in a clean container, on
every pull request that touches this connector. The `ERPNext (live)` job
brings the site up from `labs/erpnext/docker-compose.yml` (images pinned to
digests, listed in `labs/erpnext/VERSIONS.md`), runs ERPNext's setup wizard
and `labs/erpnext/seed_erpnext.py`, and then runs `tests/test_erpnext_live.py`.

Every assertion in that round trip reads ERPNext's own documents back. What
the MES believes it sent is not evidence. It proves:

- a submitted Work Order reaches the MES with its item, quantity and dates;
- acknowledging it sets `custom_mes_synced` on ERPNext's document, and the
  next fetch no longer offers it;
- a confirmation lands as the quantity fields and the lot on the Work Order,
  as ERPNext's own `produced_qty`, as one submitted Manufacture stock entry
  of the right quantity, and as a comment;
- sending the same confirmation twice still leaves exactly one stock entry —
  the retry that would otherwise book the plant's production twice;
- `MES_ERPNEXT_POST_STOCK_ENTRY=false` still records every number;
- an over-run inside ERPNext's over-production allowance is booked for every
  unit, and one beyond it is refused whole and delivered nowhere
  ([below](#when-the-line-made-more-than-the-order-asked-for));
- the site written to is the one the `Host` header named.

**Not covered, as of 2026-09-10.** ERPNext v16 — nothing here claims
anything about it. The sync worker and the MES-side booking that decides
what to confirm: the round trip drives the adapter, not `fsmes run-erp-sync`.
A bench serving more than one company. See
[compatibility](compatibility.md). If you run this against a real site, an
issue with the versions of both sides is the most useful thing you can send.

## When the line made more than the order asked for

The MES books every unit a machine counted, and an order for 400 that ran to
420 is confirmed as 420 good with 20 over
([decision 0019](../decisions/0019-count-everything-the-machine-counted.md)).
ERPNext has an opinion about that of its own: **Manufacturing Settings →
Over Production Percentage For Work Order**, zero out of the box.

Measured against v15.120.0 on 2026-09-10, in the live job, three cases:

| ERPNext's allowance | Ordered | MES counted | What ERPNext did |
|---|---|---|---|
| 10% | 400 | 420 | took it whole: `produced_qty` 420, one Manufacture entry of 420, the order Completed |
| 10% | 400 | 500 | **refused**: HTTP 417, `For quantity 500.0 should not be greater than allowed quantity 440.0`. `produced_qty` stayed 0 |
| 0% | 400 | 401 | **refused**, the same way, at `allowed quantity 400.0` |

The refusal is whole. ERPNext does not book the 440 it would have allowed
and does not leave a draft: nothing moves, and its own `produced_qty` does
not change.

**What the connector does about it.** The confirmation is *not* delivered —
what the ERP accepted was nothing, and nothing is what gets reported as
accepted. Because retrying sends the identical stock entry and gets the
identical answer, the message does not spend eight attempts and an hour
finding that out: it goes straight to `dead` in the outbox, carrying
ERPNext's own sentence as its error. `dead` is what this outbox has always
meant by *a person decides*.

Three things are true at once after a refusal, and all three are visible:

1. **The MES's record does not change.** The line made 500; it made 500 in
   the MES whatever ERPNext thinks.
2. **`custom_mes_good_qty` and `custom_mes_over_qty` on the Work Order still
   say 500 and 100.** They are what the machines counted, which is exactly
   what ERPNext has nowhere else to put, and a person deciding what to do
   needs the number.
3. **ERPNext's `produced_qty` says 0**, and a comment on the Work Order says
   why: what was refused, what the MES counted, and ERPNext's own words.

That gap is a business fact, not an error to retry: somebody raises the
allowance, or accounts for the surplus another way. When they have,
`POST /erp/outbox/{id}/retry` sends the same confirmation again — and if the
allowance now covers it, it lands.

There is no partial booking. Posting the 440 ERPNext would have taken would
invent a decision nobody made and leave 60 units unaccounted for in both
systems.

### What a missing custom field actually does

Measured on ERPNext v15.120.0, not reasoned about: a `PUT` to a submitted
Work Order naming a field the doctype does not have **succeeds**. Frappe
answers `200`, the response is a normal document, and the value is neither
stored nor returned. Nothing anywhere says a number was dropped.

The experiment is `test_erpnext_takes_a_write_to_a_field_that_does_not_exist_and_loses_it`:
it installs a probe custom field, proves a write to it survives, deletes the
field, writes the same shape again, and reads the document back. It runs on
every live job, so if ERPNext ever changes its mind, that is where it shows.

This is why the read-back check above exists. ERPNext gives the connector no
signal at all, so comparing what was written against the document that comes
back is the only way to know a number landed.

## See also

- [Writing an ERP connector](../develop/erp-connectors.md) — this connector is
  the reference implementation of that port, and every obligation on that page
  is something this connector got wrong first
- [Settings reference](../reference/settings.md) — every `MES_ERPNEXT_*` value
- [Compatibility](compatibility.md) — what is tested against what, and when
- Decision records [0008](../decisions/0008-erp-connectors-are-modules.md) and
  [0020](../decisions/0020-what-supported-means-for-an-erp-connector.md)
- The typed contract: `src/fsmes/integrations/erp/contract.py`
