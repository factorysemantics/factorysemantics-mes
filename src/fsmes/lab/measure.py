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

from fsmes.lab.truth import IDLE_STATES, LineTruth

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
            speed: float, reason: str | None = None, line_map=None,
            unassigned: dict | None = None) -> dict:
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
        "orders": _orders(truth, listed, line_map, unassigned, reason),
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


def _orders(truth: LineTruth, listed: list[dict], line_map=None,
            unassigned: dict | None = None, reason: str | None = None) -> dict:
    """What each order made, and how far past it the line ran.

    Two numbering schemes meet here. The script numbers its orders and the line
    publishes the number it is running; the MES holds its own codes and, with
    nothing telling it otherwise, infers which order a unit belongs to from
    what it has released. For five runs this measurement could only say that
    the two could not be matched.

    What matches them is the tag map's `line` block: the tag the line publishes
    its order on, and the rule that turns what it published into the code this
    MES holds. That is a fact about the plant's wiring, so it is config, and it
    is read rather than asserted - which is the difference between a join a
    reader can check and one they have to take on trust.

    The **ordered quantity** comes from the MES's own work order, because there
    is only one of it: an order is for what the plant was told it was for, and
    a second copy of that number in the line description would be a second
    place for it to be wrong. What is compared is production - the line's own
    count of what it made under that order - and the over-run the MES reports
    against it.

    Per-order truth is a **range**, like every other booking figure here: the
    replay loops and a run drains through a little of a second pass, and units
    made in that overlap are production the MES was right to book.
    """
    mes = [{"code": o.get("code"), "quantity": o.get("quantity"),
            "good": o.get("good_qty"), "scrap": o.get("scrap_qty"),
            "over": o.get("over_qty")} for o in listed]
    by_code = {str(o["code"]): o for o in mes if o.get("code")}

    rows = [_order_row(truth, order_id, by_code, line_map, reason)
            for order_id in sorted(truth.good_by_order)]
    tied = [r for r in rows if r["unknown_because"] is None]
    named = {str(r["code"]) for r in rows if r["code"]}

    why = reason or (None if tied else _untied(line_map))
    over_run_truth = sum(r["truth_over_run"] for r in tied
                         if r["truth_over_run"] is not None) if tied else None
    return {
        "tied_to_truth": bool(tied),
        "tied_how": (f"the tag map says the line publishes its order on "
                     f"{line_map.object}.{line_map.publishes_order} and that "
                     f"{line_map.order_code!r} is how that value names an order here"
                     if tied and line_map else None),
        "why": why,
        "truth_orders_total": len(truth.good_by_order),
        "truth_good_by_order": truth.good_by_order,
        "orders_tied": len(tied),
        "mes_orders_total": len(mes),
        "mes_orders": mes,
        # An order the MES holds that the line never published while this run
        # was watching. Not a fault - a plant has orders that are not running -
        # but it is why the two totals can differ, and a reader who is not told
        # will work it out as a discrepancy.
        "mes_orders_the_line_never_published": sorted(
            str(o["code"]) for o in mes if str(o.get("code")) not in named),
        "mes_good_total": sum(o["good"] or 0 for o in mes) if mes else None,
        "over_run_reported": sum(o["over"] or 0 for o in mes) if mes else None,
        "over_run_in_truth": over_run_truth,
        "over_run_unknown_because": why,
        "unassigned_production": _unassigned(unassigned),
        "rows": rows,
    }


def _unassigned(said: dict | None) -> dict:
    """What the MES counted with no order open to book it against.

    Printed beside the orders because the alternative is a report that says
    the MES booked two thousand fewer units than the line made and cannot say
    whether they were dropped or kept. Those are different faults with
    different fixes, and the MES already answers the question - it just was
    not being asked.
    """
    if not said:
        return {"good": None, "scrap": None, "entries": None,
                "unknown_because": "the run did not ask the MES what it counted with no order "
                                   "open, so where production the orders did not take went is "
                                   "not something this run establishes"}
    return {"good": said.get("good_total"), "scrap": said.get("scrap_total"),
            "entries": said.get("total"), "unknown_because": None,
            "note": "counted by a machine with no order open to book it against; the MES keeps "
                    "these rather than dropping them, with the machine and the time and no "
                    "guess about which order they belonged to. Every machine on the line, not "
                    "the last one - so it is not the counterpart of any single order's gap, "
                    "and a line of six stations counts most units six times"}


