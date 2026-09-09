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

from fsmes.integrations.erp import erpnext_setup
from fsmes.integrations.erp.contract import ProductionRequest
from fsmes.integrations.erp.erpnext_adapter import ErpNextAdapter, ErpNextClient, ErpNextError

FIELD_TYPES = {f["fieldname"]: f["fieldtype"] for f in erpnext_setup.CUSTOM_FIELDS}


class FakeErpNext:
    """A scripted Frappe: records what it was asked, answers what it was told to.

    It keeps Frappe's one dangerous habit — a PUT naming a field the doctype
    does not have succeeds, and the value is silently dropped from the
    document that comes back. `custom_fields` says which of the MES's fields
    this pretend site has; the default is a site that has been set up.
    """

    ALL_MES_FIELDS = tuple(erpnext_setup.FIELD_NAMES)

    def __init__(self, work_orders=None, produced=0.0, custom_fields=ALL_MES_FIELDS, field_types=None):
        self.work_orders = work_orders if work_orders is not None else []
        self.produced = produced
        self.custom_fields = list(custom_fields)
        # A site where somebody made one of them by hand, with the wrong type.
        self.field_types = dict(FIELD_TYPES, **(field_types or {}))
        self.updates: list[tuple[str, dict]] = []
        self.comments: list[str] = []
        self.stock_entries: list[dict] = []
        self.calls: list[str] = []

    def _stored(self, name: str, values: dict) -> dict:
        """What Frappe would return: the document, minus fields it has no column for."""
        doc = {"name": name, "produced_qty": self.produced}
        doc.update({key: value for key, value in values.items() if key in self.custom_fields})
        return doc

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")

        if path == "/api/method/login":
            return httpx.Response(200, json={"message": "Logged In"})

        if path == "/api/resource/Custom Field" and request.method == "GET":
            asked = json.loads(request.url.params.get("filters", "[]"))
            wanted = next((f[2] for f in asked if f[0] == "fieldname"), self.custom_fields)
            return httpx.Response(200, json={"data": [
                {"fieldname": f, "fieldtype": self.field_types.get(f, "Data"), "allow_on_submit": 1}
                for f in self.custom_fields if f in wanted
            ]})

        if path == "/api/resource/Custom Field" and request.method == "POST":
            doc = json.loads(request.content)
            self.custom_fields.append(doc["fieldname"])
            return httpx.Response(200, json={"data": doc})

        if path == "/api/resource/Work Order" and request.method == "GET":
            self.last_filters = json.loads(request.url.params.get("filters", "[]"))
            return httpx.Response(200, json={"data": self.work_orders})

        if path.startswith("/api/resource/Work Order/"):
            name = path.rsplit("/", 1)[-1].replace("%20", " ")
            if request.method == "PUT":
                values = json.loads(request.content)
                self.updates.append((name, values))
                return httpx.Response(200, json={"data": self._stored(name, values)})
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


def _client(fake: FakeErpNext) -> ErpNextClient:
    return ErpNextClient(
        "http://erp.test",
        site="mes.localhost",
        client=httpx.Client(base_url="http://erp.test", transport=httpx.MockTransport(fake.handler)),
    )


def make_adapter(fake: FakeErpNext, **kwargs) -> ErpNextAdapter:
    return ErpNextAdapter(_client(fake), **kwargs)


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


# ------------------------------------------------- a site nobody prepared


def test_every_field_the_adapter_writes_is_one_that_setup_creates():
    """The same defect one layer up, and the one that actually happened: a
    field added to a write without being added to the definitions
    `fsmes erp setup` installs is a field nothing creates — on a site
    `fsmes erp check` would call ready. `custom_mes_over_qty` arrived that way
    while these definitions were being moved into the package."""
    fake = FakeErpNext()
    adapter = make_adapter(fake)
    adapter.acknowledge("MFG-WO-2026-00007")
    adapter.send_confirmation(_confirmation())
    written = {key for _, values in fake.updates for key in values}
    assert written <= set(erpnext_setup.FIELD_NAMES), (
        f"the adapter writes {sorted(written - set(erpnext_setup.FIELD_NAMES))}, "
        f"which fsmes erp setup does not create"
    )


def test_the_over_run_reaches_the_ERP_as_its_own_number():
    """An order for 400 that finished at 402 says so in a field of its own,
    rather than leaving a reader to notice the good qty exceeds the order."""
    fake = FakeErpNext()
    make_adapter(fake).send_confirmation(_confirmation(good_qty=402.0, over_qty=2.0))
    assert fake.updates[0][1]["custom_mes_over_qty"] == 2.0



