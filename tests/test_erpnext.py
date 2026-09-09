"""The ERPNext adapter: what it sends, what it refuses to invent, and what it
does twice without doing damage.

These run against a scripted transport rather than a live ERPNext, so they
answer the questions that survive a version bump — field mapping, the
acknowledge handshake, retry safety — rather than re-testing Frappe. The live
round-trip is `test_erpnext_roundtrip`, marked slow and skipped when no ERPNext
is reachable.
"""

import json
from datetime import datetime

import httpx
import pytest

from fsmes.integrations.erp.contract import ProductionRequest
from fsmes.integrations.erp.erpnext_adapter import ErpNextAdapter, ErpNextClient, ErpNextError


class FakeErpNext:
    """A scripted Frappe: records what it was asked, answers what it was told to."""

    def __init__(self, work_orders=None, produced=0.0):
        self.work_orders = work_orders if work_orders is not None else []
        self.produced = produced
        self.updates: list[tuple[str, dict]] = []
        self.comments: list[str] = []
        self.stock_entries: list[dict] = []
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")

        if path == "/api/method/login":
            return httpx.Response(200, json={"message": "Logged In"})

        if path == "/api/resource/Work Order" and request.method == "GET":
            self.last_filters = json.loads(request.url.params.get("filters", "[]"))
            return httpx.Response(200, json={"data": self.work_orders})

        if path.startswith("/api/resource/Work Order/"):
            name = path.rsplit("/", 1)[-1].replace("%20", " ")
            if request.method == "PUT":
                self.updates.append((name, json.loads(request.content)))
                return httpx.Response(200, json={"data": {"name": name}})
            return httpx.Response(200, json={"data": {"name": name, "produced_qty": self.produced}})

        if path.endswith("work_order.make_stock_entry"):
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={"message": {"doctype": "Stock Entry", "work_order": body["work_order_id"],
                                  "fg_completed_qty": body["qty"], "items": []}},
            )

        if path == "/api/resource/Stock Entry" and request.method == "POST":
            doc = json.loads(request.content)
            self.stock_entries.append(doc)
            return httpx.Response(200, json={"data": {"name": "MAT-STE-TEST-0001", **doc}})

        if path.endswith("utils.add_comment"):
            self.comments.append(json.loads(request.content)["content"])
            return httpx.Response(200, json={"message": {}})

        return httpx.Response(404, json={"exception": f"no route for {path}"})


def make_adapter(fake: FakeErpNext, **kwargs) -> ErpNextAdapter:
    client = ErpNextClient(
        "http://erp.test",
        site="mes.localhost",
        client=httpx.Client(base_url="http://erp.test", transport=httpx.MockTransport(fake.handler)),
    )
    return ErpNextAdapter(client, **kwargs)


# ------------------------------------------------------------------- inbound


def test_work_orders_map_onto_the_mes_order_contract():
    fake = FakeErpNext(
        work_orders=[
            {
                "name": "MFG-WO-2026-00007",
                "production_item": "FG-BOTTLE",
                "qty": 400.0,
                "planned_end_date": "2026-08-20 17:00:00",
                "expected_delivery_date": "2026-08-21",
            }
        ]
    )
    [order] = make_adapter(fake).fetch_orders()
    assert order.code == "MFG-WO-2026-00007"
    assert order.material == "FG-BOTTLE"
    assert order.quantity == 400.0
    assert order.due_date == datetime(2026, 8, 20, 17, 0)  # planned end wins over delivery date
    assert order.erp_reference == "MFG-WO-2026-00007"


def test_no_priority_is_invented():
    """ERPNext Work Order has no priority field. Sending one would silently
    outrank the MES's own dispatch order with a number nobody set."""
    fake = FakeErpNext(work_orders=[{"name": "WO-1", "production_item": "FG-BOTTLE", "qty": 5}])
    [order] = make_adapter(fake).fetch_orders()
    assert order.priority == ProductionRequest.model_fields["priority"].default


def test_the_sync_worker_can_acknowledge_what_fetch_orders_returned():
    """The worker reads `request.code` off every order it imports. A plain dict
    has no `.code`, so an adapter that returns dicts imports an order and then
    fails to mark it taken — forever, once per poll."""
    fake = FakeErpNext(work_orders=[{"name": "WO-1", "production_item": "FG-BOTTLE", "qty": 5}])
    adapter = make_adapter(fake)
    [order] = adapter.fetch_orders()
    adapter.acknowledge(order.code)
    assert fake.updates == [("WO-1", {"custom_mes_synced": 1})]


def test_only_released_unsynced_orders_are_asked_for():
    """Draft orders are not real yet, finished ones are history, and an order
    already taken must not be imported a second time."""
    fake = FakeErpNext()
    make_adapter(fake).fetch_orders()
    filters = {tuple(f[:2]): f[2] for f in fake.last_filters}
    assert filters[("docstatus", "=")] == 1  # submitted only, never drafts
    assert filters[("custom_mes_synced", "=")] == 0  # not already taken
    assert set(filters[("status", "in")]) == {"Not Started", "In Process"}


