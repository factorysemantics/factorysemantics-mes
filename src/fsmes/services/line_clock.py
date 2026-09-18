"""What one second of wall clock is worth in line time.

A plant that replays a recorded line faster than it was recorded has two
clocks. The counters advance at the line's pace — the recording's own seconds,
played back at whatever speed was asked for — while every duration this MES
measures is wall clock: the state intervals it watched, the window it looked
back over, the run time it adds up. At 1x those are the same clock and none of
this matters. At 10x a machine that ran a recorded hour ran it in six minutes
of wall clock, and anything that divides a count by a duration is out by ten.

That is how a bottling plant came to print **performance 881 % and OEE 980 %**
on the morning of 2026-09-18: `fsmes fleet start … --speed 10`, a counter ten
times faster than the clock the run time was measured on, and a screen that
printed the ratio.

The MES is *told* the speed rather than inferring it: `fsmes fleet start
--speed` puts `MES_SIM_SPEED` into the environment of every process of that
plant, the API among them (`fsmes.plant.plant_env`), so the process that
answers `/dashboard` knows what its own clock is worth. A real plant sets
nothing, the factor is 1.0, and every line below is the identity.

Config, not code, at the plant boundary (house rule 4): the speed is a fact
about how this plant was started, and nothing here asks the line what it
thinks it is. A replay started by hand, with the speed given to the replay
process alone, tells this MES nothing — and then the MES cannot know, which is
what `MES_SIM_SPEED` on the plant is for.
"""

from __future__ import annotations

#: Said beside the figures of a plant whose clock is not the line's. A fact
#: about how this deployment was started, not a warning about the plant.
REPLAYING = (
    "this plant is replaying a recorded line at {factor:g}x wall clock, so one "
    "second on this screen is {factor:g} seconds of the line. Rates are "
    "computed on the line's clock; the durations beside them are the wall "
    "clock this MES measured"
)

#: What to run a plant at when somebody is going to look at it. Said here so
#: the docs and the screens cannot disagree about it.
STANDING_PLANT = (
    "a plant meant to be looked at runs at 1x, where the two clocks are the "
    "same clock"
)


def factor() -> float:
    """How many line seconds one wall second of this plant is worth.

    1.0 for every plant that is not replaying above real time, which is every
    real plant and every standing demo. Never zero and never negative: a
    nonsense speed is treated as real time rather than used as a divisor.
    """
    from fsmes.config import get_settings

    value = float(getattr(get_settings(), "sim_speed", 1.0) or 1.0)
    if value <= 0.0:
        return 1.0
    return value


def replaying(the_factor: float | None = None) -> bool:
    """True when this plant's clock is not the line's — a test harness."""
    return (factor() if the_factor is None else the_factor) > 1.0


def line_seconds(wall_seconds: float, the_factor: float | None = None) -> float:
    """`wall_seconds` of this MES's clock, in seconds of the line's."""
    return wall_seconds * (factor() if the_factor is None else the_factor)


def summary(the_factor: float | None = None) -> dict | None:
    """What `/health`, the console and the screens say about the clock, or
    `None` when there is nothing to say because the two clocks agree."""
    value = factor() if the_factor is None else the_factor
    if value <= 1.0:
        return None
    return {
        "factor": value,
        "means": REPLAYING.format(factor=value),
        "standing_plant": STANDING_PLANT,
    }
