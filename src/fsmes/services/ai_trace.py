"""The trace: what this plant's AI was asked, what it did, and what it cost.

`agent.py` holds a conversation and `ai_status.py` says which brains are on.
Neither leaves a record a person can read afterwards - the agent's transcript
lives in one browser's `sessionStorage` and dies with the tab, and the only
durable rows were a cost log and an append-only JSON file on the operator's
own disk. On 2026-09-26 that meant a failure Scott hit on his phone could only
be discussed by pasting a screenshot.

This service writes and reads the plant's own copy: one `ai_turns` row per
turn, in the plant's database, behind `audit.read`.

**Three rules it keeps.**

*Writing the trace never costs the person their answer.* `record` is called
after the model has already answered, and every failure in it is swallowed
with a warning - a full disk or a locked database loses the row, never the
reply. The same discipline `agent.log_turn` keeps for the file beside it.

*What the person was shown, and nothing more.* A turn row carries the words
the panel showed and each tool's one-line summary. Never the system prompt,
never a key, never a raw tool payload - see `fsmes.domain.ai_turns` for why
that is a rule about capabilities rather than about tidiness.

*A trace has a horizon, and the plant says what it is.* Rows older than
`[admin] ai_trace_days` are pruned as new ones are written, so the table
cannot grow without bound on a plant nobody administers, and the screen says
the number rather than leaving a reader to guess why last month is missing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import AiTurn

LOGGER = logging.getLogger(__name__)

#: How many rows one read of the trace may return, whatever is asked for. The
#: same reason every other list in this product states its total: a screen that
#: asks for everything on a plant three years old should get a page and a
#: number, not a browser that stops responding.
MAX_ROWS = 500

#: Strings that must never appear in a turn row. The system prompt's opening
#: words and the environment variable the key lives in: a test asserts a
#: recorded turn contains neither, because the one way this table becomes a
#: liability is by quietly starting to carry the prompt.
NEVER_STORED = ("You are the assistant inside FactorySemantics MES",
                "ANTHROPIC_API_KEY")


def record(session: Session, row: dict, *, keep_days: float | None = None) -> AiTurn:
    """Write one turn, and prune anything past the horizon.

    `row` is the shape `agent` builds for its own log line, so the file on
    disk and the row in the plant say the same thing about the same turn by
    construction rather than by agreement.
    """
    turn = AiTurn(
        ts=_ts(row.get("ts")),
        session=str(row.get("session") or "")[:40],
        brain=str(row.get("brain") or "floor")[:20],
        person=str(row.get("user") or row.get("person") or "")[:40],
        model=str(row.get("model") or "")[:60],
        kind=str(row.get("kind") or "reply")[:20],
        asked=str(row.get("asked") or ""),
        said=str(row.get("said") or ""),
        tools=list(row.get("tools") or []),
        proposals=list(row.get("proposals") or []),
        guide=(str(row["guide"])[:60] if row.get("guide") else None),
        guide_steps=row.get("guide_steps"),
        error=(str(row["error"])[:60] if row.get("error") else None),
        input_tokens=int(row.get("input") or 0),
        output_tokens=int(row.get("output") or 0),
        cache_read_tokens=int(row.get("cache_read") or 0),
        cache_write_tokens=int(row.get("cache_write") or 0),
        usd=float(row.get("usd") or 0.0),
    )
    session.add(turn)
    session.flush()
    if keep_days is not None:
        prune(session, keep_days)
    return turn


def record_quietly(session: Session, row: dict, *, keep_days: float | None = None) -> None:
    """`record`, for the callers whose job is answering a person.

    A trace that cannot be written is a fact worth a line in the plant's log
    and worth nothing at all to the person waiting for their answer, so it
    never reaches them.
    """
    try:
        record(session, row, keep_days=keep_days)
    except Exception as exc:  # the answer outranks the record
        LOGGER.warning("ai trace: this turn was not recorded (%s)", type(exc).__name__,
                       exc_info=exc)


def prune(session: Session, keep_days: float) -> int:
    """Drop turns older than the plant's horizon. Returns rows deleted.

    Zero or less keeps everything: a plant that wants its whole AI history is
    entitled to it, and "keep nothing" would be a setting whose only effect is
    to make the screen lie about what it has.
    """
    if keep_days is None or keep_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=float(keep_days))
    result = session.execute(delete(AiTurn).where(AiTurn.ts < cutoff))
    session.flush()
    return int(result.rowcount or 0)


# ------------------------------------------------------------------ reading

def _ts(value) -> datetime:
    """Naive UTC, whatever the caller had. `agent` stamps its rows with an
    aware ISO string; this table is naive UTC like every other in the plant."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return utcnow()
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    return utcnow()


