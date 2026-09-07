"""The nightly read: what the day's simulations said, in one note.

A scorecard per run is more than anyone will read. This sweeps the day, puts
each metric next to where it stood before, and asks the local model to say
what changed in plain language - one thing to look at each morning instead of
forty JSON files.

The model summarises; it does not decide. Every number in the note is computed
here from the results store, and the model is given those numbers rather than
asked to derive them, because a model that does arithmetic on your behalf is a
model that will eventually get it wrong quietly.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fsmes.sim import store

OLLAMA = "http://127.0.0.1:11434"
CHAT_MODEL = "qwen3:8b"
REPORTS = Path.home() / ".local" / "share" / "fsmes" / "reports"


def _window(days: int) -> tuple[str, str]:
    now = datetime.now(UTC)
    return (now - timedelta(days=days)).isoformat(), now.isoformat()


def gather(days: int = 1, path: Path | None = None) -> dict:
    """The day's runs, and the same metrics before it, computed not guessed."""
    since, until = _window(days)
    prior_since, _ = _window(days * 2)

    with store.connect(path) as conn:
        today = [dict(r) for r in conn.execute(
            "SELECT * FROM runs WHERE scored_at >= ? AND scored_at <= ?"
            " ORDER BY id", (since, until))]
        prior = [dict(r) for r in conn.execute(
            "SELECT * FROM runs WHERE scored_at >= ? AND scored_at < ?"
            " ORDER BY id", (prior_since, since))]

    def summarise(rows: list[dict]) -> dict:
        by_plant: dict[str, dict] = {}
        for row in rows:
            entry = by_plant.setdefault(row["plant"], {
                "runs": 0, "recalls": [], "misclassified": 0, "lags": [],
                "unknown_recall": 0})
            entry["runs"] += 1
            if row["breakdown_recall"] is None:
                entry["unknown_recall"] += 1
            else:
                entry["recalls"].append(row["breakdown_recall"])
            entry["misclassified"] += (row["planned_stop_misclassified"] or 0)
            if row["mean_lag_sim_s"] is not None:
                entry["lags"].append(row["mean_lag_sim_s"])
        out = {}
        for plant, e in by_plant.items():
            out[plant] = {
                "runs": e["runs"],
                # Honest about coverage: a mean over three of twenty runs is
                # not the same claim as a mean over twenty.
                "scored_recall_runs": len(e["recalls"]),
                "unknown_recall_runs": e["unknown_recall"],
                "mean_recall": round(sum(e["recalls"]) / len(e["recalls"]), 3)
                               if e["recalls"] else None,
                "worst_recall": min(e["recalls"]) if e["recalls"] else None,
                "planned_stops_misclassified": e["misclassified"],
                "mean_lag_sim_s": round(sum(e["lags"]) / len(e["lags"]), 1)
                                  if e["lags"] else None,
            }
        return out

    now_stats, prior_stats = summarise(today), summarise(prior)

    changes = []
    for plant, now in now_stats.items():
        was = prior_stats.get(plant)
        if not was:
            changes.append(f"{plant}: first runs in this window ({now['runs']})")
            continue
        if now["mean_recall"] is not None and was["mean_recall"] is not None:
            delta = round(now["mean_recall"] - was["mean_recall"], 3)
            if abs(delta) >= 0.01:
                direction = "up" if delta > 0 else "DOWN"
                changes.append(
                    f"{plant}: breakdown recall {direction} {abs(delta):+.3f} "
                    f"({was['mean_recall']} -> {now['mean_recall']})")
        if now["planned_stops_misclassified"] > was["planned_stops_misclassified"]:
            changes.append(
                f"{plant}: planned stops booked as downtime rose to "
                f"{now['planned_stops_misclassified']} - this destroys availability")
        if (now["mean_lag_sim_s"] is not None and was["mean_lag_sim_s"] is not None
                and now["mean_lag_sim_s"] > was["mean_lag_sim_s"] * 1.25):
            changes.append(
                f"{plant}: detection lag grew {was['mean_lag_sim_s']}s -> "
                f"{now['mean_lag_sim_s']}s of line time")

    return {
        "window_days": days, "since": since, "until": until,
        "runs_today": len(today), "runs_prior_window": len(prior),
        "plants": now_stats, "prior": prior_stats,
        "changes": changes or ["nothing moved"],
        # What the model found by reading the logs - problems nobody wrote a
        # test for, which is the half of the picture a scorecard cannot show.
        "triage": {
            "runs_with_findings": sum(1 for r in today if r.get("triage_findings")),
            "high_severity_runs": sum(1 for r in today
                                      if r.get("triage_worst") == "high"),
            "findings": [
                {"id": r["id"], "plant": r["plant"], "worst": r["triage_worst"],
                 "what": [f["what"] for f in
                          (json.loads(r["scorecard"]).get("triage") or {}).get("findings", [])][:3]}
                for r in today if r.get("triage_findings")
            ][:6],
        },
        "failures": [
            {"id": r["id"], "plant": r["plant"], "variant": r["variant"],
             "misclassified": r["planned_stop_misclassified"],
             "recall": r["breakdown_recall"]}
            for r in today
            if (r["planned_stop_misclassified"] or 0) > 0
            or (r["breakdown_recall"] is not None and r["breakdown_recall"] < 1.0)
        ],
    }


