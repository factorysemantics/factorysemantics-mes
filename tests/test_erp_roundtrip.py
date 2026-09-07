"""The full ERP loop, driven through the real REST adapter against the mock ERP
(in-process): order in -> acknowledged -> produced -> confirmation out."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fsmes.domain import ErpMessage, MessageDirection, MessageStatus, OrderStatus, ProductionSource
from fsmes.integrations.erp import mock_erp
from fsmes.integrations.erp.rest_adapter import RestErpAdapter
from fsmes.integrations.erp.sync import cycle
from fsmes.services import execution, workorders


@pytest.fixture()
def erp():
    mock_erp.ORDERS.clear()
    mock_erp.CONFIRMATIONS.clear()
    return RestErpAdapter("http://testserver", client=TestClient(mock_erp.app))


def test_full_roundtrip(session, erp, scope):
    erp.client.post("/orders", json={"code": "WO-ERP-1", "material": "FG-COLA", "quantity": 3})

    cycle(erp, scope)  # inbound: import + acknowledge
    wo = workorders.get(session, "WO-ERP-1")
    assert wo.erp_reference == "ERP-000001"
    assert mock_erp.ORDERS["WO-ERP-1"]["status"] == "acked"

    workorders.release(session, "WO-ERP-1", "test")
    execution.report(session, equipment_code="MIX01", good=3, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=3, source=ProductionSource.OPC)
    assert wo.status is OrderStatus.COMPLETED

    cycle(erp, scope)  # outbound: one confirmation per operation, then the completion
    done = [c for c in mock_erp.CONFIRMATIONS if c["kind"] == "order_completion"]
    assert done[0]["order"] == "WO-ERP-1" and done[0]["good_qty"] == 3
    assert len([c for c in mock_erp.CONFIRMATIONS if c["kind"] == "operation_confirmation"]) == 2
    assert mock_erp.ORDERS["WO-ERP-1"]["status"] == "confirmed"

    for sent in session.scalars(select(ErpMessage).where(ErpMessage.direction == MessageDirection.OUT)):
        assert sent.status is MessageStatus.SENT


def test_bad_order_is_recorded_not_fatal(session, erp, scope):
    erp.client.post("/orders", json={"code": "WO-BAD", "material": "NO-SUCH-MATERIAL", "quantity": 1})
    cycle(erp, scope)  # must not raise

    message = session.scalar(select(ErpMessage).where(ErpMessage.direction == MessageDirection.IN))
    assert message.status is MessageStatus.ERROR
    assert "NO-SUCH-MATERIAL" in message.error


def test_repeated_sync_does_not_duplicate_orders(session, erp, scope):
    erp.client.post("/orders", json={"code": "WO-ERP-2", "material": "FG-COLA", "quantity": 5})
    cycle(erp, scope)
    cycle(erp, scope)  # order is acked now, so fetch returns nothing new
    assert len(session.scalars(select(ErpMessage).where(ErpMessage.direction == MessageDirection.IN)).all()) == 1
