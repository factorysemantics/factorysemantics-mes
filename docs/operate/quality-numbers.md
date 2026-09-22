# This plant's own quality numbers

Thirteen numbers in this MES used to be literals in the source: where a
process stops being capable, how many readings a control limit needs, how
fine a gauge has to be, how many serials a certificate prints, how deep the
packaging goes. Each one was a judgment somebody made once, and most of them
had a comment beside them arguing for the number rather than stating it —
which is what a judgment call sounds like before anybody calls it
configuration.

They are this plant's now. They live in `[quality]` in the plant pack, they
are listed on **Quality › Configuration** with the value the plant is
actually running on, and they follow one rule above all the others:

> **The literal that was in the source is the shipped default, unchanged.**
> A plant that writes none of these keys behaves exactly as it did.

That is deliberate and it is the whole rollout plan. The setting lands, no
plant behaves differently, and the plants that want something else start
asking. Making a number configurable **and** moving it would be two changes,
and the second one needs its own argument.

## The keys

| Key | Ships | What it decides |
|---|---|---|
| `hold_rules` | `[1, 2, 3, 4]` | Which Western Electric rules raise a quality hold. Every rule is drawn and recorded whatever this says — [decision 0036](../decisions/0036-the-chart-draws-every-rule-the-plant-chooses-which-hold.md) |
| `major_rules` | `[1]` | Which of those open a **major** non-conformance rather than a minor one. The words come from [this plant's severity list](quality-severities.md) |
| `cpk_capable` | `1.33` | The Cpk at or above which this plant says *capable* |
| `cpk_marginal` | `1.0` | The Cpk at or above which it says *marginal* rather than *not capable*. Must be below `cpk_capable` |
| `spc_min_points` | `12` | The fewest readings control limits are drawn from. The pallet certificate prints whatever this says |
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

`fsmes pack check` reads every one of them offline. It refuses a number that
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

- **The four Western Electric rules, their numbering and their windows.** A
  plant that renumbered them would publish `SpcSignal.rule = 3` while meaning
  something nobody else means by rule 3.
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

## Where to see what this plant is set to

**Quality › Configuration** (`/dashboard/config/quality`) lists every section
with its keys, the value this plant is running on, and whether that value is
the product's default or one the plant set.

It says only those two things, and that is not laziness. By the time a plant
is serving, a pack key **is** an environment variable — `fsmes pack apply`
compiled it and the file it came from is not recorded anywhere the running
process can see. Naming a pack on that screen would be a guess, and the
screen exists so nobody has to guess what their plant is set to.

## See also

- [Plant packs](packs.md) — where these keys live
- [Who names the severities](quality-severities.md) — the words, as opposed
  to the numbers
- [What is still hard-coded](../design/config-audit-2026-09-21.md) — where
  these were rows Q0 to Q13
