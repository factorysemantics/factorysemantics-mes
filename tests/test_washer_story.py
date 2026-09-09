"""The washer story: a hot wash upstream, underweight fills downstream, and
an agent that has to name the cause rather than the symptom.

The generator gains cross-station effects (an event on one station moves an
analog on another), the machine surface gains alarm history (what a machine
raised in the last hour, not only what it is raising now), and the agent
evals gain a causal scenario whose truth the API can still answer.
"""

import json
from datetime import timedelta
from statistics import mean

import pytest
from sqlalchemy import select

from fsmes.db import utcnow
from fsmes.domain import Equipment, TagValue
from fsmes.services import auth, quality, tags
from fsmes.sim import agent_eval, generate

LINE = {
    "seed": 7, "duration_s": 400, "buffers": {"capacity": 20, "initial": 10},
    "stations": [
        {"name": "Washer", "rate_per_min": 115, "scrap_pct": 1.0,
         "analogs": [{"name": "WashTemp", "base": 71.0, "noise": 0.0, "decimals": 1}]},
        {"name": "Refill", "rate_per_min": 105, "scrap_pct": 0.8,
         "analogs": [{"name": "FillWeight", "base": 500.0, "noise": 0.0, "decimals": 1},
                     {"name": "NozzlePressure", "base": 2.6, "noise": 0.0, "decimals": 2}]},
    ],
    "events": [{"type": "scrap_burst", "station": "Washer", "scrap_pct": 10.0, "analog_offset": 3.0,
                "start": 100, "end": 200,
                "effects": [{"station": "Refill", "analog": "FillWeight", "offset": -9.0}]}],
}


def _column(rows, table, name, config):
    header = ["TSec", "State", "GoodCount", "ScrapCount", "TotalCount", "AlarmWord", "CycleTimeMs",
              "RunMinutes", "ReadyBit"]
    station = next(s for s in config["stations"] if s["name"] == table)
    header += [a["name"] for a in generate.station_analogs(station)]
    i = header.index(name)
    return {row[0]: row[i] for row in rows[table]}


def test_an_event_on_one_station_moves_an_analog_on_another():
    rows = generate.simulate(LINE)
    weight = _column(rows, "Refill", "FillWeight", LINE)
    temp = _column(rows, "Washer", "WashTemp", LINE)
    alarms_w = _column(rows, "Washer", "AlarmWord", LINE)
    alarms_r = _column(rows, "Refill", "AlarmWord", LINE)
    inside = [t for t in range(100, 200)]
    outside = [t for t in range(0, 100)] + [t for t in range(200, 400)]
    assert mean(weight[t] for t in inside) == pytest.approx(491.0)
    assert mean(weight[t] for t in outside) == pytest.approx(500.0)
    assert mean(temp[t] for t in inside) == pytest.approx(74.0)
    assert all(alarms_w[t] & 4 for t in inside), "the washer raises the quality-excursion bit"
    assert not any(alarms_r[t] & 4 for t in inside), "the filler is a symptom: it raises nothing"
    pressure = _column(rows, "Refill", "NozzlePressure", LINE)
    assert mean(pressure[t] for t in inside) == pytest.approx(2.6), "only the named analog moves"


def test_an_effect_must_name_a_real_station_and_analog(tmp_path):
    bad = json.loads(json.dumps(LINE))
    bad["events"][0]["effects"][0]["station"] = "Capper"
    path = tmp_path / "line.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SystemExit, match="Capper"):
        generate.load_config(path)
    bad["events"][0]["effects"][0].update(station="Refill", analog="Torque")
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SystemExit, match="Torque"):
        generate.load_config(path)


def test_the_scenario_timeline_tells_the_effect(tmp_path):
    generate.write_scenario(LINE, tmp_path)
    text = (tmp_path / "scenario.md").read_text(encoding="utf-8")
    assert "Refill.FillWeight -9" in text


# ------------------------------------------------------------ alarm history

def _seed_alarm_words(session, code, words):
    unit = session.scalar(select(Equipment).where(Equipment.code == code))
    now = utcnow()
    for i, word in enumerate(words):
        session.add(TagValue(equipment_id=unit.id, tag=f"{code}.AlarmWord", value_num=word,
                             ts=now - timedelta(seconds=(len(words) - i) * 10)))
    session.flush()
    return unit


def test_alarm_history_is_the_changes_not_the_samples(session):
    unit = _seed_alarm_words(session, "MIX01", [0, 0, 4, 4, 4, 0, 8])
    history = tags.alarm_history(session, unit, hours=1)
    assert [h["word"] for h in history] == [0, 4, 0, 8]
    assert all("ts" in h and isinstance(h["active"], list) for h in history)
    assert tags.alarm_history(session, unit, hours=0) == []
    recent = tags.alarm_history(session, unit, hours=0.004)  # 14 s: only the newest sample
    assert [h["word"] for h in recent] == [8]


def test_the_machine_alarms_route_serves_history_on_request(sign_in, session):
    _seed_alarm_words(session, "MIX01", [0, 4, 0])
    client = sign_in("HIST", role="viewer")
    now = client.get("/equipment/MIX01/alarms").json()
    assert now["word"] == 0 and "history" not in now
    back = client.get("/equipment/MIX01/alarms", params={"hours": 1}).json()
    assert [h["word"] for h in back["history"]] == [0, 4, 0] and back["hours"] == 1


# ------------------------------------------------------------- the scenario

@pytest.fixture()
def api(make_client, session):
    auth.create_user(session, code="AGENT", name="Plant Agent", password="agent-lab-only", role="agent")
    session.flush()
    return agent_eval.Api(make_client())


def test_the_causal_scenario_names_the_machine_that_raised_the_excursion(api, session):
    truth = agent_eval.scenario("fill_weight_cause")
    assert truth.truth(api) == {"NONE"}, "no failed fill-weight checks: nothing to explain"
    quality.create_spec(session, material_code="FG-COLA", characteristic="fill_weight", unit="g",
                        min_value=494.0, max_value=506.0)
    quality.record_check(session, material_code="FG-COLA", characteristic="fill_weight", value=491.2)
    session.flush()
    assert truth.truth(api) == {"NONE"}, "a failed check and no excursion anywhere: honest NONE"
    _seed_alarm_words(session, "MIX01", [0, 4, 0])
    assert truth.truth(api) == {"MIX01"}
    assert "MIX01" not in truth.distractors(api) and "PACK01" in truth.distractors(api)
    judged = agent_eval.score("MIX01", truth.truth(api), truth.distractors(api))
    assert judged["pass"] is True
    assert agent_eval.score("PACK01", truth.truth(api), truth.distractors(api))["pass"] is False


def test_the_summary_is_also_grouped_by_agent():
    rows = [{"scenario": "a", "agent": "claude", "pass": True}, {"scenario": "a", "agent": "none", "pass": False},
            {"scenario": "b", "agent": "claude", "pass": False}]
    s = agent_eval.summary(rows)
    assert s["by_agent"] == {"claude": 0.5, "none": 0.0} and s["by_scenario"] == {"a": 0.5, "b": 0.0}
