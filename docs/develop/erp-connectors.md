# Writing an ERP connector

*Reference. What an ERP connector must do, how to prove it does, and what
this project requires before it calls one supported. Written 2026-09-10 out
of what the ERPNext connector got wrong first.*

An ERP connector is a small package that plugs into this MES through an
entry point. It is **transport only**: it moves orders down and
confirmations up. Every MES-side rule — what counts as production, when an
operation is finished, what an over-run is — lives in `services.erp` and is
not yours to reimplement.

The architecture makes that easy. What it did not make easy, until this
page existed, was the part that actually goes wrong. The ERPNext connector
learned three things the hard way on 2026-09-09, and all three were fixed
in ERPNext-specific code before they were promoted here:

1. **What the connector needs installed on the far side.** ERPNext needed
   five custom fields on Work Order. The documentation said it needed
   nothing. Every ERP needs *something* — a service exposed, an extension
   field, a staging table, a folder, a permission.
2. **How a write is proved to have landed.** "Raise on failure" is not
   enough when the ERP answers `200` and drops the value.
3. **How a person checks it before trusting it.** A connector nobody can
   check is a connector somebody finds out about during a shift.

## The port

`fsmes.integrations.erp.base`. Six methods: three move the work, three
describe the far side.

| Method | Must do |
|---|---|
| `fetch_orders() -> list[ProductionRequest]` | Return the ERP's open orders as the **typed contract**, not dicts. |
| `acknowledge(order_code) -> None` | Tell the ERP the MES has taken this order, so the next poll does not offer it again. |
| `send_confirmation(confirmation) -> None` | Deliver one confirmation. **Raise** unless the ERP kept what was sent. |
| `requirements() -> list[Requirement]` | What must exist on the ERP side. Empty is a valid answer. |
| `setup() -> list[SetupOutcome]` | Create or verify those requirements. Idempotent. |
| `check() -> CheckResult` | Say in plain words whether this would work right now. No side effects. |

The last three have defaults — inherit `ErpConnector` and you get them —
so a transport that genuinely needs nothing is not forced to invent
ceremony, and a connector written before these existed keeps working.

```python
from fsmes.integrations.erp.base import CheckResult, ErpConnector, Requirement, SetupOutcome
from fsmes.integrations.erp.contract import Confirmation, ProductionRequest


class SapAdapter(ErpConnector):
    def fetch_orders(self) -> list[ProductionRequest]: ...
    def acknowledge(self, order_code: str) -> None: ...
    def send_confirmation(self, confirmation: Confirmation) -> None: ...
```

`fsmes erp requirements`, `fsmes erp setup` and `fsmes erp check` are these
three methods and nothing else. They act on whatever `MES_ERP_MODE` names,
so your connector gets all three commands the moment it is installed.

## What crosses the boundary

`fsmes.integrations.erp.contract`, and nothing else. `ProductionRequest`
comes down; `OperationConfirmation` (one step: quantities, cost centre,
times, consumed lots) and `OrderCompletion` (the order-level close and the
finished lot) go up. The shape is SAP-shaped on purpose — a confirmation
per operation is what CO11N wants and what ERPNext's Job Cards and Stock
Entries are built from.

No money crosses. The MES reports quantities and time against a cost
centre; the ERP owns what they are worth.

`ProductionRequest.from_payload` accepts the loose spellings ERPs actually
send (`code` / `order` / `id`, `material` / `product`), so a thin mapping
is usually enough.

## Obligation 1: say what you need

```python
def requirements(self) -> list[Requirement]:
    return [
        Requirement(
            name="Z_MES_CONF",
            where="the SAP system, as an RFC-enabled function module",
            what="accepts one operation confirmation and posts it against the order",
            why="without it no production the MES counted ever reaches SAP",
            created_by_setup=False,
        ),
    ]
```

Four fields, all required, because a requirement nobody can act on is
documentation of a problem rather than a fix. `name` is what the ERP calls
it. `where` is where it lives. `what` is enough for whoever administers
that ERP to build it. `why` is what breaks without it — no requirement is
self-evident to the person being asked to do the work.

