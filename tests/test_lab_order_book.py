"""The order book, and the supervisor who works it.

On 2026-09-18 a bottling lab plant that had been up for six hours reported
`WO-ACME-4711` at 285,881 good against an order for 4,000 - an over-run of
seventy times the order, on a line nothing had stopped. Decision 0029 was
working exactly as written: the MES does not finish an order at its quantity,
because reaching a number and being finished are different facts. What was
missing was the rest of a plant: a book with more than one order in it, and a
supervisor who finishes one and releases the next.

This file is about both halves.

**Nothing here listens on a port.** The floor talks to the plant over HTTP, so
the plant here is the real application answering over an in-process ASGI
transport: the same routers, the same database, the same sign-in, and nothing
for the operating system to leave running after the test. A plant on loopback
would satisfy the rule; no plant at all satisfies it better.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.api.app import create_app
from fsmes.api.deps import get_db, get_read_db
from fsmes.config import get_settings
from fsmes.db import Base
from fsmes.domain import AuditLog, OrderStatus, ProductionSource, WorkOrder
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import masterdata
from fsmes.services import auth, execution
from fsmes.sim.operations import Floor

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "labs" / "multiplant"
BOTTLING = PACKS / "bottling"

#: The stations of the bottling line, in the order its routing runs them.
STATIONS = ("LD01", "RD01", "WASH01", "QI01", "FILL01", "PAL01")

SUPERVISOR = "FLOOR-SUP"
SUPERVISOR_PASSWORD = "supervisor"


# ----------------------------------------------------------------- the plant


@pytest.fixture()
def plant():
    """One bottling plant, seeded from the shipped pack, in memory."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(BOTTLING / "tag_map.json")}
    masterdata.seed(session, BOTTLING / "masterdata", cycles)
    auth.ensure_builtin_roles(session)
    auth.create_user(session, code=SUPERVISOR, name="Simulated shift supervisor",
                     password=SUPERVISOR_PASSWORD, role="supervisor")
    session.commit()
    yield session
    session.close()
    engine.dispose()


def a_floor(session: Session) -> tuple[Floor, httpx.AsyncClient]:
    """The simulated supervisor, signed in to that plant over ASGI."""
    app = create_app()

    def _same_session():
        yield session
        session.flush()

    app.dependency_overrides[get_db] = _same_session
    app.dependency_overrides[get_read_db] = _same_session
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                              base_url="http://plant")
    return Floor(get_settings(), client, random.Random(7)), client


def as_the_supervisor(session: Session, work) -> None:
    """Sign the supervisor in and run one coroutine of theirs against the plant."""

    async def _go():
        floor, client = a_floor(session)
        async with client:
            await floor.sign_in(SUPERVISOR, SUPERVISOR_PASSWORD)
            await work(floor)

    asyncio.run(_go())


async def open_orders(floor: Floor) -> list[dict]:
    page = await floor.get("/workorders", status=["released", "running"], limit=500)
    return page["items"]


def the_line_makes(session: Session, units: float, *, at_the_last: float | None = None) -> None:
    """Book `units` at every station, as the OPC agent's counters would.

    `at_the_last` books a different figure at the palletiser, which is where a
    scripted over-run shows up: the order's good quantity is the last
    operation's, because that is what the order actually yielded.
    """
    for station in STATIONS:
        good = at_the_last if (at_the_last is not None and station == STATIONS[-1]) else units
        execution.report(session, equipment_code=station, good=good,
                         source=ProductionSource.OPC, actor="opc-agent")
    session.commit()


def order(session: Session, code: str) -> WorkOrder:
    return session.scalar(select(WorkOrder).where(WorkOrder.code == code))


# ------------------------------------------------------------- the book itself


