# Does the floor assistant do what people ask?

*The scorecard asks whether the MES told the truth about the plant. The
[evals](../agents/evals.md) ask whether an agent can find that truth through the
tools. This asks the third question, and it is the one a person standing at a
machine cares about: **I asked for something — did it happen?***

`tests/test_agent.py` pins the conversation loop. Reads run free, every write
pauses as a proposal, a confirmed write is idempotent and audited. All true, all
necessary, and none of it says whether the assistant, handed a sentence somebody
typed, reaches the right tool. Twice in two days it did not:

- **2026-09-25.** A request to change `default_job_minutes`. The proposal was
  right; the walk arrived on the Configuration page and stopped at the top of it,
  then said the control was not there "for your role". Fixed in #105.
- **2026-09-26.** A request to change the default reporting window. The setting
  exists; it was item 17 of a list of 22 that the tool result had cut at 6,000
  characters, so the assistant said the plant had no such setting, and then that
  no change was recorded. Fixed in #108.

Both were found by the maintainer, on his phone, over an evening. Nothing in the
repository would have caught either. This is what catches the next one.

## What the suite is

`tests/assist_suite/` — one TOML file per role, in the words people use. Each
case is a request as it was typed, the screen it was typed on, and one
expectation:

| Expectation | What it means |
|---|---|
| `propose` | the request is a change: offer exactly this tool, with the arguments the sentence named |
| `walk` | the request is "show me": walk them to the control, and to *that* control |
| `answer` | the request is a question: answer it from the plant |
| `read` | the request claims something changed, or asks what is recorded: look before answering |
| `refuse` | the request is not this person's to make: say so, and say who can |

Scoring is **strict on identity and loose on prose**. The tool name must match,
every argument key the request named must be present, and its value must be the
value the sentence gave — `1.33` and `"1.33"` are one number, `"1,2"` and
`"2, 1"` are one pair of rule numbers, `"CR"` and `"cr"` are one prefix, and
`55` is not `56`. Wording is matched as substrings, case-insensitively. A case
may say what it is `loose` about, and why, in `loose_about`.

## The two modes

They measure different things, and reading one as the other is the only way to
be misled by this.

### `fsmes assist eval --scripted`

Asks: **is this expectation reachable, through the real plumbing, for this
role?**

It runs the real guide router, the real tool catalogue for that role, the real
tools against a real seeded plant over the real API gates, the real surfaces, and
the real conversation loop — with the model replaced by a stand-in that plays the
expectation. So it cannot tell you whether a model would choose right, and it is
not pretending to. What it does catch:

- a request the guide router answers before the agent is ever asked;
- a tool this role is not offered, or is offered and should not be;
- arguments the tool will not take against a real plant;
- a "show me" with no walk behind it, or a walk that lands on the wrong control;
- a proposal reported as something already done;
- a conversation the hosted API would refuse from that point on — the stand-in
  validates the message history the way the real API does, which is what turns
  2026-09-26's `BadRequestError` into a failing case instead of a mystery.

It is deterministic, needs no key and no network, costs nothing, and takes about
two seconds. `tests/test_assist_suite_scripted.py` runs it, so the required
checks carry it on every pull request.

### `fsmes assist eval --live --plant <url> --user <code>`

Asks: **does the model choose right?**

The real model, on a running plant, through `POST /assist/agent` — the same
endpoint the assistant panel posts to, so what is scored is what a person gets.
Every proposal it opens is **declined**, so a scored plant is an unchanged plant.

It costs money. It stops at `--max-usd` (default $1.00) and says which cases it
did not run rather than reporting a short suite as a whole one; the month's cap
is in [BUDGET.md](BUDGET.md) and this is one line on it. It refuses to start
without `ANTHROPIC_API_KEY`, and refuses if the plant's own brain is off — with
the plant's reason, not a page of zeroes.

Run it against a **synthetic** plant. The result file quotes what the model did,
and that includes the arguments it chose; a real plant's codes are a real plant's
business.

## Reading a result

A live run writes `docs/ai/assist-eval/<date>.md`, the way a calibration run
writes `docs/ai/calibration/<date>/`:

- the mode, the model, the plant, the plant's commit and the suite's commit;
- what it cost in dollars and in tokens;
- a pass rate per role;
- every required case that did not pass, with the expectation, **what it did
  instead**, and a sentence per thing that was wrong;
- every case marked `not_yet`, and which handoff it is waiting on.

A plant does not report the commit it is running, so `--plant-commit` is how that
gets into the file. A result with no build behind it cannot be compared to next
month's. `--json` writes the same run as data, for a month that wants to diff
rather than read.

`assist-eval/2026-09-26-scripted.md` is a **scripted** run, kept as the example
of the shape. It scores no model, and its file name says so — a live run writes
`<date>.md`.

## `not_yet`, and the ratchet

A case the current code *cannot* pass yet is marked in the suite:

```toml
expected = "not_yet"
handoff = "assist-one-brain"
note = """Why, in plain words."""
```

Those are counted separately, so the required number never drifts because
somebody wrote a case for a thing that does not exist. **The ratchet works both
ways**: the test fails if a `not_yet` case starts passing. That is not pedantry —
it is how a fix that landed gets recorded as a fix you can rely on, instead of
quietly improving a number nobody re-read.

## Adding a case

**A bug somebody finds in the assistant becomes a case here before it becomes a
fix.** That order matters: a fix with no case is a fix that can be undone by the
next refactor, and a case written after the fix tends to describe the fix rather
than the thing the person asked for.

1. Open `tests/assist_suite/<role>.toml`.
2. Paste the request **exactly as it was typed**, typing and all. `set it to4` is
   not a typo in that file.
3. Say the screen it was typed on, and the expectation.
4. `source` says where the words came from — a `questions/` file, a journal
   entry, an issue.
5. If the tool needs arguments the sentence did not name, put them in `fills`.
   They are never scored: "create an account for Jo Patel as an operator" does
   not contain a password, and scoring one would be scoring the suite.
6. If the expectation needs a read narrowed to something, `lookup` says what.
   It is offered to every read the case names and filtered to the arguments that
   tool takes.
7. Run `fsmes assist eval --scripted --case <id>`. If it cannot pass yet, mark it
   `not_yet` and name the handoff.

The plant every case is asked about is the demo plant plus what
`fsmes.services.assist_eval.arrange` puts on it — an order, a failed check that
raised a non-conformance, a corrective maintenance job, and one setting somebody
actually changed. It is built through the write tools themselves, so a fixture
that stops working is a tool that stopped working.

## Why this is worth shipping in the open

The field publishes capability. A vendor announces how many agents, how many
integrations, which copilot; checked on 2026-09-25, nobody publishes a rerunnable
score for whether the assistant does what a *named role* asks, with the failures
listed — see [the landscape](../LANDSCAPE.md). Tool counts are inventory.
Faithfulness is what a plant is actually buying.

This MES already keeps its judgment model's calibration in public under
`docs/ai/`, on the same argument: a number you publish and can be held to is
worth more than a claim.

## See also

- [The judgment model in the build loop](JUDGMENT-IN-THE-BUILD-LOOP.md) — the
  same discipline, pointed at the build rather than the assistant.
- [Budget](BUDGET.md) — the monthly cap a live run draws on.
- [Evals](../agents/evals.md) — can an agent find the truth through the tools.
- [The write discipline](../agents/write-discipline.md) — why every change is a
  proposal.
- Decision [0035](../decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
  — who may do what, and whose the approval is.
