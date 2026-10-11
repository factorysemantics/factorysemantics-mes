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
Since 2026-10-06 the same six questions are answered of a SAMPLE as well as
of a reading (`sample_dossier`). A point on an X-bar chart is the average of n
pieces, so two things change and nothing else does: the n readings behind the
point are a block of their own, each with its distance from the sample's own
mean and the furthest named, and the window is the stretch the n readings span
plus the minutes before rather than an instant - five bottles take minutes to
measure, and a window centred on one stamp would leave the pressure dip that
caused them outside the picture.

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
    EquipmentLevel,
    EquipmentState,
    EquipmentStateName,
    Gauge,
    Material,
    NonConformance,
    QualityCheck,
    QualityCount,
    QualitySample,
    QualitySpec,
    Routing,
    RoutingOperation,
    SpcSignal,
    WorkOrder,
)
from fsmes.services import NotFound, gauges, moment, spc

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


#: The most of the line's other stations one panel reads in full. A line of
#: sixty work units is a real plant and a panel that read every one of them
#: would be sixty timeline reads deep; the ones with something in the window
#: are drawn to this many and the count of the rest is stated, which is the
#: same bound and the same sentence the stops table and the analogs already
#: carry.
MOST_LINE_STATIONS = 8

#: What the blocks that are lists of records say instead of a coverage figure
#: - the control chart's own sentence, from `services.spc`, because the chart
#: and the dossier behind a point on it are lists of the same records and must
#: not word the same refusal two ways.
RECORDS_NOT_A_RATE = spc.RECORDS_NOT_A_RATE


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


