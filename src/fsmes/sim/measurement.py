"""The measuring half of a simulated floor: which gauge took the reading.

A plant's quality history is not a list of process values. It is a list of
*measurements*, each one taken by a named instrument with its own resolution
and its own calibration date - and the difference is the whole reason a
control chart can be argued with. Until this module existed the simulated
floor posted the filler's tag value as if the number had arrived without
anybody measuring anything, which left every point on the chart unable to
answer the first question an engineer asks about it: did the process move,
or did the gauge?

**The gauges are the pack's and the script is the pack's.** `masterdata/
gauges.json` puts the instruments on the plant's register - that is master
data, and the MES owns it. What this reads is the *floor's* own script: which
gauge measures which characteristic, how often each is picked up, and which
one is drifting between calibrations. None of that is something the MES
knows or should store; it is a fact about the simulated people on the
simulated floor, so it lives in the pack as data (`[files] floor`) and this
module never changes when a plant's story does.

Nothing here talks to the MES. The floor reads the gauge register over the
API and hands the rows in, so a drift that shows up in the plant's records
is reproducible from the same seed in a test with no plant at all.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

#: What the floor assumes about its own repeatability when the script says
#: nothing: no reading error at all. Deliberately zero rather than a guessed
#: fraction of the resolution - a simulated plant that invented a spread
#: nobody wrote down would be putting variation into a quality history and
#: calling it the process.
NO_SPREAD = 0.0


def load(path: str | Path | None) -> dict:
    """One floor script, or `{}` for a plant that has none.

    A plant with no script is the normal case and is unchanged by all of
    this: its floor records what the tag said, names no gauge, and labels no
    stop - exactly as it did before. A script that will not parse is an
    error worth stopping for, because a floor silently running the default
    story is a plant whose data nobody can explain afterwards.
    """
    if not path:
        return {}
    text = Path(path).read_text(encoding="utf-8")
    loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} is a floor script: one JSON object, not a list")
    return loaded


@dataclass(frozen=True)
class Instrument:
    """One gauge as the floor uses it: the register's facts, and the script's.

    `resolution` and `last_calibrated` come from the plant's own register, so
    a gauge calibrated on the screen is calibrated here in the same instant.
    `share` and the drift are the script's.
    """

    code: str
    share: float = 1.0
    resolution: float | None = None
    last_calibrated: date | None = None
    #: How far this gauge's readings move per day since it was last
    #: calibrated, and the bias it settles at. A real gauge does not drift
    #: forever: it goes out, stays out, and is found at the next calibration.
    drift_per_day: float = 0.0
    max_bias: float = 0.0

    def bias(self, today: date) -> float:
        """What this gauge is reading high (or low) by, today.

        Zero on the day it was calibrated, and zero for a gauge the script
        says nothing about. A gauge with no calibration date has no measured
        starting point, so its bias is unknown rather than large - and
        unknown here is zero *bias*, with the register separately reporting
        the gauge as overdue, which is the fact that matters.
        """
        if not self.drift_per_day or self.last_calibrated is None:
            return 0.0
        days = max((today - self.last_calibrated).days, 0)
        drifted = self.drift_per_day * days
        if self.max_bias:
            limit = abs(self.max_bias)
            drifted = max(-limit, min(limit, drifted))
        return drifted

    def read(self, true_value: float, *, rng: random.Random, today: date,
             spread: float = NO_SPREAD) -> float:
        """What this gauge says the value is.

        Three things happen to a measurement that never happen to a tag
        value: the instrument's bias is added, the person reading it does not
        repeat themselves exactly, and the answer is written to the
        resolution the gauge can actually read. A simulated plant that skips
        all three produces a control chart of the process and calls it a
        chart of the measurements.
        """
        reading = true_value + self.bias(today)
        if spread:
            reading += rng.gauss(0.0, spread)
        if self.resolution:
            reading = round(reading / self.resolution) * self.resolution
        return round(reading, 6)


class Bench:
    """The gauges one simulated floor measures with, and how it picks one.

    Built from the floor script and the plant's own gauge register. A
    characteristic the script does not mention has no gauges here, and the
    floor records its readings with none - which is *not recorded*, and the
    honest answer.
    """

    def __init__(self, script: dict, register: list[dict] | None = None) -> None:
        measurement = script.get("measurement") or {}
        self.stale_after_s = float(measurement.get("stale_after_s") or 0.0)
        self.default_spread = float(measurement.get("default_spread") or NO_SPREAD)
        self.shift_spread = {str(k): float(v)
                             for k, v in (measurement.get("shift_spread") or {}).items()}
        self._characteristics = measurement.get("characteristics") or {}
        self._register = {row.get("code"): row for row in (register or [])}

    def knows(self, characteristic: str) -> bool:
        return characteristic in self._characteristics

    @property
    def gauge_codes(self) -> list[str]:
        """Every gauge this floor's script names, in the order it names them."""
        return [str(entry.get("gauge"))
                for spec in self._characteristics.values()
                for entry in (spec.get("gauges") or [])
                if entry.get("gauge")]

    def instruments(self, characteristic: str) -> list[Instrument]:
        """The gauges for one characteristic, as the floor sees them today.

        A gauge the script names and the register does not hold is left out
        rather than invented: the plant's register is the authority on which
        instruments exist, and a floor that measured with one the plant has
        never heard of would be writing readings the MES must then refuse.

        So is a gauge the register says is not in service. Out of service,
        lost or overdue-and-withdrawn is a decision somebody made on the
        screen, and a floor that went on measuring with it would be the
        reason that decision meant nothing.
        """
        out: list[Instrument] = []
        for entry in (self._characteristics.get(characteristic) or {}).get("gauges") or []:
            code = entry.get("gauge")
            row = self._register.get(code)
            if row is None:
                continue
            if row.get("status") not in (None, "in_service"):
                continue
            drift = entry.get("drift") or {}
            out.append(Instrument(
                code=str(code),
                share=float(entry.get("share", 1.0)),
                resolution=row.get("resolution"),
                last_calibrated=_as_date(row.get("last_calibrated")),
                drift_per_day=float(drift.get("per_day", 0.0)),
                max_bias=float(drift.get("max_bias", 0.0)),
            ))
        return out

    def spread(self, characteristic: str, shift_code: str | None) -> float:
        """How much two readings of the same thing disagree, this shift.

        A night shift is not a worse plant; it is a thinner one - fewer
        people, less light, nobody to ask - and the spread of its
        measurements says so. The multiplier is the script's, per shift
        code, so a plant that works one shift says nothing and gets nothing.
        """
        spec = self._characteristics.get(characteristic) or {}
        base = float(spec.get("spread", self.default_spread))
        return base * self.shift_spread.get(shift_code or "", 1.0)

    def pick(self, characteristic: str, rng: random.Random) -> Instrument | None:
        """Which gauge gets used, weighted by the script's shares.

        Weighted rather than alternating, because a plant with two scales
        does not use them in turn: one lives at the station and one comes out
        when the first is busy or being checked, and a records set in which
        both took exactly half the readings is a records set nobody would
        recognise.
        """
        choices = [i for i in self.instruments(characteristic) if i.share > 0]
        if not choices:
            return None
        total = sum(i.share for i in choices)
        landed = rng.uniform(0.0, total)
        for instrument in choices:
            landed -= instrument.share
            if landed <= 0:
                return instrument
        return choices[-1]

    def measure(self, characteristic: str, true_value: float, *, rng: random.Random,
                today: date, shift_code: str | None = None) -> tuple[str | None, float]:
        """`(gauge code, reading)` - or `(None, the value as it stood)`.

        The second case is a characteristic this floor has no gauge for. The
        value is passed through untouched rather than nudged, because an
        unmeasured number that has been given a reading error is a fiction
        twice over.
        """
        instrument = self.pick(characteristic, rng)
        if instrument is None:
            return None, true_value
        reading = instrument.read(true_value, rng=rng, today=today,
                                  spread=self.spread(characteristic, shift_code))
        return instrument.code, reading


def _as_date(value) -> date | None:
    """A register row's `last_calibrated`, whether it arrived as a date or as
    the string an HTTP response carries."""
    if value is None or isinstance(value, date):
        return value if isinstance(value, date) else None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
