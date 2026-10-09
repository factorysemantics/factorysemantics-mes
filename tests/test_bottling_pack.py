"""The bottling pack carries its line, and carries the same line as the code.

This plant's six stations ship *inside* the product - `fsmes seed-kepsim`
builds them, because they are the twin's own reference line. The pack
therefore carried no master data on purpose, and the argument was a good one:
a copy can drift from what it copied.

What the copy's absence cost, measured in the lab on 2026-09-14: `fsmes fleet
create` and `fsmes plant bottling init` both produced a plant with **zero
equipment**, and `fsmes score bottling` and `fsmes sweep bottling` died on
their first read - `GET /analysis/timeline` answered 404, *"no line has any
machines on it yet"* - because the ephemeral plant a scored run builds applies
the pack and nothing else. The only thing that seeded this plant was a script
the registry had stopped naming.

So the line is in the pack, and the drift the old argument feared is what this
file is for: seed one database from `seed_kepsim`, seed another from
`masterdata/`, and compare them. Either side changing alone fails here.

Nothing here starts a plant, a port or a process.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.db import Base
from fsmes.domain import (
    BomItem,
    Equipment,
    EquipmentLevel,
    Gauge,
    MaintenancePlan,
    Material,
    MaterialLot,
    QualitySpec,
    Routing,
    ShiftPattern,
    WorkOrder,
)
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import masterdata
from fsmes.seed_kepsim import seed_kepsim_line

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "labs" / "multiplant" / "bottling"
DATA = PACK / "masterdata"
KEPSIM_MAP = ROOT / "config" / "tag_map_kepsim.json"
ORDER = "WO-ACME-4711"


@pytest.fixture(autouse=True)
def _close_what_this_file_opens():
    """Every database this file makes, closed when the test ends.

    In-memory, so there is no file for Windows to refuse to delete - but a
    `StaticPool` holds its one connection for as long as the engine lives, and
    a test module that opens two per test and disposes none leaves them to the
    collector. Cheap to be tidy, and it keeps the reason written down.
    """
    OPENED.clear()
    yield
    for session in OPENED:
        session.close()
        session.get_bind().dispose()
    OPENED.clear()


#: Every session `blank` handed out during the test now running.
OPENED: list[Session] = []


def blank() -> Session:
    """One private in-memory database, with nothing seeded into it."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    OPENED.append(session)
    return session


def by_the_product() -> Session:
    """The line as `fsmes seed-kepsim` builds it.

    No orders. `seed_kepsim` builds a line, not a schedule, and the order book
    is the pack's own - since 2026-09-18 ten of them rather than one, which is
    a fact about how this plant is scheduled and not about whether the two
    seeders agree on the line. The book has its own tests below.
    """
    session = blank()
    seed_kepsim_line(session, tag_map=KEPSIM_MAP)
    session.flush()
    return session


def by_the_pack() -> tuple[Session, dict]:
    """The line as `fsmes pack apply` builds it from `masterdata/`."""
    session = blank()
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(PACK / "tag_map.json")}
    receipt = masterdata.seed(session, DATA, cycles)
    session.flush()
    return session, receipt


def shape(session: Session) -> dict:
    """Everything about this plant that either side is responsible for, as
    plain values. Ids are left out on purpose - they say which order rows were
    written in, and that is not a fact about the plant."""
    equipment = {e.code: (e.name, e.level.value,
                          e.parent.code if e.parent else None,
                          e.ideal_cycle_seconds)
                 for e in session.scalars(select(Equipment))}
    materials = {m.code: (m.name, m.unit, m.type.value)
                 for m in session.scalars(select(Material))}
    bom = sorted((b.parent.code, b.component.code, b.quantity, b.operation_seq)
                 for b in session.scalars(select(BomItem)))
    routings = {r.code: (r.name, r.material.code,
                         [(o.seq, o.name, o.equipment.code)
                          for o in sorted(r.operations, key=lambda o: o.seq)])
                for r in session.scalars(select(Routing))}
    specs = sorted((q.material.code, q.characteristic, q.unit, q.min_value, q.max_value)
                   for q in session.scalars(select(QualitySpec)))
    lots = {lot.code: (lot.material.code, lot.original_quantity)
            for lot in session.scalars(select(MaterialLot))}
    plans = {p.code: (p.name, p.equipment.code, p.trigger.value, p.interval,
                      p.expected_minutes)
             for p in session.scalars(select(MaintenancePlan))}
    shifts = {s.code: (s.name, s.starts.isoformat(), s.ends.isoformat(), s.days,
                       s.equipment.code if s.equipment else None)
              for s in session.scalars(select(ShiftPattern))}
    return {"equipment": equipment, "materials": materials, "bom": bom,
            "routings": routings, "quality_specs": specs, "lots": lots,
            "maintenance_plans": plans, "shifts": shifts}


