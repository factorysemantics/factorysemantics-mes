# The judgment model in the build loop

**What this page is about: the development build loop, and nothing else.**
No plant sends anything anywhere because of what is described here. No
screen, no API route, no agent tool and no plant data path asks a judgment
model anything, and shadow mode refuses the call outright.

The survey this comes from is [JEV.md](JEV.md), and the two decisions it
rests on are [0031](../decisions/0031-a-judgment-is-a-proposal.md) — a
judgment is a proposal — and
[0032](../decisions/0032-a-hosted-judgment-and-the-shadow.md) — a hosted
judgment is an outbound path, classified by the state it sends. Both are
proposed rather than accepted. This page is step 1 of the survey's own
phasing: the cheapest possible place to find out whether a typed judgment
model is worth anything here.

## What it does

Two things ask it, both in the build loop: every scored run's log, and every
answer an agent gives in the agent evals. The run log came first and is the
larger of the two; the evals are below.

Every scored simulated run has its log read twice.

The **open pass** has been running since it was written: about 12 KB of log
tail goes to a local `qwen3:8b`, which is asked for a JSON array of findings.
It can be surprised — it can report a failure nobody thought to ask about —
and it has one bad habit: the reply is scraped for a JSON array, and an
unparseable reply is recorded as *no findings*. A clean run and a parse
failure have been indistinguishable for as long as that code has existed.

The **typed pass** sends the same log tail to a hosted judgment model with a
fixed battery of questions:

| Question | Kind | What comes back |
|---|---|---|
| a retry storm | condition | a probability |
| a counter went backwards | condition | a probability |
| a component stopped reporting | condition | a probability |
| an exception was logged and swallowed | condition | a probability |
| a deadlock | condition | a probability |
| how serious the worst problem is | severity, on `none · low · medium · high` | a number that can fall between two levels, the probability of each, and a confidence |

**Six questions. That is the whole battery**, and it is fixed, which is
exactly its weakness: a failure nobody wrote a question for is invisible to
it. That is why both passes run and both are recorded. The comparison
between them, a run at a time, is the evidence for whether either is worth
keeping — and it is the only reason this is here at all.

## What is sent, and what is stored

**Sent:** the last 12 KB of one simulated plant's own run log, and the text
of the six questions. Nothing else — the same tail the local model already
gets, and not the scorecard summary that goes with it.

**Stored,** in the run's own record, per answer: the probability, the model
version **as served**, the provider's request id, a fingerprint of the exact
wording the question was asked in, and the time. The wording is part of a
question's identity — re-word it and last month's answers are answers to a
different question — so the fingerprint is stored rather than trusted to a
name. The severity keeps the whole distribution: the expected score the
service returned, which can fall between two levels, the probability of each
level, and the level carrying the most probability — which is a reading of the
answer and not a threshold. The call's token usage is stored once per record,
`null` where the service reported none.

### What a failed judgment looks like in a run card

`asked: false`, and a `note` that reads `not asked (…)` — the reason in the
brackets, and nothing else changed anywhere in the run. A service that did not
answer, a client that could not be built, a key the service refused, an answer
of the wrong shape, an SDK that moved under us: all of them are that sentence,
and the class of the failure is named in it, because a service being down and
this code being wrong are different facts. **No failure of any of this reaches
the run.** It was the other way round once — on 2026-09-17 the first real call
raised an `AttributeError` from a client that could not be built, and it killed
the scoring of a run that had already succeeded. `not asked (…)` is the only
thing a judgment may ever do to the thing it judges (decision 0031), and there
are tests that fail if it can do anything else.

## The second thing it is asked

The agent evals (`fsmes agent-eval`) ask an agent a question about a plant
that can only be answered through this MES's own tools, and score the reply
against the truth computed from the API at the moment of asking. That score
was a rule about tokens, and the rule was broken: it upper-cased the reply
before matching an upper-case character class, so every English word of two
letters or more was a candidate machine code. The rule is fixed, and it is
still the number the eval's trend is drawn from.

Beside it, one question per answer:

| Question | Kind | What comes back |
|---|---|---|
| the reply names every expected code and no distractor | condition | a probability |

