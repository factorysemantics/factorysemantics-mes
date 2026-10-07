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
from sqlalchemy import select
from sqlalchemy.orm import Session

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.api.app import create_app
from fsmes.config import get_settings
from fsmes.db import Base
from fsmes.domain import AuditLog, OrderStatus, ProductionSource, WorkOrder
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import masterdata
from fsmes.services import auth, execution
from fsmes.sim.operations import Floor, keep_planned

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "labs" / "multiplant"
BOTTLING = PACKS / "bottling"

#: The stations of the bottling line, in the order its routing runs them.
STATIONS = ("LD01", "RD01", "WASH01", "QI01", "FILL01", "PAL01")

SUPERVISOR = "FLOOR-SUP"
SUPERVISOR_PASSWORD = "supervisor"
PLANNER = "FLOOR-PLAN"
PLANNER_PASSWORD = "planner"


# ----------------------------------------------------------------- the plant


@pytest.fixture()
def plant(tmp_path, monkeypatch):
    """One bottling plant, seeded from the shipped pack, in a file of its own.

    A **file**, and this plant's own `MES_DATABASE_URL`, rather than the
    suite's usual in-memory database behind a dependency override. The floor
    is an HTTP client: its requests are solved in a worker thread and some of
    what they touch opens its own unit of work, so a test that overrode only
    the dependencies would be half on this plant and half on whatever database
    the process last had - which is exactly the kind of state that made a run
    pass here and fail on a runner. Everything this test builds lives in
    `tmp_path` and the environment is put back afterwards.
    """
    from fsmes.config import get_settings as settings_cache
    from fsmes.db import get_engine, get_sessionmaker

    url = f"sqlite:///{(tmp_path / 'plant.db').as_posix()}"
    monkeypatch.setenv("MES_DATABASE_URL", url)
    monkeypatch.setenv("MES_PLANT_TIMEZONE", "UTC")
    # Nothing here tests retention, and the hourly pruner opens its own
    # session in a worker thread against this file.
    monkeypatch.setenv("MES_TAG_RETENTION_DAYS", "0")
    # `fsmes pack apply` updates `os.environ` on purpose, so any test in this
    # suite that applies a pack leaves that plant's modules and vocabulary in
    # the process for whatever runs next. This plant states its own rather
    # than inherit half of somebody else's.
    for leaked in ("MES_MODULES", "MES_WORDS", "MES_PLANT_NAME", "MES_PLANT_LABEL",
                   "MES_REPLAY_DIR", "MES_SIM_SPEED", "MES_TAG_MAP_FILE"):
        monkeypatch.delenv(leaked, raising=False)
    for cache in (settings_cache, get_engine, get_sessionmaker):
        cache.cache_clear()

    engine = get_engine()
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(BOTTLING / "tag_map.json")}
    masterdata.seed(session, BOTTLING / "masterdata", cycles)
    auth.ensure_builtin_roles(session)
    auth.create_user(session, code=SUPERVISOR, name="Simulated shift supervisor",
                     password=SUPERVISOR_PASSWORD, role="supervisor")
    auth.create_user(session, code=PLANNER, name="Simulated production planner",
                     password=PLANNER_PASSWORD, role="planner")
    session.commit()

    yield session

    session.close()
    engine.dispose()
    for cache in (settings_cache, get_engine, get_sessionmaker):
        cache.cache_clear()


def as_the_supervisor(session: Session, work) -> None:
    """Sign the supervisor in and run one coroutine of theirs against the plant.

    The plant answers over an in-process ASGI transport: the real application,
    the real routers, the real sign-in, and nothing listening on a port for the
    test to leave behind.
    """
    signed_in_as(session, SUPERVISOR, SUPERVISOR_PASSWORD, work)


def as_the_planner(session: Session, work) -> None:
    """The same, as `FLOOR-PLAN`.

    A separate sign-in and not a flag, because the point of the planner is
    that it is a different person holding a different bundle: it may put an
    order in the book and it may not release one, and a test that ran its work
    on the supervisor's token would prove nothing about either.
    """
    signed_in_as(session, PLANNER, PLANNER_PASSWORD, work)


