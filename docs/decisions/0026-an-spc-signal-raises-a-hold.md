# 0026 — An SPC signal raises a quality hold, and an inspected characteristic with a specification is a check

- **Status:** accepted
- **Date:** 2026-09-14
- **Deciders:** scottkalw

## Context

`GET /quality/spc/{material}/{characteristic}` has run four Western Electric
rules over the stored checks since the chart was written, and returned them
as `signals`. Nothing read them. No hold, no non-conformance, no trigger, no
audit row — the rules fired for whoever happened to have the screen open, and
at two in the morning that is nobody.

Underneath it, the readings mostly were not there to fire on. The simulator's
inspection path (`InspSeq` / `InspPass` / `Insp_*` → `unit_inspections`) wrote
a `UnitInspection` per unit and never a `QualityCheck`. `QualityCheck` is the
only table the chart reads, so a station measuring four characteristics ten
times a second was invisible to the only code that could have judged them. A
scrap burst on a moulding line produced a wall of scrapped units and a
perfectly flat control chart.

So the two halves of the gap: the readings did not reach the chart, and the
chart's verdict reached nothing.

This came out of the lab's third round (2026-09-14, finding F15). The lab
wants to measure *how long after the first bad part does a hold exist* — and
could not, because the only thing that could link a scrap burst to a hold was
the lab itself, which would have been the lab timing its own actor.

## Options considered

| Option | For | Against |
|---|---|---|
| Leave the rules on the GET; let a plant write a trigger on the tag if it wants action | No new state, no new table | A control chart nobody has open computes nothing. And there is no tag: the rule is a statement about a series, not about a reading, so there is nothing for a tag-watching trigger to watch |
| Evaluate on the write, raise a non-conformance, once per rule per window of readings; and turn an inspected characteristic that has a specification into a check | The rule fires when the plant is still running the thing that tripped it; the hold carries the rule and the points, so a supervisor is not asked to take the machine's word for it; the plant still decides what *else* happens | A new table, an evaluation on a hot write path, and a judgement call about how often "the same excursion" may raise a record |
| Evaluate on the write and act directly — hold the lot, stop the line | Fastest containment | The MES would be stopping lines on a statistical inference. Rule 4 (eight points one side of centre) is an early warning, not a fault, and a plant that has its line stopped by one learns to switch the feature off |
| Make every inspected attribute a check | Nothing to configure; the chart sees everything | A row in `quality_checks` for every attribute of every unit at line rate. The cutlery lab alone is 160,000 rows a shift for charts nobody asked for |

## Decision

**An SPC signal is an event the MES acts on.** The four Western Electric rules
are evaluated when a check is recorded — on the write path, in
`fsmes.services.spc.evaluate` — as well as on the chart endpoint, which is
unchanged. A rule that fires is recorded as an `SpcSignal` row carrying the
rule, what it means, the reading it fired on, and the chart as it stood:
centre, sigma, the ±3σ limits, how many readings they came from, and the
points of its window. Recomputing those numbers a day later gives different
ones, and then nobody can see what the MES actually acted on.

A signal **raises a non-conformance**, with that evidence on it
(`non_conformances.evidence`), against the material and characteristic, the
work order the reading was taken against when it named one, and the station
that measured when one was recorded. It raises nothing else: it does not hold
a lot, stop a line, scrap stock or write to a machine. What a plant does next
is a person's act, or a **trigger the plant configures** — the trigger
catalogue gains `spc.signal`, a tag no PLC publishes, raised by the MES on the
station whose reading tripped the rule, with the rule number as its value. A
plant that wants rule 1 to stop a moulder writes that trigger; it is not
written into this code.

**The hold is raised `open`, not `under review.`** Decision 0024 says who took
each step is recorded and a step nobody took is null. Nobody has picked this
up — a machine raised it. `under_review` with no reviewer would be the MES
claiming a person looked.

**Once per signal.** A signal's identity is its rule plus the *window* of
readings it judged — the first and last check id of rule 1's single point,
rule 2's three, rule 3's five, rule 4's eight — held unique in `spc_signals`.
Re-running the rules over the same stored readings finds the row and raises
nothing, which is what makes evaluating on every write safe. And while the
non-conformance a rule raised is still unresolved, a further firing of that
rule on that characteristic joins it rather than opening another: an excursion
that lasts twenty readings is one thing that went wrong, not twenty. Once
somebody has dispositioned it, the next firing is a new finding.

**An inspected characteristic that has a specification is a check.** A station
event's `Insp_<Name>` reading becomes a `QualityCheck` when the plant has
written a `QualitySpec` for that material and that characteristic name, judged
in or out of spec by that specification. One with no specification stays a
`UnitInspection` and is named in the ingest's `uncharted` list and its audit
row — reported as uncharted, never silently dropped. Which characteristics are
charted is therefore the plant's configuration, not this code's guess, and
that is also what keeps the row count to what somebody asked for.

Such a reading opens **no per-reading non-conformance.** The station has
already judged the unit and the unit is already good or scrapped; a record per
bad piece on a machine inspecting ten a second is noise a supervisor learns to
scroll past. What raises the hold is the signal. A reading a *person* records
still opens one when it is out of spec, as it always has: a person measuring
one bottle has found one bad bottle, and that is a finding.

`quality_checks` gains `equipment_id`: the station that took the reading, for
readings a station took. A person with a gauge records none, and null there
means *not recorded*, not "no machine" — deriving one from the order's route
would name a machine nobody stood at.

Migration `a9c4e17b3d60` adds the table and the three columns and changes no
data.

## Consequences

Easier: a scrap burst now reaches the chart and the chart now reaches a
supervisor, in the seconds it takes the readings to arrive rather than the
next time somebody opens a screen. The lab can measure the distance between
the first bad part and the hold, because the product closes that loop and the
lab only watches it. The hold carries its own evidence, so the first question
a supervisor asks — *why do you think so* — is answered on the record.

Harder: `quality.record_check` now returns three things, not two — the check,
the non-conformance and the signals. That is a breaking change to a service
signature in a `0.x` release and is in the changelog under **Honesty**. The
rules run on a write path; they read at most the last 200 readings of the one
characteristic that changed, and a batch of station readings evaluates once
per characteristic, not once per reading.

To revisit: whether the window that raises a hold should be the plant's
setting (some plants act on rule 1 only), and whether a signal should be able
to name a lot. It cannot today: a quality check records its work order, not a
lot, and guessing the lot from the order would be inventing the traceability
this MES exists to keep honest.

## House rules touched

**Never invent production** (1): a hold raised by a rule states which rule,
which readings, and the limits it judged them against; a station the MES did
not record is `null` and reaches no trigger, rather than being derived.
**Unknown is a valid answer** (2): fewer than twelve readings raises nothing
and the chart says why, as it already did. **Unlabelled data is reported as
unlabelled** (3): a measured characteristic with no specification is counted
and named in `uncharted`, never quietly dropped. **Config, not code** (4):
which characteristics are charted is the plant's specifications, and what a
signal sets off beyond the hold is the plant's trigger. **Tests are prose**
(5): `tests/test_spc_signals.py` names each rule after the behaviour it pins.