def test_the_bottling_pack_builds_the_line_the_products_own_seeder_builds():
    """The whole point. Two databases, one from the code and one from the
    files, compared kind by kind so a failure names which kind drifted."""
    theirs = shape(by_the_product())
    session, _ = by_the_pack()
    ours = shape(session)
    for kind in theirs:
        assert ours[kind] == theirs[kind], (
            f"{kind} differs between `fsmes seed-kepsim` and "
            f"labs/multiplant/bottling/masterdata/{kind}.json")


def test_applying_the_bottling_pack_gives_the_plant_six_machines_to_read():
    """The state the lab found: a plant that answers, at head, with no line.
    A scored run applies the pack and nothing else, so what the pack seeds is
    the whole of what `fsmes score bottling` has to read - and reading a line
    with no machines on it is the 404 that killed the run."""
    session, receipt = by_the_pack()
    machines = list(session.scalars(
        select(Equipment).where(Equipment.level == EquipmentLevel.WORK_UNIT)))
    assert len(machines) == 6, f"the pack seeds {len(machines)} machines, not six"
    assert receipt["equipment"]["made"] == 10, receipt
    released = session.scalar(select(WorkOrder).where(WorkOrder.code == ORDER))
    assert released is not None, "counter deltas have no operation to book against"
    assert released.status.value == "released"


def test_the_bottling_pack_releases_exactly_one_order_and_plans_the_rest():
    """A line runs one order at a time, so one is released and the others wait.

    Releasing the whole book would put ten orders on one routing and leave the
    MES choosing between them by priority - which is the thing decision 0029
    named as still inferred rather than read.
    """
    session, _ = by_the_pack()
    book = list(session.scalars(select(WorkOrder)))
    released = [o for o in book if o.status.value == "released"]
    planned = [o for o in book if o.status.value == "planned"]
    assert len(book) == len(released) + len(planned), "an order is in neither state"
    assert [o.code for o in released] == [ORDER]
    assert len(planned) == 9, f"the book plans {len(planned)} orders behind the released one"


def test_the_bottling_book_is_due_in_the_order_it_is_meant_to_be_run():
    """Every order has a due date, and no two share one.

    The floor releases the next order by priority, then due date, then code.
    A book whose due dates repeat leaves that choice to the code, and a book
    with none leaves it to the alphabet.
    """
    session, _ = by_the_pack()
    book = sorted(session.scalars(select(WorkOrder)), key=lambda o: o.code)
    assert all(o.due_date is not None for o in book), "an order in the book has no due date"
    dues = [o.due_date for o in book]
    assert dues == sorted(dues), "the book's due dates do not follow its codes"
    assert len(set(dues)) == len(dues), "two orders in the book are due at the same moment"


def test_the_bottling_book_holds_more_than_a_day_of_this_lines_work():
    """The point of the whole change. Six hours of a plant on 2026-09-18 left
    one order 70x over because the book held that one order; the book now
    holds more than the line can make in twenty-four hours at its rated rate,
    so a plant nobody watches overnight still has work it was actually given.
    """
    session, _ = by_the_pack()
    ordered = sum(o.quantity for o in session.scalars(select(WorkOrder)))
    slowest = max(m.cycle_seconds for m in load_tag_map(PACK / "tag_map.json"))
    an_hour = 3600.0 / slowest
    assert ordered / an_hour >= 24.0, (
        f"the book is {ordered:,.0f} units, which this line makes in "
        f"{ordered / an_hour:.1f}h at its rated {an_hour:,.0f}/h")


