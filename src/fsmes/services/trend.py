"""The trend graph's two reads: several tags on one axis, and one point's records.

A historian can draw a tag. What it cannot do is answer the question a person
actually has in front of the line, which is *why did this number move* - and
that answer is not in the tag at all. It is in the order that was running, the
state the machine was in, the job somebody raised and never started, and the
sample the lab took twenty minutes later. This MES holds all four, and that is
the only reason it is worth drawing a trend here rather than in Ignition.

The product's own answer key (`labs/multiplant/bottling/line.json` -> `_chain`)
says where the walk back used to break. Link 3 is two tags on one machine,
twenty minutes apart: the chiller outlet leaving 3.2 degC at 6:20 and the
product temperature walking down from 8.4 to 6.4 degC between 6:40 and 7:00.
One tag at a time, on a window that ends at this second, nobody can see it.

Two reads, and neither of them computes anything a route already computes:

`series` - several of a machine's tags over one window. The window is settled
once, against the machine, so two tags wired up on different days still get
one axis (`analysis.machine_window`); each tag is then drawn by the same
bucketing the control-chart panel uses (`moment.tag_block`), which sends the
buckets nothing arrived in as readings of `null` and says how wide a bucket
is. Grouped into axes by unit, because degrees and bar on one scale is a
picture of nothing.

`dossier` - one point on that chart, and what else the records hold about the
second it sits in. Same shape and same habits as `services.spc_point`, and
the blocks that are about a machine and a window are literally the same code
(`services.moment`): what the machine was doing, the job that was open, the
maintenance that was open, the samples the lab took, and what every other tag
said at that instant.

**It is a plant-computed read, served as an envelope** (decision 0023), so the
screen and - later, as its own handoff - an agent read one answer. The panel
in `web/machine.js` lays these numbers out and works out none of them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.domain import (
    Equipment,
    MaintenanceOrder,
    NonConformance,
    QualityCheck,
    QualitySample,
    QualitySpec,
    SpcSignal,
    TagValue,
    WorkOrder,
    WorkOrderOperation,
)
from fsmes.kernel.tags import STRUCTURAL_TAGS
from fsmes.services import Invalid, analysis, coverage, masterdata, moment
from fsmes.services import tags as tag_service

#: The most tags one trend draws at once. Two is the question the chain asks -
#: a cause and its effect, twenty minutes apart - and a third is free because
#: a line already has three series' worth of colours in its palette. More than
#: that is a tag-picking workbench, which is a different screen and not this
#: one.
MOST_SERIES = 3

#: And the most scales. A chart has two sides. Three units on two sides would
#: mean one of them being drawn against somebody else's numbers, so a third
#: unit is refused with a sentence rather than drawn misleadingly.
MOST_AXES = 2

#: The most quality samples and maintenance orders one panel lists in full,
#: with the rest counted. A window of a whole shift on a busy filler can hold
#: a few dozen of either, and a panel is beside a chart rather than instead of
#: a page.
MOST_ROWS = 12

#: What the blocks that are lists of records say instead of a coverage figure -
#: the same sentence the control chart's own panel uses, from one place, so
#: this product does not word the same refusal two ways.
RECORDS_NOT_A_RATE = moment.RECORDS_NOT_A_RATE


# ------------------------------------------------------- what a tag is measured in


def _catalog_meta(equipment_code: str, name: str) -> dict:
    """What the tag catalog knows about one tag: its unit and its bounds.

    The same source the machine page's tag table reads (`services.tags`), so a
    trend axis cannot label itself in different units from the table above it.
    A tag the catalog does not describe has no unit here, and the chart then
    draws it on its own scale and says the unit is not recorded rather than
    guessing one.
    """
    entry = tag_service.catalog().get(equipment_code) or {}
    return (entry.get("meta") or {}).get(name) or {}


def _analogs(db: Session, unit: Equipment) -> list[str]:
    """Every numeric tag this machine has ever published that is not structural.

    The picker's list, and the default. Over all of history rather than over
    the window, because a tag that said nothing in the window is exactly the
    one a reader may want to put on the chart - the lab's own scenario turns
    on a value that stopped arriving.
    """
    structural = {f"{unit.code}.{name}" for name in STRUCTURAL_TAGS}
    rows = db.execute(
        select(TagValue.tag).where(TagValue.equipment_id == unit.id,
                                   TagValue.value_num.is_not(None))
        .distinct()).scalars()
    prefix = f"{unit.code}."
    return sorted(t[len(prefix):] for t in rows
                  if t.startswith(prefix) and t not in structural)


def _primary(db: Session, unit: Equipment, available: list[str]) -> list[str]:
    """What to draw when the caller named no tags: the machine's process value.

    The same answer `/analysis/tag` gives with no `tag=`, so the trend tab
    opens on the signal this machine was wired up for rather than on whatever
    is first alphabetically.
    """
    from fsmes.services import line as line_service

    declared = line_service.tag_map_analogs().get(unit.code)
    if declared and declared in available:
        return [declared]
    return available[:1]


def _watched_note(ledger: dict | None) -> str | None:
    """One sentence saying how much of the window the MES could see the machine.

    Worded here rather than in the browser because the chart and the panel
    both print it and two screens wording it two ways is the thing this
    product's own chart contract exists to prevent. The numbers are the
    coverage ledger's own; nothing is recomputed.

    Not the same question as a series' coverage, which is whether a tag said
    anything. A tag can go quiet on a machine nobody lost sight of, and a
    reader looking at an empty stretch of chart is owed which of the two it
    is.
    """
    if not ledger:
        return None
    window = ledger.get("window_seconds") or 0
    watched = ledger.get("observed_seconds") or 0
    share = ledger.get("coverage")
    if share is None:
        return ("this MES has no observation of this machine across the window "
                "drawn, so nothing on this chart is a measurement of the "
                "machine — only of what arrived")
    if window and watched >= window - 1:
        return (f"this MES could see this machine for all "
                f"{round(window / 60)} min of the window drawn")
    causes = ledger.get("seconds_by_cause") or {}
    where = ", ".join(f"{name.replace('_', ' ')} {round(seconds / 60)} min"
                      for name, seconds in causes.items() if seconds) or "not recorded"
    return (f"this MES could see this machine for {round(watched / 60)} of the "
            f"{round(window / 60)} min drawn ({round(share * 100)} %); the rest "
            f"went to {where}")


# ------------------------------------------------------- several tags, one axis


def series(db: Session, equipment_code: str, tags: list[str] | None = None,
           hours: float | None = None, shift: str | None = None,
           window: tuple[datetime, datetime] | None = None) -> dict:
    """Several of one machine's tags over one window, each with its own scale.

    The whole point is the *one window*. Two tags resolved separately clamp
    their starts to their own first reading and come back on two different
    axes, which is how a cause and its effect twenty minutes apart become
    impossible to see. So the window is settled once against the machine and
    handed to each tag.

    Each series is `moment.tag_block`'s - the same bucketing, the same empty
    buckets sent as readings of `null`, the same sentence about what was drawn
    and what was not. A reader comparing this chart with the one in the
    control-chart panel is looking at the same arithmetic.

    Series are grouped into axes by unit, and `axis` on each one says which
    scale it belongs to. Two tags in degrees share a scale and read as a
    comparison; a temperature and a pressure on one scale read as a straight
    line and a flat one, which is a picture of the units and not of the plant.
    """
    unit = masterdata.get_equipment(db, equipment_code)
    start, end, window_json = analysis.machine_window(db, equipment_code, hours,
                                                      shift, window)
    available = _analogs(db, unit)
    asked = [t.split(".", 1)[-1] for t in (tags or []) if t and t.strip()]
    # Asked for twice is asked for once. A picker with two boxes on it will
    # send the same tag in both the moment somebody changes their mind.
    wanted: list[str] = []
    for name in asked:
        if name not in wanted:
            wanted.append(name)
    if not wanted:
        wanted = _primary(db, unit, available)
    if len(wanted) > MOST_SERIES:
        raise Invalid(
            f"this chart draws at most {MOST_SERIES} tags at once, and "
            f"{len(wanted)} were asked for — a cause and its effect is the "
            f"question it was built for, not a tag-picking workbench")

    missing = [name for name in wanted if name not in available]
    drawn = [name for name in wanted if name in available]

    cadence, cadence_source = coverage.analog_sample_interval()
    blocks = []
    for name in drawn:
        meta = _catalog_meta(equipment_code, name)
        # `clamp=False`: the window was settled once against the machine
        # above, and narrowing it again to each tag's own first reading is
        # exactly how a cause and its effect twenty minutes apart end up on
        # two different axes. A tag with no history at the start of the window
        # comes back as buckets with no reading, which is what this product
        # draws when it has not got a number.
        block = moment.tag_block(db, unit, name, start, end, [], cadence,
                                 cadence_source, clamp=False)
        block["unit"] = meta.get("unit") or None
        block["kind"] = meta.get("kind")
        block["limits"] = {"min": meta.get("min"), "max": meta.get("max"),
                           "nominal": meta.get("nominal")}
        block["label"] = name
        blocks.append(block)

    # One scale per unit, in the order the tags were asked for. Tags the tag
    # manifest gives no unit for share one scale between them and the axis
    # says so: this product does not know they are measured in different
    # things, and inventing two axes on a guess would be as misleading as
    # inventing one. A plant that wants them apart names their units, which is
    # config and not code.
    axes: list[dict] = []
    for block in blocks:
        key = block["unit"]
        existing = next((a for a in axes if a["key"] == key), None)
        if existing is None:
            if len(axes) >= MOST_AXES:
                raise Invalid(
                    f"a chart has two sides, so it draws two scales: "
                    f"{block['label']} is measured in "
                    f"{block['unit'] or 'a unit nobody recorded'}, which is a "
                    f"third — draw it on its own or against one of the two")
            axes.append({
                "key": key, "unit": block["unit"], "side": len(axes),
                "tags": [block["label"]],
                "note": None if block["unit"] else (
                    "no unit is recorded for these signals, so they share one "
                    "scale — the tag manifest is where a unit is named"),
            })
            existing = axes[-1]
        else:
            existing["tags"].append(block["label"])
        block["axis"] = existing["side"]

    watched = moment.timeline_block(db, unit, start, end)
    return {
        "equipment": unit.code,
        "name": unit.name,
        # The axis under every series, settled once. Its `named` says which of
        # the three kinds of window this is, so the screen can say in words
        # what it is showing without guessing from two timestamps.
        "window": window_json,
        "series": blocks,
        "axes": axes,
        # Every list states its total: what was asked for, what is on the
        # chart, what this machine has to offer, and what was asked for and is
        # not there - which is a different fact from a tag that went quiet.
        "requested": wanted,
        "shown": len(blocks),
        "available": available,
        "total": len(available),
        "not_published": missing,
        "note": (None if not missing else
                 f"this machine has never published "
                 f"{', '.join(missing)} — nothing is drawn for "
                 f"{'it' if len(missing) == 1 else 'them'}, which is not the "
                 f"same as a tag that went quiet in this window"),
        # The machine's watched share of the window, from the same ledger the
        # state timeline prints. Each series carries its own figure as well,
        # and the two answer different questions: this one is "could the MES
        # see the machine", the series' is "did this tag say anything".
        "coverage": watched.get("coverage"),
        "coverage_note": _watched_note(watched.get("ledger")),
    }


# --------------------------------------------------------- one point's records


def _reading(db: Session, unit: Equipment, tag: str, at: datetime,
             bucket_seconds: float | None) -> dict:
    """The reading the reader clicked on, from the rows rather than from them.

    A point on this chart is a bucket - the mean of whatever arrived inside a
    stretch of seconds - so there are two honest numbers and the panel is owed
    both: the bucket this is a point of, recomputed here from the same rows
    the chart was drawn from, and the single stored sample nearest the instant,
    which is a real reading with a real timestamp. A panel that printed only
    the mean would be presenting an average as a measurement; one that printed
    only the nearest sample would disagree with the dot the reader clicked.

    Nothing is interpolated. A bucket with no sample in it is a bucket the
    chart drew as no reading, and this says so.
    """
    full = tag if "." in tag else f"{unit.code}.{tag}"
    width = bucket_seconds if bucket_seconds and bucket_seconds > 0 else None
    bucket = None
    if width is not None:
        half = width / 2.0
        row = db.execute(
            select(func.count(TagValue.id), func.avg(TagValue.value_num),
                   func.min(TagValue.value_num), func.max(TagValue.value_num),
                   func.min(TagValue.ts), func.max(TagValue.ts))
            .where(TagValue.equipment_id == unit.id, TagValue.tag == full,
                   TagValue.value_num.is_not(None),
                   TagValue.ts >= at - timedelta(seconds=half),
                   TagValue.ts <= at + timedelta(seconds=half))
        ).first()
        n, mean, low, high, first_ts, last_ts = row
        bucket = {
            "seconds": round(width, 3),
            "n": int(n or 0),
            "mean": None if mean is None else round(float(mean), 3),
            "min": None if low is None else round(float(low), 3),
            "max": None if high is None else round(float(high), 3),
            "first_ts": first_ts, "last_ts": last_ts,
            "note": None if n else (
                "no sample of this tag arrived inside this bucket — the chart "
                "drew it as no reading, and this is that same absence"),
        }
    nearest = moment.value_at(db, unit, full, at)
    meta = _catalog_meta(unit.code, full.split(".", 1)[-1])
    return {
        "tag": full.split(".", 1)[-1],
        "unit": meta.get("unit") or None,
        "at": at,
        "bucket": bucket,
        # The last thing this tag actually said at or before the instant
        # clicked, with how long before it said it. On a fabric that notifies
        # on change, that gap IS the reading: a setpoint that last published
        # four hours ago has not stopped, it has not moved.
        "nearest": nearest,
        "limits": {"min": meta.get("min"), "max": meta.get("max"),
                   "nominal": meta.get("nominal")},
        "note": None if bucket or nearest else (
            "this MES holds no sample of this tag at or before that instant"),
    }


def _order_at(db: Session, unit: Equipment, at: datetime) -> dict:
    """The job that was open on this machine at that second.

    From the order's own operation rows, which are this MES's recorded
    statement that this order was on this machine over that stretch - not from
    the routing, which says where an order is *meant* to go, and not from the
    bookings, which say what was declared and can be declared late.

    An operation that started before the instant and has not completed counts
    as open at it, because that is what open means. When nothing covers the
    instant the answer is null and says so: a machine between orders is not a
    machine running the last one it finished.
    """
    rows = list(db.execute(
        select(WorkOrderOperation, WorkOrder)
        .join(WorkOrder, WorkOrder.id == WorkOrderOperation.work_order_id)
        .where(WorkOrderOperation.equipment_id == unit.id,
               WorkOrderOperation.started_at.is_not(None),
               WorkOrderOperation.started_at <= at,
               (WorkOrderOperation.completed_at.is_(None))
               | (WorkOrderOperation.completed_at >= at))
        .order_by(WorkOrderOperation.started_at.desc())))
    if not rows:
        return {"order": None, "total": 0, "note": (
            "no operation of any order was open on this machine at that "
            "second, which is not the same as the machine having nothing on "
            "it — an order worked without its operation being started here "
            "would leave no record of it"),
            "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE}
    operation, order = rows[0]
    material = order.material
    return {
        "order": {
            "code": order.code,
            "status": order.status.value,
            "material": None if material is None else material.code,
            "material_name": None if material is None else material.name,
            "quantity": order.quantity,
            "started_at": order.started_at,
            "completed_at": order.completed_at,
            "operation": operation.name,
            "seq": operation.seq,
            "operation_status": operation.status.value,
            "operation_started_at": operation.started_at,
            "operation_completed_at": operation.completed_at,
            "good_qty": operation.good_qty,
            "scrap_qty": operation.scrap_qty,
        },
        # Every list states its total: more than one order open on one machine
        # at one second is a data problem worth seeing rather than hiding.
        "total": len(rows),
        "note": None if len(rows) == 1 else (
            f"{len(rows)} orders had an operation open on this machine at that "
            f"second; the most recently started is named"),
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


def _signals_on(db: Session, check_ids: list[int]) -> list[dict]:
    """What the control chart said about these readings when they were written.

    The rule firings this MES recorded, carrying the chart as it stood - not
    the chart recomputed now over a history that has moved since. The two give
    different numbers and only one of them is what the plant acted on.
    """
    if not check_ids:
        return []
    rows = list(db.scalars(
        select(SpcSignal).where(SpcSignal.check_id.in_(check_ids))
        .order_by(SpcSignal.ts, SpcSignal.rule)))
    out = []
    for signal in rows:
        code = db.scalar(select(NonConformance.code).where(
            NonConformance.id == signal.nonconformance_id)) \
            if signal.nonconformance_id else None
        out.append({"rule": signal.rule, "what": signal.what, "ts": signal.ts,
                    "value": signal.value, "nonconformance": code,
                    "nonconformance_id": signal.nonconformance_id})
    return out


def _measurements(db: Session, unit: Equipment, start: datetime,
                  end: datetime) -> dict:
    """What the lab measured at this machine in the window, and the verdict then.

    One list, two shapes, because this MES records two: a *sample* is the n
    pieces a sampled characteristic was inspected on in one act, and a lone
    *check* is one reading of a characteristic inspected one at a time. A
    panel that listed only the first would be silent about fill height
    measured singly, and silence is the one thing this product will not do.

    Scoped to this machine, because a reading records the station it was taken
    at. A reading with no station recorded is not quietly adopted into this
    one.

    The verdict beside each is the rule firing this MES *recorded* at the
    time. See `_signals_on`.
    """
    acts: list[dict] = []

    rows = list(db.execute(
        select(QualitySample, QualitySpec)
        .join(QualitySpec, QualitySpec.id == QualitySample.spec_id)
        .where(QualitySample.equipment_id == unit.id,
               QualitySample.ts >= start, QualitySample.ts <= end)))
    for sample, spec in rows:
        checks = list(db.scalars(
            select(QualityCheck).where(QualityCheck.sample_id == sample.id)
            .order_by(QualityCheck.id)))
        values = [c.value for c in checks]
        acts.append({
            "kind": "sample",
            "id": sample.id,
            "ts": sample.ts,
            "characteristic": spec.characteristic,
            "unit": spec.unit or None,
            "material": None if spec.material is None else spec.material.code,
            "order": _order_code(db, sample.work_order_id),
            "size": len(checks),
            "planned_size": spec.sample_size,
            "mean": round(sum(values) / len(values), 4) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "limits": {"min": spec.min_value, "max": spec.max_value},
            "failed": sum(1 for c in checks if c.result.value != "pass"),
            "checked_by": sample.checked_by,
            "signals": _signals_on(db, [c.id for c in checks]),
            "note": (None if checks else
                     "this sample has no readings against it, which is a "
                     "record of an act of measurement and not of a result"),
        })

    singles = list(db.execute(
        select(QualityCheck, QualitySpec)
        .join(QualitySpec, QualitySpec.id == QualityCheck.spec_id)
        .where(QualityCheck.equipment_id == unit.id,
               QualityCheck.sample_id.is_(None),
               QualityCheck.ts >= start, QualityCheck.ts <= end)))
    for check, spec in singles:
        acts.append({
            "kind": "check",
            "id": check.id,
            "ts": check.ts,
            "characteristic": spec.characteristic,
            "unit": spec.unit or None,
            "material": None if spec.material is None else spec.material.code,
            "order": _order_code(db, check.work_order_id),
            "size": 1,
            "planned_size": spec.sample_size,
            "mean": check.value,
            "min": check.value,
            "max": check.value,
            "limits": {"min": spec.min_value, "max": spec.max_value},
            "failed": 0 if check.result.value == "pass" else 1,
            "checked_by": check.checked_by,
            "signals": _signals_on(db, [check.id]),
            "note": None,
        })

    acts.sort(key=lambda a: a["ts"])
    for act in acts:
        act["verdict"] = (
            "a control-chart rule fired on this reading when it was recorded"
            if act["signals"] else
            "no control-chart rule fired on this reading when it was recorded")
        act["signals_total"] = len(act["signals"])
    shown = acts[:MOST_ROWS]
    return {
        "measurements": shown,
        "shown": len(shown),
        # Every list states its total, including the rows nobody drew.
        "total": len(acts),
        "failed": sum(1 for a in acts if a["failed"]),
        "with_signals": sum(1 for a in acts if a["signals"]),
        "note": (None if len(acts) <= MOST_ROWS else
                 f"{len(acts) - MOST_ROWS} more readings were taken at this "
                 f"machine in this window; the Quality and SPC tabs have all "
                 f"of them"),
        "says": ("the verdict beside each reading is the rule firing this MES "
                 "recorded at the time, with the chart as it stood — not the "
                 "control chart recomputed now over a history that has moved "
                 "since"),
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


def _order_code(db: Session, work_order_id: int | None) -> str | None:
    """The code of an order by id, or None when no order was recorded."""
    if work_order_id is None:
        return None
    return db.scalar(select(WorkOrder.code).where(WorkOrder.id == work_order_id))


def _other_tags(db: Session, unit: Equipment, tag: str, at: datetime) -> dict:
    """What every other tag on this machine said at that instant.

    `moment.value_at` for each, which is the last thing the tag said at or
    before the second clicked and how long before it said it. There is no
    reading AT an instant on a fabric that notifies on change, and that gap is
    a fact worth printing: a signal that last spoke four hours ago is either
    steady or gone, and the panel cannot tell a reader which, so it says what
    it has.

    Capped and counted like every other list in this product.
    """
    available = _analogs(db, unit)
    others = [name for name in available if name != tag.split(".", 1)[-1]]
    rows = []
    for name in others[:moment.MOST_TAGS]:
        meta = _catalog_meta(unit.code, name)
        said = moment.value_at(db, unit, f"{unit.code}.{name}", at)
        rows.append({
            "tag": name,
            "unit": meta.get("unit") or None,
            "kind": meta.get("kind"),
            "value": None if said is None else said["value"],
            "ts": None if said is None else said["ts"],
            "seconds_before": None if said is None else said["seconds_before"],
            "note": None if said is not None else (
                "this MES holds no sample of this tag at or before that instant"),
        })
    return {
        "tags": rows,
        "shown": len(rows),
        "total": len(others),
        "note": (None if len(others) <= moment.MOST_TAGS else
                 f"{len(others) - moment.MOST_TAGS} of this machine's signals are "
                 f"not listed — the tag table on this page has all of them"),
        "says": ("each value is the last this tag reported at or before that "
                 "second, with how long before. A tag publishes when it "
                 "changes, so nothing here is a reading taken at that instant "
                 "and nothing is interpolated between two"),
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


def dossier(db: Session, equipment_code: str, tag: str, at: datetime,
            hours: float | None = None, shift: str | None = None,
            window: tuple[datetime, datetime] | None = None,
            bucket_seconds: float | None = None) -> dict:
    """One point on a trend graph, and what else the records hold about it.

    Six blocks, which are the six questions a person standing in front of a
    moving number asks in order, and every one of them is already recorded:

        what was the reading, and when?        the bucket and the nearest
                                               stored sample
        what was the machine doing?            the state interval covering the
                                               instant, with the reason and
                                               who labelled it
        which job was open?                    the order's own operation row
        was anybody maintaining it?            every maintenance order on this
                                               machine touching the window,
                                               with its state and why
        what did the lab see?                  every reading taken here in
                                               the window and what the chart
                                               said about it at the time
        what were the other signals doing?     every other tag's last word at
                                               that instant

    Nothing here is recomputed. The machine's state, its timeline, its stops,
    its maintenance and the coverage of the window are `services.moment` -
    literally the same code the control-chart panel reads, which is why the
    two panels cannot disagree about a minute of the same machine.

    `window` is the chart's own window, not a window invented around the
    point: the reader asked a question about a stretch of time and clicked
    inside it, and a panel that silently narrowed to ten minutes either side
    would answer a different question - the maintenance order raised at the
    start of the shift is the answer the chain's own key expects to find.
    """
    unit = masterdata.get_equipment(db, equipment_code)
    start, end, window_json = analysis.machine_window(db, equipment_code, hours,
                                                      shift, window)
    if at < start or at > end:
        raise Invalid(
            "that instant is outside the window this chart is drawn on — a "
            "panel answering about a second the chart is not showing would be "
            "answering a question nobody asked")
    timeline = moment.timeline_block(db, unit, start, end)
    return {
        "equipment": unit.code,
        "name": unit.name,
        "window": window_json,
        "at": at,
        "reading": _reading(db, unit, tag, at, bucket_seconds),
        # What the machine was doing at that second, and what it had just come
        # out of. Scott's words, not the schema's: a reader wants "running,
        # and it had just finished a changeover", not "state interval".
        "machine": moment.state_at(db, unit, at),
        "order": _order_at(db, unit, at),
        "maintenance": moment.maintenance_orders(db, unit, start, end),
        "quality": _measurements(db, unit, start, end),
        "tags": _other_tags(db, unit, tag, at),
        # The stretches the machine was not running across the whole window,
        # off the same timeline the state above came from.
        "stops": moment.stops(timeline),
        # How much of the window the MES could see the machine at all. Said on
        # the panel as well as on the chart, because a panel full of nulls
        # over an unwatched hour has to say which kind of nothing it is.
        "coverage": timeline.get("coverage"),
        "coverage_note": _watched_note(timeline.get("ledger")),
        "watched": timeline.get("ledger"),
    }


def open_maintenance_in(db: Session, unit: Equipment, start: datetime,
                        end: datetime) -> list[str]:
    """The codes of maintenance orders open on this machine across the window.

    A convenience for tests and for the scorer, which both ask the same
    question of a replayed shift: was the chiller job open while the product
    went cold? The answer comes off the same read the panel draws.
    """
    return [row.code for row in db.scalars(
        select(MaintenanceOrder).where(
            MaintenanceOrder.equipment_id == unit.id,
            MaintenanceOrder.raised_at <= end,
            (MaintenanceOrder.completed_at.is_(None))
            | (MaintenanceOrder.completed_at >= start))
        .order_by(MaintenanceOrder.raised_at))]
