"""Asking the labelled set, and the two figures that decide whether any
probability this project stores may ever be compared with a line.

The survey's condition for a threshold is a confusion matrix and a
calibration plot drawn on this project's own data. These tests are about the
ways such a plot lies: an empty bin drawn as if it held something, a bin
count left off, a "not asked" counted as a wrong answer, an unlabelled
condition guessed at so the table looks fuller, and a tool that takes one
look at its own plot and picks a threshold.

No network, no key and no SDK. Every asked path goes through a transport
this file wrote, which is also the normal case in the product: without a key
nothing is asked at all.
"""

import json
from pathlib import Path

import pytest

from fsmes.config import Settings
from fsmes.integrations import jev
from fsmes.sim import calibration, labelled

RUN = Path(__file__).parent / "fixtures" / "labelled-run"

#: A key that is this file's own string and could never be a real one. It is
#: here so that the test at the bottom can prove it does not come out again.
KEY = "sk-jev-THIS-MUST-NEVER-BE-PRINTED"


def with_a_key(**overrides) -> Settings:
    return Settings(jev_api_key=KEY, jev_model="jev-1.13.0", **overrides)


class Service:
    """A judgment service that answers from a script and remembers the asking."""

    def __init__(self, reply=None, raises: Exception | None = None):
        self.reply = reply or (lambda state: ("breakdown", 0.7, 0.8))
        self.raises = raises
        self.asked: list[dict] = []

    def ask(self, *, state, questions, model):
        self.asked.append({"state": state, "questions": questions, "model": model})
        if self.raises is not None:
            raise self.raises
        chosen, probability, confidence = self.reply(state)
        options = [name for name, _ in questions[0]["options"]]
        spread = round((1.0 - probability) / (len(options) - 1), 4)
        probabilities = {name: spread for name in options}
        probabilities[chosen] = probability
        return {"model": "jev-1.13.0", "request_id": "req-0001",
                "usage": {"input_tokens": 1500, "output_tokens": 8},
                "answers": {questions[0]["name"]: {
                    "probability": probability, "level": chosen,
                    "expected_score": None, "confidence": confidence,
                    "probabilities": probabilities}}}


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return labelled.build_many([RUN])["records"]


# ------------------------------------------- the normal case: nothing asked

def test_with_no_key_nothing_is_asked_and_the_file_says_which(records):
    """The no-key path is what every installation of this package has, and a
    file saying "not asked, and here is why" is a record. Nothing found and
    nothing asked are the two facts this whole track exists to keep apart."""
    stored = calibration.ask(records, settings=Settings())
    assert stored["asked"] is False
    assert "no MES_JEV_API_KEY" in stored["note"]
    assert stored["answers"] == []
    assert stored["records_total"] == len(records)


def test_shadow_mode_refuses_the_whole_pass(records):
    """A plant lending us its data to watch did not agree to a hosted call,
    and this path has no business being open while it watches."""
    stored = calibration.ask(records, settings=with_a_key(shadow=True))
    assert stored["asked"] is False
    assert "shadow mode" in stored["note"]


# ------------------------------------------------------------ what is asked

def test_every_window_is_asked_one_choice_over_the_whole_vocabulary(records):
    service = Service()
    stored = calibration.ask(records[:3], transport=service, settings=with_a_key())
    assert len(service.asked) == 3
    asked = service.asked[0]["questions"]
    assert len(asked) == 1
    assert asked[0]["kind"] == "choice"
    assert [name for name, _ in asked[0]["options"]] == list(labelled.REASON_NAMES)
    assert all(description for _, description in asked[0]["options"]), \
        "an undescribed option is answered against whatever the word suggests"
    assert stored["state_class"] == "observation"


