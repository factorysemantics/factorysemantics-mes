# What is still hard-coded that a plant might want to own

*An audit, 2026-09-21. Written against `main` at `3f1430a8`.*
*Re-run 2026-09-22 against `main` at `40ba6e5a`, with a fourth
classification on every candidate — **scope**, §1 — and a list of what
that leaves anybody to answer, §8. Every `file:line` below was checked
again; all of them still point at the code they name.*

Decision [0035](config-assistance.md) settled *how* configuration should work
in this MES — three tiers, six domains, one `Configuration` entry per domain,
draft then approve. The pilot built it once, for downtime reasons. This page
answers the next question, which is *what else*: every number, mapping and
fixed list still written into Python, JavaScript or HTML that encodes a
judgment a reasonable plant could make differently.

**This page recommends nothing.** Scott picks the next pilot from it, the same
way he picked reason codes from the scout's landscape report. What is here is
the list, organised the way the design organises the plant, with each item
saying what it is, where it lives, why it qualified, and roughly what it would
cost.

**Why a separate page rather than a new section of `config-assistance.md`.**
That page states decisions, and a decision stays true until somebody changes
it. This page is a snapshot of a codebase on one day, produced by a command
that can be run again tomorrow and give a different answer. Mixing the two
would make the design page go stale every time somebody adds a file.
`config-assistance.md` §10 points here; nothing else about it moved.

---

## 1. The test, and the tool

Decision 0035 §3 gives one test for whether a setting belongs to the plant or
to the product:

> **Ask what breaks if two plants answer differently.** If nothing outside the
> plant breaks, it is tier one. If a number, a topic or an API field would mean
> something different at the two plants, it is tier three.

`fsmes config-audit` applies the first half of that test mechanically. It reads
every Python file under `src/fsmes` with `ast` — so a literal knows the
function it sits in, and a named mapping such as `RULE_WINDOW` is reported once
rather than once per number — and reads the browser files as text, because the
judgments that live in a browser are refresh cadences and row counts, and a
regular expression finds those without adding a JavaScript parser to a product
that does not need one.

```
fsmes config-audit                      # everything, by domain
fsmes config-audit --domain quality     # one domain
fsmes config-audit --strong             # only the ones with a hedging
                                        #   comment or a name that says what
                                        #   they are; totals stay whole
fsmes config-audit --json > audit.json  # for diffing against the next run
```

**How a future engineer reruns it.** Run it. It takes about a second, touches
no database and no plant, and needs nothing installed beyond the package
itself. The useful habit is `fsmes config-audit --json > audit.json` on `main`
and again on a branch: what is new in the diff is what that branch just
hard-coded. It is deliberately a command and not a report, because a report is
true on the day it is written and this codebase gains a file a week.

### The fourth question: whose answer is it?

The tier test settles *where a setting lives*. It does not settle *who is
asked for the value*, and on a list of seventy-five that is the question that
decides whether the list is usable at all — because nobody can honestly pick a
counter-reset threshold for somebody else's counter.

So every candidate carries a fourth classification beside its domain, its size
and the reason it qualified: its **scope**.

| Scope | Whose answer it is | Where it lives | Who gets asked |
|---|---|---|---|
| `general` | the product's | one shipped default | a maintainer weighs in — the list is **§8** |
| `plant` | the plant's | `plant.toml` or masterdata, under its domain's `Configuration` tab | nobody outside the plant. The literal in the source today ships as the default |
| `object` | one object's | on the object — beside `state_map` and `cycle_seconds` in the tag map, or the equivalent row | nobody outside the plant. The engineer looking at that tag, machine, material or gauge sets it |

**The rule, applied to all of them:**

> Would two honest engineers **at the same plant** answer differently for two
> different objects? → `object`.
> Would two honest **plants** answer differently? → `plant`.
> Otherwise → `general`.

The order matters. The object question is asked first, because an answer that
differs between two tags on one line cannot be a plant-wide setting without
being wrong for one of them.

**Where the answers are kept, so a rerun keeps them.** In the tool, not in
this page. `src/fsmes/sim/config_audit_curated.py` holds one row per curated
candidate — id, domain, scope, the argument for that scope, and an *unsure*
line where there is one — written down as a table in source exactly the way
`DOMAIN_RULES` holds the domain assignments, because both are arguments
somebody had rather than data anybody generated. Each row is anchored on a
distinctive fragment of its line and **not on a line number**, so a refactor
moves a row and the tool reports where it moved to; a row whose anchor has
gone is reported as **stale** and has to be read again by a person, rather
than quietly pointing at whatever is now on line 703. A test fails if any row
goes stale.

```
fsmes config-audit --scope object     # the twelve that belong on a tag,
                                      #   machine, material or gauge
fsmes config-audit --scope general    # the nine anybody is asked about
fsmes config-audit --scope plant --domain controls
fsmes config-audit --json             # every candidate, with its scope and
                                      #   the argument for it
```

**What it found, on the day it was written:** **419 candidates in 235 files
scanned, with 75 files skipped by rule** — 155 of the 419 carry strong
evidence (a hedging comment beside them, or a name that says what they are) and
264 are weaker matches worth a glance. Every skip is named with its reason in
the output, so "we looked at everything" is checkable rather than asserted, and
a file the domain table does not place is reported as `unassigned` rather than
folded into the biggest domain.

Those 419 are raw matches, not 419 things to build. The curated list in §4
below is **75 candidates** that survived a person reading them.

---

## 2. Calibration: the two worked examples, checked

Scott and the ask session worked two of these out by hand before the tool
existed. Both were re-read line by line, and both hold.

**Cpk 1.33 and 1.0 — the bar at which a process is called capable.**
`src/fsmes/services/spc.py:246-251`. `if cpk >= 1.33: … "in control and
capable"`, down through `1.0` for `"marginal"`, and below that `"not capable"`.
1.33 is a widely used industry convention — it is not a law of physics, and a
plant with looser tolerances or a stricter quality culture could set a
different bar and still be telling the truth about itself. The `cpk` number
itself keeps its meaning; only the English word beside it moves. **Qualifies.**
Note it has a second, independent copy in the browser at
`src/fsmes/web/spc.js:108`, which picks the verdict colour — so this is a
medium, not a small.

**80% through an interval — the maintenance "coming due" warning.**
`src/fsmes/services/maintenance.py:90-92`: `"due_soon": 0.8 <= fraction < 1.0`,
under the comment *"A plant wants warning, not a surprise. Anything past 80% is
worth putting on a shift plan even though it is not due yet."* The comment is
the evidence: it argues for the number instead of stating it, which is what a
judgment call sounds like before anybody calls it configuration.
**Qualifies.** Its twin is at `src/fsmes/web/maintenance.js:44`, so this is a
medium too.

**Both calibration examples carry a scope, and both are the plant's.** The
Cpk bar is `Q0` in the tool — it is the one candidate this page works through
in prose and never gave a row number in §4, so the curated list the tool
carries is seventy-six where §4's list is seventy-five, and both totals are
said out loud rather than one quietly becoming the other. The maintenance
warning is `P1`. Eighty per cent is a fraction of each plan's own interval, so
a weekly greasing and a yearly overhaul do not need different fractions; what
differs is how much warning a plant's spares lead time needs. The Cpk bar
carries an *unsure* line: a plant that grades characteristics critical, major
and minor might want a higher bar on a critical one, which would make it
`object`.

**One thing in the handoff's Quality example did not survive the reading.**
The handoff asked whether *which of the four Western Electric rules are active,
and their window sizes*, qualifies. It splits:

