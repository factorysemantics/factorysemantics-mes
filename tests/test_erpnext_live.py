"""The ERPNext round trip, against a real ERPNext.

`test_erpnext.py` scripts the transport, so it answers the questions that
survive a version bump: field mapping, the acknowledge handshake, retry
safety. It cannot answer the other half — whether the field names exist,
whether ERPNext accepts what we send, whether the stock entry really posts.
Nothing had ever asked ERPNext itself. This does.

Every assertion here reads ERPNext's own state back. What the MES believes
it sent is not evidence; a 200 from Frappe is not evidence either, which is
one of the things this file measures.

**These tests fail rather than skip when there is no ERPNext.** The old live
test skipped, `pyproject.toml` deselects `slow`, and CI ran plain `pytest` —
so it ran nowhere and said nothing for months. Selecting `-m erpnext_live`
is a statement that an ERPNext is supposed to be there.

Run them the way `.github/workflows/erpnext-live.yml` does:

    docker compose -f labs/erpnext/docker-compose.yml up -d
    python labs/erpnext/seed_erpnext.py --url http://127.0.0.1:8080 --site mes.localhost
    python -m pytest -m erpnext_live

**What this covers and what it does not.** It covers the adapter: the whole
conversation between `ErpNextAdapter` and ERPNext, in both directions. It
does not drive `integrations.erp.sync` or the MES database, so it proves
what the connector says to ERPNext and what ERPNext does with it, not the
MES-side booking that decides what to say. It does assert the one thing the
sync worker needs from the inbound half — that `fetch_orders` returns the
typed `ProductionRequest` rather than a dict — because that mismatch is
exactly what broke every inbound order before it was fixed.

Over-production is covered as of 2026-09-10: an over-run inside ERPNext's own
allowance, one beyond it, and the allowance at zero. What the outbox then does
with a refusal is `tests/test_erp_contract.py`, which needs no ERPNext.
"""

import uuid

import pytest

from fsmes.config import get_settings
from fsmes.integrations.erp.contract import ProductionRequest
from fsmes.integrations.erp.erpnext_adapter import (
    ErpNextAdapter,
    ErpNextClient,
    ErpNextError,
    ErpNextRefused,
)

pytestmark = [pytest.mark.slow, pytest.mark.erpnext_live]

ORDERED_QTY = 400.0
GOOD_QTY = 396.0
SCRAP_QTY = 3.0

# The five fields the connector cannot work without. Named here so a failure
# says which one is missing rather than "KeyError".
REQUIRED_FIELDS = [
    "custom_mes_synced",
    "custom_mes_good_qty",
    "custom_mes_scrap_qty",
    "custom_mes_over_qty",
    "custom_mes_lot",
]


@pytest.fixture(scope="module")
def client() -> ErpNextClient:
    settings = get_settings()
    connection = ErpNextClient(
        settings.erpnext_base_url,
        site=settings.erpnext_site,
        user=settings.erpnext_user,
        password=settings.erpnext_password,
        api_key=settings.erpnext_api_key,
        api_secret=settings.erpnext_api_secret,
        timeout=60.0,
    )
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def seeded(client: ErpNextClient) -> str:
    """The site must already carry the plant and the custom fields.

    `labs/erpnext/seed_erpnext.py` puts them there. Checking here means a
    half-seeded site says so, instead of failing four tests later with
    something that reads like a connector bug.
    """
    missing = [f for f in REQUIRED_FIELDS if not client.exists("Custom Field", f"Work Order-{f}")]
    assert not missing, (
        f"{len(missing)} of {len(REQUIRED_FIELDS)} custom fields are not installed on this "
        f"site: {', '.join(missing)}. Run labs/erpnext/seed_erpnext.py first."
    )
    boms = client.list("BOM", filters=[["item", "=", "FG-BOTTLE"], ["docstatus", "=", 1]],
                       fields=["name"], limit=5)
    assert boms, "no submitted BOM for FG-BOTTLE; run labs/erpnext/seed_erpnext.py first"
    return boms[0]["name"]