def test_the_label_is_stored_beside_the_answer_and_never_sent(records):
    """The set knows the answer; the question must not. A label that reaches
    the state is a measurement of nothing."""
    service = Service()
    stored = calibration.ask(records[:4], transport=service, settings=with_a_key())
    for record, asked in zip(records[:4], service.asked, strict=True):
        assert record["label"] not in asked["state"] or record["label"] == "none"
        assert record["label_says"] not in asked["state"]
    assert {a["label"] for a in stored["answers"]} <= set(labelled.REASON_NAMES)


def test_a_call_that_fails_costs_the_set_nothing_and_is_not_a_wrong_answer(records):
    """Decision 0031: a judgment may not cost the thing it judges. A record
    nobody could get an answer for keeps the sentence saying why, and the
    confusion matrix counts it apart from the answers."""
    service = Service(raises=RuntimeError("the service is down"))
    stored = calibration.ask(records[:3], transport=service, settings=with_a_key())
    assert stored["asked"] is True
    assert all(a["asked"] is False for a in stored["answers"])
    assert "the service is down" in stored["answers"][0]["note"]
    matrix = calibration.confusion(stored["answers"])
    assert matrix["answered"] == 0 and matrix["not_answered"] == 3


def test_an_answer_outside_the_vocabulary_is_refused_rather_than_counted():
    """An option nobody offered is not an answer to the question that was
    asked. Letting it into a confusion matrix would put a column in the
    table that the set has no truth for."""
    question = {"name": "stop_reason", "kind": "choice", "text": "why",
                "options": [["breakdown", "it broke"], ["none", "it did not stop"]]}

    from types import SimpleNamespace

    from fsmes.integrations.jev import transport as transport_module

    answer = SimpleNamespace(choice="sabotage", confidence=0.9,
                             probabilities={"sabotage": 0.9})
    with pytest.raises(jev.JevUnavailable, match="not one of the 2 option"):
        transport_module._one_answer(question, answer)


# ------------------------------------------------------------ the matrix

def a_set_of_answers(pairs: list[tuple[str, str, float]]) -> list[dict]:
    """`(scripted, proposed, stated)` triples as stored answers."""
    return [{"id": f"x/{index}", "label": label, "asked": True,
             "answer": {"level": proposed, "probability": stated,
                        "confidence": stated}}
            for index, (label, proposed, stated) in enumerate(pairs)]


def test_the_confusion_matrix_states_every_total_including_the_empty_rows():
    """A row of zeroes says this set never tested that reason, which is
    exactly what a reader needs before believing a number elsewhere in the
    table."""
    matrix = calibration.confusion(a_set_of_answers([
        ("breakdown", "breakdown", 0.9),
        ("breakdown", "micro_stop", 0.4),
        ("starved", "starved", 0.8),
    ]))
    assert list(matrix["counts"]) == list(labelled.REASON_NAMES)
    assert matrix["counts"]["breakdown"]["micro_stop"] == 1
    assert matrix["by_label"]["breakdown"] == 2
    assert matrix["by_label"]["changeover"] == 0
    assert matrix["by_proposal"]["starved"] == 1
    assert matrix["answered"] == 3 and matrix["right"] == 2
    assert matrix["accuracy"] == pytest.approx(2 / 3, abs=1e-4)


def test_an_answer_with_no_label_in_the_vocabulary_is_counted_apart():
    matrix = calibration.confusion([
        {"id": "x", "label": "something-else", "asked": True,
         "answer": {"level": "breakdown", "probability": 0.5}}])
    assert matrix["answered"] == 0
    assert matrix["proposed_outside_the_vocabulary"] == 1


# -------------------------------------------------------------- the bins

def test_an_empty_bin_is_shown_empty_and_never_interpolated():
    """A bin with no samples is a thing this set did not measure. A plot
    that hides it is a plot that claims it did."""
    bins = calibration.bins_of([(0.95, True), (0.93, True), (0.05, False)])
    assert bins["bins_total"] == calibration.DEFAULT_BINS
    assert bins["empty_bins"] == 8
    for row in bins["bins"]:
        if row["samples"] == 0:
            assert row["measured_accuracy"] is None
            assert row["mean_stated"] is None
    assert bins["samples"] == 3


