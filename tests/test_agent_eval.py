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


def test_a_distractor_named_only_as_an_english_word_is_not_a_wrong_answer():
    """The defect the Jev survey describes, as a test.

    Scoring used to upper-case the answer before matching an upper-case
    character class, which made the class inert and turned every English
    word into a candidate machine code. An agent that named the right
    machine in a sentence that mentions a distractor as a plain word was
    scored zero for a machine it had not named.
    """
    judged = agent_eval.score("DRW01. The drawing area itself is fine.",
                              {"DRW01"}, {"DRAWING", "ANN01"})
    assert judged["pass"] is True
    assert judged["hit"] == ["DRW01"] and judged["wrong"] == []


def test_a_code_that_is_also_a_word_counts_only_when_written_as_a_code():
    assert agent_eval.score("DRAWING", {"DRAWING"}, set())["pass"] is True
    assert agent_eval.score("the drawing looked fine", {"DRAWING"}, set())["pass"] is False
    # A code carrying a digit or a separator cannot be an English word, so
    # it is still read in whatever case the agent wrote it.
    assert agent_eval.score("fg-pack1", {"FG-PACK1"}, set())["pass"] is True


def test_a_code_is_named_only_as_a_whole_token():
    assert agent_eval.score("MIX011", {"MIX01"}, set())["pass"] is False
    judged = agent_eval.score("MIX01-A", {"MIX01"}, {"MIX01-A"})
    assert judged["hit"] == [] and judged["wrong"] == ["MIX01-A"]


def test_none_is_the_answer_vocabulary_and_is_read_in_any_case():
    assert agent_eval.score("none", {"NONE"}, {"MIX01"})["pass"] is True
    assert agent_eval.score("None of the machines are alarming.",
                            {"NONE"}, {"MIX01"})["pass"] is True


# --------------------------------------- what maintenance did, and what waits

@pytest.fixture()
def shift(api, session):
    """A shift with one job finished and one job still waiting to be got at.

    The two halves of the pair: an order a mechanic took, did and wrote up,
    and an order he was given and could not start because the job needs the
    line stopped. Both are real rows in the same list, which is the whole
    difficulty of the question.
    """
    from fsmes.services import dispatch, maintenance, masterdata

    masterdata.create_person(session, code="MT-05", name="Mo Spanner", role="operator")
    done = maintenance.raise_corrective(session, equipment_code="MIX01",
                                        summary="grease the arm")
    dispatch.assign(session, done.code, "MT-05", actor="SUP")
    maintenance.start(session, done.code, actor="MT-05")
    maintenance.complete(session, done.code, actor="MT-05",
                         findings="all eight points took grease",
                         downtime_minutes=0.0)
    waiting = maintenance.raise_corrective(
        session, equipment_code="PACK01", summary="clean the condenser",
        needs_stop=True, window="between_orders")
    dispatch.assign(session, waiting.code, "MT-05", actor="SUP")
    session.flush()
    return {"done": done.code, "waiting": waiting.code}


def test_what_maintenance_did_this_shift_is_the_orders_that_closed_and_who_closed_them(
        api, shift):
    truth = agent_eval.scenario("maintenance_this_shift").truth(api)

    assert truth == {shift["done"], "MT-05"}
    # The order nobody could get at is the wrong answer, and the one worth
    # catching: an agent that reads the list and reports all of it has
    # described the backlog rather than the shift.
    assert agent_eval.scenario("maintenance_this_shift").distractors(api) == {shift["waiting"]}


def test_a_shift_in_which_nothing_was_finished_says_so_rather_than_nothing(api):
    assert agent_eval.scenario("maintenance_this_shift").truth(api) == {"NONE"}


def test_the_job_that_waits_is_answered_with_the_records_own_reason(api, shift):
    """Link 2 asked as a question. `needs_stop` and `window` are quoted from
    the order, because a reader with only the status sees a job somebody
    ignored."""
    truth = agent_eval.scenario("maintenance_waiting").truth(api)

    assert truth == {shift["waiting"], "PACK01", "needs_stop", "between_orders"}
    assert shift["done"] not in truth


def test_a_reason_the_records_do_not_give_is_a_wrong_answer(api, shift):
    """The failure this pair exists to catch: an agent that knows the order is
    waiting and invents why. The two windows this order does not name, and the
    machines with nothing waiting on them, are scored against it."""
    wrong = agent_eval.scenario("maintenance_waiting").distractors(api)

    assert {"anytime", "end_of_shift"} <= wrong
    assert "between_orders" not in wrong and "needs_stop" not in wrong
    assert "MIX01" in wrong, "the machine whose job was done is not what waits"


def test_a_waiting_job_that_needs_no_stop_cannot_be_blamed_on_the_line(api, session):
    """`needs_stop` is a distractor whenever no waiting job carries it, so
    "the line never stopped" cannot be offered as the reason for a job that
    could have been done on a running machine."""
    from fsmes.services import dispatch, maintenance, masterdata

    masterdata.create_person(session, code="MT-06", name="Jo Hands", role="operator")
    order = maintenance.raise_corrective(session, equipment_code="MIX01",
                                         summary="check the belt", window="anytime")
    dispatch.assign(session, order.code, "MT-06", actor="SUP")
    session.flush()

    scenario = agent_eval.scenario("maintenance_waiting")
    assert scenario.truth(api) == {order.code, "MIX01", "anytime"}
    assert "needs_stop" in scenario.distractors(api)


def test_a_plant_where_every_job_handed_out_has_been_started_says_none(api, session):
    assert agent_eval.scenario("maintenance_waiting").truth(api) == {"NONE"}
