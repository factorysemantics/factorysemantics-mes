"""The scorer's own tests.

A scorer nobody checks is just a second thing that can be wrong, and this one
already shipped two bugs worth remembering: it stamped a naive *local* time
against a MES that records naive UTC, and it trusted a replay whose clock
drifted 8%. Both showed up as "the MES missed the breakdown" - a confident,
false accusation. The cases below pin the behaviour that makes such an
accusation trustworthy.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from fsmes.sim.score import score_run
from fsmes.sim.truth import load_truth

LAB = Path("labs/multiplant/machining")
T0 = datetime(2026, 1, 1, 12, 0, 0)
SPEED = 60.0


@pytest.fixture
def truth():
    return load_truth(LAB / "line.json", LAB / "tag_map.json")


def _at(sim_seconds: float, speed: float = SPEED) -> str:
    return (T0 + timedelta(seconds=sim_seconds / speed)).isoformat()


def _timeline(intervals, start_s=0, end_s=3600, speed: float = SPEED):
    """A fake MES timeline, in the shape /analysis/timeline returns."""
    machines = {}
    for equipment, state, a, b in intervals:
        machines.setdefault(equipment, []).append(
            {"state": state, "reason": None,
             "start": _at(a, speed), "end": _at(b, speed),
             "seconds": (b - a) / speed, "open": False}
        )
    return {
        "window": {"start": _at(start_s, speed), "end": _at(end_s, speed)},
        "machines": [{"code": code, "name": code, "intervals": ivs}
                     for code, ivs in machines.items()],
    }


def test_truth_reads_the_scripted_events(truth):
    kinds = sorted(e.type for e in truth["events"])
    assert kinds == ["changeover", "down", "drift", "micro_stops"]
    assert truth["duration_s"] == 3600
    assert truth["seed"] == 7


def test_station_names_map_to_equipment_codes(truth):
    fault = next(e for e in truth["events"] if e.is_fault)
    # line.json says "Mill"; the MES calls it MILL01. Guessing that mapping
    # would score the wrong machine and never say so.
    assert fault.station == "Mill"
    assert fault.equipment == "MILL01"


def test_changeover_is_planned_and_a_fault_is_not(truth):
    changeover = next(e for e in truth["events"] if e.type == "changeover")
    fault = next(e for e in truth["events"] if e.is_fault)
    assert changeover.planned and not changeover.is_fault
    assert fault.is_fault and not fault.planned


def test_a_detected_breakdown_scores_full_recall(truth):
    card = score_run(truth, _timeline([("MILL01", "down", 2700, 2880)]), T0, SPEED)
    assert card["metrics"]["breakdown_recall"] == 1.0
    assert card["faults"][0]["detected"] is True


def test_a_missed_breakdown_scores_zero(truth):
    # The MES watched the window and saw the machine running through it.
    card = score_run(truth, _timeline([("MILL01", "running", 2700, 2880)]), T0, SPEED)
    assert card["metrics"]["breakdown_recall"] == 0.0
    assert card["faults"][0]["detected"] is False


def test_calling_a_changeover_downtime_is_caught(truth):
    # 1500-1740 is the planned tool change. Booking it as downtime destroys
    # the plant's availability figure, which is the whole reason to check.
    card = score_run(truth, _timeline([("SAW01", "down", 1500, 1740)]), T0, SPEED)
    assert card["metrics"]["planned_stop_misclassified"] == 1
    assert card["planned_stops"][0]["offenders"][0]["equipment"] == "SAW01"


def test_a_changeover_recorded_as_setup_is_not_a_misclassification(truth):
    card = score_run(truth, _timeline([("SAW01", "setup", 1500, 1740)]), T0, SPEED)
    assert card["metrics"]["planned_stop_misclassified"] == 0


def test_an_unobserved_window_scores_unknown_not_zero(truth):
    # Principle 4 applies to the scorer too: the MES stopped watching before
    # the breakdown, so the honest answer is "unknown", not "missed it".
    card = score_run(truth, _timeline([("MILL01", "running", 0, 600)], end_s=600),
                     T0, SPEED)
    assert card["faults"][0]["detected"] is None
    assert card["metrics"]["breakdown_recall"] is None
    assert card["metrics"]["faults_scored"] == 0


def test_a_machine_the_timeline_does_not_carry_scores_unknown_not_missed(truth):
    # The timeline is scoped (one line, a screenful of machines). A fault on
    # a machine it left out is one the scorer never asked about - four of a
    # factory's five breakdowns scored "missed" this way before it checked.
    card = score_run(truth, _timeline([("SAW01", "running", 0, 3600)]), T0, SPEED)
    mill = next(f for f in card["faults"] if f["equipment"] == "MILL01")
    assert mill["observed"] is False and mill["detected"] is None
    assert card["metrics"]["breakdown_recall"] is None


def test_the_runner_asks_for_the_machines_the_script_names_in_pages(monkeypatch):
    """A factory has more lines than the timeline endpoint shows unasked, and
    more machines than it draws per request."""
    from fsmes.sim import runner

    asked = []

    def fake_get(base, path, token):
        asked.append(path)
        codes = path.split("equipment=")[1].split(",")
        return {"line": {"code": f"L{len(asked)}", "name": ""},
                "window": {"start": f"2026-01-01T12:0{len(asked)}:00", "end": "2026-01-01T13:00:00"},
                "machines": [{"code": c, "name": c, "intervals": []} for c in codes]}

    monkeypatch.setattr(runner, "_get", fake_get)
    codes = [f"M{i:03d}" for i in range(130)]
    merged = runner._timeline_for("http://x", "t", 1.0, codes)
    assert len(asked) == 3 and all("limit=60" in p for p in asked)
    assert [m["code"] for m in merged["machines"]] == codes
    assert merged["window"]["start"] == "2026-01-01T12:01:00", "the earliest page's start"
    assert merged["machines_shown"] == merged["machines_total"] == 130


def test_the_pipeline_report_reads_what_the_components_said(tmp_path):
    """Whether the harness itself kept up, from the replay's and the agent's
    own structured logs. Missing logs are unknown, not fine."""
    import json

    from fsmes.sim.runner import pipeline_report

    assert pipeline_report(tmp_path, 0.5)["sustained"] is None

    (tmp_path / "opc-replay.jsonl").write_text(
        json.dumps({"event": "replay online"}) + "\n" +
        json.dumps({"event": "replay cannot sustain the requested speed",
                    "behind_seconds": 0.1, "worst_seconds": 0.3}) + "\n", encoding="utf-8")
    (tmp_path / "opc-agent.jsonl").write_text(
        json.dumps({"event": "agent ingestion", "readings": 600, "batches": 14,
                    "backlog_peak": 165, "lag_max_s": 0.034, "busy": 0.08}) + "\n" +
        "not json\n" +
        json.dumps({"event": "agent ingestion", "readings": 700, "batches": 15,
                    "backlog_peak": 110, "lag_max_s": 0.021, "busy": 0.06}) + "\n", encoding="utf-8")
    report = pipeline_report(tmp_path, 0.5, speed=60.0, wall_s=60.0)
    assert report["replay_max_behind_s"] == 0.3 and report["agent_max_lag_s"] == 0.034
    assert report["agent_max_backlog"] == 165 and report["agent_readings"] == 1300
    assert report["sustained"] is True
    assert report["replay_sustainable_speed"] == 59.7, "what the replay actually achieved"

    assert pipeline_report(tmp_path, 0.02)["sustained"] is False, "behind by more than one sample"

    # The agent's log gone: the replay alone cannot vouch for the pipeline,
    # but a replay known to be behind still condemns it.
    (tmp_path / "opc-agent.jsonl").unlink()
    assert pipeline_report(tmp_path, 0.5)["sustained"] is None
    assert pipeline_report(tmp_path, 0.02)["sustained"] is False


def test_a_run_whose_pipeline_fell_behind_withholds_its_verdict(truth):
    """An instrument out of calibration keeps its readings and loses its
    verdict - never a misleading number in the trend."""
    from fsmes.sim.runner import withhold_verdict

    card = score_run(truth, _timeline([("MILL01", "running", 2700, 2880)]), T0, SPEED)
    assert card["metrics"]["breakdown_recall"] == 0.0
    withhold_verdict(card, "the harness fell behind")
    assert card["metrics"]["breakdown_recall"] is None
    assert card["metrics"]["planned_stop_misclassified"] is None
    assert card["metrics"]["faults_scored"] == 0
    assert card["faults"][0]["detected"] is None
    assert card["faults"][0]["unknown_because"] == "the harness fell behind"
    assert card["faults"][0]["mes_recorded_down"] is not None, "the evidence stays"
    assert card["verdict_withheld"]


@pytest.mark.asyncio
async def test_a_late_replay_tick_still_yields_to_the_server():
    """Writing a node is pure memory work. A replay that is behind and never
    sleeps starves the OPC server sharing its loop: at 150x with 27
    stations it stopped publishing, the subscriptions timed out, and the
    agent received nothing for the rest of the hour."""
    import asyncio

    from fsmes.integrations.opc.csv_replay import _wait_for_tick

    loop = asyncio.get_running_loop()
    served = asyncio.Event()
    asyncio.get_running_loop().call_soon(served.set)   # the server's turn, if it gets one
    behind = await _wait_for_tick(started=loop.time() - 10.0, tick=1, period=1.0)
    assert behind > 8.0, "reports how late it is"
    assert served.is_set(), "and gave the loop a turn before writing on"

    on_time = await _wait_for_tick(started=loop.time(), tick=1, period=0.01)
    assert on_time == 0.0


@pytest.mark.parametrize("speed", [1.0, 10.0, 60.0])
def test_speed_changes_wall_clock_but_not_the_verdict(truth, speed):
    """The same plant behaviour must score the same at any replay speed.

    Speed is the one knob a sweep turns freely, so a verdict that depends on
    it would make every swept result incomparable.
    """
    timeline = _timeline([("MILL01", "down", 2700, 2880)], speed=speed)
    card = score_run(truth, timeline, T0, speed)
    assert card["metrics"]["breakdown_recall"] == 1.0
    assert card["metrics"]["planned_stop_misclassified"] == 0


# --------------------------------------------------------------------------
# Observation resolution
#
# The third false accusation this scorer nearly made. At 60x a 12-second jam
# lasts 200 ms of wall clock; sampled every 500 ms the MES cannot see it, and
# calling that a miss blames the plant for the harness's blind spot.
# --------------------------------------------------------------------------

def test_an_event_too_brief_to_sample_scores_unknown_not_missed(truth):
    # 180 line-seconds at 60x with 500 ms sampling = 6 samples' worth of
    # resolution per 30 line-seconds; ask for a speed where it cannot resolve.
    card = score_run(truth, _timeline([("MILL01", "running", 2700, 2880)]),
                     T0, speed=600.0, observe_interval_s=0.5)
    fault = card["faults"][0]
    assert fault["resolvable_at_this_speed"] is False
    assert fault["detected"] is None
    assert card["metrics"]["breakdown_recall"] is None
    assert "MILL01" in card["observation"]["unresolvable_at_this_speed"]


def test_the_same_event_is_scored_when_sampling_keeps_up(truth):
    # Same speed, faster sampling: now it resolves and a real miss is a miss.
    card = score_run(truth, _timeline([("MILL01", "running", 2700, 2880)]),
                     T0, speed=600.0, observe_interval_s=0.05)
    fault = card["faults"][0]
    assert fault["resolvable_at_this_speed"] is True
    assert fault["detected"] is False
    assert card["metrics"]["breakdown_recall"] == 0.0


def test_the_scorecard_states_the_speed_that_would_resolve_everything(truth):
    card = score_run(truth, _timeline([("MILL01", "down", 2700, 2880)]),
                     T0, SPEED, observe_interval_s=0.5)
    # Shortest scripted window on Northgate is the 180s breakdown; two samples
    # of 0.5 s must fit inside it, so 180 / (2 * 0.5) = 180x.
    assert card["observation"]["max_speed_for_full_resolution"] == 180.0


def test_detection_lag_is_reported_in_line_time(truth):
    # The MES saw it, but 60 line-seconds late. That is a number worth
    # trending: a plant that notices faults slower has regressed even if it
    # still notices them all.
    card = score_run(truth, _timeline([("MILL01", "down", 2760, 2880)]), T0, SPEED)
    assert card["faults"][0]["detected"] is True
    assert card["faults"][0]["lag_sim_seconds"] == 60.0


def test_runner_picks_a_sampling_interval_the_script_can_be_seen_through(truth):
    from fsmes.sim.runner import DEFAULT_PUBLISH_MS, MIN_PUBLISH_MS, publish_interval_ms

    # Northgate's briefest window is the 180 s breakdown.
    assert publish_interval_ms(truth, speed=1.0) == DEFAULT_PUBLISH_MS   # no need to hurry
    assert publish_interval_ms(truth, speed=60.0) == DEFAULT_PUBLISH_MS  # 1.5 s still fine
    # Fast enough that 500 ms would blind it, so the interval tightens...
    assert publish_interval_ms(truth, speed=600.0) == 150
    # ...but never below what an agent can usefully sustain.
    assert publish_interval_ms(truth, speed=100000.0) == MIN_PUBLISH_MS


def test_bottling_is_now_describable_as_data():
    """The whole point of Phase 2's first move.

    Bottling could not be scored while its line lived in a hardcoded
    generator: a scorer needs ground truth it can read, and a scripted event
    buried in a loop body is not readable.
    """
    from pathlib import Path
    bottling = load_truth(Path("labs/kepsim/line.json"),
                          Path("config/tag_map_kepsim.json"))
    kinds = sorted({e.type for e in bottling["events"]})
    assert kinds == ["changeover", "counter_reset", "down", "drift",
                     "micro_stops", "scrap_burst"]
    # The precursor names the analog it moves, because RD has two and the
    # healthy one must not be credited as the warning.
    drift = next(e for e in bottling["events"] if e.type == "drift")
    assert drift.equipment == "RD01"
    assert drift.detail["analog"] == "MotorTemp"


def test_a_station_can_carry_more_than_one_analog():
    """RD exposes PressForce and MotorTemp; only one of them warns."""
    import json

    from fsmes.sim.generate import station_analogs

    line = json.loads(Path("labs/kepsim/line.json").read_text(encoding="utf-8"))
    rd = next(s for s in line["stations"] if s["name"] == "RD")
    names = [a["name"] for a in station_analogs(rd)]
    assert names[:2] == ["PressForce", "MotorTemp"]
    assert len(names) >= 3, "a real machine publishes more than its headline signal"
    # A single-analog station still works without the plural form.
    assert station_analogs({"name": "X", "analog": {"name": "Only"}})[0]["name"] == "Only"
    assert station_analogs({"name": "X"})[0]["name"] == "Value"


def test_an_ephemeral_run_binds_loopback_whatever_the_plant_does():
    """A persistent lab plant may sit on the tailnet so it can be opened from
    a phone. A run that lives for sixty seconds must not inherit that - the
    runner dials loopback, and once did so against an API listening elsewhere.
    """
    from pathlib import Path

    from fsmes import plant as plants

    cfg = {"api_host": "192.0.2.10", "api_port": 8010, "opc_port": 4841,
           "tag_map": "config/tag_map_kepsim.json", "replay_dir": "labs/kepsim/out"}
    env = plants.plant_env("bottling", cfg, Path("."))
    assert env["MES_API_HOST"] == "192.0.2.10", "the plant itself keeps its bind"


def test_a_fault_recorded_after_its_window_is_withheld_not_scored_missed(truth):
    """Two of seventeen identical bottling runs (2026-09-02) scored recall 0
    with the agent six to nine minutes of line time behind: the faults were
    recorded, late. Behind is not wrong; the check is withheld and named."""
    late = _timeline([("MILL01", "running", 2700, 2880), ("MILL01", "down", 3100, 3280)])
    card = score_run(truth, late, T0, SPEED)
    fault = card["faults"][0]
    assert fault["recorded_late"] is True and fault["detected"] is None and fault["recall"] is None
    assert fault["lag_sim_seconds"] == 400.0
    assert card["metrics"]["breakdown_recall"] is None
    assert card["metrics"]["faults_recorded_late"] == 1 and card["metrics"]["faults_scored"] == 0
    assert card["late_faults"] == [{"equipment": "MILL01", "lag_sim_seconds": 400.0}]
    # A down recorded a little late, overlapping the window, is still a detection.
    card = score_run(truth, _timeline([("MILL01", "down", 2760, 2880)]), T0, SPEED)
    assert card["faults"][0]["recorded_late"] is False and card["faults"][0]["detected"] is True


def test_the_agents_first_minute_is_startup_and_does_not_void_the_hour(tmp_path):
    """Nine hundred tags answer a fresh subscription at once and the agent
    drains the burst in seconds. A lag inside that minute is stated beside
    the verdict, not allowed to withhold it; a lag after it still does."""
    import json

    from fsmes.sim.runner import AGENT_WARMUP_S, pipeline_report

    def report(seconds, lag):
        return json.dumps({"event": "agent ingestion", "readings": 500, "batches": 5, "backlog_peak": 300,
                           "lag_max_s": lag, "busy": 0.1,
                           "timestamp": f"2026-09-06T21:43:{seconds:02d}.000000Z" if seconds < 60
                           else f"2026-09-06T21:44:{seconds - 60:02d}.000000Z"})

    (tmp_path / "opc-replay.jsonl").write_text(json.dumps({"event": "replay online"}) + "\n", encoding="utf-8")
    (tmp_path / "opc-agent.jsonl").write_text(
        "\n".join([report(5, 0.2), report(58, 0.529), report(int(AGENT_WARMUP_S) + 5, 0.21), report(90, 0.15)]) + "\n",
        encoding="utf-8")
    r = pipeline_report(tmp_path, 0.5)
    assert r["sustained"] is True
    assert r["agent_max_lag_s"] == 0.21 and r["agent_max_lag_s_including_startup"] == 0.529
    assert r["agent_warmup_s"] == AGENT_WARMUP_S

    (tmp_path / "opc-agent.jsonl").write_text(
        "\n".join([report(5, 0.2), report(int(AGENT_WARMUP_S) + 5, 0.61)]) + "\n", encoding="utf-8")
    assert pipeline_report(tmp_path, 0.5)["sustained"] is False, "a lag after the warm-up still condemns it"


def test_a_components_log_is_read_with_its_rotated_files_oldest_first(tmp_path):
    """A busy hour rotates the agent's log; the first file holds the start
    and the report must not read the newest alone."""
    import json

    from fsmes.sim.runner import _json_lines, log_files

    (tmp_path / "opc-agent.jsonl.2").write_text(json.dumps({"n": 1}) + "\n", encoding="utf-8")
    (tmp_path / "opc-agent.jsonl.1").write_text(json.dumps({"n": 2}) + "\n", encoding="utf-8")
    (tmp_path / "opc-agent.jsonl").write_text(json.dumps({"n": 3}), encoding="utf-8")
    names = [p.name for p in log_files(tmp_path / "opc-agent.jsonl")]
    assert names == ["opc-agent.jsonl.2", "opc-agent.jsonl.1", "opc-agent.jsonl"]
    assert [r["n"] for r in _json_lines(tmp_path / "opc-agent.jsonl")] == [1, 2, 3]
