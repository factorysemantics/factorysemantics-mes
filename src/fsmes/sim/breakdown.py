"""A breakdown the floor answers, and the true answers of a scene that has one.

Two halves, both data-driven and both the simulator's, never the product's:

- **The floor's half.** A floor script's `breakdowns` section says which
  machine, stopped with which of its own tags reading what, is a breakdown
  somebody calls maintenance for - FORK1's mould down with its melt under
  206 C is a heater out, not the everyday warm stop at minute 10. `decide`
  says what the floor does on one look at that machine: raise a corrective
  order and start it, close the one it has open, or nothing. The floor
  (`operations.Floor.answer_the_breakdowns`) does the HTTP; this module
  decides, so a test can hold the decision without a plant.

- **The answer key.** `answer_key` reads a scene's own data and the hour the
  generator wrote for it, and says what really happened and what it cost:
  in two halves. *What happened* - minute failed, minute the temperature
  left its band, minute the chart fired, stop and restart, minutes down,
  the part used, technician hours, forks scrapped, output lost, work and
  cost center - read off the generated tables, which are exactly what the
  replay plays to the MES, and checked against its database. *What the ERP
  should show* - the part's price, labour, scrap at material cost, the
  total - fixed numbers checked against nothing yet: costs come from the
  ERP, and no price or cost goes into any MES table, field or tool.

Minutes are line minutes from the start of the line's hour.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path

from fsmes.services import spc
from fsmes.sim import measurement

#: The state a machine that has broken reports, as the MES words it...
DOWN = "down"
#: ...and as the generator writes it in a table's `State` column.
STATE_DOWN = 4


@dataclass(frozen=True)
class Breakdown:
    """One machine's breakdown, as a floor script writes it."""

    machine: str
    #: The tag whose reading tells this breakdown from any other stop.
    tag: str
    #: A stop with the tag reading under this is the breakdown.
    below: float
    summary: str
    reason: str | None
    skill: str | None
    priority: int | None
    #: The person code the work is started and closed as.
    technician: str | None
    findings: str
    #: The part the repair uses, as the stores name it, and how many; `None`
    #: for a breakdown mended without one. A code and a count, never a price.
    part_code: str | None = None
    part_qty: float | None = None
    #: The part of the machine the repair order is raised on - heater band 2
    #: of the mould - when the script names one; `None` raises it on the
    #: machine itself. The machine is still what is watched and what stops.
    on: str | None = None

    @property
    def raised_on(self) -> str:
        """The equipment code the corrective order names."""
        return self.on or self.machine


def breakdowns(script: dict) -> list[Breakdown]:
    """The breakdowns in one floor script; none for a script without the section,
    which is every pack's everyday shift."""
    out: list[Breakdown] = []
    for entry in script.get("breakdowns") or []:
        if not isinstance(entry, dict):
            continue
        when = entry.get("when") or {}
        if when.get("state", DOWN) != DOWN:
            raise ValueError(f"a breakdown on {entry.get('machine')} is a stop the machine "
                             f"reports as {DOWN!r}; {when.get('state')!r} is not one")
        out.append(Breakdown(
            machine=str(entry["machine"]),
            tag=str(when["tag"]),
            below=float(when["below"]),
            summary=str(entry["summary"]),
            reason=entry.get("reason"),
            skill=entry.get("skill"),
            priority=int(entry["priority"]) if entry.get("priority") is not None else None,
            technician=entry.get("technician"),
            findings=str(entry["findings"]),
            part_code=(entry.get("part") or {}).get("code"),
            part_qty=(float(entry["part"]["quantity"]) if (entry.get("part") or {}).get("code") else None),
            on=str(entry["on"]) if entry.get("on") else None,
        ))
    return out


@dataclass(frozen=True)
class Decision:
    """What the floor does on one look: `raise`, `close`, or nothing (`None`)."""

    act: str | None
    #: For `close`: the line minutes the machine was down, from the MES's own
    #: record of when the stop began and when the next state began.
    downtime_minutes: float | None = None