def signed_in_as(session: Session, code: str, password: str, work) -> None:

    async def _go():
        floor = Floor(get_settings(), client, random.Random(7))
        await floor.sign_in(code, password)
        await work(floor)

    session.commit()
    app = create_app()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://plant")

    async def _run():
        async with client:
            await _go()

    asyncio.run(_run())
    # The plant moved underneath this session; read it again rather than from
    # the identity map.
    session.expire_all()


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


# ----------------------------------------------------------- the planner


def every_order_but(session: Session, keep_planned: int) -> None:
    """Run the pack's book down to `keep_planned` planned orders.

    Which is where every lab plant ends up: bottling's ten orders were all
    finished by 2026-10-07 04:21 UTC, forty-three hours after it was built.
    The finished ones stay in the book, because what a plant has made is
    still the pattern of what it makes.
    """
    planned = [row for row in session.scalars(select(WorkOrder)).all()
               if row.status is OrderStatus.PLANNED]
    for row in sorted(planned, key=lambda r: r.code)[:len(planned) - keep_planned]:
        row.status = OrderStatus.COMPLETED
    session.commit()


def planned_codes(session: Session) -> list[str]:
    return sorted(row.code for row in session.scalars(select(WorkOrder)).all()
                  if row.status is OrderStatus.PLANNED)


def test_the_planner_plans_nothing_when_the_pack_has_not_asked_for_one(plant):
    """Zero is the default, and zero means the plant behaves exactly as before.

    The scripted over-run experiment depends on this: a plant whose book is
    meant to run out must be allowed to run out.
    """
    assert keep_planned({}) == 0, "a pack that says nothing got a planner"
    assert keep_planned({"planning": {}}) == 0
    every_order_but(plant, 0)
    before = {row.code for row in plant.scalars(select(WorkOrder)).all()}

    async def work(floor: Floor) -> None:
        assert await floor.plan_the_book(0) == []

    as_the_planner(plant, work)

    assert {row.code for row in plant.scalars(select(WorkOrder)).all()} == before


def test_the_planner_keeps_the_book_as_deep_as_the_pack_asked(plant):
    """Three planned orders behind the line, which is what bottling's pack says."""
    assert keep_planned(json.loads(
        (BOTTLING / "floor.json").read_text(encoding="utf-8"))) == 3
    every_order_but(plant, 0)
    assert planned_codes(plant) == []

    async def work(floor: Floor) -> None:
        await floor.plan_the_book(3)

    as_the_planner(plant, work)

    assert planned_codes(plant) == ["WO-ACME-4721", "WO-ACME-4722", "WO-ACME-4723"], (
        "the planner did not continue this plant's own sequence to the depth asked")


def test_the_planner_tops_the_book_up_rather_than_planning_a_fresh_three(plant):
    """One planned order and a depth of three is two more, not three."""
    every_order_but(plant, 1)
    had = planned_codes(plant)
    assert len(had) == 1

    async def work(floor: Floor) -> None:
        await floor.plan_the_book(3)

    as_the_planner(plant, work)

    after = planned_codes(plant)
    assert len(after) == 3, f"the book is {len(after)} deep, not three"
    assert had[0] in after, "the planner replaced the order that was already waiting"


def test_the_planner_never_reuses_an_order_code(plant):
    """Four passes, and every code the plant has ever held is still unique.

    A planner that counted from a number it held in memory rather than from
    the book would collide with itself the first time the floor restarted.
    Each pass here signs in again, which is a restart.
    """
    every_order_but(plant, 0)

    async def work(floor: Floor) -> None:
        await floor.plan_the_book(2)

    for _pass in range(4):
        as_the_planner(plant, work)
        # Whatever is planned goes onto the line, so the next pass has to plan
        # two more rather than find its own two still waiting.
        for row in plant.scalars(select(WorkOrder)).all():
            if row.status is OrderStatus.PLANNED:
                row.status = OrderStatus.COMPLETED
        plant.commit()

    codes = [row.code for row in plant.scalars(select(WorkOrder)).all()]
    assert len(codes) == len(set(codes)), "the planner reused a code"
    assert len(codes) == 10 + 8, f"{len(codes) - 10} orders were planned, not eight"


