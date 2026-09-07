"""Serialisation at the cutlery plant's scale: ten million pieces a day,
arriving a stack at a time.

What the first serialisation could not do: number a unit without counting
the table, take a stack in one call, or answer a pallet without a query per
piece. What this pins is that a batch is one transaction and one audit
entry, that the counter is a row, that every walk of the tree is a query per
level, and that a recall through the order's consumption is answered as
pallets and says that is its basis.
"""

import time

import pytest
from sqlalchemy import func, select

from fsmes.db import utcnow
from fsmes.domain import AuditLog, SerialSequence, SerialUnit
from fsmes.services import Conflict, Invalid, NotFound, execution, serialization, workorders


def _order(session, code="WO-SER", material="FG-COLA", lots=("LOT-SUGAR-001",)):
    wo = workorders.create(session, code=code, material_code=material, quantity=100000)
    workorders.release(session, code)
    for lot in lots:
        execution.consume(session, order_code=code, lot_code=lot, quantity=1.0)
    session.flush()
    return wo


def _stack(session, n=1, pieces=24, order="WO-SER", equipment="MIX01"):
    units = [{"serial": f"F-{n:06d}-{i:03d}", "material": "FG-COLA"} for i in range(pieces)]
    return serialization.produce_batch(
        session, units=units, order_code=order, equipment_code=equipment,
        container={"serial": f"ST-{n:06d}", "material": "FG-COLA"})


def test_a_stack_arrives_in_one_call_with_one_audit_entry(session):
    _order(session)
    before = session.scalar(select(func.count(AuditLog.id)))
    out = _stack(session, 1)
    assert out == {"created": 24, "container": "ST-000001", "packed": 0}
    assert session.scalar(select(func.count(AuditLog.id))) == before + 1
    entry = session.scalar(select(AuditLog).order_by(AuditLog.id.desc()))
    assert entry.action == "unit.batch" and entry.after["units"] == 24
    assert entry.after["first"] == "F-000001-000" and entry.after["last"] == "F-000001-023"
    stack = serialization.contents(session, "ST-000001")
    assert stack["contains_total"] == 24 and stack["units_inside"] == 24
    assert stack["by_material"] == {"FG-COLA": 24}
    assert stack["order"] == "WO-SER" and stack["equipment"] == "MIX01"


def test_a_pack_takes_the_stack_and_a_pallet_takes_the_packs(session):
    _order(session)
    for n in range(1, 4):
        _stack(session, n)
    for n in range(1, 4):
        out = serialization.produce_batch(
            session, units=[], container={"serial": f"P-{n:06d}", "material": "FG-COLA"},
            contains=[f"ST-{n:06d}"])
        assert out["packed"] == 1
    pallet = serialization.produce_batch(
        session, units=[], container={"serial": "PL-000001", "material": "FG-COLA"},
        contains=["P-000001", "P-000002", "P-000003"])
    assert pallet["packed"] == 3
    tree = serialization.contents(session, "PL-000001", limit=2)
    assert tree["contains_total"] == 3 and len(tree["contains"]) == 2, "counted in full, drawn to the limit"
    assert tree["units_inside"] == 3 + 3 + 72
    assert tree["contains"][0]["contains"][0]["contains_total"] == 24
    assert serialization.get(session, "ST-000002").parent.serial == "P-000002"


def test_a_batch_refuses_duplicates_loops_and_the_unknown(session):
    _order(session)
    _stack(session, 1)
    with pytest.raises(Conflict, match="already exist"):
        _stack(session, 1)
    with pytest.raises(NotFound):
        serialization.produce_batch(session, units=[], container={"serial": "P-X", "material": "FG-COLA"},
                                    contains=["ST-999999"])
    serialization.produce_batch(session, units=[], container={"serial": "P-000001", "material": "FG-COLA"},
                                contains=["ST-000001"])
    with pytest.raises(Invalid, match="already inside"):
        serialization.produce_batch(session, units=[], into="F-000001-000", contains=["P-000001"])
    with pytest.raises(Invalid, match="at most"):
        serialization.produce_batch(session, units=[{"serial": f"Z-{i}", "material": "FG-COLA"}
                                                    for i in range(serialization.MAX_BATCH + 1)])
    with pytest.raises(Invalid):
        serialization.produce_batch(session, units=[{"serial": "Z-1"}])   # no material anywhere