def _gauge_block(session: Session, spec: QualitySpec, gauge_id: int | None,
                 *, subject: str = "this reading") -> dict:
    """Which instrument took the reading, and whether it could be believed.

    `null` for the gauge is *not recorded* - which is the honest answer on a
    plant whose floor has never written one down, and the one the panel has to
    be able to say out loud rather than leaving the first question anybody
    asks about a point looking answered.
    """
    if not gauge_id:
        return {"gauge": None, "resolution_check": None, "last_calibration": None,
                "note": f"no gauge is recorded against {subject}, which is not the "
                        f"same as no gauge having taken it"}
    gauge = session.get(Gauge, gauge_id)
    if gauge is None:
        # A foreign key with nothing behind it. Say so; a blank would read as
        # "nobody recorded one", and those are different facts.
        return {"gauge": None, "resolution_check": None, "last_calibration": None,
                "note": f"{subject} names gauge {gauge_id}, which is no "
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


def _neighbours(session: Session, spec: QualitySpec, *, at: datetime,
                gauge_id: int | None, hours: float) -> dict:
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
            QualityCheck.ts >= at - span,
            QualityCheck.ts <= at + span)
        .order_by(QualityCheck.ts)))

    codes = {g.id: g.code for g in session.scalars(
        select(Gauge).where(Gauge.id.in_({r.gauge_id for r in rows if r.gauge_id})))}

    groups: dict[str | None, list[QualityCheck]] = {}
    for row in rows:
        groups.setdefault(codes.get(row.gauge_id), []).append(row)

    mine = codes.get(gauge_id)
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
            "this_reading": code == mine and gauge_id is not None,
            # The honest name for the null key, said once per row rather than
            # left for the screen to invent.
            "note": None if code is not None else "no gauge recorded against these",
        })

    return {
        "hours": hours,
        "start": at - span,
        "end": at + span,
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




# ------------------------------------------------- the rest of the line


def _routing_sequence(session: Session, material_id: int) -> tuple[dict[int, int], list[str]]:
    """{equipment id: the step number this material's routing gives it}.

    The routing is the only recorded statement this MES holds about the order
    of the stations on a line, and it is a statement about *this material's*
    route rather than about the line's plumbing - so it orders the block where
    it names a station and says nothing where it does not.

    It is deliberately not read as upstream and downstream. Bottling's own
    routing runs Load → Denest → Wash → Inspect → Fill → Palletise while the
    bottles come off the filler to the inspection bench, so "the station
    before this one" worked out from the sequence would be wrong on the very
    plant this block was written for. A step number is a step number.

    The lowest step per station when a material has more than one routing, and
    the routing codes come back so the answer can say which were read.
    """
    rows = list(session.execute(
        select(RoutingOperation.equipment_id, func.min(RoutingOperation.seq),
               func.min(Routing.code))
        .join(Routing, Routing.id == RoutingOperation.routing_id)
        .where(Routing.material_id == material_id,
               RoutingOperation.equipment_id.is_not(None))
        .group_by(RoutingOperation.equipment_id)))
    codes = sorted({row[2] for row in rows})
    return {row[0]: row[1] for row in rows}, codes


def _line_events(session: Session, station_ids: list[int], start: datetime,
                 end: datetime) -> set[int]:
    """Which of these stations recorded a stretch of not running in the window.

    One query over the same table `moment.timeline_block` reads, used only to choose
    which stations are worth reading in full: a line of sixty work units would
    otherwise be sixty timeline reads and sixty coverage ledgers for a panel
    that draws eight. Every fact the block then states comes from the timeline
    itself, so there is one source for the numbers and this is a filter.
    """
    if not station_ids:
        return set()
    return set(session.scalars(
        select(EquipmentState.equipment_id)
        .where(EquipmentState.equipment_id.in_(station_ids),
               EquipmentState.state != EquipmentStateName.RUNNING,
               EquipmentState.started_at < end,
               (EquipmentState.ended_at.is_(None))
               | (EquipmentState.ended_at > start))
        .distinct()))


def _placed_against(interval: dict, at: datetime) -> dict:
    """The same interval, with where it sits relative to the reading on it.

    *Ended three minutes before this sample* is the sentence that makes a row
    on another machine mean anything, and it is arithmetic - so it is done here
    rather than in the panel. `web/spc.js` lays these numbers out and works out
    none of them (house rule 6), and a panel subtracting its own timestamps
    would be a second opinion about a plant that has one.

    Three different facts and three keys, because they are not interchangeable:
    the reading fell inside the stretch; the stretch ended before it; the
    stretch began after it. An interval still open at the end of the window has
    no end to measure from and says so through `open`, which the timeline
    already carries.
    """
    began, finished = interval.get("start"), interval.get("end")
    inside = (began is not None and finished is not None
              and began <= at <= finished)
    return {
        **interval,
        "over_the_reading": inside,
        "ended_seconds_before": (
            None if inside or finished is None or interval.get("open")
            or finished > at else round((at - finished).total_seconds(), 1)),
        "started_seconds_after": (
            None if inside or began is None or began <= at
            else round((began - at).total_seconds(), 1)),
    }


def _line_block(session: Session, unit: Equipment | None, start: datetime,
                end: datetime, *, at: datetime, material_id: int,
                noun: str = "reading") -> dict:
    """What the rest of this line was doing in the same window.

    The block that #143 and #147 both asked for in the same words: *"the panel
    answers what happened at this station, and the planted cause is a station
    upstream."* A fill-height sample taken at the inspection bench twenty
    minutes after the filler changed over is a sample about the changeover, and
    a panel scoped to one station cannot say so however honest it is.

    **Nothing here is inferred.** Two things are recorded and both are used:
    every work unit has a parent (`Equipment.parent_id`), and a stretch of not
    running is an interval with a reason on it. The block is the *siblings under
    the same parent* - it names that parent, so a reader on a plant modelled
    line → cell → machine can see it scoped to the cell - each with its own
    stops and changeovers in this window, read from `moment.timeline_block` and split
    by `moment.stops`, which is the same source and the same definition the station's
    own block above uses. There is no upstream and no downstream in it.

    Bounded: the stations with something in the window are read in full, to
    `MOST_LINE_STATIONS` of them, and the ones that recorded nothing come back
    by name in `quiet` with the count. A station in `quiet` recorded no stretch
    of not running in this window; that is a statement about records and not
    about the machine, which is why it is worded that way and why the note says
    so.
    """
    empty = {
        "parent": None, "stations": [], "shown": 0, "total": 0,
        "with_events": 0, "quiet": [], "routings": [],
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }
    if unit is None:
        return {**empty, "note": (
            f"no station is recorded against this {noun}, so there is no line "
            f"whose other stations this block could draw")}
    parent = unit.parent
    if parent is None:
        return {**empty, "note": (
            f"{unit.code} hangs under nothing in this plant's equipment tree, so "
            f"*the rest of the line* is not a question this MES can answer about "
            f"it — there is no recorded line to be the rest of")}

    siblings = list(session.scalars(
        select(Equipment)
        .where(Equipment.parent_id == parent.id,
               Equipment.id != unit.id,
               Equipment.level == EquipmentLevel.WORK_UNIT)
        .order_by(Equipment.code)))
    seq, routings = _routing_sequence(session, material_id)
    # Routing order where the routing names the station, the rest after it by
    # code. A station the routing says nothing about is not sorted into a
    # position it never had.
    siblings.sort(key=lambda eq: (0, seq[eq.id], eq.code) if eq.id in seq
                  else (1, 0, eq.code))

    if not siblings:
        return {**empty, "parent": {"code": parent.code, "name": parent.name},
                "routings": routings, "note": (
                    f"{unit.code} is the only work unit under {parent.code}, so "
                    f"there is no rest of the line to draw")}

    spoke = _line_events(session, [eq.id for eq in siblings], start, end)
    busy = [eq for eq in siblings if eq.id in spoke]
    quiet = [eq for eq in siblings if eq.id not in spoke]
    drawn = busy[:MOST_LINE_STATIONS]

    stations = []
    for eq in drawn:
        timeline = moment.timeline_block(session, eq, start, end)
        intervals = moment.stops(timeline)
        placed = [_placed_against(row, at) for row in intervals["stops"]]
        changeovers = [i for i in placed if i.get("state") == "setup"]
        stops = [i for i in placed if i.get("state") != "setup"]
        stations.append({
            "code": eq.code,
            "name": eq.name,
            # Null where this material's routing does not name the station,
            # which is a normal state and not a gap to be filled in.
            "routing_seq": seq.get(eq.id),
            # A changeover is called out from the other stops rather than
            # counted among them, because the pack's own words are that "a
            # machine in setup on this line is the whole line changing over" -
            # and it was the changeover neither panel could name. Both lists
            # come off one `moment.stops` of one timeline, so the two cannot disagree
            # about the same minutes, and `not_running_total` is their sum.
            "changeovers": changeovers,
            "stops": stops,
            "not_running_total": intervals["total"],
            "labelled": intervals["labelled"],
            "unlabelled": intervals["unlabelled"],
            "seconds": intervals["seconds"],
            "coverage": timeline.get("coverage"),
            "coverage_note": (
                "how much of this window anybody was watching this station"),
        })

    rest = len(busy) - len(drawn)
    note = (
        f"{len(stations)} of the {len(siblings)} other work unit"
        f"{'' if len(siblings) == 1 else 's'} under {parent.code} drawn — the ones "
        f"that recorded a stop or a changeover in this window"
        + (f", {rest} more of which did and are not drawn" if rest else "")
        + (f". {len(quiet)} recorded nothing but running in it, which is a "
           f"statement about what this MES holds and not about what the machine "
           f"did." if quiet else ".")
        + (f" Ordered by {', '.join(routings)}, this material's routing, where it "
           f"names the station; the rest after it by code."
           if routings else
           " No routing of this material names a station, so the order is by code "
           "and no step number is given.")
    )
    return {
        "parent": {"code": parent.code, "name": parent.name},
        "stations": stations,
        "shown": len(stations),
        # Every list states its total: the other work units under this parent,
        # drawn or not.
        "total": len(siblings),
        "with_events": len(busy),
        "quiet": [{"code": eq.code, "name": eq.name, "routing_seq": seq.get(eq.id)}
                  for eq in quiet],
        "routings": routings,
        "note": note,
        # The same word the other record lists carry: this is a list of what
        # was recorded on other machines in a window, not a rate over a watched
        # one. Each station inside it carries its own watched share.
        "coverage": "absent",
        "coverage_note": RECORDS_NOT_A_RATE,
    }




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
    if unit is not None:
        marks = [{"t": check.ts, "label": "this reading"}]
        analogs = moment.analog_trends(session, unit, start, end, marks)
        tags, tags_total = analogs["trends"], analogs["total"]
        timeline = moment.timeline_block(session, unit, start, end)
        machine = moment.state_at(session, unit, check.ts)

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
        "gauge": _gauge_block(session, spec, check.gauge_id),
        "neighbours": _neighbours(session, spec, at=check.ts,
                                  gauge_id=check.gauge_id, hours=neighbour_hours),
        "machine": machine,
        "timeline": timeline,
        "stops": None if timeline is None else moment.stops(timeline),
        # And what the rest of the same line was doing in the same window. The
        # block the station's own answer cannot hold: four of the five causes
        # planted in the lab were findable at the station, and the two that
        # were not - the washer's hot-running stop and the line's changeover -
        # happened on a machine beside it.
        "line": _line_block(session, unit, start, end, at=check.ts,
                            material_id=spec.material_id, noun="reading"),
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
        "maintenance": moment.maintenance_orders(session, unit, start, end),
        "findings": moment.findings(session, _order_code(session, check), start, end),
        # The station's window, as the one figure the panel's own header states.
        # Null when no station is recorded: there is no machine whose watched
        # time this could be a share of.
        "coverage": None if timeline is None else timeline.get("coverage"),
        "coverage_note": (
            None if timeline is not None else
            "no station is recorded against this reading, so how much of this "
            "window anybody watched is not a question this panel can answer"),
    }


