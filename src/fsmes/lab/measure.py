"""The MES's answer, put beside what the line actually did.

Every function here is pure: a truth, what the MES said, and the two facts that
decide how to read them (the replay speed and whether the harness kept up). No
HTTP, no database, no clock - so a measurement can be argued with in a test
before it is argued with in a plant.

Three rules hold throughout, and they are principle 4 in this file's terms:

* **Unknown is not zero.** A measurement whose truth the run cannot establish
  says `null` and says why in the same object. Nothing is averaged over
  unknowns.
* **The band is stated.** A replay loops, so a run always plays a little of a
  second pass while the agent drains. Units booked in that overlap are not
  over-booking, and the range they make is written into the measurement rather
  than absorbed into a tolerance nobody can see.
* **Nobody is right by default.** Where the MES and the truth disagree the
  measurement names the difference; where the two are not measuring the same
  thing (a different window, a different rated cycle) it says so instead of
  reporting the difference as a fault.
"""

from __future__ import annotations

from fsmes.lab.truth import LineTruth

#: Availability differences smaller than this share of the window cannot be
#: told apart from the window the MES happened to measure over.
WINDOW_TOLERANCE = 0.02


def _share(value: float | None, of: float | None) -> float | None:
    if value is None or not of:
        return None
    return round(value / of, 4)


def _unknown(reason: str) -> dict:
    return {"value": None, "unknown_because": reason}


def withheld(card: dict) -> str | None:
    """Why this run's headline numbers are not worth trending, if they are not.

    `fsmes.sim.runner` already decides this for the scorecard - a harness that
    fell behind its own sample, or an MES that recorded a fault only after its
    window. The lab's measurements are read off the same run, so they are
    withheld for the same reasons rather than a second set.
    """
    return card.get("verdict_withheld")


# ------------------------------------------------------------------ booking

def booking(truth: LineTruth, oee: dict, orders: dict, tag_map: dict[str, str],
            order_tags: int, speed: float, reason: str | None = None) -> dict:
    """Did the MES book what the line made?

    Per station, because a plant fixes one machine at a time, and in total,
    because the total is what an order is confirmed against. The comparison is
    a *range*, not a number: the run replayed `truth.overlap_s` line seconds of
    a second pass while the agent drained, and what the line made in those
    seconds the MES was right to book. Below the range is under-booking; above
    it is the class of fault that booked 16 units against an order for 15.
    """
    by_code = {s["code"]: s for s in oee.get("stations", [])}
    rows = []
    for name, station in truth.stations.items():
        code = tag_map.get(name)
        said = by_code.get(code) if code else None
        low, high = station.good, station.good + station.overlap_good
        booked = None if said is None else int(said.get("good_qty") or 0)
        scrapped = None if said is None else int(said.get("scrap_qty") or 0)
        rows.append({
            "station": name,
            "equipment": code,
            "truth_good": station.good,
            "truth_scrap": station.scrap,
            "overlap_good": station.overlap_good,
            "expected_range": [low, high],
            "mes_good": booked,
            "mes_scrap": scrapped,
            "difference": None if booked is None else booked - station.good,
            "verdict": _booking_verdict(booked, low, high, code, reason),
        })

    mes_total = sum(r["mes_good"] for r in rows if r["mes_good"] is not None)
    answered = [r for r in rows if r["mes_good"] is not None]
    line_low, line_high = truth.line_good, truth.line_good + (
        truth.stations[truth.last_station].overlap_good if truth.last_station else 0)

    listed = orders.get("items") if isinstance(orders, dict) else orders
    listed = listed or []
    return {
        "measurement": "booking",
        "question": "did the MES book what the line made?",
        "unknown_because": reason,
        "line_seconds": truth.duration_s,
        "overlap_line_seconds": truth.overlap_s,
        "speed": speed,
        "stations_total": len(rows),
        "stations_answered": len(answered),
        "stations": rows,
        "line": {
            "truth_good": truth.line_good,
            "expected_range": [line_low, line_high],
            "mes_good_last_station": next(
                (r["mes_good"] for r in rows if r["station"] == truth.last_station), None),
            "mes_good_all_stations": mes_total if answered else None,
        },
        "orders": _orders(truth, listed, order_tags),
    }


def _booking_verdict(booked: int | None, low: int, high: int, code: str | None,
                     reason: str | None) -> str:
    if reason:
        return f"unknown - {reason}"
    if code is None:
        return "unknown - this station is not in the tag map, so no machine in the MES is it"
    if booked is None:
        return "unknown - the MES reported no such machine"
    if booked < low:
        return f"booked {low - booked} fewer than the line made"
    if booked > high:
        return f"booked {booked - high} more than the line made, beyond the replay's overlap"
    if high > low:
        return "inside the replay's overlap band"
    return "matched"