def _untied(line_map) -> str:
    """Why nothing could be matched, naming what would match it."""
    if line_map is None:
        return ("this plant's tag map has no `line` block, so nothing says where the line "
                "publishes the order it is running and which order a unit belongs to stays "
                "the MES's own inference")
    if not line_map.publishes_order:
        return (f"this plant's tag map names the line object {line_map.object!r} and no tag on "
                f"it carrying the order the line is running, so the script's order numbers "
                f"cannot be matched to the MES's codes")
    return (f"the tag map says the line publishes its order on "
            f"{line_map.object}.{line_map.publishes_order}, and none of the orders the line "
            f"published in this run is one the MES holds")


def _order_row(truth: LineTruth, order_id: str, by_code: dict, line_map,
               reason: str | None = None) -> dict:
    """One order the line published, beside the MES's order of the same name."""
    code = line_map.code_for(order_id) if line_map and line_map.publishes_order else None
    said = by_code.get(str(code)) if code else None
    made = truth.good_by_order.get(order_id, 0)
    overlap = truth.overlap_good_by_order.get(order_id, 0)
    quantity = None if said is None else said.get("quantity")
    booked = None if said is None else said.get("good")
    reported_over = None if said is None else said.get("over")

    unknown = None
    if reason:
        # A run whose own harness fell behind is an instrument out of
        # calibration. The stations already say so; an order row that went on
        # printing a confident verdict underneath them would be the same
        # withheld run answering two different ways on one page.
        unknown = reason
    elif code is None:
        unknown = _untied(line_map)
    elif said is None:
        unknown = (f"the line published order {order_id}, which this tag map reads as {code}, "
                   f"and the MES holds no order with that code - so what it made was booked "
                   f"against whatever the MES inferred instead")
    elif booked is None:
        unknown = f"the MES listed {code} without a good count"

    over_truth = None if quantity is None else max(0, made - float(quantity))
    over_range = (None if quantity is None
                  else [max(0, made - float(quantity)), max(0, made + overlap - float(quantity))])
    return {
        "order": order_id,
        "code": code,
        "truth_good": made,
        "overlap_good": overlap,
        "expected_range": [made, made + overlap],
        "mes_quantity": quantity,
        "mes_good": booked,
        "verdict": _booking_verdict(booked, made, made + overlap, code, unknown),
        "truth_over_run": over_truth,
        "truth_over_run_range": over_range,
        "mes_over_run": reported_over,
        "over_run_verdict": _over_run_verdict(reported_over, over_range, unknown),
        "unknown_because": unknown,
    }


def _over_run_verdict(reported, over_range, unknown: str | None) -> str:
    """What the MES said it made past the order, against what the line made."""
    if unknown:
        return f"unknown - {unknown}"
    if over_range is None:
        return ("unknown - the MES holds no quantity for this order, so there is nothing for "
                "production to be past")
    if reported is None:
        return "unknown - the MES reported no over-run for this order"
    low, high = over_range
    reported = float(reported)
    if reported < low:
        return f"reported {low - reported:,.0f} fewer past the order than the line made"
    if reported > high:
        return (f"reported {reported - high:,.0f} more past the order than the line made, "
                f"beyond the replay's overlap")
    if high > low:
        return "inside the replay's overlap band"
    return "matched" if reported else "the line did not run past the order, and the MES agrees"


# ----------------------------------------------------------------- downtime