def submit_work_order(client: ErpNextClient, bom: str, quantity: float) -> str:
    """A fresh submitted Work Order, so each test starts from a known state."""
    order = client.insert(
        "Work Order",
        {
            "production_item": "FG-BOTTLE",
            "bom_no": bom,
            "qty": quantity,
            "company": "ACME Beverages",
            "wip_warehouse": "Work In Progress - ACME",
            "fg_warehouse": "Finished Goods - ACME",
            "source_warehouse": "Stores - ACME",
            "use_multi_level_bom": 0,
            "skip_transfer": 1,
            "docstatus": 1,
        },
    )
    return order["name"]


@pytest.fixture
def work_order(client: ErpNextClient, seeded: str) -> str:
    return submit_work_order(client, seeded, ORDERED_QTY)


def manufacture_entries(client: ErpNextClient, order: str) -> list[dict]:
    """Submitted Manufacture stock entries against one work order."""
    return client.list(
        "Stock Entry",
        filters=[["work_order", "=", order], ["purpose", "=", "Manufacture"], ["docstatus", "=", 1]],
        fields=["name", "fg_completed_qty", "docstatus"],
        limit=20,
    )


def comments_on(client: ErpNextClient, order: str) -> list[str]:
    rows = client.list(
        "Comment",
        filters=[["reference_doctype", "=", "Work Order"], ["reference_name", "=", order],
                 ["comment_type", "=", "Comment"]],
        fields=["content"],
        limit=20,
    )
    return [row["content"] for row in rows]


# ------------------------------------------------------------- the round trip


def test_a_submitted_work_order_reaches_the_mes_and_is_not_offered_twice(client, work_order):
    """Inbound, against ERPNext's own filters — the half a mock cannot check.

    The filter names four fields (`docstatus`, `status`, `custom_mes_synced`,
    plus the fields it selects). A rename in ERPNext, or a custom field that
    was never installed, shows up here as an HTTP 417 and nowhere else.
    """
    adapter = ErpNextAdapter(client)

    offered = adapter.fetch_orders()
    mine = [o for o in offered if o.code == work_order]
    assert len(mine) == 1, f"{len(offered)} orders offered, {len(mine)} of them mine ({work_order})"
    [order] = mine
    # The typed contract the sync worker reads, not a loose dict: it takes
    # `request.code` off whatever this returns, so a dict here would be an
    # AttributeError on the plant's first real order.
    assert isinstance(order, ProductionRequest)
    assert order.material == "FG-BOTTLE"
    assert order.quantity == ORDERED_QTY
    assert order.erp_reference == work_order

    adapter.acknowledge(order.code)

    # ERPNext's own record of the acknowledgement, not the adapter's.
    assert client.get("Work Order", work_order)["custom_mes_synced"] == 1
    still_offered = [o.code for o in adapter.fetch_orders()]
    assert work_order not in still_offered, (
        f"{work_order} was offered again after acknowledgement; "
        f"{len(still_offered)} orders in that second fetch"
    )


def test_a_confirmation_lands_on_erpnext_as_quantities_stock_and_a_comment(client, work_order):
    """Outbound. Everything asserted below is read back out of ERPNext."""
    adapter = ErpNextAdapter(client)
    adapter.acknowledge(work_order)
    lot = f"{work_order}-FG"

    adapter.send_confirmation(
        {
            "order": work_order,
            "material": "FG-BOTTLE",
            "ordered_qty": ORDERED_QTY,
            "good_qty": GOOD_QTY,
            "scrap_qty": SCRAP_QTY,
            "over_qty": 0.0,
            "lot": lot,
            "completed_at": "2026-09-09T11:00:00",
        }
    )

    document = client.get("Work Order", work_order)
    assert document["custom_mes_good_qty"] == GOOD_QTY
    assert document["custom_mes_scrap_qty"] == SCRAP_QTY
    assert document["custom_mes_over_qty"] == 0.0
    assert document["custom_mes_lot"] == lot

    # ERPNext's own produced quantity, which is what the business reads.
    assert document["produced_qty"] == GOOD_QTY

    entries = manufacture_entries(client, work_order)
    assert len(entries) == 1, f"expected 1 submitted Manufacture entry, ERPNext holds {len(entries)}"
    assert entries[0]["fg_completed_qty"] == GOOD_QTY

    texts = comments_on(client, work_order)
    assert len(texts) >= 1, f"no comment on {work_order}; ERPNext holds {len(texts)}"
    assert any("396 good" in t and "3 scrap" in t and lot in t for t in texts), texts


