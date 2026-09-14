"""Watching the plant while the hour plays, and what the looks are worth.

Every measurement the lab took before this one asked the plant one question at
the end. That is enough for *what did the MES end up recording* and no use at
all for *how long did it take to say so*: by the time a run is over, a stop
that took four minutes to appear and one that appeared at once have left the
same trace.

So the runner grew a during-run hook and the lab grew a watcher to hand it.
Nothing here starts a plant: the hook is exercised with a fake clock and a
counter, and the watcher with a function that returns what a plant would have.
"""

import json

import pytest

from fsmes.lab import measure
from fsmes.lab import observe as observe_mod
from fsmes.lab import report as lab_report
from fsmes.lab import truth as lab_truth
from fsmes.lab.plan import PlanError, read_plan
from fsmes.sim import runner
from fsmes.sim.generate import generate

# ------------------------------------------------------------ the hook


def test_a_run_with_no_observer_still_just_sleeps(monkeypatch):
    """The hook is an addition. A run that does not measure latency must not
    pay for a loop, a clock read or a single HTTP request."""
    slept = []
    monkeypatch.setattr(runner.time, "sleep", slept.append)
    assert runner.play(3.0) == 0
    assert slept == [3.0]


def test_the_observer_is_handed_the_line_second_because_only_the_run_knows_it(monkeypatch):
    """A plant cannot work out when the replay's first tick was - that instant
    is decided by the runner, which waits for the log line rather than guessing
    it. So the line second is passed in, not asked for."""
    from datetime import datetime, timedelta

    t0 = datetime(2026, 9, 14, 12, 0, 0)
    ticks = iter([t0 + timedelta(seconds=n) for n in (1, 2, 3)])
    monkeypatch.setattr(runner, "_now_mes", lambda: next(ticks))
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    clock = iter([0.0, 0.5, 1.0, 1.5, 3.1])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))

    seen = []
    runner.play(2.0, lambda base, token, second: seen.append(second),
                base="http://x", token="t", t0=t0, speed=20.0, every_s=0.5)
    # One wall second into the run at 20x is twenty line seconds.
    assert seen == [20.0, 40.0, 60.0]


def test_an_observer_that_raises_does_not_end_the_run(monkeypatch):
    """Losing the hour because one HTTP call came back badly would be the
    harness throwing away the evidence it exists to collect."""
    from datetime import datetime

    monkeypatch.setattr(runner, "_now_mes", lambda: datetime(2026, 9, 14, 12, 0, 0))
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    clock = iter([0.0, 0.5, 1.0, 1.5, 3.1])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))

    said = []

    def angry(base, token, second):
        raise RuntimeError("the API said no")

    calls = runner.play(2.0, angry, base="http://x", token="t",
                        t0=datetime(2026, 9, 14, 12, 0, 0), speed=1.0, every_s=0.5,
                        echo=said.append)
    assert calls == 0
    assert len(said) == 1, "said once, not once per look"
    assert "the run continues" in said[0]


# ---------------------------------------------------------- the watcher


def _plant(states, booked=(), cursor=7):
    """A function shaped like the runner's reader, answering for one plant."""

    def get(base, path, token):
        if path == "/equipment/states":
            return [{"equipment": code, "state": state, "reason": None, "since": None}
                    for code, state in states.items()]
        if path.startswith("/line/events"):
            return {"cursor": cursor, "truncated": False,
                    "stations": [{"code": code, "state": state}
                                 for code, state in states.items()],
                    "units": [{"equipment": "PACK01", "good": g, "scrap": 0} for g in booked]}
        if path == "/health":
            return {"status": "ok"}
        raise AssertionError(f"the watcher asked for {path}, which is not one of its surfaces")

    return get


def test_a_look_writes_down_what_each_screen_was_showing_and_when():
    watch = observe_mod.Watch(get=_plant({"CUT01": "down", "PACK01": "running"}))
    watch("http://x", "t", 120.0)

    look = watch.looks[0]
    assert look.line_second == 120.0
    assert look.states == {"CUT01": "down", "PACK01": "running"}
    assert look.line_states == {"CUT01": "down", "PACK01": "running"}
    assert look.refused == {}