def lag_says(lag: float | None, resolution: float | None) -> str:
    """How late the MES was, or that the question is finer than the sampling.

    A bare signed number here has already misled a reader once: a sweep
    reported a breakdown detected one second *before* it was scripted, at a
    speed whose sampling interval was thirty line seconds. That is
    quantisation, not prescience. So a lag smaller than the resolution is
    printed as what it is, with the resolution beside it, and never as a
    number somebody could put in a trend.
    """
    if lag is None:
        return "unknown"
    if resolution is None:
        return f"{float(lag):+.1f} s (resolution unknown)"
    if abs(float(lag)) <= float(resolution):
        return f"within resolution ({float(resolution):.0f} s)"
    return f"{float(lag):+.1f} s"


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

    def line_seconds(wall) -> float | None:
        """The scorer counts in wall seconds; the script is written in line
        seconds. At 20x a 180-second stop is nine seconds of anybody's watch,
        and printing that nine under a heading that says *line seconds* is the
        kind of quiet mislabel this lab exists to catch."""
        return None if wall is None else round(float(wall) * speed, 1)

    # The shortest line-time event this run could have noticed at all. A lag
    # smaller than it is quantisation, not measurement - the steward's buffer
    # sweep printed a breakdown "detected one second before it was scripted",
    # which is not prescience, it is a 30-second sampling interval.
    resolution = (card.get("observation") or {}).get("resolution_sim_s")

    faults = [{
        "equipment": f.get("equipment"),
        "window_line_s": f.get("window_sim_s"),
        "scripted_line_seconds": line_seconds(f.get("scripted_seconds")),
        "scripted_wall_seconds": f.get("scripted_seconds"),
        "detected": f.get("detected"),
        "detected_line_seconds": line_seconds(f.get("detected_seconds")),
        "detected_wall_seconds": f.get("detected_seconds"),
        "recall": f.get("recall"),
        "lag_line_seconds": f.get("lag_sim_seconds"),
        "resolution_line_seconds": resolution,
        "lag_says": lag_says(f.get("lag_sim_seconds"), resolution),
        "unknown_because": f.get("unknown_because") or (
            None if f.get("observed") else "the MES never watched this window"),
    } for f in card.get("faults", [])]

    stops = [{
        "window_line_s": p.get("window_sim_s"),
        "scripted_line_seconds": line_seconds(p.get("scripted_seconds")),
        "scripted_wall_seconds": p.get("scripted_seconds"),
        "observed": p.get("observed"),
        "misclassified_as_downtime": p.get("misclassified_as_downtime"),
        "offenders": p.get("offenders"),
        "unknown_because": p.get("unknown_because"),
    } for p in card.get("planned_stops", [])]

    idle = [{
        "event": c.get("event"),
        "station": c.get("station"),
        "equipment": c.get("equipment"),
        "window_line_s": c.get("window_sim_s"),
        "scripted_line_seconds": line_seconds(c.get("scripted_seconds")),
        "scripted_wall_seconds": c.get("scripted_seconds"),
        "observed": c.get("observed"),
        "misclassified_as_downtime": c.get("misclassified_as_downtime"),
        "offenders": c.get("offenders"),
    } for c in card.get("idle_stops", [])]
    truth_idle = {state: sum(s.seconds_by_state.get(state, 0) for s in truth.stations.values())
                  for state in IDLE_STATES}

    return {
        "measurement": "downtime",
        "question": "were the scripted stops seen, and were the planned ones kept out of downtime?",
        "unknown_because": reason,
        "speed": speed,
        "resolution_line_seconds": resolution,
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
        "idle_stops": {
            "question": "a machine with nothing to work on, or nowhere to put what it made, "
                        "is not a machine that broke",
            "scripted": metrics.get("idle_stops_scripted"),
            "scored": metrics.get("idle_stops_scored"),
            "misclassified_as_downtime": metrics.get("idle_stop_misclassified"),
            # Every second the line spent starved or blocked, scripted or not:
            # a scripted starve at one station makes the next one starve on
            # its own, and that knock-on is most of the total.
            "truth_line_seconds": truth_idle,
            "events": idle,
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

    Three things make this a comparison rather than a subtraction, and all
    three are stated rather than quietly corrected for:

    * **The windows differ.** The MES measures over the window it was watching,
      which starts when the agent subscribed and ends after the run was left to
      drain; the script is exactly `duration_s` long. An availability
      difference smaller than that mismatch is not evidence.
    * **The clocks differ, and only performance notices.** Availability and
      quality are each a ratio of two things measured the same way, so the
      replay speed cancels out of both. Performance does not cancel: its
      numerator is priced in the line's own seconds - the rated cycle a person
      wrote down - and its denominator is run time the MES measured on the wall
      clock. Replay an hour at 20x and the MES's run time is a twentieth of the
      line's, so the figure it reports is twenty times the line's. Until
      2026-09-14 that was invisible, because the MES capped performance at 1.0
      and every station in every run read exactly 1.0. So the row restates the
      MES's performance on the line's clock - the MES's own rating, its own
      counts, its own run time multiplied by the replay speed - and compares
      that. At speed 1 it is the reported figure unchanged.
    * **The rated cycle may differ.** Performance is units against what the
      machine could have made, and the MES prices that at its master data's
      `ideal_cycle_seconds` while the script prices it at the line's
      `rate_per_min`. Where the two disagree the two performance figures are
      not measuring the same thing, and the row says so.

    Neither side is capped. A performance above 1.0 means the machine beat the
    cycle it was rated at, which is a finding about the rating.
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
            round(cycle * station.total / station.running_seconds, 4)
            if cycle and station.running_seconds else None)
        mes_cycle = None if said is None else said.get("ideal_cycle_seconds")
        like_for_like = (cycle is not None and mes_cycle is not None
                         and abs(mes_cycle - cycle) <= 0.01 * cycle)

        # The MES's own numbers, restated on the line's clock. Nothing of the
        # MES's is replaced: this is its rating, its counts and its run time,
        # with the run time multiplied by the speed the replay played at.
        mes_runtime_line_s = (
            None if said is None or said.get("runtime_seconds") is None
            else round(float(said["runtime_seconds"]) * speed, 1))
        mes_units = (
            None if said is None
            else (said.get("good_qty") or 0) + (said.get("scrap_qty") or 0))
        mes_performance_line = (
            round(mes_cycle * mes_units / mes_runtime_line_s, 4)
            if mes_cycle and mes_units and mes_runtime_line_s else None)
        mes_downtime_line_s = (
            None if said is None or said.get("downtime_seconds") is None
            else round(float(said["downtime_seconds"]) * speed, 1))
        # The MES's own OEE carries its wall-clock performance, so it is out
        # by the replay speed the same way. Restated from the MES's own three
        # numbers with performance on the line's clock - nothing of the truth's
        # is in it.
        mes_oee_line = (
            round(float(said["availability"]) * mes_performance_line * float(said["quality"]), 4)
            if said is not None and mes_performance_line is not None
            and said.get("availability") is not None and said.get("quality") is not None
            else None)
        # Performance divides units by run time, so the two sides can only be
        # told apart down to how differently they measured that run time. The
        # MES drains past the end of the script, which is the same reason the
        # window mismatch exists, priced per station instead of per line.
        performance_resolution = (
            round(abs(mes_runtime_line_s - station.running_seconds) / station.running_seconds, 4)
            if mes_runtime_line_s is not None and station.running_seconds else None)
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
            # Every field here says which clock it is on, and the two that do
            # not are the two where the clock cancels. A stored `performance`
            # of 19.77 beside a difference of -0.05 is one object answering
            # two ways, and whoever reads it next has no way to tell which
            # number the difference came from. So there is no plain
            # `performance`, `oee`, `runtime_seconds` or `downtime_seconds` in
            # here at all: a reader has to choose a clock, which is the point.
            "mes": None if said is None else {
                # Ratios of two things measured the same way: the replay speed
                # cancels out, and there is one of each.
                "availability": said.get("availability"),
                "quality": said.get("quality"),
                "good": said.get("good_qty"),
                "scrap": said.get("scrap_qty"),
                "ideal_cycle_seconds": mes_cycle,
                # As the MES reported them - on the wall clock, which at this
                # replay speed is not the line's.
                "performance_as_reported": said.get("performance"),
                "oee_as_reported": said.get("oee"),
                "runtime_wall_seconds": said.get("runtime_seconds"),
                "downtime_wall_seconds": said.get("downtime_seconds"),
                # The MES's own rating, its own counts and its own run time,
                # with the run time on the line's clock. This is what the
                # difference below was computed from.
                "runtime_line_seconds": mes_runtime_line_s,
                "downtime_line_seconds": mes_downtime_line_s,
                "performance_line_clock": mes_performance_line,
                "oee_line_clock": mes_oee_line,
                "performance_note": said.get("performance_note"),
            },
            "difference": None if said is None else {
                "availability": _difference(said.get("availability"), truth_availability),
                # Performance on the line's clock, both sides. Comparing the
                # reported figure here would be comparing an hour with three
                # minutes of it.
                "performance": _difference(mes_performance_line, truth_performance),
                "quality": _difference(said.get("quality"), truth_quality),
            },
            "performance_like_for_like": None if said is None else like_for_like,
            "performance_resolution": performance_resolution,
            # The MES said this machine beat the cycle its master data rates it
            # at. On a replay that is usually the clocks rather than the plant,
            # which is what `performance_line_clock` is for.
            "mes_performance_above_rated": (
                None if said is None or said.get("performance") is None
                else said["performance"] > 1.0),
            # Stronger, and about the MES alone: on the line's own clock, and
            # at a rating both sides agree on, the MES counted more units than
            # its own recorded run time can hold - while the line, priced the
            # same way, fitted its units inside its running seconds. So the
            # rating is not the explanation and the script is not the other
            # party: two of the MES's own numbers do not agree with each other.
            "mes_units_outrun_its_own_runtime": (
                None if said is None or mes_performance_line is None
                or truth_performance is None or not like_for_like
                else mes_performance_line > 1.0 >= truth_performance),
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


def performance_significant(row: dict) -> bool:
    """Same question for performance, against its own band.

    Performance has a per-station band rather than the line's window mismatch:
    the two sides divide by run time, and they measured run time over different
    stretches. A difference inside that cannot be told apart from it. A station
    the two sides rate differently is not compared at all - the figures are not
    measuring the same thing, and the row already says so.
    """
    difference = (row.get("difference") or {}).get("performance")
    if difference is None or row.get("performance_like_for_like") is False:
        return False
    floor = max(WINDOW_TOLERANCE, row.get("performance_resolution") or 0.0)
    return abs(difference) > floor

# ------------------------------------------------- what disagreed, as data

def differences(plant: dict) -> list[dict]:
    """Every place this plant's numbers and the truth disagreed, biggest first.

    Data rather than prose, because two readers want it: the report, which
    draws it as a table with a bar, and `fsmes lab review`, which puts the
    same rows from several runs beside each other. Computing it twice is how
    a page and a roll-up come to disagree about the same run.

    A difference inside a band the run itself cannot vouch for is not in here
    at all - the bands are stated in the sections above it, and repeating
    quantisation as a finding is how a list of findings stops being read.
    """
    found: list[dict] = []
    booking_out = plant.get("measurements", {}).get("booking")
    if booking_out:
        for row in booking_out["stations"]:
            low, high = row["expected_range"]
            booked = row["mes_good"]
            if booked is None:
                continue
            outside = booked - high if booked > high else (booked - low if booked < low else 0)
            if outside:
                found.append({
                    "size": abs(outside), "measurement": "booking",
                    "where": f"{plant['plant']} · {row['station']}",
                    "plant": plant["plant"], "station": row["station"],
                    "what": "units booked",
                    "says": f"{outside:+d} outside the range {low} to {high}",
                    "numbers": {"truth_good": row["truth_good"], "mes_good": booked,
                                "expected_range": [low, high]},
                })
        for row in booking_out["orders"].get("rows") or []:
            if row.get("unknown_because"):
                continue
            reported, band = row["mes_over_run"], row["truth_over_run_range"]
            if reported is None or band is None:
                continue
            low, high = band
            outside = (reported - high if reported > high
                       else (reported - low if reported < low else 0))
            if not outside:
                continue
            found.append({
                "size": abs(outside), "measurement": "booking - orders",
                "where": f"{plant['plant']} - {row['code']}",
                "plant": plant["plant"], "station": None,
                "what": "units past the order",
                "says": f"{outside:+,.0f} outside the range {low:,.0f} to {high:,.0f} the line "
                        f"actually made past an order for {row['mes_quantity']:,.0f}",
                "numbers": {"truth_good": row["truth_good"],
                            "mes_good": row["mes_good"],
                            "mes_quantity": row["mes_quantity"],
                            "truth_over_run_range": band,
                            "mes_over_run": reported},
            })
    oee_out = plant.get("measurements", {}).get("oee")
    if oee_out:
        mismatch = oee_out["window"]["mismatch_share"]
        for row in oee_out["stations"]:
            diff = row["difference"] or {}
            for key in ("availability", "performance", "quality"):
                value = diff.get(key)
                if value is None:
                    continue
                # Each of the three is judged against the band the run can
                # actually resolve it to: availability against the line's
                # window mismatch, performance against the station's own run
                # times, quality against nothing but the floor.
                if key == "availability":
                    floor = max(WINDOW_TOLERANCE, mismatch or 0.0)
                elif key == "performance":
                    floor = max(WINDOW_TOLERANCE, row.get("performance_resolution") or 0.0)
                else:
                    floor = WINDOW_TOLERANCE
                if abs(value) <= floor:
                    continue
                says = f"{value * 100:+.1f} points"
                if key == "performance" and row.get("performance_like_for_like") is False:
                    says += " — but the two sides rate this machine differently"
                elif key == "performance":
                    says += " — both sides on the line's clock"
                found.append({
                    "size": abs(value) * 1000, "measurement": "oee",
                    "where": f"{plant['plant']} · {row['station']}",
                    "plant": plant["plant"], "station": row["station"],
                    "what": key, "says": says,
                    # For performance this is the figure that was compared —
                    # the MES's own, restated on the line's clock — not the
                    # wall-clock figure it reported. Quoting the other one
                    # beside the difference would not add up.
                    "numbers": {
                        "truth": (row["truth"] or {}).get(key),
                        "mes": (row["mes"] or {}).get(
                            "performance_line_clock" if key == "performance" else key),
                        "difference": value,
                    },
                })
        for row in oee_out["stations"]:
            if not row.get("mes_units_outrun_its_own_runtime"):
                continue
            said, truth_side = row["mes"], row["truth"]
            units = (said.get("good") or 0) + (said.get("scrap") or 0)
            cycle = float(said.get("ideal_cycle_seconds") or 0)
            found.append({
                # Above the ordinary OEE differences. This one is not a
                # disagreement with the script: it is two of the MES's own
                # numbers disagreeing with each other, and no argument about
                # the truth makes it go away.
                "size": 1e8, "measurement": "oee",
                "where": f"{plant['plant']} · {row['station']}",
                "plant": plant["plant"], "station": row["station"],
                "what": "more units than its own run time holds",
                "says": (f"{units:,.0f} units at the {cycle} s per unit the MES itself rates "
                         f"this machine at is {units * cycle:,.0f} s of work, recorded inside "
                         f"{said.get('runtime_line_seconds'):,.0f} s of run time on the line's "
                         f"clock — the script, priced the same way, fitted its units inside "
                         f"its running seconds"),
                "numbers": {
                    "mes_units": units,
                    "mes_ideal_cycle_seconds": cycle,
                    "mes_runtime_line_seconds": said.get("runtime_line_seconds"),
                    "mes_performance_line_clock": said.get("performance_line_clock"),
                    "truth_performance": truth_side.get("performance"),
                    "truth_running_line_seconds": truth_side.get("running_line_seconds"),
                },
            })
    down = plant.get("measurements", {}).get("downtime")
    idle = (down or {}).get("idle_stops") or {}
    if idle.get("misclassified_as_downtime"):
        for event in idle.get("events") or []:
            if not event.get("misclassified_as_downtime"):
                continue
            seconds = sum(o.get("seconds") or 0 for o in event.get("offenders") or [])
            found.append({
                "size": 1e9, "measurement": "downtime", "where": plant["plant"],
                "plant": plant["plant"], "station": event.get("station"),
                "what": f"{event.get('event')} counted as downtime",
                "says": f"{seconds:.0f} s of downtime recorded for "
                        f"{event.get('equipment') or 'this machine'} while the script had it "
                        f"{'starved' if event.get('event') == 'starve' else 'blocked'}",
                "numbers": {"scripted_line_seconds": event.get("scripted_line_seconds"),
                            "downtime_seconds_recorded": round(seconds, 1)},
            })
    if down and down["planned_stops"]["misclassified_as_downtime"]:
        found.append({
            "size": 1e9, "measurement": "downtime", "where": plant["plant"],
            "plant": plant["plant"], "station": None,
            "what": "planned stop counted as downtime",
            "says": f"{down['planned_stops']['misclassified_as_downtime']} of "
                    f"{down['planned_stops']['scored']} scored",
            "numbers": {"misclassified": down["planned_stops"]["misclassified_as_downtime"],
                        "scored": down["planned_stops"]["scored"]},
        })
    found.sort(key=lambda row: row["size"], reverse=True)
    return found


def unknowns(plant: dict) -> list[dict]:
    """Every question this run could not answer, and why it could not.

    An unknown is the most valuable row in a results directory and the
    easiest to lose: it is the one an average would swallow. Collected as
    data so a roll-up can count the same unknown recurring across runs.
    """
    out: list[dict] = []
    if plant.get("verdict_withheld"):
        out.append({"measurement": "every measurement", "plant": plant["plant"],
                    "station": None, "because": plant["verdict_withheld"]})
    booking_out = plant.get("measurements", {}).get("booking")
    if booking_out:
        for row in booking_out["stations"]:
            if str(row["verdict"]).startswith("unknown"):
                out.append({"measurement": "booking", "plant": plant["plant"],
                            "station": row["station"], "because": row["verdict"]})
        orders = booking_out["orders"]
        if orders.get("why"):
            out.append({"measurement": "booking · orders", "plant": plant["plant"],
                        "station": None, "because": orders["why"]})
        for row in orders.get("rows") or []:
            # The whole-measurement reason above already says it once; a row
            # that is unknown for its own reason is a different fact.
            if row.get("unknown_because") and row["unknown_because"] != orders.get("why"):
                out.append({"measurement": "booking · orders", "plant": plant["plant"],
                            "station": None, "because": row["unknown_because"]})
    down = plant.get("measurements", {}).get("downtime")
    if down:
        for event in down["breakdowns"]["events"]:
            if event.get("unknown_because"):
                out.append({"measurement": "downtime", "plant": plant["plant"],
                            "station": event.get("equipment"),
                            "because": event["unknown_because"]})
        for event in (down.get("idle_stops") or {}).get("events") or []:
            if not event.get("observed"):
                out.append({"measurement": "downtime · idle", "plant": plant["plant"],
                            "station": event.get("station"),
                            "because": "the MES never watched this window, so whether it "
                                       "called a starved machine down is unknown"})
        if down["labels"].get("unknown_because"):
            out.append({"measurement": "downtime · labels", "plant": plant["plant"],
                        "station": None, "because": down["labels"]["unknown_because"]})
    oee_out = plant.get("measurements", {}).get("oee")
    if oee_out:
        for row in oee_out["stations"]:
            if row.get("unknown_because"):
                out.append({"measurement": "oee", "plant": plant["plant"],
                            "station": row["station"], "because": row["unknown_because"]})
    for key, why in (plant.get("views_refused") or {}).items():
        out.append({"measurement": f"the {key} view", "plant": plant["plant"],
                    "station": None, "because": f"the view did not answer: {why}"})
    return out
