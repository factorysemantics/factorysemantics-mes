"""Every shipped ERP connector against the same contract.

The obligations live in `fsmes.integrations.erp.conformance`, inside the
package, so a connector published on its own is held to exactly this bar.
This module supplies the far side for each connector that ships and runs
the suite against it.

**Adding a connector is one line** in `CASES` below: a name and a factory
that builds the connector, a way to place an order, a way to read back what
the ERP received, and a way to break it.

Three cases here, for three shipped adapters. The handoff that asked for
this listed four — `mock_erp` as well — but `mock_erp` is a pretend ERP
*server*, not a connector: it is the far side the `rest` case runs against,
and it is exercised here in that role. There is no fourth adapter to run.
"""

import json
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fsmes.integrations.erp import conformance, erpnext_setup, mock_erp
from fsmes.integrations.erp.conformance import ConformanceCase
from fsmes.integrations.erp.erpnext_adapter import ErpNextAdapter, ErpNextClient
from fsmes.integrations.erp.file_adapter import FileErpAdapter
from fsmes.integrations.erp.rest_adapter import RestErpAdapter

MATERIAL = "FG-COLA"


# ------------------------------------------------------------------- the file
def file_case(tmp_path: Path) -> ConformanceCase:
    inbox, outbox, archive = tmp_path / "in", tmp_path / "out", tmp_path / "archive"
    adapter = FileErpAdapter(inbox, outbox, archive)

    def place_order(code: str, material: str, quantity: float) -> None:
        (inbox / f"{code}.json").write_text(
            json.dumps({"code": code, "material": material, "quantity": quantity}), encoding="utf-8")

    def delivered() -> list[dict]:
        rows = []
        for path in sorted(outbox.glob("*.xml")):
            root = ET.fromstring(path.read_text(encoding="utf-8"))
            response = root.find("ProductionResponse")
            if response is None:
                response = root.find("SegmentResponse")
            if response is None:
                continue
            rows.append({"order": response.findtext("ID"), "good_qty": response.findtext("GoodQuantity")})
        return rows

    return ConformanceCase(
        name="file",
        adapter=adapter,
        place_order=place_order,
        delivered=delivered,
        # The folder somebody moved, or a share that went away mid-shift.
        break_the_far_side=lambda: shutil.rmtree(outbox),
        material=MATERIAL,
        notes=["reading an order archives its file, which is this transport's acknowledgement"],
    )


# ------------------------------------- REST, against the mock ERP that ships
def rest_case(tmp_path: Path) -> ConformanceCase:
    mock_erp.ORDERS.clear()
    mock_erp.CONFIRMATIONS.clear()
    adapter = RestErpAdapter("http://testserver", client=TestClient(mock_erp.app))

    def place_order(code: str, material: str, quantity: float) -> None:
        adapter.client.post("/orders", json={"code": code, "material": material, "quantity": quantity})

    def break_it() -> None:
        adapter.client = httpx.Client(
            base_url="http://testserver",
            transport=httpx.MockTransport(lambda request: httpx.Response(503, json={"error": "ERP is down"})),
        )

    return ConformanceCase(
        name="rest (against the mock ERP)",
        adapter=adapter,
        place_order=place_order,
        delivered=lambda: list(mock_erp.CONFIRMATIONS),
        break_the_far_side=break_it,
        material=MATERIAL,
    )


# ------------------------------------------- ERPNext, against a scripted Frappe
class ScriptedFrappe:
    """Just enough Frappe to hold the connector to the contract.

    It keeps Frappe's one dangerous habit, which is the reason half these
    obligations exist: a `PUT` naming a field the doctype does not have
    answers 200, and the value is gone from the document that comes back.
    Measured against ERPNext v15.120.0 on 2026-09-09; `tests/test_erpnext_live.py`
    is what keeps this fake honest.
    """

    def __init__(self):
        self.work_orders: dict[str, dict] = {}
        self.custom_fields: list[str] = list(erpnext_setup.FIELD_NAMES)
        self.updates: list[tuple[str, dict]] = []
        self.comments: list[str] = []
        self.stock_entries: list[dict] = []

    def place(self, code: str, material: str, quantity: float) -> None:
        self.work_orders[code] = {
            "name": code, "production_item": material, "qty": quantity, "docstatus": 1,
            "status": "Not Started", "produced_qty": 0.0, "custom_mes_synced": 0,
            "planned_end_date": None, "expected_delivery_date": None,
        }

    @staticmethod
    def _matches(doc: dict, filters: list) -> bool:
        for field, op, value in filters:
            actual = doc.get(field)
            if op == "=" and actual != value:
                return False
            if op == "in" and actual not in value:
                return False
        return True

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}

        if path == "/api/method/login":
            return httpx.Response(200, json={"message": "Logged In"})

        if path == "/api/resource/Custom Field":
            if method == "POST":
                self.custom_fields.append(body["fieldname"])
                return httpx.Response(200, json={"data": body})
            asked = json.loads(request.url.params.get("filters", "[]"))
            wanted = next((f[2] for f in asked if f[0] == "fieldname"), self.custom_fields)
            types = {f["fieldname"]: f["fieldtype"] for f in erpnext_setup.CUSTOM_FIELDS}
            return httpx.Response(200, json={"data": [
                {"fieldname": name, "fieldtype": types.get(name, "Data"), "allow_on_submit": 1}
                for name in self.custom_fields if name in wanted
            ]})

        if path == "/api/resource/Work Order" and method == "GET":
            filters = json.loads(request.url.params.get("filters", "[]"))
            fields = json.loads(request.url.params.get("fields", "[]"))
            rows = [doc for doc in self.work_orders.values() if self._matches(doc, filters)]
            return httpx.Response(200, json={"data": [
                {key: doc.get(key) for key in fields} if fields else dict(doc) for doc in rows
            ]})

        if path.startswith("/api/resource/Work Order/"):
            name = path.rsplit("/", 1)[-1].replace("%20", " ")
            doc = self.work_orders.setdefault(name, {"name": name, "produced_qty": 0.0})
            if method == "PUT":
                self.updates.append((name, body))
                # Frappe's habit: a field the doctype does not have is dropped,
                # silently, and the write still answers 200.
                doc.update({k: v for k, v in body.items()
                            if not k.startswith("custom_") or k in self.custom_fields})
                return httpx.Response(200, json={"data": {
                    k: v for k, v in doc.items()
                    if not k.startswith("custom_") or k in self.custom_fields}})
            return httpx.Response(200, json={"data": dict(doc)})

        if path.endswith("work_order.make_stock_entry"):
            return httpx.Response(200, json={"message": {
                "doctype": "Stock Entry", "work_order": body["work_order_id"],
                "fg_completed_qty": body["qty"], "items": []}})

        if path == "/api/resource/Stock Entry" and method == "POST":
            self.stock_entries.append(body)
            return httpx.Response(200, json={"data": {"name": f"MAT-STE-{len(self.stock_entries):05d}", **body}})

        if path.endswith("utils.add_comment"):
            self.comments.append(body["content"])
            return httpx.Response(200, json={"message": {}})

        return httpx.Response(404, json={"exception": f"no route for {path}"})