def test_the_line_views_count_is_a_running_total_the_watcher_keeps():
    """The feed is a cursor: it answers with what has happened since the id you
    last saw. Keeping the total is what makes each look cost one small reply
    rather than the hour so far."""
    watch = observe_mod.Watch(get=_plant({"CUT01": "running"}, booked=(3, 4)))
    watch("http://x", "t", 10.0)
    watch("http://x", "t", 20.0)
    assert watch.looks[0].booked_good == {"PACK01": 7}
    assert watch.looks[1].booked_good == {"PACK01": 14}


def test_a_screen_that_did_not_answer_is_written_down_rather_than_thrown():
    """A measurement built on nine hundred looks and eleven failures should say
    so, not average over the gap."""

    def half_broken(base, path, token):
        if path == "/equipment/states":
            raise OSError("connection refused")
        return {"cursor": 1, "stations": [], "units": []}

    watch = observe_mod.Watch(get=half_broken)
    watch("http://x", "t", 5.0)
    watch("http://x", "t", 6.0)
    assert watch.failures == {"/equipment/states": 2}
    assert "connection refused" in watch.looks[0].refused["/equipment/states"]
    assert watch.looks[0].line_states == {}


def test_the_raw_looks_are_written_beside_the_run(tmp_path):
    """A version of the measurement that asked the wrong question is worth
    re-running against an hour somebody already paid for."""
    watch = observe_mod.Watch(get=_plant({"CUT01": "running"}))
    watch("http://x", "t", 1.0)
    watch.write(tmp_path / "watched.json")
    said = json.loads((tmp_path / "watched.json").read_text(encoding="utf-8"))
    assert said["looks_total"] == 1
    assert said["looks"][0]["states"] == {"CUT01": "running"}
    assert "/equipment/states" in said["surfaces"]


# ------------------------------------------------------- the measurement

LINE = {
    "channel": "Tiny", "seed": 3, "duration_s": 300, "orders": [900],
    "buffers": {"capacity": 10, "initial": 5},
    "stations": [{"name": "Cut", "rate_per_min": 60, "scrap_pct": 0.0},
                 {"name": "Pack", "rate_per_min": 60, "scrap_pct": 0.0}],
    "events": [{"type": "down", "station": "Cut", "start": 100, "end": 160}],
}

MAPPING = {"Cut": "CUT01", "Pack": "PACK01"}

CARD = {
    "faults": [{"event": "down", "equipment": "CUT01", "window_sim_s": [100, 160]}],
    "idle_stops": [], "planned_stops": [],
}


def _watched(showing_from: float | None, every=1.0, speed=20.0, last=300.0,
             equipment="CUT01", state="down", failures=None) -> dict:
    """Looks every 20 line seconds across the run, showing `state` from
    `showing_from` onward."""
    looks = []
    second = 0.0
    while second <= last:
        states = {equipment: state} if showing_from is not None and second >= showing_from \
            else {equipment: "running"}
        looks.append({"line_second": second, "at": "2026-09-14T12:00:00",
                      "states": states, "line_states": states,
                      "booked_good": None, "booked_scrap": None,
                      "truncated": False, "refused": {}})
        second += every * speed
    return {"looks_total": len(looks), "looks": looks, "failures": failures or {},
            "every_wall_seconds": every, "resolution_line_seconds": every * speed}


@pytest.fixture
def truth(tmp_path) -> lab_truth.LineTruth:
    line = tmp_path / "line.json"
    line.write_text(json.dumps(LINE), encoding="utf-8")
    out = tmp_path / "out"
    generate(line, out, write_docs=False)
    return lab_truth.read(out, LINE, 300, overlap_s=0)


def _lag(out, route="/equipment/states"):
    return out["events"][0]["surfaces"][route]


