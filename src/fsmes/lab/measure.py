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

from fsmes.lab import observe as observe_mod
from fsmes.lab.truth import IDLE_STATES, LineTruth

#: Availability differences smaller than this share of the window cannot be
#: told apart from the window the MES happened to measure over.
WINDOW_TOLERANCE = 0.02

#: How many of the agent's own health checks an outage has to span before the
#: agent could be expected to see it at all. Two, for the same reason the
#: scorer wants two samples across a stop: one check can fall either side.
SAMPLES_TO_SEE = 2


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


# ----------------------------------------------------------------- latency

#: What state the MES has to be showing for a machine before a scripted event
#: counts as having reached a screen. The mapping is the tag map's own - a
#: starved machine is idle, a changeover is setup - restated here because a
#: latency measurement that waited for the wrong word would report every event
#: as never seen.
STATE_FOR_EVENT = {"down": "down", "starve": "idle", "block": "idle", "changeover": "setup"}

#: The screens a look asks that can answer *when did the MES notice*. Kept as
#: a pair of (key in the look, the route it came from) so a surface that is
#: added later cannot be added to the polling and forgotten in the reading.
STATE_SURFACES = (("states", "/equipment/states"), ("line_states", "/line/events"))


def _scripted(card: dict) -> list[dict]:
    """Every scripted event with a window and a machine, off the scorecard.

    Read from the card rather than from the line description a second time:
    the card has already turned the script into windows with the MES's own
    equipment codes on them, and a second reading is a second place for the
    two to drift apart.
    """
    out = []
    for kind, rows in (("down", card.get("faults") or []),
                       ("idle", card.get("idle_stops") or []),
                       ("changeover", card.get("planned_stops") or [])):
        for row in rows:
            window = row.get("window_sim_s") or [None, None]
            out.append({
                "event": row.get("event") or ("down" if kind == "down" else kind),
                "equipment": row.get("equipment"),
                "station": row.get("station"),
                "window_line_s": window,
                # A changeover is the whole line stopping together and names no
                # machine; every machine has to show it, so the first one to is
                # what the question is about.
                "line_wide": kind == "changeover",
            })
    return out


def _first_showing(looks: list[dict], key: str, equipment: str | None,
                   want: str, after: float, until: float) -> float | None:
    """The line second of the first look in which the MES showed `want`.

    Bounded at both ends on purpose. `after` is when the line did it: a machine
    that was already down before the script stopped it is a different fact, and
    counting it would report a negative lag as though the MES had been early.
    `until` is the end of the window plus nothing: a screen that caught up after
    the event was over did not show the event, it showed history.
    """
    for look in looks:
        second = float(look.get("line_second") or 0)
        if second < after or second > until:
            continue
        showing = look.get(key) or {}
        if equipment is None:
            if want in showing.values():
                return second
        elif str(showing.get(equipment) or "") == want:
            return second
    return None


