"""Why is this reading where it is? One point on a control chart, with its records.

Scott, 2026-10-05: *"a UI to show me what brief non-LLM dashboards could look
like if I wanted to fully understand why a SPC datapoint is where it is just by
clicking on the SPC chart."* This is the read behind that panel, and there is no
model anywhere in it. A control chart says a reading is unusual; the questions a
person asks next are always the same six, and every one of them is already in
this MES's records:

    did the process move, or did the gauge?       the gauge, its calibration,
                                                  and the same characteristic
                                                  measured by the other gauge
                                                  in the same hour
    what was the machine doing?                   the state it was in, and what
                                                  it had just come out of
    what were the process values doing?           the station's analogs over the
                                                  ten minutes before and two
                                                  after, with the reading marked
    who measured it, and when?                    the check's own columns
    what else happened around it?                 the stops, the maintenance
                                                  orders and the findings in
                                                  that window
    how much of that do we actually know?         every block says so

**Nothing here is computed twice.** The chart as it stands, its control limits
and which rule fired come from `services.spc.chart`; the analog trends come from
`services.analysis.tag_trend` and the machine's own timeline from
`state_timeline`, both through the window they grew for this (`_around`); the
gauge's due date and its *due soon* come from `services.gauges`; the window's
coverage comes from the ledger in `services.coverage`. A dossier that did its
own arithmetic would be a second opinion about a plant that only has one.

**It is a plant-computed read, served as an envelope** (decision 0023), so the
screen and - later, as its own handoff - an agent read the same answer. There is
no pass-through here: the panel in `web/spc.js` lays these numbers out and works
out none of them.

Two honesty rules worth naming, because both were easy to get wrong:

- **Unknown is not zero.** A gauge nobody recorded is `null` and says *not
  recorded*; a block with nothing in it states its own total of nothing; a tag
  that stopped arriving is a broken line and not a flat one. The quiet-tag
  cause in the lab's own scenario is precisely a value that looks steady and
  is not there.
- **A comparison of records is not a rate over a watched window.** The
  neighbouring readings, the maintenance orders and the findings carry
  `coverage: "absent"` with the reason, the way the trace reads do. Only the
  blocks that are about a stretch of a machine's time - the timeline and each
  analog - carry a figure.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.domain import (
    Equipment,
    EquipmentState,
    Gauge,
    MaintenanceOrder,
    Material,
    NonConformance,
    QualityCheck,
    QualitySpec,
    SpcSignal,
    TagValue,
    WorkOrder,
)
from fsmes.kernel.tags import STRUCTURAL_TAGS
from fsmes.services import NotFound, analysis, coverage, gauges, spc

#: How far back the window reaches from the reading, and how far past it. Ten
#: minutes of process values before a measurement and two after is what a
#: process engineer asks for at a filler: long enough to hold a fill-weight
#: excursion and the pressure that caused it, short enough that the picture is
#: of this reading rather than of the shift.
#:
#: Product literals, not plant settings, and overridable per request the way
#: `/analysis/tag?hours=` is: the window a reader wants around one point is a
#: property of the question they are asking, not of the plant. A plant that
#: wants a different default is a setting and a Configuration row, and that is
#: a change worth asking for rather than guessing at.
BEFORE_MINUTES = 10.0
AFTER_MINUTES = 2.0

#: How far either side the *other gauge* is looked for. An hour, because what
#: makes a gauge comparison worth anything is that both instruments were
#: measuring the same process - and an hour of a filler's output is the same
#: process, where a shift is not.
NEIGHBOUR_HOURS = 1.0

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

#: And the two ends of the range `_buckets_for` will choose inside when there
#: is a cadence. Fewer than a dozen points is not a trend; a point per second
#: over an hour is a chart nobody can read and a payload nobody wanted.
MIN_BUCKETS = 12
MOST_BUCKETS = 240

#: What the blocks that are lists of records say instead of a coverage figure.
#: `kit.js` reads the word `absent` and prints this sentence rather than
#: "watched —% of the window", which is the exact confusion it is here to stop.
RECORDS_NOT_A_RATE = (
    "a list of the records in this window, not a rate over a watched one — "
    "so there is no coverage figure to give"
)


# ----------------------------------------------------------------- the reading


def _spec(session: Session, material: str, characteristic: str) -> QualitySpec:
    mat = session.scalar(select(Material).where(Material.code == material))
    if mat is None:
        raise NotFound(f"no material {material}")
    spec = session.scalar(select(QualitySpec).where(
        QualitySpec.material_id == mat.id,
        QualitySpec.characteristic == characteristic))
    if spec is None:
        raise NotFound(f"no specification for {material}/{characteristic}")
    return spec


def _check(session: Session, spec: QualitySpec, check_id: int) -> QualityCheck:
    """The reading, refused unless it is a reading of *this* characteristic.

    The id alone would be enough to find the row. It is not enough to answer
    the question: a dossier that fetched check 41 whatever chart it was asked
    from would happily draw a fill weight under a torque specification's
    limits, and every number on the panel would then be wrong in a way that
    looks right.
    """
    check = session.get(QualityCheck, check_id)
    if check is None or check.spec_id != spec.id:
        raise NotFound(
            f"no reading {check_id} of {spec.material.code}/{spec.characteristic}")
    return check


def _equipment(session: Session, check: QualityCheck) -> Equipment | None:
    """The station that took the reading, or None.

    Null is *not recorded* and never "no machine": a person with a gauge
    records none, and working one out from the order's route would name a
    machine nobody stood at (`domain.quality`).
    """
    if not check.equipment_id:
        return None
    return session.get(Equipment, check.equipment_id)


def _order_code(session: Session, check: QualityCheck) -> str | None:
    if not check.work_order_id:
        return None
    return session.scalar(select(WorkOrder.code).where(WorkOrder.id == check.work_order_id))


def _reading(session: Session, spec: QualitySpec, check: QualityCheck,
             unit: Equipment | None) -> dict:
    """Who, when, what it read, and against what."""
    return {
        "check": check.id,
        "value": check.value,
        "unit": spec.unit,
        "ts": check.ts,
        "result": check.result.value,
        "lower_spec": spec.min_value,
        "upper_spec": spec.max_value,
        # The MES's own verdict is `result`; this says which side it was on,
        # and is null when the specification is one-sided or open.
        "below_spec": (spec.min_value is not None and check.value < spec.min_value),
        "above_spec": (spec.max_value is not None and check.value > spec.max_value),
        # Null means not recorded, on every one of these.
        "checked_by": check.checked_by,
        "work_order": _order_code(session, check),
        "shift": check.shift_code,
        "shift_day": check.shift_day,
        "equipment": None if unit is None else {"code": unit.code, "name": unit.name},
        "source_system": check.source_system,
        # Their verdict, when another system sent one. The two disagreeing is a
        # finding, not an error, and losing theirs would hide it.
        "supplied_result": None if check.supplied_result is None else check.supplied_result.value,
    }


# ------------------------------------------------------- the chart, and the rule


def _on_the_chart(session: Session, material: str, characteristic: str,
                  check: QualityCheck) -> dict:
    """The chart this reading is a point on, and the rule that fired on it.

    Read from `spc.chart`, which is the one place that computes control limits
    and runs the rules. Recomputing them here would give slightly different
    numbers from the chart the reader is looking at, which is the whole reason
    `SpcSignal` stores the window it fired on.

    A reading older than this plant's `[quality] spc_history` is not on the
    chart at all; `on_chart` is false and says so rather than pretending the
    rules were silent about it.

    `moving_range` is this reading's point on the other half of the chart -
    the gap between it and the reading before it, the limits that gap was
    judged against, and whether it was flagged. It is `null` for the first
    reading on the chart, which has nothing before it to be a gap from, and
    that is a different fact from a gap of nought.
    """
    drawn = spc.chart(session, material, characteristic)
    ids = [p.get("check") for p in drawn.get("points", [])]
    index = ids.index(check.id) if check.id in ids else None
    signal = None
    if index is not None:
        signal = next((s for s in drawn.get("signals", []) if s.get("index") == index), None)
    return {
        "on_chart": index is not None,
        # One-based, the way the signals table on the page counts readings.
        "reading": None if index is None else index + 1,
        "readings": drawn.get("n"),
        "control": drawn.get("control"),
        "capability": drawn.get("capability"),
        "stable": drawn.get("stable"),
        "verdict": drawn.get("verdict") or drawn.get("note"),
        "note": None if index is not None else (
            f"this reading is older than the {drawn.get('history')} the chart "
            f"draws, so it is not one of the points on it"),
        "rules": drawn.get("rules"),
        "hold_rules": drawn.get("hold_rules"),
        "signal": signal,
        "moving_range": _moving_range_at(drawn, check.id),
    }


def _moving_range_at(drawn: dict, check_id: int) -> dict | None:
    """This reading's point on the moving-range half, or null if it has none.

    Found by check id rather than by counting along, because the moving-range
    series is one shorter than the readings and an off-by-one here would put a
    person in front of the wrong pair of readings.
    """
    moving = drawn.get("moving_range") or {}
    point = next((p for p in moving.get("points", []) if p.get("check") == check_id), None)
    if point is None:
        return None
    return {
        "range": point.get("range"),
        # The other reading of the pair, by id, so the panel can say *from
        # which* without the reader counting dots.
        "previous_check": point.get("previous_check"),
        "centre": moving.get("centre"),
        "upper": moving.get("upper"),
        "lower": moving.get("lower"),
        "flagged": any(s.get("check") == check_id
                       for s in moving.get("signals", [])),
    }


def _recorded_signals(session: Session, check: QualityCheck) -> dict:
    """Every rule firing the MES wrote down *on this reading*, with its hold.

    Not the same question as what the chart says now. A rule is judged when a
    reading is recorded; the chart is drawn when somebody opens it, over a
    history that has moved since. These rows are what the MES actually acted
    on, carrying the chart as it stood at the time.
    """
    rows = list(session.scalars(
        select(SpcSignal).where(SpcSignal.check_id == check.id)
        .order_by(SpcSignal.rule)))
    out = []
    for row in rows:
        nc = session.get(NonConformance, row.nonconformance_id) if row.nonconformance_id else None
        out.append({
            "rule": row.rule, "what": row.what, "ts": row.ts,
            "window_key": row.window_key, "window": row.window,
            "nonconformance": None if nc is None else nc.code,
            "nonconformance_status": None if nc is None else nc.status.value,
        })
    return {"signals": out, "total": len(out),
            "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE}


# ------------------------------------------------------------------- the gauge


def _gauge_block(session: Session, spec: QualitySpec, check: QualityCheck) -> dict:
    """Which instrument took the reading, and whether it could be believed.

    `null` for the gauge is *not recorded* - which is the honest answer on a
    plant whose floor has never written one down, and the one the panel has to
    be able to say out loud rather than leaving the first question anybody
    asks about a point looking answered.
    """
    if not check.gauge_id:
        return {"gauge": None, "resolution_check": None, "last_calibration": None,
                "note": "no gauge is recorded against this reading, which is not the "
                        "same as no gauge having taken it"}
    gauge = session.get(Gauge, check.gauge_id)
    if gauge is None:
        # A foreign key with nothing behind it. Say so; a blank would read as
        # "nobody recorded one", and those are different facts.
        return {"gauge": None, "resolution_check": None, "last_calibration": None,
                "note": f"this reading names gauge {check.gauge_id}, which is no "
                        f"longer on the register"}
    block = {
        "gauge": gauges.state(session, gauge.code),
        "last_calibration": gauges.last_calibration(session, gauge.code),
        "resolution_check": None,
        "note": None,
    }
    if spec.min_value is not None and spec.max_value is not None:
        # The rule of ten against the tolerance this reading was judged by. A
        # gauge too coarse for it means the control chart is charting the
        # instrument, which is the one answer that makes every other block on
        # the panel beside the point.
        block["resolution_check"] = gauges.resolution_check(
            session, gauge.code, spec.max_value - spec.min_value)
    return block


def _neighbours(session: Session, spec: QualitySpec, check: QualityCheck,
                hours: float) -> dict:
    """The same characteristic, by every gauge, within an hour either side.

    This is the comparison that separates *the process moved* from *the gauge
    moved*, and it is the only honest way to ask it from records alone: two
    instruments measuring the same process in the same hour should agree, and
    a persistent difference between them is the instrument.

    It is still the weak end of the evidence and the answer says so, because
    they measured different bottles. A controlled comparison is one piece
    measured twice, and nothing in this MES can make the floor do that.
    """
    span = timedelta(hours=hours)
    rows = list(session.scalars(
        select(QualityCheck).where(
            QualityCheck.spec_id == spec.id,
            QualityCheck.ts >= check.ts - span,
            QualityCheck.ts <= check.ts + span)
        .order_by(QualityCheck.ts)))

    codes = {g.id: g.code for g in session.scalars(
        select(Gauge).where(Gauge.id.in_({r.gauge_id for r in rows if r.gauge_id})))}

    groups: dict[str | None, list[QualityCheck]] = {}
    for row in rows:
        groups.setdefault(codes.get(row.gauge_id), []).append(row)

    mine = codes.get(check.gauge_id)
    here = groups.get(mine, [])
    my_mean = (sum(r.value for r in here) / len(here)) if here else None

    by_gauge = []
    for code, group in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or "")):
        values = [r.value for r in group]
        mean = sum(values) / len(values)
        by_gauge.append({
            "gauge": code,
            "readings": len(values),
            "mean": round(mean, 4),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            # How far this gauge sat from the one that took the reading. Null
            # for the reading's own gauge, and null when that gauge is not
            # recorded - there is nothing to be different from.
            "difference_to_this_gauge": (
                None if (my_mean is None or code == mine) else round(mean - my_mean, 4)),
            "this_reading": code == mine and check.gauge_id is not None,
            # The honest name for the null key, said once per row rather than
            # left for the screen to invent.
            "note": None if code is not None else "no gauge recorded against these",
        })

    return {
        "hours": hours,
        "start": check.ts - span,
        "end": check.ts + span,
        "by_gauge": by_gauge,
        "gauges": len(by_gauge),
        "total": len(rows),
        "this_gauge": mine,
        "note": (
            "two instruments measuring the same process in the same hour should "
            "agree; a standing difference between them is the instrument. They "
            "measured different pieces, so this bounds the question rather than "
            "settling it — one piece measured on both gauges is the controlled "
            "comparison, and nothing here can make the floor do that"),
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


# --------------------------------------------------------------- the machine


def _analog_tags(session: Session, unit: Equipment, start: datetime,
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


def _buckets_for(span: float, cadence: float | None) -> int:
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


def _bucketed(envelope: dict, buckets: int) -> tuple[list[dict], int, float]:
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


def _last_before(session: Session, unit: Equipment, tag: str,
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


def _tag_block(session: Session, unit: Equipment, tag: str, start: datetime,
               end: datetime, at: datetime, cadence: float | None,
               cadence_source: str | None) -> dict:
    """One of the station's analogs over the window, with the reading marked.

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
    buckets = _buckets_for(span, cadence)
    envelope = analysis.tag_trend(session, unit.code, tag=tag,
                                  window=(start, end), buckets=buckets)
    points, empty, width = _bucketed(envelope, buckets)
    samples = sum(p.get("n", 0) for p in points)
    spoke = buckets - empty
    # Before the window DRAWN, for the same reason the grid is laid against it:
    # a sample inside the window asked for but outside the one realised is
    # exactly the sample a reader looking at an empty chart needs.
    last = _last_before(session, unit, tag, drawn.get("start") or start)
    silent = samples == 0
    return {
        **envelope,
        "points": points,
        # The reading this panel is about, on the tag's own clock. This is what
        # makes the picture answer the question: a pressure dip and a fill
        # weight are only an explanation if they are read against each other.
        "markers": [{"t": at, "label": "this reading"}],
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


def _timeline_block(session: Session, unit: Equipment, start: datetime,
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


def _state_at(session: Session, unit: Equipment, at: datetime) -> dict:
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


def _stops(timeline: dict) -> dict:
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


def _maintenance(session: Session, unit: Equipment | None, start: datetime,
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
        } for row in rows],
        "total": len(rows),
        "note": None,
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


def _findings(session: Session, check: QualityCheck, start: datetime,
              end: datetime) -> dict:
    """Non-conformances raised in the window, and the one this reading raised.

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
    order = _order_code(session, check)
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
                               and _order_code_of(session, row.work_order_id) == order),
        } for row in rows],
        "total": len(rows),
        "scope": "every finding raised in this window, whatever machine it was about — "
                 "a non-conformance carries an order and a lot, never a station, and "
                 "narrowing it to one would mean inventing the link",
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


def _order_code_of(session: Session, work_order_id: int) -> str | None:
    return session.scalar(select(WorkOrder.code).where(WorkOrder.id == work_order_id))


# ------------------------------------------------------------------- the whole


def dossier(session: Session, material: str, characteristic: str, check_id: int, *,
            before_minutes: float = BEFORE_MINUTES,
            after_minutes: float = AFTER_MINUTES,
            neighbour_hours: float = NEIGHBOUR_HOURS) -> dict:
    """Everything this MES holds about why one reading is where it is.

    One read, so that a panel opens on one round trip rather than on nine - and
    so that an agent, when it gets this as a tool, asks one question and is
    given one answer with every block's coverage already on it.
    """
    spec = _spec(session, material, characteristic)
    check = _check(session, spec, check_id)
    unit = _equipment(session, check)
    start = check.ts - timedelta(minutes=before_minutes)
    end = check.ts + timedelta(minutes=after_minutes)

    chart = _on_the_chart(session, material, characteristic, check)

    tags: list[dict] = []
    tags_total = 0
    timeline: dict | None = None
    machine: dict | None = None
    cadence, cadence_source = coverage.analog_sample_interval()
    if unit is not None:
        names, tags_total = _analog_tags(session, unit, start, end)
        tags = [_tag_block(session, unit, name, start, end, check.ts,
                           cadence, cadence_source) for name in names]
        timeline = _timeline_block(session, unit, start, end)
        machine = _state_at(session, unit, check.ts)

    return {
        "material": material,
        "characteristic": characteristic,
        "unit": spec.unit,
        "window": {
            "start": start, "end": end,
            "before_minutes": before_minutes, "after_minutes": after_minutes,
            "at": check.ts,
        },
        "reading": _reading(session, spec, check, unit),
        "chart": chart,
        "recorded": _recorded_signals(session, check),
        "gauge": _gauge_block(session, spec, check),
        "neighbours": _neighbours(session, spec, check, neighbour_hours),
        "machine": machine,
        "timeline": timeline,
        "stops": None if timeline is None else _stops(timeline),
        "tags": {
            "trends": tags,
            "shown": len(tags),
            # Every list states its total: how many of the station's signals
            # are drawn, and how many it published in this window.
            "total": tags_total,
            "note": (None if unit is None else (
                None if tags_total <= len(tags) else
                f"{len(tags)} of the {tags_total} signals this station published in "
                f"this window are drawn")),
        },
        "maintenance": _maintenance(session, unit, start, end),
        "findings": _findings(session, check, start, end),
        # The station's window, as the one figure the panel's own header states.
        # Null when no station is recorded: there is no machine whose watched
        # time this could be a share of.
        "coverage": None if timeline is None else timeline.get("coverage"),
        "coverage_note": (
            None if timeline is not None else
            "no station is recorded against this reading, so how much of this "
            "window anybody watched is not a question this panel can answer"),
    }
