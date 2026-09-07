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
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

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