def decide(rule: Breakdown, state: str | None, since_s: float | None,
           reading: float | None, open_since_s: float | None, speed: float) -> Decision:
    """One look at `rule.machine`.

    `state` and `since_s` are the MES's current state for the machine and
    when it began (seconds on any clock both share); `reading` is the newest
    value of the rule's tag, `None` when nobody can say; `open_since_s` is
    when the stop this floor has an order open for began, `None` when it has
    none. `speed` is line seconds per floor second.

    - down, no order open, the tag reading under the threshold: raise one.
      A down with no reading of the tag raises nothing: unknown is not cold.
    - an order open and the machine no longer down: close it, with the
      minutes between the stop's start and the next state's start.
    """
    if open_since_s is None:
        if state == DOWN and reading is not None and reading < rule.below:
            return Decision("raise")
        return Decision(None)
    if state is not None and state != DOWN and since_s is not None:
        minutes = max(since_s - open_since_s, 0.0) * speed / 60.0
        return Decision("close", round(minutes, 1))
    return Decision(None)


# ------------------------------------------------------------- the answer key


def _table(path: Path) -> list[dict]:
    with path.open(encoding="ascii", newline="") as f:
        return list(csv.DictReader(f))


def _line(scene: dict, out_dir: Path) -> dict:
    """The scene's line as the copy beside `out_dir` was built with it."""
    config = json.loads((Path(out_dir).parent / "line.json").read_text(encoding="utf-8"))
    return next(e for e in config["lines"] if e["name"] == scene["answer"]["line"])


def _round(value: float) -> float:
    return round(value, 2)