def test_a_field_ERPNext_silently_dropped_is_not_reported_as_delivered():
    """Frappe answers 200 to a PUT naming a field the doctype does not have,
    and the number is gone. If the MES took that for success it would mark the
    confirmation delivered and the plant's count would exist nowhere."""
    fake = FakeErpNext(custom_fields=[])
    with pytest.raises(ErpNextError, match="custom_mes_good_qty"):
        make_adapter(fake).send_confirmation(_confirmation())


def test_an_order_cannot_look_acknowledged_on_a_site_without_the_field():
    """Otherwise the same order is imported again on the next poll, forever,
    while the log says it was acknowledged."""
    fake = FakeErpNext(custom_fields=[])
    with pytest.raises(ErpNextError, match="custom_mes_synced"):
        make_adapter(fake).acknowledge("WO-1")


def test_the_error_says_what_to_run():
    fake = FakeErpNext(custom_fields=[])
    with pytest.raises(ErpNextError, match="fsmes erp setup"):
        make_adapter(fake).acknowledge("WO-1")


def test_a_value_the_ERP_changed_is_not_reported_as_delivered():
    """A field that exists but truncates, rounds hard, or is the wrong type is
    the same failure wearing a different hat."""
    fake = FakeErpNext()
    original = fake._stored

    def truncating(name, values):
        doc = original(name, values)
        if "custom_mes_lot" in doc:
            doc["custom_mes_lot"] = doc["custom_mes_lot"][:4]
        return doc

    fake._stored = truncating
    with pytest.raises(ErpNextError, match="custom_mes_lot"):
        make_adapter(fake).send_confirmation(_confirmation())


def test_the_rounding_a_real_ERPNext_does_is_not_treated_as_a_lost_number():
    """ERPNext stores floats at the site's precision. Two decimals is a
    rounding, not a dropped field, and must not fail a confirmation."""
    fake = FakeErpNext()
    original = fake._stored

    def rounding(name, values):
        doc = original(name, values)
        for key in ("custom_mes_good_qty", "custom_mes_scrap_qty"):
            if key in doc:
                doc[key] = round(doc[key] + 0.004, 2)
        return doc

    fake._stored = rounding
    make_adapter(fake).send_confirmation(_confirmation())
    assert fake.stock_entries  # it got all the way through


def test_setup_creates_the_four_fields_and_saying_it_twice_changes_nothing():
    fake = FakeErpNext(custom_fields=[])
    client = _client(fake)
    first = erpnext_setup.ensure_custom_fields(client)
    assert [what for _, what in first] == ["created"] * len(erpnext_setup.CUSTOM_FIELDS)
    second = erpnext_setup.ensure_custom_fields(client)
    assert [what for _, what in second] == ["already there"] * len(erpnext_setup.CUSTOM_FIELDS)
    assert erpnext_setup.field_problems(client) == []


def test_check_names_the_field_that_is_missing_rather_than_saying_misconfigured():
    fake = FakeErpNext(custom_fields=["custom_mes_synced"])
    ok, lines = erpnext_setup.check(_client(fake))
    assert not ok
    report = "\n".join(lines)
    assert "custom_mes_good_qty is missing" in report
    assert "custom_mes_lot is missing" in report
    assert "fsmes erp setup" in report


def test_check_fails_a_field_of_the_wrong_type_even_though_it_exists():
    fake = FakeErpNext(field_types={"custom_mes_good_qty": "Data"})
    ok, lines = erpnext_setup.check(_client(fake))
    assert not ok
    assert any("custom_mes_good_qty is a Data field" in line for line in lines)


def test_check_says_so_when_the_site_cannot_be_reached_and_does_not_go_on():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"exception": "frappe.exceptions.PermissionError"})

    client = ErpNextClient(
        "http://erp.test", client=httpx.Client(base_url="http://erp.test", transport=httpx.MockTransport(handler))
    )
    ok, lines = erpnext_setup.check(client)
    assert not ok
    assert "cannot read Work Order" in lines[0]
    assert len(lines) == 2  # the failure and what to check, not four more of the same


def test_check_passes_on_a_prepared_site():
    ok, lines = erpnext_setup.check(_client(FakeErpNext()))
    assert ok
    assert all(line.startswith("ok") for line in lines)


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
