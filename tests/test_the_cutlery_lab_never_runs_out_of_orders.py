"""The cutlery lab never runs out of orders.

On the night of 2026-11-10 the lab's cutlery plant, seeded at a hundredth of
the customer's rate with one released order per line and nothing behind it,
had finished all sixty-one by four in the morning; the operator walks that
night stopped on "all 61 orders are completed". The plant's own floor script
now asks for one planned order behind every line, each line's book continued
on that line, and the supervisor puts the next one on the line and issues it
the line's lot when the one before is done.

**Nothing here listens on a port.** The plant is the real application over an
in-process ASGI transport, as in `test_lab_order_book.py`.
"""

from __future__ import annotations

import asyncio
import importlib.util
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
from fsmes.domain import Equipment, LotConsumption, OrderStatus, ProductionSource, WorkOrder
from fsmes.services import Invalid, auth, execution, workorders
from fsmes.sim.operations import Floor, keep_planned, plans_each_line

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "labs" / "cutlery"
SCRIPT = json.loads((LAB / "floor.json").read_text(encoding="utf-8"))
ACTIVE = (OrderStatus.RELEASED, OrderStatus.RUNNING)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def plant(tmp_path, monkeypatch):
    """The cutlery plant at the lab's teaching pace, seeded by its own init.py,
    in a database file of its own (the reasons are `test_lab_order_book`'s)."""
    from fsmes.config import get_settings as settings_cache
    from fsmes.db import get_engine, get_sessionmaker

    monkeypatch.setenv("MES_DATABASE_URL", f"sqlite:///{(tmp_path / 'plant.db').as_posix()}")
    monkeypatch.setenv("MES_PLANT_TIMEZONE", "UTC")
    monkeypatch.setenv("MES_TAG_RETENTION_DAYS", "0")
    for leaked in ("MES_MODULES", "MES_WORDS", "MES_PLANT_NAME", "MES_PLANT_LABEL",
                   "MES_REPLAY_DIR", "MES_SIM_SPEED", "MES_TAG_MAP_FILE"):
        monkeypatch.delenv(leaked, raising=False)
    for cache in (settings_cache, get_engine, get_sessionmaker):
        cache.cache_clear()

    build_config = _load(LAB / "build_config.py", "cutlery_build_config_flowing")
    make_tag_map = _load(LAB / "make_tag_map.py", "cutlery_make_tag_map_flowing")
    init = _load(LAB / "init.py", "cutlery_init_flowing")
    (tmp_path / "line.json").write_text(json.dumps(build_config.build(scale=0.01)), encoding="utf-8")
    (tmp_path / "tag_map.json").write_text(json.dumps(make_tag_map.build(tmp_path / "line.json")),
                                           encoding="utf-8")

    engine = get_engine()
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    made = init.seed(session, tmp_path)
    init.release_orders(session, made["orders"])
    auth.ensure_builtin_roles(session)
    auth.create_user(session, code="FLOOR-SUP", name="Simulated shift supervisor",
                     password="supervisor", role="supervisor")
    auth.create_user(session, code="FLOOR-PLAN", name="Simulated production planner",
                     password="planner", role="planner")
    session.commit()
    session.info["init"], session.info["dir"] = init, tmp_path

    yield session

    session.close()
    engine.dispose()
    for cache in (settings_cache, get_engine, get_sessionmaker):
        cache.cache_clear()


def as_(session: Session, code: str, password: str, work) -> None:
    async def _run():
        app = create_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://plant") as client:
            floor = Floor(get_settings(), client, random.Random(7), SCRIPT)
            await floor.sign_in(code, password)
            await work(floor)

    session.commit()
    asyncio.run(_run())
    session.expire_all()


def supervise(session: Session) -> None:
    async def work(floor: Floor) -> None:
        page = await floor.get("/workorders", status=["released", "running"], limit=500)
        await floor.work_the_book(page["items"])
    as_(session, "FLOOR-SUP", "supervisor", work)


def plan(session: Session) -> None:
    async def work(floor: Floor) -> None:
        await floor.plan_the_book(keep_planned(SCRIPT))
    as_(session, "FLOOR-PLAN", "planner", work)


def every_line(session: Session) -> list[Equipment]:
    return session.scalars(select(Equipment).where(Equipment.code.like("%LINE"))).all()


def orders_on(session: Session, line: Equipment, *statuses: OrderStatus) -> list[WorkOrder]:
    return session.scalars(select(WorkOrder).where(WorkOrder.work_center_id == line.id,
                                                   WorkOrder.status.in_(statuses))).all()


