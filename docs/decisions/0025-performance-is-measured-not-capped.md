# 0025 — OEE performance is measured against the rating, and never capped at 1.0

- **Status:** accepted, amended by [0026](0026-counts-that-outrun-the-run-time.md)
- **Date:** 2026-09-14
- **Deciders:** maintainer

## Context

Both places that computed OEE performance wrote `min(1.0, ...)`:
`services/equipment.py` for the per-machine KPI and `services/analysis.py`
for the plant-wide breakdown. The comment beside the cap explained the
drawing problem it solved — a negative loss would put a bar below the axis
and imply the line invented units.

On 2026-09-14 the lab measured what the cap costs. Two experiments through
`fsmes lab run` (bottling at 20x, bottling plus machining at 30x). On **all
nine stations across both plants** the MES reported `performance = 1.0`,
while the script that generated the data said 0.9433 to 0.9994. Availability
and quality tracked the truth to within the replay's overlap band;
performance did not track at all, because it could not: every raw value was
above 1.0, so every reported value was exactly 1.0.

Two separate facts were hidden underneath.

1. **Performance is not a share.** Availability and quality are each a ratio
   of two quantities the MES measured itself, so neither can exceed 1.0 by
   arithmetic. Performance is different: its numerator is priced from master
   data a person typed — the rated cycle — and only its denominator is
   measured. It can exceed 1.0 whenever the rating is wrong, and that is
   information about the rating.
2. **In the lab it exceeded 1.0 for a second reason.** The rated cycle is in
   the line's own seconds and the observed run time is wall clock, so a
   replay at 20x reports twenty times the line's performance. That is a
   property of replaying, not of the MES, and it belongs to the lab's
   comparison rather than to the product.

House rule 2 says a KPI that cannot be computed honestly returns *unknown*
and says why. A KPI that reads 1.0 whatever the line does fails the same rule
from the other side: it is not unknown, it is wrong, and an OEE built on it
overstates the plant.

## Options considered

| Option | For | Against |
|---|---|---|
| Keep the cap (status quo) | Charts stay inside their axes; no screen has to handle a bar over 100 % | A factor that is 1.0 whatever the line does is not a measurement; it silently inflates every OEE and hides the master-data error that produced it |
| Cap, but flag that the cap was applied | Charts stay simple; the flag carries the fact | A flagged wrong number is still a wrong number in the API, the export and the ERP confirmation; the flag is read by whoever already knew to look |
| Report *unknown* above 1.0 | Honest that the figure cannot be trusted | Throws away a real measurement and the finding inside it: the plant learns nothing about which rating is wrong |
| **Report it, above 1.0 and all, with the reason (chosen)** | The number is the measurement; the reason names the master-data fault; nothing to un-learn later | Screens must handle a value over 1.0, and an OEE over 1.0 follows |

## Decision

Performance is ideal time for the units the machine counted ÷ the time the
MES saw it running, and it is **never capped**. One function,
`fsmes.services.oee.performance`, computes it for every screen and every API,
so the two call sites cannot drift apart again. It returns the value and a
note:

- **Below 1.0** — the machine ran slower than its rating. No note.
- **Above 1.0** — the machine ran faster than its rating. Reported as it is,
  with the note that says what it means: *the rating is slower than the
  machine*, a master-data finding, not a score above physics.

  !!! warning "Amended the same day by [0026](0026-counts-that-outrun-the-run-time.md)"

      That second sentence was wrong, and the lab caught it within hours.
      `performance > 1.0` is the same inequality as *the counted work will not
      fit inside the run time*, and the master data is only one of its
      candidate causes. The figure is still uncapped; the note now states the
      disagreement instead of naming a culprit.

      **Amended again on 2026-09-18**, after a plant replaying a recorded line
      printed 980 % OEE and the maintainer read it, correctly, as an error:
      above 1.0 the ratio is no longer *reported as performance* at all. It is
      still computed, still published as `performance_ratio`, and still never
      capped — what it loses is the name, because it measures two of the MES's
      records disagreeing rather than the machine. Nothing measured is
      discarded, which was this record's objection to reporting *unknown*.
- **`None`** — there is no honest number, and the note says which of the
  three reasons applies: no rated cycle time in master data, the machine was
  not seen running, or nothing was counted. Never 1.0 and never 0.

The rated cycle comes from master data — `equipment.ideal_cycle_seconds`,
which a plant pack reads from its own tag map — and nowhere else. The
performance loss in units is signed for the same reason: a negative loss is
the same finding priced in units, and flooring it at zero hid it one column
along. The waterfall does not draw a negative segment; it fills the bar,
prints the true figure, and puts the finding under the row in words.

OEE, being the product of the three, can exceed 1.0 too. That is the same
finding carried through rather than trimmed at the last step.

## Consequences

**Easier.** A wrong rated cycle becomes visible on the screen that uses it,
instead of being absorbed. The lab's performance comparison becomes a
measurement: both sides are now uncapped, the MES's figure is restated on the
line's clock before it is compared, and each station carries the band the two
sides' run times differ by.

**Harder.** Anything consuming `performance` or `oee` must accept a value
above 1.0. Inside this repository that is the four screens that draw them,
and they are handled. A plant trending OEE will see a step where a station's
rating is wrong; that step is the finding, and the note beside it says so.

**To revisit.** If a plant reports that an above-1.0 figure reaches a
customer report where it cannot be explained, the answer is a *report* that
states unknown for that station, not a cap put back in the kernel.

## House rules touched

- **2 — unknown is a valid answer; zero is not.** Extended to its other end:
  nor is one. A missing rating is unknown with its reason.
- **6 — charts get checked by looking at them.** The bar cannot draw more
  than itself, so the case that used to be impossible is now drawn
  deliberately: full bar, true number, finding in words beneath it.
