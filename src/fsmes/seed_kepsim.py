"""Master data for the KepSim six-station line (labs/kepsim).

    LD -> RD -> Washer -> QI -> Refill -> Palletiser

Seeding this is what turns the replayed tag values into MES facts: without
equipment rows the agent has nothing to attach tag history or states to, and
without a routing there is no operation for counter deltas to book against.

Rated cycle times are read from the tag map rather than repeated here. They are
not decoration — OEE performance is ideal_cycle_seconds x count / runtime, so a
station whose rated rate disagrees with the line that generated the data will
report a performance figure that means nothing.
"""

from datetime import time as dt_time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain import (
    BomItem,
    Equipment,
    EquipmentLevel,
    MaintenancePlan,
    Material,
    MaterialLot,
    MaterialType,
    QualitySpec,
    Routing,
    RoutingOperation,
    ShiftPattern,
    TriggerKind,
)
from fsmes.integrations.opc.tag_map import load_tag_map

TAG_MAP = Path("config/tag_map_kepsim.json")

# code -> (display name, routing step name), in line order.
STATIONS = [
    ("LD01", "Loader / Depalletiser 01", "Load"),
    ("RD01", "Rotary Denester 01", "Denest"),
    ("WASH01", "Washer 01", "Wash"),
    ("QI01", "Quality Inspection 01", "Inspect"),
    ("FILL01", "Refill Station 01", "Fill"),
    ("PAL01", "Palletiser 01", "Palletise"),
]

LINE_CODE = "SIMLINE"


def _get_or_create(session: Session, code: str, **kwargs) -> Equipment:
    existing = session.scalar(select(Equipment).where(Equipment.code == code))
    if existing is not None:
        return existing
    equipment = Equipment(code=code, **kwargs)
    session.add(equipment)
    return equipment


