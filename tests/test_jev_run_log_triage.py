"""A second opinion on a run's log, recorded beside the first and never in
its place.

The pass that has been running since it was written asks a local model an
open question and scrapes JSON out of the reply. It cannot tell a clean run
from a reply it failed to parse. The pass added here asks a hosted judgment
model a fixed battery of typed questions - five conditions and one severity -
where there is nothing to parse, and buys that by being unable to be
surprised.

So both run. These tests are mostly about the ways the second one is *not*
asked, because that is the normal case: no key is what every installation of
this package has, and the whole point of a second opinion is that the first
one is still the one holding the run up.

No network is opened here and no SDK is installed. Every asked path goes
through a transport a test wrote.
"""

import json

import pytest

from fsmes.config import Settings, get_settings
from fsmes.integrations import jev
from fsmes.sim import store, triage

LOG = """
2026-09-17 02:11:04 opc.agent connected to opc.tcp://127.0.0.1:4840
2026-09-17 02:11:09 sim.line counter CUT-01 total=1204
2026-09-17 02:11:14 sim.line counter CUT-01 total=17
2026-09-17 02:11:19 opc.agent subscription dropped, retrying (1)
2026-09-17 02:11:20 opc.agent subscription dropped, retrying (2)
2026-09-17 02:11:21 opc.agent subscription dropped, retrying (3)
""".strip()

CARD = {"plant": "cutlery", "speed": 4.0,
        "metrics": {"breakdown_recall": 1.0, "planned_stop_misclassified": 0}}


def with_a_key(**overrides) -> Settings:
    """Settings that would let a judgment be asked. The key is a test's own
    string; nothing here reads a real one and nothing here can."""
    return Settings(jev_api_key="test-key-not-a-real-one", **overrides)


class Service:
    """A judgment service that answers from a script and remembers what it
    was asked. The interface is one method, which is the whole point of the
    transport being an interface."""

    def __init__(self, answers: dict, model: str = "jev-1.12",
                 request_id: str = "req-0001", raises: Exception | None = None,
                 drop: str | None = None):
        self.answers = answers
        self.model = model
        self.request_id = request_id
        self.raises = raises
        self.drop = drop
        self.asked: list[dict] = []

    def ask(self, *, state, questions, model):
        self.asked.append({"state": state, "questions": questions, "model": model})
        if self.raises is not None:
            raise self.raises
        return {"model": self.model, "request_id": self.request_id,
                "answers": {q["name"]: self.answers[q["name"]] for q in questions
                            if q["name"] != self.drop}}


def a_full_battery(worst="medium") -> dict:
    """One answer per question the battery asks, so a test does not have to
    restate the battery to use it."""
    answers = {n.name: {"probability": 0.12, "level": None, "confidence": None,
                        "probabilities": None}
               for n in triage.JEV_QUESTIONS.nouls}
    answers["retry_storm"] = {"probability": 0.94, "level": None,
                              "confidence": None, "probabilities": None}
    answers["worst_problem"] = {
        "probability": 0.71, "level": worst, "confidence": 0.8,
        "probabilities": {"none": 0.05, "low": 0.14, "medium": 0.71, "high": 0.10}}
    return answers


# ------------------------------------------------- the normal case: no key

def test_with_no_key_nothing_is_asked_and_the_record_says_which(monkeypatch):
    """Not asked and nothing found are two different facts, and the whole
    reason this pass exists is that the pass beside it cannot tell them
    apart. So the no-key case writes a sentence rather than nothing."""
    monkeypatch.delenv("MES_JEV_API_KEY", raising=False)
    get_settings.cache_clear()

    card = dict(CARD, triage={"findings": [], "worst": None})
    record = triage.judge(card, LOG, settings=Settings())

    assert record["asked"] is False
    assert record["note"] == "not asked (no MES_JEV_API_KEY in this environment)"
    # And the run still gets its comparison line, saying one pass ran and the
    # other was not asked - which is a different sentence from the one a run
    # where both agreed gets.
    assert "not asked" in record["comparison"]["line"]
    assert record["comparison"]["agree"] is None
    get_settings.cache_clear()


def test_a_run_that_kept_no_log_is_not_asked_about_either():
    record = triage.judge(CARD, "   \n  ", settings=with_a_key())
    assert record["asked"] is False and "no log kept" in record["note"]