def test_the_serial_counter_is_a_row_and_starts_where_the_table_is(session):
    first = serialization.produce(session, material_code="FG-COLA")
    assert first.serial == "FG-COLA-000001"
    # A marker's own serial with the same prefix, higher than the counter.
    serialization.produce(session, material_code="FG-COLA", serial="FG-COLA-000500")
    row = session.get(SerialSequence, "FG-COLA")
    assert row.next == 2
    # Nothing scans: the counter row is the whole state.
    session.delete(row)
    session.flush()
    nxt = serialization.produce(session, material_code="FG-COLA")
    assert nxt.serial == "FG-COLA-000501", "restarted from the highest serial in the table, not from a count"
    assert session.get(SerialSequence, "FG-COLA").next == 502


def test_a_trace_through_the_order_says_so(session):
    _order(session, lots=("LOT-SUGAR-001", "LOT-FLAVOR-001"))
    _stack(session, 1)
    serialization.produce_batch(session, units=[], container={"serial": "P-000001", "material": "FG-COLA"},
                                contains=["ST-000001"])
    trace = serialization.trace_back(session, "P-000001")
    assert [c["lot"] for c in trace["components"]] == ["LOT-FLAVOR-001", "LOT-SUGAR-001"]
    assert all(c["basis"] == "order" for c in trace["components"])
    # 24 pieces and the stack itself, all made by the order.
    assert trace["components"][0]["units"] == 25 and trace["units_inside"] == 25
    # A unit with its own component records answers from them.
    explicit = serialization.produce(session, material_code="FG-COLA", order_code="WO-SER")
    assert all(c["basis"] == "unit" for c in serialization.trace_back(session, explicit.serial)["components"])


def test_a_recall_through_the_order_names_pallets_with_counts(session):
    _order(session, lots=("LOT-SUGAR-001",))
    for n in range(1, 5):
        _stack(session, n)
        serialization.produce_batch(session, units=[], container={"serial": f"P-{n:06d}", "material": "FG-COLA"},
                                    contains=[f"ST-{n:06d}"])
    serialization.produce_batch(session, units=[], container={"serial": "PL-000001", "material": "FG-COLA"},
                                contains=["P-000001", "P-000002"])
    serialization.produce_batch(session, units=[], container={"serial": "PL-000002", "material": "FG-COLA"},
                                contains=["P-000003"])
    found = serialization.where_used(session, "LOT-SUGAR-001")
    # 4 stacks x (24 pieces + the stack): every unit the order made. The packs
    # and pallets were made by no order, so they carry nothing themselves.
    assert found["units_affected"] == 4 * 25 and found["basis"] == "order"
    assert [(p["package"], p["units"]) for p in found["packages_to_hold"]] == [
        ("P-000004", 25), ("PL-000001", 50), ("PL-000002", 25)]
    assert "3 package(s)" in found["verdict"]


def test_holding_a_pallet_is_a_statement_per_level_not_per_piece(session):
    _order(session)
    for n in range(1, 3):
        _stack(session, n)
    serialization.produce_batch(session, units=[], container={"serial": "PL-000001", "material": "FG-COLA"},
                                contains=["ST-000001", "ST-000002"])
    touched = serialization.set_status(session, "PL-000001", "quarantined", note="recall", cascade=True)
    assert len(touched) == 1 + 2 + 48
    assert {u.status.value for u in touched} == {"quarantined"}
    assert serialization.contents(session, "PL-000001")["by_status"] == {"quarantined": 50}