def test_a_lag_bigger_than_the_polling_interval_is_a_number(truth):
    out = measure.latency(CARD, _watched(showing_from=140.0), truth, MAPPING, speed=20.0)
    said = _lag(out)
    assert said["saw_at_line_second"] == 140.0
    assert said["lag_line_seconds"] == 40.0
    assert said["lag_says"] == "+40.0 s"


def test_a_lag_smaller_than_the_polling_interval_is_within_resolution(truth):
    """A screen is only ever known to have shown something by the look that saw
    it. The same rule as a detection lag, and the same reason."""
    out = measure.latency(CARD, _watched(showing_from=100.0), truth, MAPPING, speed=20.0)
    assert _lag(out)["lag_says"] == "within resolution (20 s)"


def test_an_event_no_look_caught_is_unknown_and_never_a_maximum(truth):
    out = measure.latency(CARD, _watched(showing_from=None), truth, MAPPING, speed=20.0)
    said = _lag(out)
    assert said["lag_line_seconds"] is None
    assert "no look between line second 100 and 160 showed 'down'" in said["unknown_because"]
    assert "does not tell those two apart" in said["unknown_because"]


def test_an_unanswered_screen_says_how_many_looks_it_lost(truth):
    out = measure.latency(CARD, _watched(showing_from=None, failures={"/equipment/states": 7}),
                          truth, MAPPING, speed=20.0)
    assert "did not answer 7 time(s)" in _lag(out)["unknown_because"]


def test_an_event_that_happened_while_nobody_was_looking_is_unknown(truth):
    """Not seen because nobody looked is a different fact from not shown, and
    the two must not be one number."""
    out = measure.latency(CARD, _watched(showing_from=100.0, last=80.0), truth, MAPPING, speed=20.0)
    assert "while nobody was looking" in _lag(out)["unknown_because"]


def test_a_machine_already_showing_the_state_before_the_script_is_not_an_early_screen(truth):
    """A machine the MES already had down before the script stopped it would
    otherwise report a negative lag, as though the screen had been early."""
    out = measure.latency(CARD, _watched(showing_from=0.0), truth, MAPPING, speed=20.0)
    said = _lag(out)
    # The first look inside the window, not the first look showing it.
    assert said["saw_at_line_second"] >= 100.0
    assert said["lag_line_seconds"] >= 0


def test_a_withheld_run_makes_every_latency_unknown_with_the_same_reason(truth):
    why = "the harness fell behind its own 500 ms sample"
    out = measure.latency(CARD, _watched(showing_from=140.0), truth, MAPPING, speed=20.0, reason=why)
    assert out["unknown_because"] == why
    assert _lag(out)["unknown_because"] == why
    assert _lag(out)["lag_line_seconds"] is None


def test_each_screen_is_asked_separately_so_two_can_disagree(truth):
    """Two screens showing one machine two different states at one instant is a
    finding nothing else in the lab would catch, so they are never merged."""
    watched = _watched(showing_from=140.0)
    for look in watched["looks"]:
        look["line_states"] = {"CUT01": "running"}        # the line view never shows it
    out = measure.latency(CARD, watched, truth, MAPPING, speed=20.0)
    assert _lag(out, "/equipment/states")["lag_line_seconds"] == 40.0
    assert _lag(out, "/line/events")["lag_line_seconds"] is None


def test_health_is_asked_and_says_the_question_does_not_belong_to_it(truth):
    """Named rather than quietly left out: a reader who expected a number here
    is entitled to know the route was asked and cannot answer."""
    out = measure.latency(CARD, _watched(showing_from=140.0), truth, MAPPING, speed=20.0)
    health = next(s for s in out["surfaces"] if s["route"] == "/health")
    assert health["answers_when_the_mes_noticed"] is False
    assert "carries nothing about what any machine is doing" in health["unknown_because"]


