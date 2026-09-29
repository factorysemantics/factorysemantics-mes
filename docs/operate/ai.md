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
| `screen` | The route the browser was on when the question was asked — `/dashboard/quality`, never the query string. Null where none was recorded |
| `shift_code` / `shift_day` | Which shift the turn fell in, worked out when the row was written from this plant's calendar as it stood. Null where no pattern covered the instant |
| `tools` | Each tool call: its name, the arguments the panel displayed, whether it worked, and the one-line summary |
| `proposals` | Each proposal: the tool, the arguments the card showed, the outcome (`open`, `confirmed`, `declined`, `failed`), and — for a confirmed one — the audit row it wrote |
| `guide` | The walkthrough put on the person's screen, and how many steps it has |
| `error` | The exception class of a failed turn |
| tokens, `usd` | An estimate at list prices. The Console is the bill |

The floor assistant writes a row for every turn, including a confirm, a
decline, an error, and a turn where the cloud brain was unavailable. The
design chat writes one per exchange, under the `design` brain. The analysis
agent writes its own, under the `analysis` brain — which is how the
Conversations tab lists one agent's conversations apart from the other's.

**About `screen`, and the three ways it is null.** It is the path the panel
posts — `window.location.pathname` — with the query string and the fragment
cut off before it is stored: `?setting=nc_code_prefix` names the *thing*
somebody was looking at, and a column meant to say *where* they were would be
read for the first if it carried the second. It is null on a turn recorded
before this column existed, because nothing is backfilled; on a confirm or a
decline, because the panel posts no screen with those and guessing the screen
of the message before would record a place the plant was never told about; and
on a design-chat turn, whose panel names a screen in words rather than as a
path. The Conversations tab shows the screen a conversation was opened from
and how many others its turns crossed, and the rollups count the turns that
name none rather than leaving them out of a total.

**About the shift stamp.** Written once, when the row is written, the same way
every booking and every state change has carried one since
[decision 0028](../decisions/0028-which-shift-a-minute-belongs-to.md) — so a
question asked in January keeps January's shift when the roster is rewritten
in March. Null means *not attributed*: a plant with no shift patterns gets it
on everything, and that is the truth rather than a shift nobody named. Audit
rows carry the same pair, for the same reason, so a change and the production
it explains can be read in the unit a plant is run in.

## The two agents

There are two agent kinds, and the `brain` on every row says which one was
talking.

| | **floor** | **analysis** |
|---|---|---|
| What it is for | somebody at a machine who wants something done | somebody who has sat down with a question |
| Account and role | `AGENT`, role `agent` | `ANALYST`, role `analyst` — `plant.read` and `audit.read`, nothing that writes |
| Tools | every read, plus the writes the person's own role allows | **every read tool and no write tool**, and it is built that way rather than listed: any tool carrying `dry_run` is dropped, so a write tool added tomorrow is outside it on the day it is written |
| Can it change the plant | it proposes; a person presses "Do it" | no. There is nothing in its catalogue that changes anything, and its account would be refused if there were |
| Walkthroughs | yes — "Show me" walks you onto the real form | no. A walk ends on a button somebody presses, and this agent does not lead anybody to a change |
| What one conversation may spend | uncapped (see [BUDGET.md](../ai/BUDGET.md)) | $0.25 by default |
| In shadow mode | falls back to the local model | **off, and says so** |

Asked for a change, an analysis conversation says that it only reads and
that the assistant in the panel can propose it to whoever signs it. It does
not say the person's role is the obstacle, because it is not.

**Where to find it.** The Assistant panel on any screen has one button —
*Ask the analyst* — which starts a fresh conversation with the other agent.
A conversation belongs to one agent for its whole life: its tools, its
prompt and its budget were read from that agent when it opened, so switching
starts a new one rather than handing the old one over.

**The two accounts must both exist.** `fsmes plant <name> init` creates them
and leaves any account already there exactly as it is; a plant built before
this release gets `ANALYST` on its next `fsmes plant <name> migrate`. Until
it has one, an analysis conversation's first read comes back saying that the
`ANALYST` account cannot sign in and naming the command that creates it —
deliberately, rather than quietly falling back to `AGENT`, which holds
enough to book production.

> **What the person's own role does *not* narrow.** An agent's reads reach
> the plant as the agent's account, not as the person asking, so what a
> conversation can look up is bounded by the agent's role rather than by the
> reader's. That is true of both kinds and is not new — the floor
> assistant's `agent` role has held `audit.read` since it existed — but it
> is worth knowing before granting either account more.

## Who may be named in an analysis, and who finds out

The analysis agent can roll this trace up — which questions are being asked,
how often, by how many people. [Decision 0039](../decisions/0039-an-analysis-is-recorded-code-that-can-only-read.md)
is the position on doing that to people, and it is four sentences.

**Counts before names.** A rollup over people comes back grouped by **role, by
workcenter or by shift — never by account.** That is the default in the code and
not a rule in a prompt: with nobody asking for names, no key in any breakdown is
a person's code. The honest business question is which work is hard, not who is
slow.

**Naming a person is a capability of its own: `people.analyse`.** **No role this
product ships holds it** — not the administrator, and not the analyst whose own
job this analysis is. A plant that wants a per-operator answer defines a role,
grants it on purpose, and can say why. `audit.read` is deliberately *not* that
gate: reading one conversation to find out why the assistant failed, and ranking
eleven operators by how often they asked for help, are two different acts, and
two acts should not share one gate.

**Every per-person answer is recorded.** It writes an audit row —
`analysis.person_named`, against `personnel`/the person's code — so somebody can
search the trail for their own code and find every analysis that named them.
`GET /audit?entity_type=personnel` is that search.

**An analysis a plant will not show the person it is about should not be run.**
That is the test to apply to anything added here later. An operator seeing
everything the analysis could say about them, on their own account, is the piece
still to build.

One thing to know before any of this: **on any plant running this today, a
supervisor can already read every operator's questions verbatim** for as long as
the trace is kept — `audit.read` gates `/ai`, and the payload carries `person`
and the words that were typed. The analysis opens no new door. What it adds is
that reading becomes cheap and rankable, and that is the act these three
sentences gate.

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
| `GET /analysis/trace/rollup` | the questions this plant asked, grouped by role, workcenter or shift — with the unattributed count and the totals |
| `GET /analysis/trace/graph` | those questions and the plant's stops as one graph of recorded facts, with the empty kinds declared and the centralities refused |
| `GET /analysis/maintenance/mttr` | repair time over time, with the count of repairs nobody timed beside it |

The first four and the two `/analysis/trace/` reads are behind `audit.read` — an analysis of the trace must not be a way around the gate on the trace; the MTTR is behind `plant.read` like every other read of the floor. Every list states its total.

`GET /assist/agent/status` is the other one worth knowing: it answers for
one agent (`?kind=analysis`) and carries a `kinds` object beside it saying
the same of every kind this plant has — its account, its role, whether it
can write at all, what one conversation may spend, and why it is off if it
is. It is behind being signed in, not behind `audit.read`: it says nothing
about what anybody asked.