def answer_key(scene: dict, out_dir: Path, specs: list[dict]) -> dict:
    """The true answers of a scene with a breakdown in it.

    `out_dir` holds the generated hour (`<machine>.csv`); `specs` is the
    pack's quality specifications, for the chart's characteristic. Every
    number is read off the scene or the tables, and each says where from.
    """
    answer = scene["answer"]
    line, machine, tag = answer["line"], answer["machine"], answer["tag"]
    station = machine.split("_", 1)[1]
    rows = _table(Path(out_dir) / f"{machine}.csv")
    by_s = {int(r["TSec"]): r for r in rows}

    # The line's events as built into the copy: the scene's and the everyday
    # ones it kept, so the everyday stop is a stop here too.
    downs = [e for e in _line(scene, out_dir)["events"]
             if e["type"] == "down" and e.get("station") == station and "unit" not in e
             and "side" not in e]
    stop = max(downs, key=lambda e: int(e["end"]) - int(e["start"]))
    stop_s, restart_s = int(stop["start"]), int(stop["end"])
    failed_s = int(answer["failed"]["at_s"])

    analog = _analog(scene, out_dir, station, tag)
    low = float(analog["base"]) - float(answer.get("band_sigmas", 2)) * float(analog["noise"])
    left_band_s = _first_minute_mean_under(by_s, tag, low, failed_s)

    made = {t: int(r["TotalCount"]) for t, r in by_s.items()}
    scrap = {t: int(r["ScrapCount"]) for t, r in by_s.items()}
    good = {t: int(r["GoodCount"]) for t, r in by_s.items()}
    base_pct = float(analog["scrap_pct"])
    made_in_fault = made[stop_s] - made[failed_s]
    scrap_in_fault = scrap[stop_s] - scrap[failed_s]
    expected = made_in_fault * base_pct / 100.0
    scrapped = max(round(scrap_in_fault - expected), 0)

    # The mould's own healthy rate: good forks a minute between its everyday
    # stop and the fault, a stretch with no stop in it.
    healthy_from = max(int(e["end"]) for e in downs if int(e["end"]) <= failed_s) \
        if any(int(e["end"]) <= failed_s for e in downs) else 0
    per_min = (good[failed_s] - good[healthy_from]) / ((failed_s - healthy_from) / 60.0)
    minutes_down = (restart_s - stop_s) / 60.0
    lost = round(per_min * minutes_down)

    weight = next(s for s in specs if s["material"] == answer["characteristic"].split()[0]
                  and s["characteristic"] == "weight_g")
    grams = float(weight.get("target") or (weight["min"] + weight["max"]) / 2)
    resin = float(answer["resin_price_per_kg"])
    scrap_cost = scrapped * grams / 1000.0 * resin
    part = answer["part"]
    part_cost = float(part["price"]) * int(part.get("quantity", 1))
    labour_h = minutes_down / 60.0 * int(answer["labour"].get("people", 1))
    labour_cost = labour_h * float(answer["labour"]["rate_per_hour"])

    fired_s = chart_fired_s(scene, out_dir, specs)

    centre, cost_centre = _work_and_cost_center(Path(out_dir).parent / "masterdata", machine)
    return {
        "_what": "The true answers of this scene, in two halves. 'what_happened' is what "
                 "the MES should hold after the replay and what the scene's test checks "
                 "against its database. 'what_the_erp_should_show' is fixed numbers for "
                 "the costs, checked against nothing yet: costs come from the ERP, and no "
                 "price or cost goes into any MES table, field or tool. Minutes are line "
                 "minutes from the start of the line's hour.",
        "scene": scene["name"],
        "what_happened": {
            "line": line,
            "machine": machine,
            "work_center": centre,
            "cost_center": cost_centre,
            "failed": answer["failed"]["part"],
            "zone": answer["failed"]["zone"],
            "minute_failed": failed_s / 60.0,
            "minute_left_band": left_band_s / 60.0 if left_band_s is not None else None,
            "_minute_left_band": f"the first line minute whose mean {tag} is under "
                                 f"{low:g} {analog.get('unit', '')}: its {analog['base']:g} "
                                 f"base less {answer.get('band_sigmas', 2)} noise "
                                 "sigmas".strip(),
            "minute_chart_fired": round(fired_s / 60.0, 1) if fired_s is not None else None,
            "_minute_chart_fired": (f"the first {answer['characteristic']} point beyond "
                                    f"three sigma (rule 1) off {machine} after the fault, "
                                    f"on the check stream seeded "
                                    f"{answer.get('chart_seed', 7)}; a live floor draws "
                                    "its own pieces and fires within a few minutes of it"),
            "minute_stopped": stop_s / 60.0,
            "minute_restarted": restart_s / 60.0,
            "minutes_down": minutes_down,
            "part_used": {"name": part["name"], "quantity": int(part.get("quantity", 1))},
            "technician_hours": _round(labour_h),
            "forks_scrapped": scrapped,
            "_forks_scrapped": (f"scrap counted at {machine} from minute {failed_s / 60:g} "
                                f"to {stop_s / 60:g} ({scrap_in_fault}) less what its "
                                f"everyday {base_pct:g} % would have scrapped of the "
                                f"{made_in_fault} made"),
            "output_lost": lost,
            "_output_lost": (f"{per_min:.2f} good forks a minute (its own, minutes "
                             f"{healthy_from / 60:g}-{failed_s / 60:g}) for the "
                             f"{minutes_down:g} minutes down"),
        },
        "what_the_erp_should_show": {
            "_what": "Fixed numbers, made up to be plausible, checked against nothing yet. "
                     "The MES holds none of them.",
            "currency": answer.get("currency"),
            "part": {"name": part["name"], "quantity": int(part.get("quantity", 1)),
                     "price": float(part["price"]), "cost": _round(part_cost)},
            "labour": {"hours": _round(labour_h),
                       "rate_per_hour": float(answer["labour"]["rate_per_hour"]),
                       "cost": _round(labour_cost)},
            "scrap": {"forks": scrapped, "grams_each": round(grams, 3),
                      "material_price_per_kg": resin, "cost": _round(scrap_cost)},
            "output_lost": {"forks": lost, "cost": None,
                            "_cost": "no price for a fork is in the design: counted, not "
                                     "priced, and not in the total"},
            "total": _round(part_cost + labour_cost + scrap_cost),
            "_total": "part + labour + scrapped material",
        },
        "places": "coil_places.json (drawn, not measured)",
    }


def _work_and_cost_center(masterdata: Path, machine: str) -> tuple[str | None, str | None]:
    """The machine's work center and the cost center it inherits, read off the
    copy's equipment file the way `services.masterdata` walks the tree."""
    rows = {r["code"]: r for r in json.loads((masterdata / "equipment.json")
                                             .read_text(encoding="utf-8"))}
    centre = cost = None
    node, seen = rows.get(machine), set()
    while node is not None and node["code"] not in seen:
        seen.add(node["code"])
        if centre is None and node.get("level") == "work_center":
            centre = node["code"]
        if cost is None and node.get("cost_center"):
            cost = node["cost_center"]
        node = rows.get(node.get("parent"))
    return centre, cost


