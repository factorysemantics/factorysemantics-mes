# The AI screen — what is recorded, who sees it, how long it is kept

Every conversation this plant's AI has had is in the MES, on **AI** in the
navigation bar. This page says exactly what lands there, what deliberately
does not, and how to read a failure out of it.

It exists because of two mornings. On 2026-09-25 an assistant walk stopped on
the wrong screen and the only record was a screenshot. On 2026-09-26 a model
error broke a conversation for good — thirty-odd turns, five of which reached
the model — and it was written down nowhere at all; the conversation had to be
reconstructed from a cost log, the audit trail and a reading of the source. A
plant that can be asked to change things has to be able to say what it was
asked and what it did.

## Who may read it

`audit.read` — supervisor and above, and the `agent` role. The same gate the
audit trail is behind, and for the same reason: this is the record of what was
done in this plant and by whom. Somebody without it does not see the **AI**
chip at all, rather than seeing one that refuses.

Changing how long the trace is kept needs `users.manage`, like every other
number on Setup › Configuration.

## The three tabs

**Conversations.** One row per conversation — who, when, how many turns, what
was proposed and what became of each proposal, how many turns failed, and what
it cost. Open one and you get the turns in the order they happened: what the
person typed, what the assistant said back, every tool it called with the one
sentence the panel showed, every proposal with its outcome, and the
walkthrough it put on the screen.

**Status.** Which brains are on and why the rest are off — the model server,
the GPU, the cloud brain's spend against its cap, and every assigned job the
local model has, with when each last did anything. The same rows the Ops
screen's Local AI panel shows; this tab is where they belong beside the
conversations they produce.

**Settings.** How long the trace is kept, and links to the Setup rows that
gate the assistant itself: what one conversation may spend, how much of the
plant it is told, and when a daily AI artifact is called late. Those are
Setup's rows and this page links to them rather than copying them.

## What is recorded

Per turn:

| Field | What it is |
|---|---|
| `asked` / `said` | The person's words and the assistant's, exactly as the panel showed them |
| `tools` | Each tool call: its name, the arguments the panel displayed, whether it worked, and the one-line summary |
| `proposals` | Each proposal: the tool, the arguments the card showed, the outcome (`open`, `confirmed`, `declined`, `failed`), and — for a confirmed one — the audit row it wrote |
| `guide` | The walkthrough put on the person's screen, and how many steps it has |
| `error` | The exception class of a failed turn |
| tokens, `usd` | An estimate at list prices. The Console is the bill |

The floor assistant writes a row for every turn, including a confirm, a
decline, an error, and a turn where the cloud brain was unavailable. The
design chat writes one per exchange, under the `design` brain.

## What is not recorded, and why

- **The system prompt.** Never. A test asserts a recorded turn does not
  contain it.
- **The API key.** Never, anywhere, in anything.
- **A tool's raw payload.** Only the summary sentence the panel showed. The
  trace is a record of what the person was shown and what was done for them,
  not a second copy of the plant's rows outside the tables that own them — a
  screen gated on `audit.read` that carried raw tool results would be a way
  around the capabilities those rows are behind.
- **How far someone got through a walkthrough.** The walk runs in their
  browser and the plant is not told. Unknown is written as unknown rather
  than guessed at (house rule 2); the page says so where a walk appears.
- **The local assistant's own answers on a plant with no key.** `/assist/ask`
  has no conversation to belong to — each question is answered and forgotten.
  A plant whose cloud brain is off still gets a row per turn from the
  assistant panel saying the brain was unavailable, so the absence is visible
  rather than silent.

**Shadow mode.** A plant in shadow writes these rows like any other: the trace
changes nothing in the plant and carries nothing off the box. What shadow mode
withholds is the cloud brain itself, so a shadow plant's rows are the local
model's.

## How long it is kept

`[admin] ai_trace_days`, ninety days by default, on Setup › Configuration and
on the AI screen's own Settings tab. Rows older than the horizon are deleted
as new ones are written, so what the screen shows is what the plant has rather
than what a cleanup job has got round to. **Zero keeps everything** — a plant
entitled to its whole AI history says zero, and nothing is pruned.

The turn is also written to `~/.local/share/fsmes/agent-turns.jsonl` on the
machine running the plant (see [the local AI layer](../ai/OBSERVABILITY.md)).
That file is never pruned and belongs to whoever administers the box; this
table belongs to the plant.

## Reading a failure

A conversation that went wrong has one of three shapes, and the trace tells
them apart without anybody guessing:

1. **A turn with an `error`.** The model did not answer. The row carries the
   exception class; the plant's own log has the message and the traceback, at
   WARNING, with the session id. Look there next.
2. **A turn with tool calls and no proposal.** The model looked and decided
   not to act. The summaries say what it saw — this is the shape that produced
   *"no workspace holds such a setting"* when a tool result had been cut in
   half, and the summaries are where that is visible.
3. **A proposal that stayed `open` or went `declined`.** Nothing changed in
   the plant. A `confirmed` one names the audit row it wrote, and the entity
   it names is the one to look up on Ops.

## Without a browser

```
fsmes ai conversations --since 7      # every conversation of the last week
fsmes ai show <session>               # one conversation, turn by turn
fsmes ai-status                       # which brains are on, and why the rest are not
```

`fsmes ai show` prints the same turns the screen does, including the audit
entity behind a confirmed proposal. It is what to reach for on a plant's
server over SSH, which is where somebody usually is when they want to know
what the assistant told the night shift.

## The endpoints behind it

| Route | What it answers |
|---|---|
| `GET /ai/conversations` | one row per conversation, with `total` and `showing` |
| `GET /ai/turns?session=…` | one conversation's turns, oldest first |
| `GET /ai/turns` | the plant's most recent turns, newest first |
| `GET /ai` | the status rows — the brains, the GPU, the assigned jobs |

All four are behind `audit.read`. Every list states its total.
