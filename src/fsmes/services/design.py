"""Talk to Claude about the screen you are looking at.

Scott's problem: reviewing this product means noticing something on a screen,
coming back to a terminal, and describing it from memory. The description is
always vaguer than the thing. So the conversation moves onto the screen, and
what he is looking at goes with it - the rendered text, the data the page
fetched, the filters he has set, and the source of the page itself. "How would
a supervisor use this?" is then a question with an answer, because both sides
can see the same thing.

Three deliberate constraints.

It is a development tool, not part of the product. It stores in its own
database rather than the plant's, and it is off unless MES_DESIGN_CHAT is set -
a customer's MES must not accumulate our design notes, and a plant floor is not
a place to be billing an API per message.

The local model routes. "How do I record a check?" is an operator question the
free on-device assistant already answers well; "how should this screen work?"
is a design question worth Claude. Sending everything to the API would be
lazy and expensive.

And every conversation is kept. A design decision argued out on a Tuesday and
lost by Friday is worse than one never made.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

STORE = Path.home() / ".local" / "share" / "fsmes" / "design.db"
OLLAMA = "http://127.0.0.1:11434"
LOCAL_MODEL = "qwen3:8b"
CLAUDE_MODEL = "claude-opus-5"

# The web files that make each screen, so Claude can talk about what is
# actually rendered rather than what it imagines might be.
SCREEN_SOURCE = {
    "/dashboard": ("index.html", "app.js"),
    "/dashboard/orders": ("orders.html", "orders.js"),
    "/dashboard/quality": ("quality.html", "quality.js"),
    "/dashboard/instructions": ("instructions.html", "instructions.js"),
    "/dashboard/analysis": ("analysis.html", "analysis.js"),
    "/dashboard/ops": ("ops.html", "ops.js"),
    "/dashboard/admin": ("admin.html", "admin.js"),
    "/dashboard/line": ("line.html",),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    plant      TEXT,
    route      TEXT NOT NULL,
    title      TEXT,
    who        TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    ts              TEXT NOT NULL,
    role            TEXT NOT NULL,
    text            TEXT NOT NULL,
    model           TEXT,
    -- What was on screen when this was said. Kept because a design note read
    -- six months later is meaningless without the thing it was about.
    context         TEXT
);
CREATE INDEX IF NOT EXISTS turns_conversation ON turns (conversation_id, id);
"""


def enabled() -> bool:
    return os.environ.get("MES_DESIGN_CHAT", "").strip().lower() in ("1", "true", "yes", "on")


@contextmanager
def connect():
    STORE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STORE)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------- the local model

