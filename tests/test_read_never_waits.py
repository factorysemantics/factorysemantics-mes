"""A screen refresh does not stop anybody signing in.

WHAT HAPPENED. On 2026-09-18 Scott tried to sign in to his own lab plants
overnight and could not. Twenty sign-ins in a row against one plant: four
timed out at five seconds; against the other, eight in a row timed out. The
plants were not refused, they hung. At the same time `/equipment/FILL01/oee`
and `/analysis/oee` were returning **500**, and the tracebacks ended in
`sqlite3.OperationalError: database is locked` raised by `BEGIN IMMEDIATE`
itself.

WHY. SQLite has one writer, and this MES took the write lock at the start of
every transaction - reads included - because a transaction that discovers it
wants to write half way through is refused outright rather than made to wait
(see `fsmes.db._sqlite_transactions`). That was a fair trade while reads cost
milliseconds. It stopped being one when `/dashboard/summary` began taking ten
seconds: for those ten seconds the write lock was held by a screen refresh,
and the sign-in behind it waited out its five-second `busy_timeout` and gave
up.

Making the screen fast again (see `tests/test_kpi_budget.py`) shortens the
queue. It does not remove it: a slow read must not be able to stop a sign-in
however slow it is. So the endpoints that only read take a unit of work that
begins deferred and that the database will not let write.

These tests run against a **file-backed SQLite database of their own**,
whatever the rest of the suite is pointed at, because the lock they are about
is SQLite's and an in-memory database shared through one connection has no
lock to contend for.
"""

import threading
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

import fsmes.api.routers.dashboard as dashboard
import fsmes.db as db_module
from fsmes.api.app import create_app
from fsmes.config import get_settings
from fsmes.db import Base, get_engine, get_sessionmaker, read_only_session, session_scope, utcnow
from fsmes.domain import Equipment, EquipmentLevel, EquipmentState, EquipmentStateName, ProductionLog
from fsmes.seed import seed_demo_plant
from fsmes.services import auth

#: A sign-in is a person waiting at a screen. Anything above this is a person
#: deciding the MES is down. The failure it guards against was five seconds
#: and then an error, so there is no need to split hairs over the number.
LOGIN_BUDGET_SECONDS = 1.0

#: How long the floor screen is held mid computation, with its read
#: transaction open, while a sign-in arrives behind it. Long enough that a
#: sign-in which waited for the screen could not be mistaken for one that did
#: not, and short enough to pay for in a test suite.
SCREEN_HELD_OPEN_SECONDS = 2.0


def _caches_cleared() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@pytest.fixture()
def plant_on_disk(tmp_path, monkeypatch):
    """A real MES on a real SQLite file, with a real write lock.

    Nothing here overrides `get_db` or `get_read_db`: the point is the
    transaction each endpoint actually opens.
    """
    monkeypatch.setenv("MES_DATABASE_URL", f"sqlite:///{tmp_path / 'plant.db'}")
    _caches_cleared()
    engine = get_engine()
    Base.metadata.create_all(engine)
    with session_scope() as session:
        seed_demo_plant(session)
        auth.ensure_builtin_roles(session)
        _some_history(session)
    client = TestClient(create_app())
    client.__enter__()
    try:
        yield client
    finally:
        client.__exit__(None, None, None)
        engine.dispose()
        _caches_cleared()


def _some_history(session) -> None:
    """Enough state and production for the OEE endpoints to have work to do."""
    end = utcnow()
    machines = list(session.scalars(
        select(Equipment).where(Equipment.level == EquipmentLevel.WORK_UNIT)))
    states, bookings = [], []
    for machine in machines:
        at = end - timedelta(hours=1)
        step = 0
        while at < end:
            until = min(at + timedelta(seconds=30), end)
            states.append({"equipment_id": machine.id,
                           "state": EquipmentStateName.RUNNING if step % 4 else EquipmentStateName.DOWN,
                           "reason": None if step % 4 else "jam",
                           "started_at": at, "ended_at": None if until >= end else until})
            bookings.append({"equipment_id": machine.id, "good_qty": 5.0, "scrap_qty": 0.0, "ts": at})
            at = until
            step += 1
    session.execute(insert(EquipmentState), states)
    session.execute(insert(ProductionLog), bookings)


