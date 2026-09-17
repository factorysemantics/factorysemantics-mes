"""Reading what the plants wrote, looking for what nobody asserted.

The scorecard answers questions we thought to ask: was the changeover
misclassified, was the breakdown detected. It cannot answer the one that
matters most on a bad day - "is there something wrong in here that nobody
wrote a test for". Deadlocks, stale counters, silent exceptions, impossible
numbers: the failures you find by reading, and nobody reads forty JSON files.

So the local model reads every scored run's log and files what it finds. It is
looking for anomalies, not writing prose, and it is told to say "nothing" when
there is nothing - a triage pass that always finds something is a triage pass
nobody trusts.

Two passes read the same log, and both are recorded. The open one above asks
a local model for prose and scrapes JSON out of the reply, which is the only
thing it can do and is also its weakness: an unparseable reply is recorded as
no findings, so a clean run and a parse failure look identical. The second,
at the bottom of this file, asks a hosted judgment model a fixed battery of
typed questions - five conditions and one severity - where there is nothing
to parse and therefore nothing to misparse. It is off unless somebody sets a
key, it decides nothing, and the point of running both is the comparison.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from fsmes.integrations.jev import (
    JevUnavailable,
    Noul,
    QuestionSet,
    Score,
    from_settings,
)

OLLAMA = "http://127.0.0.1:11434"
CHAT_MODEL = "qwen3:8b"
EMBED_MODEL = "nomic-embed-text"

# The tail is what matters: a run that went wrong went wrong at the end.
LOG_TAIL_CHARS = 12_000

PROMPT = """You are reading the log of one simulated manufacturing run, looking
for problems nobody wrote a test for.

Report only things that are actually wrong or genuinely suspicious:
exceptions, stack traces, retry storms, connections dropping repeatedly,
counters going backwards, impossible or absurd values, a component that stops
reporting, timeouts, deadlocks.

Do NOT report normal operation. Do NOT restate the metrics. Do NOT speculate
about causes you cannot see in the log.

Reply with a JSON array of findings, each {"severity": "high"|"medium"|"low",
"what": "<one sentence>", "evidence": "<a short quote from the log>"}.
Reply with exactly [] if the log looks healthy. No prose outside the JSON.

Run: <<NAME>>
Scored: <<SUMMARY>>

Log tail:
<<LOG>>
"""


def _post(path: str, payload: dict, timeout: float) -> dict | None:
    try:
        req = urllib.request.Request(
            f"{OLLAMA}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _generate(prompt: str, timeout: float = 240.0) -> str | None:
    out = _post("/api/generate",
                {"model": CHAT_MODEL, "prompt": prompt, "stream": False, "think": False},
                timeout)
    return (out or {}).get("response", "").strip() or None


def embed(text: str, timeout: float = 60.0) -> list[float] | None:
    """A vector for one piece of text, for 'have we seen this before'."""
    out = _post("/api/embed", {"model": EMBED_MODEL, "input": text[:8000]}, timeout)
    vectors = (out or {}).get("embeddings") or []
    return list(vectors[0]) if vectors else None


def _parse_findings(reply: str) -> list[dict]:
    """Pull the JSON array out of whatever the model wrapped it in.

    Small models fence their JSON, or preface it, or both. Failing to parse
    must mean "no findings recorded", never a crash in a nightly job.
    """
    if not reply:
        return []
    match = re.search(r"\[.*\]", reply, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []

    findings = []
    for item in parsed:
        if not isinstance(item, dict) or not item.get("what"):
            continue
        severity = str(item.get("severity", "low")).lower()
        findings.append({
            "severity": severity if severity in ("high", "medium", "low") else "low",
            "what": str(item["what"])[:300],
            "evidence": str(item.get("evidence", ""))[:300],
        })
    return findings


def read_log(evidence_dir: Path | str | None) -> str:
    """The run's own log, if its evidence was kept."""
    if not evidence_dir:
        return ""
    path = Path(evidence_dir) / "run.log"
    if not path.is_file():
        return ""
    try:
        return path.read_text(errors="replace", encoding="utf-8")[-LOG_TAIL_CHARS:]
    except OSError:
        return ""