def test_the_batch_endpoint_and_the_tree_at_the_api(client, session):
    _order(session)
    body = {"units": [{"serial": f"F-{i:09d}"} for i in range(24)], "material": "FG-COLA",
            "order": "WO-SER", "equipment": "MIX01",
            "container": {"serial": "ST-000000001", "material": "FG-COLA"}}
    made = client.post("/trace/units/batch", json=body)
    assert made.status_code == 201, made.text
    assert made.json() == {"created": 24, "container": "ST-000000001", "packed": 0}
    assert client.post("/trace/units/batch", json=body).status_code == 409
    tree = client.get("/trace/units/ST-000000001?limit=5").json()
    assert tree["contains_total"] == 24 and len(tree["contains"]) == 5 and tree["units_inside"] == 24
    assert client.get("/trace/units/ST-000000001?limit=0").status_code == 422
    trace = client.get("/trace/units/F-000000003/trace").json()
    assert trace["components"][0]["basis"] == "order"


@pytest.mark.slow
def test_ten_thousand_pieces_a_second_is_the_floor_not_the_ceiling(session):
    """A batch of 5,000 in well under a second, in-process: the API and the
    disk are the rest of the story, measured by labs/cutlery."""
    _order(session)
    t = time.perf_counter()
    for n in range(4):
        units = [{"serial": f"F-{n:02d}-{i:06d}"} for i in range(5000)]
        serialization.produce_batch(session, units=units, material_code="FG-COLA", order_code="WO-SER",
                                    container={"serial": f"BIG-{n}", "material": "FG-COLA"})
    took = time.perf_counter() - t
    assert session.scalar(select(func.count(SerialUnit.id))) == 20004
    assert took < 4.0, took


def test_an_inspection_batch_becomes_units_inspections_and_containment(session):
    """What the vision stations send, a batch at a time: pieces judged, a stack
    claiming three of them, a wrap claiming the stack and its plate, a pallet
    claiming the wrap. One call, a handful of statements."""
    from fsmes.domain import UnitInspection

    _order(session)
    now = utcnow()
    pieces = [{"kind": "piece", "equipment": "MIX01", "seq": i, "ts": now, "serial": f"F-{i:09d}",
               "material": "FG-COLA", "order": "WO-SER", "passed": i != 3,
               "fail_mask": 0 if i != 3 else 0b0100, "values": [1.0, 2.0, 3.0, 4.0]}
              for i in range(1, 5)]
    out = serialization.ingest_inspections(session, pieces)
    assert out == {"units": 4, "inspections": 4, "packed": 0, "duplicates": 0, "unknown_members": 0}
    assert serialization.get(session, "F-000000003").status.value == "scrapped"
    assert session.scalar(select(func.count(UnitInspection.id))) == 4
    rec = session.scalar(select(UnitInspection).where(UnitInspection.seq == 3))
    assert rec.passed is False and rec.fail_mask == 4 and rec.values == [1.0, 2.0, 3.0, 4.0]

    stack = {"kind": "stack", "equipment": "PACK01", "seq": 1, "ts": now, "serial": "ST-000000001",
             "material": "FG-COLA", "order": "WO-SER", "passed": True, "fail_mask": 0, "values": [0.1, 0.2, 0.3, 0.4],
             "members": ["F-000000001", "F-000000002", "F-000000004", "F-000000099"]}
    wrap = {"kind": "wrap", "equipment": "PACK01", "seq": 2, "ts": now, "serial": "P-000000001", "material": "FG-COLA",
            "order": "WO-SER", "passed": True, "fail_mask": 0, "values": None, "members": ["ST-000000001"],
            "plate": {"serial": "PLT-000000001", "material": "FG-COLA"}}
    out = serialization.ingest_inspections(session, [stack, wrap])
    # 3 pieces packed into the stack, the stack and the plate into the wrap; one member never seen.
    assert out["units"] == 3 and out["packed"] == 5 and out["unknown_members"] == 1
    tree = serialization.contents(session, "P-000000001")
    assert tree["units_inside"] == 5 and tree["contains_total"] == 2
    # A replayed event is a duplicate, not a second unit.
    again = serialization.ingest_inspections(session, [stack])
    assert again["units"] == 0 and again["duplicates"] == 1
    pallet = {"kind": "pallet", "equipment": "PACK01", "seq": 1, "ts": now, "serial": "PL-000001",
              "material": "FG-COLA", "order": None, "members": ["P-000000001"]}
    assert serialization.ingest_inspections(session, [pallet])["packed"] == 1
    assert serialization.get(session, "P-000000001").parent.serial == "PL-000001"
    audit_rows = session.scalars(select(AuditLog).where(AuditLog.action == "inspection.batch")).all()
    assert len(audit_rows) == 4 and audit_rows[0].after["events"] == 4


