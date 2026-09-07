"""Master data for Northgate Machining — the small plant in the multi-plant lab.

    Saw -> Mill -> Deburr

Deliberately NOT in `src/fsmes/`. Seeding a specific customer's plant is
plant data, not product code, and the whole point of this lab is to show one
unchanged MES serving two plants that agree on nothing. The moment a second
plant needs a `seed_northgate.py` shipped inside the product, the product has
a tenant literal in it.

Rated cycle times are read from the tag map rather than repeated here, for the
same reason `seed_kepsim` does it: OEE performance is
ideal_cycle_seconds x count / runtime, so a rate that disagrees with the line
that generated the data produces a performance figure that means nothing.

Run it through the plant launcher, which sets MES_DATABASE_URL first:

    .\fsplant.ps1 machining init
"""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.db import session_scope
from fsmes.domain import (
    Equipment,
    EquipmentLevel,
    Material,
    MaterialLot,
    MaterialType,
    QualitySpec,
    Routing,
    RoutingOperation,
)
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.services import workorders

HERE = Path(__file__).parent
TAG_MAP = HERE / "tag_map.json"

LINE_CODE = "NGLINE"

# code -> (display name, routing step name), in line order.
STATIONS = [
    ("SAW01", "Bar Saw 01", "Cut"),
    ("MILL01", "CNC Mill 01", "Machine"),
    ("DEB01", "Deburr Cell 01", "Deburr"),
]


def _get_or_create(session: Session, code: str, **kwargs) -> Equipment:
    existing = session.scalar(select(Equipment).where(Equipment.code == code))
    if existing is not None:
        return existing
    equipment = Equipment(code=code, **kwargs)
    session.add(equipment)
    return equipment


def seed(session: Session) -> bool:
    """Populate Northgate. Returns False if it is already there."""
    if session.scalar(select(Equipment.id).where(Equipment.code == LINE_CODE)):
        return False

    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(TAG_MAP)}
    missing = [code for code, _, _ in STATIONS if code not in cycles]
    if missing:
        raise ValueError(f"{TAG_MAP} has no entry for {missing}; the seed and the tag map disagree.")

    enterprise = _get_or_create(
        session, "NORTHGATE", name="Northgate Machining", level=EquipmentLevel.ENTERPRISE
    )
    site = _get_or_create(
        session, "NG1", name="Northgate Works", level=EquipmentLevel.SITE, parent=enterprise
    )
    area = _get_or_create(
        session, "MACH", name="Machine Shop", level=EquipmentLevel.AREA, parent=site
    )
    line = Equipment(
        code=LINE_CODE, name="Cell A", level=EquipmentLevel.WORK_CENTER, parent=area
    )
    session.add(line)

    stations = {
        code: Equipment(
            code=code,
            name=name,
            level=EquipmentLevel.WORK_UNIT,
            parent=line,
            ideal_cycle_seconds=cycles[code],
        )
        for code, name, _ in STATIONS
    }
    session.add_all(stations.values())

    bar = Material(code="RAW-BAR-40", name="Steel Bar 40mm", unit="m", type=MaterialType.RAW)
    bracket = Material(
        code="FG-BRACKET", name="Machined Bracket", unit="ea", type=MaterialType.FINISHED
    )

    routing = Routing(code="RT-BRACKET", name="Cut, Machine and Deburr Bracket", material=bracket)
    routing.operations = [
        RoutingOperation(seq=(i + 1) * 10, name=step, equipment=stations[code])
        for i, (code, _, step) in enumerate(STATIONS)
    ]

    session.add_all(
        [
            bar,
            bracket,
            routing,
            # The mill's spindle drifts to 74 C before it fails at t+2700s, so this
            # spec catches the excursion rather than never firing.
            QualitySpec(
                material=bracket,
                characteristic="spindle_temp",
                unit="C",
                min_value=50.0,
                max_value=70.0,
            ),
            MaterialLot(
                code="LOT-BAR-001", material=bar, quantity=5000, original_quantity=5000
            ),
        ]
    )
    return True


def main() -> None:
    with session_scope() as session:
        created = seed(session)
        if not created:
            print("Northgate is already seeded — nothing to do.")
            return

        # An order, released, so counter deltas have an operation to book against.
        # Without this the agent records states and tag history but books no
        # production, and the dashboard looks broken when it is merely idle.
        session.flush()
        workorders.create(
            session,
            code="WO-NG-7001",
            material_code="FG-BRACKET",
            quantity=600,
            priority=10,
            actor="lab-seed",
        )
        workorders.release(session, "WO-NG-7001", actor="lab-seed")

    print("Seeded Northgate Machining: 3 stations, routing RT-BRACKET, order WO-NG-7001 released.")


if __name__ == "__main__":
    main()
