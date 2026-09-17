# The judgment model in the build loop

**What this page is about: the development build loop, and nothing else.**
No plant sends anything anywhere because of what is described here. No
screen, no API route, no agent tool and no plant data path asks a judgment
model anything, and shadow mode refuses the call outright.

The survey this comes from is `docs/ai/JEV.md`, and the two decisions it
rests on are `0031` — a judgment is a proposal — and `0032` — a hosted
judgment is an outbound path, classified by the state it sends. Both are
proposed rather than accepted. This page is step 1 of the survey's own
phasing: the cheapest possible place to find out whether a typed judgment
model is worth anything here.

## What it does

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
| how serious the worst problem is | severity, on `none · low · medium · high` | a level, with the probability of each |

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
name.

## What it may not do

- **It decides nothing.** `worst`, the two columns the results store trends,
  the night brief's selection and everything `autoloop.py` reads come from
  the open pass alone, exactly as before. A judgment is a proposal (0031).
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
| `MES_JEV_MODEL` | `jev-1.12` | The pinned version. A moving alias — anything ending in `latest` — is refused before anything is asked, because an answer stored against one cannot be read again. The version that actually answered is stored with every answer, so a change shows up as a change. |
| `MES_JEV_BASE_URL` | empty | Empty uses the client's own endpoint. Set it to point a build that may not egress at something it may reach. |
| `MES_JEV_TIMEOUT_SECONDS` | `5.0` | One attempt. The client's own default is ten seconds with two retries, which is a thirty-second worst case in front of a service whose median is about 100 ms. |

The client is an optional extra: `pip install 'factorysemantics-mes[jev]'`.
The core install does not carry it, and nothing needs it.

**With no key — the normal case, and what every installation of this package
has — the run is triaged exactly as it was before**, and the record says
`not asked (no MES_JEV_API_KEY in this environment)` rather than nothing.
Every other way of not being asked says which: no log kept, the client not
installed, the service not answering, an answer that came back short, shadow
mode.

## Shadow mode

`llm.jev` is an entry in the outbound register (`fsmes.shadow.REGISTER`),
verdict **refused** — the same verdict as the cloud brain and the design
chat, for the same reason. It writes nothing and decides nothing, but
whatever state a question carries goes off the box to answer it, and a plant
lending us its data to watch did not agree to that. Refused twice over: no
client is built, and one built some other way refuses at the call.

Decision 0032 asks for one register entry per class of state rather than one
per integration. That is the shape the product's own questions will need,
and it is not built here: the build loop has one caller, one class of state,
and one verdict. The question set does declare its class in code
(`observation`), so the classification is already a property of the question
rather than a sentence in a document.
