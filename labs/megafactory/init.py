"""Master data for the mega-factory — one shared enterprise/site, an area and
a work centre per line, a work unit per station, a raw and a finished
material and a routing per line, and one released order per line.

Deliberately NOT in src/fsmes/: seeding a specific scenario's master data is
plant data, matching labs/multiplant/machining/seed.py's precedent,
generalized from one line to N so N lines' worth of equipment/material/
routing is a loop, not N hand-typed blocks.

Reads line.json and tag_map.json rather than repeating rates/stations here,
so the seed can never disagree with the line that generated the data (the
same rule seed.py already follows for Northgate). The spike (this directory)
carries its line metadata in LINE_META below; the full factory
(full/line.json) carries it inside each line's `_meta` block, which wins when
present.

Runs before the API exists, through the database, exactly as every plant's
init script does - the runner starts the OPC agent against a tag map whose
equipment must already be there. Everything the API *can* reach (workforce,
shift patterns, quality specifications, inspections) is seeded through it
instead: see seed_breadth.py.

Run through a scored run's init step (see run_spike.py / registry.toml), or
directly, with MES_DATABASE_URL already set:

    python3 labs/megafactory/init.py
"""
import json
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

ENTERPRISE = ("MEGAFACTORY", "Mega-Factory")
SITE = ("MF1", "Mega-Factory Works")

# Spike only. line name -> (area code, area name, finished-good code/name/unit,
#                          baseline spec characteristic/unit/min/max).
# The FILL baseline spec (494-506g) mirrors the real bottling plant's fill
# spec exactly, so the washer-drift effect already in line.json (base 500,
# -9g offset -> ~491) reliably fails it - the same causal story the "washer
# story" eval proved, reused here as one of several factory lines rather
# than the whole scenario.
LINE_META = {
    "FILL": ("FILL-AREA", "Filling", "FG-BOTTLE2", "Bottled Product", "ea",
             "FillWeight", "g", 494.0, 506.0),
    "MACH": ("MACH-AREA", "Machining", "FG-BRACKET2", "Machined Part", "ea",
             "SpindleTemp", "C", 40.0, 65.0),
    "ASSEM": ("ASSEM-AREA", "Assembly", "FG-ASSY", "Assembled Unit", "ea",
              "TorqueLevel", "Nm", 5.0, 12.0),
    "WELD": ("WELD-AREA", "Welding", "FG-WELDMENT", "Weldment", "ea",
             "ArcCurrent", "A", 150.0, 220.0),
    "EXTRUDE": ("EXTRUDE-AREA", "Extrusion", "FG-PROFILE", "Extruded Profile", "m",
                "Thickness", "mm", 1.8, 2.2),
}


def line_meta(line_cfg: dict) -> dict:
    """One shape for both scenarios: area code/name, material code/name/unit,
    and an optional baseline spec (the spike's, seeded here because the spike
    predates seed_breadth's API path for specifications)."""
    meta = line_cfg.get("_meta")
    if meta:
        # Instances of one template share an area (Filling holds FILL1..3);
        # each line is its own work centre beneath it.
        return {
            "area_code": f"{meta['template']}-AREA", "area_name": meta["area"],
            "material": meta["material"], "baseline_spec": None,
        }
    area_code, area_name, mat_code, mat_name, mat_unit, char, unit, lo, hi = LINE_META[line_cfg["name"]]
    return {
        "area_code": area_code, "area_name": area_name,
        "material": {"code": mat_code, "name": mat_name, "unit": mat_unit},
        "baseline_spec": (char, unit, lo, hi),
    }


def _get_or_create(session: Session, code: str, **kwargs) -> Equipment:
    existing = session.scalar(select(Equipment).where(Equipment.code == code))
    if existing is not None:
        return existing
    equipment = Equipment(code=code, **kwargs)
    session.add(equipment)
    return equipment


def seed(session: Session, scenario_dir: Path = HERE) -> bool:
    """Populate the mega-factory. Returns False if it is already there."""
    if session.scalar(select(Equipment.id).where(Equipment.code == ENTERPRISE[0])):
        return False

    factory = json.loads((scenario_dir / "line.json").read_text(encoding="utf-8"))
    tag_map = scenario_dir / "tag_map.json"
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(tag_map)}

    enterprise = _get_or_create(session, ENTERPRISE[0], name=ENTERPRISE[1],
                                 level=EquipmentLevel.ENTERPRISE)
    site = _get_or_create(session, SITE[0], name=SITE[1],
                           level=EquipmentLevel.SITE, parent=enterprise)

    for line_cfg in factory["lines"]:
        line_name = line_cfg["name"]
        meta = line_meta(line_cfg)
        mat = meta["material"]

        area = _get_or_create(session, meta["area_code"], name=meta["area_name"],
                               level=EquipmentLevel.AREA, parent=site)
        work_center = Equipment(code=f"{line_name}LINE", name=f"{meta['area_name']} line {line_name}",
                                 level=EquipmentLevel.WORK_CENTER, parent=area)
        session.add(work_center)

        stations: dict[str, Equipment] = {}
        for station in line_cfg["stations"]:
            code = f"{line_name}_{station['name']}"
            cycle = cycles.get(code)
            if cycle is None:
                raise ValueError(f"{tag_map} has no entry for {code}; the seed and the tag map disagree.")
            stations[code] = Equipment(
                code=code, name=f"{line_name} {station['name']}",
                level=EquipmentLevel.WORK_UNIT, parent=work_center,
                ideal_cycle_seconds=cycle,
            )
        session.add_all(stations.values())

        raw = Material(code=f"RAW-{line_name}", name=f"{meta['area_name']} raw input",
                        unit=mat["unit"], type=MaterialType.RAW)
        finished = Material(code=mat["code"], name=mat["name"], unit=mat["unit"],
                            type=MaterialType.FINISHED)
        routing = Routing(code=f"RT-{line_name}", name=f"{meta['area_name']} routing", material=finished)
        routing.operations = [
            RoutingOperation(seq=(i + 1) * 10, name=station["name"],
                             equipment=stations[f"{line_name}_{station['name']}"])
            for i, station in enumerate(line_cfg["stations"])
        ]
        rows = [raw, finished, routing,
                MaterialLot(code=f"LOT-{line_name}-001", material=raw,
                            quantity=5000, original_quantity=5000)]
        if meta["baseline_spec"]:
            char, unit, lo, hi = meta["baseline_spec"]
            rows.append(QualitySpec(material=finished, characteristic=char, unit=unit,
                                    min_value=lo, max_value=hi))
        session.add_all(rows)
    return True


def main(scenario_dir: Path = HERE) -> None:
    with session_scope() as session:
        created = seed(session, scenario_dir)
        if not created:
            print("Mega-factory is already seeded — nothing to do.")
            return
        session.flush()
        factory = json.loads((scenario_dir / "line.json").read_text(encoding="utf-8"))
        for line_cfg in factory["lines"]:
            line_name = line_cfg["name"]
            mat_code = line_meta(line_cfg)["material"]["code"]
            code = f"WO-MF-{line_name}"
            workorders.create(session, code=code, material_code=mat_code,
                              quantity=1000, priority=10, actor="lab-seed")
            workorders.release(session, code, actor="lab-seed")
        n_lines = len(factory["lines"])
        n_stations = sum(len(ln["stations"]) for ln in factory["lines"])

    print(f"Seeded the mega-factory: {n_lines} lines, {n_stations} stations, "
          f"{n_lines} routings, {n_lines} orders released.")


if __name__ == "__main__":
    main()