def seed_kepsim_line(session: Session, tag_map: Path | None = None) -> bool:
    """Populate the simulated line. Returns False if it is already there.

    Safe to run alongside seed_demo_plant: it reuses the enterprise/site/area
    if they exist and creates them if they do not, so either order works and
    running it twice changes nothing.
    """
    if session.scalar(select(Equipment.id).where(Equipment.code == LINE_CODE)):
        return False

    path = Path(tag_map or TAG_MAP)
    if not path.exists():
        raise FileNotFoundError(f"Tag map {path} not found — rated cycle times come from it.")
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(path)}
    missing = [code for code, _, _ in STATIONS if code not in cycles]
    if missing:
        raise ValueError(f"{path} has no entry for {missing}; the seed and the tag map disagree.")

    enterprise = _get_or_create(session, "ACME", name="ACME Beverages", level=EquipmentLevel.ENTERPRISE)
    site = _get_or_create(session, "KC1", name="Kansas City Plant", level=EquipmentLevel.SITE, parent=enterprise)
    area = _get_or_create(session, "PKG", name="Packaging", level=EquipmentLevel.AREA, parent=site)
    line = Equipment(code=LINE_CODE, name="KepSim Simulated Line", level=EquipmentLevel.WORK_CENTER, parent=area)
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

    preform = Material(code="RAW-PREFORM", name="Bottle Preform", unit="ea", type=MaterialType.RAW)
    water = Material(code="RAW-WATER", name="Treated Water", unit="l", type=MaterialType.RAW)
    cap = Material(code="RAW-CAP", name="28mm Closure", unit="ea", type=MaterialType.RAW)
    label = Material(code="RAW-LABEL", name="Wrap Label", unit="ea", type=MaterialType.RAW)
    carton = Material(code="RAW-CARTON", name="Shipper Carton (24)", unit="ea",
                      type=MaterialType.RAW)
    bottle = Material(code="FG-BOTTLE", name="Filled Bottle 500ml", unit="ea", type=MaterialType.FINISHED)

    routing = Routing(code="RT-BOTTLE", name="Fill and Palletise Bottles", material=bottle)
    routing.operations = [
        RoutingOperation(seq=(i + 1) * 10, name=step, equipment=stations[code])
        for i, (code, _, step) in enumerate(STATIONS)
    ]

    session.add_all(
        [
            preform,
            water,
            cap,
            label,
            carton,
            bottle,
            # Each component names the routing step that consumes it. A line
            # takes different material at different machines, and a BOM that
            # cannot say which cannot tell an operator what to stage where.
            BomItem(parent=bottle, component=preform, quantity=1.0, operation_seq=10),
            BomItem(parent=bottle, component=water, quantity=0.5, operation_seq=50),
            BomItem(parent=bottle, component=cap, quantity=1.0, operation_seq=50),
            BomItem(parent=bottle, component=label, quantity=1.0, operation_seq=40),
            # 24 bottles to a case.
            BomItem(parent=bottle, component=carton, quantity=1 / 24, operation_seq=60),
            routing,
            # The generated line runs a fill weight around 500 g with occasional
            # excursions, so this spec catches something real rather than never firing.
            QualitySpec(material=bottle, characteristic="fill_weight", unit="g", min_value=494.0, max_value=506.0),
            MaterialLot(code="LOT-PREFORM-001", material=preform, quantity=100000, original_quantity=100000),
            MaterialLot(code="LOT-WATER-001", material=water, quantity=50000, original_quantity=50000),
            MaterialLot(code="LOT-CAP-001", material=cap, quantity=100000,
                        original_quantity=100000),
            MaterialLot(code="LOT-LABEL-001", material=label, quantity=100000,
                        original_quantity=100000),
            # Cartons run thinner than everything else on purpose: a line that
            # never runs short of anything teaches nobody how a shortage looks.
            MaterialLot(code="LOT-CARTON-001", material=carton, quantity=900,
                        original_quantity=900),
        ]
    )

    # --- maintenance programme ---------------------------------------------
    # Intervals are deliberately short so a demonstration line actually comes
    # due within a shift; a real filler is not serviced every four hours. Each
    # plan is triggered by what the machine has *done* - hours run, units made
    # - except the palletiser grease, which is genuinely a calendar job.
    plans = [
        ("PM-FILL-SEALS", "Replace filler valve seals", "FILL01",
         TriggerKind.RUNTIME_HOURS, 4.0, 45.0),
        ("PM-LD-BELT", "Inspect and tension loader belt", "LD01",
         TriggerKind.RUNTIME_HOURS, 6.0, 20.0),
        ("PM-WASH-NOZZLE", "Descale washer nozzles", "WASH01",
         TriggerKind.PRODUCED_QTY, 20000.0, 60.0),
        ("PM-PAL-GREASE", "Grease palletiser arm", "PAL01",
         TriggerKind.CALENDAR_DAYS, 7.0, 30.0),
        ("PM-RD-BEARING", "Check denester bearing play", "RD01",
         TriggerKind.RUNTIME_HOURS, 8.0, 90.0),
    ]
    session.add_all([
        # By relationship rather than id: these stations may not be flushed
        # yet, and depending on flush order is how a seeder breaks the first
        # time somebody reorders it.
        MaintenancePlan(code=code, name=name, equipment=stations[eq],
                        trigger=trigger, interval=interval,
                        expected_minutes=minutes)
        for code, name, eq, trigger, interval, minutes in plans
        if eq in stations
    ])
    session.flush()

    # --- the plant calendar -----------------------------------------------
    # Two shifts, seven days: the pattern Scott is designing for. Nights cross
    # midnight, which every date calculation has to survive.
    session.add_all([
        ShiftPattern(code="DAY", name="Day shift",
                     starts=dt_time(6, 0), ends=dt_time(18, 0), days="1111111"),
        ShiftPattern(code="NIGHT", name="Night shift",
                     starts=dt_time(18, 0), ends=dt_time(6, 0), days="1111111"),
    ])
    session.flush()
    return True
