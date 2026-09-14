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
* **A number above 1.0** — the MES has recorded more work than its own run
  time can hold, and one of those two numbers is wrong. It does not say which,
  because it cannot tell. See `COUNTS_OUTRUN_RUN_TIME` below for why that sentence
  changed on 2026-09-14.
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

#: Said when the counted work will not fit inside the run time.
#:
#: Until later on 2026-09-14 this sentence read "the rating is slower than the
#: machine, which is a master-data finding". That was a cause the MES cannot
#: know. The lab caught it out: on Northgate's Deburr the MES reported 826
#: units and 1,878 line-seconds of run time at a rated 2.4 s a unit — 1,982
#: seconds of work inside 1,878 seconds of running — and announced the rating
#: was slow. The script that made the data rated the machine at the same 2.4 s
#: and fitted its own 831 units inside its own 1,996 running seconds. The
#: rating was right to within a tenth of a percent; the MES's run time was
#: short, because the machine changed state every 2.7 seconds and the agent
#: only saw it every 15. No timestamp fixes that — decision 0026 records the
#: attempt and what it measured — so the MES says so instead.
#:
#: `performance > 1.0` and "counted work exceeds run time" are the same
#: inequality, and it has at least three causes the MES cannot tell apart:
#: a rating slower than the machine, run time it did not see, and units
#: counted at an instant it did not have the machine running. So the note
#: states the disagreement and its candidates, and names the one of the three
#: the MES can actually measure.
COUNTS_OUTRUN_RUN_TIME = (
    "counted work will not fit inside the run time: {units:g} units at the "
    "rated cycle of {cycle:g} s is {work:g} s of work, inside {runtime:g} s of "
    "running. One of those two numbers is wrong and the MES cannot tell which "
    "— the rating may be slower than the machine, or the run time may be short "
    "of what the machine really ran"
)

#: Appended to `COUNTS_OUTRUN_RUN_TIME` when the MES booked some of the
#: units at an instant its own state history did not have the machine running. Measured,
#: not inferred: it is the one candidate cause the MES holds evidence for.
COUNTED_OUTSIDE = (
    ". {outside:g} of those units were counted while the MES did not have this "
    "machine running"
)


def performance(
    cycle_seconds: float | None,
    units: float,
    runtime_seconds: float,
    counted_outside_run_time: float | None = None,
) -> tuple[float | None, str | None]:
    """`(value, note)` — ideal time for the units made ÷ time spent running.

    `cycle_seconds` is the rated seconds per unit from master data,
    `units` everything the machine counted (good and scrap both: a scrapped
    unit took cycle time to make), `runtime_seconds` the observed running time
    in the window.

    `counted_outside_run_time` is how many of those units the MES booked at an
    instant its own state history did not have the machine running. It is
    named in the note and nothing else: it is not taken out of `units`,
    because the MES does not know that those units were made outside run time
    — only that it counted them there.

    Never capped. `note` is a sentence for the screen beside the number: the
    reason when the value is `None`, the disagreement when it is above 1.0,
    and `None` when the number speaks for itself.
    """
    if not cycle_seconds or cycle_seconds <= 0:
        return None, NO_RATING
    if runtime_seconds <= 0:
        return None, NOT_RUNNING
    if units <= 0:
        return None, NOTHING_COUNTED

    work_seconds = cycle_seconds * units
    value = work_seconds / runtime_seconds
    if value > 1.0:
        note = COUNTS_OUTRUN_RUN_TIME.format(
            units=round(units, 2),
            cycle=round(cycle_seconds, 3),
            work=round(work_seconds, 1),
            runtime=round(runtime_seconds, 1),
        )
        if counted_outside_run_time:
            note += COUNTED_OUTSIDE.format(outside=round(counted_outside_run_time, 2))
        return value, note
    return value, None