def test_a_retried_confirmation_does_not_manufacture_the_same_units_twice(client, work_order):
    """The riskiest claim in the connector, finally asked of a real ERPNext.

    The sync worker retries a confirmation whose acknowledgement was lost. If
    the guard in `_manufacture` reads `produced_qty` wrongly — or ERPNext
    stops maintaining it — the second attempt books the order twice and the
    plant's stock is wrong by 396 bottles.
    """
    adapter = ErpNextAdapter(client)
    adapter.acknowledge(work_order)
    payload = {
        "order": work_order,
        "material": "FG-BOTTLE",
        "ordered_qty": ORDERED_QTY,
        "good_qty": GOOD_QTY,
        "scrap_qty": SCRAP_QTY,
        "over_qty": 0.0,
        "lot": f"{work_order}-FG",
    }

    adapter.send_confirmation(payload)
    adapter.send_confirmation(payload)

    entries = manufacture_entries(client, work_order)
    assert len(entries) == 1, (
        f"a retry posted a second stock entry: ERPNext holds {len(entries)} submitted "
        f"Manufacture entries for {work_order} ({[e['name'] for e in entries]})"
    )
    assert client.get("Work Order", work_order)["produced_qty"] == GOOD_QTY


# ------------------------------------- what ERPNext does with a field it lacks


def test_erpnext_takes_a_write_to_a_field_that_does_not_exist_and_loses_it(client, work_order):
    """The measurement that replaces a paragraph of reasoning.

    The connector writes five custom fields onto a submitted Work Order. If a
    site is missing one, does Frappe refuse the write, or accept it and drop
    the number? Reading Frappe's source suggested the second — an unknown key
    on a document is assigned as a plain Python attribute, never mapped to a
    column — but that was reasoning, and the honest thing is to ask.

    So: install a probe field, prove a write to it survives, delete it, write
    the same shape again, and record what came back. The probe is used rather
    than one of the connector's own five so that a failure here cannot leave
    the site unable to run the rest of the suite.
    """
    probe = "custom_mes_drop_probe"
    client.insert(
        "Custom Field",
        {
            "dt": "Work Order",
            "fieldname": probe,
            "label": "MES Drop Probe",
            "fieldtype": "Float",
            "insert_after": "status",
            "allow_on_submit": 1,
            "read_only": 1,
        },
    )
    try:
        client.update("Work Order", work_order, {probe: 42.0})
        assert client.get("Work Order", work_order).get(probe) == 42.0, (
            "the probe field did not even work while installed, so it is not a fair "
            "stand-in for one of the connector's fields"
        )
    finally:
        client.delete("Custom Field", f"Work Order-{probe}")

    assert not client.exists("Custom Field", f"Work Order-{probe}")

    # The measurement. Frappe's answer is recorded in the assertions below and
    # written up in docs/operate/erpnext.md; if ERPNext ever changes its mind,
    # this test is where that shows up.
    status = None
    try:
        client.update("Work Order", work_order, {probe: 43.0})
    except ErpNextError as exc:
        status = str(exc)

    after = client.get("Work Order", work_order)
    print(f"\nMISSING FIELD PROBE: refused={status!r} value_after={after.get(probe)!r}")

    assert status is None, (
        "ERPNext refused a write naming a field it does not have. That is better than "
        f"the silent loss this was written to expect, and the docs must be corrected: {status}"
    )
    assert probe not in after, (
        f"ERPNext kept {probe}={after.get(probe)!r} on a Work Order that has no such field"
    )


