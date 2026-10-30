"""Energy beside OEE: what the plant drew, against what its circuits can give,
and what one part cost against what its recipe says it should.

Read from the tag history - the power readings a reader wrote (today, the
Satisfactory reader; `fsmes.integrations.satisfactory.energy` says what each
one is made from - the names are kept here). A plant with no power readings at all has no energy
block: `breakdown` returns None and no screen draws an empty panel.

ONE DEFINITION PER NUMBER:

* **MW now** - a circuit's reading at the window's last power poll; a
  machine's at the last poll that answered its list (the buildings' or the
  generators'). A machine that list left out has none (`null`, with
  `last_read` saying when it was last listed): the mod lists only part of
  the factory in any one poll.
* **MWh** - the sum of the energy readings in the window. The reader books
  one per poll, only when it saw both ends of the interval, so time nobody
  watched holds none.
* **kWh a part** - MWh x 1000 over the units booked in the same window. No
  units, no figure.
* **ideal kWh a part** - the reader's latest ideal reading for the machine:
  a number, or the sentence saying why there is none ("unknown: ...").
* **gap** - kWh a part less the ideal, when both are known.
* **headroom** - a circuit's capacity less its peak demand.

Energy never enters an OEE figure; it is drawn beside it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.domain.masterdata import Equipment
from fsmes.domain.timeseries import TagValue
from fsmes.services import equipment as equipment_service

UNITS = {"draw": "MW", "energy": "MWh", "per_part": "kWh"}

# ---- the readings' names: the vocabulary a reader writes and this reads ----
# Kept here, below the readers, so a service never imports an edge; the
# Satisfactory reader takes them from here.

#: Readings on a building or a generator, as `<CODE>.<tag>`.
DRAW = "PowerMW"
DRAW_MAX = "PowerMaxMW"
ENERGY = "EnergyMWh"
IDEAL = "IdealKWhPerUnit"
OUTPUT = "OutputMW"
CAPACITY = "CapacityMW"
LOAD = "LoadPercent"
FUEL = "Fuel"
FUEL_ITEMS = "FuelItems"

#: A circuit's readings carry no machine: `POWER.CIRCUIT-<group>.<field>`.
CIRCUIT_PREFIX = "POWER."
TRIPPED = "FuseTripped"


def kwh_per_unit(mwh_booked: float | None, units: float | None) -> float | None:
    """kWh a part over a window: MWh booked x 1000 / units booked. None when
    nothing was made - a figure per part needs a part."""
    if mwh_booked is None or not units:
        return None
    return mwh_booked * 1000.0 / units


def trip_sentence(peak: float | None, capacity: float | None) -> str:
    """Why a fuse is out, in the figures of the poll that saw it."""
    if peak is None or capacity is None:
        return "fuse tripped; the feed did not carry the demand"
    if peak > capacity:
        return f"fuse tripped: demand {peak:g} MW over capacity {capacity:g} MW"
    return (f"fuse tripped: demand {peak:g} MW within capacity {capacity:g} MW at this "
            "poll - the trip came before it and the fuse has not been reset")

_MACHINE_TAGS = (DRAW, DRAW_MAX, IDEAL, OUTPUT, CAPACITY, LOAD, FUEL, FUEL_ITEMS)


def has_power(db: Session) -> bool:
    """Whether any reader ever wrote a circuit reading on this plant."""
    return db.scalar(
        select(TagValue.id).where(TagValue.equipment_id.is_(None),
                                  TagValue.tag.like(CIRCUIT_PREFIX + "%"))
        .order_by(TagValue.id.desc()).limit(1)) is not None


def _r(value: float | None, places: int = 3) -> float | None:
    return None if value is None else round(value, places)


def _latest(db: Session, machines: list[Equipment], names: tuple[str, ...],
            end: datetime) -> dict:
    """{(equipment_id, tag suffix): TagValue}, the latest at or before `end`."""
    if not machines:
        return {}
    wanted = [f"{m.code}.{name}" for m in machines for name in names]
    newest = (select(func.max(TagValue.id).label("id"))
              .where(TagValue.equipment_id.in_([m.id for m in machines]),
                     TagValue.tag.in_(wanted), TagValue.ts <= end)
              .group_by(TagValue.equipment_id, TagValue.tag)).subquery()
    rows = db.scalars(select(TagValue).join(newest, TagValue.id == newest.c.id)).all()
    return {(row.equipment_id, row.tag.rsplit(".", 1)[-1]): row for row in rows}


def _circuits(db: Session, start: datetime, end: datetime) -> tuple[list[dict], dict | None, int]:
    """Each circuit standing at the window's end, the window's last trip with the
    figures of the poll that saw it, and how many polls saw a fuse out."""
    rows = db.execute(
        select(TagValue.tag, TagValue.ts, TagValue.value_num)
        .where(TagValue.equipment_id.is_(None), TagValue.tag.like(CIRCUIT_PREFIX + "%"),
               TagValue.ts > start, TagValue.ts <= end)
        .order_by(TagValue.ts)).all()
    by_circuit: dict[str, dict[datetime, dict[str, float]]] = {}
    for tag, ts, value in rows:
        name, _, field = tag[len(CIRCUIT_PREFIX):].rpartition(".")
        by_circuit.setdefault(name, {}).setdefault(ts, {})[field] = value
    out, last_trip, tripped_polls = [], None, 0
    # The circuits as they stand: the ones in the window's last power poll.
    # A circuit that was there for a poll while the player wired one in
    # leaves its trips in the count but is not listed as standing.
    last_poll = max((max(polls) for polls in by_circuit.values()), default=None)
    for name in sorted(by_circuit):
        polls = by_circuit[name]
        at = max(polls)
        now = polls[at]
        cap, peak = now.get("CapacityMW"), now.get("PeakDemandMW")
        tripped = bool(now.get(TRIPPED))
        for ts, figures in polls.items():
            if figures.get(TRIPPED):
                tripped_polls += 1
                if last_trip is None or ts >= last_trip["at"]:
                    last_trip = {
                        "circuit": name, "at": ts,
                        "peak_demand_mw": figures.get("PeakDemandMW"),
                        "capacity_mw": figures.get("CapacityMW"),
                        "why": trip_sentence(figures.get("PeakDemandMW"),
                                                  figures.get("CapacityMW")),
                    }
        if at != last_poll:
            continue
        out.append({
            "circuit": name, "at": at,
            "capacity_mw": _r(cap), "consumed_mw": _r(now.get("ConsumedMW")),
            "peak_demand_mw": _r(peak), "production_mw": _r(now.get("ProductionMW")),
            "headroom_mw": _r(None if cap is None or peak is None else cap - peak),
            "battery_percent": _r(now.get("BatteryPercent")),
            "battery_capacity_mwh": _r(now.get("BatteryCapacityMWh")),
            "tripped": tripped,
            "why": trip_sentence(peak, cap) if tripped else None,
        })
    return out, last_trip, tripped_polls


def breakdown(db: Session, start: datetime, end: datetime,
              equipment_code: str | None = None) -> dict | None:
    """The energy block for one window, or None on a plant with no power
    readings. `equipment_code` narrows the machine list to one machine; the
    circuits stay the plant's."""
    if not has_power(db):
        return None
    circuits, last_trip, tripped_polls = _circuits(db, start, end)

    machine_ids = db.scalars(
        select(TagValue.equipment_id)
        .where(TagValue.equipment_id.is_not(None),
               TagValue.tag.like("%." + DRAW) | TagValue.tag.like("%." + OUTPUT),
               TagValue.ts > start, TagValue.ts <= end)
        .distinct()).all()
    query = select(Equipment).where(Equipment.id.in_(machine_ids))
    if equipment_code:
        query = query.where(Equipment.code == equipment_code)
    machines = db.scalars(query.order_by(Equipment.code)).all()
    ids = [m.id for m in machines]

    energy_sum = dict(db.execute(
        select(TagValue.equipment_id, func.sum(TagValue.value_num))
        .where(TagValue.equipment_id.in_(ids),
               TagValue.tag.like("%." + ENERGY),
               TagValue.ts > start, TagValue.ts <= end)
        .group_by(TagValue.equipment_id)).all()) if ids else {}
    latest = _latest(db, machines, _MACHINE_TAGS, end)
    made = equipment_service.production_sums(db, ids, start, end)

    # "Now" is the last poll that answered the machine's own list - the
    # buildings' or the generators'. The mod lists only part of the factory
    # in a poll, so a machine missing from that list has no figure now: its
    # last one is from an earlier poll and is not drawn as current.
    last_list = {DRAW: None, OUTPUT: None}
    for (_, tag), row in latest.items():
        if tag in last_list and (last_list[tag] is None or row.ts > last_list[tag]):
            last_list[tag] = row.ts

    buildings, generators = [], []
    for machine in machines:
        mwh = float(energy_sum.get(machine.id) or 0.0)

        def num(tag: str, _id: int = machine.id) -> float | None:
            row = latest.get((_id, tag))
            return None if row is None else row.value_num

        def now(tag: str, _id: int = machine.id, listed: str = DRAW) -> float | None:
            row = latest.get((_id, tag))
            if row is None or row.ts < last_list[listed]:
                return None
            return row.value_num

        draw_row = latest.get((machine.id, DRAW)) or latest.get((machine.id, OUTPUT))
        last_read = draw_row.ts if draw_row is not None else None

        if (machine.id, OUTPUT) in latest:
            fuel = latest.get((machine.id, FUEL))
            generators.append({
                "code": machine.code, "name": machine.name,
                "output_mw": _r(now(OUTPUT, listed=OUTPUT)),
                "capacity_mw": _r(now(CAPACITY, listed=OUTPUT)),
                "load_percent": _r(now(LOAD, listed=OUTPUT), 1), "last_read": last_read,
                "fuel": fuel.value_text if fuel is not None else None,
                "fuel_items": _r(num(FUEL_ITEMS), 1),
                "mwh_made": _r(mwh, 4),
            })
            continue
        good, scrap, _ = made.get(machine.id, (0.0, 0.0, 0.0))
        units = good + scrap
        per_part = kwh_per_unit(mwh, units)
        ideal_row = latest.get((machine.id, IDEAL))
        ideal = ideal_row.value_num if ideal_row is not None else None
        if ideal_row is None:
            ideal_note = "no recipe set"
        elif ideal is None:
            ideal_note = ideal_row.value_text
        else:
            ideal_note = None
        buildings.append({
            "code": machine.code, "name": machine.name,
            "mw_now": _r(now(DRAW)), "mw_full_speed": _r(num(DRAW_MAX)),
            "last_read": last_read,
            "mwh": _r(mwh, 4), "units": units,
            "kwh_per_unit": _r(per_part, 2),
            "ideal_kwh_per_unit": _r(ideal, 2),
            "ideal_note": ideal_note,
            "gap_kwh_per_unit": _r(per_part - ideal, 2)
            if per_part is not None and ideal is not None else None,
        })
    # The worst gap first: the building burning the most over its recipe is
    # the one to look at. Unknown gaps after every known one.
    buildings.sort(key=lambda b: (b["gap_kwh_per_unit"] is None,
                                  -(b["gap_kwh_per_unit"] or 0.0), b["code"]))

    def total(key: str, rows: list[dict]) -> float | None:
        values = [r[key] for r in rows if r[key] is not None]
        return round(sum(values), 3) if values else None

    return {
        "units": UNITS,
        "window": {"start": start, "end": end},
        "circuits": circuits, "circuits_total": len(circuits),
        "capacity_mw": total("capacity_mw", circuits),
        "consumed_mw": total("consumed_mw", circuits),
        "peak_demand_mw": total("peak_demand_mw", circuits),
        "headroom_mw": total("headroom_mw", circuits),
        "tripped": any(c["tripped"] for c in circuits),
        "last_trip": last_trip, "tripped_polls": tripped_polls,
        "mwh": round(sum(b["mwh"] or 0.0 for b in buildings), 4),
        "mwh_made": round(sum(g["mwh_made"] or 0.0 for g in generators), 4),
        "buildings": buildings, "buildings_total": len(buildings),
        "generators": generators, "generators_total": len(generators),
        "note": ("Energy is its own figure beside OEE and never enters it. MWh is "
                 "booked per poll only while the MES saw the building; kWh a part "
                 "is that energy over the units booked in the same window."),
    }
