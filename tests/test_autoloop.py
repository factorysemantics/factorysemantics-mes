"""The night shift's machinery.

The judgment lives in the night-shift contract and is exercised by a real
agent; what is testable here are the promises the machinery makes: the kill
switch is honoured and reported, a skipped night is visible rather than
silent, the brief carries every kind of finding, the high-water mark means a
triage finding is briefed once, and an idle night tells the agent to verify
the quiet instead of inventing work.
"""

import json

import pytest

from fsmes.sim import autoloop


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(autoloop, "BASE_DIR", tmp_path / "autoloop")
    monkeypatch.setattr(autoloop, "STATE", tmp_path / "autoloop" / "state.json")
    monkeypatch.setattr(autoloop, "OFF_SWITCH", tmp_path / "autoloop.off")
    monkeypatch.setattr(autoloop, "REPORTS", tmp_path / "reports")
    yield


SCORES = [{"plant": "bottling", "ok": True, "tail": []}]


def empty_findings(**over):
    found = {"ui": [], "triage": [], "conversations": [], "regressions": [],
             "inbox": [], "_top_run_id": 0}
    found.update(over)
    return found


# -------------------------------------------------------------- kill switch

def test_the_kill_switch_is_honoured_and_the_morning_says_so():
    """A loop that silently does not run is indistinguishable from a loop
    that found nothing - the morning note must carry the difference."""
    autoloop.OFF_SWITCH.parent.mkdir(parents=True, exist_ok=True)
    autoloop.OFF_SWITCH.touch()

    note = autoloop.run(echo=lambda *_: None)
    text = note.read_text(encoding="utf-8")
    assert "Did not run" in text
    assert "autoloop.off" in text


def test_the_env_kill_switch_works_too(monkeypatch):
    monkeypatch.setenv("MES_AUTOLOOP", "off")
    assert autoloop.disabled() is not None


# ------------------------------------------------------------------ brief

def test_the_brief_carries_every_kind_of_finding():
    found = empty_findings(
        ui=[{"plant": "bottling", "kind": "broken-link",
             "what": "/dashboard/gone answers 404", "where": "x"}],
        triage=[{"run": 7, "plant": "machining", "worst": "high",
                 "findings": [{"what": "counter went 4102 -> 17"}]}],
        conversations=[{"id": 4, "route": "/dashboard/station",
                        "title": "the station should show SPC"}],
        regressions=["run 7 (machining): breakdown recall 0.500"],
        inbox=[{"slug": "ui-broken-link-abc", "title": "a standing finding"}])
    text = autoloop.brief(SCORES, found, "2026-09-02").read_text(encoding="utf-8")

    assert "night-shift/SKILL.md" in text, "the contract outranks the brief"
    assert "/dashboard/gone answers 404" in text
    assert "counter went 4102 -> 17" in text
    assert "conversation 4" in text
    assert "recall 0.500" in text
    assert "ui-broken-link-abc" in text


def test_an_idle_night_tells_the_agent_to_verify_the_quiet():
    """Nothing waiting must not read as 'do something anyway' - inventing
    work at 3am is how a quiet product grows barnacles."""
    text = autoloop.brief(SCORES, empty_findings(), "2026-09-02").read_text(encoding="utf-8")
    assert "Nothing is waiting" in text
    assert "Verify the quiet honestly" in text


# ------------------------------------------------------------- high water

def test_a_triage_finding_is_briefed_once(monkeypatch, tmp_path):
    """Same promise the design pipeline makes: judged means judged."""
    import sqlite3

    from fsmes.sim import store

    db = tmp_path / "runs.db"
    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA)
    card = {"triage": {"findings": [{"what": "impossible count"}], "worst": "high"},
            "metrics": {}}
    conn.execute(
        "INSERT INTO runs (plant, scored_at, speed, triage_findings,"
        " triage_worst, scorecard) VALUES ('m', '2026-09-02T01:00:00', 60,"
        " 1, 'high', ?)", (json.dumps(card),))
    conn.commit()
    conn.close()
    monkeypatch.setattr(store, "DEFAULT_STORE", db)
    # Nothing else should be gathered in this test.
    monkeypatch.setattr(autoloop, "PLANTS", {})
    from fsmes.services import design_triage
    monkeypatch.setattr(design_triage, "pending", lambda **k: [])
    monkeypatch.setattr(design_triage, "notes", lambda **k: [])

    first = autoloop.gather(echo=lambda *_: None)
    assert len(first["triage"]) == 1
    autoloop._save_state({"last_triaged_run_id": first["_top_run_id"]})

    second = autoloop.gather(echo=lambda *_: None)
    assert second["triage"] == []


# ------------------------------------------------------------ morning note

def test_a_dead_plant_is_a_finding_not_a_crash(monkeypatch, tmp_path):
    """The crawl failing IS information - the agent should hear about it."""
    monkeypatch.setattr(autoloop, "PLANTS", {"bottling": "http://127.0.0.1:1"})
    from fsmes.services import design_triage
    from fsmes.sim import store
    monkeypatch.setattr(design_triage, "pending", lambda **k: [])
    monkeypatch.setattr(design_triage, "notes", lambda **k: [])
    monkeypatch.setattr(store, "DEFAULT_STORE", tmp_path / "fresh-runs.db")

    found = autoloop.gather(echo=lambda *_: None)
    assert found["ui"], "an unreachable plant must surface as a finding"
    assert found["ui"][0]["kind"] == "crawl-failed"


def test_the_morning_note_reports_an_agent_that_died():
    agent = {"ok": False, "tail": "the agent hit the 3h timeout",
             "worktree": "/srv/fsmes/worktrees/fsmes-night-2026-09-02"}
    note = autoloop.write_note("2026-09-02", SCORES, empty_findings(), agent)
    text = note.read_text(encoding="utf-8")
    assert "did not finish" in text
    assert "3h timeout" in text
    assert "fsmes-night-2026-09-02" in text, "the wreck must be findable"


def test_the_morning_note_carries_the_agents_report_when_it_wrote_one():
    autoloop.BASE_DIR.mkdir(parents=True, exist_ok=True)
    (autoloop.BASE_DIR / "report-2026-09-02.md").write_text(
        "- built orders-filter-fix on night/2026-09-02 (merged: UI-only, suite green)", encoding="utf-8")
    agent = {"ok": True, "tail": "", "worktree": "x"}
    note = autoloop.write_note("2026-09-02", SCORES, empty_findings(), agent)
    assert "merged: UI-only, suite green" in note.read_text(encoding="utf-8")


# ---------------------------------------------------------------- contract

def test_the_contract_exists_and_holds_the_load_bearing_rules():
    """The machinery must not outlive its rules. If someone deletes or
    guts the contract, the loop should fail this test rather than run an
    unbounded unattended agent."""
    contract = autoloop.REPO / ".claude" / "skills" / "night-shift" / "SKILL.md"
    text = contract.read_text(encoding="utf-8")
    for rule in ("at most 3", "Never reboot", "src/fsmes/web/",
                 "needs-guidance", "revert"):
        assert rule in text, f"the night-shift contract lost the {rule!r} rule"


def test_the_agent_budget_constants_match_the_contract():
    assert autoloop.MAX_BUILDS == 3
    assert autoloop.MAX_TURNS <= 300
