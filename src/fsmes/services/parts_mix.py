"""Parts mix beside OEE: what the plant made of each item, against the rate
order it was asked for.

Read from the bookings OEE counts (`ProductionLog`), each one put to the item
its building was making when it was booked (the `<CODE>.Item` reading a reader
writes when a building's product changes), and from the plant's own rate
order (`fsmes.services.rate_orders`). A plant with no rate order and no parts
readings has no parts-mix block: `breakdown` returns None and no screen draws
an empty panel.

ONE DEFINITION PER NUMBER:

* **actual /min** - good units booked for the item in the window, over the
  minutes the feed was watched in it (`fsmes.services.last_play`: the time
  between polls of one stretch - the only time units can be booked in). No
  watched time, or a plant whose reader never said, and the rate is unknown
  (`null`), never zero.
* **target /min** - the rate order's rate for the item. An item the order does
  not name has none (`null`), and says so.
* **target share** - the item's target over the order's total, in %.
* **actual share** - the item's actual over the actual of every item in the
  order, in %; with no order, over every item made.
* **gap /min** - actual less target, when both are known. Rows run shortest
  first; items with no target come after, most made first.
* **game made /min** - the check, not a second source: the game's own
  `getProdStats` figure for the item (`PARTS.<item>.MadePerMin`), averaged
  over the polls in the window that carried the game's statistics (a poll
  that named no row for the item counts as none made). It counts every building of the save, the
  MES only the ones it books, so a gap between the two is a question to ask.

The parts mix never enters an OEE figure, and the rate order never feeds OEE.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.domain.execution import ProductionLog
from fsmes.domain.masterdata import Equipment
from fsmes.domain.timeseries import TagValue
from fsmes.services import coverage, last_play, rate_orders

UNITS = {"rate": "units/min", "share": "%"}

# ---- the readings' names: the vocabulary a reader writes and this reads ----

#: A building's product, as `<CODE>.Item`: the item's name, as text.
ITEM = "Item"
#: The game's own production figures, plant readings `PARTS.<item>.<field>`.
PARTS_PREFIX = "PARTS."
MADE = "MadePerMin"
MAX = "MaxPerMin"
CONSUMED = "ConsumedPerMin"
PART_FIELDS = (MADE, MAX, CONSUMED)


def parts_tag(item: str, field: str) -> str:
    return f"{PARTS_PREFIX}{item}.{field}"


def has_parts(db: Session) -> bool:
    """Whether any reader ever wrote a parts reading on this plant."""
    return db.scalar(select(TagValue.id).where(
        TagValue.equipment_id.is_(None),
        TagValue.tag.like(PARTS_PREFIX + "%")).limit(1)) is not None


def _r(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def _items_at(db: Session, ids: list[int], end: datetime) -> dict[int, tuple[list, list]]:
    """Per machine, its item readings up to `end`: (moments, names), in order."""
    if not ids:
        return {}
    rows = db.execute(
        select(TagValue.equipment_id, TagValue.ts, TagValue.value_text)
        .where(TagValue.equipment_id.in_(ids), TagValue.tag.like("%." + ITEM),
               TagValue.ts <= end)
        .order_by(TagValue.equipment_id, TagValue.ts, TagValue.id)).all()
    out: dict[int, tuple[list, list]] = {}
    for equipment_id, ts, name in rows:
        moments, names = out.setdefault(equipment_id, ([], []))
        moments.append(ts)
        names.append(name)
    return out


def item_of(db: Session, equipment_id: int, at: datetime) -> str | None:
    """The item a machine was making at `at`: its latest item reading."""
    return db.scalar(
        select(TagValue.value_text)
        .where(TagValue.equipment_id == equipment_id, TagValue.tag.like("%." + ITEM),
               TagValue.ts <= at)
        .order_by(TagValue.ts.desc(), TagValue.id.desc()).limit(1))


def booked_by_item(db: Session, start: datetime, end: datetime,
                   equipment_ids: list[int] | None = None) -> tuple[dict, dict, float]:
    """({item: good units}, {item: machines}, units with no item named), over
    the window - the same rows and bounds OEE counts."""
    query = (select(ProductionLog.equipment_id, ProductionLog.ts, ProductionLog.good_qty)
             .where(ProductionLog.ts >= start, ProductionLog.ts <= end,
                    ProductionLog.good_qty > 0))
    if equipment_ids is not None:
        query = query.where(ProductionLog.equipment_id.in_(equipment_ids))
    logs = db.execute(query).all()
    named = _items_at(db, sorted({row.equipment_id for row in logs}), end)
    units: dict[str, float] = {}
    machines: dict[str, set[int]] = {}
    unnamed = 0.0
    for equipment_id, ts, good in logs:
        moments, names = named.get(equipment_id, ([], []))
        at = bisect_right(moments, ts)
        if not at:
            unnamed += good
            continue
        item = names[at - 1]
        units[item] = units.get(item, 0.0) + good
        machines.setdefault(item, set()).add(equipment_id)
    return units, {k: len(v) for k, v in machines.items()}, unnamed


def game_rates(db: Session, start: datetime, end: datetime) -> dict[str, dict[str, float]]:
    """{item: {field: average over the window's polls}} from the parts readings.

    The average is over every poll in the window that carried the game's
    statistics, not only the polls that named the item: a reader writes no
    row of zeros, so a poll with statistics and no row for an item is the
    game saying it made none of it then. Averaging over the item's own rows
    alone would count only the minutes it was being made."""
    polls = db.scalar(
        select(func.count(func.distinct(TagValue.ts)))
        .where(TagValue.equipment_id.is_(None), TagValue.tag.like(PARTS_PREFIX + "%"),
               TagValue.ts > start, TagValue.ts <= end))
    if not polls:
        return {}
    rows = db.execute(
        select(TagValue.tag, func.sum(TagValue.value_num))
        .where(TagValue.equipment_id.is_(None), TagValue.tag.like(PARTS_PREFIX + "%"),
               TagValue.ts > start, TagValue.ts <= end)
        .group_by(TagValue.tag)).all()
    out: dict[str, dict[str, float]] = {}
    for tag, total in rows:
        item, _, field_ = tag[len(PARTS_PREFIX):].rpartition(".")
        if item and total is not None:
            out.setdefault(item, {})[field_] = float(total) / polls
    return out


def breakdown(db: Session, start: datetime, end: datetime) -> dict | None:
    """The plant's parts mix over a window, or None on a plant with no rate
    order and no parts readings."""
    order = rate_orders.current(db)
    if order is None and not has_parts(db):
        return None
    targets = rate_orders.targets(order)
    units, machines, unnamed = booked_by_item(db, start, end)
    watched = last_play.watched_seconds(db, start, end)
    minutes = watched / 60.0 if watched else None
    game = game_rates(db, start, end)

    items = list(targets) + sorted(i for i in units if i not in targets)
    actual = {i: (units.get(i, 0.0) / minutes if minutes else None) for i in items}
    basis = [i for i in items if (i in targets if targets else True)]
    basis_total = (sum(actual[i] for i in basis)
                   if minutes and all(actual[i] is not None for i in basis) else None)
    target_total = sum(targets.values())

    rows = []
    for item in items:
        target = targets.get(item)
        rate = actual[item]
        in_basis = item in basis
        rows.append({
            "item": item,
            "target_per_minute": _r(target),
            "actual_per_minute": _r(rate),
            "target_share_percent": _r(100.0 * target / target_total, 1) if target else None,
            "actual_share_percent": (_r(100.0 * rate / basis_total, 1)
                                     if in_basis and rate is not None and basis_total else
                                     (0.0 if in_basis and rate == 0 else None)),
            "gap_per_minute": _r(rate - target) if rate is not None and target is not None else None,
            "units": units.get(item, 0.0),
            "machines": machines.get(item, 0),
            "game_made_per_minute": _r(game.get(item, {}).get(MADE)),
            "game_max_per_minute": _r(game.get(item, {}).get(MAX)),
            "why": None if target is not None else (
                "no target: not in the rate order" if order else "no target: no rate order"),
        })
    with_target = sorted((r for r in rows if r["gap_per_minute"] is not None),
                         key=lambda r: r["gap_per_minute"])
    rest = sorted((r for r in rows if r["gap_per_minute"] is None),
                  key=lambda r: (r["target_per_minute"] is None, -(r["actual_per_minute"] or 0)))
    not_booked = sorted(i for i, figures in game.items()
                        if (figures.get(MADE) or 0) > 0 and i not in units)
    return {
        "unit": UNITS["rate"], "share_unit": UNITS["share"],
        "order": rate_orders.as_json(order),
        "has_target": order is not None,
        "items": with_target + rest,
        "items_total": len(rows),
        "units_booked": sum(units.values()),
        "units_without_item": unnamed,
        "watched_minutes": _r(minutes),
        "share_basis": "the rate order's items" if order else "every item made",
        "game_items_not_booked": not_booked,
        "game_items_not_booked_total": len(not_booked),
        "note": None if minutes else (
            "no rate in this window: the feed never said it was watching this plant"
            if watched is None else
            "no rate in this window: the feed was not watching during it"),
    }


def machine(db: Session, equipment: Equipment, start: datetime, end: datetime) -> dict | None:
    """One building's item and its rate over the window, against the plant's
    rate for the item and the order's target. None for a machine no reader
    named an item for."""
    item = item_of(db, equipment.id, end)
    if item is None:
        return None
    units, _, _ = booked_by_item(db, start, end, [equipment.id])
    account = coverage.totals_many(db, [equipment.id], start, end).get(equipment.id)
    observed = account.observed_seconds if account else 0.0
    plant = breakdown(db, start, end) or {"items": []}
    row = next((r for r in plant["items"] if r["item"] == item), None)
    return {
        "item": item, "unit": UNITS["rate"],
        "units": units.get(item, 0.0),
        "observed_minutes": _r(observed / 60.0),
        "actual_per_minute": _r(units.get(item, 0.0) / (observed / 60.0)) if observed else None,
        "plant_actual_per_minute": row["actual_per_minute"] if row else None,
        "target_per_minute": row["target_per_minute"] if row else None,
        "why": row["why"] if row else "no target: no rate order",
    }


__all__ = ["CONSUMED", "ITEM", "MADE", "MAX", "PARTS_PREFIX", "PART_FIELDS",
           "booked_by_item", "breakdown", "has_parts", "item_of", "machine", "parts_tag"]