def test_a_service_that_does_not_answer_costs_the_run_nothing():
    """A nightly job must survive the thing it asked being down. The run that
    produced the log has already succeeded by the time this is asked."""
    service = Service({}, raises=OSError("connection refused"))
    record = triage.judge(CARD, LOG, transport=service, settings=with_a_key())
    assert record["asked"] is False and "connection refused" in record["note"]


def test_a_client_that_is_not_installed_reads_as_not_asked_and_says_how():
    """The SDK is an optional extra, so its absence is the ordinary state of
    every installation, not an error anybody has to act on."""
    service = Service({}, raises=RuntimeError(jev.NO_SDK))
    record = triage.judge(CARD, LOG, transport=service, settings=with_a_key())
    assert record["asked"] is False
    assert "factorysemantics-mes[jev]" in record["note"]


# --------------------------------------------------------- the asked path

def test_the_battery_is_asked_about_the_same_log_and_nothing_else():
    """The open pass sends the log tail and a two-metric summary. This one
    sends the log tail. A question that quietly carried more state than the
    pass it is being compared with would not be a comparison."""
    service = Service(a_full_battery())
    triage.judge(CARD, LOG, transport=service, settings=with_a_key())

    assert len(service.asked) == 1
    assert service.asked[0]["state"] == LOG
    asked = [q["name"] for q in service.asked[0]["questions"]]
    assert asked == [n.name for n in triage.JEV_QUESTIONS.nouls] + ["worst_problem"]


def test_every_answer_carries_the_version_that_answered_and_the_words_asked():
    """A judgment stored against no version cannot be read again, and a
    question re-worded is a different question - so the served version and
    the fingerprint of the exact wording are part of every answer."""
    service = Service(a_full_battery(), model="jev-1.12", request_id="req-42")
    record = triage.judge(CARD, LOG, transport=service, settings=with_a_key())

    assert record["asked"] is True
    assert record["model"] == "jev-1.12"
    for answer in record["conditions"] + [record["worst_problem"]]:
        assert answer["model"] == "jev-1.12"
        assert answer["request_id"] == "req-42"
        assert answer["asked_at"]
        wording = triage.JEV_QUESTIONS.text_of(answer["question"])
        assert answer["question_sha256"] == jev.client.question_sha256(wording)


def test_the_version_that_answered_is_recorded_even_when_it_is_not_the_one_pinned():
    """A pinned version can be retired. Storing the version asked for and
    calling it the one that answered would make that invisible."""
    service = Service(a_full_battery(), model="jev-1.13")
    record = triage.judge(CARD, LOG, transport=service, settings=with_a_key())

    assert record["model_asked_for"] == "jev-1.12"
    assert record["model"] == "jev-1.13"
    assert all(a["model"] == "jev-1.13" for a in record["conditions"])


def test_a_moving_version_is_refused_before_anything_is_asked():
    """`-latest` answers a question nobody can ask again."""
    ok, why = jev.available(with_a_key(jev_model="jev-latest"))
    assert ok is False and "pin" in why

    service = Service(a_full_battery())
    record = triage.judge(CARD, LOG, transport=service,
                          settings=with_a_key(jev_model="jev-latest"))
    assert record["asked"] is False and service.asked == []


def test_a_battery_that_comes_back_short_is_not_read_as_a_quiet_no():
    """Four answers out of six, with the missing two reading as 'no', is the
    exact ambiguity this pass exists to delete."""
    service = Service(a_full_battery(), drop="deadlock")
    record = triage.judge(CARD, LOG, transport=service, settings=with_a_key())

    assert record["asked"] is False
    assert "deadlock" in record["note"]


def test_no_question_in_the_battery_carries_a_threshold():
    """A probability becomes a verdict only once a calibration plot drawn on
    this project's own runs says where the line is. That plot does not exist,
    so there is no line, and the record says so where a reader will see it."""
    service = Service(a_full_battery())
    record = triage.judge(CARD, LOG, transport=service, settings=with_a_key())

    assert record["thresholds"] == "none: a probability is recorded, not a verdict"
    for answer in record["conditions"]:
        assert isinstance(answer["probability"], float)
        assert "holds" not in answer and "verdict" not in answer


# ------------------------------------------------------ beside, not instead