def test_every_station_takes_its_rated_cycle_time_from_the_tag_map():
    """Not repeated in the pack's own files. OEE performance is ideal cycle x
    count / runtime, so a rate here that drifted from the line that generated
    the data would produce a performance figure that means nothing."""
    written = json.loads((DATA / "equipment.json").read_text(encoding="utf-8"))
    assert not [row for row in written if "ideal_cycle_seconds" in row], (
        "a rated cycle time was written into the pack; it belongs in tag_map.json")
    session, _ = by_the_pack()
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(PACK / "tag_map.json")}
    for code, rate in cycles.items():
        machine = session.scalar(select(Equipment).where(Equipment.code == code))
        assert machine is not None and machine.ideal_cycle_seconds == rate


def test_applying_the_pack_twice_changes_nothing_and_says_so():
    session, first = by_the_pack()
    again = masterdata.seed(session, DATA,
                            {m.equipment: m.cycle_seconds
                             for m in load_tag_map(PACK / "tag_map.json")})
    for kind, counts in again.items():
        assert counts["made"] == 0, f"{kind} was seeded twice"
        assert counts["present"] == first[kind]["made"], kind


@pytest.mark.parametrize("kind", sorted(masterdata.KINDS))
def test_the_pack_declares_every_kind_this_plant_has(kind):
    """Nine files, and nine is the whole format. A kind added to the format
    that this pack cannot express would be a kind the reference line loses
    the next time somebody rebuilds it from here."""
    assert (DATA / f"{kind}.json").is_file(), f"{kind}.json is missing from the pack"


def test_the_pack_checks_out_offline():
    assert masterdata.problems(DATA) == []


def test_the_pack_puts_two_scales_on_the_filler_and_either_can_judge_the_fill_weight():
    """Two gauges per measured characteristic, because a plant with one has no
    way to ask whether the instrument or the process moved.

    The resolution matters as much as the existence: a gauge that resolves a
    third of the tolerance cannot judge it, and a fill weight held to twelve
    grams measured on a scale reading to a tenth of a gram can be argued with.
    """
    session, _ = by_the_pack()
    scales = sorted(session.scalars(select(Gauge).where(Gauge.kind == "scale")),
                    key=lambda g: g.code)
    assert [g.code for g in scales] == ["SCALE-FILL-01", "SCALE-FILL-02"]
    spec = session.scalar(select(QualitySpec).where(QualitySpec.characteristic == "fill_weight"))
    tolerance = spec.max_value - spec.min_value
    for gauge in scales:
        assert gauge.location == "FILL01"
        assert gauge.resolution is not None and gauge.resolution > 0
        assert tolerance / gauge.resolution >= 10, (
            f"{gauge.code} resolves {gauge.resolution} against a tolerance of {tolerance}")
        assert gauge.last_calibrated is not None, (
            f"{gauge.code} has never been calibrated, so every reading it takes is suspect")


def test_one_of_the_fillers_scales_is_nearly_due_for_calibration():
    """The pack is a plant, not a showroom. One scale was done last week and
    one is inside its warning window, which is what makes the calibration
    screen worth opening and is the state the drifting gauge story needs."""
    session, _ = by_the_pack()
    from fsmes.services import gauges as gauge_service

    register = {row["code"]: row for row in gauge_service.register_list(session)["gauges"]}
    assert register["SCALE-FILL-01"]["due_soon"] is False
    assert register["SCALE-FILL-02"]["due_soon"] is True, register["SCALE-FILL-02"]
    assert register["SCALE-FILL-02"]["overdue"] is False, "a gauge on the floor is in calibration"


# ------------------------------------------- what the jobs need of the line

def plan_rows() -> list[dict]:
    return json.loads((DATA / "maintenance_plans.json").read_text(encoding="utf-8"))


def floor() -> dict:
    return json.loads((PACK / "floor.json").read_text(encoding="utf-8"))


