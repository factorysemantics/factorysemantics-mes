"""The SAP-shaped ERP contract and the outbox that carries it.

Per-operation confirmations with cost center, times and consumption; an
order completion at the end; every message keyed so a re-run never posts
twice; and an outbox whose failure path is finally tested - backoff,
a dead-letter state after enough attempts, and a supervisor's retry.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from fsmes.db import utcnow
from fsmes.domain import Equipment, ErpMessage, MessageDirection, MessageStatus, ProductionSource
from fsmes.integrations.erp import contract, mock_erp
from fsmes.integrations.erp.rest_adapter import RestErpAdapter
from fsmes.integrations.erp.sync import cycle
from fsmes.services import erp, execution, masterdata, workorders


def _run_order(session, code="WO-CT-1", qty=10.0):
    wo = workorders.create(session, code=code, material_code="FG-COLA", quantity=qty, actor="test")
    workorders.release(session, code, "test")
    line = masterdata.work_center_of(session, wo.operations[0].equipment)
    if line is not None:
        line.cost_center = "CC-LINE"
    session.flush()
    return wo


def test_completing_an_operation_queues_a_typed_confirmation_with_cost_center_and_consumption(session):
    wo = _run_order(session)
    first = sorted(wo.operations, key=lambda o: o.seq)[0]
    from fsmes.domain import Material, MaterialType
    raw = session.scalar(select(Material).where(Material.type == MaterialType.RAW))
    lot = execution.create_lot(session, code="LOT-SYRUP-9", material_code=raw.code, quantity=50, actor="test")
    workorders.start_operation(session, wo.code, first.seq, actor="test")
    execution.consume(session, order_code=wo.code, lot_code=lot.code, quantity=4, seq=first.seq, actor="test")
    execution.report(session, order_code=wo.code, seq=first.seq, good=9, scrap=1, actor="test")
    workorders.complete_operation(session, wo.code, first.seq, actor="test")

    rows = list(session.scalars(select(ErpMessage).where(ErpMessage.direction == MessageDirection.OUT)))
    ops = [r for r in rows if r.kind == "operation_confirmation"]
    assert len(ops) == 1
    c = contract.parse_confirmation(ops[0].payload)
    assert isinstance(c, contract.OperationConfirmation)
    assert c.order == wo.code and c.seq == first.seq and c.equipment == first.equipment.code
    assert c.cost_center == "CC-LINE"
    assert (c.input_qty, c.good_qty, c.scrap_qty, c.wip_qty) == (10, 9, 1, 0)
    assert [x.lot for x in c.components] == ["LOT-SYRUP-9"] and c.components[0].quantity == 4
    assert c.machine_seconds is not None and c.machine_seconds >= 0
    assert ops[0].message_key == f"{wo.code}:op{first.seq}"
    assert ops[0].attempts == 0 and ops[0].status is MessageStatus.PENDING


def test_finishing_the_order_queues_one_completion_and_keys_are_idempotent(session):
    wo = _run_order(session, "WO-CT-2", 3)
    execution.report(session, equipment_code="MIX01", good=3, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=3, source=ProductionSource.OPC)
    kinds = [r.kind for r in session.scalars(select(ErpMessage).where(ErpMessage.direction == MessageDirection.OUT))]
    assert kinds.count("operation_confirmation") == 2 and kinds.count("order_completion") == 1
    again = erp.enqueue_confirmation(session, wo)
    assert again.message_key == f"{wo.code}:completion"
    assert kinds.count("order_completion") == len([r for r in session.scalars(
        select(ErpMessage).where(ErpMessage.kind == "order_completion"))])
    done = contract.parse_confirmation(session.scalar(
        select(ErpMessage).where(ErpMessage.kind == "order_completion")).payload)
    assert isinstance(done, contract.OrderCompletion) and done.good_qty == 3 and done.lot == f"{wo.code}-FG"


class FailingAdapter:
    def __init__(self, fail_times: int):
        self.fail_times, self.sent = fail_times, []

    def fetch_orders(self):
        return []

    def acknowledge(self, code):
        pass

    def send_confirmation(self, confirmation):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("ERP down")
        self.sent.append(confirmation)


def test_a_failing_delivery_backs_off_then_dies_then_can_be_revived(session, scope):
    _run_order(session, "WO-CT-3", 2)
    execution.report(session, equipment_code="MIX01", good=2, source=ProductionSource.OPC)
    message = session.scalar(select(ErpMessage).where(ErpMessage.direction == MessageDirection.OUT))

    adapter = FailingAdapter(fail_times=99)
    cycle(adapter, scope)
    session.refresh(message)
    assert message.status is MessageStatus.PENDING and message.attempts == 1
    assert "ERP down" in message.error and message.next_attempt_at > utcnow()

    # Not retried before its time.
    cycle(adapter, scope)
    session.refresh(message)
    assert message.attempts == 1

    # Time passes, attempt after attempt, until it is declared dead.
    for n in range(2, erp.MAX_ATTEMPTS + 1):
        message.next_attempt_at = utcnow() - timedelta(seconds=1)
        session.flush()
        cycle(adapter, scope)
        session.refresh(message)
        assert message.attempts == n
    assert message.status is MessageStatus.DEAD
    assert erp.outbox_summary(session)["counts"]["dead"] == 1

    # A supervisor revives it once the ERP is back; success clears the error.
    adapter.fail_times = 0
    erp.retry(session, message.id, actor="sup")
    session.flush()
    cycle(adapter, scope)
    session.refresh(message)
    assert message.status is MessageStatus.SENT and message.error is None
    assert isinstance(adapter.sent[0], contract.OperationConfirmation)


def test_backoff_grows_and_is_capped():
    assert erp.backoff_seconds(1) == 5
    assert erp.backoff_seconds(2) == 10
    assert erp.backoff_seconds(4) == 40
    assert erp.backoff_seconds(20) == erp.MAX_BACKOFF_SECONDS


def test_the_mock_erp_receives_both_kinds_through_the_rest_adapter(session, scope):
    mock_erp.ORDERS.clear()
    mock_erp.CONFIRMATIONS.clear()
    from fastapi.testclient import TestClient
    adapter = RestErpAdapter("http://testserver", client=TestClient(mock_erp.app))
    adapter.client.post("/orders", json={"code": "WO-CT-4", "material": "FG-COLA", "quantity": 2})
    cycle(adapter, scope)
    workorders.release(session, "WO-CT-4", "test")
    execution.report(session, equipment_code="MIX01", good=2, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=2, source=ProductionSource.OPC)
    cycle(adapter, scope)
    kinds = [c["kind"] for c in mock_erp.CONFIRMATIONS]
    assert kinds.count("operation_confirmation") == 2 and kinds.count("order_completion") == 1
    op = next(c for c in mock_erp.CONFIRMATIONS if c["kind"] == "operation_confirmation")
    assert {"cost_center", "machine_seconds", "components", "wip_qty"} <= set(op)
    assert mock_erp.ORDERS["WO-CT-4"]["status"] == "confirmed"


def test_the_outbox_is_visible_and_retry_is_gated(session, sign_in):
    _run_order(session, "WO-CT-5", 1)
    execution.report(session, equipment_code="MIX01", good=1, source=ProductionSource.OPC)
    sup = sign_in("SUP-ERP", role="supervisor")
    out = sup.get("/erp/outbox").json()
    assert out["counts"]["pending"] >= 1 and out["recent"][0]["kind"] == "operation_confirmation"
    mid = out["recent"][0]["id"]
    assert sup.post(f"/erp/outbox/{mid}/retry").status_code == 200
    op = sign_in("OP-ERP", role="operator")
    assert op.post(f"/erp/outbox/{mid}/retry").status_code == 403


def test_production_requests_accept_the_old_spellings():
    r = contract.ProductionRequest.from_payload(
        {"order": "WO-9", "product": "FG-COLA", "quantity": "4", "due_date": ""})
    assert (r.code, r.material, r.quantity, r.due_date, r.erp_reference) == ("WO-9", "FG-COLA", 4.0, None, "WO-9")
    with pytest.raises(ValueError):
        contract.ProductionRequest.from_payload({"material": "X"})
    legacy = contract.parse_confirmation({"order": "WO-1", "material": "FG-COLA", "ordered_qty": 5,
                                          "good_qty": 5, "scrap_qty": 0})
    assert isinstance(legacy, contract.OrderCompletion) and legacy.message_key == "WO-1:completion"


def test_equipment_cost_center_reaches_the_confirmation_from_the_cell_override(session):
    wo = _run_order(session, "WO-CT-6", 1)
    first = sorted(wo.operations, key=lambda o: o.seq)[0]
    machine = session.get(Equipment, first.equipment_id)
    machine.cost_center = "CC-CELL"
    session.flush()
    execution.report(session, equipment_code=machine.code, good=1, source=ProductionSource.OPC)
    row = session.scalar(select(ErpMessage).where(ErpMessage.message_key == f"{wo.code}:op{first.seq}"))
    assert row.payload["cost_center"] == "CC-CELL"