def erpnext_case(tmp_path: Path) -> ConformanceCase:
    frappe = ScriptedFrappe()
    adapter = ErpNextAdapter(
        ErpNextClient("http://erp.test", site="mes.localhost",
                      client=httpx.Client(base_url="http://erp.test",
                                          transport=httpx.MockTransport(frappe.handler))),
    )

    def delivered() -> list[dict]:
        return [{"order": name, "good_qty": values["custom_mes_good_qty"]}
                for name, values in frappe.updates if "custom_mes_good_qty" in values]

    return ConformanceCase(
        name="erpnext (against a scripted Frappe)",
        adapter=adapter,
        place_order=frappe.place,
        delivered=delivered,
        # The site nobody ran `fsmes erp setup` on. This is the failure the
        # live job measured: writes answer 200 and the numbers are gone.
        break_the_far_side=frappe.custom_fields.clear,
        material=MATERIAL,
    )


# ------------------------------------------------------------------ the suite
# One line per connector. A connector published on its own adds its line to
# its own copy of this list and gets the same eight obligations.
CASES: list[tuple[str, Callable[[Path], ConformanceCase]]] = [
    ("file", file_case),
    ("rest", rest_case),
    ("erpnext", erpnext_case),
]


@pytest.mark.parametrize("obligation", conformance.OBLIGATIONS, ids=lambda f: f.__name__)
@pytest.mark.parametrize(("mode", "build"), CASES, ids=[name for name, _ in CASES])
def test_every_shipped_erp_connector_meets_the_contract(mode, build, obligation, tmp_path):
    obligation(build(tmp_path))


def test_the_suite_covers_every_connector_mode_that_ships():
    """A suite that silently stopped covering a connector is worse than no
    suite, so the list of modes is derived rather than typed: the two built
    into `make_adapter` plus every `fsmes.modules` entry point installed.
    Shipping a new connector fails this test until it has its line in CASES.

    `mock_erp` is not on that list because it is not a connector — it is a
    pretend ERP *server*, and it is the far side the `rest` case runs
    against. `off` is not a connector either; it is the absence of one.
    """
    from importlib.metadata import entry_points

    ships = {"rest", "file"} | {ep.name for ep in entry_points(group="fsmes.modules")}
    covered = {mode for mode, _ in CASES}
    assert covered == ships, (
        f"{len(ships)} connectors ship ({', '.join(sorted(ships))}); "
        f"the suite runs {len(covered)} ({', '.join(sorted(covered))})"
    )


def test_a_connector_that_never_says_no_fails_the_suite(tmp_path):
    """The suite has to be able to fail. A connector whose `check` always
    passes and whose writes never raise is the exact shape this exists to
    catch, so one is built here and must not survive."""
    case = file_case(tmp_path)

    class AlwaysFine:
        def fetch_orders(self):
            return []

        def acknowledge(self, order_code):
            pass

        def send_confirmation(self, confirmation):
            pass

    with pytest.raises(AssertionError) as failure:
        conformance.check_conformance(lambda: ConformanceCase(
            name="a connector that never says no",
            adapter=AlwaysFine(),
            place_order=case.place_order,
            delivered=lambda: [],
            break_the_far_side=lambda: None,
        ))
    assert "conformance obligations failed" in str(failure.value)
    assert "a_write_the_erp_did_not_keep_raises_rather_than_returning_quietly" in str(failure.value)
