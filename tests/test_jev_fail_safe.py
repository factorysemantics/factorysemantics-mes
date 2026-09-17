"""No failure of the judgment transport may ever cost a run.

On 2026-09-17 the first real call to the judgment service raised
`AttributeError: module 'typesafe_sdk' has no attribute 'TypeSafe'` - the
client had been written from a description of the SDK rather than from the
SDK - and that error killed the scoring of a simulated run that had already
succeeded. The tests written before it proved that a service which *does not
answer* costs the run nothing; nothing proved that a client which cannot be
*built* costs the run nothing.

Decision 0031 says a judgment gates nothing and may cost nothing. So every
failure - a missing attribute, a bad argument, a refused key, an answer
shaped wrong, something nobody has thought of - becomes a sentence in the
record, and the run carries on with the answer it already had.

Nothing here needs the SDK, a key or a network. The companion file
`test_jev_sdk_surface.py` pins the SDK's actual shape where it is installed.
"""

import json
import sys
import types

import pytest

from fsmes import shadow
from fsmes.config import Settings
from fsmes.integrations import jev
from fsmes.sim import store, triage

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


# ------------------------------------------------- nothing escapes the file

def test_a_client_that_cannot_be_built_is_not_asked_rather_than_a_traceback(monkeypatch):
    """The failure that happened. `typesafe_sdk` imports, and the class this
    MES reaches for is not in it - which is what a version of somebody else's
    SDK moving under you looks like. It reads as a sentence in the record."""
    monkeypatch.setitem(sys.modules, "typesafe_sdk", types.ModuleType("typesafe_sdk"))

    record = triage.judge(CARD, LOG, settings=with_a_key())

    assert record["asked"] is False
    assert record["note"].startswith("not asked (AttributeError:")
    assert "TypeSafeClient" in record["note"]


def test_a_scored_run_is_still_scored_and_still_stored_when_the_judgment_explodes(
        monkeypatch, tmp_path):
    """The whole promise, end to end: the run that produced this log had
    already succeeded before anything was asked, and it keeps its triage, its
    comparison line and its row in the results store."""
    monkeypatch.setitem(sys.modules, "typesafe_sdk", types.ModuleType("typesafe_sdk"))

    card = json.loads(json.dumps(CARD))
    card["jev"] = triage.judge(card, LOG, settings=with_a_key())

    assert card["triage"] == CARD["triage"]
    assert "not asked" in card["jev"]["comparison"]["line"]
    assert card["jev"]["comparison"]["agree"] is None

    path = tmp_path / "runs.db"
    run_id = store.record(card, path=path)
    row = next(r for r in store.recent(path=path) if r["id"] == run_id)
    assert row["triage_findings"] == 1 and row["triage_worst"] == "low"


class Exploding:
    """A transport that raises something nobody wrote a handler for."""

    def __init__(self, exc: BaseException):
        self.exc = exc

    def ask(self, *, state, questions, model):
        raise self.exc


def test_an_exception_nobody_anticipated_is_recorded_and_the_run_carries_on():
    """`except (RuntimeError, ValueError, OSError)` is a list of the failures
    somebody thought of, and the one that hurt was not on it. So the caller
    catches every exception, and the record names the class - a service being
    down and this code being wrong are different facts."""
    record = triage.judge(CARD, LOG, transport=Exploding(ZeroDivisionError("division by zero")),
                          settings=with_a_key())

    assert record["asked"] is False
    assert record["note"] == "not asked (ZeroDivisionError: division by zero)"


def test_a_failure_with_nothing_to_say_still_says_which_class_it_was():
    record = triage.judge(CARD, LOG, transport=Exploding(AttributeError()),
                          settings=with_a_key())
    assert record["note"] == "not asked (AttributeError: no message)"


class TypeSafeAuthenticationError(Exception):
    """Named as the SDK names it, because that name is what is read.

    The real class is only importable where the extra is installed, and this
    path has to be tested everywhere - so the sentence is built from the
    class's name rather than from `isinstance`.
    """


def test_a_refused_key_says_the_key_was_refused_and_never_prints_it():
    """Which setting to look at, without putting the thing itself in a record
    that goes into a run directory somebody else reads."""
    refused = TypeSafeAuthenticationError(f"401 invalid api key (key={KEY})")
    transport = jev.SdkTransport(KEY)
    monkey = Exploding(refused)
    transport._ask = lambda **kwargs: monkey.ask(**kwargs)  # the call itself fails

    record = triage.judge(CARD, LOG, transport=transport, settings=with_a_key())

    assert record["asked"] is False
    assert "TypeSafeAuthenticationError" in record["note"]
    assert "the judgment service refused MES_JEV_API_KEY" in record["note"]
    assert KEY not in record["note"] and "[redacted]" in record["note"]
    assert KEY not in json.dumps(record)


def test_the_reason_keeps_a_judgment_unavailable_sentence_as_it_stands():
    """A sentence this package wrote is already the reason. Naming the class
    in front of it - `not asked (JevUnavailable: ...)` - would be noise."""
    assert jev.reason(jev.JevUnavailable(jev.NO_SDK)) == jev.NO_SDK
    assert jev.reason(OSError("connection refused")) == "OSError: connection refused"


def test_a_long_failure_is_cut_down_before_it_reaches_a_record():
    reason = jev.reason(RuntimeError("x" * 5_000))
    assert len(reason) <= 300


def test_shadow_mode_still_refuses_rather_than_reading_as_a_failure(monkeypatch):
    """A refusal is a decision this MES made, not a service that broke, so it
    is raised rather than swallowed - and the caller records it as what it is."""
    transport = jev.SdkTransport(KEY)
    monkeypatch.setattr(shadow, "enabled", lambda settings=None: True)

    with pytest.raises(shadow.ShadowRefused):
        transport.ask(state=LOG, questions=[{"name": "q", "kind": "noul", "text": "?"}],
                      model="jev-1.13.0")