def _orders(truth: LineTruth, listed: list[dict], order_tags: int) -> dict:
    """What each order was told it made - and why that cannot be scored here.

    The script numbers its orders; the MES numbers its own. Tying one to the
    other needs the line to publish the order it is running, and these packs'
    tag maps name no order tag - a CSV replay cannot be written to, so orders
    flow from the MES's own released operations instead. The totals are still
    comparable, and the over-run the MES reports is still worth reading; which
    scripted order a unit belonged to is not known, and says so.
    """
    mes = [{"code": o.get("code"), "quantity": o.get("quantity"),
            "good": o.get("good_qty"), "scrap": o.get("scrap_qty"),
            "over": o.get("over_qty")} for o in listed]
    why = ("this plant's tag map names no order tag, so which order a unit belongs to is "
           "the MES's own inference and the script's order numbers cannot be matched to it"
           if not order_tags else
           "matching the script's order numbers to the MES's order codes is not attempted "
           "in this version, so a per-order comparison would be a guess")
    return {
        "tied_to_truth": False,
        "why": why,
        "truth_orders_total": len(truth.good_by_order),
        "truth_good_by_order": truth.good_by_order,
        "mes_orders_total": len(mes),
        "mes_orders": mes,
        "mes_good_total": sum(o["good"] or 0 for o in mes) if mes else None,
        "over_run_reported": sum(o["over"] or 0 for o in mes) if mes else None,
        "over_run_in_truth": None,
        "over_run_unknown_because": why,
    }


# ----------------------------------------------------------------- downtime

def downtime(truth: LineTruth, card: dict, reported: dict, speed: float,
             reason: str | None = None) -> dict:
    """Were the scripted stops seen, how late, and were the planned ones kept
    out of downtime?

    The first two questions are `fsmes.sim.score`'s, read off the run's own
    scorecard rather than asked a second time here. What this adds is the size
    of it: how many seconds of downtime the MES reports against how many the
    line actually spent down, both in line seconds.
    """
    metrics = card.get("metrics", {})
    truth_down = sum(s.down_seconds for s in truth.stations.values())
    overlap_down = sum(s.overlap_seconds_by_state.get("down", 0) for s in truth.stations.values())
    truth_planned = sum(s.changeover_seconds for s in truth.stations.values())
    mes_wall = reported.get("total_seconds")
    mes_line = None if mes_wall is None else round(float(mes_wall) * speed, 1)

    faults = [{
        "equipment": f.get("equipment"),
        "window_line_s": f.get("window_sim_s"),
        "scripted_line_seconds": f.get("scripted_seconds"),
        "detected": f.get("detected"),
        "detected_line_seconds": f.get("detected_seconds"),
        "recall": f.get("recall"),
        "lag_line_seconds": f.get("lag_sim_seconds"),
        "unknown_because": f.get("unknown_because") or (
            None if f.get("observed") else "the MES never watched this window"),
    } for f in card.get("faults", [])]

    stops = [{
        "window_line_s": p.get("window_sim_s"),
        "scripted_line_seconds": p.get("scripted_seconds"),
        "observed": p.get("observed"),
        "misclassified_as_downtime": p.get("misclassified_as_downtime"),
        "offenders": p.get("offenders"),
        "unknown_because": p.get("unknown_because"),
    } for p in card.get("planned_stops", [])]

    return {
        "measurement": "downtime",
        "question": "were the scripted stops seen, and were the planned ones kept out of downtime?",
        "unknown_because": reason,
        "speed": speed,
        "breakdowns": {
            "scripted": metrics.get("faults_scripted"),
            "scored": metrics.get("faults_scored"),
            "recall": metrics.get("breakdown_recall"),
            "recorded_late": metrics.get("faults_recorded_late"),
            "events": faults,
        },
        "planned_stops": {
            "scripted": metrics.get("planned_stops_scripted"),
            "scored": metrics.get("planned_stops_scored"),
            "misclassified_as_downtime": metrics.get("planned_stop_misclassified"),
            "truth_line_seconds": truth_planned,
            "events": stops,
        },
        "total_down": {
            "truth_line_seconds": truth_down,
            "overlap_line_seconds": overlap_down,
            "expected_range": [truth_down, truth_down + overlap_down],
            "mes_line_seconds": mes_line,
            "mes_wall_seconds": mes_wall,
            "difference_line_seconds": None if mes_line is None else round(mes_line - truth_down, 1),
            "note": ("the MES records wall seconds; multiplied by the replay speed to compare "
                     "with the line's own clock"),
        },
        "labels": {
            "mes_unlabelled_share": reported.get("unlabelled_share"),
            "truth_unlabelled_share": None,
            "unknown_because": ("nothing in this run labels a stop - no downtime labels arrive "
                                "by file and no operator names one - so what share should have "
                                "been labelled is not something this run establishes"),
            "reasons": reported.get("reasons"),
        },
    }