def triage(card: dict, log: str) -> dict:
    """Read one run's log and file what is wrong with it."""
    if not log.strip():
        return {"findings": [], "note": "no log kept for this run"}

    metrics = card.get("metrics", {})
    # Placeholder substitution rather than str.format: the prompt itself
    # contains a JSON example, and format() read its braces as fields.
    prompt = (PROMPT
              .replace("<<NAME>>", f"{card.get('plant')} at {card.get('speed')}x")
              .replace("<<SUMMARY>>", json.dumps(
                  {k: metrics.get(k) for k in
                   ("breakdown_recall", "planned_stop_misclassified")}))
              .replace("<<LOG>>", log))
    reply = _generate(prompt)
    if reply is None:
        # Ollama being down must not fail the run that produced the log.
        return {"findings": [], "note": "the local model did not answer"}

    findings = _parse_findings(reply)
    return {
        "findings": findings,
        "worst": max((f["severity"] for f in findings),
                     key=lambda s: ("low", "medium", "high").index(s), default=None),
        "model": CHAT_MODEL,
    }


# --------------------------------------------------------------------------
# The second opinion: the same log, asked fixed questions instead of an open
# one.
#
# The pass above is the one that has been load-bearing since it was written,
# and it stays exactly as it is. What it cannot do is tell a clean run from a
# reply it failed to parse: `_parse_findings` returns [] for both, so "no
# findings" has meant two different things for as long as it has existed.
#
# A battery of typed questions has no reply to parse, so that ambiguity is
# gone - but it buys that by being fixed, and a fixed battery cannot be
# surprised. A failure nobody wrote a question for is invisible to it and an
# open prompt might still catch it. So both run, both are recorded, and the
# comparison per run is the evidence for which is worth keeping. Neither
# feeds `worst`, the store's columns, the night brief's selection or anything
# `autoloop.py` reads: a judgment is a proposal (decision 0031).
#
# No threshold. A probability is recorded and read by a person. Turning one
# into a verdict needs a calibration plot drawn on this project's own runs,
# and that plot does not exist yet.

#: **Six questions: five conditions and one severity.** That is the whole
#: battery, stated because a fixed battery's total is the measure of what it
#: cannot see, and a reader who does not know the total cannot judge that.
JEV_QUESTIONS = QuestionSet(
    name="run-log-triage",
    # What the state is, not where it came from: a run log is observation
    # shaped, even though the plant that produced it is simulated. Decision
    # 0032's vocabulary.
    state_class="observation",
    nouls=(
        Noul("retry_storm",
             "The log shows a retry storm: one operation failing and being "
             "retried over and over rather than a handful of isolated retries."),
        Noul("counter_went_backwards",
             "A counter in this log went backwards or reset to zero without "
             "the run itself restarting."),
        Noul("component_stopped_reporting",
             "A component that had been logging regularly stopped logging "
             "before the run ended, while the rest of the run carried on."),
        Noul("silent_exception",
             "An exception or a stack trace was logged and then swallowed: "
             "the run carried on as though nothing had happened."),
        Noul("deadlock",
             "Two or more parts of the system were waiting on each other and "
             "stopped making progress."),
    ),
    score=Score(
        "worst_problem",
        "How serious is the worst problem visible in this log?",
        levels=(
            ("none", "Nothing is wrong. Normal operation from start to finish."),
            ("low", "Something is untidy or worth a glance, and no number the "
                    "run produced is in doubt because of it."),
            ("medium", "Something is wrong: a component misbehaved, work was "
                       "retried or lost, or part of the run is not trustworthy."),
            ("high", "Something is badly wrong: the run crashed, stalled, or "
                     "produced numbers that cannot be believed."),
        ),
    ),
)

#: The qwen pass records no severity at all for a clean run. The battery has
#: a word for that, so the two are compared in the battery's vocabulary.
NO_PROBLEM = "none"


