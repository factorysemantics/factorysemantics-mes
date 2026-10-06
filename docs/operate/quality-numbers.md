# This plant's own quality numbers

Thirteen numbers in this MES used to be literals in the source: where a
process stops being capable, how many readings a control limit needs, how
fine a gauge has to be, how many serials a certificate prints, how deep the
packaging goes. Each one was a judgment somebody made once, and most of them
had a comment beside them arguing for the number rather than stating it —
which is what a judgment call sounds like before anybody calls it
configuration.

They are this plant's now. They are seeded from `[quality]` in the plant pack,
owned by the plant's database from then on, and **edited on Quality ›
Configuration** by somebody holding `quality.define` — with the value the
plant is actually running on beside each one. They follow one rule above all
the others:

> **The literal that was in the source is the shipped default, unchanged.**
> A plant that writes none of these keys behaves exactly as it did.

That is deliberate and it is the whole rollout plan. The setting lands, no
plant behaves differently, and the plants that want something else start
asking. Making a number configurable **and** moving it would be two changes,
and the second one needs its own argument.

## The keys

| Key | Ships | What it decides |
|---|---|---|
| `hold_rules` | `[1, 2, 3, 4]` | Which SPC rules raise a quality hold, from 1 to 5. Every rule is drawn on the chart whatever this says — [decision 0036](../decisions/0036-the-chart-draws-every-rule-the-plant-chooses-which-hold.md). Rule 5, a moving range beyond its upper limit, is the one the default leaves out: it is drawn and recorded on every plant and holds only where a plant adds `5` |
| `major_rules` | `[1]` | Which of those open a **major** non-conformance rather than a minor one. The words come from [this plant's severity list](quality-severities.md) |
| `cpk_capable` | `1.33` | The Cpk at or above which this plant says *capable* |
| `cpk_marginal` | `1.0` | The Cpk at or above which it says *marginal* rather than *not capable*. Must be below `cpk_capable` |
| `spc_min_points` | `12` | The fewest **points** control limits are drawn from — readings on a chart of individuals, samples on an X-bar and R chart ([the sampled case](#a-characteristic-inspected-several-pieces-at-a-time)). The pallet certificate prints whatever this says |
| `spc_history` | `200` | How far back a chart and the rules look |
| `gauge_ratio_adequate` | `10` | How many times finer than the tolerance a gauge must resolve to be called adequate |
| `gauge_ratio_floor` | `4` | Below this a gauge is too coarse to judge the tolerance at all. Must not be above `gauge_ratio_adequate` |
| `gauge_default_interval_days` | `365` | The calibration interval a new gauge gets when nobody says otherwise |
| `coa_serials_listed` | `200` | How many serials a pallet certificate prints before it says how many more there are |
| `serial_digits` | `6` | How many digits a generated serial carries after its prefix |
| `nc_code_prefix` | `"NC"` | What a non-conformance is called: `NC-00017`, or `NCR-00017` |
| `containment_max_depth` | `6` | How deep this plant's packaging goes |

```toml
[quality]
# A plant that inspects hourly, grades a four-of-five trend as major, and
# follows ANSI Z540 rather than AIAG.
spc_history = 60
major_rules = [1, 3]
gauge_ratio_adequate = 4
gauge_ratio_floor = 2
```

`fsmes pack check` reads every one of them offline, and **the Configuration
page refuses the same values in the same sentences** — one wording for one
rule, so a screen cannot accept what a pack file cannot hold. It refuses a number that
would leave the thing it decides unable to decide anything — control limits
from one reading, a serial with no digits, a Cpk bar that makes *marginal*
unreachable — and it refuses nothing else. A plant that wants twenty-five
readings behind its limits is answering its own question and is not being
second-guessed.

## What stays the product's, and why

Not everything near these numbers is a plant's to answer. The test is
[decision 0035](../decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md)'s:
**ask what breaks if two plants answer differently.** If nothing outside the
plant breaks, it is the plant's. If a number, a topic or an API field would
mean something different at the two plants, it is the product's.

- **The five SPC rules, their numbering and their windows** — the four
  Western Electric rules on the individuals chart and rule 5 on the moving
  range. A plant that renumbered them would publish `SpcSignal.rule = 3` while
  meaning something nobody else means by rule 3.
- **The range constants, d2 and D4 at a subgroup of two.** 1.128 and 3.267 are
  what the method is; a plant that changed them would be drawing a different
  chart under the same name.
- **The arithmetic behind Cp, Cpk and Pp.** Only the English word beside the
  figure moves. A Cpk of 1.21 is a Cpk of 1.21 everywhere; whether this plant
  calls that *marginal* is its own business.
- **The separator in a serial number.** The scan that recovers a counter from
  serials a plant has already issued reads `PREFIX-digits`; a plant that
  changed the hyphen would start numbering again at one over labels already
  on pallets. The width is the plant's; the hyphen is not.
- **The width of the number in a non-conformance code.** The prefix is the
  plant's word. Five digits is the product's shape.
- **A hard ceiling of twelve above `containment_max_depth`.** That number is
  also the guard that stops a containment walk running away over a cycle in
  the data. A plant chooses how deep its packaging goes; it does not get to
  switch off the guard.

## Two things that turned out not to be the plant's either

Some judgments are not one answer per plant. They are one answer per *thing*,
and a plant that has to give one answer for all of them is being asked the
wrong question. Two of these were:

**How much warning a gauge wants** — `gauges.warn_days`, thirty by default.
A quarterly calibration wants a fortnight's warning and an annual one wants
two months, and one plant owns both. It is a column on the gauge, set when
the gauge is registered and left blank for the default. Before this column
existed, the gauges screen decided *due soon* in JavaScript at three separate
places, so the shop floor's definition of the phrase lived in the browser and
the server did not know it.

**Whether a material is counted in pieces on a certificate** —
`materials.counted_in_pieces`, false by default. This one was a bug as much
as a gap: `services/coa.py` computed pallet capability only for materials
whose code started `UT-`, which is one plant's numbering convention living in
product code. Any plant not numbering its pieces that way got an empty
capability block on every pallet certificate and **no error anywhere**. The
migration sets the flag true for exactly the materials that prefix chose, so
nothing about an existing plant's certificates changes — it is the same
answer, given honestly by a flag instead of guessed from a name.

## Editing a plant's own quality numbers

**Quality › Configuration** (`/dashboard/config/quality`) lists every section
with its keys, the value this plant is running on, and whether that value is
the product's default or one the plant set. Eleven of its twelve sections are
these numbers, and each one is a box you type in.

1. Open **Quality › Configuration**. Each row's **Set to** column holds one
   box per key, with the key's name beside it.
2. Type the new value and press **Save**. There is one Save per row, because
   the Cpk bars and the gauge ratios are each *one judgment written as two
   numbers* — a screen that saved half of one would make *marginal*
   unreachable until you had typed the other half.
3. It is in force at once. No approval step, no restart, and no pack to
   re-apply: the next chart drawn, the next serial issued and the next
   certificate printed read the new number.

You need the **`quality.define`** capability, which the built-in Administrator
and Agent roles hold. Without it the page shows you every value and no box:
a number nobody can read is a number nobody can argue with, and the reading is
open to anybody who may see the plant.

Every change is written to the audit trail — who, when, what it was and what
it became. **Undo is typing the old number back.** There is deliberately no
revision history here, because nothing in this MES records *the Cpk bar that
was in force when I was judged*: a non-conformance stores its severity, and a
chart is drawn fresh every time. That is the difference between these numbers
and [the severity vocabulary](quality-severities.md), which does have a
draft → approve lifecycle, because the words it holds are written onto records
that outlive it.

### What the pack still does, and what it no longer does

`fsmes pack apply` **seeds** each key the pack carries, once. After that the
database owns it, and a later apply leaves it exactly as it is — the same rule
every other kind a pack seeds already keeps, and for the same reason: a pack
that reached back into a number somebody deliberately changed on a running
plant would be the pack overruling the plant.

So editing `plant.toml` and re-applying does **not** move a number this plant
has already taken ownership of. If that is what you want, change it on the
screen; the pack file is how a *new* plant starts, not how a running one is
steered. `fsmes pack status` reports the difference.

### Where the value lives, and what the screen can honestly say

The screen has two answers about any key — **the product's default,
unchanged** or **this plant set it** — and it says only those two. A row
written by `fsmes pack apply` is the second of them, because the plant did set
it, in its pack; naming *which* pack would be a guess, since by the time a
plant is serving the file it was built from is not recorded anywhere the
running process can see.

Underneath, a number is read in three layers, in this order:

1. the row in this plant's `plant_settings` table — what its administrator or
   its pack wrote;
2. the setting the pack compiled into the environment (`MES_QUALITY_*`);
3. the literal this version of the product ships.

Which is why **a plant that has never touched the page and never applied a
pack behaves exactly as it did before any of this existed** — and why
upgrading to this version moves no data: a plant already running on
`spc_min_points = 25` from its pack keeps drawing limits from twenty-five
readings through layer two, with an empty table.

## A characteristic inspected several pieces at a time

A specification can say how many pieces are measured at a time —
`sample_size` in the pack's `quality_specs.json`, on `POST /quality/specs`,
and one field on the Specifications tab. Left out, or one, it means what
every specification written before this existed means: one piece at a time,
charted exactly as it always was. Above one the readings are a subgroup and
the chart is X-bar and R, which is
[decision 0040](../decisions/0040-the-chart-type-follows-the-sampling-plan.md).

Nothing in the table above changes its name, but three of these keys answer
about a *point* rather than a reading, and on a sampled characteristic a point
is a sample:

- **`spc_min_points` counts samples.** Twelve means twelve samples — sixty
  bottles if they are taken five at a time — not twelve bottles. A plant that
  samples five at a time every fifteen minutes waits three hours for its first
  limits, and that is the right wait: twelve points is twelve looks at the
  process however many pieces each look holds.
- **`spc_history` counts samples** the same way, so a chart's window is the
  same number of points whichever kind it is.
- **`hold_rules` and `major_rules` are unchanged, and the rules run on the
  sample means.** Four readings in a row above the centre line is not a
  signal; four *samples* in a row is. One sample is one `evaluate`, so a
  sample whose five readings are all interesting raises at most one hold, and
  the hold's evidence names the sample, its five readings and the five rows
  they are stored in.

One thing on a sampled chart is not a Western Electric rule: a sample whose
**range** is beyond `D4·R̄` is its own signal, recorded as rule 0. It is held
on always and is not in `hold_rules`, because a range that wide means the five
pieces disagree — the mean they average to is not describing anything, so
there is nothing for a plant to opt out of. Every chart response says so in
`always_hold_rules`.

Capability is worked out from the within-process sigma, `R̄/d2`, and not from
the spread of the means, which is smaller by root n. A Cp computed the other
way would read about √5 too high on a sample of five, and the SPC screen's
*Sigma (within)* figure is the one capability uses with the mean's own spread
in its tooltip.

## See also

- [Plant packs](packs.md) — where these keys live
- [Who names the severities](quality-severities.md) — the words, as opposed
  to the numbers
- [What is still hard-coded](../design/config-audit-2026-09-21.md) — where
  these were rows Q0 to Q13
