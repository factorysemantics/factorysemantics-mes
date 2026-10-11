# 0041 — A characteristic the plant counts, and the chart that judges it

- **Status:** accepted (2026-10-11, by the maintainer)
- **Date:** 2026-10-11
- **Deciders:** maintainer
- **Amends:** [0040](0040-the-chart-type-follows-the-sampling-plan.md)

## Context
[0040](0040-the-chart-type-follows-the-sampling-plan.md) made the chart type
a property of the data: the sampling plan is on the specification and the
chart follows from it, individuals one piece at a time and X̄/R when the plan
says n at a time. Both of those charts plot a *measurement*. Something was
measured; there is a number; the limits come from how much that number
scatters.

A great deal of what a plant knows about its own quality is not a
measurement. Somebody looks at every bottle that reaches the inspection
station and sends some of them back. What that produces is two whole
numbers for a stretch of production — how many were looked at, how many went
back — and, if the plant codes its rejects, which defects they were. There is
no reading, no gauge, no spread. Before today this product had nowhere to put
that, which meant the commonest quality record in a bottling plant, a
packaging plant or an assembly line could not be written down at all.

The temptation is to write a `quality_checks` row per unit with a pass or a
fail on it. [0027](0027-an-spc-signal-raises-a-hold.md) refused that and the
refusal stands: a check is a reading of a characteristic, with a stamp, a
gauge and a value, and twelve hundred rows carrying "pass" are twelve hundred
rows that are not readings and that every chart, every capability figure and
every coverage sentence in the product would then have to learn to ignore.
What the plant actually recorded is *one* record about *one* stretch.

The other temptation is a "chart type" field beside the sample size. 0040
refused that too, for the reason that has not changed: two fields that can
disagree, and the disagreement is a chart that lies.

## Options considered
| Option | For | Against |
|---|---|---|
| **The specification says it is counted; one new table holds N, k and the defects; the chart follows as a p chart** | One fact decides the chart, as in 0040. The record is the record the plant made — a stretch, two numbers, a breakdown — and nothing has to be inferred from it. The write, the rules, the holds and the dossier are the paths that already exist | A third kind for everything that reads `kind` to learn, and a third branch in the page, the kit, the agent's reader and the simulator's scorer |
| A `quality_checks` row per unit, with pass or fail in the value | No new table. The existing chart machinery sees "data" | Refused by 0027 and still wrong. A pass is not a reading; the rows are not what anybody recorded; and every figure computed from `quality_checks` would quietly include them |
| A chart-type field on the specification, chosen by hand | A plant could draw what it liked | 0040's argument, unchanged: two fields that can disagree. And a counted characteristic has nothing for the other two charts to plot, so the disagreement is not a preference, it is a blank chart |
| np, c and u charts as well, now | Completeness. A plant with a fixed inspection quantity reads np more naturally | Three more kinds, three more sets of limits and three more things to explain, for a chart that is p's own numbers times a constant. A plant that needs them can say so; this is the one that fits what a plant records without being asked |
| Per-point limits, or one pair from the pooled average n | A single pair of straight lines is easier to read, and is what most textbooks draw | A stretch of two hundred and a stretch of two thousand are not judged to the same standard, and straight lines would say they were. The honest chart has a pair per point |

## Decision
**A characteristic whose specification says `counted` is counted, not
measured. Its record is one row per inspected stretch in `quality_counts` —
how many were looked at, how many were sent back, which defects, and what the
plant calls the lot — and its chart is a p chart whose control limits are
computed per point.**

`QualitySpec.counted` is the third word in the one place the chart type is
decided (`services.spc.chart_kind`). `counted` wins over `sample_size`,
because a counted characteristic has no readings for the other two charts to
plot; the write path refuses a specification that claims both, and
`fsmes pack check` refuses one offline, with its own sentence.

`POST /quality/counts` is the write, under `quality.record` like every other
quality write, and is the mirror of `POST /quality/samples`: one row,
however many units it covers. It takes `inspected`, `rejected`, a `lot_ref`
in the plant's own text, an optional breakdown of `[{code, count}]`, the
order, the station, the stretch's start and end, and an optional `lot_id`
into `material_lots` for a plant that ships lots. **`lot_ref` is required
and `lot_id` is not**: on a plant that does not ship lots yet the honest
answer is what the plant calls the stretch, and the screen says so under
every lot it opens — *a lot here is one inspection stretch on one order
until this plant ships lots*. Nothing is backfilled. A characteristic that
is measured refuses this write with a sentence naming the chart it does have.