def test_a_probability_of_exactly_one_lands_in_the_top_bin():
    bins = calibration.bins_of([(1.0, True)])
    assert bins["bins"][-1]["samples"] == 1


def test_the_expected_calibration_error_is_the_weighted_distance_from_the_line():
    """Two samples stated at 0.9, one of them right: the top bin says 0.9 and
    measures 0.5, so the error is 0.4 - and with every sample in one bin,
    that is the whole number."""
    bins = calibration.bins_of([(0.9, True), (0.9, False)])
    assert bins["expected_calibration_error"] == pytest.approx(0.4, abs=1e-4)


def test_a_set_with_no_samples_has_no_error_rather_than_a_zero():
    """Unknown is not zero. Nothing measured is not perfect calibration."""
    bins = calibration.bins_of([])
    assert bins["expected_calibration_error"] is None
    assert bins["samples"] == 0
    assert bins["empty_bins"] == calibration.DEFAULT_BINS


# ------------------------------------------------- and still no threshold

def test_no_bin_is_arguable_where_the_evidence_is_thin():
    bins = calibration.bins_of([(0.95, True), (0.95, True)])
    assert bins["arguable_from"]["bin"] is None
    assert "no threshold is defensible" in bins["arguable_from"]["says"]


def test_a_bin_that_earns_it_is_named_and_left_to_a_person():
    """Naming a bin is not choosing a threshold. Nothing in this package
    reads the field, and the sentence says whose decision it is."""
    bins = calibration.bins_of([(0.95, True)] * 12)
    named = bins["arguable_from"]
    assert named["bin"] == "0.9-1.0"
    assert named["samples_at_or_above"] == 12
    assert "a person's decision, not this tool's" in named["says"]


def test_the_report_says_out_loud_that_it_chose_nothing(records):
    service = Service()
    stored = calibration.ask(records[:5], transport=service, settings=with_a_key())
    page = calibration.report(
        set_file=labelled.build_many([RUN]), answers=stored,
        d1=calibration.pair_with_truth([RUN]), figures=["p1.svg"])
    assert "No threshold is chosen here" in page
    assert "a judgment is a proposal" in page
    assert "**total**" in page


# ------------------------------------------------------------- the drawing

def test_the_plot_draws_the_perfect_line_and_prints_every_bin_s_count():
    svg = calibration.reliability_svg(
        calibration.bins_of([(0.95, True), (0.95, False), (0.15, False)]),
        title="a plot")
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "stroke-dasharray" in svg, "the perfect-calibration line"
    assert "expected calibration error" in svg
    assert "8 bin(s) empty" in svg
    assert ">0<" in svg, "an empty bin is drawn with its zero"


def test_a_plot_of_nothing_says_it_could_not_be_measured():
    svg = calibration.reliability_svg(calibration.bins_of([]), title="nothing")
    assert "not measurable - no samples" in svg
    assert "0 sample(s) in 10 bin(s); 10 bin(s) empty" in svg


# ------------------------------------------- D1's conditions against truth

def test_a_disconnect_is_what_makes_a_component_stop_reporting():
    """The fixture run scripts two things the hour can decide, and the
    sentence beside each says which scripted event decided it."""
    paired = calibration.pair_with_truth([RUN])
    by_question = {p["question"]: p for p in paired["pairs"]}
    assert by_question["component_stopped_reporting"]["truth"] is True
    assert "scripted disconnect" in by_question["component_stopped_reporting"]["truth_says"]
    assert by_question["counter_went_backwards"]["truth"] is True
    assert "scripted counter reset" in by_question["counter_went_backwards"]["truth_says"]


