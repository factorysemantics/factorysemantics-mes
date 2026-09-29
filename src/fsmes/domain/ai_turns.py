"""What the plant's AI actually did, one row per turn.

Scott, 2026-09-26: *"These conversations should be traced within the MES as
well."* Twice that week the only record of an assistant failure was a
screenshot he pasted into a chat window. The `BadRequestError` that broke his
afternoon session was logged nowhere at all; the conversation had to be
reconstructed from a cost log, the audit trail and a reading of the code.

This is the record that answers instead. One row is one turn: what the person
asked, what the assistant said back, which tools it called and what each
answered in a sentence, which proposals it made and what became of each,
which walkthrough it put on the screen, what went wrong if anything, and what
the turn cost. `agent-turns.jsonl` beside it is the same line on the operator's
disk; this table is the plant's own copy, in the plant's own database
(decision 0021), which is what makes it readable on a screen by somebody who
will never have a shell on that box.

**What is deliberately not here.** The system prompt, the API key, and the raw
payload of any tool call. A tool's answer is kept as its `_summary()` - the
same sentence the assistant panel already shows the person - because the trace
is a record of *what the person was shown and what was done for them*, not a
second copy of the plant's data lying outside the tables that own it. A trace
that held raw tool payloads would be a way of reading orders, lots and people
through a screen gated on `audit.read` rather than on their own capabilities.

**Shadow mode.** A plant running in shadow writes these rows like any other:
the trace changes nothing in the plant and carries nothing off the box, so
there is nothing for shadow mode to withhold. What shadow mode does withhold
is the cloud brain itself, which is why a shadow plant's rows are the local
model's.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow
from fsmes.domain.common import ShiftStamped

#: The brains that write here. `floor` is the assistant panel's agent - the
#: cloud model with the plant's own tools; `analysis` is the analysis agent,
#: which holds every read tool and no write tool and explains rather than
#: proposes; `design` is the Design button, which talks about the product rather
#: than about the plant. One table rather than three because the question a
#: person brings to this screen is "what has the AI in this plant been doing",
#: and that question does not know which button was pressed.
#:
#: The name is the agent kind's own (`services.agent.KINDS`), so the tab can
#: list one kind's conversations apart from another's without a second table and
#: without a mapping that could disagree with the agent that wrote the row.
BRAINS = ("floor", "analysis", "design")


class AiTurn(ShiftStamped, Base):
    """One exchange between a person and one of this plant's brains."""

    __tablename__ = "ai_turns"
    __table_args__ = (
        # The two ways this table is read: one conversation in order, and the
        # recent turns of a plant (or of one person) newest first.
        Index("ix_ai_turns_session", "session", "id"),
        Index("ix_ai_turns_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)

    #: The conversation this turn belongs to. The agent's in-memory session id
    #: for the floor assistant, the design store's conversation number for the
    #: Design button. Opaque either way: it groups turns and names nothing.
    session: Mapped[str] = mapped_column(String(40))
    #: One of `BRAINS`.
    brain: Mapped[str] = mapped_column(String(20), default="floor")
    #: The person who asked, by account code. Never their password, never a
    #: token; the same code the audit trail writes.
    person: Mapped[str] = mapped_column(String(40), index=True)
    #: Which model answered, as the plant had it configured at the time.
    model: Mapped[str] = mapped_column(String(60), default="")

    #: Which screen the question was asked from, as the route path the browser
    #: was on - `/dashboard/config/engineering`, never the query string. The
    #: panel has always sent it and this table used to drop it, so "which
    #: screen do people ask about" was unanswerable in principle rather than
    #: for want of a better model (design page section 4, milestone D1).
    #:
    #: Null is *not recorded*, never *no screen*: a turn from the design chat
    #: carries a screen the panel names in words rather than a path, and one
    #: replayed by a test or a script was asked from nowhere. Nothing is
    #: backfilled, so every row written before this column existed is null.
    #:
    #: **The query string is stripped before it is stored.** `?setting=x` names
    #: a thing the person was looking at, and a column meant to say *where*
    #: must not become a second copy of *what*.
    screen: Mapped[str | None] = mapped_column(String(80))

    # `shift_code`/`shift_day` (ShiftStamped) are the shift the turn was taken
    # in, resolved from the plant calendar as it stood when the row was written
    # (decision 0028), so "OEE and scrap in a user's shift" can put the
    # questions and the floor tables side by side. Null where no pattern
    # covered the instant, which is *not attributed* and not a shift nobody
    # named.

    #: What the panel did with this turn: `reply`, `proposals`, `guide`,
    #: `error`, `unavailable`. The panel's own word, so the trace and the
    #: screen cannot drift apart.
    kind: Mapped[str] = mapped_column(String(20), default="reply")
    #: What the person typed. Empty for a turn that began with a button -
    #: pressing "Do it" is a turn with no words in it.
    asked: Mapped[str] = mapped_column(Text, default="")
    #: What the assistant said back, exactly as the panel showed it.
    said: Mapped[str] = mapped_column(Text, default="")

    #: `[{"tool": str, "ok": bool, "summary": str}]` - the tool calls of this
    #: turn, each with the one-line summary the panel showed. Never arguments
    #: that carry a value the person did not see, and never a raw result.
    tools: Mapped[list | None] = mapped_column(JSON)
    #: `[{"id", "tool", "outcome", "entity_type", "entity_id"}]` - what was
    #: proposed and what became of it. `outcome` is `open`, `confirmed`,
    #: `declined` or `failed`; the entity pair is the audit row the confirmed
    #: write produced, so the trace and the audit trail can be put side by side.
    proposals: Mapped[list | None] = mapped_column(JSON)

    #: The walkthrough this turn put on the person's screen, if it put one
    #: there, and how many steps it has. How far they got through it is **not**
    #: recorded: the walk runs in the browser and the plant is not told, and an
    #: unknown is written as an unknown rather than guessed at.
    guide: Mapped[str | None] = mapped_column(String(60))
    guide_steps: Mapped[int | None] = mapped_column(Integer)

    #: The exception class of a turn that failed, never its message: a message
    #: can carry a fragment of the request, and the plant's log already has the
    #: whole of it at WARNING (CodeQL py/stack-trace-exposure, PR #109).
    error: Mapped[str | None] = mapped_column(String(60))

    #: What this turn cost, in tokens and in dollars at the list prices the
    #: product knows. An estimate, like every other figure derived from those
    #: prices - the Console is the bill.
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float] = mapped_column(Float, default=0.0)