def test_with_stock_posting_off_every_number_still_lands_on_erpnexts_document(client, work_order):
    """`MES_ERPNEXT_POST_STOCK_ENTRY=false` is offered to sites that do not
    want the MES moving stock. It must still record what the machines
    counted, or turning it off quietly throws the numbers away.

    Read back field by field, because Frappe answers 200 whether or not a
    field it was sent exists — which is what the test above measures.
    """
    adapter = ErpNextAdapter(client, post_stock_entry=False)
    adapter.acknowledge(work_order)
    lot = uuid.uuid4().hex[:12]
    adapter.send_confirmation(
        {"order": work_order, "material": "FG-BOTTLE", "ordered_qty": ORDERED_QTY,
         "good_qty": 1.0, "scrap_qty": 2.0, "over_qty": 0.0, "lot": lot}
    )

    stored = client.get("Work Order", work_order)
    lost = [f for f, expected in (("custom_mes_good_qty", 1.0), ("custom_mes_scrap_qty", 2.0),
                                  ("custom_mes_lot", lot))
            if stored.get(f) != expected]
    assert not lost, f"{len(lost)} of 3 written fields did not survive the write: {lost}"
    assert manufacture_entries(client, work_order) == [], (
        "MES_ERPNEXT_POST_STOCK_ENTRY=false still moved stock"
    )


def test_the_site_this_ran_against_is_the_one_the_host_header_named(client, seeded):
    """The connector addresses a multi-site bench by Host header alone. If the
    bench ever stopped honouring it, every test above could still pass while
    writing into another company's books.

    `ACME Beverages` exists only on the seeded site, so finding it is evidence
    the Host header reached the site it named.
    """
    site = get_settings().erpnext_site
    assert site, "MES_ERPNEXT_SITE is empty; this test cannot mean anything"
    assert client.exists("Company", "ACME Beverages"), (
        f"the bench answered on Host {site!r} but that site has no ACME Beverages company"
    )


# ------------------------------------------------- over-production, measured

ALLOWANCE_FIELD = "overproduction_percentage_for_work_order"


def allowance_now(client: ErpNextClient) -> float:
    return float(client.get("Manufacturing Settings", "Manufacturing Settings").get(ALLOWANCE_FIELD) or 0)


@pytest.fixture
def allowance(client: ErpNextClient):
    """Set ERPNext's own over-production allowance, and put it back after.

    It is a single site-wide setting, so a test that changed it and walked
    away would silently rewrite the rules for every test that ran later.
    """
    original = allowance_now(client)

    def apply(percent: float) -> None:
        client.call("frappe.client.set_value", doctype="Manufacturing Settings",
                    name="Manufacturing Settings", fieldname=ALLOWANCE_FIELD, value=percent)
        stored = allowance_now(client)
        assert stored == percent, (
            f"asked ERPNext for an over-production allowance of {percent}% and it holds {stored}%"
        )

    yield apply
    client.call("frappe.client.set_value", doctype="Manufacturing Settings",
                name="Manufacturing Settings", fieldname=ALLOWANCE_FIELD, value=original)


def confirm_over_run(adapter: ErpNextAdapter, order: str, good: float) -> ErpNextError | None:
    """Send a completion for more good units than the order asked for.

    Returns what ERPNext did about it: `None` if it took it, the error if it
    refused. `over_qty` is what `WorkOrder.over_qty` would carry — the MES
    books every unit the machine counted and says how far past the order it
    ran (decision 0019).
    """
    try:
        adapter.send_confirmation(
            {
                "order": order,
                "material": "FG-BOTTLE",
                "ordered_qty": ORDERED_QTY,
                "good_qty": good,
                "scrap_qty": 0.0,
                "over_qty": good - ORDERED_QTY,
                "lot": f"{order}-FG",
            }
        )
    except ErpNextError as exc:
        return exc
    return None


def report(case: str, client: ErpNextClient, order: str, good: float,
           refusal: ErpNextError | None) -> dict:
    """One measured case, printed into the run log and returned for assertions."""
    document = client.get("Work Order", order)
    entries = manufacture_entries(client, order)
    measured = {
        "refused": None if refusal is None else str(refusal),
        "status_code": getattr(refusal, "status_code", None),
        "produced_qty": document.get("produced_qty"),
        "wo_status": document.get("status"),
        "custom_mes_good_qty": document.get("custom_mes_good_qty"),
        "custom_mes_over_qty": document.get("custom_mes_over_qty"),
        "entries": [(e["name"], e["fg_completed_qty"]) for e in entries],
        "comments": comments_on(client, order),
    }
    print(f"\nOVER-PRODUCTION [{case}] ordered={ORDERED_QTY:g} good={good:g} "
          f"allowance={allowance_now(client):g}%")
    for key, value in measured.items():
        print(f"    {key} = {value!r}")
    return measured


