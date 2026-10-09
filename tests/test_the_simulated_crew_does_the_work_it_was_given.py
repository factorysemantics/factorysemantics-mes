"""The crew does the work it was given: walks over, starts it, finishes it.

#157 gave this plant a dispatcher, and the Maintenance page it left behind
was a list: every order carried a name and none of them ever moved. These
tests are about the half that moves, and about the three jobs that honestly
do not - the ones whose window is shut, whose machine is making something, or
whose shift never reaches its own last hour. A simulated floor that started
everything it was given would be a worse lie than one that started nothing,
because the page would look like a plant where nothing ever gets in the way.

**Nothing here listens on a port.** The plant is the real application
answering over an in-process ASGI transport, the same arrangement as
`test_the_simulated_floor_plants_causes_an_engineer_can_find.py` and for the
same reason: the floor talks to its plant over HTTP, so the plant has to be
the application and not a stub of it.

The clock is compressed with the floor's own `speed`, not with sleeps: one
second of a 36000x floor is ten line hours, so a thirty-minute job is over in
fifty milliseconds and the test still exercises the walk, the start, the
stop, the measurement and the completion in that order.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.api.app import create_app
from fsmes.config import get_settings
from fsmes.db import Base
from fsmes.domain import (
    AuditLog,
    EquipmentState,
    EquipmentStateName,
    MaintenanceKind,
    MaintenanceOrder,
    MaintenancePlan,
    MaintenanceStatus,
    MaintenanceWindow,
    RosterEntry,
)
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import masterdata
from fsmes.services import auth, dispatch, maintenance
from fsmes.services import equipment as equipment_service
from fsmes.services import masterdata as masterdata_service
from fsmes.sim import measurement
from fsmes.sim.operations import Floor

ROOT = Path(__file__).resolve().parents[1]
BOTTLING = ROOT / "labs" / "multiplant" / "bottling"
SCRIPT = BOTTLING / "floor.json"

#: The one account the simulated trades share. A workshop has a terminal, not
#: seven logins, and the mechanic's name reaches the record through
#: `performed_by` - which is the rule `services.maintenance.whose_work` sets
#: out and the reason this floor needs no login per person.
CREW, CREW_PASSWORD = "FLOOR-CREW", "crew-lab-only"

#: A machine on this line nothing else in these tests touches.
PALLETISER = "PAL01"

#: Fast enough that a ninety-minute job is over inside a test, in line
#: seconds per real second.
FAST = 36000.0


def script(**over) -> dict:
    """The pack's own floor script, with this test's answer substituted.

    Read from the pack rather than written here: these tests are about what
    the shipped plant does, and a script typed into a test file would pass on
    a pack that had lost the key it is about.
    """
    loaded = measurement.load(SCRIPT)
    loaded["maintenance"] = {**(loaded.get("maintenance") or {}), **over}
    return loaded


def without_a_crew() -> dict:
    loaded = script()
    loaded["maintenance"].pop("crew", None)
    return loaded


# ----------------------------------------------------------------- the plant


@pytest.fixture()
def plant(tmp_path, monkeypatch):
    """One bottling plant from the shipped pack, in a database file of its own.

    A file and this plant's own `MES_DATABASE_URL`, because the floor is an
    HTTP client and the application solves its requests through its own
    sessionmaker: a test that overrode only the dependencies would be half on
    this plant and half on whatever database the process last had.
    """
    from fsmes.config import get_settings as settings_cache
    from fsmes.db import get_engine, get_sessionmaker

    url = f"sqlite:///{(tmp_path / 'plant.db').as_posix()}"
    monkeypatch.setenv("MES_DATABASE_URL", url)
    monkeypatch.setenv("MES_PLANT_TIMEZONE", "UTC")
    monkeypatch.setenv("MES_TAG_RETENTION_DAYS", "0")
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
    auth.create_user(session, code=CREW, name="Simulated maintenance crew",
                     password=CREW_PASSWORD, role="operator")
    session.commit()

    yield session

    session.close()
    engine.dispose()
    for cache in (settings_cache, get_engine, get_sessionmaker):
        cache.cache_clear()


def on_the_floor(session: Session, work, *, speed: float = FAST, seed: int = 7,
                 scr: dict | None = None):
    """Run one coroutine of the crew's against this plant, as the crew.

    One account, as the plant has one: the mechanic is named in the request
    rather than signed in, and that this is allowed at all is pinned in
    `tests/test_what_a_maintenance_job_needs_of_the_line.py` where the rule
    lives.
    """
    session.commit()
    app = create_app()

    async def _run():
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                   base_url="http://plant")
        async with client:
            crew = Floor(get_settings(), client, random.Random(seed),
                         scr if scr is not None else script(), speed)
            await crew.sign_in(CREW, CREW_PASSWORD)
            return await work(crew)

    out = asyncio.run(_run())
    session.expire_all()
    return out


async def until_the_job_is_done(crew: Floor, order: str, *, passes: int = 400,
                                gap: float = 0.005) -> bool:
    """Let the crew make its pass until this order comes back finished."""
    for _ in range(passes):
        if order in await crew.work_the_list():
            return True
        await asyncio.sleep(gap)
    return False


def somebody_on_shift(session: Session, skill: str = "GEN") -> str:
    """Whoever is on the roster right now holding this trade.

    Read from the plant rather than named here. The pack rosters four people
    on days and three on nights, so a test that named one would pass in the
    morning and fail in the evening - and the question these tests ask is
    about whoever is on, not about a particular mechanic.
    """
    rows = dispatch.roster(session)["people"]
    for person in rows:
        if skill in {s["skill"] for s in person["skills"]} and person["available"]:
            return person["person"]
    raise AssertionError(f"nobody on this shift holds {skill}; roster of {len(rows)}")


def a_job_for(session: Session, person: str, *, plan: str = "PM-PAL-GREASE",
              needs_stop: bool = False, window: str | None = "anytime") -> str:
    """One preventive order against this plan, given to this person, waiting.

    Built here rather than raised from the plan's own due-ness, because
    due-ness is a fact about hours the machine ran and units it made, and
    these tests are about what the crew does with an order it has been given.
    What the job needs of the line is set explicitly for the same reason: the
    pack's own values are pinned in `tests/test_bottling_pack.py`.
    """
    row = session.scalar(select(MaintenancePlan).where(MaintenancePlan.code == plan))
    assert row is not None, f"the pack seeds no plan {plan}"
    order = MaintenanceOrder(
        code=f"PM-TEST-{plan}", equipment_id=row.equipment_id, plan_id=row.id,
        kind=MaintenanceKind.PREVENTIVE, summary=row.name,
        skill_code=row.skill_code, priority=row.priority,
        needs_stop=needs_stop, window=MaintenanceWindow(window) if window else None)
    session.add(order)
    session.flush()
    dispatch.assign(session, order.code, person, actor="FLOOR-SUP")
    session.commit()
    return order.code


def the_machine_is(session: Session, state: str, *, equipment: str = PALLETISER,
                   since_seconds: float = 0.0, reason: str | None = None) -> None:
    equipment_service.set_state(session, equipment_code=equipment,
                                state=EquipmentStateName(state), reason=reason,
                                actor="OPC")
    row = session.scalar(select(EquipmentState).where(
        EquipmentState.equipment_id == masterdata_service.get_equipment(
            session, equipment).id,
        EquipmentState.ended_at.is_(None)))
    row.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=since_seconds)
    session.commit()


def the_state_of(session: Session, equipment: str = PALLETISER) -> EquipmentState:
    """The machine's open state interval - what it is doing right now."""
    session.expire_all()
    rows = equipment_service.current_states(session)
    found = [r for r in rows if r.equipment.code == equipment]
    assert found, f"this MES holds no open state for {equipment}"
    return found[0]