def test_a_changeover_is_line_wide_and_the_first_screen_to_show_it_answers(truth):
    card = {"faults": [], "idle_stops": [],
            "planned_stops": [{"event": "changeover", "window_sim_s": [100, 160]}]}
    watched = _watched(showing_from=120.0, state="setup", equipment="PACK01")
    out = measure.latency(card, watched, truth, MAPPING, speed=20.0)
    assert out["events"][0]["line_wide"] is True
    assert out["events"][0]["expected_state"] == "setup"
    assert _lag(out)["lag_line_seconds"] == 20.0


def test_a_starved_machine_is_waited_for_as_idle_not_as_down(truth):
    """A latency measurement that waited for the wrong word would report every
    starve as never seen."""
    card = {"faults": [], "planned_stops": [],
            "idle_stops": [{"event": "starve", "equipment": "CUT01", "station": "Cut",
                            "window_sim_s": [100, 160]}]}
    out = measure.latency(card, _watched(showing_from=120.0, state="idle"), truth, MAPPING, speed=20.0)
    assert out["events"][0]["expected_state"] == "idle"
    assert _lag(out)["lag_line_seconds"] == 20.0


# ------------------------------------------- how far behind the screen ran


def _counting(truth, behind_units: int, every=1.0, speed=20.0) -> dict:
    looks = []
    second = 0.0
    while second <= 280.0:
        made = truth.good_at(second) or 0
        looks.append({"line_second": second, "at": "2026-09-14T12:00:00",
                      "states": {}, "line_states": {},
                      "booked_good": {"PACK01": max(0.0, float(made - behind_units))},
                      "booked_scrap": {}, "truncated": False, "refused": {}})
        second += every * speed
    return {"looks_total": len(looks), "looks": looks, "failures": {},
            "the_feed_answered_with_a_cursor": True,
            "every_wall_seconds": every, "resolution_line_seconds": every * speed}


def test_how_far_behind_the_line_view_ran_is_counted_in_units(truth):
    out = measure.latency(CARD, _counting(truth, behind_units=12), truth, MAPPING, speed=20.0)
    production = out["production"]
    assert production["unknown_because"] is None
    assert production["worst_behind_units"] == pytest.approx(12, abs=1)
    assert "Units, not seconds" in production["note"]
    # The machine the line ends at, and only it: a serial line counts most
    # units once per station, so summing every machine's would be six times
    # the answer at six stations.
    assert production["machine"] == "PACK01"
    assert production["station"] == "Pack"


def test_a_run_whose_line_view_answered_no_look_says_so_rather_than_zero(truth):
    out = measure.latency(CARD, _watched(showing_from=140.0), truth, MAPPING, speed=20.0)
    assert out["production"]["unknown_because"] is not None
    assert "not something this run establishes" in out["production"]["unknown_because"]


def test_the_namespace_is_unknown_and_says_no_broker_was_configured(truth):
    out = measure.latency(CARD, _watched(showing_from=140.0), truth, MAPPING, speed=20.0)
    assert out["namespace"]["lag_line_seconds"] is None
    assert "no broker was configured" in out["namespace"]["unknown_because"]


# ------------------------------------------ what reaches the roll-up


def test_a_lag_past_the_resolution_reaches_the_differences(truth):
    plant = {"plant": "tiny", "measurements": {
        "latency": measure.latency(CARD, _watched(showing_from=140.0), truth, MAPPING, speed=20.0)}}
    rows = [row for row in measure.differences(plant) if row["measurement"] == "latency"]
    assert rows, "a forty-second lag at a twenty-second resolution is a difference"
    assert rows[0]["what"] == "down reaching /equipment/states"
    assert "at a resolution of 20 s" in rows[0]["says"]


def test_a_lag_inside_the_resolution_reaches_neither_list(truth):
    """Repeating quantisation as a finding is how a list of findings stops
    being read."""
    plant = {"plant": "tiny", "measurements": {
        "latency": measure.latency(CARD, _watched(showing_from=100.0), truth, MAPPING, speed=20.0)}}
    assert [row for row in measure.differences(plant) if row["measurement"] == "latency"] == []


