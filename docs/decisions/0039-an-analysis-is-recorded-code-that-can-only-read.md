# 0039 — A deep analysis is a recorded computation that can only read, and it counts people before it names one

- **Status:** accepted (2026-09-29, on the design page's §11 — "I agree with all recommendations … I agree with monitoring position")
- **Date:** 2026-09-29
- **Deciders:** @kalwei

## Context

[0038](0038-an-agent-is-an-account-with-a-role-a-budget-and-a-cadence.md) made an
agent an account with a role, a tool set, a cadence and a budget, and PR #130
built the first read-only one: the analysis agent, account `ANALYST`, role
`analyst` = `plant.read` + `audit.read`, 53 read tools and no tool carrying
`dry_run`.

On 2026-09-29 the maintainer asked that agent to go much deeper than the four
shift analyses reach: a management question — *"what is the biggest problem for
our operators?"* — answered by network-graph analysis over the assistant's own
conversation traces, then drilled into anything relevant on that thread.
[The design page](../design/deep-analysis.md) works that example end to end.
Two things in it need a record.

### First: how far the agent's own computation may go

The four analyses are pass-throughs: the plant computes, the agent reads
(`src/fsmes/mcp/analysis.py`). *"Whatever a person could possibly dream up of
asking"* is not a catalogue, so the alternative is the agent writing analysis
code that runs somewhere. Measured on 2026-09-29: `numpy` 2.5.3 ≈ 16 MB,
`pandas` 3.0.6 ≈ 10 MB and `networkx` 3.7 = 2.1 MB of wheels, none of them
present in `pyproject.toml`, whose nine runtime dependencies and four optional
extras all carry the same sentence — *a plant PC runs the MES, not the tooling
that tests it.* And a recomputed rate is the failure
[0031](0031-a-judgment-is-a-proposal.md) and
[0033](0033-availability-is-a-share-of-what-was-watched.md) exist to prevent.

### Second: per-person analysis, which is already possible and has no position

- `ai_turns.person` has carried the asker's account code since the table existed
  (#112), beside the verbatim `asked` and `said`.
- `GET /ai` and `GET /ai/conversations` are gated on `audit.read`
  (`src/fsmes/api/routers/system.py`) and return `person`
  (`services/ai_trace.py`, `_public`).
- **`audit.read` is held by `supervisor` and above**
  (`services/capabilities.py`).

So on any plant running this today, **every supervisor can already read every
operator's questions verbatim for the 90 days `[admin] ai_trace_days` keeps
them.** A deep analysis opens no new door. What it adds is that the reading
becomes cheap, summarisable and rankable — *"which workcenters are asking which
questions"*, *"OEE and scrap during a specific user's shift"* — and that is a
different act from reading one conversation to find out why the assistant failed.

The research behind this product has no page on monitoring people: nothing on
surveillance, works councils, GDPR, or what it does to trust on a floor. The
table was built to debug an assistant, and its own docstring says so.

## Options considered

| Option | For | Against |
|---|---|---|
| **A catalogue only** — the agent composes parameterised analyses the plant computes, for ever | every number is the plant's own arithmetic; bounded by `RESULT_LIMIT`; no new dependency; nothing to sandbox; runs on a local model, so it works in shadow mode | answers only the questions somebody wrote down; every genuinely new question is a pull request; it is the *"doesn't go nearly deep enough"* complaint, restated as an architecture |
| **Agent-written code, unsandboxed** — run it in the API process | trivial to build | arbitrary code in the process holding the plant's database session and its credentials. Not seriously considered; recorded so nobody proposes it |
| **The catalogue now, a sandboxed run later (chosen)** — extend the catalogue; take the sandbox as a second, separately-decided step, over the plant's own envelopes and never over a rate | depth arrives after it is shown to be needed; cost does not separate the two (≈ $0.16 against ≈ $0.22 for the same question), so the 29 MB and the authorisation surface are paid for a demonstrated gap rather than a speculative one; the code is the audit record, which is a stronger property than a parameter list | two engines to hold in mind once both exist; the sandbox stays unbuilt for a while, and the first release is shallower than the ask |
| **Name people by default, gated on `audit.read`** — it is the gate the trace already uses | one gate, nothing new to build; the data is already readable by the same people | reading one conversation to debug a failure and ranking eleven operators are not the same act, and one gate for both means the plant cannot grant the first without the second. A default that names people will be used to name people |
| **Count by role, workcenter or shift by default; name a person only on an explicit ask behind a capability of its own (chosen)** | the honest business question is which work is hard, not who is slow; it is one default in one function; the plant grants the stronger thing deliberately; the audit row makes it visible to the person it is about | a manager who wanted the ranking has one more step, and will ask why; "explicit ask" is a judgment the agent has to make about a sentence |
| **Refuse per-person analysis outright** | no surveillance surface at all | it would refuse questions a plant legitimately has (*"who needs training on labelling stops"*), while leaving the raw rows readable by every supervisor — a refusal that changes nothing but the convenience |

## Decision

**A deep analysis is a computation the plant records, which can only read, and
which counts people before it names one.** Four clauses.

**1. Computation.** The analysis catalogue is the engine: parameterised analyses
the plant computes and the agent composes, extended as new questions need it.
Agent-written analysis code is a second step taken only when a shipped catalogue
has demonstrated a gap it cannot close, and when taken it runs in a separate
process with no credential and no network, receiving the **bytes the plant's own
tools already returned** — so it sees exactly what the asker's role may read and
nothing more. It may compute freely over counts, durations, timestamps and text.
It may **not** compute a rate the plant computes — OEE, availability,
performance, quality, coverage — which arrive already computed with their ledger
and may only be laid out. A result envelope carrying a rate the plant did not
compute, or carrying no coverage where its source had one, is **refused
server-side**. Every run is recorded with its code, its inputs' provenance, its
wall time and its result, so a figure can be re-run rather than trusted.

**2. Counting.** Every rollup over people is returned grouped by role, by
workcenter or by shift, never by person, unless the asker names a person or asks
for names.

**3. Naming.** Naming a person in an aggregate answer requires a capability of
its own, `people.analyse`, which no shipped role holds. `audit.read` is not that
gate.

**4. Reciprocity and record.** Every per-person answer writes an audit row
(`analysis.person_named`, entity `personnel`/`<code>`), and a person can see
everything the analysis could say about them, scoped to their own account, behind
`plant.read`. An analysis a plant will not show the person it is about is not run.

**Retention.** An analysis cannot reach past `[admin] ai_trace_days`, because the
rows are gone. A recorded run that named a person is pruned on the same horizon
as the trace it read, so a stored result never becomes a copy of a record the
plant chose to delete.

## Consequences

**Easier.** The first release needs no new runtime dependency, no sandbox and no
new platform story, and it runs on a local model — so it works in shadow mode and
on a plant with no key. A per-person analysis is a thing a plant grants on
purpose, in one capability, rather than a property of a screen somebody forgot
was readable. And when the sandbox is built, its authorisation model is already
solved: the child cannot ask for anything, because it gets bytes and no
credential.

**Harder.** Every genuinely new question is a catalogue entry until the sandbox
exists, which is a pull request rather than a conversation. A manager who wants
a per-operator ranking needs a capability granted first, and will experience that
as friction. And the agent has to judge whether a sentence is an explicit ask for
names — a judgment it can get wrong in both directions, so the default it falls
back to has to be the safe one.

**What must be revisited, and when.** (a) Whether the sandbox is built at all —
after the extended catalogue has been used on a real plant for long enough to
name a question it could not answer. (b) The class this analysis sends: under
[0032](0032-a-hosted-judgment-and-the-shadow.md), `ai_turns.asked` is *"anything
a person typed"*, which is the **production** class — off by default at every
plant, shadow or not. That is the correct classification and it means the
flagship analysis is off until a plant turns it on; if that proves unworkable in
practice it is 0032 that gets revisited, not this record. (c) Whether any plant
needs a works-council agreement or a lawful-basis record for clause 2 and 3 —
that is a question for a plant's own counsel, and what this product owes is that
the purpose limitation is enforceable, which is what the capability and the audit
row are.

## House rules touched

**Rule 1, never invent production**, and **rule 2, unknown is a valid answer and
zero is not:** clause 1's refusal of a rate the plant did not compute is rule 1
applied to an agent's own arithmetic, and the requirement that an envelope carry
coverage or be refused is rule 2 moved behind the API where it cannot be skipped.

**Rule 3, unlabelled data is reported as unlabelled:** clause 2's groupings each
state the count they could not attribute — a rollup by role over turns with no
person names the unattributed rather than dropping them.

**Rule 6, charts get checked by looking at them:** the recorded run of clause 1 is
what makes that checkable after the fact — a figure whose code is kept can be
re-run against the same window and compared with what was drawn.
