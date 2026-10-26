"""Where did this lot go, on a line that books by the lot and serialises nothing.

The trace page used to answer that question only in serial numbers, so on a
bottling line - which issues cap lots to orders and makes a finished lot per
order, as most lines do - it said "nothing serialised carries LOT-CAP-001"
while the orders that drew on the cap lot sat in the database. The lot now
answers in the orders that drew on it as well: where the lot went in, how
much, when, and the lots each order made. Packages or orders, whichever the
line records.

The join is the genealogy the plant already writes (issues against orders,
and the lot an order made), so it is read a statement per level: the count
of statements does not grow with the number of orders.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import event, insert, select

from fsmes.db import utcnow
from fsmes.domain import LotConsumption, Material, MaterialLot, OrderStatus, WorkOrder
from fsmes.services import execution, serialization, workorders


@contextmanager
def statements(session):
    """Count the statements the database is actually asked to run."""
    counted: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        counted.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield counted
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _a_finished_order_that_drew_on_the_sugar(session, code="WO-LOT-1"):
    """Two issues at the mixer, one with no station, and a finished lot made."""
    order = workorders.create(session, code=code, material_code="FG-COLA", quantity=100)
    workorders.release(session, code)
    execution.consume(session, order_code=code, lot_code="LOT-SUGAR-001", quantity=20, seq=10)
    execution.consume(session, order_code=code, lot_code="LOT-SUGAR-001", quantity=5, seq=10)
    execution.consume(session, order_code=code, lot_code="LOT-SUGAR-001", quantity=1.5)
    execution.create_lot(session, code=f"{code}-FG", material_code="FG-COLA", quantity=96,
                         produced_by_order=order)
    session.flush()
    return order


def test_a_lot_no_unit_carries_still_names_the_orders_that_drew_on_it_and_what_they_made(session):
    _a_finished_order_that_drew_on_the_sugar(session)

    found = serialization.where_used(session, "LOT-SUGAR-001")

    assert found["units_affected"] == 0 and found["packages_to_hold"] == []
    assert found["orders_total"] == 1 and found["unit"] == "kg"
    (order,) = found["orders"]
    assert order["order"] == "WO-LOT-1" and order["material"] == "FG-COLA"
    assert order["status"] == "released"
    assert order["quantity"] == 26.5 and order["issues"] == 3
    assert order["first_drawn"] <= order["last_drawn"]
    assert order["lots_made"] == [{"lot": "WO-LOT-1-FG", "material": "FG-COLA", "quantity": 96}]
    assert order["lots_made_total"] == 1


def test_the_station_is_the_one_the_issue_recorded_and_unrecorded_is_said_not_guessed(session):
    _a_finished_order_that_drew_on_the_sugar(session)

    (order,) = serialization.where_used(session, "LOT-SUGAR-001")["orders"]

    assert order["stations"] == [
        {"station": "MIX01", "operation": "Mix", "seq": 10, "quantity": 25.0, "issues": 2},
        {"station": None, "operation": None, "seq": None, "quantity": 1.5, "issues": 1},
    ]


def test_the_verdict_keeps_saying_nothing_serialised_carries_it_and_adds_the_orders(session):
    """The recorded clip waits for "carries LOT-…" in the verdict; it still
    appears, followed by the orders."""
    _a_finished_order_that_drew_on_the_sugar(session)

    verdict = serialization.where_used(session, "LOT-SUGAR-001")["verdict"]

    assert verdict == "nothing serialised carries LOT-SUGAR-001; 1 order(s) drew on it and made 1 lot(s)"


def test_an_order_still_running_has_drawn_on_the_lot_and_made_nothing_yet(session):
    _a_finished_order_that_drew_on_the_sugar(session, "WO-LOT-1")
    workorders.create(session, code="WO-LOT-2", material_code="FG-COLA", quantity=100)
    workorders.release(session, "WO-LOT-2")
    execution.consume(session, order_code="WO-LOT-2", lot_code="LOT-SUGAR-001", quantity=4, seq=10)
    session.flush()

    found = serialization.where_used(session, "LOT-SUGAR-001")

    assert [o["order"] for o in found["orders"]] == ["WO-LOT-1", "WO-LOT-2"], "in the order they first drew on it"
    assert found["orders_total"] == 2
    assert found["orders"][1]["lots_made"] == [] and found["orders"][1]["lots_made_total"] == 0


def test_a_lot_nobody_drew_on_answers_with_no_orders(session):
    found = serialization.where_used(session, "LOT-SUGAR-001")
    assert found["orders"] == [] and found["orders_total"] == 0
    assert found["verdict"] == "nothing serialised carries LOT-SUGAR-001"


def test_the_api_answers_the_same_orders_as_the_service(client, session):
    _a_finished_order_that_drew_on_the_sugar(session)

    body = client.get("/trace/where-used/LOT-SUGAR-001").json()

    assert body["orders_total"] == 1
    assert body["orders"][0]["order"] == "WO-LOT-1"
    assert body["orders"][0]["lots_made"][0]["lot"] == "WO-LOT-1-FG"
    assert body["orders"][0]["stations"][0]["station"] == "MIX01"


def _orders_drawing_on_one_lot(session, lot: MaterialLot, first: int, count: int) -> None:
    """`count` orders, each with three issues at the mixer and a lot made -
    written in bulk, because the test is about the read."""
    cola = session.scalar(select(Material).where(Material.code == "FG-COLA"))
    session.execute(insert(WorkOrder), [
        {"code": f"WO-BIG-{n:04d}", "material_id": cola.id, "quantity": 100.0,
         "status": OrderStatus.COMPLETED} for n in range(first, first + count)])
    ids = dict(session.execute(select(WorkOrder.code, WorkOrder.id)
                               .where(WorkOrder.code.like("WO-BIG-%"))).all())
    stamp = utcnow()
    session.execute(insert(LotConsumption), [
        {"work_order_id": ids[f"WO-BIG-{n:04d}"], "lot_id": lot.id, "quantity": 0.5,
         "ts": stamp + timedelta(seconds=n * 10 + issue)}
        for n in range(first, first + count) for issue in range(3)])
    session.execute(insert(MaterialLot), [
        {"code": f"WO-BIG-{n:04d}-FG", "material_id": cola.id, "quantity": 100.0, "original_quantity": 100.0,
         "produced_by_order_id": ids[f"WO-BIG-{n:04d}"]} for n in range(first, first + count)])
    session.flush()


def test_three_hundred_orders_on_one_lot_take_as_many_statements_as_thirty(session):
    lot = session.scalar(select(MaterialLot).where(MaterialLot.code == "LOT-SUGAR-001"))
    _orders_drawing_on_one_lot(session, lot, 0, 30)
    serialization.where_used(session, "LOT-SUGAR-001")   # plant settings are read once and cached
    session.expire_all()
    with statements(session) as few:
        thirty = serialization.where_used(session, "LOT-SUGAR-001")

    _orders_drawing_on_one_lot(session, lot, 30, 270)
    session.expire_all()
    with statements(session) as many:
        three_hundred = serialization.where_used(session, "LOT-SUGAR-001")

    assert thirty["orders_total"] == 30 and three_hundred["orders_total"] == 300
    assert all(o["issues"] == 3 and o["lots_made_total"] == 1 for o in three_hundred["orders"])
    print(f"statements: {len(few)} for 30 orders, {len(many)} for 300")
    assert len(many) == len(few), "a statement per level, never one per order"