def the_order(session: Session, code: str) -> MaintenanceOrder:
    session.expire_all()
    return maintenance.get(session, code)


def audit_of(session: Session, action: str, order: str) -> list[AuditLog]:
    rows = session.scalars(select(AuditLog).where(AuditLog.action == action)).all()
    return [r for r in rows if order in json.dumps(r.after or {})]


# ------------------------------------------- the two questions, without a plant


@pytest.fixture()
def crew_alone(plant) -> Floor:
    """The floor's own judgment, with no plant on the other end of it.

    Both questions a mechanic asks are answered from values already in hand -
    the order's window, the machine's state - so they are tested as the
    functions they are. The end-to-end passes below are about the sequence.
    """
    return Floor(get_settings(), None, random.Random(1), script(), 1.0)


def test_a_job_that_can_be_done_at_any_time_has_an_open_window_and_says_so(crew_alone):
    answer = crew_alone._window_is_open("anytime", 30.0, False, None)
    assert answer.ok is True and "any time" in answer.because
    #: A plan that never said when is read as open, which is what every plant
    #: had before the column existed.
    assert crew_alone._window_is_open(None, 30.0, False, None).ok is True


def test_the_window_between_orders_is_shut_while_the_line_is_running_one(crew_alone):
    """Chain link 2, as a function. The chiller clean can only be done in the
    gap between orders, and this plant releases the next order as the last one
    finishes, so the gap never comes."""
    answer = crew_alone._window_is_open("between_orders", 90.0, False, None)
    assert answer.ok is False
    assert "only be done between orders" in answer.because
    assert crew_alone._window_is_open("between_orders", 90.0, True, None).ok is True