def _sign_in(client: TestClient) -> TestClient:
    response = client.post("/auth/login", json={"code": "ADMIN", "password": "admin"})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def test_a_read_only_unit_of_work_is_not_allowed_to_write(plant_on_disk):
    """What makes the deferred transaction safe.

    The old handler took the write lock on every transaction precisely because
    a transaction that turns out to write cannot be given the lock later. The
    new path does not ask callers to promise they will not write; it has the
    database refuse them.
    """
    with read_only_session() as session:
        assert session.execute(text("SELECT 1")).scalar() == 1
        with pytest.raises(Exception) as refused:
            session.execute(
                insert(Equipment),
                [{"code": "SNEAK", "name": "Sneak", "level": EquipmentLevel.WORK_UNIT}])
        assert "readonly" in str(refused.value).lower() or "read-only" in str(refused.value).lower()
    with session_scope() as session:
        assert session.scalar(select(Equipment).where(Equipment.code == "SNEAK")) is None


def test_a_read_in_flight_does_not_make_a_write_wait(plant_on_disk):
    """The shape of the fault, with the clock taken out of it.

    A read-only unit of work is held open - that is a screen refresh mid
    computation - and the plant is written to underneath it. Before this
    branch the read held the one write lock and the write waited out its
    five-second `busy_timeout`; the assertion below is that it does not wait
    at all.
    """
    with read_only_session() as reading:
        reading.execute(select(Equipment)).all()  # the transaction is open now
        began = time.perf_counter()
        with session_scope() as writing:
            writing.execute(
                insert(Equipment),
                [{"code": "WRITE01", "name": "Booked during a read", "level": EquipmentLevel.WORK_UNIT}])
        took = time.perf_counter() - began
    assert took < LOGIN_BUDGET_SECONDS, f"a write behind an open read took {took:.2f}s"


def test_a_login_answers_while_the_floor_screen_is_computing(plant_on_disk, monkeypatch):
    """The thing Scott could not do.

    `/dashboard/summary` is asked for on one thread, with its own read
    transaction held open for a known interval, and a sign-in arrives on
    another while it is still open. The sign-in is the assertion: it must not
    wait for the screen, however long the screen takes.

    What it no longer asserts is a wall-clock second. It did - `took < 1.0` -
    and that number failed twice on shared runners with nothing wrong with the
    lock at all: 1.06 s on `ubuntu-latest, 3.13` (main's run 37497028434) and
    1.41 s on `windows-latest, 3.12` (#147). What it was measuring there was
    not the lock. Two things were in the way. The refresh was never signed in,
    so it was a 401 that opened no transaction and held nothing - the test had
    been measuring a sign-in with nothing behind it since #80. And
    `TestClient` runs the app in this process, so a refresh that *does*
    compute holds the GIL and starves the sign-in, which makes the number
    track how slow the runner is rather than whether anybody waited.

    So both clients sign in, the screen is held open inside its own read
    transaction by a `time.sleep` that hands the GIL back, and the sign-in is
    measured against **the screen's own measured time** rather than against
    the clock: it has to answer before the screen is half way through. A
    sign-in that waited for the lock would sit out the whole hold - or, before
    #80, the fifteen-second `busy_timeout` behind it. On a runner twice as
    slow both numbers double and the assertion says the same thing.
    """
    # Both clients signed in. The screen is behind the same gate as everything
    # else, and until this branch the refresh in this test was an unauthorised
    # 401: it never reached the query, so nothing was ever held open and the
    # second of wall clock below it was measuring a sign-in on its own.
    client = _sign_in(plant_on_disk)
    signed_in = _sign_in(TestClient(client.app).__enter__())

    # A refresh of this plant, taking SCREEN_HELD_OPEN_SECONDS inside its own
    # read transaction. Patched rather than slowed from outside because the
    # transaction has to be *open* while the sign-in arrives, which is the
    # whole shape of the fault.
    building = threading.Event()
    real_build = dashboard._build_summary

    def slowly(*args, **kwargs):
        building.set()
        time.sleep(SCREEN_HELD_OPEN_SECONDS)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(dashboard, "_build_summary", slowly)
    # The summary is shared across viewers for a second at a time, and the
    # cache is the module's. An entry another test left behind would answer
    # this request without computing anything.
    with dashboard._cache_lock:
        dashboard._cache.clear()

    refresh: dict = {}

    def refresh_once() -> None:
        began = time.perf_counter()
        refresh["response"] = client.get("/dashboard/summary")
        refresh["took"] = time.perf_counter() - began

    refreshing = threading.Thread(target=refresh_once)
    refreshing.start()
    try:
        assert building.wait(timeout=30.0), "the screen never began computing"
        began = time.perf_counter()
        response = signed_in.post("/auth/login", json={"code": "ADMIN", "password": "admin"})
        took = time.perf_counter() - began
    finally:
        refreshing.join()

    assert response.status_code == 200, response.text
    assert refresh["response"].status_code == 200, refresh["response"].text
    # The comparison only has teeth if the screen really was slow, so that is
    # asserted first and separately.
    assert refresh["took"] >= SCREEN_HELD_OPEN_SECONDS, \
        f"the screen was not actually held open; it took {refresh['took']:.2f}s"
    # And the claim itself, against the screen's own time rather than the
    # clock's: the sign-in answered while the screen was less than half way
    # through. On a runner twice as slow both numbers double and this still
    # says the same thing.
    assert took < refresh["took"] / 2.0, (
        f"the sign-in took {took:.2f}s while the screen it was behind took "
        f"{refresh['took']:.2f}s: it waited for it")