@pytest.mark.parametrize("pack", ["bottling", "machining", "finewire"])
def test_every_shipped_lab_pack_plans_more_than_a_day_of_its_own_lines_work(pack):
    """A pack whose book is one order is a plant that runs one order for ever.

    The measure is the pack's own: the slowest station in its tag map sets the
    line's rated rate, and the book has to hold at least twenty-four hours of
    it. Nothing here is a guess about what the line does - a real line makes
    less than its rating, so a book sized this way lasts longer than a day,
    never less.
    """
    directory = PACKS / pack
    book = json.loads((directory / "masterdata" / "work_orders.json").read_text(encoding="utf-8"))
    ordered = sum(row["quantity"] for row in book)
    slowest = max(m.cycle_seconds for m in load_tag_map(directory / "tag_map.json"))
    an_hour = 3600.0 / slowest
    assert ordered / an_hour >= 24.0, (
        f"{pack}'s book is {ordered:,.0f} units, which its line makes in "
        f"{ordered / an_hour:.1f}h at its rated {an_hour:,.0f}/h")
    released = [row for row in book if row.get("release", True)]
    assert len(released) == 1, f"{pack} releases {len(released)} orders; a line runs one at a time"
    assert len(book) > 1, f"{pack} still ships a book of one order"


@pytest.mark.parametrize("pack", ["bottling", "machining", "finewire"])
def test_every_order_in_a_shipped_book_is_due_after_the_one_before_it(pack):
    directory = PACKS / pack
    book = json.loads((directory / "masterdata" / "work_orders.json").read_text(encoding="utf-8"))
    dues = [row.get("due_in_hours") for row in book]
    assert all(d is not None for d in dues), f"{pack} has an order with no due date"
    assert dues == sorted(dues) and len(set(dues)) == len(dues), (
        f"{pack}'s due dates are not a sequence: {dues}")


def test_a_pack_is_refused_when_an_order_is_due_at_a_moment_that_is_not_a_number(tmp_path):
    """`due_in_hours` is relative and positive, and the refusal says why."""
    (tmp_path / "materials.json").write_text(
        json.dumps([{"code": "FG", "name": "Thing"}]), encoding="utf-8")
    (tmp_path / "work_orders.json").write_text(
        json.dumps([{"code": "WO-1", "material": "FG", "quantity": 10,
                     "due_in_hours": "2026-09-18"}]), encoding="utf-8")
    problems = masterdata.problems(tmp_path)
    assert any("due_in_hours" in p and "positive number of hours" in p for p in problems), problems


# ------------------------------------------------- the floor working the book


def test_the_floor_finishes_an_order_at_its_quantity_and_releases_the_next(plant):
    """The whole change, in one run.

    The line makes exactly the 4,000 the released order asks for. The MES does
    not close it - that is decision 0029 and it is still true a line below.
    The simulated shift supervisor does, and then puts the next order in the
    book on the line so the counters have somewhere to go.
    """
    the_line_makes(plant, 4000)
    assert order(plant, "WO-ACME-4711").status is OrderStatus.RUNNING, (
        "the MES finished an order by itself; decision 0029 says it must not")

    async def work(floor: Floor) -> None:
        await floor.work_the_book(await open_orders(floor))

    as_the_supervisor(plant, work)

    finished = order(plant, "WO-ACME-4711")
    assert finished.status is OrderStatus.COMPLETED
    assert finished.good_qty == 4000
    assert finished.over_qty == 0, "the order ran past a quantity nobody asked it to"
    assert order(plant, "WO-ACME-4712").status is OrderStatus.RELEASED, (
        "the line has no order on it; the next count is unassigned production")
    still_planned = plant.scalars(
        select(WorkOrder).where(WorkOrder.status == OrderStatus.PLANNED)).all()
    assert len(still_planned) == 8, "the supervisor released more than the next order"


