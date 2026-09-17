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

Two things read each answer, and both are kept. The first is a rule about
tokens: does the reply name every expected code and no distractor. The
second, further down this file, asks a hosted judgment model the same
question as one typed question and records the probability it answers with.
The trend is drawn from the first. The second is drawn beside it, labelled,
gates nothing, and is off unless somebody sets a key.
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
from fsmes.integrations.jev import (
    Noul,
    QuestionSet,
    from_settings,
    reason,
)

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

#: The answer vocabulary's word for "no machine fits the question right now".
#: It is not a machine code, and `_others` can never put it among the
#: distractors, so it is read in whatever case the agent wrote it.
NO_ANSWER = "NONE"

#: A code carrying one of these cannot be mistaken for an English word.
_NOT_A_WORD = re.compile(r"[0-9_-]")


def names(answer: str, code: str) -> bool:
    """Does this reply name this code?

    Reading an answer means asking that of each code we already care about -
    the expected ones and the distractors - rather than harvesting every
    token in the reply and hoping the wanted ones fall out of it.

    Two rules. A code is named when the answer writes it as a **whole
    token**: `MIX01` is not named by `MIX011`. And case is ignored only for
    a code carrying a digit, a hyphen or an underscore, because `mix01` and
    `fg-pack1` can be nothing but codes; a code made only of letters -
    `DRAWING`, `FINEWIRE` - is an ordinary English word in lowercase, so it
    counts only where the reply writes it in capitals, which is how the
    plant writes it and how the question asks for it.

    That second rule is the fix for a real defect. This was
    `re.findall(r"[A-Z][A-Z0-9_-]{1,}", answer.upper())`, and upper-casing
    the answer before matching an upper-case character class made the class
    inert: every word of two letters or more became a candidate code. An
    agent that named the right machine in a sentence mentioning a distractor
    as a plain word - "DRW01; the drawing area itself is fine" - was scored
    zero for naming a machine it had not named.
    """
    if not code:
        return False
    flags = re.IGNORECASE if (code.upper() == NO_ANSWER or _NOT_A_WORD.search(code)) else 0
    edge = "[A-Za-z0-9_-]"
    return re.search(rf"(?<!{edge}){re.escape(code)}(?!{edge})", answer, flags) is not None


def score(answer: str, truth: set[str], distractors: set[str]) -> dict:
    """1.0 when every expected code is named and no wrong one is; partial
    credit for the fraction of expected codes named, minus nothing - a wrong
    code named alongside the right ones is reported, and fails the scenario,
    because an agent that lists every machine to be safe has not answered."""
    named = {code for code in (truth | distractors) if names(answer, code)}
    hit = truth & named
    wrong = distractors & named
    if truth == {NO_ANSWER}:
        correct = names(answer, NO_ANSWER) and not wrong
        return {"score": 1.0 if correct else 0.0, "hit": sorted(hit), "wrong": sorted(wrong), "pass": correct}
    fraction = len(hit) / len(truth) if truth else 0.0
    passed = fraction == 1.0 and not wrong
    return {"score": round(fraction if not wrong else 0.0, 3), "hit": sorted(hit),
            "wrong": sorted(wrong), "pass": passed}


# ------------------------------------------------ the judgment beside it
#
# The check above is a rule about tokens, and a rule about tokens is partly a
# measure of how an agent chose to format its reply. So a hosted judgment
# model is asked one typed question about the same reply - does it name
# exactly the expected codes and no distractor - and the probability it
# answers with is recorded beside the pass or fail, never in place of it.
#
# The trend this file exists to produce stays on the deterministic value.
# The judgment is drawn beside it, labelled, and decides nothing: decision
# 0031. It is off unless somebody sets a key, which is every installation.
#
# No threshold, so no boolean. The survey's own condition for one is a
# calibration plot drawn on this project's data, and that plot does not
# exist yet. What `summary()` reports instead - the mean probability where
# the check passed, and where it failed - is the beginning of that plot.

#: One question per answer. That is the whole battery, said out loud
#: because a fixed battery's total is the measure of what it cannot see.
JEV_QUESTIONS = QuestionSet(
    name="agent-eval-answer",
    # What the state is, not where it came from. The reply, the question and
    # the expected codes are a reading of a plant at a moment - observation
    # shaped, even though the plant the build loop asks about is simulated.
    state_class="observation",
    nouls=(
        Noul("answered_exactly",
             "The agent's reply names every machine code in the expected "
             "answer and names none of the codes listed as distractors. A "
             "reply that names the right codes in a sentence is still a "
             "reply that names them; a reply that mentions a distractor as "
             "an ordinary English word rather than as the code has not named "
             "it; a reply that hedges between two codes has not named "
             "either."),
    ),
)


def state_of(question: str, truth: set[str], distractors: set[str], answer: str) -> str:
    """What is sent: the question, both code lists with their totals, the reply.

    Nothing else about the plant goes with it, and in the build loop the
    plant is a simulated one.
    """
    expected = ", ".join(sorted(truth))
    others = ", ".join(sorted(distractors))
    return (
        "An agent was asked one question about a manufacturing plant and "
        "could answer only through that plant's own tools. Below are the "
        "question, the answer that was right at the moment of asking, the "
        "codes that would be wrong, and what the agent replied.\n\n"
        f"Question: {question}\n\n"
        f"Expected answer, {len(truth)} code(s) in total: {expected}\n\n"
        f"Distractors, {len(distractors)} code(s) in total, none of which may "
        f"be named: {others or '(none listed)'}\n\n"
        f"The agent's reply:\n{answer.strip()}\n"
    )


