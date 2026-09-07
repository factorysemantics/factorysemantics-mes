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
) -> dict:
    """Compare a scripted hour against what the MES recorded.

    Events too brief to survive the sampling interval are scored `null`, not
    `false`. Calling them missed would blame the MES for a limit of the
    harness - the same false accusation this scorer has already made twice for
    other reasons, and the reason it now states its own resolution.
    """
    floor = resolution_sim_seconds(speed, observe_interval_s) * SAMPLES_TO_RESOLVE
    planned_checks: list[dict] = []
    fault_checks: list[dict] = []

    for event in truth["events"]:
        if event.start_s is None or event.end_s is None:
            continue  # micro_stops and the like have no single window
        start, end = _window(event, t0, speed)
        scripted = (end - start).total_seconds()

        if event.planned:
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

            scoreable = observed and resolvable
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
        },
        "planned_stops": planned_checks,
        "faults": fault_checks,
        "late_faults": late_faults,
        "window": timeline.get("window"),
    }
