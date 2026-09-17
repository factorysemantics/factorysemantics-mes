# 0031 — A judgment is a proposal: what a probability may touch, and what it may never touch

- **Status:** proposed
- **Date:** 2026-09-17
- **Deciders:** maintainer

## Context
TypeSafe published Jev on 2026-09-16 — a *System One* model that takes
application state and typed questions and returns typed answers with
calibrated probabilities: `Choice` (one of a defined set, with a
distribution), `Noul` (probability a condition holds), `Score` (a
probability-weighted position on ordered, described levels). It generates no
text. Median latency is documented at about 100 ms and the published price is
$0.042 per million input tokens, output free. The full survey of where it
could sit in this repository is [docs/ai/JEV.md](../ai/JEV.md).

Three facts about *this* repository make the question sharp rather than
speculative.

**The codebase is already full of enumerated refusals.** `src/fsmes/services/oee.py`
names three candidate explanations for counted work that will not fit inside
the run time and refuses to pick one — decision
[0026](0026-counts-that-outrun-the-run-time.md) is that refusal written down.
`src/fsmes/services/analysis.py` files every stop with no reason into an
`unlabelled` bucket. `src/fsmes/integrations/opc/tag_map.py` raises rather
than guess at a PLC state code it was not given. In each case the options are
already written out in the source and the code declines to choose between
them. That is the exact shape of a `Choice`.

**The booked numbers are the product.** House rules 1 to 3 — never invent
production, unknown is a valid answer, unlabelled data is reported as
unlabelled — are what the scoring harness exists to check. `fsmes score`
replays a scripted hour and asks whether the MES booked what the line
produced and whether the scripted breakdown was detected.

**A model in that path would break the instrument.** If a judgment labels a
stop and the scorecard grades stop labelling, the harness has stopped
measuring the MES and started measuring TypeSafe. Separately, Jev is not
deterministic: TypeSafe's own consistency cookbooks report a per-question
probability standard deviation near 0.01 and label flips on 2 of 8 questions
across repeated identical calls before thresholding. CI runs a *deterministic*
simulated plant on every pull request, on two operating systems and two
Python versions. A non-deterministic call inside that gate is a flaky test
with a monthly bill.

## Options considered
| Option | For | Against |
|---|---|---|
| **A judgment is a proposal: stored with its distribution and its provenance, read by screens and by people, never by a KPI, a booking path, a graded number or a CI gate** | keeps rules 1–3 exactly as they are; the scorecard still measures the MES; a wrong answer costs a supervisor one glance; the proposal can be evaluated against the harness instead of hiding inside it | the most valuable-sounding uses — assigning orphan counts, labelling stops into the pareto the ERP sees — stay out of reach until somebody confirms each one |
| A judgment may book above a confidence threshold | the automation people actually ask for; TypeSafe documents risk-scaled thresholds | confidence is a statistic over the answer's own distribution, not a probability of being right, and its formula is undocumented. At 0.9 confidence TypeSafe's own worked example is still wrong 10 % of the time. A booked number that is 90 % right is decision 0004 reversed |
| No judgment model anywhere in the product; build loop only | zero risk to any number; the state stays synthetic and the consent question never arises | gives up the seams where the project's honesty is currently a dead end rather than a discipline — the unlabelled bucket helps nobody, it only refuses to lie |
| Judgments everywhere, gated by a plant setting | one switch, plant decides | "the plant decided" is not a defence when the number on the shift report came from a model and nothing on the screen said so |

## Decision
A typed judgment is a proposal and nothing else. Concretely, three rules.

**It may not be an input to any number the scorecard grades.** No availability,
performance, quality, OEE, booked count, yield or confirmation may be computed
from a model's answer, directly or through a column something else reads. A
judgment's output lands beside the fact it is about, never in place of it: a
proposed downtime reason is a new row with its own source, and
`EquipmentState.reason` stays null until a person or another system fills it,
so `unlabelled_share` still reports what it reports today.

**It carries its provenance and its numbers.** Every stored judgment keeps the
model version as served (`jev-1.12`, not `jev-latest`), the request id, the
full `probabilities` map, the confidence where the primitive returns one, and
the identity of the question that was asked. A proposal a person accepts
becomes that person's decision in the audit spine, attributed to them, with
the judgment kept as the evidence they were shown. Screens say a proposal is a
proposal, in the same way every screen already states its data source.

**It is never inside the gate.** No judgment call runs in CI, in
`fsmes pack check`, or in any test that can fail. The deterministic simulated
plant stays deterministic. Judgments in the build loop run in the nightly and
advisory paths, where a wrong answer costs a paragraph in a brief.

Each question declares, in code, the class of state it sends. Decision
[0032](0032-a-hosted-judgment-and-the-shadow.md) is what that class is for.

## Consequences
Easy: adopting a judgment anywhere becomes a small, reversible act, because the
worst case is a screen showing a suggestion nobody takes. Easy: measuring
whether it works, because a proposal that is never consumed can be compared
against the scored run's own truth without contaminating it — the harness
becomes the evaluation set the TypeSafe documentation says to build.

Hard: the uses that would save the most labour are the ones this decision
refuses. Orphan production rows still wait for a person
(`src/fsmes/services/execution.py`, `unassigned_production`); a counter delta
with two orders open on a station still takes the first by priority. Those are
worth an argument, but the argument has to arrive with an accuracy number from
this plant's own data, not with a demo.

Revisit when: a judgment has run in proposal mode against the scoring harness
for a meaningful number of scored hours and its accuracy and calibration on
*this* project's data are written down. Not before.

## House rules touched
Rules 1, 2 and 3 are the whole of this decision: a probability is not a
counter, an uncertain answer is not a known one, and a stop labelled by a
model is not a labelled stop. Rule 6 — a chart gets checked by looking at it —
is the reason the determinism clause is here and not only in a docstring: a
check that moves between runs is not a check.