- **Which rules are on: arguable, and the design page currently says no.**
  0035 §3 places "SPC rules 1–4" in tier three with the note *"A rule a plant
  can switch off is a chart that lies."* That reasoning is about the *chart*,
  and it is sound — a chart drawn with rule 4 off, presented as an SPC chart,
  is a chart that does not say what it appears to say. Whether a plant may
  choose which rules raise a *hold* is a different question, and 0035 does not
  answer it. It is listed in §4 as **Q5**, flagged as needing a decision
  against 0035 before anybody builds it, not as settled.
- **The window sizes: tier three, and the example was wrong.** 2-of-3, 4-of-5
  and 8-in-a-row *are* Western Electric rules 2, 3 and 4. A plant that changed
  the windows would still be publishing `SpcSignal.rule = 3` while meaning
  something nobody else means by rule 3 — the API field changes meaning, which
  is the tier-three side of the test. `RULE_WINDOW = {1: 1, 2: 3, 3: 5, 4: 8}`
  (`src/fsmes/services/spc.py:261`) is the product's, and should stay.
- **The severity mapping: qualifies, and the exact line is
  `src/fsmes/services/spc.py:370`** — `severity="major" if signal["rule"] == 1
  else "minor"`. The handoff was right that it is crude. It is **Q2** below.

---

## 3. The three rules

Everything on this list gets checked against these three before anybody builds
a screen for it. Stated once, here, rather than repeated per item.

### Rule one — a sensible default, not an empty list on day one

Reason codes shipped with six ISO-22400-shaped words rather than a blank
plant, because a plant that has to invent its own vocabulary before it can
record a stop will record no stops.

For this list the rule has an easier form than it did for reason codes, and it
is worth saying why: **every candidate here already has a value.** The
hard-coded literal *is* the sensible default, already chosen, already shipped,
already running in production. So the rule becomes a constraint on the PR
rather than a research task:

> The literal that is in the source today becomes the shipped default,
> unchanged. A plant that configures nothing gets exactly what it gets now.

A PR that makes the Cpk bar configurable **and** moves it from 1.33 is two
changes, and the second one needs its own argument. This also gives every one
of these a clean, boring rollout: the setting lands, no plant behaves
differently, and the plants that want something else start asking.

The one place the rule bites harder is a candidate that is a *list* rather than
a number — non-conformance severity (**Q3**) is the example. Today it is a free
`String(20)` that happens to hold only `minor` and `major` because those are
the only two words the code writes. The shipped default there is those two
words, and choosing a third to be helpful would be inventing a plant.

### Rule two — search and sort once a list passes twelve

A list is honest as a plain list up to **twelve** items. Past twelve it needs
search, and a stated sort.

**Why twelve.** This product has already answered "how many rows is one
screenful" once, and it answered twelve:
`src/fsmes/services/analysis.py:440` scopes a Gantt to `limit: int = 12` under
the comment *"the first screenful"*. Reusing that number rather than inventing
a second one is the whole argument — a second number for the same idea is how a
codebase grows two answers to one question.

**Why a plain list is honest below it.** Twelve rows fit on a screen without
scrolling. A person can read all of them, count them, and know they have seen
the whole thing. Search there is furniture: it costs a control and a focus trap
and buys nothing, because the eye is already faster.

**Why a list without search above it is the same failure a free-text box was.**
The free-text box failed because a person could not tell whether the word they
wanted already existed, so they typed a new one and the plant ended up with
`bearing`, `Bearing` and `brg fault` meaning one thing. A scrolled list with no
search reproduces exactly that: the word is in there, the person cannot find
it, and they reach for *Add a new one*. A list long enough to scroll and
without search does not prevent the duplicate-vocabulary failure — it just
makes it slower to cause.

**And sort.** Below twelve, whatever order the plant authored is fine and needs
no explanation. Above twelve the order must be chosen, stated on the screen,
and stable between loads — an unstable order in a long list is a person
re-reading rows they already rejected.

### Rule three — kind-tagging, translated for things that are not rows yet

The approvals panel works because everything that waits for somebody carries a
**kind**: `services/review.py`'s `KINDS` registry (`review.py:421`) maps a kind
name to its capability, its *what is waiting* query and its *what would change*
reviewer. One entry today, `downtime_reason`. A second kind joins by writing a
registry entry, and the panel, the walkthrough and the approve button all work
for it without being touched. An MCP tool per item is then mechanical, because
the kind already names the thing, its capability and its endpoints.

The handoff asks the hard version of this: **what does "tagged by kind" mean
for a candidate that is still a Python literal?** The honest answer has two
halves, and only one of them applies to everything.

**It is a rule about the eventual row shape — and it binds the PR that creates
the row, not the literal that exists today.** There is nothing to tag on a
line that reads `if cpk >= 1.33:`. Nobody should go and annotate 75 literals.
The rule applies at the moment a candidate stops being a literal, and what it
forbids is doing it in two steps.

**Concretely, the PR that makes any item on this list configurable must add,
in that same PR:**

1. **A `ConfigSection` in `src/fsmes/modules.py`**, naming its domain and
   carrying a stable `key`. This part applies to *every* candidate here,
   including the ones that will only ever be a key in `plant.toml` and never a
   database row — the section key is what the Configuration page lists and what
   anything linking to the setting points at. `tests/test_configuration_sections.py`
   already refuses a section whose domain has no nav entry, and refuses a nav
   entry with no sections, in both directions.
2. **A `KINDS` entry in `src/fsmes/services/review.py`** — *only* if the
   setting has a draft-then-approve step. A number a plant administrator edits
   and which takes effect when saved has no pending state, nothing waits for
   anybody, and inventing a kind for it would be a registry entry that never
   has a row.

**And one exception, which scope decides.** An `object`-scope setting gets
**no `ConfigSection` of its own.** It is not a screen; it is a column on
something that already has one. A per-tag counter-reset threshold appears
where the tag is configured — in the tag map, under controls engineering's
`Configuration` tab — beside `state_map` and `cycle_seconds`, and a section
key pointing at a second screen that lists every tag's threshold would be a
second place to set one thing. The same goes for a per-gauge warning window
and a per-material buffer. `general` and `plant` settings keep the rule above
unchanged.

So: **the section key applies to everything a plant configures as a setting of
its own, from the first PR. The review kind applies to whatever gets an
approval step, from the first PR that gives it one. An `object` setting joins
the row it belongs to and gets neither.** Neither is ever retrofitted. That is the whole point of stating the rule
now, because the nav *was* retrofitted — `config-nav-restructure` had to go
back and undo one top-level chip after the pilot shipped, and the test that
lands with it exists specifically so nobody does it a second time.

**What this buys.** Once an item has a section key and (where it needs one) a
kind, the MCP tool is generated work rather than design work: the kind names
the capability, the section names the screen, and `src/fsmes/mcp/` gains a
function that reads the same registry the panel reads. Nothing new is invented
at that point, which is the test of whether the tagging was done early enough.

---

## 4. The list, by domain

Seventy-five candidates, after a person read them. Sizes are a rough sense of
effort and not a commitment: **small** is a key read in one place, **medium** is
a key plus a screen or an API field, **large** is a new database-backed
vocabulary with a screen of its own.

Where a candidate has a second copy in the browser, that is noted, because a
duplicated literal is what turns a small into a medium.

