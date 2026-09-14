"""Starving and blocking, scripted - and what the MES must not call them.

A machine with nothing to work on, or nowhere to put what it has made, is
making nothing and there is nothing wrong with it. Counting those minutes as
downtime overstates breakdowns and understates availability, and it is the
kind of error that survives for years because the number still looks
plausible. It is the same fault as calling a changeover downtime, from the
other direction, and it is now scriptable so a run can look for it.

Nothing here starts a plant: the generator is a pure function of its config,
and the scorer is a pure function of the script and the timeline the MES
returned.
"""

import json
from datetime import datetime, timedelta

import pytest

from fsmes.lab import measure
from fsmes.lab.plan import Plan, PlanError
from fsmes.sim import generate as gen
from fsmes.sim.score import score_run
from fsmes.sim.truth import ScriptedEvent

LINE = {
    "channel": "Tiny",
    "seed": 5,
    "duration_s": 300,
    "orders": [900],
    "buffers": {"capacity": 20, "initial": 10},
    "stations": [
        {"name": "Cut", "rate_per_min": 60, "scrap_pct": 0.0},
        {"name": "Pack", "rate_per_min": 60, "scrap_pct": 0.0},
    ],
    "events": [],
}

STATE_COLUMN = 1


def _states(rows, station: str) -> list[int]:
    return [row[STATE_COLUMN] for row in rows[station]]


def _line(**events) -> dict:
    body = json.loads(json.dumps(LINE))
    body["events"] = events.get("events", [])
    return body


# ------------------------------------------------------------- the generator

def test_a_scripted_starve_makes_the_machine_starved_and_not_down():
    """The difference the whole measurement rests on: a starved machine is
    willing. A generator that wrote DOWN here would make the MES look right
    for agreeing."""
    rows = gen.simulate(_line(events=[
        {"type": "starve", "station": "Cut", "start": 100, "end": 160}]))
    states = _states(rows, "Cut")
    assert states[120] == gen.STARVED
    assert gen.DOWN not in states[100:161]
    assert states[90] == gen.RUNNING


def test_a_scripted_block_makes_the_machine_blocked():
    rows = gen.simulate(_line(events=[
        {"type": "block", "station": "Cut", "start": 100, "end": 160}]))
    assert _states(rows, "Cut")[120] == gen.BLOCKED


def test_starving_the_first_station_starves_the_rest_of_the_line_on_its_own():
    """The knock-on is the point of scripting the cause rather than the
    symptom: a real line starves in order, station by station, as each
    buffer empties."""
    rows = gen.simulate(_line(events=[
        {"type": "starve", "station": "Cut", "start": 30, "end": 290}]))
    downstream = _states(rows, "Pack")
    assert gen.STARVED in downstream, "Pack never ran out of work"
    assert downstream.index(gen.STARVED) > 30, (
        "Pack starved before the buffer between them could empty")


def test_a_starve_makes_no_units_and_the_counters_stand_still():
    good_column = 2
    rows = gen.simulate(_line(events=[
        {"type": "starve", "station": "Cut", "start": 100, "end": 160}]))
    assert rows["Cut"][159][good_column] == rows["Cut"][101][good_column]


def test_the_same_seed_and_script_still_give_the_same_line():
    script = [{"type": "starve", "station": "Cut", "start": 100, "end": 160}]
    assert gen.simulate(_line(events=script)) == gen.simulate(_line(events=script))


# ------------------------------------------------------- the closed vocabulary

def test_every_event_the_generator_plays_is_in_the_written_vocabulary():
    """The list in EVENT_TYPES is what the docs and the refusals quote. A
    type the simulator handles and the vocabulary does not is a scenario
    nobody can find."""
    assert set(gen.EVENT_TYPES) >= {"down", "changeover", "micro_stops", "drift",
                                    "scrap_burst", "counter_reset", "starve", "block"}
    for name, says in gen.EVENT_TYPES.items():
        assert says and says[0].islower(), f"{name} needs a sentence saying what it does"


def test_a_plan_that_scripts_an_event_no_line_can_play_is_refused_with_the_vocabulary(tmp_path):
    """Refused before anything is built. The generator refuses it too, but by
    exiting the process from inside a library call, several steps later, after
    the run has already made a directory."""
    from fsmes.lab import build as builder
    from fsmes.pack import format as fmt

    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "line.json").write_text(json.dumps(LINE), encoding="utf-8")
    (pack_dir / "plant.toml").write_text(
        '[pack]\nformat = 1\n\n[plant]\nname = "tiny"\ntimezone = "UTC"\n\n'
        '[files]\ntag_map = "tag_map.json"\nreplay_dir = "out"\n', encoding="utf-8")
    (pack_dir / "tag_map.json").write_text("{}", encoding="utf-8")

    plan = Plan(name="p", path=tmp_path / "plan.toml", packs=(pack_dir,),
                measure=("booking",), speed=10.0,
                scenario={"tiny": [{"type": "flood", "station": "Cut", "start": 1, "end": 2}]})
    with pytest.raises(PlanError) as raised:
        builder.script(plan, fmt.read(pack_dir), tmp_path / "line" / "tiny.json")
    assert "flood" in str(raised.value)
    assert "starve" in str(raised.value), "the refusal has to say what a line can be told to do"


