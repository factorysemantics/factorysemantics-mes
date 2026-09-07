"""Shift analysis: where the time went, where the units went, and why.

The operator dashboard answers "what is happening now". This answers "what
happened, and what should we do about it" — the questions an MES exists for and
an ERP cannot reach, because they are all built on machine-level state
intervals and tag history that never leave the plant floor.

Four views, each deliberately built from records the MES already committed to
rather than from anything recomputed here:

    oee_breakdown   OEE with its losses named in units and seconds
    state_timeline  every state interval per machine — the shift as a Gantt
    downtime_pareto downtime grouped by reason, worst first
    tag_trend       one machine's process value over the window

Two disciplines carried over from the rest of the MES. Nothing is invented: a
component that cannot be computed is null, never zero, and unlabelled downtime
is reported as unlabelled rather than folded into a catch-all that looks
explained. And the window never reaches back before the MES started watching,
because time we have no record of is not downtime.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import (
    Equipment,
    EquipmentLevel,
    EquipmentState,
    EquipmentStateName,
    ProductionLog,
    TagValue,
)
from fsmes.kernel.tags import STRUCTURAL_TAGS
from fsmes.services import NotFound, masterdata
from fsmes.services import equipment as equipment_service
from fsmes.services import line as line_service

# Below this much observed history, rates are not reported at all.
_MIN_WINDOW_SECONDS = 10.0
UNLABELLED = "unlabelled"


def _line_and_units(db: Session, line_code: str | None) -> tuple[Equipment, list[Equipment]]:
    """Resolve a work centre and its machines, at any depth beneath it.

    Deliberately not imported from services.line: that module is being built
    out for the 3D view and its resolver is private. The overlap is now three
    lines, because the walking itself moved to `masterdata.work_units_under`
    where both callers — and the dashboard — can share it.
    """
    centres = db.scalars(
        select(Equipment).where(Equipment.level == EquipmentLevel.WORK_CENTER).order_by(Equipment.code)
    ).all()
    populated = []
    for centre in centres:
        units = masterdata.work_units_under(db, centre)
        if units:
            populated.append((centre, list(units)))
    if not populated:
        raise NotFound("no line has any machines on it yet — seed a plant first")
    if line_code is None:
        populated.sort(key=lambda pair: (-len(pair[1]), pair[0].code))
        return populated[0]
    for centre, units in populated:
        if centre.code == line_code:
            return centre, units
    raise NotFound(f"line {line_code} not found (known: {', '.join(c.code for c, _ in populated)})")


def lines(db: Session) -> list[dict]:
    centres = db.scalars(
        select(Equipment).where(Equipment.level == EquipmentLevel.WORK_CENTER).order_by(Equipment.code)
    ).all()
    out = []
    for centre in centres:
        # Counted one hop down, a line whose machines hang off cells has no
        # stations and disappears from the picker entirely - so the screen
        # offers no way to reach the line it cannot see.
        count = len(masterdata.work_units_under(db, centre))
        if count:
            out.append({"code": centre.code, "name": centre.name, "stations": count})
    return out


def _window(db: Session, units: list[Equipment], hours: float) -> tuple[datetime, datetime]:
    """The reporting window, clamped to when the MES first saw this line."""
    end = utcnow()
    start = end - timedelta(hours=hours)
    first_seen = db.scalar(
        select(func.min(EquipmentState.started_at)).where(
            EquipmentState.equipment_id.in_([u.id for u in units])
        )
    )
    if first_seen is None:
        return end, end
    return (max(start, first_seen), end)


def _overlap(state: EquipmentState, start: datetime, end: datetime) -> float:
    lo = max(state.started_at, start)
    hi = min(state.ended_at or end, end)
    return max(0.0, (hi - lo).total_seconds())


def oee_breakdown(db: Session, line_code: str | None = None, hours: float = 8.0) -> dict:
    """OEE per station with every loss named, plus the line rollup.

    The losses are the point. "OEE 62%" tells an operator nothing; "you lost 19
    minutes to downtime and 300 units to slow running" tells them where to
    stand. Each loss is expressed in the unit its fix is measured in — seconds
    for availability, units for performance and quality.
    """
    centre, units = _line_and_units(db, line_code)
    start, end = _window(db, units, hours)
    window_seconds = (end - start).total_seconds()

    # Summed in the database, once for the whole line: loading every state
    # interval as an object to add its seconds in Python is what took a
    # 108-station plant's screens to a minute (see equipment.state_seconds).
    ids = [u.id for u in units]
    by_machine = equipment_service.state_seconds(db, ids, start, end)
    made = equipment_service.production_sums(db, ids, start, end)

    stations = []
    for unit in units:
        seconds = {name.value: 0.0 for name in EquipmentStateName}
        seconds.update(by_machine.get(unit.id, {}))
        runtime = seconds[EquipmentStateName.RUNNING.value]
        downtime = seconds[EquipmentStateName.DOWN.value]

        good, scrap = made.get(unit.id, (0.0, 0.0))
        total = good + scrap

        cycle = unit.ideal_cycle_seconds
        availability = runtime / window_seconds if window_seconds >= _MIN_WINDOW_SECONDS else None
        performance = min(1.0, cycle * total / runtime) if (runtime > 0 and total > 0 and cycle) else None
        quality = good / total if total > 0 else None
        overall = (
            availability * performance * quality if None not in (availability, performance, quality) else None
        )

        # Losses, in the units their fix is measured in.
        #
        # The performance loss is clamped at zero to stay consistent with
        # `performance`, which is capped at 1.0. A machine can out-run its rated
        # cycle time — a mis-set ideal_cycle_seconds, or a replay running faster
        # than wall-clock — and reporting that as a negative loss would put a
        # bar below the axis and imply the line invented units.
        capable = runtime / cycle if (cycle and runtime > 0) else None
        stations.append(
            {
                "code": unit.code,
                "name": unit.name,
                "ideal_cycle_seconds": cycle,
                "availability": _round(availability),
                "performance": _round(performance),
                "quality": _round(quality),
                "oee": _round(overall),
                "runtime_seconds": round(runtime, 1),
                "downtime_seconds": round(downtime, 1),
                "seconds_by_state": {k: round(v, 1) for k, v in seconds.items()},
                "good_qty": good,
                "scrap_qty": scrap,
                "loss": {
                    # What downtime cost, priced at the machine's own rated rate.
                    "availability_seconds": round(window_seconds - runtime, 1),
                    "availability_units": round((window_seconds - runtime) / cycle, 1) if cycle else None,
                    # What running slower than rated cost.
                    "performance_units": round(max(capable - total, 0.0), 1) if capable is not None else None,
                    "quality_units": scrap,
                },
            }
        )

    produced = sum(s["good_qty"] for s in stations)
    scrapped = sum(s["scrap_qty"] for s in stations)
    rated = [s["oee"] for s in stations if s["oee"] is not None]
    # The line's constraint: the station whose OEE is worst is the one worth
    # fixing first, which is a more useful headline than an average nobody acts on.
    worst = min((s for s in stations if s["oee"] is not None), key=lambda s: s["oee"], default=None)

    return {
        "line": {"code": centre.code, "name": centre.name},
        "window": {
            "hours": round(window_seconds / 3600, 4),
            "requested_hours": hours,
            "start": start,
            "end": end,
            # True when the MES simply has not been watching for as long as asked.
            "clamped": round(window_seconds / 3600, 4) < hours - 0.001,
        },
        "stations": stations,
        "line_oee": _round(min(rated)) if rated else None,
        "constraint": worst["code"] if worst else None,
        "good_qty": produced,
        "scrap_qty": scrapped,
    }


def _merge_short(intervals: list[dict], floor_seconds: float) -> list[dict]:
    """Fold intervals too short to see into the one before them.

    A shift on sixty machines is twenty-odd thousand state changes, and a Gantt
    a thousand pixels wide cannot draw them: at eight hours a pixel is about
    thirty seconds, so anything briefer is already invisible. Sending it anyway
    cost 3.1 MB per screen load.

    Merging keeps the shape honest - a run of micro-stops still reads as
    disturbed time rather than vanishing - and `merged` says how many were
    folded so the picture never silently claims to be complete.
    """
    if not intervals:
        return intervals

    out = [dict(intervals[0])]
    merged = 0
    for interval in intervals[1:]:
        last = out[-1]
        if interval["seconds"] < floor_seconds and interval["state"] == last["state"]:
            last["end"] = interval["end"]
            last["seconds"] = round(last["seconds"] + interval["seconds"], 1)
            merged += 1
        elif interval["seconds"] < floor_seconds and out and last["seconds"] >= floor_seconds:
            # Too brief to draw and a different state: absorb it into the
            # neighbour rather than drop it, so the timeline stays continuous.
            last["end"] = interval["end"]
            last["seconds"] = round(last["seconds"] + interval["seconds"], 1)
            merged += 1
        else:
            out.append(dict(interval))
    if merged:
        out[0]["_merged_into_neighbours"] = merged
    return out


def state_timeline(db: Session, line_code: str | None = None, hours: float = 8.0,
                   pixels: int = 1200, equipment: list[str] | None = None,
                   limit: int = 12) -> dict:
    """Every state interval per machine — the shift drawn as a Gantt.

    This is the view that makes a line legible: starvation walking downstream
    from a breakdown is obvious as a picture and nearly invisible as a table.
    """
    centre, units = _line_and_units(db, line_code)

    # A Gantt of sixty machines is not a chart anybody reads, and sending it
    # cost 3.2 MB per screen load. Scope it: named machines if the caller
    # knows what it wants, otherwise the first screenful.
    all_units = units
    if equipment:
        # Named machines are found across the whole plant, not one line. A
        # factory has several lines, and a caller that names a machine on
        # another one knows what it wants; scoping the names to one line
        # silently returned nothing for four of a factory's five scripted
        # breakdowns, which the scorer then called missed.
        wanted = {code.upper() for code in equipment}
        units = [
            u for u in db.scalars(
                select(Equipment).where(Equipment.level == EquipmentLevel.WORK_UNIT)
                .order_by(Equipment.code))
            if u.code.upper() in wanted
        ]
        all_units = units
    units = units[:max(1, limit)]
    start, end = _window(db, units, hours)

    rows = []
    for unit in units:
        # Columns, not objects: a dozen machines over eight busy hours is
        # tens of thousands of intervals, and hydrating each one was most of
        # this chart's cost.
        states = db.execute(
            select(EquipmentState.state, EquipmentState.reason,
                   EquipmentState.started_at, EquipmentState.ended_at)
            .where(
                EquipmentState.equipment_id == unit.id,
                EquipmentState.started_at < end,
                (EquipmentState.ended_at.is_(None)) | (EquipmentState.ended_at > start),
            )
            .order_by(EquipmentState.started_at)
        ).all()
        drawn = [
                    {
                        "state": getattr(state, "value", state),
                        "reason": reason,
                        # Clipped to the window so the client can lay out
                        # directly without re-deriving what is on screen.
                        "start": max(started_at, start),
                        "end": min(ended_at or end, end),
                        "seconds": round(max(0.0, (min(ended_at or end, end)
                                                   - max(started_at, start)).total_seconds()), 1),
                        "open": ended_at is None,
                    }
                    for state, reason, started_at, ended_at in states
        ]
        # One pixel of an `hours`-wide chart, so the floor scales with the
        # window the caller asked for rather than being a magic number.
        floor_seconds = (hours * 3600.0) / max(pixels, 1)
        rows.append({"code": unit.code, "name": unit.name,
                     "intervals": _merge_short(drawn, floor_seconds)})
    return {
        "line": {"code": centre.code, "name": centre.name},
        "window": {"start": start, "end": end,
                   "hours": round((end - start).total_seconds() / 3600, 4)},
        "machines": rows,
        # Say what is not on the chart, rather than letting a partial picture
        # look like the whole line.
        "machines_shown": len(rows),
        "machines_total": len(all_units),
        "machines_available": [u.code for u in all_units],
    }


def downtime_pareto(db: Session, line_code: str | None = None, hours: float = 8.0) -> dict:
    """Downtime grouped by reason, worst first, with a running cumulative share.

    Unlabelled downtime is reported under its own name rather than hidden in an
    "other" bucket: on a line fed by OPC alone, nothing labels a stop, and a
    pareto that quietly says 100% "other" is how a plant convinces itself it has
    no data problem.
    """
    centre, units = _line_and_units(db, line_code)
    start, end = _window(db, units, hours)
    ids = [unit.id for unit in units]
    by_id = {unit.id: unit.code for unit in units}

    states = db.scalars(
        select(EquipmentState).where(
            EquipmentState.equipment_id.in_(ids),
            EquipmentState.state == EquipmentStateName.DOWN,
            EquipmentState.started_at < end,
            (EquipmentState.ended_at.is_(None)) | (EquipmentState.ended_at > start),
        )
    ).all()

    buckets: dict[str, dict] = {}
    for state in states:
        seconds = _overlap(state, start, end)
        if seconds <= 0:
            continue
        key = state.reason or UNLABELLED
        bucket = buckets.setdefault(key, {"reason": key, "seconds": 0.0, "events": 0, "machines": {}})
        bucket["seconds"] += seconds
        bucket["events"] += 1
        code = by_id[state.equipment_id]
        bucket["machines"][code] = round(bucket["machines"].get(code, 0.0) + seconds, 1)

    ordered = sorted(buckets.values(), key=lambda b: -b["seconds"])
    total = sum(b["seconds"] for b in ordered)
    running = 0.0
    for bucket in ordered:
        # Shares come from the exact seconds, not the rounded ones: rounding
        # first lets a single bucket report more than 100% of the total.
        exact = bucket["seconds"]
        running += exact
        bucket["seconds"] = round(exact, 1)
        bucket["share"] = round(exact / total, 4) if total else None
        bucket["cumulative"] = round(running / total, 4) if total else None

    return {
        "line": {"code": centre.code, "name": centre.name},
        "window": {"start": start, "end": end, "hours": round((end - start).total_seconds() / 3600, 4)},
        "total_seconds": round(total, 1),
        "reasons": ordered,
        "unlabelled_share": next((b["share"] for b in ordered if b["reason"] == UNLABELLED), 0.0),
    }


def tag_trend(
    db: Session, equipment_code: str, tag: str | None = None, hours: float = 8.0, buckets: int = 240
) -> dict:
    """One machine's process value over the window, averaged into buckets.

    Bucketed rather than sent raw: an hour of 1 Hz data is 3,600 points per tag,
    and a chart 900 pixels wide cannot show them. Min and max come along with
    the mean, so an excursion that a mean would smooth away still appears.

    The bucketing happens in Python, on two columns fetched in timestamp order.
    Doing it in SQL would mean `julianday` on SQLite and `extract(epoch ...)` on
    PostgreSQL, and this project runs on both — a portability bug here would
    only surface in the deployment that matters.
    """
    unit = masterdata.get_equipment(db, equipment_code)
    end = utcnow()
    start = end - timedelta(hours=hours)

    if tag is None:
        # The machine's process value. Ask the tag map first: it names the
        # signal this machine was wired up for, and it is the same answer the
        # line view gives, so the two screens cannot disagree.
        #
        # Guessing from history is the fallback, and it guesses badly once a
        # machine publishes a dozen tags - "whatever is not structural, most
        # recently written" would happily chart ReadyBit.
        published = select(TagValue.tag).where(
            TagValue.equipment_id == unit.id, TagValue.value_num.is_not(None))

        # The tag map names the signal this machine was wired up for, and it
        # is the same answer the line view gives - but only believe it if the
        # machine has actually published it. A default map says "Temperature"
        # about every machine on earth.
        declared = line_service.tag_map_analogs().get(equipment_code)
        tag = db.scalar(
            published.where(TagValue.tag == f"{equipment_code}.{declared}").limit(1)
        ) if declared else None

        # Otherwise discover it: whatever this machine publishes that is not
        # one of the tags every machine carries. That list has to stay
        # complete - when it lagged the tag fabric, this drew ReadyBit.
        if tag is None:
            tag = db.scalar(
                published.where(TagValue.tag.notin_(
                    [f"{equipment_code}.{name}" for name in STRUCTURAL_TAGS]))
                .order_by(TagValue.id.desc())
                .limit(1)
            )
        if tag is None:
            return {"equipment": equipment_code, "tag": None, "points": [], "window": {"start": start, "end": end}}
    elif "." not in tag:
        tag = f"{equipment_code}.{tag}"

    # Clamp to when this tag was first recorded, the same way OEE clamps to when
    # the equipment was first observed. Without it a five-minute-old MES draws
    # an eight-hour axis with everything crushed against the right edge, which
    # reads as "the line was idle all day" rather than "we just started
    # watching".
    first_seen = db.scalar(
        select(func.min(TagValue.ts)).where(TagValue.equipment_id == unit.id, TagValue.tag == tag)
    )
    if first_seen is not None and first_seen > start:
        start = first_seen

    span = max((end - start).total_seconds(), 1.0)
    width = span / max(buckets, 1)
    rows = db.execute(
        select(TagValue.ts, TagValue.value_num)
        .where(
            TagValue.equipment_id == unit.id,
            TagValue.tag == tag,
            TagValue.value_num.is_not(None),
            TagValue.ts >= start,
            TagValue.ts <= end,
        )
        .order_by(TagValue.ts)
    ).all()

    grouped: dict[int, list[float]] = {}
    for ts, value in rows:
        grouped.setdefault(int((ts - start).total_seconds() / width), []).append(value)

    points = [
        {
            "t": start + timedelta(seconds=(bucket + 0.5) * width),
            "mean": round(sum(values) / len(values), 3),
            "min": round(min(values), 3),
            "max": round(max(values), 3),
            "n": len(values),
        }
        for bucket, values in sorted(grouped.items())
    ]
    return {
        "equipment": equipment_code,
        "tag": tag.split(".", 1)[-1],
        "window": {"start": start, "end": end, "hours": hours},
        "points": points,
    }


def production_trend(db: Session, line_code: str | None = None, hours: float = 8.0, buckets: int = 60) -> dict:
    """Good and scrap over time for the whole line, bucketed."""
    centre, units = _line_and_units(db, line_code)
    ids = [unit.id for unit in units]
    # Same clamp as the OEE panel, so every chart on the page shares one axis.
    start, end = _window(db, units, hours)
    span = max((end - start).total_seconds(), 1.0)
    width = span / max(buckets, 1)
    rows = db.execute(
        select(ProductionLog.ts, ProductionLog.good_qty, ProductionLog.scrap_qty)
        .where(ProductionLog.equipment_id.in_(ids), ProductionLog.ts >= start, ProductionLog.ts <= end)
        .order_by(ProductionLog.ts)
    ).all()

    grouped: dict[int, list[float]] = {}
    for ts, good, scrap in rows:
        bucket = grouped.setdefault(int((ts - start).total_seconds() / width), [0.0, 0.0])
        bucket[0] += good or 0.0
        bucket[1] += scrap or 0.0

    return {
        "line": {"code": centre.code, "name": centre.name},
        "window": {"start": start, "end": end, "hours": hours},
        "bucket_seconds": round(width, 1),
        "points": [
            {"t": start + timedelta(seconds=(b + 0.5) * width), "good": good, "scrap": scrap}
            for b, (good, scrap) in sorted(grouped.items())
        ],
    }


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


__all__ = [
    "downtime_pareto",
    "lines",
    "oee_breakdown",
    "production_trend",
    "state_timeline",
    "tag_trend",
]
