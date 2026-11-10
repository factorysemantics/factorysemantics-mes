"""A booking says what it moved: the step's count, and the order's.

On 2026-11-08 the pretend operator booked 120 good and 3 scrap at FILL01 on
WO-ACME-4711 while the line's own counters were running. Scrap went up by 3;
Good never jumped by 120, and it could not tell whether its booking had gone
in. It had. The order's Good is finished output - the good count of the
order's *last* step - and the filler is step 50 of six. The 120 went onto the
filler's step, where it belongs, and nothing said so.

These tests pin both halves: the numbers are counted the way the plant
counts them (a booking at the last step moves the order's Good by exactly
what was booked, live counts or not; a booking anywhere else moves that
step and not the order), and the reply to a booking carries both totals and
names the step the order's Good is counted from, so a screen can say it.
"""

from fsmes.domain.execution import ProductionSource
from fsmes.services import execution


def _a_running_order_with_live_counts(client, session, code="WO-BOOK"):
    """Two steps (MIX01 op 10, PACK01 op 20), both started, both already
    counting by themselves the way a machine's counter books its deltas."""
    assert client.post("/workorders", json={"code": code, "material": "FG-COLA", "quantity": 400}).status_code == 201
    assert client.post(f"/workorders/{code}/release").status_code == 200
    for seq in (10, 20):
        assert client.post(f"/workorders/{code}/operations/{seq}/start").status_code == 200
    execution.report(session, order_code=code, seq=10, good=102, scrap=2, source=ProductionSource.OPC)
    execution.report(session, order_code=code, seq=20, good=95, scrap=1, source=ProductionSource.OPC)
    session.flush()


def test_a_booking_at_the_last_step_raises_the_orders_good_by_exactly_what_was_booked(client, session):
    _a_running_order_with_live_counts(client, session)
    before = client.get("/workorders/WO-BOOK").json()

    reply = client.post("/execution/report", json={"order": "WO-BOOK", "seq": 20, "good": 120, "scrap": 3})
    assert reply.status_code == 200, reply.text
    after = client.get("/workorders/WO-BOOK").json()

    assert after["good_qty"] - before["good_qty"] == 120
    assert after["scrap_qty"] - before["scrap_qty"] == 3
    booked = reply.json()
    assert booked["order_good_qty"] == after["good_qty"] == 215
    assert booked["seq"] == booked["final_seq"] == 20


def test_a_booking_at_an_earlier_step_moves_that_step_and_says_the_orders_good_did_not_move(client, session):
    _a_running_order_with_live_counts(client, session)

    reply = client.post("/execution/report", json={"order": "WO-BOOK", "seq": 10, "good": 120, "scrap": 3})
    assert reply.status_code == 200, reply.text
    booked = reply.json()

    # The step took all 120 and all 3, on top of what its counter had booked.
    assert (booked["seq"], booked["good_qty"], booked["scrap_qty"]) == (10, 222, 5)
    assert (booked["booked_good"], booked["booked_scrap"]) == (120, 3)
    # The order's Good is the last step's: unchanged at 95. Its scrap counts
    # at every step: 2 + 1 + 3.
    assert (booked["order_good_qty"], booked["order_scrap_qty"]) == (95, 6)
    # And the reply names the step the order's Good is counted from, so the
    # screen can tell the operator where the 120 will show up.
    assert (booked["final_seq"], booked["final_operation"]) == (20, booked_name(client, 20))
    assert booked["operation"] == booked_name(client, 10)


def booked_name(client, seq):
    order = client.get("/workorders/WO-BOOK").json()
    return next(op["name"] for op in order["operations"] if op["seq"] == seq)
