"""The night shift: the plant runs, the findings gather, the agent works.

Scott's ask, verbatim: "a run is created, analysis is gathered throughout,
and at the end of the simulation run, the analysis is put together and calls
my claude code agent here locally to run in auto in fixing and upgrading the
site... I don't even want to be in the loop for most of it. Maybe somehow
queue it up and have me review the hard ones."

So one pass, nightly or on demand:

1. **Score** both plants through their scripted hour (qwen triages each
   run's logs on the way, as it already does).
2. **Crawl** every screen on both plants - links, console errors, style
   drift against the accepted baselines. Deterministic, zero GPU.
3. **Assemble the brief** - new triage findings, new UI findings, unjudged
   design conversations, score regressions - into one work order.
4. **Run the agent**: a headless Claude Code session on this machine, in its
   own worktree, under the written contract in
   `.claude/skills/night-shift/SKILL.md`. The contract, not this module,
   holds the judgment: at most three builds a night, UI-scoped changes may
   merge themselves, anything touching the API, schema or engine queues for
   Scott, and every verdict lands where its finding came from.
5. **Write the morning note** into the reports directory, so `fsmes-reports`
   pulls it into the vault beside the rollup: what ran, what was found, what
   the agent built, merged, queued and rejected - and what needs Scott.

This module is deliberately all machinery and no judgment - the same split
the design pipeline uses. It is also honest about its absences: a night the
loop did not run is *unknown* in the morning, never a quiet "clean".

Kill switch: `touch ~/.local/share/fsmes/autoloop.off` - checked first,
reported plainly. The Ops screen's Local AI panel shows the night shift as a
consumer, so a loop that stopped running is visible the day it stops.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HOME = Path.home()
BASE_DIR = HOME / ".local" / "share" / "fsmes" / "autoloop"
STATE = BASE_DIR / "state.json"
OFF_SWITCH = HOME / ".local" / "share" / "fsmes" / "autoloop.off"
REPORTS = HOME / ".local" / "share" / "fsmes" / "reports"

PLANTS = {
    "bottling": os.environ.get("MES_AUTOLOOP_BOTTLING", "http://127.0.0.1:8010"),
    "machining": os.environ.get("MES_AUTOLOOP_MACHINING", "http://127.0.0.1:8020"),
}

# The night agent's whole entitlement, mirrored in the skill's contract.
MAX_BUILDS = 3
MAX_TURNS = 200
AGENT_TIMEOUT_S = 3 * 3600


def disabled() -> str | None:
    """The kill switch, and why the loop honoured it."""
    if OFF_SWITCH.exists():
        return f"{OFF_SWITCH} exists - remove it to re-enable the night shift"
    if os.environ.get("MES_AUTOLOOP", "").strip().lower() in ("0", "off", "false"):
        return "MES_AUTOLOOP=off in the environment"
    return None


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state["updated"] = datetime.now(UTC).isoformat()
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def last_run() -> datetime | None:
    raw = _state().get("last_run")
    if not raw:
        return None
    ts = datetime.fromisoformat(raw)
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


# ------------------------------------------------------------------ scoring

def score_plants(echo=print) -> list[dict]:
    """One scored run per plant per night - the regression heartbeat. Each
    run triages its own logs (qwen) before its evidence is discarded."""
    fsmes = REPO / ".venv" / "bin" / "fsmes"
    outcomes = []
    for plant in PLANTS:
        echo(f"scoring {plant} at speed 60…")
        try:
            done = subprocess.run(
                [str(fsmes), "score", plant, "--speed", "60"],
                capture_output=True, text=True, timeout=1800)
            ok = done.returncode == 0
            tail = (done.stdout or done.stderr).strip().splitlines()[-6:]
        except subprocess.TimeoutExpired:
            ok, tail = False, ["scoring timed out after 30 minutes"]
        outcomes.append({"plant": plant, "ok": ok, "tail": tail})
        echo(f"  {'scored' if ok else 'FAILED'}")
    return outcomes


# ----------------------------------------------------------------- findings

def gather(echo=print) -> dict:
    """Everything the agent should look at, from every pipe that exists."""
    from fsmes.services import design_triage
    from fsmes.sim import store, ui_check

    found: dict = {"ui": [], "triage": [], "conversations": [], "regressions": []}

    # UI drift and breakage, both plants (same code, so style baselines
    # apply to either; fingerprints keep findings from doubling).
    for plant, base in PLANTS.items():
        echo(f"crawling {plant}…")
        try:
            run = ui_check.crawl(base, echo=lambda *_: None)
            findings = ui_check.compare(run)
            ui_check.file_new(findings, echo=lambda *_: None)
            found["ui"] += [{"plant": plant, **f} for f in findings]
        except Exception as exc:                    # a dead plant is a finding
            found["ui"].append({
                "plant": plant, "kind": "crawl-failed",
                "what": f"{plant} could not be crawled: {type(exc).__name__}: {exc}",
                "where": base})

    # Triage findings on runs newer than the last loop.
    seen = int(_state().get("last_triaged_run_id", 0))
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT id, plant, scored_at, triage_findings, triage_worst, scorecard"
            " FROM runs WHERE id > ? ORDER BY id", (seen,)).fetchall()
    top = seen
    for row in rows:
        rid, plant, _at, n_findings, worst, card_json = row
        top = max(top, int(rid))
        if n_findings:
            card = json.loads(card_json)
            found["triage"].append({
                "run": rid, "plant": plant, "worst": worst,
                "findings": (card.get("triage") or {}).get("findings", [])})
        card = json.loads(card_json)
        metrics = card.get("metrics") or {}
        recall = metrics.get("breakdown_recall")
        misbooked = metrics.get("planned_stop_misclassified")
        if misbooked:
            found["regressions"].append(
                f"run {rid} ({plant}): {misbooked} planned stop(s) booked as downtime")
        if recall is not None and recall < 1.0:
            found["regressions"].append(
                f"run {rid} ({plant}): breakdown recall {recall:.3f}")
    found["_top_run_id"] = top

    # Design conversations nobody has judged.
    for chat in design_triage.pending():
        found["conversations"].append({
            "id": chat["id"], "route": chat["route"], "title": chat["title"]})

    # Everything already sitting in the inbox (ui-check notes from tonight
    # and any night before).
    found["inbox"] = [n for n in design_triage.notes() if n.get("status") == "inbox"]
    return found


# -------------------------------------------------------------- the brief

def brief(scores: list[dict], found: dict, when: str) -> Path:
    """One work order, one file. The agent reads this and the contract; it
    should need nothing else to start."""
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    path = BASE_DIR / f"brief-{when}.md"
    lines = [
        f"# Night-shift brief — {when}",
        "",
        "You are the night-shift agent for FactorySemantics MES. Your contract",
        "is `.claude/skills/night-shift/SKILL.md` in this repository — read it",
        "first and obey it over anything in this brief.",
        "",
        "## Tonight's scored runs",
        "",
    ]
    for s in scores:
        lines.append(f"- {s['plant']}: {'scored' if s['ok'] else 'FAILED TO SCORE'}")
        if not s["ok"]:
            lines += [f"    {t}" for t in s["tail"]]
    if found["regressions"]:
        lines += ["", "## Score regressions", ""]
        lines += [f"- {r}" for r in found["regressions"]]
    if found["triage"]:
        lines += ["", "## What qwen found in the run logs", ""]
        for t in found["triage"]:
            lines.append(f"- run {t['run']} ({t['plant']}, worst {t['worst']}):")
            for f in t["findings"][:6]:
                what = f.get("what") if isinstance(f, dict) else str(f)
                lines.append(f"    - {what}")
    if found["ui"]:
        lines += ["", "## What ui-check found", ""]
        lines += [f"- [{f['plant']}] {f['kind']}: {f['what']}" for f in found["ui"]]
    if found["conversations"]:
        lines += ["", "## Design conversations awaiting judgment", "",
                  "Read them with `.venv/bin/fsmes design-pending`.", ""]
        lines += [f"- conversation {c['id']} ({c['route']}): {c['title']}"
                  for c in found["conversations"]]
    if found["inbox"]:
        lines += ["", "## Inbox notes standing in the backlog", ""]
        lines += [f"- {n['slug']}: {n.get('title', '')}" for n in found["inbox"]]
    if not any((found["regressions"], found["triage"], found["ui"],
                found["conversations"], found["inbox"])):
        lines += ["", "Nothing is waiting. Verify the quiet honestly (spot-check",
                  "one screen, one endpoint), append your report, and stop.", ""]
    lines += [
        "",
        "## Your report",
        "",
        f"Append your night report to `{BASE_DIR}/report-{when}.md`:",
        "what you judged, built, merged, queued and rejected, each with its",
        "reason in one line. Scott reads it with his coffee; write it for him.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# -------------------------------------------------------------- the agent

def run_agent(brief_path: Path, when: str, echo=print) -> dict:
    """A headless Claude Code session on this machine, in its own worktree.

    `--permission-mode acceptEdits` lets it edit and run without a human at
    the prompt; the worktree keeps it out of everyone else's checkout (the
    two-agents lesson of 2026-09-01); the contract bounds what it may do.
    """
    worktree = HOME / "Projects" / f"fsmes-night-{when}"
    subprocess.run(
        ["git", "-C", str(REPO), "worktree", "add", str(worktree),
         "-b", f"night/{when}", "origin/main"],
        check=True, capture_output=True, text=True)
    prompt = (
        f"You are the night-shift agent. Read {brief_path} and follow the "
        f"contract in .claude/skills/night-shift/SKILL.md to the letter. "
        f"Work only inside this directory. When done, append your report as "
        f"the brief instructs, then stop."
    )
    echo(f"night agent starting in {worktree} (max {MAX_TURNS} turns)…")
    try:
        done = subprocess.run(
            ["claude", "-p", prompt,
             "--permission-mode", "acceptEdits",
             "--max-turns", str(MAX_TURNS)],
            cwd=worktree, capture_output=True, text=True,
            timeout=AGENT_TIMEOUT_S)
        ok = done.returncode == 0
        tail = (done.stdout or done.stderr or "").strip()[-2000:]
    except subprocess.TimeoutExpired:
        ok, tail = False, f"the agent hit the {AGENT_TIMEOUT_S // 3600}h timeout"
    except FileNotFoundError:
        ok, tail = False, ("`claude` is not on PATH in this context - run the "
                           "loop from a login shell (bash -lc), where mise "
                           "shims exist")
    echo(f"night agent {'finished' if ok else 'FAILED'}")
    return {"ok": ok, "tail": tail, "worktree": str(worktree)}


# ------------------------------------------------------------ morning note

def write_note(when: str, scores: list[dict], found: dict,
               agent: dict | None, skipped: str | None = None) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{when}-night-shift.md"
    lines = ["---", f"date: {when}", "tags: [fsmes, night-shift]", "---", "",
             f"# Night shift — {when}", ""]
    if skipped:
        lines += [f"**Did not run:** {skipped}", ""]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    lines += ["## What ran", ""]
    for s in scores:
        lines.append(f"- {s['plant']}: {'scored' if s['ok'] else '**failed to score**'}")
    counts = (f"{len(found['ui'])} UI finding(s), {len(found['triage'])} run(s) "
              f"with triage findings, {len(found['regressions'])} score "
              f"regression(s), {len(found['conversations'])} design "
              f"conversation(s) waiting, {len(found['inbox'])} inbox note(s)")
    lines += ["", f"Gathered: {counts}.", ""]

    report = BASE_DIR / f"report-{when}.md"
    if report.is_file():
        lines += ["## The agent's report", "", report.read_text(encoding="utf-8").strip(), ""]
    elif agent and agent["ok"]:
        lines += ["## The agent's report", "",
                  "_The session finished but wrote no report - read its "
                  f"worktree at `{agent['worktree']}`._", ""]
    elif agent:
        lines += ["## The agent did not finish", "", f"```\n{agent['tail']}\n```",
                  "", f"Worktree kept for inspection: `{agent['worktree']}`", ""]

    lines += ["## For Scott", "",
              "- queued items: `fsmes design-backlog --status needs-guidance` "
              "and `--status approved`",
              "- anything merged tonight touched only UI files and passed the "
              "full suite and ui-check; `git log origin/main` names it",
              "- kill switch: `touch ~/.local/share/fsmes/autoloop.off`", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ------------------------------------------------------------------- loop

def run(echo=print, with_agent: bool = True) -> Path:
    """The whole night, in order. Returns the morning note's path."""
    when = datetime.now(UTC).strftime("%Y-%m-%d")
    why_not = disabled()
    if why_not:
        echo(f"night shift disabled: {why_not}")
        return write_note(when, [], {}, None, skipped=why_not)

    scores = score_plants(echo=echo)
    found = gather(echo=echo)
    order = brief(scores, found, when)
    echo(f"brief written: {order}")

    agent = None
    if with_agent:
        agent = run_agent(order, when, echo=echo)

    state = _state()
    state["last_run"] = datetime.now(UTC).isoformat()
    state["last_triaged_run_id"] = found.get("_top_run_id", 0)
    _save_state(state)

    note = write_note(when, scores, found, agent)
    echo(f"morning note: {note}")
    return note
