# ACME bottling — master data, as data

The six-station reference line, as the files `fsmes pack apply` reads and
`fsmes pack check` validates offline. One file per kind, each a flat list of
entries; a file this product does not read is a problem, because master data
nobody reads is master data somebody thinks is loaded.

## Why this exists now, when it deliberately did not before

This line ships *inside the product*: `fsmes seed-kepsim` builds it, because
it is the twin's own reference line and the demo plant's. The argument for
leaving it out of the pack was that copying it here would be one copy that
could drift from the other.

What that argument cost, measured on 2026-09-14: `fsmes fleet create` and
`fsmes plant bottling init` both left this plant with **zero equipment** and
nothing anywhere said so; `fsmes score bottling` and `fsmes sweep bottling`
died on their first read — `GET /analysis/timeline` → 404, *"no line has any
machines on it yet — seed a plant first"* — because the ephemeral plant a
scored run builds applies the pack and nothing else; and the one step that
did seed it, `init.py`, was a script the registry had stopped naming on
2026-09-13 and which a person therefore had to know to run.

A plant that needs a step nobody can see is house rule 4's own example of a
bug. So the line is here, and `init.py` is gone: `fleet create`, `plant
init`, `score` and `sweep` now build the same plant the same way.

## Where it came from, and how to tell it has not drifted

Generated once by running `fsmes.seed_kepsim.seed_kepsim_line` into a scratch
database and reading the rows back, so these files started life as exactly
what the code they replace produces.
`tests/test_bottling_pack.py::test_the_bottling_pack_builds_the_line_the_products_own_seeder_builds`
keeps them that way: it seeds one database each way and compares them, and
fails if either side changes alone.

## The two scales on the filler

`gauges.json` puts this plant's instruments on its register: `SCALE-FILL-01`
and `SCALE-FILL-02`, both at the filler, both reading to a tenth of a gram
against a fill-weight tolerance of twelve. Two rather than one, because a
plant with a single gauge has no way to ask whether the instrument or the
process moved — which is the first question anybody asks about a point on a
control chart.

One was calibrated a week ago and one eighty-four days ago against a ninety-day
interval, so the second is inside its fortnight's warning and due this week.
That is deliberate: it is the state that makes the calibration screen worth
opening, and it is the gauge the floor script has drifting.

`calibrated_days_ago` is relative for the same reason `due_in_hours` is. No
calibration event is invented behind the date — the register records what the
plant says it knows, which is what a gauge register migrated into a new MES
looks like.

Which gauge takes which reading, and which of them is drifting, is **not**
here: it is in this pack's [`floor.json`](../floor.json), because it is a fact
about the simulated people rather than master data the MES owns.

## The maintenance crew, and who gets the work

Added 2026-10-09, with the dispatcher. A plant that knows its machines and not
its electricians cannot send a filler's electrical fault to an electrician,
and that is the whole job: handed to a mechanic it is a shift lost and a
supervisor who stops trusting the list.

**Seven people** in `personnel.json`, `MT-01` to `MT-07`. None of them has a
password, and that is deliberate rather than unfinished: `Person.password_hash`
null means *cannot sign in*, and a tradesperson who never opens the MES should
be on its books without an account somebody has to manage. The accounts this
plant does sign in with are `[[accounts]]` in `plant.toml`, which read their
passwords from the environment. `role` is `operator` for all seven because
this product's role list has no maintenance role yet; it grants them nothing,
because an account with no password cannot use it.

Five of the seven are based at a machine (`home_equipment`), which is what the
`nearest` strategy walks — same work centre first, then the same line. It
grants nothing: it is where somebody usually is, not what they are allowed to
touch.

**Three trades** in `skills.json`: `ELEC`, `MECH`, `GEN`. `MECH` covers
pipefitting and welding on this plant, said out loud in its own description; a
plant that keeps those apart adds the rows and changes nothing else.

**Nine entries** in `personnel_skills.json`, because people hold more than
one. Levels are 1 trainee, 2 competent, 3 expert, and the dispatcher will not
send a trainee on their own — `MT-05` holds `ELEC` at 1 and `MT-06` holds
`MECH` at 1, so both appear in `--explain` as considered and skipped, which is
the output a supervisor checks the rules against.

