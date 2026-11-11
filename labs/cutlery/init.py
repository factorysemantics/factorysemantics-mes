"""Master data for the cutlery plant - what the serialising shop floor
(trace_driver.py) needs to exist before the API does.

One enterprise and site; an area per kind of line (Moulding, Stacking,
Wrapping, Palletizing); a work centre per line and a work unit per station;
the materials the pieces become (UT-FORK, UT-SPOON, UT-KNIFE, STACK-24, PACK,
PALLET) and the ones they consume (RAW-PP resin, RAW-PLATE); a routing per
line; and two orders per line - one released with its raw lot issued to it,
so every piece can be traced back to a resin lot and every pack to a plate
lot through the order that made it, and one planned behind it, so the line
has a next order the moment the first is done.

The codes are numbered per line, `WO-FORK1-0001` then `WO-FORK1-0002`, and
that is not decoration: the simulated planner (`planning.each_line` in
floor.json) reads each line's sequence back from the book and keeps one
planned order behind it for as long as the plant runs, and the supervisor
releases it and issues it the line's lot when the order before it is done.
The lots are sized for weeks of line time so that holds for days, not hours.

Runs before the API exists, through the database, exactly as every plant's
init script does. Reads line.json and tag_map.json so it cannot disagree with
the line that generated the data.

    FSMES_PLANT_REGISTRY=labs/cutlery/registry.toml fsmes plant cutlery init
    python3 labs/cutlery/init.py          (with MES_DATABASE_URL already set)
    python3 labs/cutlery/init.py <dir>    the line.json and tag_map.json in <dir>
                                          (a plant built with build_config.py --out)

Thirty-two stackers make the same STACK-24, so every order names the line
that makes it and the plant builds it from that line's routing. Which member
of a cell runs an order is still decided at planning, not at dispatch - the
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
# How much line time a line's raw lot holds. The lot stays with the line from
# one order to the next, so it is sized for the plant's life rather than for
# one order: thirty days at the nominal rate, at whatever scale the line was
# built.
DAYS_OF_LOT = 30.0
# The orders seeded per line: one released, the rest planned behind it.
ORDERS_PER_LINE = 2


def _get_or_create(session: Session, code: str, **kwargs) -> Equipment:
    existing = session.scalar(select(Equipment).where(Equipment.code == code))
    if existing is not None:
        return existing
    equipment = Equipment(code=code, **kwargs)
    session.add(equipment)
    return equipment


def _material(session: Session, spec: dict, kind: MaterialType,
              counted_in_pieces: bool = False) -> Material:
    existing = session.scalar(select(Material).where(Material.code == spec["code"]))
    if existing is not None:
        return existing
    material = Material(code=spec["code"], name=spec["name"], unit=spec["unit"], type=kind,
                        # Whether a pallet certificate counts this material in
                        # pieces. The utensils are the pieces on this line, and
                        # that is a fact about the line rather than about the
                        # shape of their codes - which is what the product used
                        # to read it from.
                        counted_in_pieces=counted_in_pieces)
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
                            MaterialType.FINISHED if kind in ("wrapper", "palletizer") else MaterialType.INTERMEDIATE,
                            counted_in_pieces=(kind == "utensil"))
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

        # What each line consumes, as a lot issued to its orders. Resin at
        # 4.5 g a piece; one plate a wrap.
        last = line_cfg["stations"][-1]
        per_hour = last["rate_per_min"] * 60
        per_piece = 0.0045 if kind == "utensil" else 1.0
        lot_qty = round(per_hour * 24 * DAYS_OF_LOT * per_piece)
        lot = None
        if kind == "utensil":
            resin = _material(session, meta["raw"], MaterialType.RAW)
            lot = MaterialLot(code=f"LOT-PP-{name}-001", material=resin, quantity=lot_qty,
                              original_quantity=lot_qty)
        elif kind == "wrapper":
            plates = _material(session, meta["plate"], MaterialType.RAW)
            lot = MaterialLot(code=f"LOT-PLATE-{name}-001", material=plates, quantity=lot_qty,
                              original_quantity=lot_qty)
        if lot is not None:
            session.add(lot)

        # The first order is issued what it will use; the ones behind it are
        # issued theirs when they are released.
        for n in range(1, ORDERS_PER_LINE + 1):
            orders.append({"code": f"WO-{name}-{n:04d}", "material": product.code, "line": name,
                           "centre": centre.code, "quantity": int(per_hour * HOURS_OF_ORDER),
                           "release": n == 1, "lot": lot.code if lot else None,
                           "lot_qty": per_hour * HOURS_OF_ORDER * per_piece})
    session.flush()
    return {"orders": orders, "lines": len(factory["lines"]),
            "stations": sum(len(ln["stations"]) for ln in factory["lines"])}


def release_orders(session: Session, orders: list[dict]) -> None:
    """Every order on its own line; the first on each released with its raw
    lot issued, so the counters book and the pieces trace, and the rest left
    planned behind it."""
    for spec in orders:
        workorders.create(session, code=spec["code"], material_code=spec["material"],
                          quantity=spec["quantity"], priority=10, line_code=spec["centre"],
                          actor="lab-seed")
        session.flush()
        if not spec["release"]:
            continue
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
    released = sum(1 for o in made["orders"] if o["release"])
    print(f"Seeded the cutlery plant: {made['lines']} lines, {made['stations']} stations, "
          f"{len(made['orders'])} orders - {released} released with their lots issued, "
          f"{len(made['orders']) - released} planned behind them.")


if __name__ == "__main__":
    import sys

    main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE)
