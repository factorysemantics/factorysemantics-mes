"""Agent evals: the questions, their truths, and the scoring.

The scorecard measures whether the MES told the truth; this measures whether
an agent can find it through the tools. These tests prove the machinery
without a model in the loop: truths come from the API, a scripted agent's
answers are scored honestly, and a failed agent is a scored zero rather
than a crash.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from fsmes.db import utcnow
from fsmes.domain import Equipment, TagValue
from fsmes.services import auth
from fsmes.sim import agent_eval


@pytest.fixture()
def api(make_client, session):
    auth.create_user(session, code="AGENT", name="Plant Agent", password="agent-lab-only", role="agent")
    session.flush()
    return agent_eval.Api(make_client())


def test_scoring_is_strict_about_wrong_codes_and_honest_about_none():
    assert agent_eval.score("MIX01", {"MIX01"}, {"FILL01"})["pass"] is True
    assert agent_eval.score("the answer is mix01.", {"MIX01"}, {"FILL01"})["pass"] is True
    judged = agent_eval.score("MIX01, FILL01", {"MIX01"}, {"FILL01"})
    assert judged["pass"] is False and judged["wrong"] == ["FILL01"] and judged["score"] == 0.0
    assert agent_eval.score("MIX01", {"MIX01", "PAL01"}, set())["score"] == 0.5
    assert agent_eval.score("NONE", {"NONE"}, {"MIX01"})["pass"] is True
    assert agent_eval.score("MIX01", {"NONE"}, {"MIX01"})["pass"] is False
    assert agent_eval.score("", {"MIX01"}, set())["pass"] is False


def test_truths_come_from_the_api(api, session):
    unit = session.scalar(select(Equipment).where(Equipment.code == "MIX01"))
    session.add(TagValue(equipment_id=unit.id, tag="MIX01.AlarmWord", value_num=1, ts=utcnow()))
    session.flush()
    assert agent_eval.scenario("alarming").truth(api) == {"MIX01"}
    assert "MIX01" not in agent_eval.scenario("alarming").distractors(api)
    assert agent_eval.scenario("due_maintenance").truth(api) == {"NONE"}
    quiet = agent_eval.scenario("quiet").truth(api)
    assert quiet == {"NONE"} or all(isinstance(q, str) for q in quiet)
    with pytest.raises(KeyError):
        agent_eval.scenario("nope")


def test_a_scripted_agent_is_scored_and_kept(api, session, tmp_path):
    unit = session.scalar(select(Equipment).where(Equipment.code == "MIX01"))
    session.add(TagValue(equipment_id=unit.id, tag="MIX01.AlarmWord", value_num=8,
                         ts=utcnow() - timedelta(seconds=1)))
    session.flush()
    answers = {"alarming": "MIX01", "due_maintenance": "NONE"}

    def scripted(prompt: str) -> str:
        for key, reply in answers.items():
            if key == "alarming" and "alarm bit" in prompt:
                return reply
            if key == "due_maintenance" and "maintenance plan" in prompt:
                return reply
        raise RuntimeError("model unavailable")

    store = tmp_path / "evals.jsonl"
    rows = agent_eval.run("testplant", api=api, agent=scripted, agent_name="scripted",
                          store=store, echo=lambda s: None)
    by = {r["scenario"]: r for r in rows}
    assert by["alarming"]["pass"] is True and by["alarming"]["truth"] == ["MIX01"]
    assert by["due_maintenance"]["pass"] is True
    failed = by["most_wip"]
    assert failed["pass"] is False and failed["error"] == "model unavailable"
    kept = agent_eval.recent(store=store)
    assert len(kept) == len(agent_eval.SCENARIOS)
    s = agent_eval.summary(kept)
    assert s["runs"] == len(kept) and 0 < s["pass_rate"] < 1
    assert s["by_scenario"]["alarming"] == 1.0