def latency(card: dict, watched: dict, truth: LineTruth, tag_map: dict[str, str],
            speed: float, reason: str | None = None) -> dict:
    """How long after the line did each screen say it?

    The other measurements ask the plant one question at the end. This one is
    built out of what the run saw while the hour played, because the interval
    between the line doing something and a screen showing it has closed by the
    time the run is over: a stop that took four minutes to appear and one that
    appeared at once leave the same trace in a scorecard.

    Two rules hold and are stated in the result rather than assumed:

    * **The resolution is the polling interval.** A screen is only ever known
      to have shown something *by* the look that saw it, so a lag smaller than
      one interval of line time is quantisation and is printed as *within
      resolution*, never as a number somebody could trend. Same rule as a
      detection lag, same reason (#59, and the sweep that reported a breakdown
      noticed one second before it was scripted).
    * **Not seen is not the same as late.** An event the watch never caught -
      because nobody was looking yet, because the route stopped answering,
      because the run ended - is *unknown* with which of those it was. A zero
      or a maximum standing in for silence is exactly the number this whole lab
      exists to stop being reported.
    """
    looks = watched.get("looks") or []
    seconds = [float(look.get("line_second") or 0) for look in looks]
    first, last = (min(seconds), max(seconds)) if seconds else (None, None)
    resolution = (watched.get("resolution_line_seconds")
                  or (round(watched.get("every_wall_seconds", 0) * speed, 1)
                      if watched.get("every_wall_seconds") else None))
    failures = watched.get("failures") or {}

    events = []
    for event in _scripted(card):
        start, end = event["window_line_s"]
        want = STATE_FOR_EVENT.get(event["event"])
        row = {**event, "expected_state": want, "surfaces": {}}
        for key, route in STATE_SURFACES:
            row["surfaces"][route] = _one_surface(
                looks, key, route, event, want, start, end, first, last,
                resolution, failures, reason)
        events.append(row)

    return {
        "measurement": "latency",
        "question": "how long after the line did each screen say it?",
        "unknown_because": reason,
        "speed": speed,
        "watched": {
            "looks": watched.get("looks_total", len(looks)),
            "every_wall_seconds": watched.get("every_wall_seconds"),
            "resolution_line_seconds": resolution,
            "first_look_line_second": first,
            "last_look_line_second": last,
            "failures": failures,
            "note": ("a screen is only ever known to have shown something by the look that saw "
                     "it, so the polling interval is the resolution of every figure here"),
        },
        "surfaces": [
            {"route": route, "what": what,
             "answers_when_the_mes_noticed": route not in observe_mod.CARRIES_NO_LINE_STATE,
             "unknown_because": observe_mod.CARRIES_NO_LINE_STATE.get(route),
             "looks_it_refused": failures.get(route, 0)}
            for route, what in observe_mod.SURFACES.items()
        ],
        "events_total": len(events),
        "events_answered": sum(1 for e in events
                               if any(s.get("lag_line_seconds") is not None
                                      for s in e["surfaces"].values())),
        "events": events,
        "production": _production_lag(looks, truth, tag_map, watched, resolution, reason),
        "namespace": {
            "lag_line_seconds": None,
            "unknown_because": ("no broker was configured for this run, so how long an event "
                                "took to reach the unified namespace is not something this run "
                                "establishes"),
        },
    }


def _one_surface(looks, key, route, event, want, start, end, first, last,
                 resolution, failures, reason) -> dict:
    """One screen's answer about one scripted event."""
    out = {"saw_at_line_second": None, "lag_line_seconds": None,
           "lag_says": "unknown", "unknown_because": None}
    if reason:
        out["unknown_because"] = reason
        return out
    if want is None:
        out["unknown_because"] = (f"a {event['event']!r} has no state a screen would show for "
                                  f"it, so there is nothing to wait to appear")
        return out
    if start is None or end is None:
        out["unknown_because"] = "this event has no window, so there is nothing to be late to"
        return out
    if first is None:
        out["unknown_because"] = "nothing watched this run while it played"
        return out
    if start < first or end > last:
        out["unknown_because"] = (f"the watch ran from line second {first:.0f} to {last:.0f} and "
                                  f"this event was {start:.0f} to {end:.0f} - part of it "
                                  f"happened while nobody was looking")
        return out

    seen = _first_showing(looks, key, None if event["line_wide"] else event["equipment"],
                          want, start, end)
    if seen is None:
        refused = failures.get(route, 0)
        out["unknown_because"] = (
            f"no look between line second {start:.0f} and {end:.0f} showed {want!r}"
            + (f", and this screen did not answer {refused} time(s) during the run"
               if refused else "")
            + " - the screen never showed it, or showed it after the event was over, and "
              "this run does not tell those two apart")
        return out
    lag = round(seen - start, 1)
    out.update(saw_at_line_second=seen, lag_line_seconds=lag,
               lag_says=lag_says(lag, resolution))
    return out


