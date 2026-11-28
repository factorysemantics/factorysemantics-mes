"""The repair order's Part used box offers the ERP's own parts list.

Scott, 2026-11-28: the MES reads the plant's parts from the ERP; it keeps no
list of its own, and no price. The close form asks `/maintenance/parts` when
it opens; the MES reads the ERP's list through the connection, holds it in
memory a few minutes, and writes none of it down. With no ERP, or an ERP
connection that offers no list, the box offers the codes this plant has used
before. With the ERP down the form says so and a typed code still saves.

ERPNext and the plain-web ERP are scripted transports here, as in
`test_a_repair_order_closed_with_a_part_tells_the_erp_once.py`. The browser
half is `test_the_part_used_box_in_a_browser.py`.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import event

from fsmes.config import Settings
from fsmes.integrations.erp import base
from fsmes.integrations.erp.contract import SparePart
from fsmes.integrations.erp.erpnext_adapter import ErpNextAdapter, ErpNextClient
from fsmes.integrations.erp.file_adapter import FileErpAdapter
from fsmes.integrations.erp.rest_adapter import RestErpAdapter
from fsmes.services import maintenance, spare_parts

PRICE_WORDS = ("price", "rate", "valuation", "cost", "amount", "value")


@pytest.fixture(autouse=True)
def _no_copy_between_tests():
    spare_parts.forget()
    yield
    spare_parts.forget()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _band():
    return SparePart(code="SP-HEATER-BAND", name="Heater band", unit="Nos", in_stores=3)


def _close(session, *, part_code="SP-HEATER-BAND"):
    order = maintenance.raise_corrective(session, equipment_code="MIX01", summary="Heater out")
    maintenance.start(session, order.code, actor="MT-01")
    return maintenance.complete(session, order.code, findings="band replaced", downtime_minutes=42.0,
                                part_code=part_code, part_qty=1, actor="MT-01")


def _no_price(rows):
    text = json.dumps(rows).lower()
    return not any(word in text for word in PRICE_WORDS)


# ------------------------------------------------------------ the connections

class FakeErpNext:
    """Items in the Spare Parts group, each with the valuation ERPNext keeps
    beside it, and their stock lines."""

    def __init__(self):
        self.asked: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.asked.append((path, dict(request.url.params)))
        if path == "/api/resource/Item":
            return httpx.Response(200, json={"data": [
                {"name": "SP-HEATER-BAND", "item_name": "Heater band", "stock_uom": "Nos",
                 "valuation_rate": 140.0, "standard_rate": 150.0},
                {"name": "SP-THERMOCOUPLE", "item_name": "Thermocouple J", "stock_uom": "Nos",
                 "valuation_rate": 22.0},
            ]})
        if path == "/api/resource/Bin":
            return httpx.Response(200, json={"data": [
                {"item_code": "SP-HEATER-BAND", "actual_qty": 2, "valuation_rate": 140.0},
                {"item_code": "SP-HEATER-BAND", "actual_qty": 1, "stock_value": 140.0},
            ]})
        return httpx.Response(404, json={"exception": f"no route for {path}"})


def _erpnext(fake, **kwargs) -> ErpNextAdapter:
    return ErpNextAdapter(ErpNextClient(
        "http://erp.test", site="mes.localhost",
        client=httpx.Client(base_url="http://erp.test", transport=httpx.MockTransport(fake.handler))), **kwargs)


def test_erpnext_gives_the_spare_parts_groups_items_with_their_count_and_no_price_though_it_sends_one():
    fake = FakeErpNext()
    parts = base.list_parts(_erpnext(fake))
    assert [p.model_dump() for p in parts] == [
        {"code": "SP-HEATER-BAND", "name": "Heater band", "unit": "Nos", "in_stores": 3.0},
        {"code": "SP-THERMOCOUPLE", "name": "Thermocouple J", "unit": "Nos", "in_stores": None},
    ]
    assert _no_price([p.model_dump() for p in parts])
    item_ask = next(params for path, params in fake.asked if path == "/api/resource/Item")
    assert json.loads(item_ask["fields"]) == ["name", "item_name", "stock_uom"]
    assert ["item_group", "=", "Spare Parts"] in json.loads(item_ask["filters"])


def test_erpnext_counts_only_the_named_warehouse_when_one_is_set():
    fake = FakeErpNext()
    base.list_parts(_erpnext(fake, parts_warehouse="Stores - CW"))
    bin_ask = next(params for path, params in fake.asked if path == "/api/resource/Bin")
    assert ["warehouse", "=", "Stores - CW"] in json.loads(bin_ask["filters"])


def test_a_part_erpnext_holds_no_stock_line_for_has_an_unknown_count_not_zero():
    parts = base.list_parts(_erpnext(FakeErpNext()))
    assert parts[1].in_stores is None


def test_the_plain_web_erp_drops_a_price_it_sends():
    def handler(request):
        return httpx.Response(200, json=[{"code": "SP-SEAL-KIT", "name": "Seal kit", "unit": "Nos",
                                          "in_stores": 5, "price": 12.5, "unit_cost": 11}])
    adapter = RestErpAdapter("http://erp.test", client=httpx.Client(
        base_url="http://erp.test", transport=httpx.MockTransport(handler)))
    [part] = base.list_parts(adapter)
    assert part.model_dump() == {"code": "SP-SEAL-KIT", "name": "Seal kit", "unit": "Nos", "in_stores": 5.0}


def test_the_file_connection_reads_its_parts_file_and_drops_a_price(tmp_path):
    parts_file = tmp_path / "parts.json"
    parts_file.write_text(json.dumps([{"code": "SP-HEATER-BAND", "name": "Heater band",
                                       "in_stores": 3, "valuation_rate": 140}]), encoding="utf-8")
    adapter = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "arc", parts_file=parts_file)
    [part] = base.list_parts(adapter)
    assert (part.code, part.name, part.in_stores) == ("SP-HEATER-BAND", "Heater band", 3.0)
    assert _no_price([part.model_dump()])


def test_the_file_connection_with_no_parts_file_is_not_answering_rather_than_an_empty_store(tmp_path):
    adapter = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "arc",
                             parts_file=tmp_path / "missing.json")
    with pytest.raises(FileNotFoundError):
        base.list_parts(adapter)


def test_a_part_has_no_field_a_price_could_live_in():
    assert set(SparePart.model_fields) == {"code", "name", "unit", "in_stores"}


def test_the_mock_erp_answers_with_a_small_example_and_no_price():
    from fastapi.testclient import TestClient

    from fsmes.integrations.erp.mock_erp import app

    rows = TestClient(app).get("/parts").json()
    assert rows and _no_price(rows)


# ------------------------------------------------------------ the four states

class ConfirmsOnly:
    """A connector written before the parts call existed."""

    def fetch_orders(self):
        return []

    def acknowledge(self, code):
        pass

    def send_confirmation(self, confirmation):
        pass


def test_an_old_connector_without_the_call_gives_the_codes_used_before_not_an_error(session, monkeypatch):
    _close(session, part_code="SP-OLD-SEAL")
    monkeypatch.setattr(base, "make_adapter", lambda settings: ConfirmsOnly())
    monkeypatch.setattr(base, "_PARTS_ADAPTERS", {})
    connected, lister, key = base.parts_source(Settings(erp_mode="rest"))
    offer = spare_parts.offer(session, connected=connected, lister=lister, key=key)
    assert offer["erp"] == "no_list"
    assert offer["items"] == [] and offer["total"] == 0
    assert [p["code"] for p in offer["used_before"]] == ["SP-OLD-SEAL"]
    assert offer["used_before_total"] == 1


def test_a_plant_with_no_erp_is_offered_the_codes_used_before(session):
    _close(session, part_code="SP-OLD-SEAL")
    connected, lister, key = base.parts_source(Settings(erp_mode="off"))
    offer = spare_parts.offer(session, connected=connected, lister=lister, key=key)
    assert offer["erp"] == "off" and offer["used_before_total"] == 1


def test_the_erp_not_answering_is_said_plainly_and_a_typed_code_still_saves(session):
    def down():
        raise httpx.ConnectError("connection refused")
    offer = spare_parts.offer(session, connected=True, lister=down, key="erp")
    assert offer["erp"] == "not_answering" and offer["items"] == []
    done = _close(session, part_code="SP-HEATER-BAND")
    assert (done.status.value, done.part_code, done.part_qty) == ("done", "SP-HEATER-BAND", 1.0)
    assert done.part_name is None


def test_the_erps_list_comes_with_the_time_it_was_read_and_its_total(session):
    offer = spare_parts.offer(session, connected=True, lister=lambda: [_band()], key="erp")
    assert offer["erp"] == "listed" and offer["read_at"] is not None
    assert offer["total"] == len(offer["items"]) == 1
    assert offer["items"][0] == {"code": "SP-HEATER-BAND", "name": "Heater band", "unit": "Nos", "in_stores": 3.0}


def test_a_part_the_erp_listed_keeps_its_name_on_the_repair_order(session):
    spare_parts.offer(session, connected=True, lister=lambda: [_band()], key="erp")
    done = _close(session, part_code="sp-heater-band")
    assert done.part_name == "Heater band"


def test_a_code_the_erp_does_not_know_still_saves_with_no_name(session):
    spare_parts.offer(session, connected=True, lister=lambda: [_band()], key="erp")
    done = _close(session, part_code="SP-NOT-IN-STORES")
    assert (done.status.value, done.part_code, done.part_name) == ("done", "SP-NOT-IN-STORES", None)


def test_the_copy_in_memory_is_used_for_its_minutes_and_dropped_after(session):
    clock, reads = Clock(), []

    def lister():
        reads.append(clock.now)
        return [_band()]
    spare_parts.offer(session, connected=True, lister=lister, key="erp", minutes=5, clock=clock)
    clock.now += 4 * 60
    spare_parts.offer(session, connected=True, lister=lister, key="erp", minutes=5, clock=clock)
    assert len(reads) == 1
    assert spare_parts.name_of("SP-HEATER-BAND", clock=clock) == "Heater band"
    clock.now += 2 * 60
    assert spare_parts.name_of("SP-HEATER-BAND", clock=clock) is None
    spare_parts.offer(session, connected=True, lister=lister, key="erp", minutes=5, clock=clock)
    assert len(reads) == 2


def test_a_copy_read_from_one_erp_is_never_offered_for_another(session):
    spare_parts.offer(session, connected=True, lister=lambda: [_band()], key="erp-a")
    offer = spare_parts.offer(session, connected=True, lister=lambda: [], key="erp-b")
    assert offer["items"] == []


def test_reading_the_list_writes_nothing_to_the_database(session):
    _close(session, part_code="SP-OLD-SEAL")
    session.flush()
    written: list[str] = []
    bind = session.get_bind()

    def watch(conn, cursor, statement, *args):
        if statement.lstrip().split(None, 1)[0].upper() in ("INSERT", "UPDATE", "DELETE", "CREATE"):
            written.append(statement)
    event.listen(bind, "before_cursor_execute", watch)
    try:
        spare_parts.offer(session, connected=True, lister=lambda: [_band()], key="erp")
        spare_parts.offer(session, connected=True, lister=lambda: [_band()], key="erp")
        session.flush()
    finally:
        event.remove(bind, "before_cursor_execute", watch)
    assert written == [] and not session.new and not session.dirty


# ------------------------------------------------------------ the read address

def test_the_read_address_answers_with_the_state_and_the_codes_used_before(client, session, monkeypatch):
    from fsmes import config

    _close(session, part_code="SP-OLD-SEAL")
    monkeypatch.setattr(config, "get_settings", lambda: Settings(erp_mode="off"))
    monkeypatch.setattr("fsmes.api.routers.maintenance.get_settings", lambda: Settings(erp_mode="off"))
    body = client.get("/maintenance/parts").json()
    assert body["erp"] == "off"
    assert body["used_before"] == [{"code": "SP-OLD-SEAL", "name": None}]


def test_history_and_the_export_show_the_parts_name_beside_its_code(client, session):
    spare_parts.offer(session, connected=True, lister=lambda: [_band()], key="erp")
    done = _close(session, part_code="SP-HEATER-BAND")
    session.flush()
    rows = client.get("/maintenance/orders", params={"status": "done"}).json()["items"]
    mine = next(r for r in rows if r["code"] == done.code)
    assert (mine["part_code"], mine["part_name"]) == ("SP-HEATER-BAND", "Heater band")
    from fsmes.api.routers.maintenance import MAINTENANCE_EXPORT

    assert [c.header for c in MAINTENANCE_EXPORT if c.header.startswith("Part")] == [
        "Part used", "Part name", "Part qty"]
