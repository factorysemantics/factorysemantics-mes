"""A repair order that closes with a part on it tells the ERP, once.

The part used to be words in the findings ("band replaced"), which no ERP can
book. Now a repair order carries a part code and a count - never a price -
and its close queues one `part_used` message for the ERP. ERPNext books it as
a Material Issue: the part out of stores, the line charged to the machine's
cost center. An ERP module that does not book parts says so in the outbox;
a plant with no ERP, and a repair that used no part, are as they were.

ERPNext here is a scripted transport, as in `test_erpnext.py`. The live half
- one in, one out, and $140 on CC-MOULD01 - is in `test_erpnext_live.py`.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from fsmes.db import utcnow
from fsmes.domain import ErpMessage, MessageStatus
from fsmes.integrations.erp.base import make_adapter
from fsmes.integrations.erp.contract import PartUsed, parse_message
from fsmes.integrations.erp.erpnext_adapter import ErpNextAdapter, ErpNextClient, ErpNextRefused
from fsmes.integrations.erp.sync import cycle
from fsmes.services import Invalid, erp, maintenance, masterdata, workorders


def _part_messages(session) -> list[ErpMessage]:
    return list(session.scalars(select(ErpMessage).where(ErpMessage.kind == "part_used")))


@pytest.fixture()
def mixer(session):
    machine = masterdata.get_equipment(session, "MIX01")
    machine.cost_center = "CC-MIX01"
    session.flush()
    return machine


def _broke_and_mended(session, *, part_code=None, part_qty=None, minutes=42.0):
    order = maintenance.raise_corrective(session, equipment_code="MIX01", summary="Heater out")
    maintenance.start(session, order.code, actor="MT-01")
    return maintenance.complete(session, order.code, findings="band replaced", downtime_minutes=minutes,
                                part_code=part_code, part_qty=part_qty, actor="MT-01")


# ------------------------------------------------------------- the MES side

def test_a_repair_order_closed_with_a_part_records_the_part_and_its_count_and_no_price(session, mixer):
    done = _broke_and_mended(session, part_code="SP-HEATER-BAND", part_qty=1)
    assert (done.part_code, done.part_qty) == ("SP-HEATER-BAND", 1.0)
    columns = {c.name for c in done.__table__.columns}
    assert not {c for c in columns if any(w in c for w in ("price", "cost", "rate", "value"))} - {"cost_center"}


def test_a_repair_order_closed_with_a_part_queues_one_message_naming_the_machine_line_and_cost_center(
        session, mixer):
    done = _broke_and_mended(session, part_code="SP-HEATER-BAND", part_qty=1)
    messages = _part_messages(session)
    assert len(messages) == 1
    sent = parse_message(messages[0].payload)
    assert isinstance(sent, PartUsed)
    assert sent.repair_order == done.code
    assert (sent.part_code, sent.quantity) == ("SP-HEATER-BAND", 1.0)
    assert sent.equipment == "MIX01"
    assert sent.work_center == masterdata.work_center_of(session, mixer).code
    assert sent.cost_center == "CC-MIX01"
    assert sent.performed_by == "MT-01"
    assert sent.minutes_down == 42.0
    assert sent.completed_at is not None
    assert not {k for k in messages[0].payload if any(w in k for w in ("price", "cost", "rate"))} - {"cost_center"}


def test_the_same_close_queued_twice_is_one_message(session, mixer):
    done = _broke_and_mended(session, part_code="SP-HEATER-BAND", part_qty=1)
    erp.enqueue_part_used(session, done)
    erp.enqueue_part_used(session, done)
    assert len(_part_messages(session)) == 1


def test_a_repair_order_with_no_part_sends_nothing(session, mixer):
    before = session.scalar(select(ErpMessage.id).order_by(ErpMessage.id.desc()).limit(1))
    done = _broke_and_mended(session)
    assert (done.part_code, done.part_qty) == (None, None)
    assert _part_messages(session) == []
    after = session.scalar(select(ErpMessage.id).order_by(ErpMessage.id.desc()).limit(1))
    assert after == before


def test_a_part_code_without_a_count_is_refused_rather_than_read_as_one(session, mixer):
    with pytest.raises(Invalid, match="count"):
        _broke_and_mended(session, part_code="SP-HEATER-BAND")
    assert _part_messages(session) == []


def test_a_count_without_a_part_code_is_refused(session, mixer):
    with pytest.raises(Invalid, match="code"):
        _broke_and_mended(session, part_qty=1)


def test_a_count_of_zero_is_refused(session, mixer):
    with pytest.raises(Invalid, match="more than zero"):
        _broke_and_mended(session, part_code="SP-HEATER-BAND", part_qty=0)


def test_the_message_names_the_production_order_the_machine_was_on_when_it_broke(session, mixer):
    wo = workorders.create(session, code="WO-RUNNING", material_code="FG-COLA", quantity=100)
    workorders.release(session, wo.code)
    op = wo.operations[0]
    op.equipment_id = mixer.id
    op.started_at = utcnow() - timedelta(minutes=30)
    wo.erp_reference = "WO-ERP-0007"
    session.flush()
    _broke_and_mended(session, part_code="SP-HEATER-BAND", part_qty=1)
    sent = parse_message(_part_messages(session)[0].payload)
    assert (sent.work_order, sent.work_order_erp_reference) == (wo.code, "WO-ERP-0007")


def test_the_close_carries_the_part_through_the_api_and_back(session, mixer, supervisor):
    order = maintenance.raise_corrective(session, equipment_code="MIX01", summary="Heater out")
    session.flush()
    r = supervisor.post(f"/maintenance/orders/{order.code}/complete",
                        json={"findings": "band replaced", "downtime_minutes": 42,
                              "part_code": "SP-HEATER-BAND", "part_qty": 1, "performed_by": "SUPER"})
    assert r.status_code == 200, r.text
    assert (r.json()["part_code"], r.json()["part_qty"]) == ("SP-HEATER-BAND", 1.0)


# ------------------------------------------------------ the sync and modules

class ConfirmsOnly:
    """An ERP module written before this message: it books confirmations only."""

    def __init__(self):
        self.sent = []

    def fetch_orders(self):
        return []

    def acknowledge(self, code):
        pass

    def send_confirmation(self, confirmation):
        self.sent.append(confirmation)


class BooksParts(ConfirmsOnly):
    def __init__(self):
        super().__init__()
        self.parts = []

    def send_part_used(self, part_used):
        self.parts.append(part_used)


def test_a_module_that_books_parts_is_handed_the_part_once(session, mixer, scope):
    _broke_and_mended(session, part_code="SP-HEATER-BAND", part_qty=1)
    adapter = BooksParts()
    cycle(adapter, scope)
    cycle(adapter, scope)
    assert [p.part_code for p in adapter.parts] == ["SP-HEATER-BAND"]
    assert not any(isinstance(c, PartUsed) for c in adapter.sent)
    assert _part_messages(session)[0].status is MessageStatus.SENT


def test_a_module_that_does_not_book_parts_says_so_plainly_and_nothing_else_breaks(session, mixer, scope):
    _broke_and_mended(session, part_code="SP-HEATER-BAND", part_qty=1)
    adapter = ConfirmsOnly()
    cycle(adapter, scope)
    message = _part_messages(session)[0]
    assert message.status is MessageStatus.DEAD
    assert message.attempts == 1
    assert "does not book parts used on repair orders" in message.error
    assert "recorded in the MES only" in message.error
    assert not any(isinstance(c, PartUsed) for c in adapter.sent)


def test_a_plant_with_no_erp_has_no_connector_and_its_close_is_unchanged(session, mixer):
    from fsmes.config import Settings

    assert make_adapter(Settings(erp_mode="off")) is None
    done = _broke_and_mended(session)
    assert done.status.value == "done" and done.downtime_minutes == 42.0


def test_an_order_completion_still_goes_out_beside_a_part_used(session, mixer, scope):
    _broke_and_mended(session, part_code="SP-HEATER-BAND", part_qty=1)
    wo = workorders.create(session, code="WO-DONE", material_code="FG-COLA", quantity=100)
    erp.enqueue_confirmation(session, wo)
    adapter = BooksParts()
    cycle(adapter, scope)
    assert len(adapter.parts) == 1 and len(adapter.sent) >= 1


# ---------------------------------------------------------- ERPNext's side

class FakeStores:
    """A scripted ERPNext holding one cost center, one item with a default
    warehouse, and the stock entries it was asked to post."""

    def __init__(self, cost_centers=(("CC-MOULD01 - CW", "Cutlery Works"),), refuses=""):
        self.cost_centers = [{"name": n, "company": c} for n, c in cost_centers]
        self.refuses = refuses
        self.entries: list[dict] = []
        self.comments: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/api/resource/Stock Entry" and method == "GET":
            filters = json.loads(request.url.params.get("filters", "[]"))
            like = next(f[2] for f in filters if f[0] == "remarks").strip("%")
            hits = [{"name": e["name"]} for e in self.entries if like in e["remarks"]]
            return httpx.Response(200, json={"data": hits})
        if path == "/api/resource/Stock Entry" and method == "POST":
            if self.refuses:
                return httpx.Response(417, json={"exception": f"frappe.exceptions.ValidationError: {self.refuses}"})
            doc = json.loads(request.content)
            doc["name"] = f"MAT-STE-TEST-{len(self.entries) + 1:04d}"
            self.entries.append(doc)
            return httpx.Response(200, json={"data": doc})
        if path == "/api/resource/Cost Center":
            filters = json.loads(request.url.params.get("filters", "[]"))
            name = next(f[2] for f in filters if f[0] == "cost_center_name")
            rows = [c for c in self.cost_centers if c["name"].split(" - ")[0] == name]
            return httpx.Response(200, json={"data": rows})
        if path == "/api/resource/Item/SP-HEATER-BAND":
            return httpx.Response(200, json={"data": {"name": "SP-HEATER-BAND", "item_defaults": [
                {"company": "Cutlery Works", "default_warehouse": "Stores - CW"}]}})
        if path.endswith("utils.add_comment"):
            body = json.loads(request.content)
            self.comments.append((body["reference_name"], body["content"]))
            return httpx.Response(200, json={"message": {}})
        return httpx.Response(404, json={"exception": f"no route for {method} {path}"})


def _erpnext(fake: FakeStores) -> ErpNextAdapter:
    return ErpNextAdapter(ErpNextClient(
        "http://erp.test", site="mes.localhost",
        client=httpx.Client(base_url="http://erp.test", transport=httpx.MockTransport(fake.handler))))


def _band(**overrides) -> PartUsed:
    values = dict(message_key="CM-00001:part:SP-HEATER-BAND", repair_order="CM-00001",
                  part_code="SP-HEATER-BAND", quantity=1, equipment="FORK1_Mold",
                  work_center="FORK1LINE", cost_center="CC-MOULD01", performed_by="MT-01",
                  minutes_down=42.0, work_order="WO-FORK1-0002", work_order_erp_reference="WO-FORK1-0002")
    values.update(overrides)
    return PartUsed(**values)


def test_erpnext_books_the_part_as_a_material_issue_from_stores_against_the_machines_cost_center():
    fake = FakeStores()
    _erpnext(fake).send_part_used(_band())
    [entry] = fake.entries
    assert entry["purpose"] == entry["stock_entry_type"] == "Material Issue"
    assert entry["company"] == "Cutlery Works" and entry["docstatus"] == 1
    assert entry["items"] == [{"item_code": "SP-HEATER-BAND", "qty": 1.0,
                               "s_warehouse": "Stores - CW", "cost_center": "CC-MOULD01 - CW"}]
    assert "CM-00001" in entry["remarks"] and "42 min down" in entry["remarks"]
    assert not any(k in json.dumps(entry) for k in ("basic_rate", "valuation_rate", "amount"))


def test_sent_twice_erpnext_has_one_stock_entry():
    fake = FakeStores()
    adapter = _erpnext(fake)
    adapter.send_part_used(_band())
    adapter.send_part_used(_band())
    assert len(fake.entries) == 1


def test_the_minutes_down_go_as_a_comment_on_the_work_order_that_was_running():
    fake = FakeStores()
    _erpnext(fake).send_part_used(_band())
    assert fake.comments == [("WO-FORK1-0002", "MES-TWIN: repair order CM-00001 on FORK1_Mold, 42 min down; "
                                                "1 x SP-HEATER-BAND issued (MAT-STE-TEST-0001)")]


def test_a_repair_on_a_machine_on_no_erp_order_leaves_no_comment():
    fake = FakeStores()
    _erpnext(fake).send_part_used(_band(work_order=None, work_order_erp_reference=None))
    assert len(fake.entries) == 1 and fake.comments == []


def test_a_cost_center_erpnext_does_not_have_is_a_refusal_the_outbox_stops_on():
    fake = FakeStores(cost_centers=())
    with pytest.raises(ErpNextRefused, match="no cost center named CC-MOULD01") as caught:
        _erpnext(fake).send_part_used(_band())
    assert caught.value.permanent and fake.entries == []


def test_a_part_erpnext_will_not_issue_is_a_refusal_in_erpnexts_own_words():
    fake = FakeStores(refuses="Not enough stock of SP-HEATER-BAND in Stores - CW")
    with pytest.raises(ErpNextRefused, match="Not enough stock") as caught:
        _erpnext(fake).send_part_used(_band())
    assert caught.value.permanent


def test_the_coil_out_breakdown_uses_the_part_the_erp_seed_puts_in_stores():
    from pathlib import Path

    from fsmes.sim import breakdown

    scenes = Path(__file__).resolve().parents[1] / "labs" / "cutlery" / "scenes"
    base = json.loads((scenes / "coil-out.json").read_text(encoding="utf-8"))
    ids = json.loads((scenes / "coil-out-erp.json").read_text(encoding="utf-8"))["erp"]
    [rule] = breakdown.breakdowns({"breakdowns": base["breakdowns"]})
    assert (rule.part_code, rule.part_qty) == (ids["spare"]["code"], 1.0)
    assert "price" not in base["breakdowns"][0]["part"]


def test_the_part_used_kind_is_never_read_as_a_production_confirmation():
    from fsmes.integrations.erp.contract import CONFIRMATION_KINDS, ERP_KINDS

    assert "part_used" in ERP_KINDS and "part_used" not in CONFIRMATION_KINDS