**The roster** is seven standing rows: four on `DAY` (`MT-01`, `MT-03`,
`MT-05`, `MT-06`) and three on `NIGHT` (`MT-02`, `MT-04`, `MT-07`). No `day`
on any of them, and that is the point — a row with no day is a *standing*
assignment, on that shift whenever it runs, and a pack is applied whenever
somebody builds the plant, so a dated row in one is about a day in the past
the week after it was written. A dated row still works and overrides the
standing one, which is how an absence or a training day is written down
without rewriting the roster.

Nights hold one electrician, one mechanic and one general hand, so a night
with two electrical jobs at once produces a real `all_busy`, and a night with
a job needing a trade nobody on shift holds produces a real
`nobody_on_shift_with_skill`. Both are states worth being able to see.

**Three rules** in `dispatch_rules.json`, tried in `sequence` order, first one
that finds a free person wins. Each row is one sentence the shift supervisor
would say out loud:

| seq | code | what it says |
| --- | --- | --- |
| 10 | `SAFETY-NOW` | Safety work (priority 1) anywhere on the plant goes to whoever has least on. |
| 20 | `FILL-ELEC` | Filler electrical work goes to the nearest electrician. |
| 90 | `LINE-REST` | Everything else on `SIMLINE` is spread by turns. |

A rule with no `skill` still only reaches people who hold *the order's* trade
— the rule's `skill` narrows which work it catches, not which people it may
choose from. That is why `SAFETY-NOW` can be written without a trade on it and
still never send a mechanic to an electrical fault.

**The six plans gained `skill` and `priority`.** The chiller condenser is
`ELEC` (the fan and the pressures), seals, belt and bearing are `MECH`, the
grease and the descale are `GEN`; the three filler and denester jobs are
priority 2 (production-critical) and the rest 3 (routine). Nothing here is
priority 1: this plant has no safety plan, and inventing one to exercise a
rule would be inventing production. `SAFETY-NOW` is still the right first
rule to have written, and corrective work raised at priority 1 is what will
hit it.

**Nobody starts the work.** The simulated floor raises what is due and now
dispatches it; it does not start or complete it. The chain an auditor walks
back ends at an order somebody has been given, which is further than it
reached before and still short of a finished job.

## What is not here

- **Rated cycle times.** An equipment entry leaves `ideal_cycle_seconds` out
  and it is read from this pack's own `tag_map.json`, exactly as machining's
  are. OEE performance is ideal cycle × count ÷ runtime, so a rate repeated
  here that drifted from the line that generated the data would produce a
  performance figure that means nothing.
- **Anything generated.** Line data lives in `../../kepsim/out`, comes from
  `fsmes sim-generate`, and is never committed.

`RAW-CARTON` is 24 bottles to a case, so its BOM quantity is one twenty
fourth and is written to the last digit rather than rounded: a quantity that
is nearly right is a shortage report that is nearly right.

## The order book, and the arithmetic behind it

`work_orders.json` is a schedule, not a single order. Until 2026-09-18 it was
one released order — `WO-ACME-4711`, for 4,000 — and a plant left up overnight
reported that order at **285,881 good, over_qty 281,881, still running**. The
MES was right (decision 0029: an order does not finish itself at its
quantity), and the plant was wrong: a bottling line with one order for
thirty-nine minutes of work is not a plant anybody would recognise.

The sizing, so it can be argued with:

- The slowest station in `tag_map.json` sets the line: the palletiser at
  **0.588 s** a bottle, which is **6,122 an hour** at the rating, or about
  49,000 in an eight-hour shift. A real hour is less — scrap, micro-stops and
  a changeover — so a book sized against the rating lasts longer than the
  clock says, never less.
- **Ten orders, 151,600 bottles**, which is **24.8 hours** of that rating.
  Three to four orders a shift, each 2–3 hours, in multiples of 1,200 (fifty
  shipper cartons of 24).
- `WO-ACME-4711` keeps its code, its 4,000 and its place at the head of the
  book, because `labs/experiments/over-run.toml` is written around it.
- **One product.** `FG-BOTTLE` is the only finished material this plant's
  routing makes, so every order is for it. A second product here would be a
  second routing and a second set of line data, which is a different pack.

One order is released; the other nine are planned, with due dates two to
twenty-six hours out. The simulated shift supervisor finishes an order once
the line has made its quantity and releases the next — see
`fsmes.sim.operations`. When the book is empty it says so, once, and invents
nothing.