def every_line_makes_its_order(session: Session) -> None:
    """Every station books what its line's open order asks for, as the OPC
    agent's counters would: the moment each order has made its number."""
    for wo in session.scalars(select(WorkOrder).where(WorkOrder.status.in_(ACTIVE))).all():
        for op in wo.operations:
            execution.report(session, equipment_code=op.equipment.code, good=wo.quantity,
                             source=ProductionSource.OPC, actor="opc-agent")
    session.commit()


def without_an_open_order(session: Session) -> list[str]:
    return [line.code for line in every_line(session) if not orders_on(session, line, *ACTIVE)]


def test_the_cutlery_floor_script_asks_for_a_planned_order_behind_every_line():
    assert keep_planned(SCRIPT) >= 1 and plans_each_line(SCRIPT)
    assert SCRIPT["planning"].get("_why")


def test_a_cutlery_line_is_never_without_an_order_and_always_has_the_next_one_planned(plant):
    """Three orders through every line, and not one moment without an order.

    Each round is one order's worth of line time: every line makes its
    number, the supervisor finishes it and puts the next on, the planner
    plans another behind it. The plant is looked at before and after each
    step; the share of those looks in which any line had no open order is
    the time a line spent idle for want of an order, and it is zero.
    """
    lines = every_line(plant)
    assert len(lines) == 61
    looks = idle = 0

    def look() -> None:
        nonlocal looks, idle
        looks += len(lines)
        idle += len(without_an_open_order(plant))

    look()
    for round_ in range(1, 4):
        every_line_makes_its_order(plant)
        look()
        supervise(plant)
        look()
        plan(plant)
        look()

        completed = plant.scalars(select(WorkOrder).where(WorkOrder.status == OrderStatus.COMPLETED)).all()
        assert len(completed) == 61 * round_, f"round {round_}: {len(completed)} orders finished"
        assert len(plant.scalars(select(WorkOrder)).all()) == 61 * (round_ + 2), (
            "the book did not grow by one order a line")
        for line in lines:
            open_ = orders_on(plant, line, *ACTIVE)
            planned = orders_on(plant, line, OrderStatus.PLANNED)
            assert len(open_) == 1, f"{line.code} has {len(open_)} open orders"
            assert len(planned) >= 1, f"{line.code} has nothing planned behind {open_[0].code}"
            name = line.code.removesuffix("LINE")
            assert open_[0].code == f"WO-{name}-{round_ + 1:04d}"
            assert planned[0].code == f"WO-{name}-{round_ + 2:04d}"
            if name.startswith(("FORK", "SPOON", "KNIFE", "WRAP")):
                issued = plant.scalars(select(LotConsumption).where(
                    LotConsumption.work_order_id == open_[0].id)).all()
                assert [c.lot.code for c in issued] in (
                    [f"LOT-PP-{name}-001"], [f"LOT-PLATE-{name}-001"]), (
                    f"{open_[0].code} runs with no lot issued; its pieces trace to nothing")
                assert issued[0].quantity == pytest.approx(
                    plant.scalars(select(LotConsumption).join(WorkOrder).where(
                        WorkOrder.code == f"WO-{name}-0001")).one().quantity, abs=0.1)

    assert looks > 0 and idle / looks == 0, f"lines were without an order in {idle} of {looks} looks"


def test_the_seed_runs_once_and_a_second_run_changes_nothing(plant):
    init, directory = plant.info["init"], plant.info["dir"]
    before = sorted((wo.code, wo.status.value) for wo in plant.scalars(select(WorkOrder)))
    assert len(before) == 122
    assert init.seed(plant, directory) is None
    assert sorted((wo.code, wo.status.value) for wo in plant.scalars(select(WorkOrder))) == before


def test_an_order_named_for_a_line_is_built_from_that_lines_routing_and_any_other_is_refused(plant):
    wo = workorders.create(plant, code="WO-FORK3-9000", material_code="UT-FORK", quantity=10,
                           line_code="FORK3LINE")
    assert [op.equipment.code for op in wo.operations] == ["FORK3_Mold", "FORK3_Degate", "FORK3_Mark"]
    assert wo.work_center.code == "FORK3LINE"
    with pytest.raises(Invalid, match="no routing on line"):
        workorders.create(plant, code="WO-FORK3-9001", material_code="UT-FORK", quantity=10,
                          line_code="SPOON1LINE")
