"""What the plant was recorded doing around one moment on one machine.

A point on a control chart and a point on a trend graph ask the same six
questions of the records, because they are the same question asked from two
screens: *what was the machine doing just then, what were its other signals
doing, what job was open, what was anybody maintaining, and how much of that
do we actually know?* Only the thing being clicked differs - a measured
reading on one screen, a bucket of an analog on the other.

So the blocks that are about a machine and a window live here, and both
dossiers call them: `services.spc_point` for a reading on a control chart
(#143-#148) and `services.trend_point` for a point on a trend graph. The
alternative was a second copy, and a second copy is how two screens come to
answer the same question two ways - the thing `web/kit.js` and the analysis
reads both exist to make impossible.

**Nothing here is computed twice.** The machine's timeline comes from
`services.analysis.state_timeline`, each analog from `analysis.tag_trend`,
the watched share from the ledger in `services.coverage`. The only arithmetic
below is laying a bucket grid over a window and filling in the buckets
nothing arrived in, which is a statement about the drawing and not about the
plant.

Two honesty rules the moved code already carried, named again because both
are easy to lose in a move:

- **Unknown is not zero.** A tag that said nothing in a window has
  `coverage: null` and a sentence saying a value which has not changed
  publishes nothing on a fabric that notifies on change; an instant the MES
  holds no state interval for says so rather than reading as *stopped*.
- **A comparison of records is not a rate over a watched window.** The
  maintenance orders and the findings carry `coverage: "absent"` with the
  reason. Only the blocks about a stretch of a machine's time - the timeline
  and each analog - carry a figure.

Moved here from `services.spc_point` on 2026-10-11 with no change of
behaviour; the call sites there now read `moment.state_at(...)` and the rest.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.domain import (
    Equipment,
    EquipmentState,
    MaintenanceOrder,
    NonConformance,
    TagValue,
    WorkOrder,
)
from fsmes.kernel.tags import STRUCTURAL_TAGS
from fsmes.services import analysis, coverage, spc

#: What the blocks that are lists of records say instead of a coverage figure
#: - the control chart's own sentence, from `services.spc`, because a chart
#: and the dossier behind a point on it are lists of the same records and must
#: not word the same refusal two ways.
RECORDS_NOT_A_RATE = spc.RECORDS_NOT_A_RATE

#: The most analogs one panel draws, and the most tags it will look through to
#: find them. Both stated in the answer: a panel showing three of a station's
#: eleven signals must not look like a picture of the station.
MOST_TAGS = 6

#: How many buckets one analog is drawn in when this plant has no configured
#: cadence to choose against. A bucket with no sample in it is sent as a
#: reading of `null`, which is how the kit already breaks a line rather than
#: drawing one through a stretch nothing arrived in - so the bucket width is
#: the resolution at which this panel can say *the tag stopped*, and on a
#: plant fed by hand there is no rate to pick it from.
TAG_BUCKETS = 60

#: And the two ends of the range `buckets_for` will choose inside when there
#: is a cadence. Fewer than a dozen points is not a trend; a point per second
#: over an hour is a chart nobody can read and a payload nobody wanted.
MIN_BUCKETS = 12
MOST_BUCKETS = 240

# --------------------------------------------------------------- the machine


def analog_tags(session: Session, unit: Equipment, start: datetime,
                 end: datetime) -> tuple[list[str], int]:
    """Which of this station's signals are worth a small chart, and how many it has.

    Whatever it published numerically in the window that is not one of the tags
    every machine carries - `State`, the counters, `ReadyBit` and the rest.
    Those are the plant's structure and not its process, and a chart of
    `ReadyBit` is a chart of the tag fabric.

    Returns the ones that will be drawn and the total it chose from, because a
    panel showing three of eleven signals must not look like the station.
    """
    structural = {f"{unit.code}.{name}" for name in STRUCTURAL_TAGS}
    rows = session.execute(
        select(TagValue.tag, func.count(TagValue.id))
        .where(TagValue.equipment_id == unit.id,
               TagValue.value_num.is_not(None),
               TagValue.ts >= start, TagValue.ts <= end)
        .group_by(TagValue.tag)).all()
    found = sorted(tag for tag, _ in rows if tag not in structural)
    return found[:MOST_TAGS], len(found)


def buckets_for(span: float, cadence: float | None) -> int:
    """How many buckets one analog is drawn in over a window this wide.

    Two stored samples to a bucket, so a plant that drops one sample now and
    then does not read as a plant with holes in its history, while a tag that
    stops for a minute does. The cadence is the rate this plant is CONFIGURED
    to store analogs at (`coverage.analog_sample_interval`) and never a rate
    measured from the data: a cadence inferred from the gaps would declare the
    quietest stretch of a window normal, which is the one stretch the reader
    came here about.

    Bounded both ways. A bucket per sample on a long window is a chart of a
    thousand points nobody can read; fewer than a handful is not a trend.
    A plant with no configured cadence gets `TAG_BUCKETS` and is told so.
    """
    if cadence is None or cadence <= 0:
        return TAG_BUCKETS
    return int(max(MIN_BUCKETS, min(MOST_BUCKETS, span / (cadence * 2))))


def bucketed(envelope: dict, buckets: int) -> tuple[list[dict], int, float]:
    """Every bucket of the window, including the ones nothing arrived in.

    `tag_trend` returns the buckets it had samples for and no entry at all for
    the ones it did not, so a chart drawn straight from it joins a line across
    a quarter of an hour of silence - which is a measurement of that quarter
    hour, and there wasn't one. The lab's own scenario plants exactly that: a
    tag that stops while the machine runs on and every other signal reports.

    So the empty buckets are sent, as readings of `null`. That is the shape the
    kit already breaks a line on and already counts in its footer ("4 readings
    drawn, 56 with no reading") - not a second rendering of unknown invented
    here. Nothing is interpolated and nothing is carried forward: a value
    repeated would be a reading somebody could believe.

    The grid is laid against the window the ENVELOPE realised, not the one that
    was asked for. `tag_trend` clamps its start to when the tag was first
    recorded, and bucketing a clamped series against an unclamped grid puts
    every point in the wrong bucket - by a whole minute, on the first run of
    this.

    Returns the filled series, how many buckets had nothing, and how wide one
    bucket is in seconds.
    """
    window = envelope.get("window") or {}
    start, end = window.get("start"), window.get("end")
    if start is None or end is None:
        return list(envelope.get("points", [])), 0, 0.0
    span = max((end - start).total_seconds(), 1.0)
    width = span / buckets
    held: dict[int, dict] = {}
    for point in envelope.get("points", []):
        index = int(min(buckets - 1, max(0, (point["t"] - start).total_seconds() / width)))
        held[index] = point
    out = []
    for index in range(buckets):
        point = held.get(index)
        out.append(point if point is not None else {
            "t": start + timedelta(seconds=(index + 0.5) * width),
            "mean": None, "min": None, "max": None, "n": 0})
    return out, buckets - len(held), width


def last_before(session: Session, unit: Equipment, tag: str,
                 start: datetime) -> dict | None:
    """The newest sample of this tag from BEFORE the window, or None.

    The one fact that tells a tag which went quiet apart from a tag which has
    nothing to say. OPC UA notifies on change, so a value sitting still sends
    nothing at all - a filler's fill-weight setpoint publishes once when the
    order is set and then never again, and over any twelve minutes afterwards
    it has no samples whatever and is in perfect health.

    On the first look at this with real data (a replayed line, 2026-10-05)
    `FILL01.FillWeightSP` was drawn as an empty box reporting that nobody had
    watched it. That was the panel's own fault, not the plant's, and this is
    the fact that fixes it: the chart is still empty, and under it the panel
    can now say what the value was and how long before the window it was last
    reported.
    """
    row = session.execute(
        select(TagValue.ts, TagValue.value_num)
        .where(TagValue.equipment_id == unit.id, TagValue.tag == tag,
               TagValue.value_num.is_not(None), TagValue.ts < start)
        .order_by(TagValue.ts.desc()).limit(1)).first()
    if row is None:
        return None
    ts, value = row
    return {"value": value, "ts": ts,
            "seconds_before_window": round((start - ts).total_seconds(), 1)}


def tag_block(session: Session, unit: Equipment, tag: str, start: datetime,
               end: datetime, marks: list[dict], cadence: float | None,
               cadence_source: str | None) -> dict:
    """One of the station's analogs over the window, with the readings marked.

    The envelope is `analysis.tag_trend`'s own - its window, its bucketing, its
    min/max band - with a few things added that are about *this* question: the
    moment the reading was taken, the buckets nothing arrived in, how many
    samples the tag managed, the last value it reported before the window, and
    the share of the window it spoke in at all.

    A second bucketer here is how two screens come to draw the same tag
    differently, which is the thing `kit.js` and the analysis reads both exist
    to make impossible. So the bucket count is passed to the analysis read
    rather than re-implemented, and the only arithmetic below is filling in the
    buckets it had nothing to report.
    """
    # What window will the analysis read actually realise? It clamps its start
    # to when this tag was first recorded, and a bucket count worked out
    # against the window we ASKED for lands a grid of the wrong WIDTH on the
    # window we get: on a plant twenty minutes old, a 12-minute request
    # realised as 3 minutes put 72 buckets across 172 seconds and every other
    # one of them read as a bucket nothing arrived in. Measured on a replayed
    # replay, 2026-10-05 - the first thing looking at real data found.
    #
    # So the span is read off a deliberately coarse first pass, and the grid is
    # chosen against the window the second pass will draw.
    probe = analysis.tag_trend(session, unit.code, tag=tag,
                              window=(start, end), buckets=2)
    drawn = probe.get("window") or {}
    span = (max((drawn["end"] - drawn["start"]).total_seconds(), 1.0)
            if drawn.get("start") and drawn.get("end")
            else max((end - start).total_seconds(), 1.0))
    buckets = buckets_for(span, cadence)
    envelope = analysis.tag_trend(session, unit.code, tag=tag,
                                  window=(start, end), buckets=buckets)
    points, empty, width = bucketed(envelope, buckets)
    samples = sum(p.get("n", 0) for p in points)
    spoke = buckets - empty
    # Before the window DRAWN, for the same reason the grid is laid against it:
    # a sample inside the window asked for but outside the one realised is
    # exactly the sample a reader looking at an empty chart needs.
    last = last_before(session, unit, tag, drawn.get("start") or start)
    silent = samples == 0
    return {
        **envelope,
        "points": points,
        # The reading - or, on a sampled characteristic, each of the n
        # readings - this panel is about, on the tag's own clock. This is what
        # makes the picture answer the question: a pressure dip and a fill
        # weight are only an explanation if they are read against each other,
        # and a sample of five bottles took five minutes of them to measure.
        "markers": list(marks),
        # The resolution the picture is drawn at, so the axis states its own
        # denominator (chart contract rule 4) rather than leaving a reader to
        # work out what one point of this line is an average of.
        "bucket_seconds": round(width, 3),
        "samples": samples,
        "buckets": buckets,
        "buckets_with_no_reading": empty,
        # What this tag last said before the window, so a tag that is simply
        # not changing is not read as a tag nobody watched.
        "last_before_window": last,
        # The cadence the bucket was chosen against, and where the number came
        # from - carried so a reader can tell "this tag went quiet" from "this
        # plant has no configured cadence and the bucket is a default".
        "sample_interval_seconds": cadence,
        "sample_interval_source": cadence_source,
        # How much of the window this tag spoke in at all, at the resolution
        # drawn - and never the machine's watched share: a tag can go quiet on
        # a machine nobody lost sight of, which is the whole point of drawing
        # this beside the state timeline rather than instead of it.
        #
        # `null`, not zero, for a tag that said nothing whatever. On a
        # change-driven fabric that is what a value sitting still looks like,
        # and "nobody watched any of this window" would be a different claim
        # and the wrong one. Unknown is not zero, here as everywhere.
        "coverage": None if silent else round(spoke / buckets, 4),
        "coverage_note": (
            (
                # Two things look identical here and the reader is owed both,
                # because this answer cannot tell them apart: a value that has
                # not changed publishes nothing on a fabric that notifies on
                # change, and a window the MES had not started watching holds
                # nothing either. The window line the chart already prints says
                # which, and the last value before the window is the fact that
                # makes the empty box mean something.
                "this tag reported nothing in the window drawn. A value that has "
                "not changed publishes nothing on a fabric that notifies on "
                "change, and a window this MES had not started watching holds "
                "nothing either — the window line says which"
                + (f"; it last reported {last['value']} at "
                   f"{last['ts'].isoformat()}, "
                   f"{round(last['seconds_before_window'] / 60, 1)} min before this "
                   f"window" if last else
                   ", and this MES holds no earlier sample of it either")
            ) if silent else (
                f"{spoke} of {buckets} buckets of {round(width, 1)} s in this window "
                f"hold a sample ({samples} in all); the rest are drawn as no reading "
                f"rather than as a line through them"
                + ("" if cadence else
                   " — this plant publishes on no configured cadence, so the bucket "
                   "is this product's default rather than a multiple of the rate "
                   "analogs are stored at")
            )),
    }


def analog_trends(session: Session, unit: Equipment, start: datetime,
                  end: datetime, marks: list[dict]) -> dict:
    """One station's analogs over a window, as a panel beside a chart draws them.

    The block behind "what were the process values doing" — the tags chosen,
    each one bucketed through `analysis.tag_trend`, the marks laid on them, and
    the sentence that says how many of the station's signals are drawn.

    Three panels ask it: a point on a control chart, a stretch on the state
    timeline, and a point on the trend graph. They must draw the same tag the
    same way, and a tag drawn twice from two bucketers is the one thing this
    module exists to make impossible.
    """
    cadence, cadence_source = coverage.analog_sample_interval()
    names, total = analog_tags(session, unit, start, end)
    trends = [tag_block(session, unit, name, start, end, marks,
                        cadence, cadence_source) for name in names]
    return {
        "trends": trends,
        "shown": len(trends),
        # Every list states its total: how many of the station's signals are
        # drawn, and how many it published in this window.
        "total": total,
        "note": (None if total <= len(trends) else
                 f"{len(trends)} of the {total} signals this station published in "
                 f"this window are drawn"),
    }


def timeline_block(session: Session, unit: Equipment, start: datetime,
                    end: datetime) -> dict:
    """What the machine was doing across the window, and who watched it.

    `analysis.state_timeline` for this one station, plus the coverage ledger
    for the same window so the chart can print where the unwatched seconds
    went. Both are the plant's own; this adds nothing.
    """
    envelope = analysis.state_timeline(
        session, equipment=[unit.code], window=(start, end), limit=1)
    ledger = coverage.ledger(session, equipment_code=unit.code, equipment_id=unit.id,
                             start=start, end=end)
    return {**envelope, "ledger": ledger.as_json(), "coverage": (
        None if ledger.coverage is None else round(ledger.coverage, 4))}


def state_at(session: Session, unit: Equipment, at: datetime) -> dict:
    """The interval the reading fell in, and the one before it.

    *Running*, *changeover* or *just restarted* is one question with two
    halves, and the second half is the interesting one: a point above the
    limit thirty seconds after a changeover ended is a point about the
    changeover. So the state it was in, and what it had just come out of, with
    the seconds between.

    Both are null when the MES holds no interval covering that instant, which
    happens and is not "the machine was stopped".
    """
    now = (select(EquipmentState)
           .where(EquipmentState.equipment_id == unit.id,
                  EquipmentState.started_at <= at,
                  (EquipmentState.ended_at.is_(None)) | (EquipmentState.ended_at > at))
           .order_by(EquipmentState.started_at.desc()).limit(1))
    here = session.scalars(now).first()
    before = session.scalars(
        select(EquipmentState)
        .where(EquipmentState.equipment_id == unit.id,
               EquipmentState.ended_at.is_not(None),
               EquipmentState.ended_at <= at)
        .order_by(EquipmentState.ended_at.desc()).limit(1)).first()

    def said(row: EquipmentState | None) -> dict | None:
        if row is None:
            return None
        return {"state": getattr(row.state, "value", row.state),
                "reason": row.reason,
                # Null means this MES named it; a word says who else did.
                "reason_source": row.reason_source,
                "started_at": row.started_at, "ended_at": row.ended_at,
                "open": row.ended_at is None}

    return {
        "at": at,
        "state": said(here),
        "seconds_in_state": (None if here is None
                             else round((at - here.started_at).total_seconds(), 1)),
        "previous": said(before),
        "seconds_since_previous_ended": (
            None if before is None or before.ended_at is None
            else round((at - before.ended_at).total_seconds(), 1)),
        "note": None if here is not None else (
            "the MES holds no state interval covering this instant, which is not "
            "the same as the machine having stood still"),
    }


def stops(timeline: dict) -> dict:
    """Every stretch in the window the machine was not running, with its reason.

    Taken off the timeline envelope rather than queried again: two lists of the
    same minutes that could disagree are two lists, and the Gantt beside this
    one is already drawn from these intervals.
    """
    intervals = []
    for machine in timeline.get("machines", []):
        for interval in machine.get("intervals", []):
            if interval.get("state") != "running":
                intervals.append(interval)
    labelled = [i for i in intervals if i.get("reason")]
    return {
        "stops": intervals,
        "total": len(intervals),
        "labelled": len(labelled),
        # House rule 3, on one window: an unlabelled stop is reported as
        # unlabelled rather than folded into anything.
        "unlabelled": len(intervals) - len(labelled),
        "seconds": round(sum(i.get("seconds") or 0.0 for i in intervals), 1),
        "coverage": timeline.get("coverage"),
        "coverage_note": "the same intervals the timeline beside it is drawn from",
    }


# ------------------------------------------------- what else was in the window


def maintenance_orders(session: Session, unit: Equipment | None, start: datetime,
                 end: datetime) -> dict:
    """Maintenance orders on this station that touch the window.

    Touching, not raised in: a job that started an hour earlier and was still
    open when the reading was taken is the answer, and one scoped to orders
    raised inside twelve minutes would never find it. An order with no
    completion is open and treated as reaching the end of the window.
    """
    if unit is None:
        return {"orders": [], "total": 0, "note": "no station is recorded against "
                                                  "this reading, so there is nothing "
                                                  "to look for maintenance on",
                "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE}
    rows = list(session.scalars(
        select(MaintenanceOrder).where(
            MaintenanceOrder.equipment_id == unit.id,
            MaintenanceOrder.raised_at <= end,
            (MaintenanceOrder.completed_at.is_(None))
            | (MaintenanceOrder.completed_at >= start))
        .order_by(MaintenanceOrder.raised_at)))
    return {
        "orders": [{
            "code": row.code, "kind": row.kind.value, "status": row.status.value,
            "summary": row.summary, "reason": row.reason,
            "raised_at": row.raised_at, "started_at": row.started_at,
            "completed_at": row.completed_at, "performed_by": row.performed_by,
            "findings": row.findings, "downtime_minutes": row.downtime_minutes,
            # Whether the job asks for the machine to be stopped, and which
            # stretch of the plant's week it was allowed in. These are what
            # make a down block readable as planned work rather than as a
            # breakdown with a note on it (#158 put both in the data).
            "needs_stop": row.needs_stop,
            "window": None if row.window is None else row.window.value,
            # Who it was given to, when, and by what - a rule's code or a
            # person. Null throughout on an order nobody has handed out.
            "assigned_to": row.assigned_to, "assigned_at": row.assigned_at,
            "assigned_by": row.assigned_by,
        } for row in rows],
        "total": len(rows),
        "note": None,
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


def findings(session: Session, order: str | None, start: datetime,
              end: datetime) -> dict:
    """Non-conformances raised in the window, and which of them share the order.

    Scoped by time and not by machine, because a non-conformance carries an
    order and a lot and no equipment - so narrowing it to a station would mean
    inventing the link. The answer says which scope it used rather than
    letting a plant-wide list look like the filler's.
    """
    rows = list(session.scalars(
        select(NonConformance).where(
            NonConformance.created_at >= start,
            NonConformance.created_at <= end)
        .order_by(NonConformance.created_at)))
    return {
        "nonconformances": [{
            "code": row.code, "description": row.description, "severity": row.severity,
            "status": row.status.value, "created_at": row.created_at,
            "raised_by": row.raised_by, "shift": row.shift_code,
            "disposition": None if row.disposition is None else row.disposition.value,
            "disposition_reason": row.disposition_reason,
            # Whether it is about the order this reading was taken on. Not a
            # filter: the list is every finding in the window, and this says
            # which of them are about the same work.
            "same_order": bool(order and row.work_order_id
                               and order_code_of(session, row.work_order_id) == order),
        } for row in rows],
        "total": len(rows),
        "scope": "every finding raised in this window, whatever machine it was about — "
                 "a non-conformance carries an order and a lot, never a station, and "
                 "narrowing it to one would mean inventing the link",
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


def order_code_of(session: Session, work_order_id: int) -> str | None:
    return session.scalar(select(WorkOrder.code).where(WorkOrder.id == work_order_id))