`created_by_setup=False` is not a lesser answer. The MES can create a
Custom Field on a Frappe site; it cannot create a transport request in
somebody's SAP. Saying so is the point, and `fsmes erp requirements` prints
the list to send them.

## Obligation 2: set it up, twice

`setup()` is run by people who are not sure whether they ran it. The second
run must change nothing and must not fail. Return one `SetupOutcome` per
requirement, naming it — `created`, `already there`, `must be done by hand`.
Never a count: a person can check `created` against a named field and cannot
check `5 fields`.

## Obligation 3: check, and admit what you did not check

`check()` returns a `CheckResult`: lines, each with its own verdict.

| Verdict | Means |
|---|---|
| `ok` | verified, just now, against the ERP |
| `not ok` | this would stop the connector working; `fsmes erp check` exits non-zero |
| `unknown` | could not be checked without a side effect |
| `note` | a continuation of the line above — what to do about it |

`unknown` is why this is not a boolean. The REST connector can prove it
reached the ERP's order list; it cannot prove `POST /confirmations` accepts
a confirmation without posting one, and `check` must be safe to run against
production. Reporting that as `ok` would be inventing a fact about somebody
else's ERP — house rule 2, unknown is a valid answer and zero is not. A
check with an unknown in it prints `Ready, as far as anything above was
checked.`

Name what is wrong, not that something is. `custom_mes_good_qty is a Data
field on Work Order, but the MES writes a Float` is actionable;
`configuration invalid` is not.

## Obligation 4: prove the write landed

This is the one that costs the most to get wrong, and it is the reason this
page exists.

**Measured on ERPNext v15.120.0 on 2026-09-09**, in CI, against a real
container: a `PUT` to a submitted Work Order naming a field the doctype does
not have **succeeds**. Frappe answers `200`, returns a normal document, and
the value is neither stored nor returned. Nothing anywhere says a number was
dropped.

So "raise on failure" is not a sufficient instruction, because that is not a
failure by any definition the transport offers. A connector that reports it
as success makes the MES mark a confirmation *delivered* for a number no ERP
ever stored — and the outbox, which exists precisely to retry, never retries
it.

**Read your writes back where the ERP will let you**, and compare them
against what you sent:

```python
returned = self.client.update("Work Order", order, values)
lost = [name for name in values if name not in returned]
if lost:
    raise ErpNextError(f"the ERP accepted the write to {order} but kept nothing in {lost}")
```

Be forgiving about form and strict about substance: a site's float precision
can round `6.001` to `6.00`, and a Check field comes back as `0`/`1`. A
number that agrees to a hundredth agrees. A number that is gone does not.

Where the ERP genuinely offers no read-back, say so on your connector's
page. That is a real limitation of that ERP and a plant is entitled to know
it before the first over-run.

## When the ERP says no

Raising is right for both of these, and they are not the same thing:

- **The ERP could not take the message.** It was down, the session had gone,
  the network dropped. Time may repair it, and the outbox's job is to keep
  trying — eight attempts with growing backoff, then dead.
- **The ERP read the message and refused it.** Nothing about the message
  will be different next time, so eight more attempts only delay the moment
  a person hears about it and bury the ERP's own words under seven copies of
  themselves.

If your connector can tell the second from the first, say so on the
exception: **`permanent = True`**, and the sync worker marks the message dead
on the spot with the ERP's sentence as its error, rather than backing off.
Nothing declares this in the port, so a connector that never sets it keeps
exactly the old behaviour.

```python
class ErpNextRefused(ErpNextError):
    """ERPNext read the request, understood it, and said no."""

    permanent = True
```

