"""Master data for any line, straight from its tag map.

`seed_kepsim` hard-codes the six simulated stations. This is its generalized
sibling for a real plant: point it at the tag map the worksheet produced and
it creates exactly the equipment that map describes — the stations in worksheet
order, each with its display name and rated cycle time — under whatever
site/area names fit the plant.

Why seeding matters at all: the agent can only attach tag history, states and
production to equipment rows that exist. No rows, no facts.

The routing is optional but recommended. With it, releasing one work order
lets counter deltas book production and OEE quality/performance mean
something. Without it (--no-routing) the MES is a pure observer: states,
downtime and tag trends still record; production does not book.

Cycle times are read from the raw JSON rather than the loaded map, because the
loader supplies a default for machines whose worksheet left the cell blank —
and a default cycle time would quietly turn OEE performance into fiction.
Blank stays blank; performance reads unknown; the number gets filled in when
Engineering knows it.
"""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain import (
    Equipment,
    EquipmentLevel,
    Material,
    MaterialType,
    Routing,
    RoutingOperation,
)
from fsmes.integrations.opc.tag_map import load_tag_map


def _get_or_create(session: Session, code: str, **kwargs) -> Equipment:
    existing = session.scalar(select(Equipment).where(Equipment.code == code))
    if existing is not None:
        return existing
    equipment = Equipment(code=code, **kwargs)
    session.add(equipment)
    return equipment


def seed_line_from_tag_map(
    session: Session,
    tag_map: Path,
    *,
    line_code: str,
    line_name: str | None = None,
    enterprise_code: str = "PLANT",
    enterprise_name: str = "Plant",
    site_code: str = "SITE1",
    site_name: str = "Main Site",
    area_code: str = "PROD",
    area_name: str = "Production",
    with_routing: bool = True,
) -> bool:
    """Create the line the tag map describes. Returns False if it already exists.

    Idempotent, and shares the hierarchy with anything already seeded: existing
    enterprise/site/area rows are reused, so several lines can live side by side.
    """
    if session.scalar(select(Equipment.id).where(Equipment.code == line_code)):
        return False

    machines = load_tag_map(tag_map)  # validation: a broken map fails here, before any rows
    raw = {
        m["equipment"]: m for m in json.loads(Path(tag_map).read_text(encoding="utf-8"))["machines"]
    }

    enterprise = _get_or_create(session, enterprise_code, name=enterprise_name, level=EquipmentLevel.ENTERPRISE)
    site = _get_or_create(session, site_code, name=site_name, level=EquipmentLevel.SITE, parent=enterprise)
    area = _get_or_create(session, area_code, name=area_name, level=EquipmentLevel.AREA, parent=site)
    line = Equipment(
        code=line_code, name=line_name or line_code, level=EquipmentLevel.WORK_CENTER, parent=area
    )
    session.add(line)

    stations: list[Equipment] = []
    for spec in machines:
        entry = raw[spec.equipment]
        station = Equipment(
            code=spec.equipment,
            name=spec.name or spec.equipment,
            level=EquipmentLevel.WORK_UNIT,
            parent=line,
            # Only when the worksheet actually said so — see the module docstring.
            ideal_cycle_seconds=float(entry["cycle_seconds"]) if "cycle_seconds" in entry else None,
        )
        session.add(station)
        stations.append(station)

    if with_routing:
        material = session.scalar(select(Material).where(Material.code == f"FG-{line_code}"))
        if material is None:
            material = Material(
                code=f"FG-{line_code}",
                name=f"{line_name or line_code} output",
                unit="ea",
                type=MaterialType.FINISHED,
            )
            session.add(material)
        routing = Routing(code=f"RT-{line_code}", name=f"{line_name or line_code} routing", material=material)
        routing.operations = [
            RoutingOperation(seq=(index + 1) * 10, name=station.name, equipment=station)
            for index, station in enumerate(stations)
        ]
        session.add(routing)

    return True