def test_the_audit_names_the_simulated_supervisor_and_not_the_mes(plant):
    """Who finished this order has to survive in the record.

    The whole defence of decision 0029 is that finishing an order is somebody's
    act. A simulated plant where the act is attributed to `system` would read,
    a month later, as the MES having closed the order itself.
    """
    the_line_makes(plant, 4000)

    async def work(floor: Floor) -> None:
        await floor.work_the_book(await open_orders(floor))

    as_the_supervisor(plant, work)

    rows = plant.scalars(
        select(AuditLog).where(AuditLog.entity_id == "WO-ACME-4711")).all()
    by_action = {r.action: r.actor for r in rows}
    assert by_action.get("workorder.completed") == SUPERVISOR, (
        f"the order was completed by {by_action.get('workorder.completed')!r}")
    steps = [r for r in rows if r.action == "operation.completed"]
    assert len(steps) == len(STATIONS), f"{len(steps)} of six steps were completed"
    assert {r.actor for r in steps} == {SUPERVISOR}

    released = {r.action: r.actor for r in plant.scalars(
        select(AuditLog).where(AuditLog.entity_id == "WO-ACME-4712")).all()}
    assert released.get("workorder.released") == SUPERVISOR, (
        f"the next order was released by {released.get('workorder.released')!r}")


def test_a_scripted_over_run_survives_the_supervisor_and_is_reported_in_full(plant):
    """An over-run that really happened is still the true figure afterwards.

    The line runs 120 past the order before anybody stops it. The supervisor
    finishing the order must not round that away: 4,120 made against 4,000
    ordered is an over-run of 120, on the completed order, in the completion
    the ERP is handed.
    """
    the_line_makes(plant, 4000, at_the_last=4120)

    async def work(floor: Floor) -> None:
        await floor.work_the_book(await open_orders(floor))

    as_the_supervisor(plant, work)

    finished = order(plant, "WO-ACME-4711")
    assert finished.status is OrderStatus.COMPLETED
    assert finished.good_qty == 4120
    assert finished.over_qty == 120


def test_the_floor_leaves_a_running_order_alone_until_the_line_has_made_the_number(plant):
    """Short of the quantity, nobody touches it. A supervisor who finished an
    order at 3,999 would be inventing a completion, which is the same class of
    fault as inventing production."""
    the_line_makes(plant, 3999)

    async def work(floor: Floor) -> None:
        await floor.work_the_book(await open_orders(floor))

    as_the_supervisor(plant, work)

    assert order(plant, "WO-ACME-4711").status is OrderStatus.RUNNING
    assert order(plant, "WO-ACME-4712").status is OrderStatus.PLANNED


def test_a_scripted_over_run_is_left_to_run_when_the_plan_says_nobody_stops_it(plant):
    """`[floor] finish_orders = false` - what `labs/experiments/over-run.toml`
    asks for. The line is 2,000 past its order and the supervisor walks by."""
    the_line_makes(plant, 6000)

    async def work(floor: Floor) -> None:
        await floor.work_the_book(await open_orders(floor), finish=False)

    as_the_supervisor(plant, work)

    running = order(plant, "WO-ACME-4711")
    assert running.status is OrderStatus.RUNNING
    assert running.over_qty == 2000
    assert order(plant, "WO-ACME-4712").status is OrderStatus.PLANNED


def test_when_the_book_runs_out_the_floor_says_so_once_and_invents_nothing(plant):
    """The behaviour this replaces made up an order code and a quantity.

    A plant with nothing planned left has nothing planned left. The floor says
    so - once, not every twenty seconds for the rest of the shift - and the
    number of orders in the plant does not change.
    """
    for row in plant.scalars(select(WorkOrder)).all():
        row.status = OrderStatus.CANCELLED
    plant.commit()
    before = plant.scalars(select(WorkOrder)).all()

    said: list[tuple] = []

    async def work(floor: Floor) -> None:
        await floor.release_next()
        await floor.release_next()
        await floor.release_next()

    import structlog

    with structlog.testing.capture_logs() as captured:
        as_the_supervisor(plant, work)
    said = [row for row in captured if row.get("event") == "the order book is empty"]

    assert len(said) == 1, f"the empty book was announced {len(said)} times"
    assert len(plant.scalars(select(WorkOrder)).all()) == len(before), (
        "the floor invented an order rather than say the book was empty")