**One question. That is the whole battery** for this caller. **Sent:** the
question the agent was asked, the codes that were right at the moment of
asking and the codes that would be wrong — each list with its total — and
what the agent replied. Nothing else about the plant, and in the build loop
the plant is a simulated one. **Stored:** on the eval's own row, beside the
pass or fail, with the same provenance as every other answer here.

`fsmes agent-eval --summary` prints the pass rate and the judgment's numbers
separately and says which is which. The judgment's side carries two means —
where the check passed, and where it failed — rather than an agreement rate,
because without a threshold there is no boolean to agree with, and those two
means are the beginning of the plot a threshold would need.

## What it may not do

- **It decides nothing.** `worst`, the two columns the results store trends,
  the night brief's selection and everything `autoloop.py` reads come from
  the open pass alone, exactly as before, and the agent evals' pass rate
  comes from their own check alone. A judgment is a proposal
  ([0031](../decisions/0031-a-judgment-is-a-proposal.md)).
- **It has no threshold.** A probability is recorded and read by a person.
  Turning one into a verdict needs a calibration plot drawn on this
  project's own runs; that plot does not exist, so there is no line, and the
  record says so where a reader will see it.
- **It never runs inside a gate.** Not in CI, not in `fsmes pack check`, not
  in any test that can fail. The test suite's default path is the one where
  no key is set; the asked path is exercised through a transport the tests
  write. Nothing in this repository opens a network connection to ask a
  judgment anything.

## Turning it on

Four settings, and the only one without a working default is the key.

| Setting | Default | What it is |
|---|---|---|
| `MES_JEV_API_KEY` | empty | The key. Read from the environment or the settings file, the same way `MES_ERPNEXT_API_SECRET` is. Never in this repository, never printed: `repr`, the log and the run record all say only whether one is set. |
| `MES_JEV_MODEL` | `jev-1.13.0` | The pinned version. A moving alias — anything ending in `latest` — is refused before anything is asked, because an answer stored against one cannot be read again. The version that actually answered is stored with every answer, so a change shows up as a change. |
| `MES_JEV_BASE_URL` | empty | Empty uses the client's own endpoint. Set it to point a build that may not egress at something it may reach. |
| `MES_JEV_TIMEOUT_SECONDS` | `5.0` | One attempt. The client's own default is ten seconds with two retries, which is a thirty-second worst case in front of a service whose median is about 100 ms. |

The client is an optional extra: `pip install 'factorysemantics-mes[jev]'`.
The core install does not carry it, and nothing needs it.

**Which version to pin is learned by asking, not by listing.** On 2026-09-17
the service's own model list offered `jev-latest` and `jev-preview` and nothing
else — no numbered version at all — while a call made as `jev-latest` came back
naming `jev-1.13.0`, which is then accepted as a pin by name. So what is listed
and what can be pinned are two different facts. `fsmes jev models` prints the
list with its total; `fsmes jev models --resolve` spends one call on a one-line
synthetic state to learn the concrete name, and says what moving the pin would
mean. One call, made because somebody asked for it: moving a pin is a
re-validation, and answers either side of it are answers from different
versions.

**With no key — the normal case, and what every installation of this package
has — the run is triaged exactly as it was before**, and the record says
`not asked (no MES_JEV_API_KEY in this environment)` rather than nothing.
Every other way of not being asked says which: no log kept, an agent that
replied with nothing, the client not installed, the service not answering,
an answer that came back short, shadow mode.

## Shadow mode

`llm.jev` is an entry in the outbound register (`fsmes.shadow.REGISTER`),
verdict **refused** — the same verdict as the cloud brain and the design
chat, for the same reason. It writes nothing and decides nothing, but
whatever state a question carries goes off the box to answer it, and a plant
lending us its data to watch did not agree to that. Refused twice over: no
client is built, and one built some other way refuses at the call.

[Decision 0032](../decisions/0032-a-hosted-judgment-and-the-shadow.md) asks
for one register entry per class of state rather than one per integration. That is the shape the product's own questions will need,
and it is not built here: the build loop has two callers, but one class of
state between them and one verdict. Each question set declares its class in
code (`observation` for both), so the classification is already a property
of the question rather than a sentence in a document.
