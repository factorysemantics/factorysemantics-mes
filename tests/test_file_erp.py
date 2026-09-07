"""File-based ERP exchange: B2MML-lite schedules in, confirmations out."""

from fsmes.integrations.erp import b2mml
from fsmes.integrations.erp.file_adapter import FileErpAdapter
from fsmes.integrations.erp.sync import cycle
from fsmes.services import workorders

SCHEDULE_XML = """<?xml version='1.0'?>
<ProductionSchedule>
  <ProductionRequest>
    <ID>WO-FILE-1</ID>
    <Product>FG-COLA</Product>
    <Quantity>5</Quantity>
    <Priority>10</Priority>
  </ProductionRequest>
</ProductionSchedule>
"""


def test_schedule_file_becomes_work_order(tmp_path, session, scope):
    adapter = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "archive")
    (tmp_path / "in" / "schedule.xml").write_text(SCHEDULE_XML, encoding="utf-8")

    cycle(adapter, scope)

    wo = workorders.get(session, "WO-FILE-1")
    assert wo.quantity == 5 and wo.priority == 10
    assert not list((tmp_path / "in").iterdir())  # inbox drained
    assert (tmp_path / "archive" / "schedule.xml").exists()  # history kept


def test_confirmation_written_as_b2mml(tmp_path):
    adapter = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "archive")
    from fsmes.integrations.erp.contract import OrderCompletion
    adapter.send_confirmation(OrderCompletion(message_key="WO-FILE-1:completion", order="WO-FILE-1",
                                              material="FG-COLA", ordered_qty=5, good_qty=5, scrap_qty=0))

    files = list((tmp_path / "out").glob("confirmation_WO-FILE-1_*.xml"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "<GoodQuantity>5</GoodQuantity>" in content


def test_b2mml_parse_render_symmetry():
    orders = b2mml.parse_production_schedule(SCHEDULE_XML)
    assert orders == [
        {"code": "WO-FILE-1", "material": "FG-COLA", "quantity": 5.0, "due_date": None, "priority": 10}
    ]
    xml = b2mml.render_production_performance({"order": "WO-1", "good_qty": 9})
    assert "<ID>WO-1</ID>" in xml and "<GoodQuantity>9</GoodQuantity>" in xml


def test_an_operation_confirmation_renders_as_a_segment_response(tmp_path):
    from fsmes.integrations.erp.contract import ComponentUse, OperationConfirmation
    adapter = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "archive")
    adapter.send_confirmation(OperationConfirmation(
        message_key="WO-FILE-2:op10", order="WO-FILE-2", material="FG-COLA", seq=10, operation="Mix",
        equipment="MIX01", work_center="LINE-A", cost_center="CC-100", input_qty=5, good_qty=5, scrap_qty=0,
        wip_qty=0, machine_seconds=120.0,
        components=[ComponentUse(lot="LOT-1", material="RAW-SYRUP", quantity=2.5, equipment="MIX01")]))
    [path] = list((tmp_path / "out").glob("confirmation_WO-FILE-2_op10_*.xml"))
    xml = path.read_text(encoding="utf-8")
    assert "<SegmentResponse>" in xml and "<CostCenter>CC-100</CostCenter>" in xml
    assert "<MaterialLotID>LOT-1</MaterialLotID>" in xml and "<MachineSeconds>120</MachineSeconds>" in xml


def test_an_unreadable_schedule_is_kept_as_rejected_not_silently_archived(tmp_path, scope):
    adapter = FileErpAdapter(tmp_path / "in", tmp_path / "out", tmp_path / "archive")
    (tmp_path / "in" / "garbage.xml").write_text("<not xml", encoding="utf-8")
    cycle(adapter, scope)
    assert (tmp_path / "archive" / "garbage.xml.rejected").exists()
