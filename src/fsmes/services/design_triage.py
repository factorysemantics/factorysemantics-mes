"""Design feedback, turned into work somebody can pick up.

Named `design_triage`, not `triage`: `fsmes.sim.triage` already means
reading a scored run's logs for anomalies nobody asserted. Two different
jobs sharing one word is how the wrong module gets imported at 2am.

The design chat (`fsmes.services.design`) is a capture surface. qwen3:8b on
this machine answers every message for nothing, and every conversation is
kept. What the chat cannot do is decide whether an idea is *right*, or build
it. That judgment needs the architecture, the data schema and the ratified
plan in view at once, which is a Claude Code session's job — so this module is
the machinery around that judgment and deliberately holds none of it itself.
The same reasoning already settled the nightly rollup: an 8B model is weak at
open-ended judgment, so it is given numbers and asked only what changed.

Three things it owns.

**Ideas are notes in this repository, not in the vault.** The chat database,
the code and the agent all live on `main`; a laptop-side backlog would need
two-way sync for every status change. In-repo notes are versioned, travel to
the private remote, and land in the same commit as the work they caused.
`fsmes-reports` mirrors them into Obsidian one way, for reading.

**Every verdict goes back into the conversation that produced it.** An idea
submitted in a chat and answered somewhere else is an idea the person who had
it never hears about again — so a verdict is appended as a turn, and opening
the screen's Design panel shows what happened to what you said.

**The high-water mark is per conversation**, so a triage run that dies half
way loses nothing: the conversations it never reached are still pending. A
single global mark would have silently swallowed them.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from fsmes.services import design

STATE = Path.home() / ".local" / "share" / "fsmes" / "design-triage-state.json"
BACKLOG = Path(__file__).resolve().parents[3] / "docs" / "design" / "backlog"

# Verdict turns are written by a Claude Code session, not by a model the
# server called, and the transcript should not pretend otherwise.
TRIAGE_MODEL = "claude-code-triage"

# What a note's status may say. An unknown one is an error rather than a
# silent no-op — the same rule the sweep knobs already follow, and for the
# same reason: a typo that quietly does nothing is worse than a refusal.
STATUSES = {
    "inbox": "extracted from a conversation, not yet judged",
    "approved": "worth building, not built yet",
    "in-progress": "being built now",
    "built": "implemented on a branch with the suite green — awaiting review and merge",
    "needs-guidance": "sound but underspecified, or it touches the schema;"
                      " the questions are in the note",
    "rejected": "conflicts with something already ratified; the note says what",
    "done": "merged into main",
}

# A branch is not a merge. "built" is deliberately distinct from "done" so a
# backlog cannot claim work is finished while it sits unreviewed.
UNMERGED = "built"


# --------------------------------------------------------------- the mark

def _read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"conversations": {}}


def _write_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def marks() -> dict[str, int]:
    """The last turn judged, per conversation."""
    return {str(k): int(v) for k, v in _read_state().get("conversations", {}).items()}


def advance(conversation_id: int, turn_id: int) -> None:
    """Record that everything up to `turn_id` in this conversation is judged.

    Never moves backwards: re-triaging an old conversation must not make
    newer turns look unjudged.
    """
    state = _read_state()
    seen = state.setdefault("conversations", {})
    key = str(int(conversation_id))
    seen[key] = max(int(seen.get(key, 0)), int(turn_id))
    state["updated"] = datetime.now(UTC).isoformat()
    _write_state(state)


# --------------------------------------------------------------- reading

def _turns(conversation_id: int, after: int = 0) -> list[dict]:
    """Turns with their ids — `design.transcript` does not carry them, and the
    mark is meaningless without them."""
    with design.connect() as conn:
        rows = conn.execute(
            "SELECT id, ts, role, text, model FROM turns"
            " WHERE conversation_id = ? AND id > ? ORDER BY id",
            (int(conversation_id), int(after))).fetchall()
    return [dict(r) for r in rows]


def latest_turn(conversation_id: int) -> int:
    """The newest turn in a conversation, verdicts included — what the mark
    advances to once an idea has been written up."""
    turns = _turns(conversation_id)
    return max((int(t["id"]) for t in turns), default=0)


def pending(everything: bool = False, conversation: int | None = None) -> list[dict]:
    """Conversations carrying turns this pipeline has not judged yet.

    `everything=True` ignores the marks — for re-reading a conversation whose
    triage was interrupted after some of its ideas were already written up.
    """
    seen = {} if everything else marks()
    out = []
    for row in design.conversations(limit=200):
        cid = int(row["id"])
        if conversation is not None and cid != conversation:
            continue
        fresh = _turns(cid, after=int(seen.get(str(cid), 0)))
        if not fresh:
            continue
        out.append({
            "id": cid,
            "route": row["route"],
            "plant": row["plant"],
            "title": row["title"],
            "who": row["who"],
            "started_at": row["started_at"],
            "turns": fresh,
            "last_turn": max(int(t["id"]) for t in fresh),
        })
    return sorted(out, key=lambda c: c["id"])


def record_verdict(conversation_id: int, text: str) -> None:
    """Put the verdict back where the idea was raised."""
    design.add_turn(int(conversation_id), "assistant", text, model=TRIAGE_MODEL)


# --------------------------------------------------------------- the notes

def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60] or "untitled"


def note_path(slug: str, backlog: Path | None = None) -> Path:
    return (backlog or BACKLOG) / f"{slugify(slug)}.md"


def write_note(*, slug: str, title: str, status: str, conversation: int,
               body: str, summary: str = "", route: str | None = None,
               plant: str | None = None, turns: list[int] | None = None,
               branch: str | None = None, backlog: Path | None = None) -> Path:
    """One idea, one file, with enough front matter to query the backlog."""
    if status not in STATUSES:
        raise ValueError(
            f"unknown status {status!r}; expected one of {', '.join(sorted(STATUSES))}")

    path = note_path(slug, backlog)
    path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).date().isoformat()
    created = today
    if path.exists():                    # keep the date the idea first arrived
        created = str(front_matter(path.read_text(encoding="utf-8")).get("created", today))

    head = [
        "---",
        f"title: {title}",
        f"status: {status}",
        f"conversation: {int(conversation)}",
        f"turns: [{', '.join(str(int(t)) for t in (turns or []))}]",
        f"route: {route or ''}",
        f"plant: {plant or ''}",
        f"created: {created}",
        f"updated: {today}",
        f"branch: {branch or ''}",
        "tags: [fsmes, design, backlog]",
        "---",
        "",
        f"# {title}",
        "",
        f"**{status}** — {summary}" if summary else f"**{status}**",
        "",
    ]
    path.write_text("\n".join(head) + body.rstrip() + "\n", encoding="utf-8")
    return path


def front_matter(text: str) -> dict:
    """The `key: value` head of a note. A whole YAML parser would be a
    dependency bought for six scalar fields."""
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("---\n")
    block, _, _ = rest.partition("\n---")
    out: dict = {}
    for line in block.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        out[key.strip()] = value.strip()
    return out


def notes(backlog: Path | None = None) -> list[dict]:
    """Every idea in the backlog, newest first."""
    folder = backlog or BACKLOG
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*.md")):
        if path.name.upper() == "README.MD":
            continue
        meta = front_matter(path.read_text(encoding="utf-8"))
        meta["slug"] = path.stem
        meta["path"] = str(path)
        found.append(meta)
    return sorted(found, key=lambda n: (n.get("updated", ""), n["slug"]), reverse=True)
