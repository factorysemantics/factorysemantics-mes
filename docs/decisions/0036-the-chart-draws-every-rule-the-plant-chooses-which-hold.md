# 0036 — The chart draws every rule; the plant chooses which raise a hold

- **Status:** accepted (2026-09-22, by the maintainer)
- **Date:** 2026-09-22
- **Deciders:** maintainer

## Context
`src/fsmes/services/spc.py` runs four Western Electric rules over every
characteristic's readings, on the write path, so a rule that trips is acted
on whether or not anybody has the chart open. All four have always run and
all four have always raised a quality hold — a non-conformance a person works
through review and a disposition, which is decision
[0027](0027-an-spc-signal-raises-a-hold.md).

Decision
[0035](0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
placed "SPC rules 1–4" in tier three — what the product owns — with the note
*"a rule a plant can switch off is a chart that lies."* That reasoning is
about the **chart**, and it holds: a chart drawn with rule 4 switched off,
presented as an SPC chart, does not say what it appears to say.

The configuration audit of 2026-09-21
([the list](../design/config-audit-2026-09-21.md), row **Q5**) found that
0035 had answered a question it was not asked. Whether a plant may choose
which rules raise a **hold** is a different question from which rules are
drawn, and 0035 never asked it. Q5 is the only one of that audit's
seventy-five candidates listed as `general` in quality and the only one it
records as *blocked on a decision rather than as work*: until this is
decided, nothing about it can be built.

The rule *windows* — 2-of-3, 4-of-5, 8-in-a-row — are not in question and
stay the product's. A plant that changed them would publish
`SpcSignal.rule = 3` while meaning something nobody else means by rule 3,
which is the tier-three side of 0035's own test.
`RULE_WINDOW = {1: 1, 2: 3, 3: 5, 4: 8}` (`services/spc.py`) is fixed.

What is at stake in practice: a plant inspecting frequently on a
characteristic that drifts slowly gets rule 4 — eight readings in a row on
one side of centre — several times a shift. Each one opens a non-conformance
that somebody must review, disposition and close. A plant in that position
today has two options, and both are bad: work a queue of holds nobody thinks
are findings, or stop recording checks.

## Options considered
| Option | For | Against |
|---|---|---|
| **Draw and record all four always; let the plant choose which raise a hold** | The chart never lies — every rule is drawn, every firing is recorded as an `SpcSignal` row, and the verdict still says *out of control*. What the plant chooses is who gets called, which is a plant's own business and differs honestly between an aerospace cell and a bottling line | Two plants' hold queues are no longer comparable. A plant can quieten a rule and then forget it did |
| Leave it: all four rules always raise a hold | Nothing to build, nothing to explain, every plant's holds mean the same thing | The plant above still has to choose between a queue it ignores and not recording checks, and a hold queue nobody works is decision 0027 already broken, quietly |
| Let the plant switch rules off entirely — not drawn, not recorded | One switch rather than two concepts | This is exactly what 0035 §3 refused, and for a reason that has not changed: a chart drawn without rule 4 and labelled an SPC chart is a chart that lies |
| A severity per rule instead — every rule holds, at the plant's own grade | Nothing disappears; triage sorts itself out | It does not solve the problem, which is the *number* of records somebody must work, not what they are called. That is **Q2**, and it is a different change |

## Decision
**Every Western Electric rule is evaluated, drawn and recorded on every
plant. Which of them raise a quality hold is the plant's, and defaults to all
four.**

The four rules, their windows and their numbering stay the product's and are
not configurable by anything. A firing is always written as an `SpcSignal`
row, always drawn on the chart, and always counted in the verdict: a process
that trips rule 3 is out of control on every plant, and capability stays
withheld until it is settled. Decision 0027 is unchanged for every rule a
plant holds on.

What a plant chooses is `[quality] hold_rules` in its pack — a list of rule
numbers from 1 to 4, compiling to `MES_QUALITY_HOLD_RULES`. Leaving it out is
all four, which is what this product has always done, so a plant that
configures nothing behaves exactly as it does now. The key may only ever
narrow the set {1, 2, 3, 4}; `fsmes pack check` refuses anything else,
offline, naming the four rules.

**An empty list is a real answer and is never silent.** A plant may record
and draw every rule and raise a hold on none — running SPC as an observation
is a plant, not a mistake. It is said out loud rather than allowed to be
discovered: the chart payload carries `rules` and `hold_rules` on every
response, and the SPC screen prints which rules this plant holds on and which
are *drawn and recorded and raise no hold*, on every chart, including the one
where all four are held. A signal the plant does not hold on is returned with
`held: false`, which the screen renders as *no hold — this plant does not
hold on this rule*, so a firing that opened nothing is explained rather than
noticed.

Scott's words, 2026-09-22: *"Draw all four; the plant chooses which hold."*

This amends 0035 §3 in place rather than superseding it: the sentence *"a
rule a plant can switch off is a chart that lies"* stays true and is scoped
to the chart, which is what it was always about.

## Consequences
Easier: a plant whose process trips a warning rule several times a shift can
keep recording checks and keep the chart honest without working a hold queue
it does not believe in. Easier: the audit's Q5 stops blocking, and the rest
of quality's configuration section can be built.

Harder: two plants' hold counts are no longer straightforwardly comparable, so
a fleet view that counts open non-conformances is counting something each
plant defined. The chart is comparable and the `SpcSignal` rows are
comparable; the holds are not, and anything that compares them across plants
has to say so.

Harder: a plant can quieten a rule and forget. The mitigation is that nothing
hides it — the chart says which rules hold on every load, the Configuration
page lists the key and names the pack it lives in, and the rule still fires,
is still recorded and still moves the verdict.

To revisit: if a plant turns out to want a *severity* per rule rather than a
hold or nothing — the audit's **Q2**, `spc_major_rules` — that is a different
key and its own change. This decision deliberately does not pre-empt it.

## House rules touched
**Never invent production** and **unknown is not zero** (1 and 2): nothing
here changes a reading, a signal or a verdict. A rule that fires is recorded
and drawn on every plant, so the evidence is identical whatever the plant
holds on; only who is called changes, and the screen says which rules those
are. A firing with no hold is labelled with *why* there is no hold rather
than left as an absence a reader has to interpret.

**Config, not code, at plant boundaries** (4): which findings are worth
somebody's morning is a plant's own judgment, and it was a literal — four
`for` loops with no number in them — until this decision.
