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
