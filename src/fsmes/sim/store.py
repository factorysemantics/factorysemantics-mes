"""Where scored runs are kept, so a single number can become a trend.

One scorecard answers "is the MES honest right now". A hundred of them answer
"is it getting better", which is the only question that guides a product.

Two retention policies, deliberately opposite:

  understanding  the scorecard, its metrics, the run's description - kilobytes
                 each, kept forever, because a trend you truncated is a trend
                 you cannot see.
  evidence       raw logs and per-run artefacts - megabytes each, pruned on a
                 schedule, because you can always run the scenario again but
                 you cannot re-derive last month's answer.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_STORE = Path.home() / ".local" / "share" / "fsmes" / "runs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plant         TEXT    NOT NULL,
    scored_at     TEXT    NOT NULL,
    speed         REAL    NOT NULL,
    seed          INTEGER,
    duration_s    INTEGER,
    -- The knobs this run was given, so a sweep's points can be told apart.
    variant       TEXT,
    -- Headline metrics, promoted to columns because they are what gets
    -- trended and a JSON extract in every query would be a slow lie.
    planned_stop_misclassified INTEGER,
    breakdown_recall           REAL,
    faults_scored              INTEGER,
    faults_scripted            INTEGER,
    mean_lag_sim_s             REAL,
    -- What the local model found by reading the run's log: things nobody
    -- wrote a test for. Promoted so "which runs had high-severity findings"
    -- is a query rather than a scan of every scorecard.
    triage_findings            INTEGER,
    triage_worst               TEXT,
    -- The whole card, for questions nobody thought to promote.
    scorecard     TEXT    NOT NULL,
    -- Where the evidence lives, and whether it still does.
    evidence_dir  TEXT,
    evidence_pruned_at TEXT
);
CREATE INDEX IF NOT EXISTS runs_plant_time ON runs (plant, scored_at);
CREATE INDEX IF NOT EXISTS runs_time ON runs (scored_at);
"""


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    target = Path(path or DEFAULT_STORE)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _mean_lag(card: dict) -> float | None:
    lags = [f["lag_sim_seconds"] for f in card.get("faults", [])
            if f.get("lag_sim_seconds") is not None]
    return round(sum(lags) / len(lags), 2) if lags else None


def record(card: dict, variant: dict | None = None,
           evidence_dir: Path | None = None, path: Path | None = None) -> int:
    """Keep one scored run. Returns its id."""
    m = card.get("metrics", {})
    # A factory scorecard carries one seed per line (fsmes.sim.truth); the
    # column is text, and sqlite refuses a list outright - which took every
    # factory scored through the CLI or the sim MCP down at the moment of
    # recording, after the whole run had already succeeded.
    seed = card.get("seed")
    if isinstance(seed, (list, tuple)):
        seed = ",".join(str(s) for s in seed)
    with connect(path) as conn:
        cur = conn.execute(
            """INSERT INTO runs (plant, scored_at, speed, seed, duration_s, variant,
                                 planned_stop_misclassified, breakdown_recall,
                                 faults_scored, faults_scripted, mean_lag_sim_s,
                                 triage_findings, triage_worst,
                                 scorecard, evidence_dir)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                card.get("plant"),
                card.get("scored_at") or datetime.now(UTC).isoformat(),
                card.get("speed"),
                seed,
                card.get("duration_s"),
                json.dumps(variant) if variant else None,
                m.get("planned_stop_misclassified"),
                m.get("breakdown_recall"),
                m.get("faults_scored"),
                m.get("faults_scripted"),
                _mean_lag(card),
                # A clean run is 0; a run never triaged is NULL. The first
                # version wrote `len(...) or None`, which collapsed a real
                # zero into unknown - the exact lie principle 4 exists to
                # forbid, pointed the other way. Found during the 2026-09-02
                # observability audit: the only stored run read as untriaged
                # when it had in fact been triaged clean.
                None if "triage" not in card
                else len(card["triage"].get("findings", [])),
                (card.get("triage") or {}).get("worst"),
                json.dumps(card, default=str),
                str(evidence_dir) if evidence_dir else None,
            ),
        )
        return int(cur.lastrowid)


def recent(plant: str | None = None, limit: int = 20,
           path: Path | None = None) -> list[dict]:
    sql = ("SELECT id, plant, scored_at, speed, variant, planned_stop_misclassified,"
           " breakdown_recall, faults_scored, faults_scripted, mean_lag_sim_s,"
           " triage_findings, triage_worst"
           " FROM runs")
    args: list = []
    if plant:
        sql += " WHERE plant = ?"
        args.append(plant)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect(path) as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def get(run_id: int, path: Path | None = None) -> dict | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["scorecard"] = json.loads(out["scorecard"])
    if out.get("variant"):
        out["variant"] = json.loads(out["variant"])
    return out


def trend(plant: str, metric: str = "breakdown_recall", limit: int = 50,
          path: Path | None = None) -> list[dict]:
    """One metric over time, oldest first — the shape a question about
    'is it getting better' actually needs."""
    allowed = {"breakdown_recall", "planned_stop_misclassified", "mean_lag_sim_s"}
    if metric not in allowed:
        raise ValueError(f"Unknown metric {metric!r}. Known: {', '.join(sorted(allowed))}")
    with connect(path) as conn:
        rows = conn.execute(
            f"SELECT id, scored_at, speed, {metric} AS value FROM runs"
            " WHERE plant = ? ORDER BY id DESC LIMIT ?",
            (plant, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def prune_evidence(older_than_days: int = 30, path: Path | None = None) -> int:
    """Delete raw evidence past its keep-window; the understanding stays.

    Marks what it removed rather than forgetting it happened, so a later
    question about a thin old run gets "the logs were pruned" instead of
    silence that looks like the run never produced any.
    """
    import shutil

    cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
    removed = 0
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT id, evidence_dir FROM runs"
            " WHERE evidence_dir IS NOT NULL AND evidence_pruned_at IS NULL"
            " AND scored_at < ?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            shutil.rmtree(row["evidence_dir"], ignore_errors=True)
            conn.execute("UPDATE runs SET evidence_pruned_at = ? WHERE id = ?",
                         (datetime.now(UTC).isoformat(), row["id"]))
            removed += 1
    return removed