def test_an_eight_hour_shift_replayed_in_sixteen_minutes_never_reaches_its_own_last_hour(crew_alone):
    """`end_of_shift` is read on the plant's own clock and not in line time: a
    shift is eight hours wherever it is replayed. So the washer descale
    honestly waits on a 30x replay, and goes in at the end of the day on the
    lab plant that runs at the speed of a day."""
    now = datetime.now(UTC).replace(tzinfo=None)
    assert crew_alone._window_is_open("end_of_shift", 60.0, True,
                                      now + timedelta(hours=7)).ok is False
    assert crew_alone._window_is_open("end_of_shift", 60.0, True,
                                      now + timedelta(minutes=20)).ok is True


def test_a_window_this_floor_cannot_read_leaves_the_job_where_it_is(crew_alone):
    """Guessing would be this floor deciding when a plant may stop its line."""
    answer = crew_alone._window_is_open("when_the_boss_says", 30.0, True, None)
    assert answer.ok is False and "does not know how to read" in answer.because


def test_a_machine_that_has_only_just_gone_quiet_is_starved_and_not_stopped(crew_alone):
    """The ten minutes in the pack's `standing_s`, and what it is for. A
    mechanic does not take the filler apart because the conveyor starved it
    for forty seconds; and a floor that read every pause as its chance would
    book a stop of its own across the scripted changeover, which is the one
    availability defect `fsmes score` exists to catch."""
    now = datetime.now(UTC).replace(tzinfo=None)
    just_quiet = {PALLETISER: {"equipment": PALLETISER, "state": "idle",
                               "since": (now - timedelta(seconds=40)).isoformat()}}
    answer = crew_alone._can_get_at_it(PALLETISER, just_quiet)
    assert answer.ok is False and "starved and" in answer.because

    stopped = {PALLETISER: {"equipment": PALLETISER, "state": "idle",
                            "since": (now - timedelta(minutes=30)).isoformat()}}
    assert crew_alone._can_get_at_it(PALLETISER, stopped).ok is True


def test_a_machine_that_is_making_something_is_not_opened_for_a_job(crew_alone):
    now = datetime.now(UTC).replace(tzinfo=None)
    running = {PALLETISER: {"equipment": PALLETISER, "state": "running",
                            "since": (now - timedelta(hours=3)).isoformat()}}
    answer = crew_alone._can_get_at_it(PALLETISER, running)
    assert answer.ok is False and "it is running" in answer.because


def test_a_machine_this_mes_holds_no_state_for_cannot_be_called_stopped(crew_alone):
    """Unknown is not zero (house rule 2). Nothing here can say that a machine
    nobody has ever reported on is standing still, so the order waits with
    that written down rather than being started on a machine that may be
    running."""
    answer = crew_alone._can_get_at_it(PALLETISER, {})
    assert answer.ok is False and "holds no state for it at all" in answer.because


def test_a_job_that_waits_all_shift_on_the_same_reason_says_so_once_not_every_pass(
        crew_alone, caplog):
    """The sentence a waiting job says carries this moment's figures - how
    long the machine has been standing, how many minutes of the shift are
    left - and those change every pass. Dedup is on the reason's name, so an
    eight-hour wait is one line in the log and not fourteen hundred of them
    counting down."""
    now = datetime.now(UTC).replace(tzinfo=None)
    order = "PM-00001"
    for seconds in (40, 60, 80, 100):
        quiet = {PALLETISER: {"equipment": PALLETISER, "state": "idle",
                              "since": (now - timedelta(seconds=seconds)).isoformat()}}
        answer = crew_alone._can_get_at_it(PALLETISER, quiet)
        assert answer.ok is False
        crew_alone._say_once_why_it_waits(order, answer)
    assert crew_alone._waiting[order] == f"not-standing-long-enough:{PALLETISER}:idle"

    #: And the reason CHANGING is still the event: the same order now waiting
    #: because the machine is running is a second line, not a repeat.
    running = {PALLETISER: {"equipment": PALLETISER, "state": "running",
                            "since": (now - timedelta(hours=1)).isoformat()}}
    crew_alone._say_once_why_it_waits(order, crew_alone._can_get_at_it(PALLETISER, running))
    assert crew_alone._waiting[order] == f"the-machine-is-running:{PALLETISER}"


