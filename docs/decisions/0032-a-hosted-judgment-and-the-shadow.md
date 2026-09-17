# 0032 — A hosted judgment is an outbound path, classified by the state it sends

- **Status:** proposed
- **Date:** 2026-09-17
- **Deciders:** maintainer

## Context
Jev is a hosted API and only a hosted API. As of 2026-09-17 TypeSafe's
documentation describes no on-premises deployment, no VPC option, no
downloadable weights and no offline mode; the service is documented as hosted
in the United States, the privacy policy states that inputs are not used to
train models, and no retention period and no rate limits are published. Those
last three are questions to put to TypeSafe in writing, not gaps to assume
around.

`src/fsmes/shadow.py` holds `REGISTER`, an enumerated list of every outbound
path this software has, each an `Outbound(name, where, reaches, verdict,
note)`; `tests/test_shadow_mode.py` scans the source and fails on an outbound
call site that is not in it. So a Jev client cannot be added quietly. The
precedent is `llm.cloud_agent`, verdict `refused`, whose note is the sentence
this decision has to answer: it "carries this plant's numbers off the box, and
a plant lending us its data to watch did not agree to that."

Shadow mode is not a side feature. 0.2.0 exists so a first real plant can run
this beside the MES already in charge, and the whole proposition of that is
that nothing leaves. Meanwhile the strongest *cheap* uses of a judgment model
in this repository send no plant data at all: the assistant's guide catalogue
is text that ships in the wheel, and the build loop's state is a simulated
plant.

Treating "does it reach TypeSafe" as one question therefore throws away the
distinction that matters. What leaves the box is not a property of the
integration; it is a property of each question.

## Options considered
| Option | For | Against |
|---|---|---|
| **Classify the state each question sends, and let shadow mode refuse by class** | the honest answer: a question over the product's own guide titles is not the same act as a question over last night's counts; makes the refusal reviewable question by question; the class is declarable in code and testable | a new concept in the codebase, and a wrong classification is a quiet leak rather than a loud one — so the classification has to be in the test suite, not the docstring |
| One `refused` entry, matching `llm.cloud_agent` | one line, precedent already set, nothing to explain to a plant | refuses the assistant's guide routing, which sends a question and a catalogue of product text — and refuses it for a reason that is not true of it |
| Allowed, opt-in per plant, one setting | the plant decides, which is the honest locus | seventy-one settings already exist and this one would be the one nobody reads carefully; a single switch cannot distinguish a tag name from a customer's order book |
| Dev loop only; never reachable from a plant installation | no plant consent question ever arises; the build loop's state is synthetic | leaves the product seams unbuilt, and does not actually answer the question — a maintainer's laptop running a lab pack is a plant installation |

## Decision
Every judgment question declares the class of state it sends, and shadow mode
refuses by class rather than by integration. Four classes, from the outside in:

- **catalogue** — text that ships in the wheel and is identical at every
  plant: guide titles, status vocabularies, the canonical column names of a
  boundary format, the descriptions of a question's own options.
- **configuration** — what this plant is: machine codes, tag names, routing
  and material codes, its reason vocabulary, shift patterns. Identifies the
  plant; says nothing about what it made.
- **observation** — what the line did: states and durations, alarm bits,
  process values, cycle times.
- **production** — counts, order codes, quantities, yields, measurements
  against a specification, and anything a person typed: findings, rationales,
  dispositions, names.

`catalogue` is allowed in shadow mode. `configuration`, `observation` and
`production` are refused in shadow mode, and outside shadow mode each is off
until the plant turns that class on. `production` additionally carries a
person's words and another party's commercial facts, so it stays off by
default at every plant, shadow or not, and turning it on is a decision the
plant records rather than a setting a commissioning engineer flips.

The register gets one entry per class, not one for the integration, so
`fsmes shadow` prints what would and would not leave in the same vocabulary a
plant asks the question in. The class of a question is a property of the
question object in code; a test asserts that every registered question
declares one and that no question's assembled state reaches outside its class.

An air-gapped or refusing installation is not a degraded one. The client is
constructed with an injected transport and a configurable base URL, so a build
that may not egress can be shown not to, and every consumer keeps the
deterministic behaviour it has today as its fallback. Nothing in the product
may require a judgment to answer.

## Consequences
Easy: saying yes to the cheap, obviously safe uses — guide routing, the build
loop — without reopening the question for the ones that carry a plant's
numbers. Easy: the conversation with a plant, because the answer to "what
would you send?" is a list, per class, that `fsmes shadow` prints.

Hard: four classes is three more than one, and classifying a question wrongly
is the failure this creates. The mitigation is the test, and the test is only
as good as the state assembly it inspects, so state for a judgment must be
assembled in one place per question rather than passed through.

Harder: this decision does not make Jev available to the plants that most need
it. A site that will not send `observation` gets no downtime proposals, and a
site with no internet gets nothing at all. If that turns out to be most sites,
the right answer is not to relax a class — it is that a hosted judgment model
is the wrong shape for this product and the seams should wait for something
that runs on the box.

Revisit when: TypeSafe answers the retention, rate-limit and SLA questions in
writing; or when an on-premises or VPC option exists; or when the first real
plant says which classes it would allow.

## House rules touched
Rule 4 — config, not code, at plant boundaries: which classes a plant permits
is the plant's configuration, and belongs in its pack rather than in a
constant. Rule 2 is why a refused class must produce *unknown* and the
deterministic fallback, never a blank that reads like an answer.