def ui_drift() -> int | None:
    """How much UI drift stands unaccepted - None when ui-check has never
    run, which is unknown, not zero."""
    from fsmes.sim import ui_check

    return ui_check.unaccepted_count()


def narrate(facts: dict, timeout: float = 120.0) -> str | None:
    """Ask the local model to say what the numbers mean. Best effort."""
    prompt = (
        "You are reporting on an overnight batch of manufacturing-execution-system "
        "simulations to the engineer who builds the system.\n\n"
        "Every number below was computed from recorded results. Do not calculate "
        "anything new, do not invent figures, and do not restate the whole table. "
        "Write at most six sentences of plain prose saying what changed and what "
        "is worth looking at. If nothing moved, say so in one sentence.\n\n"
        "Context for the metrics:\n"
        "- breakdown recall: how much of a scripted machine failure the MES saw. "
        "1.0 is perfect.\n"
        "- planned stops misclassified: a planned changeover booked as downtime. "
        "Any number above zero is a real defect - it silently ruins every "
        "availability figure the plant reports.\n"
        "- detection lag: how late the MES noticed, in seconds of line time. "
        "Growing lag is a regression even if recall is unchanged.\n"
        "- triage: what the local model found by reading run logs - problems "
        "nobody wrote a test for. A high-severity finding is worth naming.\n\n"
        f"{json.dumps(facts, indent=1)}"
    )
    payload = {"model": CHAT_MODEL, "prompt": prompt, "stream": False, "think": False}
    try:
        req = urllib.request.Request(
            f"{OLLAMA}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r).get("response", "").strip() or None
    except Exception:
        # A missing narrator must not cost you the numbers.
        return None


def _ui_drift_lines() -> list[str]:
    """One short section, only when there is something to say. The UI loop's
    entire claim on the nightly note - and on the GPU - is this paragraph."""
    count = ui_drift()
    if count is None:
        return []          # never run; the loop does not exist yet on this box
    if count == 0:
        return ["", "## UI conformance", "",
                "Clean - every link answers and the screens match the "
                "accepted look."]
    return ["", "## UI conformance", "",
            f"- **{count} finding(s) stand unaccepted** - see "
            f"`fsmes design-backlog --status inbox` and judge them with "
            f"/design-triage. Deliberate restyles are accepted with "
            f"`fsmes ui-check --accept` in the branch that made them."]


def write_report(facts: dict, summary: str | None, out_dir: Path | None = None) -> Path:
    target = Path(out_dir or REPORTS)
    target.mkdir(parents=True, exist_ok=True)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    path = target / f"{day}-sim-rollup.md"

    lines = [
        "---",
        f"date: {day}",
        "tags: [fsmes, simulation, rollup]",
        "---",
        "",
        f"# Simulation rollup — {day}",
        "",
        f"{facts['runs_today']} scored run(s) in the last {facts['window_days']} day(s); "
        f"{facts['runs_prior_window']} in the window before.",
        "",
    ]

    if summary:
        lines += ["## What changed", "", summary, ""]
    else:
        lines += ["## What changed", "",
                  "_The local model did not answer, so this note carries the "
                  "numbers only._", ""]

    lines += ["## Numbers", "",
              "| plant | runs | mean recall | worst | planned stops misbooked | lag (line s) |",
              "|---|---:|---:|---:|---:|---:|"]
    for plant, s in facts["plants"].items():
        recall = "unknown" if s["mean_recall"] is None else f"{s['mean_recall']:.3f}"
        worst = "-" if s["worst_recall"] is None else f"{s['worst_recall']:.3f}"
        lag = "-" if s["mean_lag_sim_s"] is None else f"{s['mean_lag_sim_s']:.1f}"
        lines.append(f"| {plant} | {s['runs']} | {recall} | {worst} "
                     f"| {s['planned_stops_misclassified']} | {lag} |")
        if s["unknown_recall_runs"]:
            lines.append(f"| ↳ _{s['unknown_recall_runs']} run(s) scored unknown "
                         f"(too brief to resolve, or unobserved)_ | | | | | |")

    lines += ["", "## Movement", ""]
    lines += [f"- {c}" for c in facts["changes"]]

    triaged = facts.get("triage") or {}
    if triaged.get("findings"):
        lines += ["", "## Found by reading the logs", "",
                  f"_{triaged['runs_with_findings']} run(s) had findings; "
                  f"{triaged['high_severity_runs']} of high severity._", ""]
        for entry in triaged["findings"]:
            for what in entry["what"]:
                lines.append(f"- run {entry['id']} ({entry['plant']}, "
                             f"{entry['worst']}): {what}")

    if facts["failures"]:
        lines += ["", "## Runs worth opening", ""]
        for f in facts["failures"]:
            lines.append(
                f"- run {f['id']} ({f['plant']}{', ' + f['variant'] if f['variant'] else ''}): "
                f"recall {f['recall']}, planned stops misbooked {f['misclassified']}")

    lines += _ui_drift_lines()

    lines += ["", "---", "",
              "_Written by `fsmes rollup` on main. Numbers computed from the results "
              "store; prose written by the local model from those numbers._", ""]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