def test_the_open_pass_is_untouched_by_whatever_the_battery_says():
    """`worst`, the findings and the count the store promotes to columns are
    the open pass's own, before and after. A judgment is a proposal."""
    card = dict(CARD, triage={"findings": [{"severity": "low", "what": "a retry",
                                            "evidence": "retrying (3)"}],
                              "worst": "low", "model": "qwen3:8b"})
    before = json.loads(json.dumps(card["triage"]))

    service = Service(a_full_battery(worst="high"))
    card["jev"] = triage.judge(card, LOG, transport=service, settings=with_a_key())

    assert card["triage"] == before


def test_what_the_store_trends_still_comes_from_the_open_pass_alone(tmp_path):
    """The two promoted columns are what 'which runs had high-severity
    findings' is answered from. A judgment may not be an input to either."""
    card = dict(CARD, triage={"findings": [{"severity": "low", "what": "a retry",
                                            "evidence": "retrying (3)"}],
                              "worst": "low"})
    service = Service(a_full_battery(worst="high"))
    card["jev"] = triage.judge(card, LOG, transport=service, settings=with_a_key())

    path = tmp_path / "runs.db"
    run_id = store.record(card, path=path)
    row = next(r for r in store.recent(path=path) if r["id"] == run_id)

    assert row["triage_findings"] == 1
    assert row["triage_worst"] == "low"
    # And the judgment is still kept, in the card the store writes whole.
    with store.connect(path) as conn:
        kept = json.loads(conn.execute(
            "SELECT scorecard FROM runs WHERE id = ?", (run_id,)).fetchone()[0])
    assert kept["jev"]["worst_problem"]["level"] == "high"


def test_the_two_passes_are_compared_and_neither_is_called_right():
    """The comparison is the evidence the survey asks for, accumulated a run
    at a time. It says where they agree; it never says who is correct."""
    card = dict(CARD, triage={"findings": [{"severity": "medium", "what": "a retry storm",
                                            "evidence": "retrying (3)"}],
                              "worst": "medium"})
    service = Service(a_full_battery(worst="medium"))
    record = triage.judge(card, LOG, transport=service, settings=with_a_key())

    comparison = record["comparison"]
    assert comparison["agree"] is True
    assert comparison["qwen_worst"] == "medium" and comparison["jev_worst"] == "medium"
    assert comparison["jev_highest_condition"]["question"] == "retry_storm"
    assert "Both found something" in comparison["line"]
    for forbidden in ("right", "wrong", "correct", "better"):
        assert forbidden not in comparison["line"].lower()


def test_a_clean_run_and_a_disagreement_both_read_as_sentences():
    clean = triage.compare({"findings": [], "worst": None},
                           {"asked": True,
                            "conditions": [{"question": "deadlock", "probability": 0.02}],
                            "worst_problem": {"level": "none"}})
    assert clean["agree"] is True and "Both read the run as clean" in clean["line"]

    split = triage.compare({"findings": [{"severity": "high", "what": "x"}], "worst": "high"},
                           {"asked": True,
                            "conditions": [{"question": "deadlock", "probability": 0.02}],
                            "worst_problem": {"level": "none"}})
    assert split["agree"] is False and "Only qwen found something" in split["line"]


def test_a_comparison_of_a_pass_that_was_not_asked_says_so():
    """Rather than reading as agreement with a silence."""
    comparison = triage.compare({"findings": [], "worst": None},
                                {"asked": False, "note": "not asked (no key)"})
    assert comparison["agree"] is None and "not asked (no key)" in comparison["line"]


# ------------------------------------------------------------------ the key

def test_the_key_is_never_in_a_record_a_repr_or_a_log(caplog):
    """It is read from one named setting and held behind the transport. The
    only thing anything prints about it is whether one is set."""
    secret = "sk-jev-THIS-MUST-NEVER-BE-PRINTED"
    settings = Settings(jev_api_key=secret)

    transport = jev.SdkTransport(settings.jev_api_key)
    assert secret not in repr(transport)
    assert "key=set" in repr(transport)

    client, why = jev.from_settings(settings, transport=Service(a_full_battery()))
    assert secret not in repr(client) and secret not in why

    with caplog.at_level("DEBUG"):
        record = triage.judge(CARD, LOG, transport=Service(a_full_battery()),
                              settings=settings)
    assert secret not in json.dumps(record)
    assert secret not in caplog.text

    ok, why = jev.available(Settings())
    assert ok is False and "MES_JEV_API_KEY" in why and secret not in why


def test_a_transport_cannot_be_built_without_a_key_at_all():
    with pytest.raises(ValueError, match="needs a key"):
        jev.SdkTransport("")
