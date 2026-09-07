"""What each station needs, what it has had, and where it will run short.

This is the question a BOM-by-operation exists to answer, and the one nobody
could ask before: not "what does this order need" but "what does the washer
need, and will it last the shift".
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain import BomItem, LotConsumption, Material, MaterialLot
from fsmes.domain.execution import LotStatus
from fsmes.services import workorders


def bom_for(session: Session, material_code: str) -> list[dict]:
    """Every component of a material, with the station that consumes it."""
    material = session.scalar(select(Material).where(Material.code == material_code))
    if material is None:
        return []
    return [
        {"component": item.component.code, "name": item.component.name,
         "per_unit": item.quantity, "seq": item.operation_seq}
        for item in sorted(session.scalars(
            select(BomItem).where(BomItem.parent_id == material.id)),
            key=lambda i: (i.operation_seq if i.operation_seq is not None else 9999,
                           i.component.code))
    ]


def staging(session: Session, order_code: str) -> dict:
    """Per station: what this order needs there, what has been issued, and
    what is left in stock.

    Shortfall is computed against the order's *remaining* quantity rather than
    its total, because a station that has already run most of the order does
    not need the whole bill again - and telling a supervisor otherwise sends
    them chasing material they do not need.
    """
    order = workorders.get(session, order_code)
    remaining = max(0.0, order.quantity - (order.good_qty or 0.0))

    issued: dict[tuple[str, int | None], float] = {}
    for c in session.scalars(
        select(LotConsumption).where(LotConsumption.work_order_id == order.id)
    ):
        key = (c.lot.material.code, c.operation.seq if c.operation else None)
        issued[key] = issued.get(key, 0.0) + c.quantity

    on_hand: dict[str, float] = {}
    for lot in session.scalars(
        select(MaterialLot).where(MaterialLot.status == LotStatus.AVAILABLE)
    ):
        on_hand[lot.material.code] = on_hand.get(lot.material.code, 0.0) + lot.quantity

    by_station: dict[int | None, dict] = {}
    for line in bom_for(session, order.material.code):
        seq = line["seq"]
        station = by_station.setdefault(seq, {
            "seq": seq,
            "operation": None,
            "equipment": None,
            "components": [],
        })
        if seq is not None:
            op = next((o for o in order.operations if o.seq == seq), None)
            if op is not None:
                station["operation"] = op.name
                station["equipment"] = op.equipment.code

        needed = round(line["per_unit"] * remaining, 2)
        already = round(issued.get((line["component"], seq), 0.0), 2)
        stock = round(on_hand.get(line["component"], 0.0), 2)
        short = round(max(0.0, needed - already - stock), 2)
        station["components"].append({
            "component": line["component"],
            "name": line["name"],
            "per_unit": line["per_unit"],
            "needed_for_remaining": needed,
            "issued": already,
            "available_in_stock": stock,
            # The number a supervisor is actually looking for.
            "short_by": short,
            "will_run_out": short > 0,
        })

    stations = [by_station[k] for k in sorted(
        by_station, key=lambda s: (s if s is not None else 9999))]
    shortages = [
        {"seq": st["seq"], "equipment": st["equipment"], **c}
        for st in stations for c in st["components"] if c["will_run_out"]
    ]
    return {
        "order": order.code,
        "material": order.material.code,
        "quantity": order.quantity,
        "made_so_far": order.good_qty,
        "remaining": remaining,
        "stations": stations,
        "shortages": shortages,
        # Said plainly, because a list of stations is not an answer.
        "verdict": (
            f"{len(shortages)} component(s) will run short before this order finishes"
            if shortages else "every station has what it needs for the rest of this order"
        ),
    }