def _production_lag(looks: list[dict], truth: LineTruth, tag_map: dict[str, str],
                    watched: dict, resolution, reason: str | None) -> dict:
    """How far behind the line's own output the line view ran, look by look.

    **The last station and only the last station.** The feed reports every
    machine on the line, and a serial line counts most units once per station -
    adding them up and comparing the total against what came off the end would
    be six times the answer at six stations. So this is the machine the line
    ends at, against the line's own good count, which is the same pair the
    booking measurement compares at the end of the run.

    **Counted in units and stated in units.** Turning a backlog into seconds
    needs a rate, and the rate is exactly what a line that is starved, blocked
    or down has not got - so the seconds would be invented at precisely the
    moments worth measuring.

    Everything is *since the watch began*: the feed hands a new watcher the
    live tail rather than the hour so far, which is its own contract and the
    reason each look is cheap.
    """
    if reason:
        return {"unknown_because": reason}
    if not truth.last_station:
        return {"unknown_because": "this line has no last station, so there is no line output "
                                   "to be behind"}
    code = tag_map.get(truth.last_station)
    if not code:
        return {"unknown_because": f"the line ends at {truth.last_station}, which is not in the "
                                   f"tag map, so no machine in the MES is it"}
    counted = [look for look in looks if isinstance(look.get("booked_good"), dict)]
    if not counted:
        return {"unknown_because": "the line view's feed answered no look, so how far behind "
                                   "the MES's own screen was is not something this run "
                                   "establishes"}
    if not watched.get("the_feed_answered_with_a_cursor", True):
        return {"unknown_because": "the line view's feed never handed back a cursor, so every "
                                   "look read as a watcher that had just arrived and no look "
                                   "could carry a count"}

    first_second = float(counted[0].get("line_second") or 0)
    rows = []
    for look in counted:
        second = float(look.get("line_second") or 0)
        made = truth.good_between(first_second, second)
        if made is None:
            continue
        booked = float((look.get("booked_good") or {}).get(code, 0.0))
        rows.append({"line_second": round(second, 1),
                     "truth_good_since_watch_began": made,
                     "mes_booked_since_watch_began": booked,
                     "behind_units": round(made - booked, 1)})
    if not rows:
        return {"unknown_because": "the replay wrote no line table, so what the line had made "
                                   "at each look is not something this run establishes"}
    behind = [row["behind_units"] for row in rows]
    return {
        "question": "how far behind the line's own count was the line view, while it played?",
        "unknown_because": None,
        "machine": code,
        "station": truth.last_station,
        "counted_from_line_second": round(first_second, 1),
        "looks": len(rows),
        "looks_catching_up": sum(1 for look in counted if look.get("truncated")),
        "resolution_line_seconds": resolution,
        "worst_behind_units": max(behind),
        "median_behind_units": sorted(behind)[len(behind) // 2],
        "ahead_at_worst_units": min(behind),
        "note": ("the machine the line ends at, against the line's own good count - a serial "
                 "line counts most units once per station, so summing every machine's would be "
                 "six times the answer. Units, not seconds: turning a backlog into seconds needs "
                 "a rate, and a line that is starved, blocked or down has not got one. A "
                 "negative figure is the screen ahead of the line, which on a looping replay "
                 "means the second pass has begun"),
        "samples": rows,
    }


# ----------------------------------------------------------------- console

def console(phases: list[dict], plants_total: int, reason: str | None = None) -> dict:
    """Did `fsmes fleet console` count the plants the run actually had?

    One question, asked at every phase of the run: **of the plants this console
    had been told about, how many were answering, and did that match how many
    were really up?** The lab runs its plants one after another, so the answer
    changes as the run goes, and the phase where it is worth having is the one
    where a plant this run built is no longer there.

    The rule the console lives by is decision 0023's: a plant that did not
    answer is *unknown*, never healthy and never down. So this checks the
    counts and it separately checks the words - a console that got the totals
    right and called a stopped plant *down* would have invented a breakdown out
    of a plant nobody could reach, which is the same fault as calling a
    changeover downtime and the same size.
    """
    rows = []
    for phase in phases:
        totals = phase.get("totals") or {}
        listed = list(phase.get("listed") or [])
        running = list(phase.get("running") or [])
        answered, unknown = totals.get("answered"), totals.get("unknown")
        said_of = phase.get("plants") or {}

        # Every plant the console had been told about that was not up. Each
        # must read unknown: it is the console's whole promise.
        misread = sorted(
            name for name in listed if name not in running
            and str((said_of.get(name) or {}).get("state") or "") not in ("unknown", ""))
        rows.append({
            "phase": phase.get("phase"),
            "at": phase.get("at"),
            "plants_in_the_plan": plants_total,
            "plants_the_console_knew_of": len(listed),
            "plants_really_running": len(running),
            "running": running,
            "console_says": phase.get("says"),
            "console_answered": answered,
            "console_unknown": unknown,
            "console_plants": totals.get("plants"),
            "answered_matches": _matches(answered, len(running), reason),
            "counted_every_plant_it_knew_of": _matches(
                None if totals.get("plants") is None else int(totals["plants"]),
                len(listed), reason),
            "stopped_plants_read_unknown": (None if reason or not listed
                                            else not misread),
            "stopped_plants_read_otherwise": misread,
            "what_it_called_them": {name: (said_of.get(name) or {}).get("state")
                                    for name in listed},
            "unknown_because": reason or phase.get("unknown_because"),
        })

    answerable = [row for row in rows if row["unknown_because"] is None]
    return {
        "measurement": "console",
        "question": "did the fleet console count the plants the run actually had?",
        "unknown_because": reason,
        "plants_total": plants_total,
        "phases_total": len(rows),
        "phases_answered": len(answerable),
        "phases_where_the_count_matched": sum(1 for row in answerable
                                              if row["answered_matches"] is True),
        # The number that is a defect at any value above zero. A stopped plant
        # the console called anything other than unknown is a plant somebody
        # would act on.
        "stopped_plants_not_read_as_unknown": sum(
            len(row["stopped_plants_read_otherwise"]) for row in answerable),
        "never_said_down": (None if not answerable else not any(
            "down" in str(row["what_it_called_them"].values()).lower() for row in answerable)),
        "note": ("the lab runs its plants one after another, so at any moment during a "
                 "several-plant experiment exactly one is answering and the rest are not - "
                 "which is the console's hardest case arriving for free, on real ports, over "
                 "real HTTP"),
        "phases": rows,
    }


def _matches(said, was: int, reason: str | None) -> bool | None:
    """Did the console's count match what was true? None when unknowable."""
    if reason or said is None:
        return None
    return int(said) == int(was)


def console_findings(seen: dict) -> tuple[list[dict], list[dict]]:
    """What the console measurement contributes to the differences and the
    unknowns, as the same rows every other measurement produces.

    Separate from `differences` and `unknowns` because those take one plant
    and this one is about the run. Same shape, so the report and the roll-up
    read it with the code they already have.
    """
    differences, unknowns = [], []
    if seen.get("unknown_because"):
        unknowns.append({"measurement": "console", "plant": None, "station": None,
                         "because": seen["unknown_because"]})
        return differences, unknowns
    for phase in seen.get("phases") or []:
        if phase.get("unknown_because"):
            unknowns.append({"measurement": "console", "plant": None, "station": None,
                             "because": f"{phase['phase']}: {phase['unknown_because']}"})
            continue
        for name in phase.get("stopped_plants_read_otherwise") or []:
            called = (phase.get("what_it_called_them") or {}).get(name)
            differences.append({
                # The console's whole promise, so it ranks with the other
                # things that are defects at any value above zero.
                "size": 1e9, "measurement": "console",
                "where": f"the console · {name}", "plant": name, "station": None,
                "what": "a plant nobody could reach, not read as unknown",
                "says": f"{phase['phase']}: the console called {name} {called!r} when it was "
                        f"not running - a plant that did not answer is unknown, never healthy "
                        f"and never down",
                "numbers": {"phase": phase["phase"], "state": called,
                            "running": phase.get("running")},
            })
        if phase.get("answered_matches") is False:
            differences.append({
                "size": abs((phase.get("console_answered") or 0)
                            - phase.get("plants_really_running", 0)) * 1000,
                "measurement": "console",
                "where": "the console", "plant": None, "station": None,
                "what": "the count of answering plants",
                "says": f"{phase['phase']}: the console counted "
                        f"{phase.get('console_answered')} answering and "
                        f"{phase.get('plants_really_running')} really were",
                "numbers": {"phase": phase["phase"],
                            "console_answered": phase.get("console_answered"),
                            "really_running": phase.get("plants_really_running")},
            })
    return differences, unknowns


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
                # As the MES reported them. Since 2026-09-18 the MES computes
                # performance on the line's clock itself, from the same
                # `MES_SIM_SPEED` this run was started with, and reports no
                # figure at all when the counted work will not fit inside the
                # run time - so this is `None` exactly when
                # `counts_outrun_run_time` is true, and the ratio behind it is
                # `performance_ratio`. The restatement below is kept because
                # it is computed from the MES's own three numbers and can be
                # checked against them.
                "performance_as_reported": said.get("performance"),
                "performance_ratio_as_reported": said.get("performance_ratio"),
                "counts_outrun_run_time": said.get("counts_outrun_run_time"),
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
            # The MES's own arithmetic says this machine beat the cycle its
            # master data rates it at. Read from `performance_ratio` rather
            # than from the reported figure, because above 1.0 there is no
            # reported figure any more - that is the whole of decision 0026 as
            # amended. Before 2026-09-18 this was usually the clocks rather
            # than the plant; the MES now settles the clocks itself.
            "mes_performance_above_rated": (
                None if said is None or said.get("performance_ratio") is None
                else said["performance_ratio"] > 1.0),
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


# ------------------------------------------------------------------ connection

def connection(card: dict, watched: dict, oee: dict, tag_map: dict[str, str],
               speed: float, reason: str | None = None) -> dict:
    """What did the MES say about the minutes it could not see?

    The only measurement here that *has* to be read from during the run. By the
    time the hour is over the plant has reconnected and every screen says so;
    the question is what they said while the link was down, and a plant asked
    afterwards cannot answer it.

    Three things are asked of each scripted outage, and each has an honest
    unknown:

    * **Did the machines read as disconnected?** Not down, not idle, not the
      last state they were in. A look that did not happen inside the window -
      because the watch had not started, because the route did not answer -
      is *unknown*, never a miss.
    * **Did anything claim to know what the machines were doing?** A machine
      still showing `running` or `down` in the middle of an outage is the
      whole fault this exists to catch, and it is reported by name.
    * **Did the window come back as unknown time rather than as run time?**
      Read off the OEE answer at the end, which is where a plant's numbers
      actually come from: `unknown_seconds` has to cover roughly the scripted
      outage, restated on the line's clock.
    """
    windows = card.get("disconnects") or []
    looks = watched.get("looks") or []
    resolution = (watched.get("resolution_line_seconds")
                  or (round(watched.get("every_wall_seconds", 0) * speed, 1)
                      if watched.get("every_wall_seconds") else None))
    seconds = [float(look.get("line_second") or 0) for look in looks]
    first, last = (min(seconds), max(seconds)) if seconds else (None, None)

    # The agent's own health check, on the line's clock. An outage shorter than
    # a couple of these cannot be seen at all, and a measurement that called
    # that a miss would be blaming the MES for the speed the harness chose. The
    # same rule the scorer applies to a stop shorter than its sample, for the
    # same reason.
    health = (card.get("observation") or {}).get("agent_health_interval_s")
    floor = round(health * speed * SAMPLES_TO_SEE, 1) if health else None

    events = []
    for window in windows:
        start, end = window["window_sim_s"]
        inside = [look for look in looks
                  if start <= float(look.get("line_second") or 0) <= end]
        events.append(_one_outage(window, inside, start, end, first, last, floor, reason))

    # What the numbers said afterwards. The MES's seconds are wall seconds and
    # the script's are line seconds, so the MES's are restated on the line's
    # clock before the two are put beside each other - the same conversion
    # every other measurement here makes, and for the same reason.
    scripted_line_seconds = sum(float(w["window_sim_s"][1]) - float(w["window_sim_s"][0])
                                for w in windows if w.get("window_sim_s"))
    stations = oee.get("stations") or []
    unknown_by_station = [
        {"station": name, "equipment": tag_map.get(name),
         "unknown_line_seconds": _unknown_line_seconds(stations, tag_map.get(name), speed),
         "availability": _availability_of(stations, tag_map.get(name))}
        for name in sorted(tag_map)
    ]
    counted = [row["unknown_line_seconds"] for row in unknown_by_station
               if row["unknown_line_seconds"] is not None]

    return {
        "measurement": "connection",
        "question": "what did the MES say about the minutes it could not see?",
        "unknown_because": reason,
        "speed": speed,
        "scripted_outages": len(windows),
        "scripted_line_seconds": round(scripted_line_seconds, 1),
        "watched": {
            "looks": watched.get("looks_total", len(looks)),
            "resolution_line_seconds": resolution,
            "agent_health_interval_line_seconds": round(health * speed, 1) if health else None,
            "shortest_outage_the_agent_could_see": floor,
            "first_look_line_second": first,
            "last_look_line_second": last,
            "note": ("a machine is only ever known to have read disconnected by the look that "
                     "saw it, so the polling interval is the resolution of every figure here"),
        },
        "outages": events,
        "after_the_run": {
            "note": ("the MES counts wall seconds and the script is in line seconds; these are "
                     "the MES's own figures restated on the line's clock"),
            "stations": unknown_by_station,
            # Machine-seconds, because the endpoint carries every machine: one
            # outage of N seconds on a line of six is six machines' worth.
            "unknown_line_seconds_total": round(sum(counted), 1) if counted else None,
            "unknown_line_seconds_expected": (
                round(scripted_line_seconds * len(unknown_by_station), 1) if windows else 0.0),
            **_overhang(sum(counted) if counted else None,
                        scripted_line_seconds * len(unknown_by_station) if windows else 0.0),
        },
    }


def _one_outage(window: dict, inside: list[dict], start: float, end: float,
                first: float | None, last: float | None, floor: float | None,
                reason: str | None) -> dict:
    """One scripted outage, and what the screens said inside it."""
    scripted_line_seconds = float(end) - float(start)
    row = {**window, "scripted_line_seconds": round(scripted_line_seconds, 1),
           "resolvable_at_this_speed": None if floor is None else scripted_line_seconds >= floor}
    if reason:
        return {**row, "verdict": "unknown", "unknown_because": reason}
    if floor is not None and scripted_line_seconds < floor:
        # Shorter than the agent could notice at this replay speed. Not a
        # miss - the same rule, and the same refusal to accuse, that the
        # scorer applies to a stop shorter than its own sample.
        return {**row, "verdict": "unknown", "looks_inside": len(inside),
                "unknown_because": (f"the outage is {scripted_line_seconds:.0f} s of line time and "
                                    f"the agent's health check is every {floor / SAMPLES_TO_SEE:.0f} s "
                                    f"at this speed: shorter than it could see")}
    if not inside:
        why = ("the watch had not started yet" if first is None or first > end
               else "the run ended before this window" if last is not None and last < start
               else "no look landed inside the window")
        return {**row, "verdict": "unknown", "unknown_because": why}

    machines = sorted({code for look in inside for code in (look.get("connections") or {})})
    said_disconnected = sorted({
        code for look in inside
        for code, state in (look.get("connections") or {}).items()
        if state == "disconnected"})
    # One look disagreeing with itself: the same instant in which the plant
    # says it cannot see a machine, and a screen says what that machine is
    # doing. Per look, not across the window - the looks before the agent
    # noticed are legitimately still showing the last state it heard, and
    # counting those would report the detection lag as a lie.
    claiming = sorted({
        f"{code} ({state})"
        for look in inside
        for code, state in (look.get("states") or {}).items()
        if (look.get("connections") or {}).get(code) == "disconnected"
        and state not in ("unknown", "disconnected")})
    worst = max((int((look.get("watching") or {}).get("disconnected") or 0)
                 for look in inside), default=0)

    return {
        **row,
        "looks_inside": len(inside),
        "machines_seen": len(machines),
        "machines_read_disconnected": said_disconnected,
        "health_said_disconnected_at_worst": worst,
        "machines_still_claiming_a_state": claiming,
        "verdict": ("nothing read as disconnected" if not said_disconnected
                    else "a machine kept claiming a state through the outage" if claiming
                    else "read as disconnected"),
    }


def _overhang(measured: float | None, expected: float) -> dict:
    """How the MES's unknown time compares with the script's, and which way.

    The two directions are not symmetrical and the measurement must not average
    them into one "difference". **More** unknown time than was scripted is the
    detection and reconnect lag: the agent takes up to one health check to
    notice the link has gone and up to one retry to find it back, and in
    between it is honestly saying it does not know. **Less** is the fault -
    the MES claiming knowledge of minutes it did not have.
    """
    if measured is None:
        return {"difference_line_seconds": None,
                "difference_says": "no station reported an unknown figure"}
    difference = measured - expected
    if difference >= 0:
        says = ("longer than the script by the agent's detection and reconnect lag - it "
                "notices the link is gone at its next health check and finds it back at its "
                "next retry, and says it does not know in between")
    else:
        says = ("SHORTER than the script: the MES accounted for minutes it could not see, "
                "which is knowledge it did not have")
    return {"difference_line_seconds": round(difference, 1), "difference_says": says}


def _unknown_line_seconds(stations: list[dict], code: str | None, speed: float) -> float | None:
    if not code:
        return None
    said = next((s for s in stations if s.get("code") == code), None)
    if said is None or said.get("unknown_seconds") is None:
        return None
    return round(float(said["unknown_seconds"]) * speed, 1)


def _availability_of(stations: list[dict], code: str | None) -> float | None:
    said = next((s for s in stations if s.get("code") == code), None)
    return None if said is None else said.get("availability")


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
    late = plant.get("measurements", {}).get("latency")
    if late:
        resolution = (late.get("watched") or {}).get("resolution_line_seconds")
        for event in late.get("events") or []:
            for route, said in (event.get("surfaces") or {}).items():
                lag = said.get("lag_line_seconds")
                if lag is None or resolution is None or abs(lag) <= float(resolution):
                    continue
                found.append({
                    "size": abs(lag), "measurement": "latency",
                    "where": f"{plant['plant']} · {event.get('station') or event.get('equipment') or 'the line'}",
                    "plant": plant["plant"], "station": event.get("station"),
                    "what": f"{event.get('event')} reaching {route}",
                    "says": f"{lag:+,.0f} s of line time after the line did it, at a resolution "
                            f"of {float(resolution):.0f} s",
                    "numbers": {"lag_line_seconds": lag,
                                "resolution_line_seconds": resolution,
                                "window_line_s": event.get("window_line_s"),
                                "saw_at_line_second": said.get("saw_at_line_second")},
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
    late = plant.get("measurements", {}).get("latency")
    if late:
        for event in late.get("events") or []:
            for route, said in (event.get("surfaces") or {}).items():
                if said.get("unknown_because"):
                    out.append({"measurement": f"latency · {route}", "plant": plant["plant"],
                                "station": event.get("station"),
                                "because": said["unknown_because"]})
        for surface in late.get("surfaces") or []:
            if surface.get("unknown_because"):
                out.append({"measurement": f"latency · {surface['route']}",
                            "plant": plant["plant"], "station": None,
                            "because": surface["unknown_because"]})
        for block in ("production", "namespace"):
            why = (late.get(block) or {}).get("unknown_because")
            if why:
                out.append({"measurement": f"latency · {block}", "plant": plant["plant"],
                            "station": None, "because": why})
    for key, why in (plant.get("views_refused") or {}).items():
        out.append({"measurement": f"the {key} view", "plant": plant["plant"],
                    "station": None, "because": f"the view did not answer: {why}"})
    return out