def test_the_machine_page_answers_while_the_plant_is_being_written_to(plant_on_disk):
    """One of the two endpoints that were returning 500.

    A writer holds the write lock - which is what an OPC agent booking a batch
    of readings looks like - and the machine's OEE is asked for underneath it.
    It used to be a 500 with `database is locked` on `BEGIN IMMEDIATE`; a read
    has no business needing that lock.
    """
    client = _sign_in(plant_on_disk)
    with session_scope() as booking:
        booking.execute(insert(ProductionLog),
                        [{"equipment_id": 1, "good_qty": 1.0, "scrap_qty": 0.0, "ts": utcnow()}])
        booking.flush()  # the write lock is held from here to the commit
        response = client.get("/equipment/MIX01/oee")
    assert response.status_code == 200, response.text
    assert response.json()["equipment"] == "MIX01"


def test_the_shift_analysis_answers_while_the_plant_is_being_written_to(plant_on_disk):
    """The other one."""
    client = _sign_in(plant_on_disk)
    with session_scope() as booking:
        booking.execute(insert(ProductionLog),
                        [{"equipment_id": 1, "good_qty": 1.0, "scrap_qty": 0.0, "ts": utcnow()}])
        booking.flush()
        response = client.get("/analysis/oee?hours=1")
    assert response.status_code == 200, response.text
    assert response.json()["stations"], "the breakdown should name the plant's machines"


def test_the_write_lock_is_still_taken_for_a_transaction_that_may_write(plant_on_disk):
    """The old behaviour is still the default, and deliberately so.

    Decision: only a caller that says it reads gets the deferred transaction.
    Everything else keeps `BEGIN IMMEDIATE`, because a transaction that
    discovers it wants to write half way through is refused rather than made
    to wait - which is the fault `_sqlite_transactions` was written for.
    """
    statements: list[str] = []
    import sqlalchemy

    @sqlalchemy.event.listens_for(get_engine(), "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with session_scope() as session:
        session.execute(select(Equipment)).all()
    assert any(s.startswith("BEGIN IMMEDIATE") for s in statements), statements
    statements.clear()
    with read_only_session() as session:
        session.execute(select(Equipment)).all()
    assert any(s.strip() == "BEGIN" for s in statements), statements
    assert not any(s.startswith("BEGIN IMMEDIATE") for s in statements), statements
    assert db_module.READ_ONLY  # the option the handler reads


#: Every endpoint moved onto the read-only unit of work. The rest of the suite
#: shares one writable session with its clients, so a write that slipped into
#: one of these would pass there and fail on a plant. Here they run against
#: the real thing.
READ_ONLY_ENDPOINTS = (
    "/health",
    "/metrics",
    "/dashboard/summary",
    "/equipment/MIX01/oee",
    "/analysis/lines",
    "/analysis/shifts",
    "/analysis/oee?hours=1",
    "/analysis/timeline?hours=1",
    "/analysis/downtime?hours=1",
    "/analysis/production?hours=1",
    "/analysis/tag/MIX01",
)


@pytest.mark.parametrize("path", READ_ONLY_ENDPOINTS)
def test_an_endpoint_that_says_it_only_reads_only_reads(plant_on_disk, path):
    """A read-only endpoint that writes is a 500 on a real plant, and nothing
    in the rest of the suite would see it: those clients share one writable
    session. These run against a unit of work the database will not let
    write, so a stray write fails here instead of on a shop floor."""
    response = _sign_in(plant_on_disk).get(path)
    assert response.status_code == 200, f"{path}: {response.text[:400]}"
