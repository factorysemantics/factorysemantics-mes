"""A plant on SQLite books production while somebody reads its dashboard.

WHY THIS FILE EXISTS. Two lab plants ran for six hours on 2026-09-18 at
replay speed 10. The bottling plant's log held **18,564**
``sqlite3.OperationalError: database is locked``, 825 HTTP 500s and 754
failed shop-floor steps, and the machine could not book its own
production. Every one of those tracebacks ended on the same statement:
``BEGIN IMMEDIATE``.

``db.py`` begins *every* transaction with the write lock taken, reads
included, because a transaction that discovers it wants to write has to
upgrade, and SQLite refuses that upgrade outright rather than waiting.
Taking the lock up front turns the refusal into a wait that
``busy_timeout`` covers. That was right for the writers and wrong for
everybody else: a dashboard read that spends ten seconds computing a KPI
held SQLite's one write lock for those ten seconds, and every booking
that arrived meanwhile waited five and gave up.

So this is the shape that has to work: readers reading while writers
write. The harness below runs the plant's three real parties against one
file-backed database in WAL mode — the OPC agent booking readings, a
simulated floor working through the HTTP API, and dashboards reading —
and counts what fails. It is the reproduction, and the test is the
reproduction run short enough for CI. Run it as long as the lab ran::

    python -m tests.test_a_plant_books_while_its_dashboard_is_read 60

It is deliberately file-backed and deliberately threaded: an in-memory
database has no lock to contend for, which is why the rest of the suite
never saw any of this.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import func, select

import fsmes.domain  # noqa: F401  (registers every table)
from fsmes.domain import (
    Equipment,
    EquipmentLevel,
    EquipmentState,
    EquipmentStateName,
    ProductionLog,
    TagValue,
)
from fsmes.integrations.opc import agent as opc_agent
from fsmes.integrations.opc.tag_map import MachineMap

# The lab's bottling plant: six work units, one-second cycles, replay
# speed 10. The harness sends the same tags the OPC agent subscribes to.
MACHINES = ("FILL01", "WASH01", "RD01", "LD01", "QI01", "PAL01")
STATE_MAP = {"1": EquipmentStateName.RUNNING, "4": EquipmentStateName.DOWN}

# How much history the screens have to read through. The lab's bottling
# plant had six hours of it: 12,168 state intervals, 92,707 production
# logs, 278,033 tag values. A dashboard read on an empty database is
# microseconds and contends with nothing, so a harness seeded with
# nothing would prove nothing.
#
# Seeded to the lab's size, `/dashboard/summary` here takes 7.5 s — the
# lab measured 10.07 s, so this is the same plant. What the summary costs
# is the sibling handoff's to fix; what it must not do is stop the plant
# booking while it costs it, and that is what this file is about.
HISTORY = {"states": 12_000, "production": 90_000, "tags": 120_000}
# A tenth of it for the suite: a 0.35 s read, which is thirty-five of the
# agent's batches long — plenty of overlap, and a test that runs in
# seconds rather than minutes.
CI_HISTORY = {"states": 2_400, "production": 18_000, "tags": 24_000}

# What "the lab's rate" was: the agent booked 278,033 tag values and
# 92,707 production logs in six hours — about thirteen readings a second
# from six machines. One batch of six a tick at ten ticks a second is
# that rate, arriving the way the agent's consumer sees it.
BATCHES_PER_SECOND = 10.0


def _specs() -> dict[str, MachineMap]:
    return {
        code: MachineMap(equipment=code, object=code.title(), cycle_seconds=1.0,
                         analog="Analog", state_map=STATE_MAP)
        for code in MACHINES
    }


@dataclass
class Tally:
    """What the plant managed, and what it lost."""

    seconds: float = 0.0
    batches: int = 0
    reads: int = 0
    floor_steps: int = 0
    floor_refused: int = 0
    locked: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    good_sent: int = 0
    good_booked: float = 0.0

    @property
    def locks(self) -> int:
        return sum(self.locked.values())

    def note(self, party: str, exc: BaseException) -> None:
        if "database is locked" in str(exc):
            self.locked[party] += 1
        else:
            self.errors.append(f"{party}: {type(exc).__name__}: {exc}")

    def report(self) -> str:
        lost = self.good_sent - self.good_booked
        return (
            f"{self.seconds:.0f}s: {self.batches} agent batches, {self.reads} dashboard reads, "
            f"{self.floor_steps} floor steps ({self.floor_refused} refused)\n"
            f"  database is locked: {self.locks} {dict(self.locked) or ''}\n"
            f"  other failures: {len(self.errors)}"
            + (f" (first: {self.errors[0]})" if self.errors else "")
            + f"\n  units the plant counted: {self.good_sent}; units booked: {self.good_booked:g}; "
              f"unaccounted for: {lost:g}"
        )


def seed(history: dict[str, int] | None = None) -> None:
    """A six-machine plant with as much history behind it as the lab had.

    Written straight to the tables rather than through the services: this
    is the scenery the screens read, and building six hours of it a row at
    a time through the domain layer would take longer than the run it is
    scenery for.
    """
    from datetime import timedelta

    from fsmes.db import Base, get_engine, session_scope, utcnow
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    history = HISTORY if history is None else history
    Base.metadata.create_all(get_engine())
    with session_scope() as db:
        seed_demo_plant(db)
        auth.ensure_builtin_roles(db)
        line = db.scalar(select(Equipment).where(Equipment.code == "LINE1"))
        for code in MACHINES:
            db.add(Equipment(code=code, name=f"{code} in the lab", parent=line,
                             level=EquipmentLevel.WORK_UNIT, ideal_cycle_seconds=1.0))

    with session_scope() as db:
        ids = dict(db.execute(select(Equipment.code, Equipment.id)
                              .where(Equipment.code.in_(MACHINES))).all())
        now = utcnow()
        window = timedelta(hours=8)
        each = max(1, history["states"] // len(MACHINES))
        step = window / each
        states, production, tags = [], [], []
        for eid in ids.values():
            for i in range(each):
                at = now - window + step * i
                states.append({"equipment_id": eid, "state": EquipmentStateName.RUNNING
                               if i % 4 else EquipmentStateName.DOWN,
                               "started_at": at, "ended_at": at + step, "actor": "seed"})
        for i in range(history["production"]):
            code = MACHINES[i % len(MACHINES)]
            production.append({"equipment_id": ids[code], "good_qty": 1.0, "scrap_qty": 0.0,
                               "ts": now - window + window * i / history["production"]})
        for i in range(history["tags"]):
            code = MACHINES[i % len(MACHINES)]
            tags.append({"equipment_id": ids[code], "tag": f"{code}.Analog", "value_num": float(i % 100),
                         "ts": now - window + window * i / history["tags"]})
        for table, rows in ((EquipmentState, states), (ProductionLog, production), (TagValue, tags)):
            for start in range(0, len(rows), 5000):
                db.execute(table.__table__.insert(), rows[start:start + 5000])


def _agent_loop(stop: threading.Event, tally: Tally, counts: dict[str, int]) -> None:
    """The OPC agent, booking batches the way its consumer does.

    Not a stand-in: this is ``_Handler._process``, the method the running
    agent calls on every drain, with the same batch shape.
    """
    specs = _specs()
    handler = opc_agent._Handler(node_info={})
    tick = 1.0 / BATCHES_PER_SECOND
    running = "1"
    while not stop.is_set():
        started = time.monotonic()
        batch = []
        for code in MACHINES:
            counts[code] += 1
            spec = specs[code]
            batch.append((spec, "State", running, None, started))
            batch.append((spec, "GoodCount", counts[code], None, started))
            batch.append((spec, "Analog", 42.0 + counts[code] % 5, None, started))
        try:
            handler._process(batch)
        except Exception as exc:  # the agent logs and carries on; so do we
            tally.note("agent", exc)
        tally.batches += 1
        slept = tick - (time.monotonic() - started)
        if slept > 0:
            stop.wait(slept)


def _dashboard_loop(stop: threading.Event, tally: Tally, reader: int) -> None:
    """A screen asking for the summary, over and over, as the lab's own
    shop-floor loop did — 21,777 times in six hours.

    It calls the endpoint's own builder inside a real session, because
    what matters here is how long a transaction is open and what kind it
    is, not how the request arrived.
    """
    from fsmes.api.routers.dashboard import _build_summary
    from fsmes.db import read_only_session

    hours = 8.0 + reader  # a different window per screen, so the cache never serves it
    while not stop.is_set():
        try:
            with read_only_session() as db:
                _build_summary(db, hours, None, None, None, None, 0)
            tally.reads += 1
        except Exception as exc:
            tally.note("dashboard", exc)


def _floor_loop(stop: threading.Event, tally: Tally, base_url: str) -> None:
    """The simulated floor, writing through the HTTP API as an operator's
    browser would: sign in, then record a quality check every step."""
    import httpx

    with httpx.Client(base_url=base_url, timeout=20.0) as client:
        token = client.post("/auth/login", json={"code": "SCOTT", "password": "operator"})
        if token.status_code != 200:
            tally.errors.append(f"floor could not sign in: {token.status_code} {token.text[:120]}")
            return
        client.headers["Authorization"] = f"Bearer {token.json()['token']}"
        step = 0
        while not stop.is_set():
            step += 1
            try:
                response = client.post("/quality/checks", json={
                    "material": "FG-COLA", "characteristic": "brix",
                    "value": 10.0 + (step % 7) / 10,
                    "equipment": MACHINES[step % len(MACHINES)]})
                tally.floor_steps += 1
                if response.status_code >= 400:
                    tally.floor_refused += 1
                    if "database is locked" in response.text:
                        tally.locked["floor"] += 1
                    else:
                        tally.errors.append(f"floor: {response.status_code} {response.text[:160]}")
            except Exception as exc:
                tally.note("floor", exc)
            stop.wait(0.1)


@contextlib.contextmanager
def _an_api_on_loopback():
    """A real API process's worth of app, on a port the kernel picks.

    Port 0 so the harness can never collide with a plant somebody is
    running, and the socket closes when the block does.
    """
    import uvicorn

    from fsmes.api.app import create_app

    config = uvicorn.Config(create_app(), host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="api", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("the harness's API never came up")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=30)


class _CountsLockFailures(logging.Handler):
    """Counts what the plant's own log would show.

    The agent catches `database is locked` and logs it rather than
    raising, so a tally that only watched exceptions would report zero
    while the log filled with them - which is exactly how this went
    unnoticed. This counts the same thing the steward counted with grep.
    """

    def __init__(self, tally: Tally) -> None:
        super().__init__()
        self.tally = tally

    def emit(self, record: logging.LogRecord) -> None:
        text = str(record.msg)
        if record.exc_info and record.exc_info[1] is not None:
            text += " " + str(record.exc_info[1])
        if "database is locked" in text:
            self.tally.locked["logged"] += 1


def _production_booked() -> float:
    from fsmes.db import read_only_session

    with read_only_session() as db:
        return db.scalar(select(func.coalesce(func.sum(ProductionLog.good_qty), 0.0))) or 0.0


def run_the_plant(*, seconds: float, readers: int = 3, floor: bool = True) -> Tally:
    """Run the plant's three parties against one database for `seconds`.

    The agent books readings; the screens read the summary; the floor
    works through a real HTTP API on loopback, the way the lab's
    `run-operations` does. Everything stops when the run does.
    """
    tally = Tally()
    counts = dict.fromkeys(MACHINES, 0)
    booked_before = _production_booked()
    stop = threading.Event()

    with contextlib.ExitStack() as stack:
        watcher = _CountsLockFailures(tally)
        logging.getLogger().addHandler(watcher)
        stack.callback(logging.getLogger().removeHandler, watcher)
        base_url = stack.enter_context(_an_api_on_loopback()) if floor else None
        threads = [threading.Thread(target=_agent_loop, args=(stop, tally, counts), name="agent")]
        threads += [threading.Thread(target=_dashboard_loop, args=(stop, tally, i), name=f"screen-{i}")
                    for i in range(readers)]
        if base_url:
            threads.append(threading.Thread(target=_floor_loop, args=(stop, tally, base_url),
                                            name="floor"))
        started = time.monotonic()
        for t in threads:
            t.daemon = True
            t.start()
        stop.wait(seconds)
        stop.set()
        for t in threads:
            t.join(timeout=60)
        tally.seconds = time.monotonic() - started

    tally.good_sent = sum(counts.values())
    tally.good_booked = _production_booked() - booked_before
    return tally


@pytest.fixture()
def a_plant_on_a_file(tmp_path, monkeypatch):
    """The real engine, pointed at a file in WAL mode, and put back after."""
    from fsmes import config
    from fsmes import db as db_module

    cached = (config.get_settings, db_module.get_engine, db_module.get_sessionmaker)
    monkeypatch.setenv("MES_DATABASE_URL", f"sqlite:///{(tmp_path / 'plant.db').as_posix()}")
    for c in cached:
        c.cache_clear()
    seed(CI_HISTORY)
    yield tmp_path / "plant.db"
    db_module.get_engine().dispose()
    for c in cached:
        c.cache_clear()


def test_a_dashboard_being_read_never_stops_the_plant_booking(a_plant_on_a_file):
    """The whole point, in one sentence: three screens reading the summary
    on a loop while the agent books six machines' readings ten times a
    second, and not one booking is refused.

    Before the fix this failed in the first second — the screens held the
    write lock, the agent waited `busy_timeout` and gave up.
    """
    tally = run_the_plant(seconds=5.0)

    assert tally.reads > 0, "no screen ever read the summary; the harness proves nothing"
    assert tally.batches > 0, "the agent never booked a batch"
    assert tally.locks == 0, f"the plant could not book its own production\n{tally.report()}"
    assert tally.errors == [], f"the plant failed for another reason\n{tally.report()}"


def test_every_unit_the_plant_counted_after_its_baseline_is_booked(a_plant_on_a_file):
    """Never invent production, read the other way round: never lose it.

    The agent's counter baseline may only move when the booking that used
    it has committed. It used to move first, so a booking that failed took
    its units with it - and the retry, recomputing the delta from the
    baseline that had already moved, booked nothing and reported success.

    The one unit per machine that is not booked is the machine's first
    reading. A counter is absolute, so the first value the agent ever sees
    says where the machine is, not what it made while the MES was watching;
    booking it would be inventing production. Six machines, six units, and
    that number is in the assertion rather than glossed.
    """
    tally = run_the_plant(seconds=5.0)

    assert tally.good_sent > 0, "the plant counted nothing; the harness proves nothing"
    baselines = len(MACHINES)
    assert tally.good_booked == tally.good_sent - baselines, (
        f"{tally.good_sent - baselines - tally.good_booked:g} units were counted by a "
        f"machine and never reached a book\n{tally.report()}")


if __name__ == "__main__":  # the reproduction, run for as long as you like
    import os
    import tempfile

    import structlog

    from fsmes import config
    from fsmes import db as db_module
    from fsmes.integrations.opc import agent as _agent
    from fsmes.logging import setup_logging

    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "plant.db"
        os.environ["MES_DATABASE_URL"] = f"sqlite:///{path.as_posix()}"
        for cache in (config.get_settings, db_module.get_engine, db_module.get_sessionmaker):
            cache.cache_clear()
        # Log the way a plant logs, so the console this writes can be counted
        # with the same grep the steward ran over `logs/bottling/plant.log`.
        setup_logging("INFO", Path(tmp) / "logs", "harness")
        _agent.log = structlog.get_logger("opc.agent")
        seed()
        print(run_the_plant(seconds=duration).report())
