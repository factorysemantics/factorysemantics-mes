"""One typed judgment beside the agent evals' own check, and mostly the ways
it is not asked.

The check beside it is a rule about tokens, and it is the one the trend is
drawn from. The judgment is a second opinion recorded next to it: a
probability, the model version that answered, and the time. It gates
nothing (decision 0031), it has no threshold, and it is off unless somebody
sets a key - which is the state every installation of this package is in,
and the state the whole of this suite runs in except where a test hands in
a service of its own.

No network is opened here and no SDK is installed. Every asked path goes
through a transport a test wrote.
"""

import pytest

from fsmes.config import Settings, get_settings
from fsmes.services import auth
from fsmes.sim import agent_eval

QUESTION = "On plant cutlery, which machines currently have any alarm bit set?"


def with_a_key(**overrides) -> Settings:
    """Settings that would let a judgment be asked. The key is a test's own
    string; nothing here reads a real one and nothing here can."""
    return Settings(jev_api_key="test-key-not-a-real-one", **overrides)


class Service:
    """A judgment service that answers from a script and remembers what it
    was asked."""

    def __init__(self, probability: float = 0.93, model: str = "jev-1.12",
                 request_id: str = "req-0007", raises: Exception | None = None,
                 answer_nothing: bool = False):
        self.probability = probability
        self.model = model
        self.request_id = request_id
        self.raises = raises
        self.answer_nothing = answer_nothing
        self.asked: list[dict] = []

    def ask(self, *, state, questions, model):
        self.asked.append({"state": state, "questions": questions, "model": model})
        if self.raises is not None:
            raise self.raises
        answers = {} if self.answer_nothing else {
            q["name"]: {"probability": self.probability, "level": None,
                        "confidence": 0.77, "probabilities": None}
            for q in questions}
        return {"model": self.model, "request_id": self.request_id, "answers": answers}


@pytest.fixture()
def api(make_client, session):
    auth.create_user(session, code="AGENT", name="Plant Agent",
                     password="agent-lab-only", role="agent")
    session.flush()
    return agent_eval.Api(make_client())


# ------------------------------------------------- the normal case: no key

def test_with_no_key_nothing_is_asked_and_the_record_says_which(monkeypatch):
    """"Not asked" and "the model thought the answer was wrong" are two
    different facts, and an eval store that cannot tell them apart is the
    thing this change exists to avoid."""
    monkeypatch.delenv("MES_JEV_API_KEY", raising=False)
    get_settings.cache_clear()
    record = agent_eval.judge(QUESTION, {"MIX01"}, {"FILL01"}, "MIX01",
                              settings=Settings())
    assert record["asked"] is False
    assert record["note"] == "not asked (no MES_JEV_API_KEY in this environment)"
    assert agent_eval.probability_of({"jev": record}) is None
    get_settings.cache_clear()


def test_with_no_key_a_whole_run_scores_exactly_as_it_did_before(api):
    """The deterministic score, the store and the pass rate are untouched by
    a judgment that was never asked."""
    rows = agent_eval.run("testplant", api=api, agent=lambda prompt: "NONE",
                          agent_name="scripted", store=None, echo=lambda s: None,
                          settings=Settings())
    assert len(rows) == len(agent_eval.SCENARIOS)
    assert all(r["jev"]["asked"] is False for r in rows)
    assert all("no MES_JEV_API_KEY" in r["jev"]["note"] for r in rows)
    s = agent_eval.summary(rows)
    assert s["judgment"]["asked"] == 0 and s["judgment"]["of"] == len(rows)
    assert s["judgment"]["mean_probability"] is None
    assert s["pass_rate"] is not None


def test_shadow_mode_refuses_the_question_before_it_is_asked():
    """A plant lending us its data to watch did not agree to a hosted call,
    and in shadow mode there is no client to make one with."""
    record = agent_eval.judge(QUESTION, {"MIX01"}, {"FILL01"}, "MIX01",
                              settings=with_a_key(shadow=True))
    assert record["asked"] is False and "shadow mode" in record["note"]


