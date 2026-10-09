"""What a maintenance job needs of the line: a stop, and a window.

Two columns, and between them the difference between a plant and a list. An
order sitting at `assigned` eight hours after it was raised is either a job
nobody could get at or a job nobody did, and a reader with only the status
cannot tell which - so the plan says whether the machine has to be stopped
for the job and when the job may be done, the order copies both when it is
raised, and every screen and every scorer reads them from the order.

The other half of this file is who may record the work. A plant whose
mechanics do not each have a login still has to have its records name the
mechanic, and `whose_work` is the rule that allows exactly that and nothing
more: you may book Mary's job as Mary's; you may not book it as your own.
"""

from datetime import datetime, time, timedelta

import pytest

from fsmes.domain import MaintenanceStatus, MaintenanceWindow
from fsmes.services import Forbidden, Invalid, calendar, dispatch, maintenance, masterdata

#: A Thursday inside the day shift, on the plant's own clock - naive UTC, the
#: one timestamp convention this product has.
NOON = datetime(2026, 10, 8, 12, 0)


@pytest.fixture()
def plant(session):
    """A plant with a day shift, two trades and two people on it."""
    calendar.create_pattern(session, code="DAY", name="Day shift",
                            starts=time(0, 0), ends=time(23, 59), days="1111111")
    for code, name in (("MECH", "Mechanic"), ("GEN", "General maintenance")):
        dispatch.create_skill(session, code=code, name=name)
    for code, name, skill in (("MARY", "Mary Shaw", "MECH"), ("JOE", "Joe Bray", "GEN")):
        masterdata.create_person(session, code=code, name=name, role="operator")
        dispatch.grant_skill(session, person_code=code, skill_code=skill)
        dispatch.set_roster(session, person_code=code, shift_code="DAY")
    session.flush()
    return session


def a_plan(session, **over):
    settings = {"code": "PM-1", "name": "Clean the condenser",
                "equipment_code": "MIX01", "trigger": "calendar_days",
                "interval": 30.0, "expected_minutes": 90.0}
    return maintenance.create_plan(session, **{**settings, **over})


# ------------------------------------------------- what the plan says, and why


def test_a_plan_says_whether_the_line_must_stop_and_when_the_job_may_be_done(plant):
    plan = a_plan(plant, needs_stop=True, window="between_orders")

    assert plan.needs_stop is True
    assert plan.window is MaintenanceWindow.BETWEEN_ORDERS
    row = maintenance.status_of(plant, plan)
    assert row["needs_stop"] is True and row["window"] == "between_orders"


def test_a_plan_that_says_nothing_needs_no_stop_and_names_no_window(plant):
    """Every plan written before 2026-10-09, and every database that opens
    after this migration. A job that has not said it needs the machine
    stopped does not get to stop it, and a window nobody has stated is read
    as open rather than invented."""
    plan = a_plan(plant)

    assert plan.needs_stop is False
    assert plan.window is None
    assert maintenance.status_of(plant, plan)["window"] is None


def test_a_window_this_product_does_not_know_is_refused_rather_than_stored(plant):
    with pytest.raises(Invalid) as refused:
        a_plan(plant, window="when-the-boss-says")
    assert "unknown window" in str(refused.value)
    for word in ("anytime", "between_orders", "end_of_shift"):
        assert word in str(refused.value)


def test_a_field_nobody_filled_in_is_not_an_opinion_about_when(plant):
    """The pack reader and a form both hand over "" for a field nobody
    filled in, and refusing that would make "I have no opinion about when"
    impossible to express through either."""
    assert a_plan(plant, window="").window is None


def test_an_order_carries_what_the_job_needs_without_the_plan_beside_it(plant):
    """An order is judged on what was asked for when it was raised. The plan
    may be re-written tomorrow; this order is a record of today."""
    a_plan(plant, needs_stop=True, window="end_of_shift")

    raised = maintenance.raise_due(plant)

    assert len(raised) == 1
    assert raised[0].needs_stop is True
    assert raised[0].window is MaintenanceWindow.END_OF_SHIFT


def test_work_raised_because_something_broke_can_say_it_needs_the_line_stopped(plant):
    order = maintenance.raise_corrective(
        plant, equipment_code="MIX01", summary="bearing is screaming",
        needs_stop=True, window="anytime")

    assert order.needs_stop is True
    assert order.window is MaintenanceWindow.ANYTIME


# ----------------------------------------------------- whose work it is to book


def _assigned(session, person="MARY", **over):
    a_plan(session, **over)
    order = maintenance.raise_due(session)[0]
    dispatch.assign(session, order.code, person, actor="SUP")
    return order


