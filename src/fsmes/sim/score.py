"""Score what the MES reported against what the simulator actually did.

Two questions in this first version, both named by the lab's own README as the
things worth checking:

  1. Was a planned stop misclassified as downtime? Calling a changeover
     downtime destroys every availability figure the plant reports, silently.
  2. Was a scripted breakdown detected at all? A fault the MES never saw is a
     fault nobody was told about.

Both are reported as numbers that can trend, plus enough detail to see why.
Where the MES has not observed enough of the window to answer, the result is
`null` and the reason is stated - never a misleading zero.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fsmes.sim.truth import ScriptedEvent

# States the MES uses. `setup` is the correct home for a changeover: planned,
# and deliberately not counted against availability.
DOWN_STATES = {"down"}
PLANNED_STATES = {"setup"}


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _window(event: ScriptedEvent, t0: datetime, speed: float) -> tuple[datetime, datetime]:
    """Simulated seconds -> wall clock.

    The replay advances one row per `1/speed` seconds, so an event scripted at
    simulated second N happens at t0 + N/speed. At speed 60 the scripted hour
    is over in a minute; the arithmetic is the only thing that changes.
    """
    return (t0 + timedelta(seconds=(event.start_s or 0) / speed),
            t0 + timedelta(seconds=(event.end_s or 0) / speed))


def _intervals_for(timeline: dict, equipment: str | None) -> list[dict]:
    out = []
    for machine in timeline.get("machines", []):
        if equipment is None or machine["code"] == equipment:
            for iv in machine.get("intervals", []):
                out.append({**iv, "equipment": machine["code"]})
    return out


def _overlap_seconds(a_start: datetime, a_end: datetime,
                     b_start: datetime, b_end: datetime) -> float:
    latest, earliest = max(a_start, b_start), min(a_end, b_end)
    return max(0.0, (earliest - latest).total_seconds())


def _observed(timeline: dict, start: datetime, end: datetime,
              equipment: str | None = None) -> bool:
    """Did the MES watch this window - and this machine - at all?

    Principle 4 applies to the scorer as much as to the product: a window the
    MES never observed must score as unknown, not as a failure. So must a
    machine the timeline does not carry: the timeline is scoped (one line, a
    screenful of machines), and a breakdown on a machine it left out is not
    a breakdown the MES missed - it is one the scorer never asked about. Four
    of a factory's five faults scored "missed" that way before this checked.
    """
    w = timeline.get("window") or {}
    if not w.get("start") or not w.get("end"):
        return False
    if equipment is not None and not any(
            m.get("code") == equipment for m in timeline.get("machines", [])):
        return False
    return _parse(w["start"]) <= start and _parse(w["end"]) >= end


# A change has to survive at least this many sampling intervals before we are
# willing to call a miss a miss. Two is the Nyquist floor; below it, absence of
# evidence really is not evidence of absence.
SAMPLES_TO_RESOLVE = 2


def resolution_sim_seconds(speed: float, observe_interval_s: float) -> float:
    """The shortest line-time event the MES could possibly notice.

    The agent samples the OPC server every `observe_interval_s` of wall clock.
    Replay compresses line time by `speed`, so one sample covers
    `observe_interval_s * speed` seconds of the line. At 60x with the default
    500 ms subscription that is 30 seconds of line time - which makes a
    12-second jam invisible no matter how good the MES is.
    """
    return observe_interval_s * speed


def score_run(
    truth: dict,
    timeline: dict,
    t0: datetime,
    speed: float,
    observe_interval_s: float = 0.5,
    chain_evidence: dict | None = None,
) -> dict:
    """Compare a scripted hour against what the MES recorded.

    Events too brief to survive the sampling interval are scored `null`, not
    `false`. Calling them missed would blame the MES for a limit of the
    harness - the same false accusation this scorer has already made twice for
    other reasons, and the reason it now states its own resolution.

    `chain_evidence` is what somebody fetched from the other record books -
    tag history, the maintenance list, a control chart, the findings - for the
    chain this line wrote down. Left out, the chain section still appears and
    still names its links; every one of them is `null`, because nobody looked.
    """
    # The written-down chain, if this line has one. Scored first because it
    # reads a different set of records from everything below it and shares
    # nothing with them but the clock.
    chain_card = score_chain(truth.get("chain"), timeline, t0, speed, chain_evidence)
    floor = resolution_sim_seconds(speed, observe_interval_s) * SAMPLES_TO_RESOLVE
    planned_checks: list[dict] = []
    fault_checks: list[dict] = []
    idle_checks: list[dict] = []
    # Scripted outages of the OPC endpoint. Carried, never scored: nothing
    # happened to the line, so there is no MES answer to be right or wrong
    # about here. What the MES said about the minutes it could not see is the
    # lab's `connection` measurement, which reads these windows off the card
    # rather than the line description a second time.
    disconnects: list[dict] = []

    # The blind windows, gathered before anything is scored: a fault the MES
    # could not see is not a fault the MES missed, and the check below has to
    # know about an outage that comes later in the file than the fault it
    # covers.
    blind = [(e.start_s, e.end_s) for e in truth["events"]
             if e.is_disconnect and e.start_s is not None and e.end_s is not None]

    for event in truth["events"]:
        if event.start_s is None or event.end_s is None:
            continue  # micro_stops and the like have no single window
        start, end = _window(event, t0, speed)
        scripted = (end - start).total_seconds()

        if event.is_disconnect:
            disconnects.append({
                "event": "disconnect",
                "window_sim_s": [event.start_s, event.end_s],
                "scripted_seconds": round(scripted, 1),
                "window_wall": [start.isoformat(), end.isoformat()],
            })

        elif event.planned:
            # A changeover is line-wide: every machine must avoid calling it
            # downtime, so check them all.
            offenders = []
            for iv in _intervals_for(timeline, None):
                if iv["state"] in DOWN_STATES:
                    bad = _overlap_seconds(start, end, _parse(iv["start"]), _parse(iv["end"]))
                    if bad > 0:
                        offenders.append({"equipment": iv["equipment"],
                                          "seconds": round(bad, 1),
                                          "reason": iv.get("reason")})
            planned_checks.append({
                "event": "changeover",
                "window_sim_s": [event.start_s, event.end_s],
                "scripted_seconds": round(scripted, 1),
                "observed": _observed(timeline, start, end),
                "misclassified_as_downtime": bool(offenders),
                "offenders": offenders,
            })

        elif event.is_idle:
            # Starved or blocked: this machine, and only this one. Unlike a
            # changeover, which is the whole line stopping together, having
            # nothing to work on is a fact about one station - so the check
            # is scoped to its own equipment, and a neighbour that really was
            # down in the same minutes is not counted against it.
            offenders = []
            for iv in _intervals_for(timeline, event.equipment):
                if iv["state"] in DOWN_STATES:
                    bad = _overlap_seconds(start, end, _parse(iv["start"]), _parse(iv["end"]))
                    if bad > 0:
                        offenders.append({"equipment": iv["equipment"],
                                          "seconds": round(bad, 1),
                                          "reason": iv.get("reason")})
            idle_checks.append({
                "event": event.type,
                "equipment": event.equipment,
                "station": event.station,
                "window_sim_s": [event.start_s, event.end_s],
                "scripted_seconds": round(scripted, 1),
                "observed": _observed(timeline, start, end, event.equipment),
                "misclassified_as_downtime": bool(offenders),
                "offenders": offenders,
            })

        elif event.is_fault:
            seen = 0.0
            for iv in _intervals_for(timeline, event.equipment):
                if iv["state"] in DOWN_STATES:
                    seen += _overlap_seconds(start, end,
                                             _parse(iv["start"]), _parse(iv["end"]))
            observed = _observed(timeline, start, end, event.equipment)
            scripted_sim = (event.end_s or 0) - (event.start_s or 0)
            resolvable = scripted_sim >= floor
            # Every down interval the MES recorded for this machine, whether
            # or not it lines up. When a fault scores as missed, this is what
            # says whether the MES was blind or the clocks disagree - and the
            # first version of this scorer needed exactly that to find its own
            # alignment bug.
            recorded = [
                {"start": iv["start"], "end": iv["end"],
                 "seconds": iv["seconds"], "reason": iv.get("reason")}
                for iv in _intervals_for(timeline, event.equipment)
                if iv["state"] in DOWN_STATES
            ]
            # How late the MES's record was, in line time. This is detection
            # latency, and it is worth trending in its own right - a plant that
            # notices its faults slower than it used to has regressed even if
            # it still notices them all.
            lag = None
            if recorded:
                nearest = min(recorded, key=lambda iv: abs(
                    (_parse(iv["start"]) - start).total_seconds()))
                lag = round((_parse(nearest["start"]) - start).total_seconds() * speed, 1)

            # How much of this fault's window nobody could see. A breakdown
            # scripted inside a scripted outage is the MES being blind, not
            # the MES being wrong, and scoring it as recall 0 is the same
            # false accusation this scorer already refuses to make for a
            # window it did not sample fast enough. Decision 0030.
            unseen = sum(max(0.0, min(event.end_s, b) - max(event.start_s, a))
                         for a, b in blind)
            blinded = unseen > 0

            scoreable = observed and resolvable and not blinded
            # Recorded, but entirely after its window: the MES saw the fault
            # and saw it late, which is a pipeline lag and not a miss. Scoring
            # that as recall 0 turned "minutes behind" into "got it wrong" in
            # two of seventeen identical bottling runs (the vault's Multiplant
            # Scoring Reproducibility note) - the same false accusation this
            # scorer has already stated it will not make. It is withheld and
            # named instead.
            late = bool(scoreable and seen == 0 and recorded and lag is not None and lag > scripted_sim)
            if late:
                scoreable = False
            fault_checks.append({
                "event": "down",
                "equipment": event.equipment,
                "window_sim_s": [event.start_s, event.end_s],
                "expected_window": [start.isoformat(), end.isoformat()],
                "scripted_seconds": round(scripted, 1),
                "observed": observed,
                # Too brief to survive the sampling interval at this speed:
                # not the MES's failure, and not scored as one.
                "resolvable_at_this_speed": resolvable,
                # Line seconds of this fault's window that the MES could not
                # see at all, because the script closed the endpoint over it.
                "unseen_sim_seconds": round(unseen, 1),
                "inside_a_disconnect": blinded,
                "detected": (seen > 0) if scoreable else None,
                "detected_seconds": round(seen, 1) if scoreable else None,
                "recall": round(seen / scripted, 3) if scoreable and scripted else None,
                "lag_sim_seconds": lag,
                # The MES recorded this fault only after its window had passed:
                # not scored as missed, and the run's verdict says why.
                "recorded_late": late,
                "mes_recorded_down": recorded,
            })

    answered_faults = [c for c in fault_checks if c["detected"] is not None]
    # (faults filtered out above were unobserved or unresolvable; both are
    #  "unknown", and averaging unknowns into a recall figure would invent one)
    answered_planned = [c for c in planned_checks if c["observed"]]
    # A window the MES was not watching answers nothing, the same rule the
    # planned stops already follow - never a zero standing in for silence.
    answered_idle = [c for c in idle_checks if c["observed"]]

    # The fastest replay at which every scripted event would still be
    # resolvable - actionable guidance rather than a shrug.
    durations = [(e.end_s - e.start_s) for e in truth["events"]
                 if e.start_s is not None and e.end_s is not None]
    max_speed = (min(durations) / (SAMPLES_TO_RESOLVE * observe_interval_s)
                 if durations else None)
    unresolvable = [c["equipment"] for c in fault_checks
                    if not c["resolvable_at_this_speed"]]
    late_faults = [{"equipment": c["equipment"], "lag_sim_seconds": c["lag_sim_seconds"]}
                   for c in fault_checks if c["recorded_late"]]

    return {
        "channel": truth.get("channel"),
        "seed": truth.get("seed"),
        "duration_s": truth.get("duration_s"),
        "speed": speed,
        "observation": {
            "interval_s": observe_interval_s,
            "resolution_sim_s": round(resolution_sim_seconds(speed, observe_interval_s), 1),
            "shortest_scoreable_sim_s": round(floor, 1),
            "max_speed_for_full_resolution": round(max_speed, 1) if max_speed else None,
            "unresolvable_at_this_speed": unresolvable,
        },
        "started_at": t0.isoformat(),
        "metrics": {
            # The availability-killer. Any non-zero value is a real defect.
            "planned_stop_misclassified": (
                sum(1 for c in answered_planned if c["misclassified_as_downtime"])
                if answered_planned else None
            ),
            # Of the faults the MES was watching for, how many did it see?
            "breakdown_recall": (
                round(sum(1 for c in answered_faults if c["detected"]) / len(answered_faults), 3)
                if answered_faults else None
            ),
            "faults_scripted": len(fault_checks),
            "faults_scored": len(answered_faults),
            # Recorded after their window: seen, late, and not counted either way.
            "faults_recorded_late": len(late_faults),
            "planned_stops_scripted": len(planned_checks),
            "planned_stops_scored": len(answered_planned),
            # The same availability-killer from the other direction: a machine
            # that was starved or blocked is not a machine that broke.
            "idle_stop_misclassified": (
                sum(1 for c in answered_idle if c["misclassified_as_downtime"])
                if answered_idle else None
            ),
            "idle_stops_scripted": len(idle_checks),
            "idle_stops_scored": len(answered_idle),
            **chain_metrics(chain_card),
        },
        "planned_stops": planned_checks,
        "idle_stops": idle_checks,
        "disconnects": disconnects,
        "faults": fault_checks,
        "late_faults": late_faults,
        "chain": chain_card,
        "window": timeline.get("window"),
    }


# ------------------------------------------------------------------- chains
#
# A chain is several causes in a row, each of which lands in a DIFFERENT one
# of the plant's record books: a changeover in the order book, an overdue job
# on the maintenance list, two tags in the historian, low sample means on a
# control chart. A line description may write the chain down - `_chain` in
# `line.json`, read by `fsmes.sim.truth` - and this is what marks it.
#
# Why the key is data and this is only the marker: a chain hard-coded here
# would grade one line and would drift from that line's own events the first
# time somebody moved a window. The key names only what the events beside it
# script, so the plant and its answer key cannot disagree without the JSON
# saying so.
#
# The honesty rule is the one the stop sections already follow. Three answers,
# never two: recorded, not recorded, and *not observed* - and the third is
# `null` with the reason beside it, never a zero. A record the MES was not
# asked for, a window it did not watch, a tag it holds nothing for and a
# characteristic nobody sampled in the window are all "nobody looked", which
# is a different statement from "the record is not there".

#: What a link's record may be, and where this scorer looks for it. A kind
#: that is not in here scores `null` rather than `false`: a key that names a
#: record this scorer cannot read is this scorer's gap, not the plant's.
CHAIN_RECORDS: dict[str, str] = {
    "stop": "an interval on the state timeline, in a named MES state",
    "tag": "a tag's own history, moved `by` in `direction` against the mean "
           "over everything the MES holds before the window",
    "maintenance": "a maintenance order against a named plan, at a named status",
    "sample": "a point on a control chart, past a named bound",
    "finding": "an open non-conformance against a named characteristic",
}

#: Said instead of a figure when nothing collected the evidence. A card scored
#: without it is not a card that found nothing.
NO_EVIDENCE = ("nobody asked the MES for this record: this run collected no "
               "chain evidence")


def _link_window(spec: dict, fallback: list | None = None) -> tuple[float, float] | None:
    window = spec.get("window_s") or fallback
    if not window or len(window) != 2:
        return None
    return float(window[0]), float(window[1])


def _wall(window: tuple[float, float], t0: datetime, speed: float) -> tuple[datetime, datetime]:
    return (t0 + timedelta(seconds=window[0] / speed),
            t0 + timedelta(seconds=window[1] / speed))


def _points_in(points: list[dict], start: datetime, end: datetime,
               before: bool = False) -> list[dict]:
    out = []
    for point in points:
        stamp = point.get("t") or point.get("ts")
        if not stamp or (point.get("mean") is None and point.get("value") is None):
            continue
        when = _parse(stamp)
        if (when < start) if before else (start <= when < end):
            out.append(point)
    return out


def _mean_of(points: list[dict]) -> float | None:
    values = [float(p["mean"] if p.get("mean") is not None else p["value"]) for p in points]
    return sum(values) / len(values) if values else None


def _score_stop(record: dict, start: datetime, end: datetime,
                timeline: dict) -> dict:
    equipment, state = record.get("equipment"), record.get("state")
    if not _observed(timeline, start, end, equipment):
        return {"recorded": None,
                "why": f"the MES's timeline does not cover {equipment} over this window"}
    seconds = 0.0
    for interval in _intervals_for(timeline, equipment):
        if interval["state"] != state:
            continue
        if record.get("reason") and interval.get("reason") not in (None, record["reason"]):
            continue
        seconds += _overlap_seconds(start, end, _parse(interval["start"]),
                                    _parse(interval["end"]))
    return {"recorded": seconds > 0, "seconds": round(seconds, 1),
            "why": (f"{round(seconds, 1)} s of {state} on {equipment} inside the window"
                    if seconds > 0 else
                    f"the MES recorded no {state} interval on {equipment} in this window")}


def _score_tag(record: dict, start: datetime, end: datetime,
               evidence: dict) -> dict:
    equipment, tag = record.get("equipment"), record.get("tag")
    trend = (evidence.get("tags") or {}).get(f"{equipment}/{tag}")
    if trend is None:
        return {"recorded": None,
                "why": f"nobody asked the MES for {equipment}.{tag}"}
    points = trend.get("points") or []
    inside = _points_in(points, start, end)
    baseline = _points_in(points, start, end, before=True)
    if not inside:
        return {"recorded": None,
                "why": f"the MES holds no {tag} readings for {equipment} in this window"}
    if not baseline:
        return {"recorded": None,
                "why": f"the MES holds no {tag} readings for {equipment} BEFORE this "
                       "window, so there is nothing to measure the move against"}
    here, there = _mean_of(inside), _mean_of(baseline)
    moved = here - there
    want = float(record.get("by", 0.0))
    down = str(record.get("direction", "up")) == "down"
    recorded = (-moved >= want) if down else (moved >= want)
    return {
        "recorded": recorded,
        "moved_by": round(moved, 3),
        "asked_for": round(-want if down else want, 3),
        "baseline": round(there, 3),
        "in_window": round(here, 3),
        "readings": sum(int(p.get("n") or 1) for p in inside),
        "why": (f"{equipment}.{tag} averaged {round(here, 3)} in the window against "
                f"{round(there, 3)} before it, a move of {round(moved, 3)}"),
    }


def _score_maintenance(record: dict, start: datetime, end: datetime,
                       evidence: dict) -> dict:
    orders = (evidence.get("maintenance") or {}).get("items")
    if orders is None:
        return {"recorded": None, "why": "nobody asked the MES for its maintenance orders"}
    plan, status = record.get("plan"), record.get("status")
    hits = [o for o in orders
            if o.get("plan") == plan
            and (record.get("equipment") in (None, o.get("equipment")))
            and o.get("status") == status]
    # Whether anybody started one, which is the point of this link on a plant
    # whose floor raises its work and never goes. Reported beside the verdict
    # rather than folded into it: "raised and still due" and "raised, started
    # and finished" are both records, and only the key says which one the
    # story wanted.
    started = [o.get("code") for o in orders
               if o.get("plan") == plan and o.get("started_at")]
    if not hits and not (evidence.get("maintenance") or {}).get("complete"):
        return {"recorded": None,
                "why": "the MES's maintenance list came back in part, so an order "
                       f"against {plan} may be on a page nobody fetched"}
    return {
        "recorded": bool(hits),
        "orders": [o.get("code") for o in hits],
        "ever_started": started,
        "why": (f"{len(hits)} order(s) against {plan} at {status}"
                + (f", and {len(started)} against {plan} were started at some point"
                   if started else f", none against {plan} ever started")
                if hits else
                f"the MES holds no order against {plan} at {status}"),
    }


def _score_sample(record: dict, start: datetime, end: datetime,
                  evidence: dict) -> dict:
    material = record.get("material")
    characteristic = record.get("characteristic")
    chart = (evidence.get("charts") or {}).get(f"{material}/{characteristic}")
    if chart is None:
        return {"recorded": None,
                "why": f"nobody asked the MES for the {material} {characteristic} chart"}
    if chart.get("coverage") == "absent":
        return {"recorded": None,
                "why": chart.get("coverage_note")
                       or f"the MES holds no {characteristic} readings for {material}"}
    inside = _points_in(chart.get("points") or [], start, end)
    if not inside:
        return {"recorded": None,
                "why": f"nobody measured {material} {characteristic} inside this window, "
                       "so there is no point on the chart to be right or wrong about"}
    values = [float(p["value"]) for p in inside]
    if record.get("below") is not None:
        bound = float(record["below"])
        past = [v for v in values if v < bound]
        where = f"below {bound}"
    else:
        bound = float(record.get("above", 0.0))
        past = [v for v in values if v > bound]
        where = f"above {bound}"
    return {
        "recorded": bool(past),
        "points_in_window": len(values),
        "points_past_the_bound": len(past),
        "lowest": round(min(values), 3),
        "highest": round(max(values), 3),
        # Which chart this is, in the chart's own word: a sampled chart plots
        # sample MEANS, and a reader told only "a point" will believe it is a
        # bottle somebody measured.
        "chart": chart.get("kind"),
        "sample_size": chart.get("sample_size"),
        "why": (f"{len(past)} of {len(values)} {chart.get('kind') or 'chart'} points in "
                f"the window are {where}"),
    }


def _score_finding(record: dict, start: datetime, end: datetime,
                   evidence: dict) -> dict:
    """A finding opened on this characteristic inside the window.

    Matched on the finding's own `evidence` - the material and the
    characteristic the MES wrote down when it raised it - and not on the
    words of its description. A plant with two specs on the same material
    would otherwise credit one characteristic's chain to the other's finding.
    A finding a person raised carries no evidence and is not matched: they
    wrote the description, and reading a characteristic out of their prose
    would be a guess.
    """
    findings = (evidence.get("findings") or {}).get("items")
    if findings is None:
        return {"recorded": None, "why": "nobody asked the MES for its non-conformances"}
    material = record.get("material")
    characteristic = record.get("characteristic")
    hits, rules = [], []
    for row in findings:
        seen = row.get("evidence") or {}
        if characteristic and seen.get("characteristic") != characteristic:
            continue
        if material and seen.get("material") != material:
            continue
        stamp = row.get("created_at")
        if not stamp:
            continue
        if start <= _parse(stamp) < end:
            hits.append(row.get("code"))
            if seen.get("rule") is not None:
                rules.append(seen["rule"])
    if not hits and not (evidence.get("findings") or {}).get("complete"):
        return {"recorded": None,
                "why": "the MES's findings came back in part, so one on "
                       f"{characteristic} may be on a page nobody fetched"}
    return {
        "recorded": bool(hits),
        "findings": hits,
        # Which rule actually fired, in the order they fired. The key says
        # which one it expects to come first; this is what happened, and the
        # two being different is worth seeing rather than hiding.
        "rules_fired": rules or None,
        "why": (f"{len(hits)} finding(s) on {material} {characteristic} opened inside "
                f"the window" + (f", on SPC rule(s) {rules}" if rules else "")
                if hits else
                f"the MES opened no finding on {material} {characteristic} inside "
                "this window"),
    }


_RECORD_SCORERS = {
    "stop": lambda r, a, b, ev, tl: _score_stop(r, a, b, tl),
    "tag": lambda r, a, b, ev, tl: _score_tag(r, a, b, ev),
    "maintenance": lambda r, a, b, ev, tl: _score_maintenance(r, a, b, ev),
    "sample": lambda r, a, b, ev, tl: _score_sample(r, a, b, ev),
    "finding": lambda r, a, b, ev, tl: _score_finding(r, a, b, ev),
}


def score_chain(chain: dict | None, timeline: dict, t0: datetime, speed: float,
                evidence: dict | None) -> dict:
    """Mark a line's written-down chain against what the MES recorded.

    One card per link, and one row inside it per record the link names. A
    link is recorded when every record it names was found; not recorded when
    any one of them is definitely absent; and `null` - not observed - when
    the only thing standing between it and an answer is that nobody looked.
    """
    if not chain:
        return {"key": "absent", "links": [], "name": None, "finding": None,
                "why": "this line description has no `_chain` block, so there is "
                       "no chain to mark"}
    cards = []
    for spec in chain.get("links") or []:
        link_window = spec.get("window_s")
        rows = []
        for record in spec.get("records") or []:
            kind = record.get("kind")
            window = _link_window(record, link_window)
            if kind not in CHAIN_RECORDS:
                rows.append({"kind": kind, "recorded": None,
                             "why": f"this scorer cannot read a {kind!r} record; it "
                                    f"reads {', '.join(sorted(CHAIN_RECORDS))}"})
                continue
            if window is None:
                rows.append({"kind": kind, "recorded": None,
                             "why": "neither this record nor its link says which "
                                    "window it happens in"})
                continue
            if evidence is None:
                rows.append({"kind": kind, "window_sim_s": list(window),
                             "recorded": None, "why": NO_EVIDENCE})
                continue
            start, end = _wall(window, t0, speed)
            scored = _RECORD_SCORERS[kind](record, start, end, evidence, timeline)
            rows.append({"kind": kind, "window_sim_s": list(window),
                         "window_wall": [start.isoformat(), end.isoformat()],
                         **scored})
        verdicts = [row["recorded"] for row in rows]
        if not verdicts:
            recorded = None
        elif False in verdicts:
            recorded = False
        elif None in verdicts:
            recorded = None
        else:
            recorded = True
        cards.append({
            "link": spec.get("link"),
            "what": spec.get("what"),
            "window_sim_s": link_window,
            "shows_as": spec.get("shows_as"),
            "recorded": recorded,
            "records": rows,
        })
    return {
        "key": "present",
        "name": chain.get("name"),
        "finding": chain.get("finding"),
        "links": cards,
    }


def chain_metrics(chain_card: dict) -> dict:
    """Numbers off a chain card that can trend, with the same honesty.

    `null` wherever nothing was scored: a chain nobody collected evidence for
    has no coverage of nought, it has no coverage at all.
    """
    links = chain_card.get("links") or []
    answered = [card for card in links if card["recorded"] is not None]
    records = [row for card in links for row in card["records"]]
    answered_records = [row for row in records if row["recorded"] is not None]
    return {
        "chain_links": len(links),
        "chain_links_recorded": sum(1 for c in answered if c["recorded"]) if answered else None,
        "chain_links_not_recorded": sum(1 for c in answered if not c["recorded"]) if answered else None,
        # Links nobody could answer for - the coverage figure, and the reason
        # a chain that scores 2 of 2 is not the same as one that scores 4 of 4.
        "chain_links_not_observed": sum(1 for c in links if c["recorded"] is None),
        "chain_links_scored": len(answered),
        "chain_recorded": (round(sum(1 for c in answered if c["recorded"]) / len(answered), 3)
                           if answered else None),
        "chain_records": len(records),
        "chain_records_recorded": (sum(1 for r in answered_records if r["recorded"])
                                   if answered_records else None),
        "chain_records_not_observed": sum(1 for r in records if r["recorded"] is None),
    }