def test_an_empty_reply_is_not_worth_asking_about():
    """The check beside it has already scored a silent agent zero, and there
    is nothing for a judgment to read."""
    service = Service()
    record = agent_eval.judge(QUESTION, {"MIX01"}, {"FILL01"}, "   ",
                              transport=service, settings=with_a_key())
    assert record["asked"] is False
    assert record["note"] == "not asked (the agent replied with nothing)"
    assert service.asked == []


# ------------------------------------------------------------ asked, and kept

def test_the_judgment_is_recorded_with_its_probability_version_and_time():
    record = agent_eval.judge(QUESTION, {"MIX01"}, {"FILL01"},
                              "MIX01 is the one alarming.",
                              transport=Service(probability=0.93),
                              settings=with_a_key())
    assert record["asked"] is True
    answer = record["answer"]
    assert answer["probability"] == 0.93
    assert answer["confidence"] == 0.77
    assert record["model"] == "jev-1.12" and record["model_asked_for"] == "jev-1.12"
    assert answer["model"] == "jev-1.12" and answer["request_id"] == "req-0007"
    assert answer["asked_at"].startswith("20") and answer["question_sha256"]
    assert record["thresholds"].startswith("none")
    assert record["state_class"] == "observation"


def test_one_question_is_asked_and_the_battery_says_so():
    """A fixed battery's total is the measure of what it cannot see, so it is
    stated rather than counted by a reader."""
    service = Service()
    agent_eval.judge(QUESTION, {"MIX01"}, {"FILL01"}, "MIX01",
                     transport=service, settings=with_a_key())
    assert len(agent_eval.JEV_QUESTIONS.nouls) == 1
    assert agent_eval.JEV_QUESTIONS.score is None
    asked = service.asked[0]["questions"]
    assert [q["name"] for q in asked] == ["answered_exactly"]
    assert asked[0]["kind"] == "noul"


def test_what_is_sent_is_the_question_both_code_lists_and_the_reply():
    """Nothing else about the plant leaves the box, and each list states its
    own total."""
    state = agent_eval.state_of(QUESTION, {"MIX01", "PAL01"}, {"FILL01"},
                                "MIX01 and PAL01")
    assert QUESTION in state
    assert "2 code(s) in total: MIX01, PAL01" in state
    assert "1 code(s) in total, none of which may be named: FILL01" in state
    assert state.rstrip().endswith("MIX01 and PAL01")


def test_a_service_that_will_not_answer_is_recorded_as_not_asked():
    """A judgment nobody could get is not a finding, and it is not a crash in
    the middle of an eval run either."""
    for failure in (RuntimeError("the judgment service is down"),
                    OSError("connection refused"),
                    ValueError("unknown question kind")):
        record = agent_eval.judge(QUESTION, {"MIX01"}, {"FILL01"}, "MIX01",
                                  transport=Service(raises=failure),
                                  settings=with_a_key())
        assert record["asked"] is False and str(failure) in record["note"]


def test_an_answer_that_never_came_back_is_not_read_as_a_no():
    record = agent_eval.judge(QUESTION, {"MIX01"}, {"FILL01"}, "MIX01",
                              transport=Service(answer_nothing=True),
                              settings=with_a_key())
    assert record["asked"] is False and "answered_exactly" in record["note"]


def test_a_moving_model_version_is_refused_before_a_question_is_asked():
    """A judgment stored against `latest` cannot be reproduced next month."""
    service = Service()
    record = agent_eval.judge(QUESTION, {"MIX01"}, {"FILL01"}, "MIX01",
                              transport=service,
                              settings=with_a_key(jev_model="jev-latest"))
    assert record["asked"] is False and "moving version" in record["note"]
    assert service.asked == []


# ------------------------------------------- beside, and never in place of

