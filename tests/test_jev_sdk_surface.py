"""The request this MES sends the judgment service, and the answer it reads.

The real `typesafe-sdk` client, driven through an `httpx2` mock transport:
no network, no key, one scripted response. This is what would have caught
the attribute error of 2026-09-17 before a scored run did - the client is
built and called here exactly as the product builds and calls it, so a
change in the SDK's shape fails a test instead of a run.

These skip, saying so, where the SDK is not installed: it is an optional
extra (`[jev]`) and CI does not install it. The promise that no failure of
any of this can reach a run is pinned without the SDK, in
`test_jev_fail_safe.py`.
"""

import json

import pytest

from fsmes import shadow
from fsmes.config import Settings
from fsmes.integrations import jev
from fsmes.sim import triage

typesafe_sdk = pytest.importorskip(
    "typesafe_sdk",
    reason="the judgment client is the optional [jev] extra; install "
           "'factorysemantics-mes[jev]' to pin its surface here")
httpx2 = pytest.importorskip("httpx2", reason="ships with the [jev] extra's client")

KEY = "sk-jev-THIS-MUST-NEVER-BE-PRINTED"

LOG = """
2026-09-17 02:11:19 opc.agent subscription dropped, retrying (1)
2026-09-17 02:11:20 opc.agent subscription dropped, retrying (2)
""".strip()

CARD = {"plant": "cutlery", "speed": 4.0,
        "metrics": {"breakdown_recall": 1.0, "planned_stop_misclassified": 0},
        "triage": {"findings": [{"severity": "low", "what": "a retry",
                                 "evidence": "retrying (2)"}],
                   "worst": "low", "model": "qwen3:8b"}}


def with_a_key(**overrides) -> Settings:
    """Settings that would let a judgment be asked. The key is this file's own
    string; nothing here reads a real one and nothing here can."""
    return Settings(jev_api_key=KEY, **overrides)


def a_service(answers: dict, *, model: str = "jev-1.13.0", status: int = 200,
              seen: dict | None = None):
    """The real SDK client, talking to a scripted HTTP response.

    No network and no key: `httpx2.MockTransport` answers in-process, which
    is the SDK's own documented way of being driven by a test.
    """
    def handler(request):
        if seen is not None:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            seen["authorization"] = request.headers.get("authorization", "")
        if status != 200:
            return httpx2.Response(status, json={"error": {"message": "invalid api key"}})
        return httpx2.Response(200, json={"model": model,
                                          "usage": {"input_tokens": 320, "output_tokens": 21},
                                          "answers": answers},
                               headers={"x-typesafe-request-id": "req-abc"})
    return httpx2.MockTransport(handler)


def a_full_battery() -> dict:
    """One answer per question the run-log battery asks, in the wire shape the
    service replies in."""
    answers = {n.name: {"type": "noul", "noul": 0.04} for n in triage.JEV_QUESTIONS.nouls}
    answers["retry_storm"] = {"type": "noul", "noul": 0.94}
    answers["worst_problem"] = {
        "type": "score", "score": 1.8, "confidence": 0.62,
        "legend": {"0": "none", "1": "low", "2": "medium", "3": "high"},
        "probabilities": {"0": 0.05, "1": 0.2, "2": 0.65, "3": 0.1}}
    return answers


def test_the_request_this_mes_sends_is_the_shape_the_service_documents():
    """What would have caught the attribute error without spending a run: the
    real client, built and called exactly as the product builds and calls it."""
    seen: dict = {}
    transport = jev.SdkTransport(KEY, http_transport=a_service(a_full_battery(), seen=seen))

    record = triage.judge(CARD, LOG, transport=transport, settings=with_a_key())
    assert record["asked"] is True, record.get("note")

    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    body = seen["body"]
    assert body["state"] == LOG
    assert body["model"] == "jev-1.13.0"
    # Six questions, keyed by the names the answers come back under, and each
    # carrying the exact words the record fingerprints.
    assert len(body["questions"]) == 6
    assert body["questions"]["retry_storm"]["type"] == "noul"
    assert body["questions"]["retry_storm"]["instructions"] == \
        triage.JEV_QUESTIONS.text_of("retry_storm")
    score = body["questions"]["worst_problem"]
    assert score["type"] == "score"
    assert [c["level"] for c in score["criteria"]] == ["none", "low", "medium", "high"]
    # And nothing else about the run goes with it.
    assert set(body) == {"state", "model", "questions"}