def _ollama(path: str, payload: dict, timeout: float) -> dict | None:
    try:
        req = urllib.request.Request(
            f"{OLLAMA}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def local_generate(prompt: str, timeout: float = 120.0) -> str | None:
    out = _ollama("/api/generate",
                  {"model": LOCAL_MODEL, "prompt": prompt,
                   "stream": False, "think": False}, timeout)
    return (out or {}).get("response", "").strip() or None


def is_design_question(message: str) -> bool:
    """Design question, or an operator's question about running the plant?

    The on-device assistant answers the second kind well and for nothing. Only
    the first is worth a paid model, and the local one is perfectly capable of
    telling them apart.
    """
    reply = local_generate(
        "Classify one message from someone using a manufacturing system.\n\n"
        "DESIGN - they are critiquing or redesigning the software itself: how a "
        "screen should work, what is missing, what a user would need, layout, "
        "wording, workflow.\n"
        "OPERATION - they are trying to run the plant: how to do a task, what "
        "the current numbers are, what a procedure says.\n\n"
        f"Message: {message}\n\n"
        "Reply with one word, DESIGN or OPERATION.",
        timeout=45.0)
    if reply:
        return "DESIGN" in reply.upper()
    # Ollama down: assume design. This surface exists to be talked to about
    # design, and the operator assistant is a click away.
    return True


def compress(text: str, what: str, budget: int = 2500) -> str:
    """Shrink a big page payload before it is sent anywhere.

    The local model earns its keep here: a floor screen's JSON is tens of
    kilobytes of repetition, and paying a frontier model to read all of it is
    a waste. Falls back to plain truncation, which is honest about what it did.
    """
    if len(text) <= budget:
        return text
    summary = local_generate(
        f"Summarise this {what} from a manufacturing system for another "
        f"engineer. Keep concrete numbers, codes and counts - they are the "
        f"point. No preamble, at most 200 words.\n\n{text[:12000]}",
        timeout=90.0)
    if summary:
        return f"[summarised on-device from {len(text)} characters]\n{summary}"
    return text[:budget] + f"\n[...truncated from {len(text)} characters]"


# ------------------------------------------------------------------- context

def read_source(route: str, web_dir: Path, budget: int = 14000) -> str:
    """The code behind the screen, so suggestions can name real things."""
    files = SCREEN_SOURCE.get(route, ())
    chunks = []
    for name in files:
        path = web_dir / name
        if not path.is_file():
            continue
        text = path.read_text(errors="replace", encoding="utf-8")
        chunks.append(f"----- {name} ({len(text)} chars) -----\n{text}")
    joined = "\n\n".join(chunks)
    if len(joined) > budget:
        joined = joined[:budget] + "\n[...source truncated]"
    return joined


def build_prompt(message: str, context: dict, history: list[dict],
                 source: str, claude: bool = True) -> tuple[str, list[dict]]:
    """The system prompt, and the conversation as messages.

    `claude` says which model is about to answer. Claude gets the full
    opinionated-collaborator brief. The on-device model gets a narrower one:
    acknowledge, clarify if genuinely needed, record - nothing more. A design
    partner and an intake clerk are different jobs, and the convergence plan
    already found qwen3:8b weak at the first one; the pipeline's own judgment
    happens later, properly, at /design-triage. Sending the collaborator brief
    to the weaker model was what made its replies wander.
    """
    if claude:
        system = (
            "You are the design partner for FactorySemantics MES, an open-source, "
            "agent-native Manufacturing Execution System. You are talking to Scott, "
            "who is building it, while he looks at one of its screens.\n\n"
            "He is designing this for a real factory: 300 people, 60 machines, two "
            "shifts, seven days a week. Judge everything against that, not against "
            "a demo. If a screen would only work for a small job shop, say so and "
            "say what it would need instead.\n\n"
            "You can see what he sees: the rendered text of the screen, the data it "
            "fetched, and the source that draws it. Use them. Refer to real "
            "elements, real numbers and real files rather than speaking generally. "
            "When you propose a change, be specific enough that it could be built "
            "from your description.\n\n"
            "Be direct. He wants an opinionated collaborator, not validation - if "
            "something he suggests would be worse, say why. Keep replies tight "
            "unless he asks you to go deep."
        )
    else:
        system = (
            "You are recording design feedback for FactorySemantics MES. Scott is "
            "looking at one of its screens and telling you something about it. A "
            "separate, stronger process reviews every idea afterwards against the "
            "real architecture and decides what happens to it - that is not your "
            "job. Yours is only to capture this one well.\n\n"
            "Acknowledge what he said in one short sentence. Ask at most one "
            "clarifying question, and only if the idea is genuinely too vague to "
            "act on later - which screen, which case, what should happen instead. "
            "If it is already clear, ask nothing.\n\n"
            "Never propose a design, never suggest an alternative, never critique "
            "what is on screen unless he asked you to. Two or three sentences, no "
            "more."
        )

    parts = [f"I am looking at {context.get('screen') or context.get('route')}."]
    if context.get("filters"):
        parts.append(f"Filters currently set: {json.dumps(context['filters'])}")
    if context.get("visible"):
        parts.append(f"What the screen is showing right now:\n{context['visible']}")
    if context.get("data"):
        parts.append(f"The data behind it:\n{context['data']}")
    if source:
        parts.append(f"The source that draws this screen:\n{source}")
    parts.append(f"\nMy question: {message}")

    messages = [{"role": t["role"], "content": t["text"]} for t in history]
    messages.append({"role": "user", "content": "\n\n".join(parts)})
    return system, messages


# ------------------------------------------------------------------- answering

def ask_claude(system: str, messages: list[dict]) -> tuple[str, str]:
    """Claude, when a key is configured. Returns (text, model)."""
    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8000,
        system=system,
        messages=messages,
        # A policy decline here would be surprising, but a design conversation
        # that simply stops is worse than one that continues on another model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        return ("I was not able to answer that one.", CLAUDE_MODEL)
    text = "".join(b.text for b in response.content if b.type == "text")
    return text.strip(), CLAUDE_MODEL


def ask_local(system: str, messages: list[dict]) -> tuple[str, str]:
    """The on-device model, when there is no key or Claude is unreachable."""
    conversation = "\n\n".join(
        f"{'Scott' if m['role'] == 'user' else 'You'}: {m['content']}"
        for m in messages[-4:])
    reply = local_generate(f"{system}\n\n{conversation}\n\nYou:", timeout=240.0)
    return (reply or "Neither Claude nor the local model answered.", LOCAL_MODEL)


def claude_available() -> bool:
    # A key in the plant's environment may be there for the floor agent, not
    # for design chat. MES_DESIGN_CLAUDE=0 keeps design questions on the local
    # model even when a key is present.
    if os.environ.get("MES_DESIGN_CLAUDE", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


# ------------------------------------------------------------------- storage

def start(route: str, plant: str | None, who: str | None, title: str) -> int:
    now = datetime.now(UTC).isoformat()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (started_at, updated_at, plant, route, title, who)"
            " VALUES (?,?,?,?,?,?)", (now, now, plant, route, title[:200], who))
        return int(cur.lastrowid)


def add_turn(conversation_id: int, role: str, text: str,
             model: str | None = None, context: dict | None = None) -> None:
    now = datetime.now(UTC).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO turns (conversation_id, ts, role, text, model, context)"
            " VALUES (?,?,?,?,?,?)",
            (conversation_id, now, role, text, model,
             json.dumps(context, default=str) if context else None))
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?",
                     (now, conversation_id))


def history(conversation_id: int, limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT role, text FROM turns WHERE conversation_id = ?"
            " ORDER BY id DESC LIMIT ?", (conversation_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def conversations(route: str | None = None, limit: int = 40) -> list[dict]:
    sql = ("SELECT c.*, (SELECT COUNT(*) FROM turns t WHERE t.conversation_id = c.id)"
           " AS turns FROM conversations c")
    args: list = []
    if route:
        sql += " WHERE c.route = ?"
        args.append(route)
    sql += " ORDER BY c.updated_at DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def transcript(conversation_id: int) -> dict | None:
    with connect() as conn:
        head = conn.execute("SELECT * FROM conversations WHERE id = ?",
                            (conversation_id,)).fetchone()
        if head is None:
            return None
        turns = conn.execute(
            "SELECT ts, role, text, model FROM turns WHERE conversation_id = ?"
            " ORDER BY id", (conversation_id,)).fetchall()
    return {**dict(head), "turns": [dict(t) for t in turns]}
