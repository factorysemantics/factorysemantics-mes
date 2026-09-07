"""Agent evals: can an agent, given only the tools, answer what the plant knows?

The scorecard asks whether the MES told the truth about the plant. This
asks the next question: whether an agent working *through the product's
agent surface alone* can find that truth. A scenario is a question and a
way to compute the right answer from the API at the moment of asking; the
agent gets the question and the MCP server, nothing else; the answer is
scored against the truth and kept, so "is the MES getting more usable by
agents" becomes a trend the way honesty already is.

Truth is computed through the same public API the agent uses (the dogfood
rule): if the API cannot answer a question, the scenario cannot exist.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from fsmes import plant as plants

STORE = Path.home() / ".local" / "share" / "fsmes" / "agent-evals.jsonl"
AGENT_USER = "AGENT"
AGENT_PASSWORD = os.environ.get("FSMES_AGENT_PASSWORD", plants.LAB_ONLY_AGENT_PASSWORD)
MCP_URL = os.environ.get("FSMES_MCP_URL", "http://127.0.0.1:8310/mcp")


class Api:
    """One signed-in plant, read through its HTTP API. Anything with a
    `.get(path).json()` will do, which is how the tests hand in a TestClient."""

    def __init__(self, client, code: str = AGENT_USER, password: str = AGENT_PASSWORD):
        self.client = client
        r = client.post("/auth/login", json={"code": code, "password": password})
        if r.status_code != 200:
            raise RuntimeError(f"cannot sign in as {code}: {r.status_code}")
        self.client.headers["Authorization"] = f"Bearer {r.json()['token']}"

    def get(self, path: str):
        r = self.client.get(path)
        r.raise_for_status()
        return r.json()


@dataclass
class Scenario:
    id: str
    question: str            # what the agent is asked; {plant} is filled in
    truth: Callable[[Api], set[str]]   # the right answer(s), from the API, now
    answer_shape: str = "one or more machine codes"
    # How the answer is judged: every expected token must appear in the
    # agent's reply, and none of the listed distractors may.
    distractors: Callable[[Api], set[str]] = field(default=lambda api: set())


# ------------------------------------------------------------- scenarios

def _machines(api: Api) -> list[str]:
    """Every work unit the plant has, whether or not it has reported a state."""
    return sorted(m["code"] for m in api.get("/equipment/tags")["machines"])


def _most_wip(api: Api) -> set[str]:
    w = api.get("/line/wip")
    if not w["stations"]:
        return {"NONE"}
    top = max(w["stations"], key=lambda s: s["wip_qty"])
    return {top["code"]}


def _alarming(api: Api) -> set[str]:
    active = {a["equipment"] for a in api.get("/equipment/alarms") if a["active"]}
    return active or {"NONE"}


def _worst_oee(api: Api) -> set[str]:
    o = api.get("/analysis/oee?hours=8")
    return {o["constraint"]} if o.get("constraint") else {"NONE"}


def _due_maintenance(api: Api) -> set[str]:
    d = api.get("/maintenance/due")
    return {r["equipment"] for r in d["due"]} or {"NONE"}


def _quiet_machines(api: Api) -> set[str]:
    b = api.get("/equipment/tags")
    quiet = {m["code"] for m in b["machines"]
             if m["quiet_for_seconds"] is not None and m["quiet_for_seconds"] > b["stale_after_seconds"]}
    return quiet or {"NONE"}


def _fill_weight_cause(api: Api) -> set[str]:
    """The washer story's truth, from the API: fill-weight checks failed in
    the last hour, and some machine raised the quality-excursion alarm bit
    (bit 2 of the alarm word) in that hour. That machine is the cause; the
    filler, where the checks failed, is the symptom and a distractor."""
    failed = api.get("/quality/checks?characteristic=fill_weight&result=fail&limit=50")["items"]
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    if not any(datetime.fromisoformat(str(c["ts"]).replace("Z", "")) >= cutoff for c in failed):
        return {"NONE"}
    causes = {code for code in _machines(api)
              if any(int(h["word"]) & 4 for h in api.get(f"/equipment/{code}/alarms?hours=1")["history"])}
    return causes or {"NONE"}


def _others(truth: Callable[[Api], set[str]]) -> Callable[[Api], set[str]]:
    def distractors(api: Api) -> set[str]:
        return set(_machines(api)) - truth(api)
    return distractors


SCENARIOS: list[Scenario] = [
    Scenario("most_wip",
             "On plant {plant}, which station on the busiest line currently holds the most work in "
             "progress? Answer with the machine code only, or NONE.",
             _most_wip, distractors=_others(_most_wip)),
    Scenario("alarming",
             "On plant {plant}, which machines currently have any alarm bit set? Answer with the "
             "machine codes only, comma separated, or NONE.",
             _alarming, distractors=_others(_alarming)),
    Scenario("worst_oee",
             "On plant {plant}, which machine on the busiest line has had the worst OEE over the "
             "last 8 hours - the line's constraint? Answer with the machine code only, or NONE.",
             _worst_oee, distractors=_others(_worst_oee)),
    Scenario("due_maintenance",
             "On plant {plant}, which machines have a preventive maintenance plan that is due right "
             "now (not merely due soon)? Answer with the machine codes only, comma separated, or NONE.",
             _due_maintenance, distractors=_others(_due_maintenance)),
    Scenario("quiet",
             "On plant {plant}, which machines have stopped publishing tags - gone quiet for longer "
             "than the staleness threshold? Answer with the machine codes only, comma separated, or NONE.",
             _quiet_machines, distractors=_others(_quiet_machines)),
    Scenario("fill_weight_cause",
             "On plant {plant}, fill-weight quality checks have been failing within the last hour. Which "
             "machine's process excursion caused them? The machine where the checks were taken may be the "
             "symptom rather than the cause. Answer with the machine code only, or NONE.",
             _fill_weight_cause, distractors=_others(_fill_weight_cause)),
]


def scenario(scenario_id: str) -> Scenario:
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    raise KeyError(f"no scenario {scenario_id!r}; known: {', '.join(s.id for s in SCENARIOS)}")


# ---------------------------------------------------------------- scoring

def score(answer: str, truth: set[str], distractors: set[str]) -> dict:
    """1.0 when every expected code is named and no wrong one is; partial
    credit for the fraction of expected codes named, minus nothing - a wrong
    code named alongside the right ones is reported, and fails the scenario,
    because an agent that lists every machine to be safe has not answered."""
    tokens = set(re.findall(r"[A-Z][A-Z0-9_-]{1,}", answer.upper()))
    hit = truth & tokens
    wrong = distractors & tokens
    if truth == {"NONE"}:
        correct = "NONE" in tokens and not wrong
        return {"score": 1.0 if correct else 0.0, "hit": sorted(hit), "wrong": sorted(wrong), "pass": correct}
    fraction = len(hit) / len(truth) if truth else 0.0
    passed = fraction == 1.0 and not wrong
    return {"score": round(fraction if not wrong else 0.0, 3), "hit": sorted(hit),
            "wrong": sorted(wrong), "pass": passed}


# ----------------------------------------------------------------- agents

def claude_agent(prompt: str, *, mcp_url: str = MCP_URL, max_turns: int = 12,
                 timeout: int = 300) -> str:
    """Claude Code, headless, with only the fsmes MCP server - not the user's
    own MCP config, so the eval measures the product's surface and nothing
    else. Needs `claude` on PATH (a login shell on main)."""
    config = json.dumps({"mcpServers": {"fsmes": {"type": "http", "url": mcp_url}}})
    done = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json",
         "--mcp-config", config, "--strict-mcp-config",
         "--allowedTools", "mcp__fsmes__*",
         "--max-turns", str(max_turns)],
        capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout or "claude failed").strip()[-800:])
    try:
        return str(json.loads(done.stdout).get("result", ""))
    except ValueError:
        return done.stdout.strip()


def no_agent(prompt: str) -> str:
    """The dry run: proves the scenarios, truths and scoring without a model."""
    return ""


# -------------------------------------------------------------------- run

def plant_client(name: str, root: Path | None = None) -> httpx.Client:
    where = plants.find_root(root or Path.home() / "Projects" / "factorysemantics-mes")
    cfg = plants.load_registry(where)[name]
    host = cfg.get("api_host", "127.0.0.1")
    return httpx.Client(base_url=f"http://{host}:{cfg['api_port']}", timeout=20.0)


def run(plant: str, *, api: Api, agent: Callable[[str], str], scenarios: list[Scenario] | None = None,
        agent_name: str = "claude", store: Path | None = STORE, echo=print) -> list[dict]:
    """Ask every scenario, score every answer, keep every result."""
    results = []
    for s in scenarios or SCENARIOS:
        truth = s.truth(api)
        distractors = s.distractors(api)
        prompt = (s.question.format(plant=plant)
                  + f" Use only the fsmes tools for plant '{plant}'. Reply with the answer alone "
                  f"({s.answer_shape}), no explanation.")
        started = datetime.now(UTC)
        try:
            answer = agent(prompt)
            error = None
        except Exception as exc:  # a failed agent is a scored zero, not a crash
            answer, error = "", str(exc)[:400]
        judged = score(answer, truth, distractors)
        row = {
            "at": started.isoformat(), "plant": plant, "scenario": s.id, "agent": agent_name,
            "question": s.question.format(plant=plant), "truth": sorted(truth),
            "answer": answer.strip()[:400], "error": error, **judged,
            "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
        }
        results.append(row)
        echo(f"  {s.id:<16} {'PASS' if row['pass'] else 'fail':<5} truth={sorted(truth)} "
             f"answer={row['answer'][:60]!r}" + (f" error={error}" if error else ""))
        if store is not None:
            store.parent.mkdir(parents=True, exist_ok=True)
            with store.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
    return results


def recent(limit: int = 50, store: Path = STORE) -> list[dict]:
    if not store.is_file():
        return []
    lines = store.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[-limit:] if line.strip()]


def summary(rows: list[dict]) -> dict:
    """Pass rate per scenario and overall - the number to trend."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["scenario"], []).append(r)
    per = {k: round(sum(1 for r in v if r["pass"]) / len(v), 3) for k, v in by.items()}
    # Grouped by agent as well: a dry run's zeros must not read as a model's.
    agents: dict[str, list[dict]] = {}
    for r in rows:
        agents.setdefault(str(r.get("agent", "?")), []).append(r)
    by_agent = {k: round(sum(1 for r in v if r["pass"]) / len(v), 3) for k, v in agents.items()}
    return {"runs": len(rows), "pass_rate": round(sum(1 for r in rows if r["pass"]) / len(rows), 3) if rows else None,
            "by_scenario": per, "by_agent": by_agent}