The chart plots `p = k/n` per stretch. The centre is **pooled** — `Σk/Σn`,
not the mean of the shares — because a stretch of two thousand says more
about where the process sits than a stretch of two hundred, and averaging the
shares would weight them equally. Each point's limits are
`p̄ ± 3·√(p̄(1−p̄)/nᵢ)` with its own `nᵢ`, so the pair moves with the lot;
they are drawn as **steps**, held flat across half the gap to each
neighbour, because nothing moves between two lots and a sloped line would
read as a limit that drifts. A lower limit that computes below nought is
drawn at nought and **the chart says what that means**: too few units were
looked at for a clean lot to be remarkable, so nothing on that lot could
fire a rule downward.

**Rule 1 only.** A point outside its own limits is the signal, recorded and
held through `evaluate()` on the write path exactly as on the other two
charts ([0027](0027-an-spc-signal-raises-a-hold.md),
[0036](0036-the-chart-draws-every-rule-the-plant-chooses-which-hold.md)).
Rules 2, 3 and 4 count points inside sigma *bands*, and on a p chart every
point's sigma is a different width — two points "beyond two sigma" would be
two points measured against two different rulers, and a run of them would be
a pattern in the inspection quantities as much as in the process. Rule 5 is a
range beyond its limit and there is no range here. The chart draws the rules
it runs and **says in words which it is not running and why**, rather than
leaving a reader to notice that a run of eight did nothing.

**No capability.** Cp, Cpk, Pp and Ppk are a spread against a specification
and a count has neither: there is nothing to be in or out of spec about on
one unit, and what judges a stretch is the control limits of its own chart.
The three cells say "not for a count" and carry that sentence; no figure is
printed, and none is withheld silently.

The point dossier opens on a lot
(`GET /quality/spc/{material}/{characteristic}/lot/{count_id}`) and its
**first** block is which defects, because the dot says *more of this lot came
back than usual* and the only useful next question is which ones. Its window
is the stretch, not the instant the count was written down. Below the first
three blocks it is the same dossier a reading and a sample open — the machine,
its Gantt, the rest of the line, the process values, the maintenance, the
findings — read from the same keys, so there is one panel and not a third one
that would drift.

**Three different facts about a breakdown, three different sentences.** A
null `defects` is *this MES does not know how they were sorted*; an empty
list is *nothing was coded against the rejects*; a coded total that differs
from `rejected` is a unit carrying two defects, or an inspector who coded
some and not all. The screen prints which, and never draws a clean lot for
any of them.

## Consequences
Easier: the commonest quality record on a packaging or assembly line can be
written down, charted, and argued with, from the same write path, the same
rules, the same holds and the same dossier as a measurement. A plant that
counts rejects per lot gets the chart that judges those counts honestly
instead of a spreadsheet.

Harder: `kind` now has three words, and everything that reads it had to learn
the third — the page, the chart kit, the agent's chart reader, the simulated
plant's chain scorer. Each of them now **names a kind it does not know** and
says so, rather than drawing a share as if it were a millimetre. That is the
cost of the decision in 0040 that the chart type is a property of the data,
and it is paid once per reader.

Harder: a reader who knows X̄/R has to be told that the limits on this chart
are not straight. The chart tells them, in its footer, every time.

Not decided here, and named so nobody builds them by accident:

- **np, c and u charts.** A plant whose inspection quantity never changes can
  read np, and a plant counting defects rather than rejected units needs c or
  u. Both are the same records with different arithmetic on them. When a
  plant asks, the table is already right.
- **Scrap reason codes.** The defects on a count are the plant's own words
  and this product does not police the vocabulary. A managed list of reason
  codes, with a hierarchy and a Pareto that rolls up, is a separate piece of
  master data: `docs/design/backlog/scrap-reason-codes.md`.
- **Finished-goods lots.** `lot_id` is there for a plant that ships them.
  Making this plant ship them is not this decision.

## House rules touched
**Never invent production** (1): a stretch in which nothing went through the
inspection station posts nothing, rather than a lot with nought looked at —
a point at nought rejects for a quarter of an hour when the machine was down
is a chart saying the plant was perfect while it was stopped. A counter that
goes backwards re-baselines and books nothing. `inspected` and `rejected` are
the record; the share is arithmetic on them and is never stored.

**Unknown is not zero** (2): a null breakdown is not a clean lot, and the
screen says which of the three facts it is holding. A lower limit at nought
is a sentence, not a line nobody explained.

**Every list states its total** (3): the defect table states how many codes,
how many units were sent back, and how many of them were coded — three
totals, because they can honestly differ.

**Config, not code, at plant boundaries** (4): `counted` is in the pack's
`quality_specs.json` and on the Specifications tab; `fsmes pack check`
refuses a counted characteristic that also asks for samples, or that carries
specification limits, offline. Which defects a plant sorts rejects into, and
what it calls a stretch, are the pack's.

**Tests are named as prose** (5): the suite for this reads
`test_a_characteristic_the_plant_counts.py`.