def _analog(scene: dict, out_dir: Path, station: str, tag: str) -> dict:
    """The station's own analog as the line file built into the copy writes it,
    with the station's everyday scrap rate beside it."""
    st = next(s for s in _line(scene, out_dir)["stations"] if s["name"] == station)
    analog = next(a for a in st.get("analogs") or [] if a.get("name") == tag)
    return {**analog, "scrap_pct": st.get("scrap_pct", 0.0)}


def _first_minute_mean_under(by_s: dict[int, dict], tag: str, low: float,
                             after_s: int) -> int | None:
    """The first whole line minute from `after_s` whose mean reading is under `low`."""
    last = max(by_s)
    for start in range(after_s, last - 59, 60):
        values = [float(by_s[t][tag]) for t in range(start, start + 60)
                  if t in by_s and by_s[t][tag] not in ("", None)]
        if values and sum(values) / len(values) < low:
            return start
    return None


def check_stream(scene: dict, out_dir: Path, characteristic: str,
                 seed: int) -> list[tuple[int, str, float]]:
    """The checks a floor would take of one characteristic over the hour:
    one every `every_line_s`, off a piece one of the rule's moulds made,
    drawn at that mould's reading of the followed tag then. Seeded, so the
    answer key and a test read the same stream."""
    material, name = characteristic.split()
    rule = next(r for r in measurement.piece_rules(scene)
                if (r.material, r.characteristic) == (material, name))
    tag = rule.follows.tag if rule.follows else None
    hours: dict[str, list[tuple[bool, float | None]]] = {}
    for mould in rule.stations:
        rows = _table(Path(out_dir) / f"{mould}.csv")
        readings = [float(r[tag]) if tag and r[tag] else None for r in rows]
        hours[mould] = [(int(r["State"]) == STATE_DOWN, _trailing_mean(readings, i))
                        for i, r in enumerate(rows)]
    # The longest hour is the story's; a shorter one replays on its own length.
    length = max(len(h) for h in hours.values())
    rng = random.Random(seed)
    stream = []
    for t in range(0, length, int(rule.every_line_s)):
        working = [m for m in sorted(rule.stations) if not hours[m][t % len(hours[m])][0]]
        if not working:
            continue
        # A mould that is down makes nothing, so nothing is measured off it.
        mould = rng.choice(working)
        stream.append((t, mould, rule.draw(rng, hours[mould][t % len(hours[mould])][1])))
    return stream


#: How many line seconds of a tag a piece's reading is the mean of. The live
#: floor reads the newest bucket of a three-minute trend (`Floor._reading_of`),
#: which at the scene's 10x is about thirty line seconds; a fork's fill follows
#: the melt in the barrel, not one second's noise on its thermocouple.
READING_MEAN_S = 30


def _trailing_mean(readings: list[float | None], i: int) -> float | None:
    window = [v for v in readings[max(i - READING_MEAN_S + 1, 0):i + 1] if v is not None]
    return sum(window) / len(window) if window else None


def chart_fired_s(scene: dict, out_dir: Path, specs: list[dict]) -> int | None:
    """The line second of the first rule-1 point off the broken machine after
    the fault, judged as the chart judges it: limits from the history so far
    (the MES's own `spc` arithmetic), the newest point against them."""
    answer = scene["answer"]
    stream = check_stream(scene, out_dir, answer["characteristic"],
                          int(answer.get("chart_seed", 7)))
    failed_s = int(answer["failed"]["at_s"])
    values: list[float] = []
    for t, mould, value in stream:
        values.append(value)
        history = values[-spc.HISTORY:]
        if len(history) < spc.MIN_POINTS or t < failed_s or mould != answer["machine"]:
            continue
        limits = spc._limits(spc.Series(kind=spc.INDIVIDUALS, size=1, values=history,
                                        stamps=[], keys=[], checks=[], readings=history))
        if limits is None:
            continue
        if abs(value - limits["centre"]) > 3 * limits["sigma"]:
            return t
    return None