def _public(turn: AiTurn) -> dict:
    return {
        "id": turn.id,
        "ts": turn.ts,
        "session": turn.session,
        "brain": turn.brain,
        "person": turn.person,
        "model": turn.model,
        "kind": turn.kind,
        "asked": turn.asked,
        "said": turn.said,
        "tools": turn.tools or [],
        "proposals": turn.proposals or [],
        "guide": turn.guide,
        "guide_steps": turn.guide_steps,
        "error": turn.error,
        "tokens": {"input": turn.input_tokens, "output": turn.output_tokens,
                   "cache_read": turn.cache_read_tokens,
                   "cache_write": turn.cache_write_tokens},
        "usd": round(turn.usd, 6),
    }


def turns(session: Session, *, conversation: str | None = None,
          person: str | None = None, since: datetime | None = None,
          limit: int = 100) -> dict:
    """Turns, newest first - or oldest first when one conversation is named,
    because a conversation is read in the order it happened.

    Always with the plant's own `total` beside them: house rule, and the one
    that matters most here, because the whole reason this table exists is a
    list that was cut without saying so.
    """
    where = []
    if conversation:
        where.append(AiTurn.session == conversation)
    if person:
        where.append(AiTurn.person == person)
    if since is not None:
        where.append(AiTurn.ts >= since)

    total = session.scalar(select(func.count()).select_from(AiTurn).where(*where)) or 0
    kept = max(1, min(int(limit), MAX_ROWS))
    order = ((AiTurn.ts.asc(), AiTurn.id.asc()) if conversation
             else (AiTurn.ts.desc(), AiTurn.id.desc()))
    rows = session.scalars(select(AiTurn).where(*where).order_by(*order).limit(kept)).all()
    return {"total": int(total), "showing": len(rows),
            "turns": [_public(t) for t in rows]}


def conversations(session: Session, *, person: str | None = None,
                  since: datetime | None = None, limit: int = 50) -> dict:
    """One row per conversation: who, when, how many turns, what came of the
    proposals, how many turns failed, and what it cost.

    Built from the turns rather than from a second table, so a conversation
    cannot exist that has no turns in it and a count cannot drift from the
    rows it counts.
    """
    where = []
    if person:
        where.append(AiTurn.person == person)
    if since is not None:
        where.append(AiTurn.ts >= since)

    total = session.scalar(
        select(func.count(func.distinct(AiTurn.session))).select_from(AiTurn).where(*where)) or 0
    kept = max(1, min(int(limit), MAX_ROWS))

    # Newest by when it happened, not by when its rows were inserted. They
    # agree on a plant and disagree the moment anything is backfilled, and a
    # list a person reads as "most recent first" has to mean the clock.
    newest = (select(AiTurn.session.label("session"),
                     func.max(AiTurn.ts).label("last_ts"))
              .where(*where).group_by(AiTurn.session)
              .order_by(func.max(AiTurn.ts).desc(), AiTurn.session.desc())
              .limit(kept)).subquery()
    wanted = [row.session for row in session.execute(select(newest.c.session))]
    if not wanted:
        return {"total": int(total), "showing": 0, "conversations": []}

    rows = session.scalars(
        select(AiTurn).where(AiTurn.session.in_(wanted), *where)
        .order_by(AiTurn.ts.asc(), AiTurn.id.asc())).all()

    grouped: dict[str, list[AiTurn]] = {}
    for turn in rows:
        grouped.setdefault(turn.session, []).append(turn)

    out = [_summarise(sid, grouped[sid]) for sid in grouped]
    out.sort(key=lambda c: (c["last"], c["session"]), reverse=True)
    return {"total": int(total), "showing": len(out), "conversations": out}


def _summarise(session_id: str, rows: list[AiTurn]) -> dict:
    outcomes: dict[str, int] = {}
    for turn in rows:
        for proposal in turn.proposals or []:
            outcome = str(proposal.get("outcome") or "open")
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
    opened = next((t for t in rows if t.asked), rows[0])
    return {
        "session": session_id,
        "brain": rows[0].brain,
        "person": rows[0].person,
        "model": next((t.model for t in rows if t.model), ""),
        "started": rows[0].ts,
        "last": rows[-1].ts,
        "turns": len(rows),
        "opened_with": opened.asked or "(pressed a button)",
        "proposals": outcomes,
        "errors": sum(1 for t in rows if t.error),
        "tool_calls": sum(len(t.tools or []) for t in rows),
        "walks": sum(1 for t in rows if t.guide),
        "usd": round(sum(t.usd for t in rows), 6),
    }


def spend(session: Session, *, since: datetime | None = None) -> float:
    """What this plant's conversations have cost, over the rows it still has.

    Not the same number as `agent.spend_this_month()`, which reads the box's
    own usage file and counts every plant on it. This one is the plant's, over
    a trace that is pruned - so it is a floor, and the screen says so rather
    than presenting it as the bill.
    """
    where = [AiTurn.ts >= since] if since is not None else []
    return round(session.scalar(
        select(func.coalesce(func.sum(AiTurn.usd), 0.0)).where(*where)) or 0.0, 6)