def test_the_sentence_a_waiting_job_says_counts_down_even_though_the_reason_does_not(
        crew_alone):
    """Both halves are wanted: a person reading the log needs the figures, and
    the log needs one line. So the sentences differ where the keys match."""
    now = datetime.now(UTC).replace(tzinfo=None)
    first = crew_alone._window_is_open("end_of_shift", 60.0, True, now + timedelta(hours=7))
    later = crew_alone._window_is_open("end_of_shift", 60.0, True, now + timedelta(hours=6))
    assert first.because != later.because
    assert first.key == later.key == "the-shift-is-not-ending-yet"


# --------------------------------------------------------- the pass, on a plant


def test_a_pack_that_says_nothing_about_a_crew_leaves_the_work_where_the_dispatcher_put_it(plant):
    """Off by default. Every pack written before 2026-10-09 raises its work,
    hands it out, and waits - and scores exactly as it did."""
    person = somebody_on_shift(plant)
    order = a_job_for(plant, person)
    the_machine_is(plant, "idle", since_seconds=3600)

    finished = on_the_floor(plant, lambda crew: crew.work_the_list(),
                            scr=without_a_crew())

    assert finished == []
    assert the_order(plant, order).status is MaintenanceStatus.ASSIGNED
    assert audit_of(plant, "maintenance.started", order) == []


def test_nobody_is_sent_to_a_machine_before_they_have_walked_to_it(plant):
    """`walk_s` from the pack, in line seconds. Without it an order is
    assigned and started in the same second, and the page shows a plant where
    nobody walks anywhere."""
    person = somebody_on_shift(plant)
    order = a_job_for(plant, person)
    the_machine_is(plant, "running", since_seconds=3600)

    async def one_pass(crew):
        return await crew.work_the_list()

    #: At the plant's own speed the walk is three minutes, so the first pass
    #: sends him off and starts nothing.
    assert on_the_floor(plant, one_pass, speed=1.0) == []
    assert the_order(plant, order).status is MaintenanceStatus.ASSIGNED


def test_a_job_the_machine_can_run_through_is_finished_with_findings_and_a_name(plant):
    """The ordinary half of maintenance, and the half a plant does not notice:
    greasing a palletiser arm while the line runs. Nought minutes of
    downtime, because the machine never stopped - a plant whose preventive
    orders all booked their planned minutes as downtime would have a pareto
    made of the plan rather than of the shift."""
    person = somebody_on_shift(plant)
    order = a_job_for(plant, person)
    the_machine_is(plant, "running", since_seconds=3600)

    assert on_the_floor(plant, lambda crew: until_the_job_is_done(crew, order)) is True

    done = the_order(plant, order)
    assert done.status is MaintenanceStatus.DONE
    assert done.performed_by == person
    assert done.downtime_minutes == 0.0
    assert done.findings in script()["maintenance"]["findings"]["PM-PAL-GREASE"]
    #: Still running: a job that needs no stop never touches the state.
    assert the_state_of(plant).state is EquipmentStateName.RUNNING


def test_the_records_name_the_mechanic_and_not_the_terminal_he_booked_it_on(plant):
    """`MT-05 started PM-…`, not `FLOOR-CREW started PM-…`. One account for
    the trades is how a plant with seven mechanics and one workshop terminal
    actually records its work, and the audit trail keeps who typed it in as a
    separate fact."""
    person = somebody_on_shift(plant)
    order = a_job_for(plant, person)
    the_machine_is(plant, "running", since_seconds=3600)

    on_the_floor(plant, lambda crew: until_the_job_is_done(crew, order))

    for action in ("maintenance.started", "maintenance.completed"):
        rows = audit_of(plant, action, order)
        assert rows, f"no {action} row for {order}"
        assert rows[0].on_behalf_of == person, (
            f"{action} names nobody: {rows[0].actor} on behalf of {rows[0].on_behalf_of}")
        assert json.loads(json.dumps(rows[0].after))["performed_by"] == person
        #: And who typed it in, kept beside it rather than instead of it.
        assert rows[0].actor == CREW


def test_a_job_that_needs_the_line_stopped_waits_while_the_machine_is_making_something(plant):
    """The seals, and the reason the Maintenance page can tell a job that
    waits from a job nobody did. The order stays where the dispatcher put it
    and nothing is booked against the machine."""
    person = somebody_on_shift(plant, "MECH")
    order = a_job_for(plant, person, plan="PM-FILL-SEALS", needs_stop=True,
                      window="anytime")
    the_machine_is(plant, "running", equipment="FILL01", since_seconds=3600)

    async def several_passes(crew):
        for _ in range(5):
            await crew.work_the_list()
            await asyncio.sleep(0.005)

    on_the_floor(plant, several_passes)

    assert the_order(plant, order).status is MaintenanceStatus.ASSIGNED
    assert the_state_of(plant, "FILL01").state is EquipmentStateName.RUNNING
    assert audit_of(plant, "maintenance.started", order) == []


