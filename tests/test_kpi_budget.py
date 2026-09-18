"""The floor screen and a metrics scrape answer a shift of history in time.

WHY THERE IS A CLOCK IN THE SUITE. On 2026-09-18 Scott could not sign in to
his own lab plants. `/dashboard/summary` on a six-machine plant with a few
hours of one-second history was taking **ten seconds**, the simulated shop
floor asks for it about once a second, and everything else - his browser, his
sign-in - queued behind it. Nothing in the suite would have noticed: every
test plant is a handful of rows, and a query whose cost grows with bookings
times history looks free on a handful of rows.

So this file builds a plant of the shape that broke - six machines, eight
hours, state and tag history at one second and a booking every second and a
half - and times the two endpoints a plant is watched through. It is a
regression gate, not a benchmark: it is here to catch a query that has gone
back to reading the whole history to answer a question about the last shift,
and that kind of regression is not 20 % slow, it is forty times slow.

The generated plant is sized from the real one. Scott's bottling plant at
04:26 on 2026-09-18 held 12,168 state intervals, 92,707 bookings and 278,033
tag rows for six machines; this builds 11,520, 92,160 and 172,800 for six.
"""

import time
from datetime import timedelta

import pytest
from sqlalchemy import insert, select

from fsmes.api.routers import dashboard
from fsmes.db import utcnow
from fsmes.domain import (
    Equipment,
    EquipmentLevel,
    EquipmentState,
    EquipmentStateName,
    ProductionLog,
    TagValue,
)

#: The plant that broke, rebuilt: six machines and one shift.
MACHINES = 6
WINDOW_HOURS = 8
#: A state change every fifteen seconds, four fifths of it running.
STATE_SECONDS = 15
#: A booking every second and a half while the machine runs.
BOOKING_SECONDS = 1.5
#: One tag row a second per machine, alternating the machine's process value
#: with a structural tag, because the machine card's "latest reading" query
#: has to pick the process value out of the history and that is the search
#: that used to read the whole of it.
TAG_SECONDS = 1

#: THE BUDGET, AND WHERE THE NUMBERS COME FROM.
#:
#: Measured on the machine the fix was written on - an Arch Linux desktop,
#: CPython 3.14 - this plant's `/dashboard/summary` answers in **0.069 s** on
#: the suite's in-memory SQLite and **0.20 s** against a file-backed copy of
#: Scott's real bottling plant; `/metrics` in 0.014 s. The same summary on the
#: same data before this branch took **8.0 s**.
#:
#: Two numbers because a plant on SQLite and a plant on PostgreSQL are not the
#: same deployment. SQLite is the one that broke and the one a small plant
#: runs, and 300 ms is what a floor screen refreshing every two seconds should
#: be held to - four times the measured figure, which leaves room for a CI
#: runner slower than a desk and not for a query that has gone quadratic
#: again. PostgreSQL pays per-row overhead SQLite does not for the correlated
#: lookup in `production_sums`, and the suite reaches it over a socket, so its
#: gate is looser at 1.5 s; it is still a twentieth of the regression this
#: file exists to catch.
#:
#: A gate that goes red on a busy runner teaches people to re-run it until it
#: passes, which is worse than no gate at all. If either number turns out to
#: flake, raise it and say so here - do not delete the test.
BUDGET_SECONDS = {"sqlite": 0.3, "postgresql": 1.5}
#: Any other dialect: the suite has never been run on one, so it gets the
#: looser of the two rather than a number nobody has measured.
DEFAULT_BUDGET_SECONDS = 1.5


def budget(session) -> float:
    return BUDGET_SECONDS.get(session.get_bind().dialect.name, DEFAULT_BUDGET_SECONDS)