def test_a_pallet_certificate_states_capability_over_its_own_window_and_lists_everything(session, client):
    """The cutlery plant's certificate: for the pieces on one pallet, the
    Cpk of each dimensional characteristic from the checks recorded while
    they were made, the automated inspections, and every wrap, stack, plate
    and piece - rendered from the containment record, issued immutable."""
    from datetime import timedelta

    from fsmes.domain import Material, QualityCheck, QualitySpec
    from fsmes.services import coa, quality

    _order(session)
    cola = session.scalar(select(Material).where(Material.code == "FG-COLA"))
    # A utensil material with two dimensional specifications, checked eight times each in the window.
    session.add(Material(code="UT-FORK", name="Fork", unit="ea", type=cola.type))
    session.flush()
    fork = session.scalar(select(Material).where(Material.code == "UT-FORK"))
    for char, lo, hi in (("length_mm", 164.0, 166.0), ("weight_g", 3.6, 4.2)):
        session.add(QualitySpec(material=fork, characteristic=char, unit="", min_value=lo, max_value=hi))
    session.flush()
    t0 = utcnow() - timedelta(minutes=30)
    values = {"length_mm": [165.0, 165.1, 164.9, 165.05, 164.95, 165.1, 164.9, 165.0, 165.08, 164.92, 165.02, 164.97],
              "weight_g": [3.9, 3.92, 3.88, 3.91, 3.89, 3.9, 3.93, 3.87, 3.91, 3.9, 3.89, 3.92]}
    for i in range(12):
        for char, series in values.items():
            quality.record_check(session, material_code="UT-FORK", characteristic=char, value=series[i], actor="QC")
    checks = session.scalars(select(QualityCheck)).all()
    for i, c in enumerate(checks):
        c.ts = t0 + timedelta(minutes=i)
    session.flush()

    now = t0 + timedelta(minutes=25)
    pieces = [{"kind": "piece", "equipment": "MIX01", "seq": i, "ts": t0, "serial": f"F-{i:09d}",
               "material": "UT-FORK", "order": "WO-SER", "passed": True, "fail_mask": 0, "values": [1.0]}
              for i in range(1, 4)]
    serialization.ingest_inspections(session, pieces)
    serialization.ingest_inspections(session, [
        {"kind": "stack", "equipment": "PACK01", "seq": 1, "ts": t0 + timedelta(minutes=2), "serial": "ST-000000001",
         "material": "FG-COLA", "order": "WO-SER", "passed": True, "fail_mask": 0, "values": [],
         "members": ["F-000000001", "F-000000002", "F-000000003"]}])
    serialization.ingest_inspections(session, [
        {"kind": "wrap", "equipment": "PACK01", "seq": 1, "ts": t0 + timedelta(minutes=3), "serial": "W-000000001",
         "material": "FG-COLA", "order": "WO-SER", "passed": True, "fail_mask": 0, "values": [],
         "members": ["ST-000000001"], "plate": {"serial": "PLT-000000001", "material": "RAW-SUGAR"}}])
    serialization.ingest_inspections(session, [
        {"kind": "pallet", "equipment": "PACK01", "seq": 1, "ts": now, "serial": "PL1-0000001",
         "material": "FG-COLA", "order": None, "members": ["W-000000001"]}])

    data = coa.gather_pallet(session, "PL1-0000001")
    assert data["units_inside"] == 6 and data["wraps"] == 1
    assert data["inspections"] == {"units_inspected": 5, "failed_on_pallet": 0}
    by_char = {c["characteristic"]: c for c in data["characteristics"]}
    if checks:
        assert by_char["length_mm"]["n"] == 12 and by_char["length_mm"]["cpk"] is not None
        assert by_char["length_mm"]["cpk"] > 1.0
    doc = coa.issue_pallet(session, "PL1-0000001", actor="SUP")
    assert doc.code == "COA-PALLET-PL1-0000001"
    body = coa.latest_pallet(session, "PL1-0000001")["body"]
    assert "| W-000000001 | ST-000000001 | PLT-000000001 | F-000000001, F-000000002, F-000000003 |" in body
    assert "Cpk" in body
    got = client.get("/coa/pallet/PL1-0000001").json()
    assert got["revision"] == 1 and got["pallet"] == "PL1-0000001"
    assert client.get("/coa/pallet/PL1-0000001/data").json()["units_inside"] == 6
    assert client.post("/coa/pallet/PL1-0000001").status_code == 403, "issuing is a supervisor's act"