def test_the_person_the_job_was_given_to_can_start_it(plant):
    order = _assigned(plant)

    maintenance.start(plant, order.code, actor="MARY")

    assert order.status is MaintenanceStatus.IN_PROGRESS
    assert order.performed_by == "MARY"


def test_anybody_may_book_marys_job_as_marys(plant):
    """The shop-floor terminal beside the machine, the supervisor booking his
    crew's work at the end of the shift, and the simulated crew's one
    account: the only way a plant whose mechanics have no logins can have its
    records name the mechanic."""
    order = _assigned(plant)

    maintenance.start(plant, order.code, actor="TERMINAL", performed_by="MARY",
                      capabilities={"maintenance.perform"})

    assert order.performed_by == "MARY"


def test_nobody_may_book_marys_job_as_their_own(plant):
    order = _assigned(plant)

    with pytest.raises(Forbidden) as refused:
        maintenance.start(plant, order.code, actor="JOE",
                          capabilities={"maintenance.perform"})

    assert "assigned to MARY" in str(refused.value)
    assert order.status is MaintenanceStatus.ASSIGNED


def test_nobody_may_book_marys_job_as_a_third_persons(plant):
    order = _assigned(plant)

    with pytest.raises(Forbidden):
        maintenance.start(plant, order.code, actor="TERMINAL", performed_by="JOE",
                          capabilities={"maintenance.perform"})


def test_a_supervisor_who_can_reassign_the_work_is_not_made_to_do_it_in_two_steps(plant):
    order = _assigned(plant)

    maintenance.start(plant, order.code, actor="SUP",
                      capabilities={"maintenance.plan", "maintenance.perform"})

    assert order.status is MaintenanceStatus.IN_PROGRESS


def test_a_name_that_is_nobody_is_refused_rather_than_shown_on_the_board(plant):
    order = _assigned(plant)

    with pytest.raises(Exception) as refused:
        maintenance.start(plant, order.code, actor="TERMINAL", performed_by="NOBODY",
                          capabilities={"maintenance.perform"})

    assert "NOBODY" in str(refused.value)


# ------------------------------------------------- the downtime a job books


def test_the_downtime_a_job_books_is_what_the_caller_measured_and_not_how_long_it_took(plant):
    """Greasing a bearing on a running palletiser stops nothing, and booking
    the job's twenty minutes against the line would invent downtime the plant
    never had. A caller that measured it says so."""
    order = _assigned(plant)
    maintenance.start(plant, order.code, actor="MARY")
    order.started_at = order.started_at - timedelta(minutes=40)

    maintenance.complete(plant, order.code, findings="all eight points took grease",
                         downtime_minutes=0.0, performed_by="MARY",
                         capabilities={"maintenance.perform"}, actor="TERMINAL")

    assert order.status is MaintenanceStatus.DONE
    assert order.downtime_minutes == 0.0
    assert order.findings == "all eight points took grease"
    assert order.performed_by == "MARY"


def test_a_caller_that_measured_nothing_still_books_the_time_the_job_took(plant):
    """What every release before the column did, left exactly as it was: a
    plant whose screens do not measure downtime keeps the figure it had."""
    order = _assigned(plant)
    maintenance.start(plant, order.code, actor="MARY")
    order.started_at = order.started_at - timedelta(minutes=40)

    maintenance.complete(plant, order.code, actor="MARY")

    assert order.downtime_minutes == pytest.approx(40.0, abs=1.0)


# ------------------------------------------------- what the roster now shows


def test_the_roster_says_which_job_is_in_somebodys_hands_and_what_it_needs(plant):
    """Handoff 3 draws this. The plan as well as the order, because a reader
    who wants to know what the mechanic is actually doing to the machine has
    to be able to get from the person to it in one step."""
    order = _assigned(plant, needs_stop=True, window="between_orders")
    order.assigned_at = NOON - timedelta(minutes=190)

    rows = {p["person"]: p for p in dispatch.roster(plant, now=NOON)["people"]}

    mary = rows["MARY"]["on_now"]
    assert mary["order"] == order.code
    assert mary["plan"] == "PM-1"
    assert mary["equipment"] == "MIX01"
    assert mary["status"] == "assigned"
    assert mary["minutes"] == pytest.approx(190.0, abs=1.0)
    assert mary["expected_minutes"] == 90.0
    assert mary["needs_stop"] is True
    assert mary["window"] == "between_orders"
    assert rows["JOE"]["on_now"] is None