def test_a_condition_the_scripted_hour_cannot_decide_is_unlabelled(tmp_path):
    """Nothing in a scenario scripts whether this MES retried in a storm or
    swallowed an exception. A guessed label measured against a probability
    produces a number that looks like evidence and is not."""
    paired = calibration.pair_with_truth([RUN])
    unlabelled = [p for p in paired["pairs"] if p["truth"] is None]
    assert {p["question"] for p in unlabelled} == {
        "retry_storm", "silent_exception", "deadlock", "worst_problem"}
    assert paired["labelled"] + paired["unlabelled"] == paired["pairs_total"]
    assert all(p["truth_says"] for p in unlabelled)


def test_an_hour_that_scripts_nothing_of_the_kind_is_a_labelled_false(tmp_path):
    """The other half of the pairing, and the half round 5 needs: a run with
    no disconnect in it is a run where nothing stopped reporting, which is
    what makes a high probability there something to look at."""
    import shutil

    copied = tmp_path / "quiet"
    shutil.copytree(RUN, copied)
    line = json.loads((copied / "line" / "bench.json").read_text(encoding="utf-8"))
    line["events"] = [e for e in line["events"]
                      if e["type"] not in ("disconnect", "counter_reset")]
    (copied / "line" / "bench.json").write_text(json.dumps(line), encoding="utf-8")

    paired = calibration.pair_with_truth([copied])
    by_question = {p["question"]: p for p in paired["pairs"]}
    assert by_question["component_stopped_reporting"]["truth"] is False
    assert "nothing in this hour was scripted to go silent" in \
        by_question["component_stopped_reporting"]["truth_says"]
    assert by_question["counter_went_backwards"]["truth"] is False


def test_only_the_labelled_pairs_reach_a_plot():
    paired = calibration.pair_with_truth([RUN])
    samples = calibration.samples_from_d1(paired["pairs"])
    assert set(samples) == set(calibration.CONDITION_TRUTH)
    assert sum(len(pairs) for pairs in samples.values()) == paired["labelled"]


# --------------------------------------------------------------- the cost

def test_the_cost_is_estimated_before_anything_is_asked(records):
    """What a pass will cost is printed first, in the right order of
    magnitude, from the set itself rather than afterwards from the bill."""
    cost = calibration.estimate(records)
    assert cost["calls"] == len(records)
    assert cost["questions_per_call"] == 1
    assert cost["state_characters"] == sum(r["state_characters"] for r in records)
    assert cost["estimated_input_tokens"] > 0
    assert cost["estimated_input_tokens"] < cost["state_characters"]


def test_usage_the_service_did_not_report_is_counted_as_unreported(records):
    """Unknown is not zero: a token count nobody sent is not nought tokens,
    and a month of these has to be addable honestly or not at all."""
    class Quiet(Service):
        def ask(self, **kwargs):
            reply = super().ask(**kwargs)
            reply["usage"] = {"input_tokens": None, "output_tokens": None}
            return reply

    stored = calibration.ask(records[:2], transport=Quiet(), settings=with_a_key())
    assert stored["usage"]["not_reported_for"] == 2
    assert stored["usage"]["reported_for"] == 0


# ------------------------------------------------------------- the secret

def test_the_key_is_never_in_a_record_a_repr_or_a_log(records, caplog):
    """The one thing that must never leave this machine.

    Asked through a transport that fails, because the failure path is where
    a key reaches a message if it ever does: the sentence a record prints is
    built from the exception, and the exception came from somebody else's
    code.
    """
    service = Service(raises=RuntimeError(f"refused key {KEY}"))
    stored = calibration.ask(records[:2], transport=service, settings=with_a_key())
    printed = json.dumps(stored)
    assert KEY not in printed
    assert "[redacted]" in printed

    good = calibration.ask(records[:2], transport=Service(), settings=with_a_key())
    assert KEY not in json.dumps(good)
    assert KEY not in caplog.text

    client, why = jev.from_settings(with_a_key(), transport=Service())
    assert client is not None and why == ""
    assert KEY not in repr(client)
    assert KEY not in json.dumps(calibration.report(
        set_file=labelled.build_many([RUN]), answers=good,
        d1=calibration.pair_with_truth([RUN]), figures=[]))
