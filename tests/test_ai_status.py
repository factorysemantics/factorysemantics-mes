"""The Local AI panel's engine.

It exists because a layer that works silently and fails silently is
indistinguishable from one that never ran. These pin the honesty rules: a
store that cannot be read is unknown rather than blank, a sleeping machine is
stale rather than dead, absent hardware is absent rather than an error, and
the store's own zero-vs-unknown bug stays fixed.
"""

import sqlite3
from datetime import UTC, datetime

import pytest

from fsmes.services import ai_status


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_status, "RUNS_DB", tmp_path / "runs.db")
    monkeypatch.setattr(ai_status, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(ai_status, "DESIGN_DB", tmp_path / "design.db")
    # No test may reach a real Ollama or a real GPU.
    monkeypatch.setattr(ai_status, "_get", lambda *a, **k: None)
    monkeypatch.setattr(ai_status, "gpu", lambda: None)
    yield


def seed_runs(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE runs (scored_at TEXT, triage_findings INTEGER)")
    conn.executemany("INSERT INTO runs VALUES (?,?)", rows)
    conn.commit()
    conn.close()


def by_name(name):
    return next(c for c in ai_status.consumers() if c["name"] == name)


# ------------------------------------------------------------------ honesty

def test_a_store_that_cannot_be_read_is_unknown_not_blank():
    """The difference between "nothing happened" and "I could not look" is
    the whole reason this panel exists."""
    row = by_name("Run triage")
    assert row["state"] == "unknown"
    assert "could not be read" in row["note"]


def test_runs_with_no_triage_verdict_are_counted_and_said_aloud(tmp_path):
    now = datetime.now(UTC).isoformat()
    seed_runs(tmp_path / "runs.db", [(now, 0), (now, None), (now, 2)])
    row = by_name("Run triage")
    assert row["state"] == "ok"
    assert "3 run(s)" in row["note"]
    assert "1 with no triage verdict" in row["note"]


def test_a_machine_that_slept_reads_stale_not_dead(tmp_path):
    """main is LUKS-encrypted and regularly off overnight. A 3-day-old rollup
    note means the machine was off, and the message should say that instead
    of shouting failure."""
    import os
    import time

    reports = tmp_path / "reports"
    reports.mkdir()
    note = reports / "old-rollup.md"
    note.write_text("x", encoding="utf-8")
    old = time.time() - 3 * 24 * 3600
    os.utime(note, (old, old))

    row = by_name("Nightly rollup")
    assert row["state"] == "stale"
    assert "off overnight" in row["note"]


def test_a_fresh_rollup_is_simply_ok(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-09-02-sim-rollup.md").write_text("x", encoding="utf-8")
    row = by_name("Nightly rollup")
    assert row["state"] == "ok"
    assert row["note"] == "2026-09-02-sim-rollup.md"


def test_ollama_down_degrades_the_on_demand_jobs_not_the_panel():
    """The assistant and the drafter have no store to stat - their health IS
    the model server's, and the assistant's note says what the fallback is."""
    assistant = by_name("Floor assistant")
    assert assistant["state"] == "down"
    assert "lexical" in assistant["note"]
    assert by_name("Instruction drafting")["state"] == "down"


def test_absent_gpu_is_none_not_an_error(monkeypatch):
    """A plant PC with no NVIDIA tooling is a normal plant PC."""
    monkeypatch.undo()
    monkeypatch.setattr(ai_status, "OLLAMA_URL", "http://127.0.0.1:1")
    import subprocess

    def no_smi(*a, **k):
        raise FileNotFoundError("nvidia-smi")
    monkeypatch.setattr(subprocess, "run", no_smi)
    assert ai_status.gpu() is None


def test_disabled_machines_report_disabled_and_nothing_else(monkeypatch):
    monkeypatch.setenv("MES_LOCAL_AI", "0")
    assert ai_status.status() == {"enabled": False}


def test_the_status_payload_carries_the_budget():
    """The point of writing the priority order down is seeing it where the
    health is - "is the GPU doing what I decided" in one glance."""
    out = ai_status.status()
    assert out["budget"] == ai_status.BUDGET
    assert out["budget"][0].startswith("floor assistant")


# ------------------------------------------------------------- the endpoint

def test_the_endpoint_serves_the_same_facts(admin):
    body = admin.get("/ai").json()
    assert body["enabled"] is True
    assert {c["name"] for c in body["consumers"]} >= {
        "Run triage", "Nightly rollup", "Design chat",
        "Floor assistant", "Instruction drafting"}


def test_an_operator_is_not_shown_the_machine_room(client):
    """The panel reads stores and GPU state - audit.read territory, the same
    gate as the rest of the Ops screen."""
    assert client.get("/ai").status_code == 403


# ------------------------------------------------------------- the store fix

def test_a_clean_run_stores_zero_and_an_untriaged_run_stores_null(tmp_path):
    """Found by the 2026-09-02 audit: `len(...) or None` collapsed a real
    zero into unknown, so a run triaged clean was indistinguishable from a
    run never triaged - principle 4's lie, pointed the other way."""
    from fsmes.sim import store

    db = tmp_path / "store.db"
    clean = {"plant": "p", "speed": 1.0, "metrics": {},
             "triage": {"findings": [], "worst": None}}
    untriaged = {"plant": "p", "speed": 1.0, "metrics": {}}
    store.record(clean, path=db)
    store.record(untriaged, path=db)

    got = sqlite3.connect(db).execute(
        "SELECT triage_findings FROM runs ORDER BY id").fetchall()
    assert got == [(0,), (None,)]


# ------------------------------------------------------------- the screen

def test_the_ops_screen_carries_the_panel_and_wires_it():
    from pathlib import Path

    web = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"
    html = (web / "ops.html").read_text(encoding="utf-8")
    js = (web / "ops.js").read_text(encoding="utf-8")

    assert 'id="ai-section"' in html and 'id="ai-consumers"' in html
    assert 'api("/ai")' in js
    assert "loadAi" in js
    # Hidden until the payload says the layer exists - a plant with no local
    # AI must not show an empty panel.
    assert 'class="panel span-2 hidden" id="ai-section"' in html