def test_every_plan_on_this_plant_says_whether_the_line_must_stop_and_why():
    """A plan that does not say is a plan the crew reads as "work while it
    runs", and on a filler that would be a mechanic with his hands inside a
    machine making bottles. Six plans, each with the field and each with the
    sentence beside it - the sentence because the field is a judgment about
    this plant's guarding and this plant's job, and a reader who disagrees
    with it has to be able to see what it was."""
    rows = plan_rows()
    assert len(rows) == 6, "six plans on this line; a seventh needs its own two fields"
    for row in rows:
        assert isinstance(row.get("needs_stop"), bool), (
            f"{row['code']} does not say whether the machine has to be stopped")
        assert row.get("_why_needs_stop"), f"{row['code']} says it without saying why"
        assert row.get("window") in ("anytime", "between_orders", "end_of_shift"), (
            f"{row['code']} names no window the product knows")
        assert row.get("_why_window"), f"{row['code']} names a window without saying why"


def test_the_crew_has_something_to_write_down_for_every_job_it_could_finish():
    """`findings` is what the mechanic wrote on the job, and an order closed
    with nothing on it is the row a maintenance report cannot use. The crew
    reads the list from `floor.json`, so the list has to name plans this
    plant actually has, and has to cover every one of them: a plan whose
    findings were forgotten would be the one job that closes blank."""
    codes = {row["code"] for row in plan_rows()}
    findings = floor()["maintenance"]["findings"]

    assert set(findings) == codes, (
        f"findings cover {sorted(set(findings))} and the plant has {sorted(codes)}")
    for code, lines in findings.items():
        assert len(lines) >= 2, f"{code} has one finding, so every replay writes the same one"
        for line in lines:
            assert line.strip() and not line.startswith("_"), code


def test_the_two_jobs_this_shift_is_expected_to_finish_are_jobs_a_replay_can_reach():
    """The pack states which plans a replayed shift should get through, and
    the statement is checkable rather than hopeful. Both halves of reachable:
    the trigger has to be one a compressed replay accrues - running hours are
    real hours out of the machine's own state history, so a 30x replay of a
    shift accrues sixteen minutes of them and no runtime plan ever comes due -
    and the job must not need the line stopped, because this shift's line
    never stands still long enough for the crew to touch it.
    """
    by_code = {row["code"]: row for row in plan_rows()}
    expected = floor()["maintenance"]["expected_done"]

    assert len(expected) >= 2, "the definition of done asks for two finished orders"
    for code in expected:
        plan = by_code[code]
        assert plan["trigger"] != "runtime_hours", (
            f"{code} is triggered on running hours, which a replay never accrues")
        assert plan["needs_stop"] is False, (
            f"{code} needs the line stopped, and this shift's line does not stop")
        assert plan["window"] == "anytime", f"{code} would have to wait for its window"


def test_the_order_the_chain_hangs_on_is_the_one_job_that_cannot_be_got_at():
    """Link 2 of the planted chain, as data. The condenser clean needs the
    filler isolated and waits for the gap between orders, and on this shift
    the next order is released as the last one finishes - so the order is
    raised in the first minutes and is still at `assigned` at the end. If
    this plan were ever made doable the chain would quietly lose a link, and
    the scorer would say 3/4 with nothing to point at."""
    chiller = {row["code"]: row for row in plan_rows()}["PM-FILL-CHILLER"]

    assert chiller["needs_stop"] is True
    assert chiller["window"] == "between_orders"
    assert chiller["trigger"] == "calendar_days", "it has to be due in the first pass"
    assert "PM-FILL-CHILLER" not in floor()["maintenance"]["expected_done"]


def test_the_pack_sends_a_crew_and_says_what_it_assumes_about_them():
    """Off by default in the product, on in this pack, and every number the
    crew reads stated here rather than defaulted in Python - the walk to the
    machine, how long a machine has to have been standing before a job that
    needs it stopped may start, and the reason code the stop is booked
    under. A pack that turned the crew on and left the rest to the code
    would be a plant whose behaviour is in the product again."""
    block = floor()["maintenance"]

    assert block["crew"] is True and block["_crew"]
    assert block["walk_s"] > 0 and block["_walk_s"]
    assert block["standing_s"] > 0 and block["_standing_s"]
    assert block["stop_reason"] and block["_stop_reason"]