def judge(card: dict, log: str, *, transport=None, settings=None) -> dict:
    """Ask the battery about the same log, and record what came back.

    Never raises and never changes anything the pass above produced. Every
    way of not being asked - no key, shadow mode, no log, no SDK, no answer -
    is recorded as a sentence saying which, because "nothing here" and "not
    asked" are the two things this whole change exists to keep apart.
    """
    def not_asked(why: str) -> dict:
        record = {"asked": False, "note": f"not asked ({why})"}
        record["comparison"] = compare(card.get("triage") or {}, record)
        return record

    if not log.strip():
        return not_asked("no log kept for this run")

    client, why = from_settings(settings, transport=transport)
    if client is None:
        return not_asked(why)

    try:
        answers = client.ask(JEV_QUESTIONS, log)
    except (JevUnavailable, RuntimeError, ValueError, OSError) as exc:
        # A judgment nobody could get is not a finding, and it is not a
        # crash in a nightly job either.
        return not_asked(str(exc))

    by_name = {a.question: a for a in answers}
    score = by_name.pop(JEV_QUESTIONS.score.name, None)
    record = {
        "asked": True,
        "note": "asked and answered",
        "state_class": JEV_QUESTIONS.state_class,
        "battery": JEV_QUESTIONS.name,
        "model_asked_for": client.model,
        "model": score.model if score else next(iter(answers)).model,
        "conditions": [a.as_record() for a in answers
                       if a.question != JEV_QUESTIONS.score.name],
        "worst_problem": score.as_record() if score else None,
        # Said in the record rather than only in a docstring, because the
        # record is what a person reads in six weeks.
        "thresholds": "none: a probability is recorded, not a verdict",
    }
    record["comparison"] = compare(card.get("triage") or {}, record)
    return record


def compare(qwen: dict, jev: dict) -> dict:
    """Where the two passes agree about one run, and where they do not.

    Evidence, not a verdict. Nothing reads this to decide anything; it exists
    so that after a few weeks of runs there is something to read rather than
    an argument about which pass sounds better.
    """
    findings = qwen.get("findings") or []
    qwen_worst = qwen.get("worst") or NO_PROBLEM
    if not jev.get("asked"):
        return {"agree": None,
                "qwen_findings": len(findings),
                "qwen_worst": qwen_worst,
                "line": f"qwen: {len(findings)} finding(s), worst {qwen_worst}. "
                        f"jev: {jev.get('note', 'not asked')}."}

    scored = jev.get("worst_problem") or {}
    jev_worst = scored.get("level") or NO_PROBLEM
    conditions = jev.get("conditions") or []
    ranked = sorted(conditions,
                    key=lambda c: (c.get("probability") is not None,
                                   c.get("probability") or 0.0),
                    reverse=True)
    highest = ranked[0] if ranked else None
    said_something = [bool(findings), jev_worst != NO_PROBLEM]
    line = (f"qwen: {len(findings)} finding(s), worst {qwen_worst}. "
            f"jev: worst {jev_worst}")
    if highest:
        line += (f", highest condition {highest['question']} at "
                 f"{highest['probability']}")
    line += ". "
    if all(said_something):
        line += ("Both found something" if qwen_worst == jev_worst
                 else f"Both found something, at different severities "
                      f"({qwen_worst} against {jev_worst})")
    elif not any(said_something):
        line += "Both read the run as clean"
    else:
        line += ("Only qwen found something" if findings
                 else "Only the battery found something")
    return {
        # Agreement on whether there is anything here at all. Severity is
        # reported beside it rather than folded in, because the two passes
        # do not choose their severities the same way and pretending they do
        # would make the weeks of evidence unreadable.
        "agree": said_something[0] == said_something[1],
        "qwen_findings": len(findings),
        "qwen_worst": qwen_worst,
        "jev_worst": jev_worst,
        "jev_highest_condition": (
            {"question": highest["question"], "probability": highest["probability"]}
            if highest else None),
        "line": line + ".",
    }
