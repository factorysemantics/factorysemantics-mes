# 0033 — Availability is a share of what was watched, and every figure says how much that was

- **Status:** accepted
- **Date:** 2026-09-17
- **Deciders:** maintainer

## Context

Decision 0030 took the seconds a machine was recorded as disconnected out of
availability's denominator and put the share beside the figure. That was the
right move and it did not go far enough, because it left two things unsaid.

**The denominator was still not an account.** `window − disconnected` answers
"how long was the window, less the outages we happened to record". It does not
answer "how much of this window did anybody watch, and where did the rest of
it go". Those are different questions, and only the second one can be checked:
an account that has to add up to the window will show you the seconds it
cannot explain, and a subtraction will not.

**And there was more than one way to be blind.** A disconnection is the one
the agent writes down. Decision 0030 itself listed the one it does not: an
agent killed outright writes nothing on its way down, so the state history
simply stops. Those seconds were inside `window − disconnected`, which means
they were in availability's denominator, priced as time the machine was not
running. The MES was reporting a machine as less available because its own
agent had been killed.

The wider problem is the one the field has. Nobody trusts OEE. Every vendor's
answer is "capture it from the PLC", and automatic capture has holes of its
own. The result is a number whose denominator includes time nobody watched,
and no product prints the size of that time beside the number. This project
has a house rule about exactly this — *unknown is not zero; every list states
its total* — and had not applied it to its most visible figure.

## Options considered

| Option | For | Against |
|---|---|---|
| Keep subtracting, add more things to subtract | Small change; no new concept | Each new blindness is another term in a subtraction nobody can audit, and two of them overlapping double-counts silently. There is no way to ask "do the seconds add up" of a subtraction |
| Put a `coverage` field on the OEE response and compute it beside availability | One number, cheap | It is the same subtraction with a new name. A coverage figure that is not *derived from* the same account as availability can disagree with it, and the first time it does, both are worthless |
| Add a `not_observed` member to `EquipmentStateName` | Every reader picks it up for free | Decision 0030 already refused this, and for the same reason: it is not a production state, and putting it in that column feeds it to the downtime pareto, the ERP confirmation and every consumer that switches on the enum |
| **A ledger that tiles the window exactly (chosen)** | Every second is in exactly one interval with one disposition, so the seconds can be *checked* against the window and the check can fail loudly; availability and coverage come out of the same object and cannot disagree; the unwatched time is named rather than subtracted | A new concept, a new module, and two code paths (listed and summed) that have to be held equal by a test |

## Decision

**Availability is derived from a coverage ledger, and every figure carries the
ledger's coverage beside it.**

1. **The ledger tiles the window exactly.** Per machine, per window, disjoint
   intervals that meet end to end, the first starting where the window starts
   and the last ending where it ends. Four dispositions: `observed_running`,
   `observed_stopped_labelled`, `observed_stopped_unlabelled`, `not_observed`.
   **An OEE window whose seconds do not add up is a bug, not a rounding
   difference** — `Ledger.tiles_exactly()` says so, tests pin it, and
   `fsmes oee explain` exits non-zero rather than print a table that does not
   balance.

2. **Availability is run time over observed time; coverage is observed time
   over the window.** Both from the same object, so they cannot disagree.
   Performance and quality keep their own arithmetic and carry the same
   window's coverage honestly beside them; deriving their inputs from the
   ledger is a separate change and is not pretended here.

3. **Coverage is stated against the window that was asked for**, never the one
   clamped to what this MES turned out to hold. A line commissioned twenty
   minutes ago would otherwise report that we saw all of its window, which is
   true of the clamped window and useless as an answer. The clamped window is
   still what run time and unit counts are read over, and is still reported,
   as `window_hours`.

4. **Every unwatched second carries a cause**, and there are four, because
   four is how many this MES holds evidence for: `before_first_sample`,
   `disconnected`, `after_last_sample`, `no_state_recorded`. A disconnection
   interval holds a sentence somebody's code wrote and the endpoint it was
   dialling; sorting that free text into "the agent was down" versus "the
   plant was unreachable" would be this MES claiming to know which of several
   things happened when all it holds is that the link was gone. So
   `disconnected` is one cause and the recorded sentence is carried beside it
   unaltered. When a plant's server publishes a heartbeat tag, per-machine
   staleness becomes a positive signal and this list gains a fifth member with
   evidence behind it (decision 0030's "to revisit"). Not before.

5. **How precisely time was watched is stated, not solved.** Each observed
   interval carries the sampling cadence it was built at — this plant's
   configured OPC publish interval, with a sentence saying that is what the
   number is. Decision 0026 measured what a coarse grid costs. Coverage asks
   *whether* time was observed; the cadence is about *how precisely*. A later
   change-driven capture makes the observed intervals more precise without
   changing the ledger's shape.

6. **Coverage is always shown; refusing to draw is opt-in, per pack.** There
   is no default floor. A plant that writes `[oee] coverage_floor` — a share
   in (0, 1], validated by `fsmes pack check` — is asking to be told *unknown*
   rather than shown a figure measured over eleven minutes of a shift. Below
   the floor, the API, `/metrics` and the screens report the KPI as unknown,
   and the screens print the ledger instead of drawing a smaller bar: a
   shorter bar reads as a worse machine, and what happened is that nobody
   watched it. **What is withheld is the figure, never the evidence** — run
   time, counts and the whole ledger stay on the object.

7. **`/metrics` exports the pair.** `mes_equipment_availability` and
   `mes_equipment_coverage` ship together, so a Grafana panel cannot show one
   without being able to show the other, plus
   `mes_equipment_not_observed_seconds` by cause. An availability this MES
   cannot state is **absent**, not zero — which is what Prometheus already
   means by unknown.

Two code paths, held equal by a test. `coverage.ledger()` lists the intervals
for one machine — what `fsmes oee explain` and the machine page show.
`coverage.totals_many()` sums the same account for a whole plant in grouped
queries that never bring an interval out of the database, because eight hours
of a 108-station plant is 400,000 state intervals and the OEE path is a screen
refresh. They share one cause precedence and a test holds them to the same
numbers on the same data.

## Consequences

**Easier.** A plant engineer who disagrees with an availability figure can
print the interval they disagree about and the rule that produced it. A shadow
run can say how much of the fortnight it actually watched. The lab can score
"the MES was blind for four minutes" as a number rather than an impression.

**Harder, and lower.** Availability changes meaning: seconds in a hole in the
state history that no disconnection was recorded for used to sit in the
denominator priced as downtime and now leave it. Figures go **up** where that
hole was large and the machine was running, and windows that used to report a
number now report unknown on a plant that sets a floor. A shadow deployment
beside an incumbent will often show lower availability than the incumbent —
because it is subtracting the minutes it could not see and the incumbent is
not. That is the point of the feature and the reason it was built.

**To revisit.** Performance and quality are not yet derived from the ledger;
they carry its coverage and keep their own inputs. And the cadence on an
observed interval is the plant's configured one, not a per-interval
measurement — that is the field a change-driven capture fills in.

## House rules touched

- **1 — never invent production.** A second nobody watched is `not_observed`
  with a cause, never a state and never a share of one.
- **2 — unknown is a valid answer; zero is not.** Below a plant's floor a KPI
  is unknown with its ledger, and in `/metrics` it is absent rather than zero.
- **3 — unlabelled data is reported as unlabelled.** A watched stop with no
  reason on it is its own disposition, and every list states its total —
  including the window itself, which is the whole of this decision.
- **4 — config, not code, at plant boundaries.** The floor is pack data. The
  product ships none.