def _history(session, machines, end):
    """Eight hours of one-second plant, written straight to the tables.

    Bulk inserts rather than the services, because this is about what the
    reads cost on a shift of rows and not about how the rows got there.
    """
    start = end - timedelta(hours=WINDOW_HOURS)
    states, bookings, tags = [], [], []
    for machine in machines:
        at = start
        step = 0
        while at < end:
            # Four running intervals to one stopped one, which is roughly the
            # lab plant's mix and keeps availability off both rails.
            running = step % 5 != 4
            until = min(at + timedelta(seconds=STATE_SECONDS), end)
            states.append({
                "equipment_id": machine.id,
                "state": EquipmentStateName.RUNNING if running else EquipmentStateName.DOWN,
                "reason": None if running else "jam",
                "started_at": at,
                # The last interval stays open, which is what a live plant
                # looks like and what `last_record` has to cope with.
                "ended_at": None if until >= end else until,
            })
            if running:
                booked = at
                while booked < until:
                    bookings.append({"equipment_id": machine.id, "good_qty": 1.0,
                                     "scrap_qty": 0.0, "ts": booked})
                    booked += timedelta(seconds=BOOKING_SECONDS)
            at = until
            step += 1
        at = start
        flip = 0
        while at < end:
            structural = flip % 2 == 0
            tags.append({
                "equipment_id": machine.id,
                "tag": f"{machine.code}.{'Running' if structural else 'FillWeight'}",
                "ts": at,
                "value_num": 1.0 if structural else 500.0 + flip % 9,
                "value_text": None,
            })
            at += timedelta(seconds=TAG_SECONDS)
            flip += 1
    for model, rows in ((EquipmentState, states), (ProductionLog, bookings), (TagValue, tags)):
        for chunk in range(0, len(rows), 5000):
            session.execute(insert(model), rows[chunk:chunk + 5000])
    session.commit()
    return len(states), len(bookings), len(tags)


@pytest.fixture()
def a_shift_of_history(session):
    """Six machines with a shift of one-second history behind them."""
    centre = session.scalars(
        select(Equipment).where(Equipment.level == EquipmentLevel.WORK_CENTER)).first()
    machines = [
        Equipment(code=f"BUD{n:02d}", name=f"Budget {n}", level=EquipmentLevel.WORK_UNIT,
                  parent_id=centre.id if centre else None, ideal_cycle_seconds=1.5)
        for n in range(1, MACHINES + 1)
    ]
    session.add_all(machines)
    session.flush()
    counts = _history(session, machines, utcnow())
    return counts


def _fastest(call, attempts=3):
    """The best of a few goes.

    Best rather than mean: a runner that was descheduled halfway through one
    attempt has said nothing about the query, and the regression this guards
    against is forty times over the line in every attempt.
    """
    best = None
    for _ in range(attempts):
        dashboard._cache.clear()  # the answer is cached for a second; time the work
        began = time.perf_counter()
        response = call()
        took = time.perf_counter() - began
        assert response.status_code == 200, response.text
        best = took if best is None else min(best, took)
    return best


def test_the_floor_screen_answers_a_shift_of_history_inside_its_budget(
        client, session, a_shift_of_history):
    states, bookings, tags = a_shift_of_history
    assert (states, bookings) > (10_000, 90_000), "the plant under test is not the shape that broke"
    allowed = budget(session)
    took = _fastest(lambda: client.get("/dashboard/summary"))
    assert took < allowed, (
        f"/dashboard/summary took {took:.2f}s over {states} state intervals, {bookings} "
        f"bookings and {tags} tag rows; the budget is {allowed}s")


def test_the_floor_screen_still_counts_the_whole_plant_at_that_size(client, a_shift_of_history):
    """Fast and right: the budget above means nothing if the payload thinned."""
    body = client.get("/dashboard/summary").json()
    assert body["plant"]["machines_total"] == body["machines_page"]["scope_total"]
    budgeted = [m for m in body["machines"] if m["code"].startswith("BUD")]
    assert len(budgeted) == MACHINES
    for machine in budgeted:
        ledger = machine["oee"]["ledger"]
        assert ledger["tiles_exactly"], f"{machine['code']}'s window does not add up"
        assert machine["oee"]["counted_outside_run_time"] is not None
        assert machine["analog"]["name"] == "FillWeight"


def test_a_metrics_scrape_answers_a_shift_of_history_inside_its_budget(
        client, session, a_shift_of_history):
    allowed = budget(session)
    took = _fastest(lambda: client.get("/metrics"))
    assert took < allowed, f"/metrics took {took:.2f}s; the budget is {allowed}s"
    assert "mes_equipment_coverage" in client.get("/metrics").text
