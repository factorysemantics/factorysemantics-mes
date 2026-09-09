"""Master data for the cutlery plant - what the serialising shop floor
(trace_driver.py) needs to exist before the API does.

One enterprise and site; an area per kind of line (Moulding, Stacking,
Wrapping, Palletizing); a work centre per line and a work unit per station;
the materials the pieces become (UT-FORK, UT-SPOON, UT-KNIFE, STACK-24, PACK,
PALLET) and the ones they consume (RAW-PP resin, RAW-PLATE); a routing per
line; and one released order per line with its raw lot issued to it, so
every piece can be traced back to a resin lot and every pack to a plate lot
through the order that made it.

Runs before the API exists, through the database, exactly as every plant's
init script does. Reads line.json and tag_map.json so it cannot disagree with
the line that generated the data.

    FSMES_PLANT_REGISTRY=labs/cutlery/registry.toml fsmes plant cutlery init
    python3 labs/cutlery/init.py          (with MES_DATABASE_URL already set)

One product gap is worked around here and recorded as a finding: an order's
operations are resolved from the routing's material, first routing wins, so
eight stackers making the same STACK-24 would all be given the first
stacker. Each stack order is pointed at its own stacker after creation. The
product cannot say "this order runs on this member of the cell" - the
late-binding dispatch that ARCHITECTURE.md names as not built.
"""
from __future__ import annotations

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
from fsmes.services import execution, workorders

HERE = Path(__file__).resolve().parent

ENTERPRISE = ("CUTLERYCO", "Cutlery Co.")
SITE = ("CW1", "Cutlery Works")

AREA_CODES = {"Moulding": "MOULD-AREA", "Stacking": "STACK-AREA",
              "Wrapping": "WRAP-AREA", "Palletizing": "PALLET-AREA"}

# Sized for a two-hour run at the nominal rate, so a scored hour never runs
# out of order and a standing instance at 1x has an hour of slack.
HOURS_OF_ORDER = 2.0


def _get_or_create(session: Session, code: str, **kwargs) -> Equipment:
    existing = session.scalar(select(Equipment).where(Equipment.code == code))
    if existing is not None:
        return existing
    equipment = Equipment(code=code, **kwargs)
    session.add(equipment)
    return equipment


def _material(session: Session, spec: dict, kind: MaterialType) -> Material:
    existing = session.scalar(select(Material).where(Material.code == spec["code"]))
    if existing is not None:
        return existing
    material = Material(code=spec["code"], name=spec["name"], unit=spec["unit"], type=kind)
    session.add(material)
    return material