# ------------------------------------------------------------- the scorer

T0 = datetime(2026, 9, 14, 12, 0, 0)


def _timeline(state: str, start_s: int, end_s: int, equipment: str = "CUT01",
              watched_s: int = 3600) -> dict:
    return {
        "window": {"start": T0.isoformat(),
                   "end": (T0 + timedelta(seconds=watched_s)).isoformat()},
        "machines": [{"code": equipment, "intervals": [
            {"state": state, "equipment": equipment, "reason": None,
             "start": (T0 + timedelta(seconds=start_s)).isoformat(),
             "end": (T0 + timedelta(seconds=end_s)).isoformat()}]}],
    }


def _truth(event_type: str) -> dict:
    return {"duration_s": 300, "equipment": ["CUT01"], "stations": ["Cut"],
            "events": [ScriptedEvent(type=event_type, start_s=100, end_s=160,
                                     station="Cut", equipment="CUT01")]}


def test_a_starved_machine_the_mes_called_down_is_a_finding_with_the_seconds():
    card = score_run(_truth("starve"), _timeline("down", 100, 160), T0, speed=1.0)
    stop = card["idle_stops"][0]
    assert stop["misclassified_as_downtime"] is True
    assert stop["offenders"][0]["equipment"] == "CUT01"
    assert card["metrics"]["idle_stop_misclassified"] == 1


def test_a_starved_machine_the_mes_called_idle_is_not_a_finding():
    card = score_run(_truth("starve"), _timeline("idle", 100, 160), T0, speed=1.0)
    assert card["idle_stops"][0]["misclassified_as_downtime"] is False
    assert card["metrics"]["idle_stop_misclassified"] == 0


def test_a_window_the_mes_never_watched_is_unknown_and_not_a_pass():
    """Never a zero standing in for silence - the same rule the planned stops
    already keep."""
    card = score_run(_truth("block"), _timeline("idle", 0, 50, watched_s=50), T0, speed=1.0)
    assert card["idle_stops"][0]["observed"] is False
    assert card["metrics"]["idle_stop_misclassified"] is None
    assert card["metrics"]["idle_stops_scored"] == 0


def test_a_neighbour_that_really_broke_is_not_counted_against_a_starved_machine():
    """Unlike a changeover, which is the whole line stopping together, having
    nothing to work on is a fact about one station."""
    card = score_run(_truth("starve"), _timeline("down", 100, 160, equipment="PACK01"),
                     T0, speed=1.0)
    assert card["idle_stops"][0]["misclassified_as_downtime"] is False


# --------------------------------------------------------- the measurement

class _Station:
    """Just enough of StationTruth for the downtime measurement."""

    down_seconds = 0
    changeover_seconds = 0

    def __init__(self):
        self.seconds_by_state = {"starved": 60, "blocked": 0}
        self.overlap_seconds_by_state = {}


class _Truth:
    def __init__(self):
        self.stations = {"Cut": _Station()}


def test_the_measurement_carries_the_scripted_windows_and_the_lines_own_idle_seconds():
    card = score_run(_truth("starve"), _timeline("down", 100, 160), T0, speed=1.0)
    out = measure.downtime(_Truth(), card, {"total_seconds": 60}, speed=1.0)
    idle = out["idle_stops"]
    assert idle["misclassified_as_downtime"] == 1
    assert idle["truth_line_seconds"] == {"starved": 60, "blocked": 0}
    assert idle["events"][0]["station"] == "Cut"


def test_a_starve_counted_as_downtime_reaches_the_differences_and_the_roll_up():
    card = score_run(_truth("starve"), _timeline("down", 100, 160), T0, speed=1.0)
    plant = {"plant": "tiny", "measurements": {
        "downtime": measure.downtime(_Truth(), card, {"total_seconds": 60}, speed=1.0)}}
    rows = measure.differences(plant)
    assert any(row["what"] == "starve counted as downtime" for row in rows)
    assert any("CUT01" in row["says"] for row in rows)


def test_a_window_nobody_watched_reaches_the_unknowns_rather_than_the_differences():
    card = score_run(_truth("starve"), _timeline("idle", 0, 50, watched_s=50), T0, speed=1.0)
    plant = {"plant": "tiny", "measurements": {
        "downtime": measure.downtime(_Truth(), card, {"total_seconds": 60}, speed=1.0)}}
    assert measure.differences(plant) == []
    assert any(row["measurement"] == "downtime · idle" for row in measure.unknowns(plant))


def test_a_scripted_window_is_printed_in_the_lines_own_seconds_not_the_watchs():
    """At 20x a 180-second stop is nine seconds of anybody's watch. Printing
    that nine under a heading that says *line seconds* is the quiet mislabel
    this lab exists to catch."""
    card = score_run(_truth("starve"), _timeline("idle", 100, 160), T0, speed=20.0)
    out = measure.downtime(_Truth(), card, {"total_seconds": 60}, speed=20.0)
    event = out["idle_stops"]["events"][0]
    assert event["window_line_s"] == [100, 160]
    assert event["scripted_line_seconds"] == 60.0
    assert event["scripted_wall_seconds"] == 3.0