**By scope: 9 `general`, 54 `plant`, 12 `object` — 75 in all.** Only the nine
are questions for anybody reading this page, and §8 lists them. The other
sixty-six are *routed*, not asked: fifty-four to a plant's `Configuration` tab
under the domain named on their row, twelve to the row of the tag, machine,
material or gauge they describe. Every one of those sixty-six ships the
literal that is in the source today, so a plant that configures nothing
behaves exactly as it does now.

**Eight rows say they are unsure** which side of a line they fall on — `Q0`,
`Q4`, `Q10`, `P12`, `C4`, `C13`, `A21`, `A22` — and each says what would
settle it rather than picking quietly. Seven of them are in the table below;
the eighth is `Q0`, the Cpk bar, which lives in §2. The tool carries all
seventy-six; this section's total is seventy-five, and they differ by that one
row.

### Quality engineering — 13

> **Built, 2026-09-22.** Every row in this table is answered. Q3 is the
> product's second draft-then-approve vocabulary
> ([who names the severities](../operate/quality-severities.md)); Q5 is
> [decision 0036](../decisions/0036-the-chart-draws-every-rule-the-plant-chooses-which-hold.md);
> the rest are `[quality]` keys and two object columns
> ([this plant's own quality numbers](../operate/quality-numbers.md)). **Every
> one ships the literal that is in the *Where* column**, so a plant that
> configures nothing behaves exactly as it did — which is rule one, and it is
> the whole rollout plan. The table below is left as it was read on
> 2026-09-21, with a *settled* line on each row in
> `fsmes config-audit --scope plant --domain quality`, because deleting a row
> the day somebody acted on it would lose the argument with it.

| | What it is | Where | Why it qualified | Size | Scope — whose answer is it? |
|---|---|---|---|---|---|
| **Q1** | Fewest readings before control limits are drawn at all | `services/spc.py:45` `MIN_POINTS = 12` | Comment already hedges: *"Below this the limits move so much with each new reading that they mislead more than they inform."* A plant that insists on 25 produces the same payload shape. | small (the pallet certificate prints the word "twelve" at `services/coa.py:380`) | **`plant`** — twelve or twenty-five is one plant's trust in its own data, the same for every characteristic |
| **Q2** | Which SPC rule counts as a *major* finding | `services/spc.py:370` `severity="major" if signal["rule"] == 1 else "minor"` | A plant that treats a four-of-five trend as major changes only its own triage queue; rule numbers and the NC payload are identical. | small | **`plant`** — one triage policy per plant; two characteristics do not want different answers |
| **Q3** | The non-conformance severity vocabulary itself | `domain/quality.py:99` `String(20), default="minor"`; written at `services/quality.py:148` and `spc.py:370`, unvalidated at `api/routers/quality.py:274` | Named in 0035 §2 as quality-engineering configuration and in §3 as *"free text by accident, not by design"*. `critical/major/minor/observation` breaks nothing outside the plant. | large | **`plant`** — the plant's own words, used the same way everywhere in it |
| **Q4** | How far back a chart and the rules look | `services/spc.py:49`, `:145`, `:288` (`limit: int = 200`); `api/routers/quality.py:368` | A plant inspecting every fifteen minutes and one inspecting hourly want different histories behind one chart. | small | **`plant`** — a plant inspecting every fifteen minutes wants a different history from one inspecting hourly. *Unsure: it could be per characteristic — see the tool's row* |
| **Q5** | Which Western Electric rules raise a hold | `services/spc.py:80-106` — all four always did | **Decided 2026-09-22 by [0036](../decisions/0036-the-chart-draws-every-rule-the-plant-chooses-which-hold.md)**, and built: every rule is drawn and recorded on every plant, and `[quality] hold_rules` chooses which of them open a non-conformance, defaulting to all four. Window sizes stay tier three — see §2. | medium — **done** | **`general`** — the question *may a plant choose at all* was the product's, and 0036 answered it. What the answer hands the plant is a `plant` key — **§8** |
| **Q6** | The gauge rule of ten, and its floor of four | `services/gauges.py:179` `"adequate": ratio >= 10`, `:185` `ratio >= 4` | Comment: *"Four is the usual floor."* AIAG says 10:1, ANSI Z540 says 4:1; both plants are right and nothing off-plant reads the word `adequate`. Directly analogous to the accepted Cpk case. | small | **`plant`** — AIAG says 10:1 and ANSI Z540 says 4:1; a plant follows one standard for every gauge it owns |
| **Q7** | A gauge is "due soon" thirty days out | `web/gauges.js:31`, `:41`, `:80` | The maintenance 80% judgment in another domain — and invented in the browser: the server deliberately returns only `days_until_due` and `overdue` (`services/gauges.py:53-54`). Quarterly calibration wants 14 days; annual wants 60. | medium (needs to reach the browser) | **`object`** — a quarterly calibration wants fourteen days of warning and an annual one sixty — it belongs beside the gauge's own interval |
| **Q8** | Default calibration interval for a new gauge | `services/gauges.py:59` `interval_days: int = 365`, repeated at `api/routers/quality.py:354` and `web/gauges.js:122` | The per-gauge column exists; only the plant's house default is product-owned, in three places. | small | **`plant`** — the per-gauge interval is already the engineer's; this is the plant's house default |
| **Q9** | Which materials are "pieces" on a pallet certificate | `services/coa.py:277` — `m.startswith("UT-")` | One plant's material-numbering convention in product code. Any plant not numbering pieces `UT-*` gets an empty capability block and no error. Arguably a latent bug as much as a config gap. | medium | **`object`** — whether a material is counted in pieces on a certificate is a fact about that material |
| **Q10** | How many serials a certificate lists before truncating | `services/coa.py:175`, `:177` — `[:200]` | A customer-facing certificate that must list every unit, versus one that must stay printable, is the plant's and its customer's decision. | small | **`plant`** — the plant's agreement with the people who read the certificate. *Unsure: it could sit on the customer* |
| **Q11** | The serial number format | `services/serialization.py:87` `f"{prefix}-{number:06d}"` | Eight digits or a different separator affects only the plant's own labels and scans; a serial is an opaque value, not a field name. | medium (the recovery scan at `:77-80` must keep reading old serials) | **`plant`** — one label scheme per plant, read by its own scanners |
| **Q12** | The non-conformance code format | `services/quality.py:158` `f"NC-{nc.id:05d}"` | A plant that calls them NCRs changes only its own `code` strings; nothing off-plant is keyed on the prefix. | small | **`plant`** — a plant that calls them NCRs calls all of them NCRs |
| **Q13** | How deep a containment tree may go | `services/serialization.py:48` `MAX_DEPTH = 6` | Comment: *"a sixth is somebody's mistake, not a plant"* — which is an assertion about somebody else's packaging. Piece→stack→pack→case→pallet→truck→container is not a mistake. | small (it doubles as a loop guard, so the key needs a hard ceiling) | **`plant`** — how deep the plant's own packaging goes; one answer, not one per shipment |

*Scope: 1 general · 10 plant · 2 object — 13 in all.*

### Manufacturing / process engineering — 12

| | What it is | Where | Why it qualified | Size | Scope — whose answer is it? |
|---|---|---|---|---|---|
| **P1** | The maintenance "coming due" warning at 80% | `services/maintenance.py:92`; twin at `web/maintenance.js:44` | **Calibration example.** See §2. | medium | **`plant`** — a fraction of each plan's own interval, so it already scales; what differs between plants is spares lead time |
| **P2** | Fallback cycle time when a machine has no rating | `services/scheduling.py:37` `DEFAULT_CYCLE_SECONDS = 3.0` | Comment admits it: *"a schedule built on it is a guess, which `uses_default_cycle` reports rather than hides."* A bottling line and a CNC cell want different guesses. | small | **`plant`** — the per-object answer is the machine's own rating, which already exists; this is the plant's fallback when there is none |
| **P3** | How long a maintenance job with no plan is assumed to take | `services/maintenance.py:284` and `services/scheduling.py:109` — both `60.0` | Every corrective job. It sizes the backlog's downtime figure and the block the scheduler reserves; a supervisor deciding whether tonight is the night is reading it. | small (one key, two call sites) | **`plant`** — same shape as P2: the plant's fallback when a job has no plan |
| **P4** | Default expected duration of a new maintenance plan | `services/maintenance.py:227` `30.0`, repeated at `api/routers/maintenance.py:21` and `web/maintenance.js:208` | The per-plan column exists; only the house default is hard-coded, three times. | small | **`plant`** — the per-plan column exists; only the house default is in code |
| **P5** | The default reporting window — eight hours, "a shift" | `services/analysis.py:196,438,549,673,779`; `api/routers/analysis.py:27`, `kpis.py:14`, `equipment.py:105`, `dashboard.py:86`, `assist.py:60-61`; `web/common.js:493` | The comment says the assumption out loud: *"then eight hours - a shift."* A twelve-hour-shift plant wants 12. Every payload already carries `requested_hours`, so nothing downstream is keyed on the default. | medium | **`plant`** — shift length, which the plant already states in `shift_patterns` |
| **P6** | The fixed list of windows every screen offers | `web/common.js:484` `WINDOWS = [[0.25,…],[1,…],[8,…],[24,…],[168,…]]` | A tier-one list (the plant's shifts, which already live in `shift_patterns`) with a tier-three literal in front of it. Same judgment as P5, one level up. | medium | **`plant`** — which windows a plant's people work in; its own shifts are the honest source |
| **P7** | How many machines a Gantt draws | `services/analysis.py:440` `limit: int = 12`; `api/routers/analysis.py:65` | A six-station cell and a 108-station plant want different screenfuls. The payload states `machines_shown` / `machines_total`, so nothing is hidden by moving it. *(This is also the twelve that rule two borrows.)* | small | **`plant`** — one plant, one screenful, for all of its screens |
| **P8** | How far back "the previous shift" may reach | `services/calendar.py:52` `PREVIOUS_HORIZON_DAYS = 14` | Comment hedges: *"Two weeks covers a plant that ran nothing over a shutdown."* A seasonal plant with a six-week shutdown answers differently, and the refusal sentence is the plant's own. | small | **`plant`** — a seasonal plant with a six-week shutdown answers differently from a continuous one |
| **P9** | The default working week | `services/calendar.py:469` `days: str = "1111100"`; also `api/routers/scheduling.py:21`, `pack/masterdata.py:451`, `mcp/scheduling.py:53`, `domain/calendar.py:45` | Sunday–Thursday is a real working week. The mask format is a product fact; which mask is the default is not. At `pack/masterdata.py:451` a pack that omits `days` is silently seeded a five-day plant — arguably the honest fix there is to refuse. | small (one key, five sites) | **`plant`** — the mask format is the product's; which mask is the plant's calendar |
| **P10** | The floor below which no rate is reported at all | `services/coverage.py:285` `MIN_OBSERVED_SECONDS = 10.0` | This product already made the sibling judgment a pack key — `[oee] coverage_floor` — with the argument that *"a number vanishing off a screen because of a threshold nobody chose is its own kind of dishonesty."* By its own reasoning this second suppression threshold belongs beside it. | small | **`plant`** — its sibling `[oee] coverage_floor` is already a pack key, argued that way when it was made one |
| **P11** | The schedule board's default horizon | `services/scheduling.py:210` `hours: float = 24.0`; `api/routers/scheduling.py:66`; the fixed picker at `web/schedule.html:48` | A job shop planning a fortnight and a line planning a shift want different boards. | small (medium if the offered options become a plant list) | **`plant`** — a job shop planning a fortnight and a line planning a shift want different boards |
| **P12** | What counts as a good yield | `web/orders.js:43-44` — `>= 0.98` good, `>= 0.90` warning | A foundry at 85% and an assembly line at 99.5% are both healthy. This is a plant's own definition of acceptable, read by nobody outside it. | medium (browser-side) | **`object`** — a difficult casting and a simple assembly do not share a good yield, and one plant makes both. *Unsure: a single-product plant would call this plant-wide* |

*Scope: 0 general · 11 plant · 1 object — 12 in all.*

### Controls engineering — 14

| | What it is | Where | Why it qualified | Size | Scope — whose answer is it? |
|---|---|---|---|---|---|
| **C1** | Tag staleness — "not live after a minute" | `services/tags.py:45` `STALE_AFTER_SECONDS = 60.0`; applied `:165-166`, published `:202`, `:394`; screens `web/tags.js:50,122`, `web/machine.js:82,105` | A plant polling a historian every five minutes shows every machine permanently stale. The product already publishes the threshold *as data* (`stale_after_seconds`), which is the tell that it wants to be a setting. | medium | **`object`** — a counter published every 200 ms and a historian value polled every five minutes go stale at different ages — it belongs beside `cycle_seconds` |
| **C2** | OPC reconnect wait — a flat three seconds, forever | `integrations/opc/agent.py:1016`, `:1022` `await asyncio.sleep(3)` | A flaky wireless gateway wants a backing-off retry rather than a hammer; a wired backbone wants one second. The number is also duplicated into the operator-visible log line *"retrying in 3s"*. | small | **`object`** — a wireless gateway and a wired backbone are two endpoints in one plant; it belongs on the connection |
| **C3** | Booking retry count and backoff before readings are given up | `integrations/opc/agent.py:519-520` `BOOK_ATTEMPTS = 4`, `BOOK_BACKOFF_S = 0.5` | Its own fourteen-line comment names `sqlite_busy_timeout_ms`, which **is** already a setting — so one half of this pair is configurable and the other is not. | small | **`plant`** — the agent arguing with its own database — the same policy for every tag it books |
| **C4** | Inspection-group grace and timeout | `integrations/opc/agent.py:118` `GROUP_GRACE_S = 0.25`, `:121` `GROUP_TIMEOUT_S = 3.0` | Both comments derive them from `opc_publish_ms`, which is configurable; these are not. A station behind a gateway that fans tags out over half a second records every unit as partial. | medium | **`object`** — a station behind a gateway that fans its tags out needs more grace than one that publishes them together. *Unsure: it may be per connection* |
| **C5** | How long a station's current order is trusted | `integrations/opc/agent.py:123` `ORDER_CACHE_S = 10.0` | "Trusted" is the hedge. A job-shop station changing over every ninety seconds mis-books up to ten seconds of units to the previous order at every changeover. | small | **`object`** — a job-shop station changing over every ninety seconds trusts a cached order far less than a continuous line does |
| **C6** | Setpoint "did the process follow?" fraction | `services/adjustments.py:32` `FOLLOWED_FRACTION = 0.5`, applied `:170` | The comment reasons from an assumed first-order lag. A fast electric heater and a two-tonne oil bath disagree, and the same 0.5 marks one plant's every adjustment verified and the other's every adjustment failed. | medium | **`object`** — a fast electric heater and a two-tonne oil bath follow a setpoint differently; it belongs on the loop, beside its lag |
| **C7** | Default verification wait: three time constants, a 40 s assumed lag, a 30 s floor | `services/adjustments.py:80` `max(3 * float(meta.get("lag_s", 40.0)), 30.0)` | Three separate judgments about somebody else's process, on one line. A plant with a twenty-minute oven verifies at thirty seconds and records a failure that did not happen. `verify_after_seconds` is already a per-recommendation field, so only the default is at stake. | small | **`object`** — `lag_s` is already a per-recommendation field; the 40 s and the 30 s floor are the same per-object judgment left in code |
| **C8** | Unified-namespace delivery retry policy | `services/uns.py:30-32` `MAX_ATTEMPTS = 8`, `BASE_BACKOFF_SECONDS = 5`, `MAX_BACKOFF_SECONDS = 3600` | A broker restarted nightly for twenty minutes kills every queued event at eight attempts. `uns_qos`, `uns_batch`, `uns_inflight` and `uns_poll_seconds` are all already settings — the retry policy is the one piece left in code. | small | **`plant`** — one broker per plant, one retry policy |
| **C9** | How often an approved trigger reaches the running agent | `services/triggers.py:260` `reload_seconds: float = 30.0` | The docstring promises an approval *"reaches the agent without a restart"*; the number quietly bounds how fast. A plant stopping a line on an SPC signal wants five seconds. | small | **`plant`** — one agent, one reload cadence |
| **C10** | Default cooldown on a new trigger | `services/triggers.py:131` `cooldown_seconds: float = 300.0`; also `api/routers/triggers.py:23`, `domain/triggers.py:54` | Five minutes of silence after a *machine down* trigger is right on a continuous line and wrong on a station with forty-second cycles. The per-trigger value is already tier one; only the inherited default is not. | small | **`plant`** — the per-trigger cooldown is already the engineer's; only the inherited default is in code |
| **C11** | Process-value history sampling ratio and floor | `integrations/opc/agent.py:76-77` `HISTORY_RATIO = 10`, `MIN_HISTORY_MS = 1000` | `tag_retention_days` is already a setting; how densely those days are filled is not. The comment's *"buys nothing a person will read"* is an assertion about somebody else's engineer. | small | **`plant`** — a ratio against each tag's own publish rate, so it scales already; what is left is how much the plant stores |
| **C12** | Order-code write cadence and adjustment dispatch cadence | `integrations/opc/agent.py:67` `_ORDER_SYNC_SECONDS = 2.0`; `:1047` `_ADJUSTMENT_POLL_SECONDS = 5.0` | Two hundred machines on one endpoint is a hundred database reads a second to discover nothing changed. `api/routers/adjustments.py:60` makes a user-facing promise — *"The OPC agent writes within seconds"* — that rests on the second one. | small | **`plant`** — agent-wide cadences against one database |
| **C13** | Container member retry count | `integrations/opc/agent.py:107` `MEMBER_RETRIES = 3` | *"in case its pieces were written just after it"* — "in case" is the hedge. Three is a guess about one line's publishing order. | small | **`object`** — three is a guess about how one line publishes a container and its pieces. *Unsure: it could be plant-wide* |
| **C14** | Counter-reset detection — "fell below half" | `integrations/opc/agent.py:703` `if last is None or value < last // 2:` | **Flagged for review as the closest of these to tier three**, because it sits directly on house rule 1. The comment says *"a counter that fell to near zero is a PLC reset"*; the code says *below half*, and those are not the same sentence. A counter that wraps at 65535 and one reset per shift disagree about what "near zero" means. | small, but it needs the never-invent-production argument made explicitly | **`object`** — **the first concrete case of bulk editing.** A counter that wraps at 65535 and one zeroed every shift are two tags, not two plants — the threshold belongs on the tag, beside `state_map` and `cycle_seconds`, set by the controls engineer looking at that counter |

*Scope: 0 general · 6 plant · 8 object — 14 in all.*

### Supply chain / ERP — 7

| | What it is | Where | Why it qualified | Size | Scope — whose answer is it? |
|---|---|---|---|---|---|
| **S1** | ERP delivery retry policy | `services/erp.py:41-43` `MAX_ATTEMPTS = 8`, `BASE_BACKOFF_SECONDS = 5`, `MAX_BACKOFF_SECONDS = 3600` | An ERP down for a four-hour weekly window kills a shift of confirmations. `mark_refused` (`:188-207`) already argues at length that *"eight attempts over an hour only delay the moment a person hears about it"* — and resolved it by special-casing refusals rather than by moving the number. | small to medium (`attempts` is on the outbox screen) | **`plant`** — one ERP, one maintenance window, one retry policy |
| **S2** | Which ERP order statuses the MES will take | `integrations/erp/erpnext_adapter.py:44` `_OPEN_STATUSES = ("Not Started", "In Process")` | ERPNext's vocabulary, which sites customise, written into MES code as an order-release policy. The comment states it as a policy, not a fact. | medium (belongs beside `erpnext_company`) | **`plant`** — the site's own ERPNext customisation, beside `erpnext_company` |
| **S3** | ERP read-back agreement tolerance | `integrations/erp/erpnext_adapter.py:204` `rel_tol=1e-3, abs_tol=0.01` | The comment contains the two-plants test by accident: *"a Float rounded to the site's float precision, which is two decimals **on some sites**"*. A site on precision 4 with gram-level quantities has a real disagreement hidden by it. | small | **`plant`** — one site, one float precision |
| **S4** | ERP HTTP timeouts | `integrations/erp/erpnext_adapter.py:97` `timeout: float = 30.0` (and `from_settings` at `:264-275` never passes one); `integrations/erp/rest_adapter.py:12` `timeout=10.0` | An ERPNext bench across a VPN posting a Manufacture entry against a large BOM routinely exceeds thirty seconds, and every one of those burns an attempt. `jev_timeout_seconds` is a setting with a paragraph of justification; the path that talks to a system the plant does not own has none. | small — `from_settings` already reads eight `erpnext_*` settings and could read a ninth | **`plant`** — one link between this plant and its ERP |
| **S5** | Material shortage threshold — zero buffer | `services/staging.py:76` and `:86` — `will_run_out: short > 0` | A plant wanting safety stock or a lead-time buffer ("warn me at eight hours of cover") gets no warning until it is already short. The docstring hedges the *other* half of the same judgment and never mentions this one. | medium (large if per-material) | **`object`** — a screw with a week of lead time and a casting with three months want different buffers — it belongs on the material |
| **S6** | Default priority for an ERP order that carries none | `integrations/erp/contract.py:29`, `:48`; `services/workorders.py:72`; `domain/workorders.py:43` — all `50` | `erpnext_adapter.py:306-308` refuses to invent a priority — *"inventing one here would outrank the MES's own dispatch ordering with a number nobody set"* — and then defers to an invented number. On a plant using a 1–9 scale every ERP order sorts behind everything a person typed. | small | **`plant`** — priority is already per order; only what an order carrying none inherits is in code |
| **S7** | Confirmation time-agreement tolerance | `integrations/erp/validate.py:50` `SECONDS_TOLERANCE = 1.0` | *"because a file can carry whole seconds where the MES had more"* is a fact about one plant's incumbent. `incumbent.py:86` already reads a plant-supplied `tolerances` object for exactly this; this constant was the one not routed through it — arguably a bug rather than a new setting. | small | **`plant`** — a fact about one plant's incumbent; `incumbent.py` already reads a plant-supplied `tolerances` object |

*Scope: 0 general · 6 plant · 1 object — 7 in all.*

### Plant administration — 25

| | What it is | Where | Why it qualified | Size | Scope — whose answer is it? |
|---|---|---|---|---|---|
| **A1** | Default role a new account gets | `services/auth.py:170` and `api/routers/auth.py:80` — `role: str = "operator"` | One plant onboards everyone as `viewer` and grants up. The role *codes* stay tier three; only which is the default moves. | small | **`plant`** — one plant onboards everyone as `viewer` and grants up; another starts them on the floor |
| **A2** | PBKDF2 iteration count — the product's entire password policy | `services/auth.py:110` `_ITERATIONS = 240_000` | The count is written into the stored hash (`:116`) and read back at verify (`:124`), so moving it breaks no existing account. Worth noting on its own that this is the *only* password-policy number in the product: no length rule, no complexity rule, no lockout. | small | **`general`** — a password floor is the product's, not a preference a plant may quietly lower — **§8** |
| **A3** | Default page size and hard ceiling for every list endpoint | `api/paging.py:28-29` `DEFAULT_LIMIT = 50`, `MAX_LIMIT = 500` | Comment: *"Big enough that a shift's work fits, small enough that no screen ever ships megabytes by accident"* — one plant's shift. The envelope shape is what an API reader depends on, not the number. | small (medium if `le=500` must stay in the OpenAPI schema) | **`plant`** — a plant with three thousand characteristics and one with fifty want different ceilings |
| **A4** | Pending-approvals panel page size — its own number, different from A3 | `api/routers/dashboard.py:378` `Query(20, ge=1, le=100)` | The panel has no pager, so item 21 is simply not on the screen. The endpoint's own docstring argues *"A draft nobody acts on waits, visibly"*, which is the argument for letting a plant choose. | small | **`plant`** — how many waiting drafts fit on one plant's screen |
| **A5** | Admin screen page sizes | `web/admin.js:23` `userPageSize = 25`, `:29` `routingPageSize = 25` | `api/routers/admin.py:158-160` says *"Three hundred employees is an ordinary plant"*; 25 is one plant's answer to that. | small | **`plant`** — twenty-five rows is one plant's answer to three hundred employees |
| **A6** | Floor dashboard page sizes and refresh clocks | `web/app.js:20-24` `REFRESH_MS = 2000`, `PENDING_REFRESH_MS = 30000`, `MACHINE_PAGE = 24`, `ORDER_PAGE = 10`, `SPEC_CHOICES = 200` | The file's own header narrates these as tuning against one plant: *"On the 108-station lab plant that was the Floor summary's whole cost."* A plant on a thin WAN link wants a ten-second poll. | small (medium if they should come from the server) | **`plant`** — a plant on a thin WAN link wants a ten-second poll where one on a switched floor network wants two |
| **A7** | Admin screen refresh — a third, inconsistent clock | `web/admin.js:400` `setInterval(refresh, 8000)` | 8 s here, 2 s on the floor, 30 s for approvals, with no shared knob. The inconsistency is itself the evidence that nobody chose these together. | small | **`plant`** — the same answer as A6 from a third file; the plant should say it once |
| **A8** | The fixed list of time windows, and its default | `web/common.js:484`, `:493` | Same item as **P6**, seen from administration: a tier-one list (the plant's shifts) with a hard-coded literal in front of it. Listed in both domains because it is one change that two domains are waiting on. | medium | **`plant`** — the same item as P6, seen from administration |
| **A9** | `FS.allPages` ceiling | `web/common.js:297` `{ limit = 500, cap = 2000 }` | A plant with 3,000 characteristics hits it. It returns `complete: false`, so nothing lies — but where the ceiling sits is the plant's call. | small | **`plant`** — where the ceiling sits is the plant's own size |
| **A10** | Maximum steps in a recorded walkthrough, and four silent truncations | `services/walkthroughs.py:50` `MAX_STEPS = 60`; `:91` `title[:120]`, `body[:1000]`; `:96` `[:200]`; `:99` `[:60]` | A plant with a ninety-step changeover procedure is refused for a reason nothing outside it cares about, and the four truncations silently shorten the plant's own words. None of the five numbers carries a comment. | small | **`plant`** — a plant with a ninety-step changeover procedure is refused today for nobody else's reason |
| **A11** | Default capability a recorded walkthrough requires | `services/walkthroughs.py:105` and `:122` — `needs or "plant.read"` | A plant that wants every recorded walkthrough gated to at least `production.book` has to name it on every recording. Capability names stay tier three. | small | **`plant`** — a plant says once what every recorded walkthrough must be gated to |
| **A12** | Floor-agent round budget, session lifetime and result cap | `services/agent.py:45-47` `MAX_ROUNDS = 12`, `SESSION_TTL = 30 * 60`, `RESULT_LIMIT = 6000`; `:377` `max_tokens=4096` | `SESSION_TTL` is a session-lifetime policy exactly like `token_ttl_seconds`, which *is* an env var. The three comments describe rather than justify — there is no "why 12". `MODEL` and `MES_AGENT_MONTHLY_USD` two lines away are already env-read, so the pattern exists. | small | **`plant`** — `SESSION_TTL` is a session-lifetime policy like `token_ttl_seconds`, which is already the plant's |
| **A13** | "At most four sentences" — the assistant's house style, stated twice | `services/agent.py:109`, `services/assistant.py:825` | A plant that wants terser or fuller answers on the floor screen. Changes only what a person reads. Duplicated in two files that would have to stay in step. | medium | **`general`** — the assistant's voice is the product's, and two plants hearing different lengths is it speaking two ways — **§8** |
| **A14** | Suggestion and guide chip counts | `services/assistant.py:692` `out[:4]`; `web/assist.js:163` `guides.slice(0, 4)` | How many *things to try* the floor screen offers — and the server truncates to four *and* the client truncates to four, independently. Two hard-coded copies of one judgment. | small | **`general`** — the shape of the product's own screen, and the honest fix is one number rather than the two copies there are — **§8** |
| **A15** | Lexical guide-match threshold, the fallback when the model is down | `services/assistant.py:782` `score >= 2` | The comment defends the *approach* (*"Crude on purpose"*) and never the number. A plant whose guide titles use its own words gets different overlap counts. | small | **`general`** — nobody outside this repository can see what the score counts, so no plant can answer it — **§8** |
| **A16** | Context truncation before the model is asked | `services/assistant.py:836` `[:3000]`; `services/design.py:191` `budget: int = 2500`, `:203` `[:12000]`, `:212` `budget: int = 14000` | These decide how much of the plant's own facts reach the answer. A plant running a larger local model wants more. | small | **`plant`** — how much of the plant's own facts reach the model, on the plant's own hardware |
| **A17** | Local-model timeouts — six of them, all different, none shared | `services/assistant.py:730` `60.0`; `services/drafting.py:43` `180.0`; `services/design.py:160` `120.0`, `:183` `45.0`, `:204` `90.0`, `:321` `240.0` | A plant on a slower GPU needs longer everywhere, and there is no single place to say so. | medium | **`plant`** — one GPU per plant to say it about |
| **A18** | The work-instruction house style | `services/drafting.py:30-40`, especially `:35` *"Six to ten steps"* and `:33-34` the Purpose / Steps / If it fails structure | A plant whose QMS mandates Scope / Hazards / Steps / Records gets the wrong shape. **One clause of `HOUSE_STYLE` is explicitly *not* a candidate**: `:37-38`, *"An operator must never be told to adjust a reading toward the middle"*, is a product invariant and must not become configurable. | medium | **`plant`** — the plant's document standard — with the one clause at `:37-38` that stays a product invariant and is not configurable |
| **A19** | Rollup-staleness threshold on the AI panel | `services/ai_status.py:56` `ROLLUP_STALE = timedelta(hours=40)` | The comment names the single machine it was chosen for: *"main is LUKS-encrypted and regularly off overnight, so 'late' usually means 'the machine slept'."* | small | **`plant`** — chosen for one machine's sleep pattern; a plant server that never sleeps answers differently |
| **A20** | The standing GPU priority order | `services/ai_status.py:46-51` `BUDGET = [...]` | A fixed list openly described as a ratified decision with a date on it. A plant that runs neither triage nor rollups is shown a priority list describing somebody else's machine. Weaker: it is displayed, not enforced. | small | **`general`** — a list describing one development machine, displayed rather than enforced — **§8** |
| **A21** | Assistant log ring size and fill-retry budget | `web/assist.js:59` `entries.slice(-60)`; `:503` `attempt < 20`, `150` ms | How much of a conversation survives a page change, and how long a walkthrough waits for a control to appear on a slow plant PC. | small | **`plant`** — the plant's slowest PC. *Unsure: the 60-entry log ring in the same row is a product shape and would be general on its own* |
| **A22** | Toast durations and typing debounces, inconsistent across screens | `web/common.js:235` `3500`; `web/admin.js:48` `4000`; debounces at `admin.js:392`, `app.js:572`, `app.js:643`, all `250` | Accessibility: a plant that wants confirmations to linger. The 3500/4000 split is two files disagreeing about one judgment. | small | **`plant`** — an accessibility answer a plant gives for its own people. *Unsure: 3500 against 4000 is a product disagreement to settle first* |
| **A23** | Line-list cache lifetime on the floor screen | `web/app.js:172` `Date.now() - linesLoadedAt < 60000` | How stale the line picker may be. Nothing outside reads it. | small | **`general`** — a browser cache lifetime no plant has a way to set — **§8** |
| **A24** | Downtime-code length cap | `services/reasons.py:40` `{1,39}`, surfaced at `:229` as *"two to forty characters"* | Split deliberately: the **character class** is a protocol fact and the docstring at `:36-39` says why, so it does not qualify. The **40** does — nothing downstream cares whether a code is 40 or 64 characters. | small | **`general`** — forty characters or sixty-four changes nothing anybody can observe — **§8** |
| **A25** | Default shift day-mask when a pack omits one | `pack/masterdata.py:451` `row.get("days", "1111100")` | A plant running seven days is silently seeded a five-day calendar. Same item as **P9** from the pack's side; the honest fix here may be to refuse rather than default. | small | **`plant`** — the same answer as P9, from the pack's side |

*Scope: 7 general · 18 plant · 0 object — 25 in all.*

### IT — 4

0035 §2 deliberately leaves IT outside the role model, on the grounds that
where the database lives and which port a plant serves on are facts about the
machine, not the plant. Most of what the scan found under IT is exactly that
and is rejected below. Four are plant judgments wearing an IT coat.

| | What it is | Where | Why it qualified | Size | Scope — whose answer is it? |
|---|---|---|---|---|---|
| **I1** | Fleet health-probe timeout | `fleet/observe.py:32` `TIMEOUT = 3.0` | Comment: *"Long enough for a plant that is busy, short enough that a console polling a dozen of them does not hang on the one that is off."* Two fleets with different link quality answer differently, and getting it wrong reports a healthy plant as unreachable. | small | **`plant`** — one answer per fleet; getting it wrong reports a healthy plant as unreachable |
| **I2** | Rotating log size and backup count | `logging.py:95` `maxBytes=5_000_000, backupCount=5` | Twenty-five megabytes of history per component is a retention policy, one file away from `tag_retention_days`, which is a setting. Weak but real. | small | **`plant`** — a retention policy one file from `tag_retention_days`, which is already the plant's |
| **I3** | Retention prune cadence | `api/app.py:142` `await asyncio.sleep(3600)` | "Hourly" is stated as policy on the Ops screen (`services/retention.py:68`). The window it prunes *to* is already a setting; the cadence is not. Weak. | small | **`general`** — hourly is defensible in every plant; the window it prunes to is already the plant's — **§8** |
| **I4** | Which local model answers | `services/assistant.py:33`, `services/drafting.py:28`, `services/design.py:44` — all `qwen3:8b` | Which model drafts a plant's work instructions is the plant's call, and it is already recorded on the document as `drafted_by_model`, so the field's meaning does not change. `MES_AGENT_MODEL` exists for the cloud half; the local half has no equivalent. | small | **`plant`** — the plant's hardware and its choice, already recorded on the document as `drafted_by_model` |

*Scope: 1 general · 3 plant · 0 object — 4 in all.*

### Unassigned — 0

Every candidate a person kept was placed. The tool reports 17 raw matches as
`unassigned` — `api/paging.py`, `cli.py`, `plant.py`, `integrations/jev/` — and
of those only `api/paging.py` survived reading, listed above as **A3** because
paging defaults are the plant administrator's. The rest are development tooling
and the CLI's own timeouts. The bucket exists and is reported with its total
rather than folded into the biggest domain, which is the point of having it.

---

## 5. What the tool could not see

Stated plainly, because "we looked at everything" is only worth saying if the
gaps are named too. **A manual pass was done as well as the scan, by reading
the files in each domain**, and these are the things it caught that the scan
did not:

1. **A judgment with no number in it.** The four Western Electric rules
   (§2, **Q5**) are the standing example: *which* rules run is expressed as
   four `for` loops, and no scanner looking for literals is going to call a
   `for` loop a setting. Same for the policies "a failing check always opens a
   non-conformance" (`services/quality.py:113-122`) and "a further signal joins
   the open hold rather than opening a second" (`services/spc.py:390-409`) —
   both real judgments, neither a literal.
2. **A judgment expressed as a string.** The assistant's *"at most four
   sentences"* (**A13**) and the work-instruction house style (**A18**) are
   prose in a prompt. The scan looks for numbers and named collections; a
   sentence is neither.
3. **A judgment expressed as a prefix test.** `m.startswith("UT-")` (**Q9**) is
   one plant's material numbering, and it contains no number at all.
4. **Duplicates in the browser.** The scan reports `spc.py:246` and
   `spc.js:108` as two unrelated candidates, because they are two files. That
   they are the same decision written twice — which is what turns a small into
   a medium — is something only a person reading both notices. Same for
   `maintenance.py:92` / `maintenance.js:44`, and for the four-chip truncation
   at **A14**.
5. **Everything under `src/fsmes/sim/`,** which the tool skips by rule (the
   simulator invents a plant, and the scanner itself lives there, so it would
   otherwise match its own word list). The manual pass found the most
   self-aware hedges in the whole repository sitting in there —
   `sim/calibration.py:97-98` literally names two constants
   `ARGUABLE_MINIMUM_SAMPLES` and `ARGUABLE_ACCURACY`. They are development
   tooling, not plant configuration, so none of them is on the list; the
   pattern of writing the arguable thing down as a named constant is worth
   copying.

Conversely, the scan caught things a person skimming would not: the third
inconsistent refresh clock (**A7**), the `250` ms debounce repeated across four
files (**A22**), and the fact that `uns.py:30-32` and `erp.py:41-43` are the
same three constants with the same three values in two modules (**C8**, **S1**).

---

## 6. Found on the way, and not configuration

Three things turned up that are defects rather than settings. Named here so
they are not lost; none was touched.

- **`services/coa.py:277`** — a pallet certificate computes capability only for
  materials whose code starts with `UT-`. Any other plant gets a silently empty
  capability block and no error. (Also **Q9**.)
- **`services/maintenance.py:107-109`** — `_next_code` numbers from *the count
  of all maintenance orders*, so `PM-` and `CM-` share one sequence and a
  deleted row would make a code collide.
- **`integrations/erp/erpnext_adapter.py:133`** — `list()` takes `limit: int =
  100` and `fetch_orders` never passes one, so a plant releasing 150 orders
  between polls imports 100 and picks the rest up next cycle. Nothing is lost,
  but nothing says so either.

Two more were named by earlier work and are still open: the Ollama URL is
hard-coded in three product files while `services/ai_status.py:37` already
reads `MES_OLLAMA` and its own comment records folding the others onto it as
follow-up; and `services/analysis.py:59` defines `_MIN_WINDOW_SECONDS = 10.0`
that nothing reads — the live copy is `coverage.MIN_OBSERVED_SECONDS` (**P10**).

---

## 7. What this page does not do

**It does not turn seventy-five candidates into seventy-five questions.** A
`plant` item and an `object` item are *routed*, not asked: the first one
appears under its domain's `Configuration` tab with today's literal as its
default, and the second one appears on the row of the tag, machine, material
or gauge it describes, set by the engineer who is looking at that object.
Neither ever comes back to a maintainer as an open question, because a
maintainer cannot answer it honestly — the right answer depends on a plant, or
on a counter, that they have never seen. Sixty-six of the seventy-five are in
that position. What is left to answer is §8, and it is nine items long.

It does not pick, either. Seventy-five candidates across six domains, sized
roughly, each with the argument for why it qualified written next to it — so
the choice can be made on what the plant needs rather than on what is easiest
to build.

Four of them come with a caveat rather than a clean recommendation, and those
are worth reading before choosing: **Q5** needs a decision against 0035 §3
first; **C14** sits on house rule 1, and scope is the argument it needed — a
threshold nobody can set for a counter they have never seen is not a default
anybody should be asked to pick, it is a property of the tag, which is what
`object` scope says and why C14 is §9's first bulk-edit case; **A18** contains
one clause that must stay a product invariant; and **Q9** and **S7** may be
better fixed as bugs than shipped as settings.

Rerun `fsmes config-audit` before using this page. It was true on 2026-09-21,
and every `file:line` in it was checked again on 2026-09-22.

---

## 8. The nine that are anybody's to weigh in on

Everything else on the list is routed. These nine are `general` — one default
across every plant, because two honest plants have no reason, or no way, to
answer them differently. Each ships **today's literal, unchanged**; that is
the rule from §3 and it applies here too. What is being asked is whether the
number the product already picked is the right one, not what to set for
somebody's plant.

| | What it is | The default it ships | Why anybody is asked |
|---|---|---|---|
| **Q5** | Which Western Electric rules raise a hold | all four, always | 0035 §3 says a rule a plant can switch off is a chart that lies — and it was answering about the *chart*. Whether a plant may choose which rules raise a **hold** is a question it never asked. **Answered 2026-09-22 by [0036](../decisions/0036-the-chart-draws-every-rule-the-plant-chooses-which-hold.md): the chart draws and records every rule, and the plant chooses which of them hold. Built, defaulting to all four.** |
| **A2** | PBKDF2 iteration count | `240_000` | It is the product's entire password policy — no length rule, no complexity rule, no lockout beside it. A plant that turns it down is weaker without knowing it, so the floor is the product's to raise as hardware gets faster. |
| **A13** | The assistant's house style — "at most four sentences" | four sentences | The assistant's voice is the product's. It is written twice, in `agent.py` and `assistant.py`, which is the evidence that nobody owns it yet. |
| **A14** | How many suggestion and guide chips a screen offers | four — and four again, independently | Two hard-coded copies of one judgment, one on the server and one in the browser. The honest fix is one number, not a setting. |
| **A15** | Lexical guide-match threshold, used when the model is down | `score >= 2` | Nobody outside this repository can see what the score counts, so no plant can answer it. The product picks, and says why. |
| **A20** | The standing GPU priority order | the list at `ai_status.py:46-51` | It describes one development machine and is displayed rather than enforced. The honest answer may be to stop showing a plant somebody else's priorities at all. |
| **A23** | Line-list cache lifetime on the floor screen | 60 s | A browser cache lifetime for a list that changes when somebody edits masterdata. No plant has a way to know what to set it to. |
| **A24** | Downtime-code length cap | 40 characters | No payload, screen or export is keyed on the length. Forty or sixty-four changes nothing anybody can observe. The character class beside it stays a protocol fact. |
| **I3** | Retention prune cadence | hourly | The window it prunes *to* is already the plant's answer; how often the product sweeps to honour it is the product's business. |

*Scope: 9 general — 1 quality, 7 administration, 1 IT. No `general` candidate
was found in process, controls or supply chain: every judgment in those three
is either a plant's or an object's, which is what you would expect of the
three domains that touch the floor.*

**Q5** was the only one of the nine that blocked anything, and it no longer
does: decision
[0036](../decisions/0036-the-chart-draws-every-rule-the-plant-chooses-which-hold.md)
answered it on 2026-09-22 and the key is built. The other eight are one-line
changes whenever somebody decides the product's number should be a different
number.

---

## 9. Bulk editing, which `object` scope makes necessary

Design only. Nothing here is built, and this is two paragraphs rather than a
design page; the design page is its own step if it is picked.

**What it means in this product's terms.** Twelve candidates are `object`
scope, and an `object` setting is not one value — it is one value *per tag, per
gauge, per material*. A plant with four hundred counter tags cannot be asked to
open four hundred rows, and telling an engineer "set all production tags to a
small threshold" is the sentence they would actually say. In this product that
sentence is a **draft that touches N rows instead of one**: the assistant
proposes a single draft listing every tag-map entry it would change and the
before and after of each, validation runs **per tag** and a tag that fails
drops out of the draft with its reason shown rather than failing the whole
set, the engineer signs the draft once with the same `signals.approve`
capability the reason-codes pilot uses, and the whole thing is one revision,
so undoing it is one undo and not four hundred. It is the same
draft → validated → discovered → signed → live → undone loop 0035 already
describes, applied to a set of objects rather than to one vocabulary. The
seams it would use all exist: the `ConfigSection` for controls engineering,
the `KINDS` registry in `services/review.py` (a bulk edit is a new kind, whose
*what would change* reviewer renders N rows instead of one), the tag map file
itself, and `pack apply`'s never-overwrite rule, which already says a pack may
not silently replace a value a plant set by hand — a bulk edit must obey the
same rule in reverse, and say which tags it is leaving alone.

**What it must refuse.** A bulk edit may set a value on many objects; it may
not change what a value *means* on any of them. Setting a counter-reset
threshold on every production counter is one judgment applied many times, and
an engineer can check it by reading one row and trusting the rest. Rewriting
what a tag's `state_map` maps — which word a PLC code means — is a different
judgment per tag, and applying one answer to four hundred of them would be
inventing production on three hundred and ninety-nine machines nobody looked
at. The line is: **a bulk edit is allowed where the same sentence is true of
every object it touches, and refused where the engineer would have to look at
each object to know.** `C14` is on the right side of that line, which is why
it is the first case: *"a counter that resets once a shift"* is a property an
engineer knows about a whole class of counters at once.
