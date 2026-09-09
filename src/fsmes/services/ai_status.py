"""What the local AI layer is doing, and whether anyone would notice it stop.

Scott's question, verbatim: "I'm not really even sure how the previous
assigned work for the qwen model is working or going… I really don't even
know if it's working or where any of it goes." A layer that works silently
and fails silently is indistinguishable from one that never ran - so every
consumer of the local model reports three things here: when it last did
anything, where its output goes, and whether that is recent enough to trust.

Two rules, both inherited.

**Unknown is not zero and stale is not dead** (principle 4). A rollup note
that is forty hours old on a machine that sleeps at night is probably a
machine that was off, and the panel says so instead of shouting failure. A
store that cannot be read reports *unknown*, never a healthy-looking blank.

**This module only reads.** It asks Ollama over HTTP, asks nvidia-smi, and
stats files other components wrote. Nothing here can change the plant or the
model - an observability layer that can act is a control layer wearing the
wrong name.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

# One idle knob for the whole machine, env-overridable per plant. The other
# services still carry their own constant; folding them onto this one is
# recorded follow-up in docs/ai/OBSERVABILITY.md, not smuggled into this
# change.
OLLAMA_URL = os.environ.get("MES_OLLAMA", "http://127.0.0.1:11434")

RUNS_DB = Path.home() / ".local" / "share" / "fsmes" / "runs.db"
REPORTS = Path.home() / ".local" / "share" / "fsmes" / "reports"
DESIGN_DB = Path.home() / ".local" / "share" / "fsmes" / "design.db"

# The standing priority order, ratified 2026-09-02 (docs/ai/BUDGET.md holds
# the reasoning). The panel shows it so "is the GPU doing what I decided it
# should" is answerable at a glance.
BUDGET = [
    "floor assistant and product features",
    "scored-run log triage",
    "nightly rollup narration",
    "design chat",
]

# Older than this and a daily artifact is flagged. Generous on purpose: main
# is LUKS-encrypted and regularly off overnight, so "late" usually means
# "the machine slept", and the message says so.
ROLLUP_STALE = timedelta(hours=40)


def enabled() -> bool:
    """Whether this machine is meant to have a local AI layer at all.

    Explicitly disabled beats guessing: a customer plant with no GPU sets
    nothing and simply never reaches Ollama, and the panel stays absent
    rather than red.
    """
    return os.environ.get("MES_LOCAL_AI", "1").strip().lower() not in ("0", "false", "no", "off")


# ------------------------------------------------------------------ sources

def _get(path: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}{path}", timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def ollama() -> dict:
    """Is the model server up, what is loaded, what is installed."""
    ps = _get("/api/ps")
    if ps is None:
        return {"reachable": False, "url": OLLAMA_URL, "loaded": [], "installed": []}
    tags = _get("/api/tags") or {}
    return {
        "reachable": True,
        "url": OLLAMA_URL,
        "loaded": [
            {"model": m.get("name"),
             "vram_bytes": m.get("size_vram"),
             "until": m.get("expires_at")}
            for m in ps.get("models", [])
        ],
        "installed": [m.get("name") for m in tags.get("models", [])],
    }


def gpu() -> dict | None:
    """nvidia-smi's four numbers, or None where there is no NVIDIA tooling -
    absent hardware is not an error."""
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    used, total, util, temp = [v.strip() for v in out.stdout.strip().split(",")[:4]]
    return {"vram_used_mb": int(used), "vram_total_mb": int(total),
            "utilization_pct": int(util), "temperature_c": int(temp)}


# ---------------------------------------------------------------- consumers

def _age(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    delta = datetime.now(UTC) - ts
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)}m ago"
    if hours < 48:
        return f"{hours:.1f}h ago"
    return f"{delta.days}d ago"


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


def _sqlite_scalar(db: Path, sql: str):
    """One value out of a store this module does not own. Failure is
    *unknown*, never a healthy-looking default."""
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            return conn.execute(sql).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def consumers() -> list[dict]:
    """Every assigned job the local model has, one honest row each."""
    rows: list[dict] = []
    now = datetime.now(UTC)

    # --- scored-run triage -------------------------------------------------
    got = _sqlite_scalar(
        RUNS_DB,
        "SELECT MAX(scored_at), COUNT(*),"
        " SUM(CASE WHEN triage_findings IS NULL THEN 1 ELSE 0 END) FROM runs")
    if got is None:
        rows.append({
            "name": "Run triage", "trigger": "every scored run",
            "output": str(RUNS_DB),
            "state": "unknown", "last": None,
            "note": "the results store could not be read"})
    else:
        newest, total, untriaged = got
        ts = _parse_ts(newest)
        note = f"{total or 0} run(s) stored"
        if untriaged:
            note += f"; {untriaged} with no triage verdict recorded"
        rows.append({
            "name": "Run triage", "trigger": "every scored run",
            "output": str(RUNS_DB),
            "state": "ok" if total else "idle",
            "last": _age(ts), "note": note})

    # --- nightly rollup ----------------------------------------------------
    notes = sorted(REPORTS.glob("*.md"), key=lambda p: p.stat().st_mtime,
                   reverse=True) if REPORTS.is_dir() else []
    ts = _mtime(notes[0]) if notes else None
    if ts is None:
        state, note = "unknown", "no rollup note found"
    elif now - ts > ROLLUP_STALE:
        state = "stale"
        note = ("last note is old - was the machine off overnight? "
                "The timer catches up on boot (Persistent=true).")
    else:
        state, note = "ok", notes[0].name
    rows.append({
        "name": "Nightly rollup", "trigger": "05:30 timer (catches up after boot)",
        "output": str(REPORTS),
        "state": state, "last": _age(ts), "note": note})

    # --- design chat -------------------------------------------------------
    got = _sqlite_scalar(
        DESIGN_DB, "SELECT MAX(updated_at), COUNT(*) FROM conversations")
    if got is None:
        rows.append({
            "name": "Design chat", "trigger": "the Design button, on demand",
            "output": str(DESIGN_DB),
            "state": "unknown", "last": None,
            "note": "design.db could not be read"})
    else:
        newest, total = got
        rows.append({
            "name": "Design chat", "trigger": "the Design button, on demand",
            "output": f"{DESIGN_DB} → triage → docs/design/backlog/",
            "state": "ok" if total else "idle",
            "last": _age(_parse_ts(newest)),
            "note": f"{total or 0} conversation(s) kept"})

    # --- the on-demand pair ------------------------------------------------
    up = ollama()["reachable"]
    rows.append({
        "name": "Floor assistant", "trigger": "the Assistant button, on demand",
        "output": "answers live; stores nothing",
        "state": "ok" if up else "down",
        "last": None,
        "note": "routes questions and picks guides"
                if up else "Ollama unreachable - falls back to lexical matching"})
    # --- the cloud brain behind the same panel --------------------------------
    from fsmes.services import agent as floor_agent
    on, why = floor_agent.available()
    spent = floor_agent.spend_this_month()
    rows.append({
        "name": "Floor agent", "trigger": "the Assistant panel, on demand",
        "output": f"proposes and, once confirmed, performs; usage in {floor_agent.USAGE_FILE}",
        "state": "ok" if on else "off",
        "last": _age(floor_agent.last_used()),
        "note": (f"{floor_agent.MODEL}: ${spent:.2f} of ${floor_agent.monthly_cap_usd():.0f} this month"
                 if on else why)})
    rows.append({
        "name": "Instruction drafting", "trigger": "fsmes draft-instructions, on demand",
        "output": "documents module, marked drafted_by_model, arriving unapproved",
        "state": "ok" if up else "down",
        "last": None,
        "note": "a draft is never in force until a person approves it"})

    # --- the night shift ---------------------------------------------------
    loop_dir = Path.home() / ".local" / "share" / "fsmes" / "autoloop"
    off = Path.home() / ".local" / "share" / "fsmes" / "autoloop.off"
    notes = sorted(loop_dir.glob("report-*.md"), reverse=True)
    state_file = loop_dir / "state.json"
    last = _mtime(state_file)
    if off.exists():
        rows.append({
            "name": "Night shift", "trigger": "02:30 timer + fsmes autoloop",
            "output": str(loop_dir),
            "state": "down", "last": _age(last),
            "note": "disabled by the kill switch (autoloop.off)"})
    elif last is None:
        rows.append({
            "name": "Night shift", "trigger": "02:30 timer + fsmes autoloop",
            "output": str(loop_dir),
            "state": "idle", "last": None,
            "note": "has never run on this machine"})
    else:
        stale = datetime.now(UTC) - last > ROLLUP_STALE
        rows.append({
            "name": "Night shift", "trigger": "02:30 timer + fsmes autoloop",
            "output": f"{loop_dir} + morning note in reports/",
            "state": "stale" if stale else "ok", "last": _age(last),
            "note": (notes[0].name if notes else "ran, but no agent report found")
                    + (" - was the machine off overnight?" if stale else "")})

    return rows


def status() -> dict:
    """Everything the panel and the CLI show. Reads only."""
    if not enabled():
        return {"enabled": False}
    return {
        "enabled": True,
        "ollama": ollama(),
        "gpu": gpu(),
        "consumers": consumers(),
        "budget": BUDGET,
        "checked_at": datetime.now(UTC).isoformat(),
    }