# ------------------------------------------------- the same question, of a sample

def _sample(session: Session, spec: QualitySpec, sample_id: int) -> QualitySample:
    """The sample, refused unless it is a sample of *this* characteristic.

    The same refusal `_check` makes and for the same reason: an id alone would
    find the row, and a dossier that drew sample 41's five bottles under a
    torque specification's limits would have every number on the panel wrong
    in a way that looks right.
    """
    sample = session.get(QualitySample, sample_id)
    if sample is None or sample.spec_id != spec.id:
        raise NotFound(
            f"no sample {sample_id} of {spec.material.code}/{spec.characteristic}")
    return sample


def _sample_readings(session: Session, sample: QualitySample) -> list[QualityCheck]:
    """The readings recorded under this sample, oldest id first."""
    return list(session.scalars(
        select(QualityCheck).where(QualityCheck.sample_id == sample.id)
        .order_by(QualityCheck.id)))


def _readings_block(session: Session, spec: QualitySpec,
                    readings: list[QualityCheck]) -> dict:
    """The n pieces behind one point, and which of them pulled the average.

    The question a person asks of a sampled chart is not the one they ask of an
    individuals chart. A point here is an average, and an average can sit above
    its limit because every piece was high or because one was very high - two
    different plants, two different mornings. So each reading says how far it
    sat from the sample's own mean, and the one furthest out is named.

    *Furthest from the mean* is arithmetic and not blame, and the sentence says
    so: in a sample of five bottles one of them is always the furthest out, and
    on a settled process that means nothing whatever. It is the mean's distance
    from the centre line, on the chart, that makes it worth looking at - and
    that is the upper chart's answer, not this block's.
    """
    values = [c.value for c in readings]
    mean = (sum(values) / len(values)) if values else None
    codes = {g.id: g.code for g in session.scalars(
        select(Gauge).where(Gauge.id.in_({c.gauge_id for c in readings if c.gauge_id})))}
    gaps = [abs(c.value - mean) for c in readings] if mean is not None else []
    widest = max(gaps) if gaps else None
    rows = []
    for i, check in enumerate(readings):
        rows.append({
            "check": check.id,
            # Which of the n this is, in the order they were recorded. A
            # person at the filler took them in this order and the telemetry
            # beside this block is marked in it.
            "position": i + 1,
            "value": check.value,
            "ts": check.ts,
            "result": check.result.value,
            "gauge": codes.get(check.gauge_id),
            "checked_by": check.checked_by,
            # Signed, because *high* and *low* are different findings, and the
            # absolute figure beside it because that is what *furthest* means.
            "difference_to_mean": None if mean is None else round(check.value - mean, 4),
            "distance_from_mean": None if mean is None else round(abs(check.value - mean), 4),
            # Every reading tied at the widest distance is named, not the
            # first of them: a sample of five where two sat equally far out is
            # a different fact from one where one did.
            "furthest": (widest is not None and widest > 0
                         and abs(check.value - mean) == widest),
            "below_spec": (spec.min_value is not None and check.value < spec.min_value),
            "above_spec": (spec.max_value is not None and check.value > spec.max_value),
            # Null means not recorded, here as everywhere.
            "gauge_note": None if check.gauge_id else "no gauge recorded against this reading",
        })
    furthest = [r["check"] for r in rows if r["furthest"]]
    return {
        "readings": rows,
        "total": len(rows),
        "mean": None if mean is None else round(mean, 4),
        "range": None if not values else round(max(values) - min(values), 4),
        "min": None if not values else min(values),
        "max": None if not values else max(values),
        "furthest": furthest,
        "note": (
            "one of these is always the furthest from the sample's own mean, and "
            "on a settled process that means nothing at all — it is where the "
            "MEAN sat against the centre line that makes this point worth "
            "opening, which is the upper chart's answer and not this table's"
            # No reading is named when they are all the same distance from
            # the mean - which, in a sample, means they are all the same
            # reading. Naming the first of them would be inventing a finding
            # out of the order somebody typed them in.
            + ("" if furthest or not rows else
               "; every reading here is the same, so none of them is the one "
               "that pulled it")
            + ("" if len(furthest) < 2 else
               f"; {len(furthest)} of them sit equally far out and all are named")),
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


def _sample_on_the_chart(session: Session, material: str, characteristic: str,
                         sample: QualitySample) -> dict:
    """The chart this sample is a point on, both halves of it, and what fired.

    Read from `spc.chart` for the same reason the reading's block is: it is the
    one place control limits are computed, and a second opinion here would be
    slightly different numbers under the chart the reader is looking at.

    Both halves, because a sampled chart has two and the reader came from one
    of them: the mean against `X̿ ± A2·R̄` above, the spread against `D4·R̄`
    below. `signals` is a list and not one firing - rules 1 to 4 judge where
    the mean sat and rule 5 judges the spread beside it, and both are about
    this one sample, so two of them can be true at once.
    """
    drawn = spc.chart(session, material, characteristic)
    keys = [p.get("sample") for p in drawn.get("points", [])]
    index = keys.index(sample.id) if sample.id in keys else None
    point = drawn["points"][index] if index is not None else None
    signals = ([s for s in drawn.get("signals", []) if s.get("index") == index]
               if index is not None else [])
    lower = drawn.get("range_chart") or {}
    return {
        "on_chart": index is not None,
        # One-based, the way the signals table on the page counts samples.
        "sample": None if index is None else index + 1,
        "samples": drawn.get("n"),
        "readings": drawn.get("readings"),
        "sample_size": drawn.get("sample_size"),
        # The two figures this point is, read off the chart rather than
        # recomputed: the mean plotted above and the range plotted below.
        "mean": None if point is None else point.get("value"),
        "range": None if point is None else point.get("range"),
        "control": drawn.get("control"),
        "capability": drawn.get("capability"),
        "stable": drawn.get("stable"),
        "verdict": drawn.get("verdict") or drawn.get("note"),
        "note": None if index is not None else (
            f"this sample is not one of the {drawn.get('n')} points on the chart: "
            f"either it is older than the history the chart draws, or it holds a "
            f"different number of readings than the {drawn.get('sample_size')} this "
            f"specification now asks for"
            + (f" — {drawn['set_aside']} stored sample(s) are left out for that "
               f"reason" if drawn.get("set_aside") else "")),
        "rules": drawn.get("rules"),
        "hold_rules": drawn.get("hold_rules"),
        "signals": signals,
        "set_aside": drawn.get("set_aside"),
        # The lower half's limits and its own sentence, with whether THIS
        # sample's spread is the one that went beyond them.
        "range_chart": {
            "centre": lower.get("centre"),
            "upper": lower.get("upper"),
            "lower": lower.get("lower"),
            "n": lower.get("n"),
            "stable": lower.get("stable"),
            "verdict": lower.get("verdict"),
            "flagged": any(s.get("rule") == spc.RANGE_RULE for s in signals),
        },
    }


def _recorded_for_sample(session: Session, readings: list[QualityCheck]) -> dict:
    """Every rule firing the MES wrote down on any reading of this sample.

    A sampled characteristic's signal is recorded against the LAST reading of
    the sample (`services.spc.evaluate`), so that the row still names something
    a person can open. Looking only at that one reading would be right today
    and wrong the first time that changes, so this asks about all n of them and
    says which reading each firing was written against.
    """
    ids = [c.id for c in readings]
    rows = [] if not ids else list(session.scalars(
        select(SpcSignal).where(SpcSignal.check_id.in_(ids))
        .order_by(SpcSignal.rule)))
    out = []
    for row in rows:
        nc = session.get(NonConformance, row.nonconformance_id) if row.nonconformance_id else None
        out.append({
            "rule": row.rule, "what": row.what, "ts": row.ts,
            "check": row.check_id,
            "window_key": row.window_key, "window": row.window,
            "nonconformance": None if nc is None else nc.code,
            "nonconformance_status": None if nc is None else nc.status.value,
        })
    return {"signals": out, "total": len(out),
            "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE}


def sample_dossier(session: Session, material: str, characteristic: str,
                   sample_id: int, *,
                   before_minutes: float = BEFORE_MINUTES,
                   after_minutes: float = AFTER_MINUTES,
                   neighbour_hours: float = NEIGHBOUR_HOURS) -> dict:
    """Everything this MES holds about why one SAMPLE is where it is.

    The same six questions and the same blocks as `dossier`, asked of a point
    that is an average rather than a reading. Two things are genuinely
    different and both are in here:

    - **The n readings behind the point.** An average above its limit because
      every bottle was high and an average above its limit because one bottle
      was very high are two different mornings, so the five rows, each one's
      distance from the sample's own mean, and the one furthest out.
    - **The window is the one the sample spans, not an instant.** Five bottles
      are measured over minutes; the telemetry runs from the minutes before the
      FIRST of them to the last plus the usual tail, and every one of the n is
      marked on each trend. A window centred on the sample's stamp would put
      the pressure dip that caused it outside the picture.

    Nothing here is computed twice, exactly as in `dossier`: the limits and the
    firings are `spc.chart`'s, the trends are `analysis.tag_trend`'s, the
    timeline is `state_timeline`'s, the gauge's due date is the register's.
    """
    spec = _spec(session, material, characteristic)
    sample = _sample(session, spec, sample_id)
    readings = _sample_readings(session, sample)

    # The sample's own station and gauge, falling back to nothing rather than
    # to a reading's: a sample is one act of measurement and the row that holds
    # it is where that fact lives (`domain.quality`).
    unit = None if not sample.equipment_id else session.get(Equipment, sample.equipment_id)
    order = (None if not sample.work_order_id else
             moment.order_code_of(session, sample.work_order_id))

    stamps = [c.ts for c in readings] or [sample.ts]
    first, last = min(stamps), max(stamps)
    start = first - timedelta(minutes=before_minutes)
    end = max(last, sample.ts) + timedelta(minutes=after_minutes)

    chart = _sample_on_the_chart(session, material, characteristic, sample)
    behind = _readings_block(session, spec, readings)

    marks = [{"t": row["ts"], "label": f"reading {row['position']}"}
             for row in behind["readings"]] or [{"t": sample.ts, "label": "this sample"}]

    tags: list[dict] = []
    tags_total = 0
    timeline: dict | None = None
    machine: dict | None = None
    if unit is not None:
        analogs = moment.analog_trends(session, unit, start, end, marks)
        tags, tags_total = analogs["trends"], analogs["total"]
        timeline = moment.timeline_block(session, unit, start, end)
        # The state at the sample's own stamp - the moment the plant recorded
        # the act of measuring - with what it had just come out of, which is
        # the half that finds a changeover.
        machine = moment.state_at(session, unit, sample.ts)

    gauge = _gauge_block(session, spec, sample.gauge_id, subject="this sample")
    # Whether the readings name an instrument the sample does not. A sample is
    # one act of measurement by one person with one gauge, so they should not -
    # and a plant where they do is a finding rather than something to average
    # over silently.
    others = sorted({r["gauge"] for r in behind["readings"]
                     if r["gauge"] and r["gauge"] != (
                         gauge["gauge"]["code"] if gauge.get("gauge") else None)})
    if others:
        gauge["readings_note"] = (
            f"the readings in this sample name {', '.join(others)} as well, which a "
            f"sample recorded as one act of measurement should not")
    else:
        gauge["readings_note"] = None

    return {
        "material": material,
        "characteristic": characteristic,
        "unit": spec.unit,
        # Which question this envelope answers. A screen drawing both panels
        # from one layout must not have to guess from which keys are present.
        "subject": "sample",
        "window": {
            "start": start, "end": end,
            "before_minutes": before_minutes, "after_minutes": after_minutes,
            # The sample's own stamp, and the stretch its n readings cover -
            # which is the thing that is not an instant here.
            "at": sample.ts,
            "first_reading": first, "last_reading": last,
            "spanned_seconds": round((last - first).total_seconds(), 1),
        },
        "sample": {
            "sample": sample.id,
            "ts": sample.ts,
            "n": len(readings),
            "sample_size": spec.sample_size or 1,
            "mean": behind["mean"],
            "range": behind["range"],
            "lower_spec": spec.min_value,
            "upper_spec": spec.max_value,
            "checked_by": sample.checked_by,
            "work_order": order,
            "shift": sample.shift_code,
            "shift_day": sample.shift_day,
            "equipment": None if unit is None else {"code": unit.code, "name": unit.name},
            # The plan asked for n and the sample holds this many. Equal on
            # every sample the chart draws; said out loud because a sample that
            # is not equal is left off the chart and the reader is owed why.
            "note": (None if len(readings) == (spec.sample_size or 1) else
                     f"this sample holds {len(readings)} readings where the "
                     f"specification asks for {spec.sample_size or 1}, so the chart "
                     f"leaves it out rather than judge it with the wrong constants"),
        },
        "readings": behind,
        "chart": chart,
        "recorded": _recorded_for_sample(session, readings),
        "gauge": gauge,
        "neighbours": _neighbours(session, spec, at=sample.ts,
                                  gauge_id=sample.gauge_id, hours=neighbour_hours),
        "machine": machine,
        "timeline": timeline,
        "stops": None if timeline is None else moment.stops(timeline),
        # The same block, over the window the sample's n readings span. A
        # changeover that ended between the second bottle and the third is the
        # answer to a sampled point, and it is the filler's changeover rather
        # than the bench's.
        "line": _line_block(session, unit, start, end, at=sample.ts,
                            material_id=spec.material_id, noun="sample"),
        "tags": {
            "trends": tags,
            "shown": len(tags),
            "total": tags_total,
            "note": (None if unit is None else (
                None if tags_total <= len(tags) else
                f"{len(tags)} of the {tags_total} signals this station published in "
                f"this window are drawn")),
        },
        "maintenance": moment.maintenance_orders(session, unit, start, end),
        "findings": moment.findings(session, order, start, end),
        "coverage": None if timeline is None else timeline.get("coverage"),
        "coverage_note": (
            None if timeline is not None else
            "no station is recorded against this sample, so how much of this "
            "window anybody watched is not a question this panel can answer"),
    }


def _count(session: Session, spec: QualitySpec, count_id: int) -> QualityCount:
    """The inspection count, refused unless it is a count of *this*
    characteristic.

    The same refusal `_check` and `_sample` make, for the same reason: a URL
    naming one characteristic and a row belonging to another would answer
    about the wrong lot while looking right.
    """
    row = session.get(QualityCount, count_id)
    if row is None or row.spec_id != spec.id:
        raise NotFound(f"no inspection count {count_id} on "
                       f"{spec.material.code}/{spec.characteristic}")
    return row


def _defects_block(count: QualityCount) -> dict:
    """Which ones were sent back, in the plant's own codes, worst first.

    This is the whole reason a p chart is worth opening: the dot says *more
    of this lot came back than usual*, and the only useful next question is
    *what was wrong with them*. Every list states its total, and the two
    totals that can differ from `rejected` are both named rather than
    reconciled: a bottle can carry two defects, and an inspector can code
    some rejects and not all of them.

    Null `defects` is *not broken down* and an empty list is *nothing
    recorded against the rejects*. They are different facts and the sentence
    says which, because drawing an empty list for the first would be this
    screen telling a reader the lot was clean.
    """
    if count.defects is None:
        return {"defects": [], "total": 0, "coded": None, "rejected": count.rejected,
                "note": ("this count was recorded without a breakdown, so which "
                         "defects were found is not something this MES knows "
                         "about this lot"),
                "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE}
    rows = sorted(
        ({"code": str(d.get("code")), "count": int(d.get("count") or 0)}
         for d in count.defects if isinstance(d, dict)),
        key=lambda d: (-d["count"], d["code"]))
    coded = sum(d["count"] for d in rows)
    if not rows:
        note = (f"nothing was coded against the {count.rejected} unit(s) sent "
                f"back from this lot")
    elif coded > count.rejected:
        note = (f"{coded} defects on {count.rejected} unit(s) sent back - a "
                f"unit can carry more than one")
    elif coded < count.rejected:
        note = (f"{coded} of the {count.rejected} unit(s) sent back are coded; "
                f"the rest were sent back without a defect recorded")
    else:
        note = None
    return {"defects": rows, "total": len(rows), "coded": coded,
            "rejected": count.rejected, "note": note,
            "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE}


def _count_on_the_chart(session: Session, material: str, characteristic: str,
                        count: QualityCount) -> dict:
    """The p chart this lot is a point on, and what fired on it.

    Read from `spc.chart`, like the other two blocks of its kind: control
    limits are computed in one place, and a second opinion here would be
    slightly different numbers under the chart the reader just clicked.

    The limits named are **this lot's own**, because on a p chart that is what
    the point was judged against - a share of a lot of twenty is judged more
    loosely than the same share of a lot of two thousand, and quoting one
    pair of lines for the whole chart would tell the reader they were held to
    a standard they were not.
    """
    drawn = spc.chart(session, material, characteristic)
    keys = [p.get("count") for p in drawn.get("points", [])]
    index = keys.index(count.id) if count.id in keys else None
    point = drawn["points"][index] if index is not None else None
    signals = ([s for s in drawn.get("signals", []) if s.get("index") == index]
               if index is not None else [])
    control = drawn.get("control") or {}
    return {
        "on_chart": index is not None,
        "kind": drawn.get("kind"),
        # One-based, the way the signals table on the page counts points.
        "point": None if index is None else index + 1,
        "points": drawn.get("n"),
        # The chart's totals, so the reader can put this lot beside the rest.
        "inspected": drawn.get("inspected"),
        "rejected": drawn.get("rejected"),
        "p": None if point is None else point.get("p"),
        "centre": control.get("centre"),
        # This point's own limits. Nulls when the chart has none to draw yet.
        "upper": None if point is None else point.get("upper"),
        "lower": None if point is None else point.get("lower"),
        "sigma": None if point is None else point.get("sigma"),
        "lower_at_zero": None if point is None else point.get("lower_at_zero"),
        "stable": drawn.get("stable"),
        "verdict": drawn.get("verdict") or drawn.get("note"),
        "rules": drawn.get("rules"),
        "rules_not_run": drawn.get("rules_not_run"),
        "rules_not_run_note": drawn.get("rules_not_run_note"),
        "hold_rules": drawn.get("hold_rules"),
        "signals": signals,
        # No capability, and the chart's own words for why, so the panel does
        # not have to invent a sentence that would drift from the chart's.
        "capability": None,
        "capability_note": drawn.get("capability_note"),
        "note": None if index is not None else (
            f"this lot is not one of the {drawn.get('n')} points on the chart: "
            f"either it is older than the history the chart draws, or nobody "
            f"was inspected in it"),
    }


def _recorded_for_count(session: Session, count: QualityCount) -> dict:
    """Every rule firing the MES wrote down against this lot, with its hold."""
    rows = list(session.scalars(
        select(SpcSignal).where(SpcSignal.count_id == count.id)
        .order_by(SpcSignal.rule)))
    out = []
    for row in rows:
        nc = session.get(NonConformance, row.nonconformance_id) if row.nonconformance_id else None
        out.append({
            "rule": row.rule, "what": row.what, "ts": row.ts,
            "count": row.count_id, "value": row.value,
            "window_key": row.window_key, "window": row.window,
            "nonconformance": None if nc is None else nc.code,
            "nonconformance_status": None if nc is None else nc.status.value,
        })
    return {"signals": out, "total": len(out),
            "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE}


def lot_dossier(session: Session, material: str, characteristic: str,
                count_id: int, *,
                before_minutes: float = BEFORE_MINUTES,
                after_minutes: float = AFTER_MINUTES) -> dict:
    """Everything this MES holds about why one LOT is where it is.

    The same panel as `dossier` and `sample_dossier`, asked of a point that
    is a share rather than a reading or an average. What is different:

    - **The defects.** A dot above its limit says more of this lot came back
      than usual; the only useful next question is which ones, and the
      breakdown recorded with the count is the answer. It is the first block
      here for that reason.
    - **The window is the stretch, not an instant.** A count covers minutes
      of production, so the telemetry runs from before the start of the
      stretch to after its end. A window centred on when the count was
      written down would put the half hour it is about outside the picture.
    - **No gauge, and no capability.** Nobody gauged this; somebody looked.
      Both absences are said in words rather than left as empty fields, which
      is the one thing a reader must not have to infer.

    Nothing here is computed twice: the limits and the firings are
    `spc.chart`'s, the trends are `analysis.tag_trend`'s, the timeline is
    `state_timeline`'s.
    """
    spec = _spec(session, material, characteristic)
    count = _count(session, spec, count_id)

    unit = None if not count.equipment_id else session.get(Equipment, count.equipment_id)
    order = (None if not count.work_order_id else
             moment.order_code_of(session, count.work_order_id))

    # The stretch itself when the count recorded one, and the moment it was
    # written down when it did not. A count with no window is still a count,
    # and guessing a window for it would put trends either side of a time
    # nobody claimed anything about.
    first = count.started_at or count.ts
    last = count.ended_at or count.ts
    if last < first:
        first, last = last, first
    start = first - timedelta(minutes=before_minutes)
    end = last + timedelta(minutes=after_minutes)

    marks = [{"t": first, "label": "stretch starts"}]
    if last != first:
        marks.append({"t": last, "label": "stretch ends"})

    tags: list[dict] = []
    tags_total = 0
    timeline: dict | None = None
    machine: dict | None = None
    if unit is not None:
        analogs = moment.analog_trends(session, unit, start, end, marks)
        tags, tags_total = analogs["trends"], analogs["total"]
        timeline = moment.timeline_block(session, unit, start, end)
        machine = moment.state_at(session, unit, last)

    return {
        "material": material,
        "characteristic": characteristic,
        # A share has no unit of measure. Empty, like the chart's.
        "unit": "",
        # Which question this envelope answers, so a screen drawing three
        # panels from one layout never has to guess from which keys are here.
        "subject": "lot",
        "window": {
            "start": start, "end": end,
            "before_minutes": before_minutes, "after_minutes": after_minutes,
            "at": count.ts,
            "stretch_start": count.started_at, "stretch_end": count.ended_at,
            "spanned_seconds": (None if count.started_at is None or count.ended_at is None
                                else round((last - first).total_seconds(), 1)),
            "note": (None if count.started_at and count.ended_at else
                     "this count was recorded without the stretch it covers, so "
                     "the window below is the minutes either side of when it was "
                     "written down rather than the production it is about"),
        },
        "lot": {
            "count": count.id,
            "lot_ref": count.lot_ref,
            "lot_id": count.lot_id,
            # What a lot is on a plant that does not ship them yet, in words,
            # rather than an empty link where a lot number would be.
            "lot_note": (None if count.lot_id else spc.A_LOT_IS_A_STRETCH),
            "ts": count.ts,
            # The record, in the plant's words rather than in statistics.
            "inspected": count.inspected,
            "rejected": count.rejected,
            "p": (round(count.rejected / count.inspected, 6)
                  if count.inspected else None),
            "checked_by": count.checked_by,
            "work_order": order,
            "shift": count.shift_code,
            "shift_day": count.shift_day,
            "equipment": None if unit is None else {"code": unit.code, "name": unit.name},
            "gauge_note": ("nobody gauged this lot - it was looked at and "
                           "counted, so there is no instrument to ask about"),
        },
        # First, because it is what the reader came for.
        "defects": _defects_block(count),
        "chart": _count_on_the_chart(session, material, characteristic, count),
        "recorded": _recorded_for_count(session, count),
        "machine": machine,
        "timeline": timeline,
        "stops": None if timeline is None else moment.stops(timeline),
        "line": _line_block(session, unit, start, end, at=last,
                            material_id=spec.material_id, noun="lot"),
        "tags": {
            "trends": tags,
            "shown": len(tags),
            "total": tags_total,
            "note": (None if unit is None else (
                None if tags_total <= len(tags) else
                f"{len(tags)} of the {tags_total} signals this station published in "
                f"this window are drawn")),
        },
        "maintenance": moment.maintenance_orders(session, unit, start, end),
        "findings": moment.findings(session, order, start, end),
        "coverage": None if timeline is None else timeline.get("coverage"),
        "coverage_note": (
            None if timeline is not None else
            "no station is recorded against this count, so how much of this "
            "window anybody watched is not a question this panel can answer"),
    }