def test_a_job_that_needs_the_line_stopped_books_the_stop_against_its_own_order(plant):
    """The planned stop on the State Timeline, with the order's code in the
    sentence and the plant's own word for it beside the code - so the stop and
    the job are one event in the records rather than two a reader has to join
    by eye. The machine is handed back the way it was found."""
    person = somebody_on_shift(plant, "MECH")
    order = a_job_for(plant, person, plan="PM-FILL-SEALS", needs_stop=True,
                      window="anytime")
    the_machine_is(plant, "idle", equipment="FILL01", since_seconds=3600,
                   reason="waiting for the next order")

    assert on_the_floor(plant, lambda crew: until_the_job_is_done(crew, order)) is True

    done = the_order(plant, order)
    assert done.status is MaintenanceStatus.DONE
    assert done.downtime_minutes > 0.0, "a stop nobody measured is not a stop"
    stops = plant.scalars(select(EquipmentState).where(
        EquipmentState.state == EquipmentStateName.DOWN)).all()
    planned = [s for s in stops if order in str(s.reason or "")]
    assert len(planned) == 1, f"{len(stops)} stops, {len(planned)} naming {order}"
    assert planned[0].reason_code == script()["maintenance"]["stop_reason"]
    #: Handed back to what he found, not left down for the rest of the run.
    assert the_state_of(plant, "FILL01").state is EquipmentStateName.IDLE


def test_the_crew_never_finishes_an_order_somebody_else_started(plant):
    """A restart of this floor mid-job, or a mechanic who started it on the
    screen. Nobody here watched it begin, so nobody here knows when it is
    finished, and completing it with findings this floor made up would put
    fiction in the records under a real person's name."""
    person = somebody_on_shift(plant)
    order = a_job_for(plant, person)
    maintenance.start(plant, order, actor=person)
    plant.commit()

    async def several_passes(crew):
        for _ in range(5):
            await crew.work_the_list()
            await asyncio.sleep(0.005)

    on_the_floor(plant, several_passes)

    assert the_order(plant, order).status is MaintenanceStatus.IN_PROGRESS
    assert the_order(plant, order).findings is None


def test_a_mechanic_whose_shift_ends_mid_job_still_hands_the_machine_back(plant):
    """The job is the floor's own, not the roster's. A pass that only worked
    through the people on shift would leave an order in progress and a machine
    down for the rest of the run the moment a shift changed - which on a
    plant whose crew turns over at six would be a planned stop with no end."""
    person = somebody_on_shift(plant, "MECH")
    order = a_job_for(plant, person, plan="PM-FILL-SEALS", needs_stop=True,
                      window="anytime")
    the_machine_is(plant, "idle", equipment="FILL01", since_seconds=3600)

    async def start_then_send_him_home(crew):
        for _ in range(100):
            await crew.work_the_list()
            if the_order_is_in_progress(plant, order):
                break
            await asyncio.sleep(0.005)
        taken_off_the_roster(plant, person)
        return await until_the_job_is_done(crew, order)

    assert on_the_floor(plant, start_then_send_him_home) is True
    assert the_order(plant, order).status is MaintenanceStatus.DONE
    assert the_state_of(plant, "FILL01").state is EquipmentStateName.IDLE


def in_its_own_session(session: Session, work):
    """Read or write this plant from inside the floor's own run.

    Its own session, committed and closed at once, because this plant is a
    SQLite file and every transaction here takes a write lock (`fsmes.db`
    opens them `BEGIN IMMEDIATE`). The test's long-lived session holding one
    open while the application tries to write is a deadlock, and the symptom
    is "database is locked" rather than anything about the shift.
    """
    with Session(session.get_bind()) as own:
        out = work(own)
        own.commit()
    return out


def the_order_is_in_progress(session: Session, code: str) -> bool:
    return in_its_own_session(
        session,
        lambda own: maintenance.get(own, code).status is MaintenanceStatus.IN_PROGRESS)


def taken_off_the_roster(session: Session, person: str) -> None:
    """His shift ends while he is inside the machine."""
    def drop(own: Session) -> None:
        for row in own.scalars(select(RosterEntry)).all():
            if row.person.code == person:
                own.delete(row)

    in_its_own_session(session, drop)
