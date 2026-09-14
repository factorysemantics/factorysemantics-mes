"""OEE performance, in one place, for every screen and every API.

Performance is a **measurement against a rating a person wrote down**, not a
score out of one. Until 2026-09-14 both places that computed it wrote
`min(1.0, ...)`, so a station that out-ran its rated cycle reported exactly
1.0. The lab found what that costs: across two experiments, nine stations in
two plants, the MES said performance was 1.0 on every one of them while the
script that generated the data said 0.943 to 0.9994. A factor that reads 1.0
whatever the line does is not a measurement, and an OEE built on it overstates
the plant.

So there is no cap here. Three answers, and each one says what it is:

* **A number below 1.0** — the machine ran slower than its rating.
* **A number above 1.0** — the machine ran faster than its rating. That is a
  finding about the master data, not a plant running at 112 % of physics, and
  a plant wants to see it: somebody typed a cycle time that is slower than the
  machine, or the counter is counting something other than what the rating
  rates. It is reported with the note that says so.
* **`None`** — there is no honest number, and `note` says why (house rule 2:
  unknown is a valid answer, zero is not).

Availability and quality are shares of things the MES watched, so they cannot
exceed 1.0 by arithmetic. Performance can, because half of it comes from
master data. OEE, being their product, can exceed 1.0 too — the same finding,
carried through rather than hidden.
"""

from __future__ import annotations

#: No rated cycle time in master data. The commissioning worksheet leaves the
#: cell blank when nobody knows it, and a guessed one is worse than none.
NO_RATING = "no rated cycle time for this machine, so there is nothing to measure against"

#: The machine has not been seen running in this window, so there is no run
#: time to divide by. Not the same fact as running badly.
NOT_RUNNING = "the MES did not see this machine running in this window"

#: Nothing was counted. Zero units in zero seconds is not zero performance.
NOTHING_COUNTED = "nothing was counted on this machine in this window"

#: Said when the measured rate beats the rating. Formatted with the rating.
ABOVE_RATING = (
    "ran faster than its rated cycle of {cycle} s per unit — the rating is "
    "slower than the machine, which is a master-data finding rather than a "
    "score above 100 %"
)


def performance(
    cycle_seconds: float | None,
    units: float,
    runtime_seconds: float,
) -> tuple[float | None, str | None]:
    """`(value, note)` — ideal time for the units made ÷ time spent running.

    `cycle_seconds` is the rated seconds per unit from master data,
    `units` everything the machine counted (good and scrap both: a scrapped
    unit took cycle time to make), `runtime_seconds` the observed running time
    in the window.

    Never capped. `note` is a sentence for the screen beside the number: the
    reason when the value is `None`, the master-data finding when it is above
    1.0, and `None` when the number speaks for itself.
    """
    if not cycle_seconds or cycle_seconds <= 0:
        return None, NO_RATING
    if runtime_seconds <= 0:
        return None, NOT_RUNNING
    if units <= 0:
        return None, NOTHING_COUNTED

    value = cycle_seconds * units / runtime_seconds
    if value > 1.0:
        return value, ABOVE_RATING.format(cycle=cycle_seconds)
    return value, None
