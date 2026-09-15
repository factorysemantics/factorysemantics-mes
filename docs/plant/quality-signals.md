# When a control chart notices something

*Explanation — for the quality engineer who wants to know what the MES will
do on its own, and what it will not.*

## The two things that happen

A control chart in this MES is not a picture you go and look at. When a
reading is recorded, the four Western Electric rules are run over that
characteristic's recent readings there and then. A rule that fires is written
down as a **signal**, and the first signal of an excursion raises a
**quality hold** — a non-conformance, in the ordinary lifecycle
(*open → under review → dispositioned → closed*) your supervisors already
work.

That is all it does. It does not stop a line, quarantine a lot, scrap stock
or write anything to a machine. Rule 4 — eight readings in a row on one side
of the centre line — is an early warning, not a fault, and a plant whose line
stops for one learns very quickly to switch the feature off.

## What the hold tells you

A record a machine opened has to answer *why do you think so* without anybody
having to go and re-derive it. So the hold carries:

- the **rule** that fired and what it means in words;
- the **material and characteristic**;
- the **readings** of the rule's window — one point for rule 1, three for
  rule 2, five for rule 3, eight for rule 4;
- the **chart as it stood**: the centre line, sigma, the ±3σ limits and how
  many readings they were computed from. Not recomputed later — recomputing
  gives different numbers, and then nobody can see what the MES acted on;
- the **station**, if a station took the reading. If a person took it with a
  gauge, this is blank. Blank means *not recorded*; it does not mean *no
  machine*, and the MES will not work one out from the routing.

The hold is raised **open**, not *under review*. Under review means a person
picked it up, and nobody has.

## How often it raises one

Once per excursion. An excursion that runs for twenty readings is one thing
that went wrong, and a process going out of control usually trips two or
three of the four rules on its way out. Every firing is recorded as its own
signal; they all point at the one hold. When somebody dispositions that hold,
the next signal is a new finding and raises a new one.

A rule only fires on a **new** reading. One wild measurement moves the centre
line, and every settled reading behind it is suddenly on one side of it — the
MES does not go back and raise two dozen holds about a fortnight nobody was
worried about until a moment ago.

Fewer than twelve readings raises nothing, and the chart says why. Control
limits computed from six points move with every reading.

## Making something else happen

What a plant does next is the plant's decision, so it is configuration, not
code. The trigger catalogue offers a tag called **`spc.signal`**: no PLC
publishes it, the MES raises it on the station whose reading tripped the
rule, and its value is the rule number. Draft a trigger on it the way you
would on any tag:

| Field | Value |
|---|---|
| Tag | `spc.signal` |
| Machine | the moulder, or *any machine* |
| Condition | `equals`, threshold `1` — rule 1 only |
| Action | `set_machine_down`, or `create_maintenance_order`, or `log_event` |

A trigger is a draft until somebody approves it, and a signal on a reading
with no station recorded reaches no trigger at all.

## Which readings reach the chart

A station that inspects every unit publishes its measurements as `Insp_*`
tags. A measured characteristic becomes a **quality check** — and therefore
appears on the chart and can trip a rule — when your plant has written a
**specification** for that material and that characteristic name. One with no
specification stays an inspection on the unit, and the ingest counts it and
names it as *uncharted* rather than dropping it quietly.

So the plant chooses what is charted, by writing specifications. That is
deliberate: without the choice there would be a row in `quality_checks` for
every attribute of every unit at line rate, and a control chart nobody asked
for is not a kindness.

A station reading that is out of spec opens **no hold of its own**. The
station has already judged the unit and the unit is already good or scrapped;
one record per bad piece on a machine inspecting ten a second is a list
nobody reads. What raises the hold is the signal.

A reading a **person** records still opens a hold when it is out of spec, as
it always has. A person measuring one bottle has found one bad bottle, and
that is a finding.

---

The reasoning, the options that were rejected, and what has still to be
decided are in decision record
[0026](../decisions/0026-an-spc-signal-raises-a-hold.md).
