"""The hold state.

Scott's call on the order-lifecycle question: a concern on an order wants a
HOLD, not a euphemism. These pin what holding actually means - nothing books,
nothing schedules, and resuming lands where the order truly was.
"""

import pytest

from fsmes.services import Conflict, Invalid, execution, scheduling, workorders


@pytest.fixture()
def order(session):
    workorders.create(session, code="WO-H1", material_code="FG-COLA", quantity=10,
                      actor="test")
    workorders.release(session, "WO-H1", actor="test")
    session.flush()
    return "WO-H1"


def test_a_hold_needs_a_reason(session, order):
    """A hold with no reason is indistinguishable from an accident."""
    with pytest.raises(Invalid, match="needs a reason"):
        workorders.hold(session, order, "  ", actor="SUP")


def test_nothing_books_against_a_held_order_even_by_name(session, order):
    """The machine path filters holds out in SQL; naming the order must not
    be a way around the hold."""
    workorders.hold(session, order, "suspect raw material", actor="SUP")
    with pytest.raises(Conflict, match="on hold"):
        execution.report(session, order_code=order, seq=10, good=5, actor="test")


def test_material_cannot_be_issued_to_a_held_order(session, order):
    execution.create_lot(session, code="LOT-H1", material_code="RAW-SUGAR",
                         quantity=100, actor="test")
    workorders.hold(session, order, "quality concern", actor="SUP")
    session.flush()
    with pytest.raises(Conflict):
        execution.consume(session, order_code=order, lot_code="LOT-H1",
                          quantity=1, actor="test")


def test_the_scheduler_refuses_a_held_order(session, order):
    """A plan that includes it would promise dates the hold exists to
    suspend."""
    workorders.hold(session, order, "engineering query", actor="SUP")
    with pytest.raises(Invalid, match="on hold"):
        scheduling.plan_order(session, order)


def test_plan_all_skips_held_orders(session, order):
    workorders.hold(session, order, "engineering query", actor="SUP")
    session.flush()
    assert all(p["order"] != order for p in scheduling.plan_all(session))


def test_an_untouched_order_resumes_to_released(session, order):
    workorders.hold(session, order, "await customer", actor="SUP")
    resumed = workorders.resume(session, order, actor="SUP")
    assert resumed.status.value == "released"


def test_a_started_order_resumes_to_running(session, order):
    """Resuming to the wrong place would either claim progress that never
    happened or erase progress that did."""
    workorders.start_operation(session, order, 10, actor="test")
    workorders.hold(session, order, "machine fault", actor="SUP")
    resumed = workorders.resume(session, order, actor="SUP")
    assert resumed.status.value == "running"


def test_resume_only_works_on_a_held_order(session, order):
    with pytest.raises(Conflict, match="not on hold"):
        workorders.resume(session, order, actor="SUP")


def test_a_held_order_can_be_cancelled_but_not_completed(session, order):
    workorders.hold(session, order, "obsolete", actor="SUP")
    with pytest.raises(Conflict):
        workorders.close(session, order, actor="SUP")
    workorders.cancel(session, order, actor="SUP")
    assert workorders.get(session, order).status.value == "cancelled"


def test_hold_and_resume_over_the_api_need_orders_close(sign_in, session, order):
    operator = sign_in("SCOTT", "operator")
    assert operator.post(f"/workorders/{order}/hold",
                         json={"reason": "x"}).status_code == 403
    supervisor = sign_in("SUPH", role="supervisor")
    assert supervisor.post(f"/workorders/{order}/hold",
                           json={"reason": "suspect lot"}).status_code == 200
    assert supervisor.post(f"/workorders/{order}/resume").status_code == 200