def test_an_event_nobody_saw_reaches_the_unknowns(truth):
    plant = {"plant": "tiny", "measurements": {
        "latency": measure.latency(CARD, _watched(showing_from=None), truth, MAPPING, speed=20.0)}}
    reasons = [row["because"] for row in measure.unknowns(plant)
               if row["measurement"].startswith("latency")]
    assert any("no look between" in reason for reason in reasons)
    assert any("carries nothing about what any machine is doing" in reason for reason in reasons)
    assert [row for row in measure.differences(plant) if row["measurement"] == "latency"] == []


def test_the_report_prints_the_resolution_beside_every_lag(truth):
    page = lab_report._latency(
        measure.latency(CARD, _watched(showing_from=140.0), truth, MAPPING, speed=20.0))
    assert "How long after the line did the screen say it?" in page
    assert "+40.0 s" in page
    assert "a lag smaller than this is quantisation" in page
    assert "carries nothing about what any machine is doing" in page


# -------------------------------------------------------------- the plan


def _pack(directory):
    directory.mkdir(parents=True)
    (directory / "plant.toml").write_text(
        '[pack]\nformat = 1\n\n[plant]\nname = "tiny"\ntimezone = "UTC"\n\n'
        '[files]\ntag_map = "tag_map.json"\nreplay_dir = "out"\nmasterdata = "md"\n',
        encoding="utf-8")
    (directory / "tag_map.json").write_text("{}", encoding="utf-8")
    (directory / "md").mkdir()
    (directory / "line.json").write_text(json.dumps(LINE), encoding="utf-8")
    (directory / "out").mkdir()


def test_a_plan_may_ask_for_latency_now_and_is_not_refused_by_name(tmp_path):
    _pack(tmp_path / "tiny")
    path = tmp_path / "plan.toml"
    path.write_text('packs = ["tiny"]\nmeasure = ["latency"]\n', encoding="utf-8")
    assert read_plan(path).measure == ("latency",)


def test_a_plan_chooses_how_often_the_run_looks(tmp_path):
    _pack(tmp_path / "tiny")
    path = tmp_path / "plan.toml"
    path.write_text('packs = ["tiny"]\nmeasure = ["latency"]\n\n[watch]\nevery = 0.25\n',
                    encoding="utf-8")
    assert read_plan(path).watch_every_s == 0.25


def test_a_watch_too_slow_for_the_speed_it_asks_for_is_refused_with_the_arithmetic(tmp_path):
    """Every lag would read *within resolution* and the measurement would say
    nothing. Refusing beats running for six minutes to produce a page of
    shrugs."""
    _pack(tmp_path / "tiny")
    path = tmp_path / "plan.toml"
    path.write_text('packs = ["tiny"]\nmeasure = ["latency"]\nspeed = 60\n\n'
                    '[watch]\nevery = 5\n', encoding="utf-8")
    with pytest.raises(PlanError) as raised:
        read_plan(path)
    assert "300s of line time" in str(raised.value)
    assert "Look more often, or replay more slowly" in str(raised.value)


def test_a_slow_watch_is_fine_when_nobody_asked_for_latency(tmp_path):
    _pack(tmp_path / "tiny")
    path = tmp_path / "plan.toml"
    path.write_text('packs = ["tiny"]\nmeasure = ["booking"]\nspeed = 60\n\n'
                    '[watch]\nevery = 5\n', encoding="utf-8")
    assert read_plan(path).watch_every_s == 5.0


def test_a_setting_the_watch_table_does_not_have_is_refused_by_name(tmp_path):
    _pack(tmp_path / "tiny")
    path = tmp_path / "plan.toml"
    path.write_text('packs = ["tiny"]\n\n[watch]\nforever = true\n', encoding="utf-8")
    with pytest.raises(PlanError) as raised:
        read_plan(path)
    assert "forever" in str(raised.value)