def test_the_trend_stays_on_the_check_and_the_judgment_is_drawn_beside_it(api):
    """The judgment disagrees with every answer in this run, and changes no
    number the report draws its trend from."""
    service = Service(probability=0.01)
    rows = agent_eval.run("testplant", api=api, agent=lambda prompt: "MIX01",
                          agent_name="scripted", store=None, echo=lambda s: None,
                          jev_transport=service, settings=with_a_key())
    deterministic = agent_eval.summary(
        [{k: v for k, v in r.items() if k != "jev"} for r in rows])
    both = agent_eval.summary(rows)
    assert both["pass_rate"] == deterministic["pass_rate"]
    assert both["by_scenario"] == deterministic["by_scenario"]
    assert both["judgment"]["asked"] == len(rows)
    assert both["judgment"]["mean_probability"] == 0.01
    assert both["judgment"]["models"] == ["jev-1.12"]
    assert deterministic["judgment"]["asked"] == 0


def test_the_report_keeps_the_two_means_apart_so_a_plot_can_be_drawn_later():
    """There is no threshold, so there is no boolean to agree or disagree
    with. What is kept instead is the mean where the check passed and the
    mean where it failed, which is the beginning of the calibration plot a
    threshold would need."""
    def row(passed: bool, probability: float | None, asked: bool = True) -> dict:
        jev = ({"asked": True, "model": "jev-1.12",
                "answer": {"probability": probability}}
               if asked else {"asked": False, "note": "not asked (no key)"})
        return {"scenario": "alarming", "agent": "scripted", "pass": passed, "jev": jev}

    s = agent_eval.summary([row(True, 0.9), row(True, 0.8), row(False, 0.2),
                            row(False, None, asked=False)])
    j = s["judgment"]
    assert j["asked"] == 3 and j["of"] == 4
    assert j["mean_probability_where_the_check_passed"] == 0.85
    assert j["mean_probability_where_the_check_failed"] == 0.2
    assert s["pass_rate"] == 0.5


def test_a_run_keeps_the_judgment_in_the_same_row_as_the_score(api, tmp_path):
    """One row per answer, read back from the store with both opinions on it."""
    store = tmp_path / "evals.jsonl"
    agent_eval.run("testplant", api=api, agent=lambda prompt: "MIX01",
                   scenarios=[agent_eval.scenario("alarming")],
                   agent_name="scripted", store=store, echo=lambda s: None,
                   jev_transport=Service(probability=0.42), settings=with_a_key())
    kept = agent_eval.recent(store=store)
    assert len(kept) == 1
    assert kept[0]["jev"]["asked"] is True
    assert agent_eval.probability_of(kept[0]) == 0.42
    assert "pass" in kept[0] and "score" in kept[0]


def test_the_key_reaches_no_record_no_log_and_no_repr(api, caplog, tmp_path):
    """The one string this file must never emit."""
    secret = "jev-key-that-must-not-appear-anywhere"
    settings = Settings(jev_api_key=secret)
    store = tmp_path / "evals.jsonl"
    with caplog.at_level("DEBUG"):
        rows = agent_eval.run("testplant", api=api, agent=lambda prompt: "MIX01",
                              scenarios=[agent_eval.scenario("alarming")],
                              agent_name="scripted", store=store,
                              echo=lambda s: None,
                              jev_transport=Service(), settings=settings)
    written = store.read_text(encoding="utf-8")
    assert secret not in written
    assert secret not in repr(rows)
    assert secret not in caplog.text
    assert secret not in repr(agent_eval.summary(rows))


def test_a_machine_named_in_prose_is_scored_the_same_whoever_is_asked(api):
    """The defect the judgment sits beside is fixed, so the two now start
    from the same reading of the same reply."""
    unit = api.get("/equipment/tags")["machines"][0]["code"]
    judged = agent_eval.score(f"{unit}. Nothing else is alarming.",
                              {unit}, {"FILL01"})
    assert judged["pass"] is True and judged["wrong"] == []
