"""Reading the runs' logs for what nobody asserted."""

import pytest

from fsmes.sim import triage


def test_the_prompt_survives_its_own_json_example():
    """The prompt contains a JSON example. str.format read its braces as
    fields and took down every triage pass with a KeyError."""
    built = (triage.PROMPT
             .replace("<<NAME>>", "x").replace("<<SUMMARY>>", "{}")
             .replace("<<LOG>>", "y"))
    assert "<<" not in built
    assert '"severity"' in built


def test_a_healthy_log_produces_no_findings(monkeypatch):
    """A triage pass that always finds something is one nobody trusts."""
    monkeypatch.setattr(triage, "_generate", lambda *a, **k: "[]")
    out = triage.triage({"plant": "p", "speed": 1, "metrics": {}}, "all fine\n")
    assert out["findings"] == []
    assert out["worst"] is None


def test_findings_are_parsed_out_of_whatever_wraps_them(monkeypatch):
    """Small models fence their JSON, or preface it, or both."""
    monkeypatch.setattr(triage, "_generate", lambda *a, **k: (
        "Here is what I found:\n```json\n"
        '[{"severity":"high","what":"agent reconnected 40 times",'
        '"evidence":"connection refused"}]\n```'))
    out = triage.triage({"plant": "p", "speed": 1, "metrics": {}}, "log")
    assert len(out["findings"]) == 1
    assert out["worst"] == "high"
    assert out["findings"][0]["what"].startswith("agent reconnected")


def test_unparseable_output_means_no_findings_not_a_crash(monkeypatch):
    """This runs inside a nightly job; a chatty model must not fail the run
    that produced the log."""
    monkeypatch.setattr(triage, "_generate", lambda *a, **k: "I think it looks fine!")
    assert triage.triage({"plant": "p", "speed": 1, "metrics": {}}, "log")["findings"] == []


def test_a_silent_model_is_reported_not_hidden(monkeypatch):
    monkeypatch.setattr(triage, "_generate", lambda *a, **k: None)
    out = triage.triage({"plant": "p", "speed": 1, "metrics": {}}, "log")
    assert out["findings"] == []
    assert "did not answer" in out["note"]


def test_no_log_is_said_plainly(monkeypatch):
    out = triage.triage({"plant": "p", "speed": 1, "metrics": {}}, "   ")
    assert out["note"] == "no log kept for this run"


def test_a_nonsense_severity_is_downgraded_not_trusted(monkeypatch):
    monkeypatch.setattr(triage, "_generate", lambda *a, **k:
                        '[{"severity":"CATASTROPHIC","what":"something"}]')
    out = triage.triage({"plant": "p", "speed": 1, "metrics": {}}, "log")
    assert out["findings"][0]["severity"] == "low"


@pytest.mark.parametrize("worst,expected", [
    (["low", "high", "medium"], "high"),
    (["low", "medium"], "medium"),
    (["low"], "low"),
])
def test_the_worst_finding_is_the_headline(monkeypatch, worst, expected):
    import json as _json
    monkeypatch.setattr(triage, "_generate", lambda *a, **k: _json.dumps(
        [{"severity": s, "what": f"thing {s}"} for s in worst]))
    out = triage.triage({"plant": "p", "speed": 1, "metrics": {}}, "log")
    assert out["worst"] == expected
