# 0040 — The chart type follows the sampling plan

- **Status:** accepted (2026-10-06, by the maintainer)
- **Date:** 2026-10-06
- **Deciders:** maintainer

## Context
Until today `src/fsmes/services/spc.py` drew one chart: individuals and
moving range. Every reading was a point, and the limits came from the mean
moving range of consecutive readings. That is the right chart when a
characteristic is inspected one piece at a time, which is how every
characteristic in this product was inspected.

It is the wrong chart when the plan says otherwise. A bottling line that
measures five bottles every fifteen minutes is not producing five
individuals; it is producing one *sample* of five, and what a person wants
plotted is the sample's mean, with limits built from the within-sample
spread. Plotting those five readings as individuals widens the limits with
the bottle-to-bottle variation the sampling was designed to hold constant,
and the chart stops answering the question it is drawn for: has the
*process* moved?

The two charts are not a preference. Which one is correct is decided by how
the pieces were collected, and that is a fact about the plan — so the plan
has to be recorded before the chart can be right.

## Options considered
| Option | For | Against |
|---|---|---|
| **The specification carries the sample size; the chart type follows it** | The plan is recorded where the characteristic is defined, which is where a quality engineer looks for it. One fact decides the chart, the limits, the rules and the capability sigma — nothing can be set inconsistently | A specification that changes its plan changes the meaning of its own history. Readings taken under the old plan are set aside rather than redrawn |
| A setting on the plant: this plant charts X-bar and R | One key, no schema change | Wrong by construction: two characteristics on the same line are honestly inspected differently, and a plant-wide switch would mis-chart one of them |
| Infer it from the data — readings that arrive together are a sample | Nothing to configure | This is inventing production. Five readings a minute apart might be a sample of five or five individuals from a busy station, and the product would be guessing which chart you meant |
| A chart-type field, chosen by hand, beside the sample size | A plant could draw individuals of a sampled characteristic if it wanted | Two fields that can disagree, and the disagreement is a chart that lies. The plan already decides it |

## Decision
**A characteristic's sampling plan is `QualitySpec.sample_size`, and the
chart type follows from it. Null or 1 is individuals and moving range, exactly
as before. `n` from 2 to 10 is X-bar and R.**

A sample is a record, not a grouping: `POST /quality/samples` takes exactly
`n` values and writes one `quality_checks` row per reading — a reading is
still a reading, with its own gauge, stamp and value — plus one
`quality_samples` row that ties them together and carries the stamp, the
order, the equipment and the gauge. `quality_checks.sample_id` is nullable
and nothing is backfilled: a reading with no sample was not taken as part of
one, and inferring otherwise would be inventing a sampling plan nobody wrote
down. `POST /quality/checks` on a sampled characteristic is refused with a
sentence that names the sample size.

On a sampled characteristic the plotted point is the sample mean, the limits
are `X̿ ± A2·R̄`, the range chart's are `D3·R̄` and `D4·R̄`, and the sigma
capability is computed from is `R̄/d2` — the within-sample spread, not the
spread of the means. The constants for n = 2…10 are written out in a table.

**The four Western Electric rules run on the means, once per sample.** A
sample is one observation of the process, so it gets one evaluation and at
most one non-conformance, and the evidence names the sample and every reading
in it. Five bottles whose individual values straddle three sigma and whose
mean sits inside the limits fire nothing, which is the point: the process did
not move.

A sample range beyond `D4·R̄` is its own signal, and it is **not** a fifth
Western Electric rule. The vocabulary of four rules, their windows and their
numbering stay what decision
[0036](0036-the-chart-draws-every-rule-the-plant-chooses-which-hold.md)
fixed. The range signal is recorded as rule `0`, meaning *not one of the
four*, and it always raises a hold whatever the plant holds on: limits built
from a mean range that one inflated sample widened are not limits, so a plant
that ignored it would be reading a chart it had quietly broken. The chart
payload says so on every response — `always_hold_rules` — so nobody has to
discover it. Whether a plant may choose otherwise is `spc_hold_rules`'
business and a later change.

## Consequences
Easier: a line that inspects several pieces at a time gets the chart it
should always have had, and its capability figure stops being inflated by
bottle-to-bottle variation.

Harder: a characteristic whose plan changes has history under two plans. The
readings taken under the old plan are **set aside** — not padded into
short samples, not charted as if they were the new plan — and the chart says
how many were set aside and why. That is the honest answer and it is not a
comfortable one.

Harder: `spc_min_points` now counts samples on a sampled characteristic, so a
plant that inspects five at a time every fifteen minutes waits five hours for
twenty points rather than twenty readings. The number means what it always
meant — points on the chart — but the wait is longer, and a plant that wants
limits sooner has to say so.

Not decided here: drawing the X-bar and R chart on the screen, and the panel
behind a point. `web/spc.js` says in one sentence that it cannot draw this
chart yet rather than drawing means as if they were readings.

## House rules touched
**Never invent production** (1): nothing is backfilled, nothing is inferred
from how close together readings arrived, and a reading taken outside a plan
is set aside rather than fitted into one.

**Unknown is not zero** (2): a characteristic with no sample size is not a
characteristic inspected one at a time by decree — it is one nobody has
written a plan for, and the product keeps drawing what it has always drawn
for it.

**Config, not code, at plant boundaries** (4): the plan is in the pack's
`quality_specs.json` and on the Specifications tab, and `fsmes pack check`
refuses a sample size that is not a whole number from 1 to 10, offline.