def judge(question: str, truth: set[str], distractors: set[str], answer: str,
          *, transport=None, settings=None) -> dict:
    """Ask the one question about one reply, and record what came back.

    Never raises and never changes the score beside it. Every way of not
    being asked - no key, shadow mode, no SDK, an empty reply, no answer -
    is recorded as a sentence saying which, because "not asked" and "the
    model thought the answer was wrong" are two different facts.
    """
    def not_asked(why: str) -> dict:
        return {"asked": False, "note": f"not asked ({why})"}

    if not (answer or "").strip():
        # Nothing to judge, and the check beside it already scored it zero.
        return not_asked("the agent replied with nothing")

    client, why = from_settings(settings, transport=transport)
    if client is None:
        return not_asked(why)

    try:
        judgment = client.ask(JEV_QUESTIONS, state_of(question, truth, distractors, answer))
    except Exception as exc:  # a judgment may not cost the eval
        # A judgment nobody could get is not a finding, and it is not a
        # crash in the middle of an eval run either. Every exception, not a
        # list of the ones anybody thought of: decision 0031 says a judgment
        # may not cost the thing it judges, and on 2026-09-17 one did.
        return not_asked(reason(exc))

    got = judgment.answers[0]
    return {
        "asked": True,
        "note": "asked and answered",
        "state_class": JEV_QUESTIONS.state_class,
        "battery": JEV_QUESTIONS.name,
        "model_asked_for": client.model,
        "model": judgment.model,
        "usage": judgment.usage,
        "answer": got.as_record(),
        # Said in the record and not only in a docstring, because the record
        # is what a person reads in six weeks.
        "thresholds": "none: a probability is recorded, not a verdict",
    }


def probability_of(row: dict) -> float | None:
    """The judgment's probability for one kept result, or None if not asked."""
    jev = row.get("jev") or {}
    if not jev.get("asked"):
        return None
    return (jev.get("answer") or {}).get("probability")


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
    from fsmes.pack import fleet

    cfg = fleet.load(where)[name]
    host = cfg.get("api_host", "127.0.0.1")
    return httpx.Client(base_url=f"http://{host}:{cfg['api_port']}", timeout=20.0)


def run(plant: str, *, api: Api, agent: Callable[[str], str], scenarios: list[Scenario] | None = None,
        agent_name: str = "claude", store: Path | None = STORE, echo=print,
        jev_transport=None, settings=None) -> list[dict]:
    """Ask every scenario, score every answer, keep every result.

    `jev_transport` and `settings` are how a test hands in a judgment
    service. Left alone, the judgment is asked only where a key is set, and
    the whole run behaves as it did before it existed.
    """
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
        asked = s.question.format(plant=plant)
        row = {
            "at": started.isoformat(), "plant": plant, "scenario": s.id, "agent": agent_name,
            "question": asked, "truth": sorted(truth),
            "answer": answer.strip()[:400], "error": error, **judged,
            # Beside the pass or fail above, never in place of it.
            "jev": judge(asked, truth, distractors, answer,
                         transport=jev_transport, settings=settings),
            "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
        }
        results.append(row)
        probability = probability_of(row)
        echo(f"  {s.id:<16} {'PASS' if row['pass'] else 'fail':<5} truth={sorted(truth)} "
             f"answer={row['answer'][:60]!r}"
             + (f" judgment={probability}" if probability is not None else "")
             + (f" error={error}" if error else ""))
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
            "by_scenario": per, "by_agent": by_agent,
            "judgment": judgment_summary(rows)}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def judgment_summary(rows: list[dict]) -> dict:
    """The judgment's side of the report, kept apart from the pass rate.

    Two means rather than one, because the interesting question is not what
    the model thinks on average but whether it thinks the same thing the
    check does. Where the check passed and where it failed, side by side, is
    the beginning of the calibration plot that a threshold would need - and
    until that plot exists there is no threshold here and no boolean.

    `asked` is stated against `of` because a mean over three answers and a
    mean over three hundred are not the same number, and a reader who does
    not know which is which cannot tell.
    """
    asked = [r for r in rows if (r.get("jev") or {}).get("asked")]
    probabilities = [p for p in (probability_of(r) for r in asked) if p is not None]
    when_passed = [p for r in asked
                   if (p := probability_of(r)) is not None and r.get("pass")]
    when_failed = [p for r in asked
                   if (p := probability_of(r)) is not None and not r.get("pass")]
    models = sorted({str((r.get("jev") or {}).get("model") or "") for r in asked} - {""})
    return {
        "asked": len(asked),
        "of": len(rows),
        "models": models,
        "mean_probability": _mean(probabilities),
        "mean_probability_where_the_check_passed": _mean(when_passed),
        "mean_probability_where_the_check_failed": _mean(when_failed),
        "note": "a second opinion recorded beside the pass rate; it gates nothing, "
                "and there is no threshold to turn a probability into a verdict",
    }
