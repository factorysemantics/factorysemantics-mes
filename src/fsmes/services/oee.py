"""OEE performance, in one place, for every screen and every API.

Performance is a **measurement against a rating a person wrote down**, not a
score out of one. Until 2026-09-14 both places that computed it wrote
`min(1.0, ...)`, so a station that out-ran its rated cycle reported exactly
1.0. The lab found what that costs: across two experiments, nine stations in
two plants, the MES said performance was 1.0 on every one of them while the
script that generated the data said 0.943 to 0.9994. A factor that reads 1.0
whatever the line does is not a measurement, and an OEE built on it overstates
the plant.

So there is no cap here, and there never will be: a cap is a lie in the
direction of flattery. There are four answers, and each one says what it is:

* **A number below 1.0** — the machine ran slower than its rating.
* **1.0** — it held its rating.
* **`None` with `outruns_run_time`** — the counted work will not fit inside
  the run time. The arithmetic still produces a ratio, and the ratio is kept
  and published (`ratio`), but it is not reported *as performance*: it is a
  measurement of two of this MES's own records disagreeing, not of the
  machine. See `COUNTS_OUTRUN_RUN_TIME`.
* **`None`** — there is no honest number at all, and `note` says why (house
  rule 2: unknown is a valid answer, zero is not).

Availability and quality are shares of things the MES watched, so they cannot
exceed 1.0 by arithmetic. Performance can, because it is the one factor that
divides a *count* by a *duration* — and the two can come from different
clocks. That is why `replay_factor` is here: on a plant replaying a recorded
line faster than real time, the counts are the line's and the run time is the
wall's, and the division is only honest once the run time is restated on the
line's clock. See `fsmes.services.line_clock`.
"""

from __future__ import annotations

from dataclasses import dataclass

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

#: Why there is no figure beside that sentence — decision 0026 as amended on
#: 2026-09-18. Dividing the two numbers is still arithmetic, and the answer is
#: still kept (`Performance.ratio`); what it is not is a performance figure. A
#: percentage on a screen is read as a measurement of the machine, and this
#: one measures the gap between two of the MES's own records.
NO_FIGURE = (
    ". Until they agree there is no performance figure to print: their ratio "
    "measures the disagreement, not the machine"
)

#: Appended to `COUNTS_OUTRUN_RUN_TIME` when the MES booked some of the
#: units at an instant its own state history did not have the machine running. Measured,
#: not inferred: it is the one candidate cause the MES holds evidence for.
COUNTED_OUTSIDE = (
    ". {outside:g} of those units were counted while the MES did not have this "
    "machine running"
)

#: Appended when the run time in the sentence above is not the run time this
#: MES measured, because the plant is replaying a recorded line faster than
#: real time. Said rather than silently applied: a reader who checks the
#: arithmetic against the run time on the same screen must be able to.
ON_LINE_CLOCK = (
    " (the run time is on the line's clock: {wall:g} s of wall clock at {factor:g}x)"
)


@dataclass(frozen=True)
class Performance:
    """What this MES will say about one machine's performance.

    `value` is the figure a screen may print, and it is `None` whenever there
    is no honest one. `ratio` is the arithmetic — ideal time for the units
    made over the run time — kept whether or not it may be printed, because a
    measurement that cannot be reported as a score is still evidence and
    throwing it away was rejected in decision 0025.

    `outruns_run_time` is the one case where those two differ: `ratio` above
    1.0, `value` `None`, and `note` naming the disagreement.
    """

    value: float | None
    note: str | None
    ratio: float | None = None
    outruns_run_time: bool = False


def performance(
    cycle_seconds: float | None,
    units: float,
    runtime_seconds: float,
    counted_outside_run_time: float | None = None,
    replay_factor: float = 1.0,
) -> Performance:
    """Ideal time for the units made ÷ time spent running.

    `cycle_seconds` is the rated seconds per unit from master data,
    `units` everything the machine counted (good and scrap both: a scrapped
    unit took cycle time to make), `runtime_seconds` the observed running time
    in the window **as this MES measured it**, on its own clock.

    `replay_factor` restates that run time on the line's clock, and is 1.0 for
    every plant that is not a replay running faster than real time — see
    `fsmes.services.line_clock`. The units are already the line's; the run
    time is the wall's; dividing one by the other without this is what printed
    881 % on a plant replaying at 10x.

    `counted_outside_run_time` is how many of those units the MES booked at an
    instant its own state history did not have the machine running. It is
    named in the note and nothing else: it is not taken out of `units`,
    because the MES does not know that those units were made outside run time
    — only that it counted them there.

    Never capped. `note` is a sentence for the screen: the reason when there
    is no figure, the disagreement when the counts outrun the run time, and
    `None` when the number speaks for itself.
    """
    if not cycle_seconds or cycle_seconds <= 0:
        return Performance(None, NO_RATING)
    if runtime_seconds <= 0:
        return Performance(None, NOT_RUNNING)
    if units <= 0:
        return Performance(None, NOTHING_COUNTED)

    on_the_line = runtime_seconds * (replay_factor if replay_factor > 0 else 1.0)
    work_seconds = cycle_seconds * units
    ratio = work_seconds / on_the_line
    if ratio > 1.0:
        note = COUNTS_OUTRUN_RUN_TIME.format(
            units=round(units, 2),
            cycle=round(cycle_seconds, 3),
            work=round(work_seconds, 1),
            runtime=round(on_the_line, 1),
        )
        if replay_factor > 1.0:
            note += ON_LINE_CLOCK.format(wall=round(runtime_seconds, 1), factor=replay_factor)
        if counted_outside_run_time:
            note += COUNTED_OUTSIDE.format(outside=round(counted_outside_run_time, 2))
        note += NO_FIGURE
        # The figure is withheld; the measurement is not. Decision 0026,
        # amended 2026-09-18 after a replayed plant printed 980 % OEE on the
        # dashboard and the maintainer read it, correctly, as an error.
        return Performance(None, note, ratio=ratio, outruns_run_time=True)
    return Performance(ratio, None, ratio=ratio)