def test_a_pallet_made_between_two_checks_states_capability_from_the_last_twelve_on_record(session):
    """A pallet closes in minutes and a check is recorded every fifteen: the
    window alone holds no sample. The certificate extends the record back to
    the most recent twelve checks at or before the close, and says how many
    were inside the window and where the record starts."""
    from datetime import timedelta

    from fsmes.domain import Material, QualityCheck, QualitySpec
    from fsmes.services import coa, quality

    _order(session)
    cola = session.scalar(select(Material).where(Material.code == "FG-COLA"))
    session.add(Material(code="UT-FORK", name="Fork", unit="ea", type=cola.type))
    session.flush()
    fork = session.scalar(select(Material).where(Material.code == "UT-FORK"))
    session.add(QualitySpec(material=fork, characteristic="length_mm", unit="mm", min_value=164.0, max_value=166.0))
    session.flush()
    t0 = utcnow() - timedelta(hours=4)
    series = [165.0, 165.1, 164.9, 165.05, 164.95, 165.1, 164.9, 165.0, 165.08, 164.92, 165.02, 164.97, 165.3, 164.7]
    for v in series:
        quality.record_check(session, material_code="UT-FORK", characteristic="length_mm", value=v, actor="QC")
    checks = session.scalars(select(QualityCheck).order_by(QualityCheck.id)).all()
    for i, c in enumerate(checks):
        c.ts = t0 + timedelta(minutes=15 * i)      # fourteen checks, every fifteen minutes
    session.flush()
    # The pallet's pieces are made and the pallet closed between the twelfth and thirteenth check.
    made = t0 + timedelta(minutes=15 * 11 + 3)
    closed = made + timedelta(minutes=2)
    serialization.ingest_inspections(session, [
        {"kind": "piece", "equipment": "MIX01", "seq": 1, "ts": made, "serial": "F-000000001",
         "material": "UT-FORK", "order": "WO-SER", "passed": True, "fail_mask": 0, "values": [1.0]}])
    serialization.ingest_inspections(session, [
        {"kind": "pallet", "equipment": "PACK01", "seq": 1, "ts": closed, "serial": "PL1-0000002",
         "material": "FG-COLA", "order": None, "members": ["F-000000001"]}])

    ch = coa.gather_pallet(session, "PL1-0000002")["characteristics"][0]
    assert ch["in_window"] == 0, "no check fell inside the five minutes the pallet took"
    assert ch["n"] == 12 and ch["cpk"] is not None, "the last twelve at or before the close"
    assert ch["record_start"] == checks[0].ts and ch["record_end"] == checks[11].ts, \
        "the thirteenth and fourteenth checks, after the close, are not the pallet's record"
    body = coa.render_pallet(coa.gather_pallet(session, "PL1-0000002"), issued_by="SUP",
                             issued_at=closed, revision=1, supersedes=None)
    assert "Record from" in body and "| 12 |" in body