def test_a_shared_bench_only_gives_up_the_configured_company_s_orders():
    """One ERPNext can hold several companies' books. Without this filter the
    MES imports work orders belonging to a business it has never heard of."""
    fake = FakeErpNext()
    make_adapter(fake, company="Widgets Ltd").fetch_orders()
    filters = {tuple(f[:2]): f[2] for f in fake.last_filters}
    assert filters[("company", "=")] == "Widgets Ltd"


def test_no_company_set_means_every_company_rather_than_a_guessed_one():
    """A single-company site should not have to name itself, and the MES must
    not silently substitute the demo's company and import nothing."""
    fake = FakeErpNext()
    make_adapter(fake).fetch_orders()
    assert not any(f[0] == "company" for f in fake.last_filters)


def test_acknowledge_marks_the_order_taken():
    fake = FakeErpNext()
    make_adapter(fake).acknowledge("MFG-WO-2026-00007")
    assert fake.updates == [("MFG-WO-2026-00007", {"custom_mes_synced": 1})]


# ------------------------------------------------------------------ outbound


def _confirmation(**overrides) -> dict:
    payload = {
        "order": "MFG-WO-2026-00007",
        "material": "FG-BOTTLE",
        "ordered_qty": 400.0,
        "good_qty": 400.0,
        "scrap_qty": 18.0,
        "lot": "MFG-WO-2026-00007-FG",
        "completed_at": "2026-08-17T22:49:00",
    }
    payload.update(overrides)
    return payload


def test_confirmation_records_machine_counts_and_posts_stock():
    fake = FakeErpNext()
    make_adapter(fake).send_confirmation(_confirmation())

    name, values = fake.updates[0]
    assert name == "MFG-WO-2026-00007"
    assert values["custom_mes_good_qty"] == 400.0
    # Machine-counted scrap has no native ERPNext home; it must not be dropped.
    assert values["custom_mes_scrap_qty"] == 18.0
    assert values["custom_mes_lot"] == "MFG-WO-2026-00007-FG"

    [entry] = fake.stock_entries
    assert entry["fg_completed_qty"] == 400.0
    assert entry["docstatus"] == 1  # submitted, not left as a draft nobody posts
    assert "18" in fake.comments[0] and "400" in fake.comments[0]


def test_a_retry_does_not_manufacture_the_same_units_twice():
    """The sync worker retries a failed confirmation. If the stock entry landed
    but the acknowledgement did not, a naive retry would double the stock."""
    fake = FakeErpNext(produced=400.0)
    make_adapter(fake).send_confirmation(_confirmation())
    assert fake.stock_entries == []


def test_a_partial_retry_only_posts_the_remainder():
    fake = FakeErpNext(produced=150.0)
    make_adapter(fake).send_confirmation(_confirmation())
    [entry] = fake.stock_entries
    assert entry["fg_completed_qty"] == 250.0


def test_nothing_is_manufactured_when_nothing_was_made():
    fake = FakeErpNext()
    make_adapter(fake).send_confirmation(_confirmation(good_qty=0, scrap_qty=12, lot=None))
    assert fake.stock_entries == []
    # The scrap still gets recorded — a failed order is a fact the ERP needs.
    assert fake.updates[0][1]["custom_mes_scrap_qty"] == 12


def test_stock_posting_can_be_turned_off_without_losing_the_numbers():
    fake = FakeErpNext()
    make_adapter(fake, post_stock_entry=False).send_confirmation(_confirmation())
    assert fake.stock_entries == []
    assert fake.updates[0][1]["custom_mes_good_qty"] == 400.0
    assert fake.comments


def test_a_confirmation_without_an_order_is_refused():
    with pytest.raises(ErpNextError, match="no order code"):
        make_adapter(FakeErpNext()).send_confirmation({"good_qty": 5})


def test_frappe_errors_surface_their_message_not_just_a_status_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(417, json={"exception": "frappe.exceptions.LinkValidationError: no such item"})

    client = ErpNextClient(
        "http://erp.test", client=httpx.Client(base_url="http://erp.test", transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ErpNextError, match="LinkValidationError"):
        client.list("Work Order")


def test_the_site_header_is_sent_so_a_shared_bench_writes_to_the_right_company():
    """This bench also serves another company's books on the default site."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.headers.get("Host")
        return httpx.Response(200, json={"data": []})

    ErpNextClient(
        "http://erp.test",
        site="mes.localhost",
        client=httpx.Client(
            base_url="http://erp.test",
            headers={"Host": "mes.localhost"},
            transport=httpx.MockTransport(handler),
        ),
    ).list("Work Order")
    assert seen["host"] == "mes.localhost"


# ----------------------------------------------------------------- live ERPNext


@pytest.mark.slow
def test_erpnext_roundtrip():
    """Against a real ERPNext, if one is running. Proves the field names still
    exist — the half a mock transport can never check."""
    from fsmes.config import get_settings

    settings = get_settings()
    try:
        client = ErpNextClient(
            settings.erpnext_base_url,
            site=settings.erpnext_site,
            user=settings.erpnext_user,
            password=settings.erpnext_password,
            timeout=5.0,
        )
        orders = ErpNextAdapter(client).fetch_orders()
    except Exception as exc:  # not running, not seeded, not reachable
        pytest.skip(f"no ERPNext at {settings.erpnext_base_url} ({type(exc).__name__})")

    assert isinstance(orders, list)
    for order in orders:
        assert order["code"] and order["material"]
        assert order["quantity"] >= 0