Only claim it where you have measured it. ERPNext's is
[an over-run beyond the site's over-production allowance](../operate/erpnext.md#when-the-line-made-more-than-the-order-asked-for):
an HTTP 417 naming the quantity it would have allowed, with nothing booked.
Everything else the connector meets is still retried, because nobody has
measured it.

And do not book *something else* instead. Posting the quantity the ERP would
have accepted, or a draft for a person to fix, invents a decision nobody
made. A refusal is a business fact: what the MES counted and what the ERP
will hold genuinely differ, and somebody has to resolve that. Leave the
message dead, put the reason where a person will find it, and let them retry
it when they have.

## The conformance suite

`fsmes.integrations.erp.conformance` ships **inside the package**, so a
connector published on its own can be held to the same bar without
vendoring this project's test tree.

You supply a `ConformanceCase`: the connector, and the three things only you
can do — put an order on your ERP, read back what your ERP received, and
break your ERP so a failure can be proved to fail.

```python
from fsmes.integrations.erp import conformance
from fsmes.integrations.erp.conformance import ConformanceCase


def a_case() -> ConformanceCase:
    fake = ScriptedSap()
    return ConformanceCase(
        name="sap (against a scripted SAP)",
        adapter=SapAdapter(fake.client),
        place_order=fake.place,
        delivered=lambda: fake.confirmations,
        break_the_far_side=fake.remove_the_function_module,
    )


def test_the_sap_connector_meets_the_erp_contract():
    conformance.check_conformance(a_case)
```

Eight obligations, each of them something a connector got wrong once:

1. an order on the ERP arrives as a typed `ProductionRequest`
2. an acknowledged order is not offered again
3. a confirmation reaches the ERP with its quantities
4. a write the ERP did not keep raises rather than returning quietly
5. every requirement says where it lives, what it is and why
6. `setup` is idempotent
7. `check` passes on a far side that is ready — and says something
8. `check` fails when the far side is not ready

`check_conformance` takes a *factory*, not a case: half the obligations
deliberately break the far side, and the next one must not inherit the
wreckage.

`tests/test_erp_conformance.py` runs all eight against every connector that
ships. Adding a connector there is one line.

**Passing is not the same as being tested.** The suite holds a connector to
the contract against whatever far side you supply, and a fake that lies in
the same direction the connector does will pass. That is why the next two
sections exist.

## Test against the real thing

A connector with no live test is a connector nobody has run.

The ERPNext connector's `ERPNext (live)` CI job brings a real ERPNext up
from `labs/erpnext/docker-compose.yml`, with every image pinned by digest,
seeds it and drives the whole round trip — asserting against ERPNext's own
documents, never against what the MES believes it sent. It runs on pull
requests that touch the connector, and deliberately not on a schedule: the
digests are pinned, so a nightly run would test identical bytes and prove
nothing. Changing a pin is what makes it worth running again.

It costs about three minutes a run and it is where the `200`-and-drop
finding came from. Reasoning from an ERP's source got the right answer that
time; it is not a method anyone should rely on twice.

## Register it

```toml
[project.entry-points."fsmes.modules"]
sap = "fsmes_sap.adapter:from_settings"
```

`from_settings(settings)` is handed the MES `Settings` object and returns
your connector. `MES_ERP_MODE=sap` then selects it, `fsmes info` lists it,
and the three `fsmes erp` commands act on it. Nothing in this package
changes. See [your first module](your-first-module.md) and decision
[0008](../decisions/0008-erp-connectors-are-modules.md).

## Say what you tested, and when

Add a row to [compatibility](../operate/compatibility.md): the ERP, the
**exact version** you tested against, and the **date**. A connector with no
live test says so in its row. This is a rule, not a habit — see decision
[0020](../decisions/0020-what-supported-means-for-an-erp-connector.md),
which is also where the three words this project uses about connectors are
defined.

Short version: a connector is **supported** when it passes the conformance
suite, is tested against a stated version of a real system on a schedule
somebody maintains, and has a page saying what it does not cover. Anything
else is **contributed** or **experimental**, and the compatibility table
says which.

Odoo, SAP, Oracle and NetSuite are none of these today. The contract
exists; the connectors do not. This page is for the person who would write
one.

## See also

- [The ERPNext connector](../operate/erpnext.md) — the reference
  implementation, and what it is and is not tested against
- [Compatibility](../operate/compatibility.md) — what has been tested
  against what
- [Your first module](your-first-module.md) — the entry-point mechanism
- Decision records
  [0008](../decisions/0008-erp-connectors-are-modules.md) and
  [0020](../decisions/0020-what-supported-means-for-an-erp-connector.md)
- The code: `src/fsmes/integrations/erp/base.py`, `contract.py`,
  `conformance.py`