def seed(session: Session, scenario_dir: Path = HERE) -> dict | None:
    """Populate the plant. Returns what was made, or None if it already exists."""
    if session.scalar(select(Equipment.id).where(Equipment.code == ENTERPRISE[0])):
        return None

    factory = json.loads((scenario_dir / "line.json").read_text(encoding="utf-8"))
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(scenario_dir / "tag_map.json")}

    enterprise = _get_or_create(session, ENTERPRISE[0], name=ENTERPRISE[1], level=EquipmentLevel.ENTERPRISE)
    site = _get_or_create(session, SITE[0], name=SITE[1], level=EquipmentLevel.SITE, parent=enterprise)

    orders: list[dict] = []
    for line_cfg in factory["lines"]:
        name, meta = line_cfg["name"], line_cfg["_meta"]
        area = _get_or_create(session, AREA_CODES[meta["area"]], name=meta["area"],
                              level=EquipmentLevel.AREA, parent=site)
        centre = Equipment(code=f"{name}LINE", name=f"{meta['area']} line {name}",
                           level=EquipmentLevel.WORK_CENTER, parent=area)
        session.add(centre)
        stations: dict[str, Equipment] = {}
        for station in line_cfg["stations"]:
            code = f"{name}_{station['name']}"
            if code not in cycles:
                raise ValueError(f"tag_map.json has no entry for {code}; the seed and the tag map disagree.")
            stations[code] = Equipment(code=code, name=f"{name} {station['name']}",
                                       level=EquipmentLevel.WORK_UNIT, parent=centre,
                                       ideal_cycle_seconds=cycles[code])
        session.add_all(stations.values())

        kind = meta["kind"]
        product = _material(session, meta["material"],
                            MaterialType.FINISHED if kind in ("wrapper", "palletizer") else MaterialType.INTERMEDIATE)
        # The dimensional checks a person records every fifteen minutes on
        # this utensil - the characteristics the pallet certificate states a
        # Cpk for. Declared once per material, whichever of its lines seeds first.
        for char, unit, lo, hi, _nominal in meta.get("dimensional_specs", []):
            exists = session.scalar(select(QualitySpec.id).join(Material, QualitySpec.material_id == Material.id)
                                    .where(Material.code == product.code, QualitySpec.characteristic == char))
            if not exists:
                session.add(QualitySpec(material=product, characteristic=char, unit=unit, min_value=lo, max_value=hi))
        routing = Routing(code=f"RT-{name}", name=f"{name} routing", material=product)
        routing.operations = [
            RoutingOperation(seq=(i + 1) * 10, name=station["name"],
                             equipment=stations[f"{name}_{station['name']}"])
            for i, station in enumerate(line_cfg["stations"])
        ]
        session.add(routing)

        # What each line consumes, as a lot issued to its order.
        lot = None
        if kind == "utensil":
            resin = _material(session, meta["raw"], MaterialType.RAW)
            lot = MaterialLot(code=f"LOT-PP-{name}-001", material=resin, quantity=40000, original_quantity=40000)
        elif kind == "wrapper":
            plates = _material(session, meta["plate"], MaterialType.RAW)
            lot = MaterialLot(code=f"LOT-PLATE-{name}-001", material=plates, quantity=200000,
                              original_quantity=200000)
        elif kind == "palletizer":
            lot = None
        if lot is not None:
            session.add(lot)

        last = line_cfg["stations"][-1]
        per_hour = last["rate_per_min"] * 60
        # Resin at 4.5 g a piece; one plate a wrap. Lots are issued in full to
        # the order so every piece traces to a lot through it.
        lot_qty = per_hour * HOURS_OF_ORDER * (0.0045 if kind == "utensil" else 1.0)
        orders.append({"code": f"WO-{name}", "material": product.code, "line": name,
                       "quantity": int(per_hour * HOURS_OF_ORDER), "station": f"{name}_{last['name']}",
                       "lot": lot.code if lot else None, "lot_qty": lot_qty})
    session.flush()
    return {"orders": orders, "lines": len(factory["lines"]),
            "stations": sum(len(ln["stations"]) for ln in factory["lines"])}


def release_orders(session: Session, orders: list[dict]) -> None:
    """One released order per line, pointed at that line's own machines and
    with its raw lot issued, so the counters book and the pieces trace."""
    for spec in orders:
        wo = workorders.create(session, code=spec["code"], material_code=spec["material"],
                               quantity=spec["quantity"], priority=10, actor="lab-seed")
        # Same material, several lines: the routing chosen was the first for
        # the material, so point this order's operations at its own line.
        own = {e.code: e for e in session.scalars(
            select(Equipment).where(Equipment.code.like(f"{spec['line']}_%"),
                                    Equipment.level == EquipmentLevel.WORK_UNIT))}
        for op in wo.operations:
            wanted = own.get(f"{spec['line']}_{op.name}")
            if wanted is not None and op.equipment_id != wanted.id:
                op.equipment_id = wanted.id
        first = min(wo.operations, key=lambda o: o.seq)
        centre = session.get(Equipment, first.equipment_id).parent
        wo.work_center_id = centre.id if centre else None
        session.flush()
        workorders.release(session, spec["code"], actor="lab-seed")
        if spec["lot"]:
            execution.consume(session, order_code=spec["code"], lot_code=spec["lot"],
                              quantity=round(spec["lot_qty"], 1), seq=10, actor="lab-seed")


def main(scenario_dir: Path = HERE) -> None:
    with session_scope() as session:
        made = seed(session, scenario_dir)
        if made is None:
            print("The cutlery plant is already seeded - nothing to do.")
            return
        release_orders(session, made["orders"])
    print(f"Seeded the cutlery plant: {made['lines']} lines, {made['stations']} stations, "
          f"{len(made['orders'])} orders released with their lots issued.")


if __name__ == "__main__":
    main()