def test_a_planned_order_copies_this_plants_own_book_and_nothing_else(plant):
    """The book is the pattern of what this plant makes - nothing is invented.

    Material, quantity and priority all come back out of the plant's own
    orders, read over the API. The floor never opens a pack file.
    """
    book = json.loads((BOTTLING / "masterdata" / "work_orders.json").read_text(encoding="utf-8"))
    known = {(row["material"], float(row["quantity"]), int(row["priority"])) for row in book}
    every_order_but(plant, 0)

    async def work(floor: Floor) -> None:
        await floor.plan_the_book(4)

    as_the_planner(plant, work)

    fresh = [row for row in plant.scalars(select(WorkOrder)).all()
             if row.status is OrderStatus.PLANNED]
    assert len(fresh) == 4
    for row in fresh:
        assert (row.material.code, float(row.quantity), int(row.priority)) in known, (
            f"{row.code} is for {row.quantity} of {row.material.code} at priority "
            f"{row.priority}, which this plant has never made")
        assert row.due_date is not None, f"{row.code} was planned with no due date"
    dues = sorted(row.due_date for row in fresh)
    assert dues == [row.due_date for row in sorted(fresh, key=lambda r: r.code)], (
        "the planned orders are not due in the order they were planned")


def test_the_planner_plans_and_does_not_release(plant):
    """Releasing stays the supervisor's act, so the sequence is a real plant's.

    Not a matter of restraint: `FLOOR-PLAN` holds `orders.create` and does not
    hold `orders.release`, so the plant itself would refuse.
    """
    every_order_but(plant, 0)

    async def work(floor: Floor) -> None:
        await floor.plan_the_book(2)
        refused = await floor.post("/workorders/WO-ACME-4721/release")
        assert refused.status_code == 403, (
            f"the planner released an order; the plant answered {refused.status_code}")

    as_the_planner(plant, work)

    fresh = [row for row in plant.scalars(select(WorkOrder)).all()
             if row.code in ("WO-ACME-4721", "WO-ACME-4722")]
    assert len(fresh) == 2
    assert {row.status for row in fresh} == {OrderStatus.PLANNED}


def test_the_audit_names_the_simulated_planner_and_not_the_mes(plant):
    """Who put this order in the book has to survive in the record.

    A simulated plant where an order appeared with `system` against it would
    read, a month later, as the MES having invented production - which is the
    one thing this product says it never does (decision 0019).
    """
    every_order_but(plant, 0)

    async def work(floor: Floor) -> None:
        await floor.plan_the_book(1)

    as_the_planner(plant, work)

    rows = plant.scalars(
        select(AuditLog).where(AuditLog.entity_id == "WO-ACME-4721")).all()
    by_action = {r.action: r.actor for r in rows}
    assert by_action.get("workorder.created") == PLANNER, (
        f"the order was created by {by_action.get('workorder.created')!r}")


def test_a_plant_whose_book_has_never_held_an_order_plans_nothing_and_says_so(plant):
    """There is no default order. What a plant makes is not a simulator's to know.

    The honest answer to an empty book on a plant with no pattern in it is to
    say the pattern is missing - once - and plan nothing, rather than invent a
    first order of some plausible size.
    """
    import structlog

    for row in plant.scalars(select(WorkOrder)).all():
        plant.delete(row)
    plant.commit()

    async def work(floor: Floor) -> None:
        await floor.plan_the_book(3)
        await floor.plan_the_book(3)
        await floor.plan_the_book(3)

    with structlog.testing.capture_logs() as captured:
        as_the_planner(plant, work)
    said = [row for row in captured
            if row.get("event", "").startswith("nothing in this plant's book")]

    assert len(said) == 1, f"the missing pattern was announced {len(said)} times"
    assert plant.scalars(select(WorkOrder)).all() == [], (
        "the planner invented an order for a plant with no book at all")


@pytest.mark.parametrize("pack", ["bottling", "machining", "finewire"])
def test_every_shipped_lab_pack_asks_for_a_planner(pack):
    """The three lab plants run for weeks, so all three name a depth.

    A pack is the only place this can be said: how much work a plant keeps in
    hand is the plant's, not the product's, and the product's own default is
    no planner at all.
    """
    script = json.loads((PACKS / pack / "floor.json").read_text(encoding="utf-8"))
    assert keep_planned(script) >= 2, f"{pack} keeps {keep_planned(script)} orders planned"
    assert script["planning"].get("_why"), f"{pack}'s planning block says why it is there"