# --------------------------------------------------------------------- OEE

def oee(truth: LineTruth, reported: dict, tag_map: dict[str, str], speed: float,
        reason: str | None = None) -> dict:
    """Availability, performance and quality per station, against the script.

    Two things make this a comparison rather than a subtraction, and both are
    stated rather than corrected for:

    * **The windows differ.** The MES measures over the window it was watching,
      which starts when the agent subscribed and ends after the run was left to
      drain; the script is exactly `duration_s` long. An availability
      difference smaller than that mismatch is not evidence.
    * **The rated cycle may differ.** Performance is units against what the
      machine could have made, and the MES prices that at its master data's
      `ideal_cycle_seconds` while the script prices it at the line's
      `rate_per_min`. Where the two disagree the two performance figures are
      not measuring the same thing, and the row says so.
    """
    window = (reported.get("window") or {})
    mes_window_line_s = None
    hours = window.get("hours")
    if hours is not None:
        mes_window_line_s = round(float(hours) * 3600 * speed, 1)
    mismatch = (None if not mes_window_line_s or not truth.duration_s
                else round(abs(mes_window_line_s - truth.duration_s) / truth.duration_s, 4))

    by_code = {s["code"]: s for s in reported.get("stations", [])}
    rows = []
    for name, station in truth.stations.items():
        code = tag_map.get(name)
        said = by_code.get(code) if code else None
        truth_availability = _share(station.running_seconds, truth.duration_s)
        truth_quality = _share(station.good, station.total)
        cycle = station.ideal_cycle_seconds
        truth_performance = (
            round(min(1.0, cycle * station.total / station.running_seconds), 4)
            if cycle and station.running_seconds else None)
        mes_cycle = None if said is None else said.get("ideal_cycle_seconds")
        like_for_like = (cycle is not None and mes_cycle is not None
                         and abs(mes_cycle - cycle) <= 0.01 * cycle)
        rows.append({
            "station": name,
            "equipment": code,
            "truth": {
                "availability": truth_availability,
                "performance": truth_performance,
                "quality": truth_quality,
                "running_line_seconds": station.running_seconds,
                "down_line_seconds": station.down_seconds,
                "good": station.good,
                "scrap": station.scrap,
                "rated_cycle_seconds": cycle,
            },
            "mes": None if said is None else {
                "availability": said.get("availability"),
                "performance": said.get("performance"),
                "quality": said.get("quality"),
                "oee": said.get("oee"),
                "runtime_seconds": said.get("runtime_seconds"),
                "downtime_seconds": said.get("downtime_seconds"),
                "good": said.get("good_qty"),
                "scrap": said.get("scrap_qty"),
                "ideal_cycle_seconds": mes_cycle,
            },
            "difference": None if said is None else {
                "availability": _difference(said.get("availability"), truth_availability),
                "performance": _difference(said.get("performance"), truth_performance),
                "quality": _difference(said.get("quality"), truth_quality),
            },
            "performance_like_for_like": None if said is None else like_for_like,
            # The MES caps performance at 1.0 - a machine can out-run its rated
            # cycle, and a bar below the axis would imply the line invented
            # units. So a reported 1.0 means "at or above rated", and the
            # distance from the script's figure is a floor on the difference
            # rather than the difference.
            "mes_performance_at_cap": None if said is None else said.get("performance") == 1.0,
            "unknown_because": _oee_reason(said, code, reason),
        })

    answered = [r for r in rows if r["mes"] is not None]
    return {
        "measurement": "oee",
        "question": "does the MES's OEE match the hour the line actually had?",
        "unknown_because": reason,
        "speed": speed,
        "window": {
            "truth_line_seconds": truth.duration_s,
            "mes_line_seconds": mes_window_line_s,
            "mismatch_share": mismatch,
            "availability_resolution": mismatch,
            "note": ("the MES measured over its own window; an availability difference smaller "
                     "than the window mismatch cannot be told apart from it"),
        },
        "stations_total": len(rows),
        "stations_answered": len(answered),
        "stations": rows,
        "line_oee_reported": reported.get("line_oee"),
        "constraint_reported": reported.get("constraint"),
    }


def _difference(said, truth_value) -> float | None:
    if said is None or truth_value is None:
        return None
    return round(float(said) - float(truth_value), 4)


def _oee_reason(said, code: str | None, reason: str | None) -> str | None:
    if reason:
        return reason
    if code is None:
        return "this station is not in the tag map, so no machine in the MES is it"
    if said is None:
        return "the MES reported no such machine"
    return None


def significant(row: dict, mismatch: float | None) -> bool:
    """Is this station's availability difference bigger than the window
    mismatch it could be explained by?"""
    difference = (row.get("difference") or {}).get("availability")
    if difference is None:
        return False
    floor = max(WINDOW_TOLERANCE, mismatch or 0.0)
    return abs(difference) > floor