def test_an_over_run_inside_erpnexts_allowance_is_booked_for_every_unit(client, seeded, allowance):
    """420 against an order for 400, on a site that allows 10%.

    ERPNext takes it whole: `produced_qty` is the 420 the machines counted,
    not the 400 somebody asked for, and the order closes as Completed.
    """
    allowance(10.0)
    order = submit_work_order(client, seeded, ORDERED_QTY)
    adapter = ErpNextAdapter(client)
    adapter.acknowledge(order)

    good = ORDERED_QTY * 1.05
    refusal = confirm_over_run(adapter, order, good)
    measured = report("within allowance", client, order, good, refusal)

    assert refusal is None, f"ERPNext refused an over-run inside its own allowance: {refusal}"
    assert measured["produced_qty"] == good
    assert measured["wo_status"] == "Completed"
    assert len(measured["entries"]) == 1, (
        f"expected 1 submitted Manufacture entry, ERPNext holds {len(measured['entries'])}"
    )
    assert measured["entries"][0][1] == good
    assert measured["custom_mes_good_qty"] == good
    assert measured["custom_mes_over_qty"] == good - ORDERED_QTY
    assert any("over the ordered quantity" in text for text in measured["comments"]), measured["comments"]


def test_an_over_run_beyond_erpnexts_allowance_is_refused_whole_and_booked_nowhere(client, seeded, allowance):
    """500 against an order for 400, on a site that allows 10%.

    Measured on v15.120.0: ERPNext throws `For quantity 500.0 should not be
    greater than allowed quantity 440.0` as an HTTP 417, and books **nothing**
    - not the 440 it would have allowed, not a draft. `produced_qty` stays 0.

    So the confirmation is not delivered, and the connector says why: the
    error is permanent, the outbox stops rather than retrying, and a comment
    on the Work Order tells whoever has to decide what happened.
    """
    allowance(10.0)
    order = submit_work_order(client, seeded, ORDERED_QTY)
    adapter = ErpNextAdapter(client)
    adapter.acknowledge(order)

    good = ORDERED_QTY * 1.25
    refusal = confirm_over_run(adapter, order, good)
    measured = report("beyond allowance", client, order, good, refusal)

    assert isinstance(refusal, ErpNextRefused), (
        f"ERPNext's answer to an over-run beyond its allowance was {refusal!r}; the connector's "
        "behaviour and the docs are written from the measurement, so both need correcting"
    )
    assert refusal.permanent is True
    assert refusal.status_code == 417
    assert "allowed quantity 440" in str(refusal), str(refusal)

    assert measured["produced_qty"] == 0.0, "ERPNext booked something after refusing the entry"
    assert measured["entries"] == []
    # What the machines counted still reaches the fields that carry it. The
    # MES's record does not change because the ERP said no; what the ERP
    # accepted - nothing - is what is reported as accepted.
    assert measured["custom_mes_good_qty"] == good
    assert measured["custom_mes_over_qty"] == good - ORDERED_QTY
    assert any("refused" in text for text in measured["comments"]), (
        f"nothing on the Work Order says why: {measured['comments']}"
    )


def test_with_the_allowance_at_zero_one_unit_over_is_refused_the_same_way(client, seeded, allowance):
    """ERPNext's own default is no over-production at all, and the connector
    must not be honest only at 10%. One unit past the order is refused, in the
    same words, with the same nothing booked."""
    allowance(0.0)
    order = submit_work_order(client, seeded, ORDERED_QTY)
    adapter = ErpNextAdapter(client)
    adapter.acknowledge(order)

    good = ORDERED_QTY + 1
    refusal = confirm_over_run(adapter, order, good)
    measured = report("allowance zero", client, order, good, refusal)

    assert isinstance(refusal, ErpNextRefused), f"one unit over was answered with {refusal!r}"
    assert "allowed quantity 400" in str(refusal), str(refusal)
    assert measured["produced_qty"] == 0.0
    assert measured["entries"] == []
    assert measured["custom_mes_good_qty"] == good