def test_the_answer_is_read_back_onto_the_record_the_run_keeps():
    transport = jev.SdkTransport(KEY, http_transport=a_service(a_full_battery()))
    record = triage.judge(CARD, LOG, transport=transport, settings=with_a_key())

    assert record["model"] == "jev-1.13.0"
    assert record["usage"] == {"input_tokens": 320, "output_tokens": 21}
    storm = next(c for c in record["conditions"] if c["question"] == "retry_storm")
    assert storm["probability"] == 0.94 and storm["request_id"] == "req-abc"
    assert storm["level"] is None and storm["expected_score"] is None

    worst = record["worst_problem"]
    # The service answers a score with a number that can fall between two
    # levels; the level stored is the one carrying the most probability, the
    # number is kept as it came, and the whole distribution is kept beside it.
    assert worst["level"] == "medium"
    assert worst["expected_score"] == 1.8
    assert worst["confidence"] == 0.62
    assert worst["probabilities"] == {"none": 0.05, "low": 0.2, "medium": 0.65, "high": 0.1}
    assert record["thresholds"] == "none: a probability is recorded, not a verdict"


def test_an_answer_of_the_wrong_kind_is_not_read_as_an_answer():
    """A score where a condition was asked would otherwise arrive as `None`
    and read as a quiet no."""
    answers = a_full_battery()
    answers["deadlock"] = {"type": "score", "score": 0.0, "confidence": 0.5,
                           "legend": {"0": "none"}, "probabilities": {"0": 1.0}}
    transport = jev.SdkTransport(KEY, http_transport=a_service(answers))

    record = triage.judge(CARD, LOG, transport=transport, settings=with_a_key())
    assert record["asked"] is False
    assert "'deadlock'" in record["note"] and "condition" in record["note"]


def test_a_refused_key_reaches_the_record_as_a_sentence_without_the_key():
    """The real error class, from the real client, on a real 401 body."""
    transport = jev.SdkTransport(KEY, http_transport=a_service({}, status=401))

    record = triage.judge(CARD, LOG, transport=transport, settings=with_a_key())

    assert record["asked"] is False
    assert "TypeSafeAuthenticationError" in record["note"]
    assert "refused MES_JEV_API_KEY" in record["note"]
    assert KEY not in json.dumps(record)


def test_the_key_travels_as_a_header_and_the_battery_carries_only_the_log():
    seen: dict = {}
    transport = jev.SdkTransport(KEY, http_transport=a_service(a_full_battery(), seen=seen))
    triage.judge(CARD, LOG, transport=transport, settings=with_a_key())

    assert KEY in seen["authorization"], "the key is how the service knows us"
    assert KEY not in json.dumps(seen["body"])


# ------------------------------------------- which version there is to pin

def a_model_list(names: list[str]):
    def handler(request):
        return httpx2.Response(200, json={"models": [
            {"name": name, "description": "a judgment model", "release_date": "2026-09-01"}
            for name in names]})
    return httpx2.MockTransport(handler)


def test_the_model_list_is_reported_with_its_total():
    listed = jev.list_models(KEY, http_transport=a_model_list(["jev-latest", "jev-preview"]))
    assert [m["name"] for m in listed] == ["jev-latest", "jev-preview"]
    assert len(listed) == 2


def test_the_version_that_answers_is_learned_by_asking_not_by_listing():
    """The service listed only moving aliases on 2026-09-17, and a call made
    as `jev-latest` answered as a concrete version that is then pinnable. So
    resolving a pin costs one deliberate call, and this is it."""
    seen: dict = {}
    resolved = jev.resolve_version(
        KEY, http_transport=a_service({"failed": {"type": "noul", "noul": 0.03}},
                                      model="jev-1.13.0", seen=seen))

    assert resolved["asked_as"] == "jev-latest"
    assert resolved["served"] == "jev-1.13.0"
    assert resolved["usage"] == {"input_tokens": 320, "output_tokens": 21}
    assert seen["body"]["model"] == "jev-latest"
    assert len(seen["body"]["questions"]) == 1


def test_neither_asks_anything_in_shadow_mode(monkeypatch):
    monkeypatch.setattr(shadow, "enabled", lambda settings=None: True)
    with pytest.raises(shadow.ShadowRefused):
        jev.list_models(KEY, http_transport=a_model_list([]))
    with pytest.raises(shadow.ShadowRefused):
        jev.resolve_version(KEY, http_transport=a_model_list([]))
