"""Statistical process control: is the process stable, and is it capable?

Two different questions, and confusing them is the classic mistake.

*Stable* asks whether the process is behaving like itself - judged against
control limits computed from its own variation, not from the tolerance. A
process can sit comfortably inside spec while drifting badly, and a chart
drawn against the specification will never show it.

*Capable* asks whether that variation fits inside what the customer asked for.
Cpk without stability is meaningless: a capability number computed on an
out-of-control process describes a process that no longer exists.

**The chart type follows the sampling plan.** A plant that inspects one bottle
at a time has no rational subgroups, and pretending otherwise produces control
limits that are simply wrong - so that plant gets individuals and moving
range, which is what this module did and all it did until 2026-10-06. Where
the plan says *five bottles at a time*, the five readings are a subgroup: the
honest chart is X-bar and R, the points are the sample means, and the rules
run on the means. Running them on the five single readings instead would flag
ordinary within-sample noise and miss the shift between samples that the plan
exists to catch. Which of the two a characteristic gets is `QualitySpec.
sample_size` - the plant's sampling plan, recorded on the specification -
and never this module's guess. Decision record 0040.

On an individuals chart both halves are drawn. The individuals chart asks
whether a reading is where it should be; the moving-range chart asks whether
the gap between consecutive readings is. They are two different failures, and
only the second one sees a process that stays inside its limits while jumping
further and further between them - which is the one that makes the first
chart's limits widen until they stop meaning anything. The mean moving range
was always here, because sigma is estimated from it; since 2026-10-06 the
series it is the mean of is on the chart too, with its own centre line, its
own upper limit (`D4_N2` times the mean moving range) and a rule of its own.

**A range beyond its upper limit is rule 5, on either chart.** The moving
range between two readings here, the spread inside one sample on a sampled
chart - one statement about a process, so one rule number for it, drawn and
recorded on every plant (decision 0036) and raising a hold where the plant's
`hold_rules` names it. Two numbers for it, which is what this module briefly
had, would have meant a plant asking to be called about a range having to say
so twice and a reader counting how often it happened having to read two
columns.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain import (
    Equipment,
    Material,
    NcStatus,
    NonConformance,
    QualityCheck,
    QualitySample,
    QualitySpec,
    SpcSignal,
    WorkOrder,
)
from fsmes.services import NotFound

# For an individuals chart the estimate of sigma is mean moving range over
# d2 for n=2. 1.128 is the standard constant; it is written out rather than
# imported so nobody has to go looking for what it means.
D2_N2 = 1.128

#: The subgroup constants, for a sample of two to ten. Written out rather
#: than computed, for the same reason `D2_N2` is: these are the published
#: factors every SPC textbook and every commercial package uses, and a reader
#: checking this product's limits against their own has to be able to see the
#: number being used.
#:
#: Each row is `(A2, D3, D4, d2)`:
#:
#: * ``A2`` - the X-bar chart's limits are ``X̿ ± A2·R̄``. A2 is ``3 / (d2·√n)``,
#:   so those limits are three sigma on the *mean* and not on a reading.
#: * ``D3``, ``D4`` - the R chart's lower and upper limits, ``D3·R̄`` and
#:   ``D4·R̄``. D3 is zero below n = 7: with six readings or fewer there is no
#:   lower limit on a range, because a range of nothing is possible.
#: * ``d2`` - the estimate of the process sigma is ``R̄ / d2``, which is what
#:   capability is computed from. `D2_N2` above is this column's n = 2 entry,
#:   and the two agree on purpose.
#:
#: Ten is the ceiling because the range stops being an efficient estimate of
#: spread above about ten, and a plan that inspects more than ten at a time
#: wants X-bar and S, which this product does not draw yet. A specification
#: that asks for one is refused when it is written, not when it is charted.
SUBGROUP: dict[int, tuple[float, float, float, float]] = {
    2: (1.880, 0.0, 3.267, 1.128),
    3: (1.023, 0.0, 2.574, 1.693),
    4: (0.729, 0.0, 2.282, 2.059),
    5: (0.577, 0.0, 2.114, 2.326),
    6: (0.483, 0.0, 2.004, 2.534),
    7: (0.419, 0.076, 1.924, 2.704),
    8: (0.373, 0.136, 1.864, 2.847),
    9: (0.337, 0.184, 1.816, 2.970),
    10: (0.308, 0.223, 1.777, 3.078),
}

#: The largest sample this product can chart, from the table above.
MAX_SAMPLE_SIZE = max(SUBGROUP)

#: What the two chart kinds are called in the envelope. A screen, the chart
#: kit and an agent all read the same word, so none of them has to infer the
#: chart type from which keys happen to be present.
INDIVIDUALS = "imr"
XBAR_R = "xbar_r"

# The moving-range chart's own limits come from the table above, at n = 2,
# because a moving range *is* a subgroup of two: the upper limit is `D4·R̄`
# (3.267 times the mean moving range) and the lower one is `D3·R̄`. Named here
# rather than indexed at each point of use, and read out of the table rather
# than written down a second time so the two cannot drift apart. `D2_N2` above
# is the same row's d2 and says so.
#
# D3 at n = 2 is zero, so the lower limit is zero. It is stated rather than
# left out, because a chart with no lower line and a chart whose lower line is
# nought look the same drawn and are not the same claim.
D3_N2 = SUBGROUP[2][1]
D4_N2 = SUBGROUP[2][2]
# Fewest points worth computing limits from. Below this the limits move so
# much with each new reading that they mislead more than they inform. The
# plant's, since the configuration audit of 2026-09-21 named it - `[quality]
# spc_min_points`, twelve by default, which is what was here.
MIN_POINTS = 12
# How many readings back a chart and the rules look. `[quality] spc_history`,
# two hundred by default.
HISTORY = 200
# Where a process stops being capable, and where it stops being marginal.
# `[quality] cpk_capable` and `cpk_marginal`. Only the English word beside the
# figure moves: the Cpk is arithmetic and means the same on every plant.
CPK_CAPABLE = 1.33
CPK_MARGINAL = 1.0
# Which rules raise a hold, and which of those are a major finding.
# `[quality] hold_rules` and `major_rules`; the four individuals rules and rule
# 1 alone, which is what this product has always done.
#
# Rule 5 - the moving range beyond its upper limit - is DRAWN on every plant
# and is deliberately NOT in this list. A plant that has run this product for
# a year without a moving-range chart should not come in one morning to a
# queue of holds about readings it already lived through; it turns the rule on
# when it has looked at the chart and decided it wants to be called.
HOLD_RULES = (1, 2, 3, 4)
MAJOR_RULES = (1,)


def _number(session: Session, name: str, fallback):
    """One of this plant's quality numbers, read at the moment it is needed.

    Read through `fsmes.services.plant_settings`, which is three layers: the
    row this plant's own administrator edited, the setting its pack compiled,
    and the literal above. So a number a person changes on Quality's
    Configuration page is in force on the next reading with no restart, and a plant
    that has changed none of them reads exactly what it read before the table
    existed.

    It takes the session its caller already has rather than opening one. A
    setting owned by the database is read from the database, and the reading is
    memoised on the unit of work (`plant_settings.rows`), so a screen that
    draws four charts costs one query and none of it can go stale inside a
    request.
    """
    from fsmes.services import plant_settings

    return plant_settings.value(session, "quality", name, fallback)


def min_points(session: Session) -> int:
    """The fewest readings this plant draws control limits from."""
    return int(_number(session, "spc_min_points", MIN_POINTS))


def history(session: Session) -> int:
    """How many readings back this plant's charts and rules look."""
    return int(_number(session, "spc_history", HISTORY))


def cpk_bars(session: Session) -> tuple[float, float]:
    """`(capable, marginal)` - where this plant draws the two words.

    Returned together because they are one judgment written as two numbers,
    and a caller that read one without the other could print *capable* and
    *not capable* for the same figure.
    """
    return (float(_number(session, "cpk_capable", CPK_CAPABLE)),
            float(_number(session, "cpk_marginal", CPK_MARGINAL)))


def _spec(session: Session, material: str, characteristic: str) -> QualitySpec:
    mat = session.scalar(select(Material).where(Material.code == material))
    if mat is None:
        raise NotFound(f"no material {material}")
    spec = session.scalar(select(QualitySpec).where(
        QualitySpec.material_id == mat.id,
        QualitySpec.characteristic == characteristic))
    if spec is None:
        raise NotFound(f"no specification for {material}/{characteristic}")
    return spec


def plan_size(spec: QualitySpec) -> int:
    """How many pieces this characteristic is inspected at a time.

    One unless the specification says otherwise, which is what every
    specification written before `sample_size` existed says and is why null
    and 1 mean the same thing here. A size this product has no constants for
    is charted as individuals rather than with the wrong constants - the write
    path refuses such a specification, so this is the belt to that braces.
    """
    size = spec.sample_size or 1
    return size if size in SUBGROUP else 1


@dataclass
class Series:
    """What a chart plots, whichever chart it is.

    One shape for both kinds, so the limits, the rules, the record of a
    firing and the envelope are written once. `values` is the plotted
    statistic - a reading on an individuals chart, a sample mean on an X-bar
    chart - and `keys` is the id each point is identified by, which is a
    check id for individuals and a sample id for samples. Everything that
    tells the two apart is decided here and nowhere else.
    """

    kind: str
    size: int
    values: list[float]
    stamps: list[datetime]
    keys: list[int]
    #: The reading each point points at. For a sample it is the last of the
    #: n, so a signal still names a row in `quality_checks` that a person can
    #: open; the sample and all its readings are in the evidence.
    checks: list[QualityCheck]
    #: Every individual reading behind the series, in order. The same as
    #: `values` on an individuals chart; n times longer on a sampled one. The
    #: overall spread Pp and Ppk describe is this, because this is what the
    #: customer received.
    readings: list[float]
    #: One entry per sample: id, stamp, mean, range, and the reading ids.
    #: Empty on an individuals chart.
    samples: list[dict] = field(default_factory=list)
    #: The range of each sample, in the same order as `values`.
    ranges: list[float] = field(default_factory=list)
    #: Samples that are stored but not plotted, because they hold a different
    #: number of readings than the specification now asks for. Charting those
    #: with this sample size's constants would produce limits that are simply
    #: wrong, so they are left out and counted - the chart says how many.
    set_aside: int = 0

    @property
    def sampled(self) -> bool:
        return self.kind == XBAR_R


def _series(session: Session, spec: QualitySpec, limit: int | None = None) -> Series:
    """This characteristic's recent history, as the chart plots it."""
    limit = history(session) if limit is None else limit
    size = plan_size(spec)
    if size == 1:
        checks = list(session.scalars(
            select(QualityCheck).where(QualityCheck.spec_id == spec.id)
            .order_by(QualityCheck.id.desc()).limit(limit)))
        checks.reverse()
        values = [c.value for c in checks]
        return Series(kind=INDIVIDUALS, size=1, values=values,
                      stamps=[c.ts for c in checks], keys=[c.id for c in checks],
                      checks=checks, readings=list(values))

    samples = list(session.scalars(
        select(QualitySample).where(QualitySample.spec_id == spec.id)
        .order_by(QualitySample.id.desc()).limit(limit)))
    samples.reverse()
    by_sample: dict[int, list[QualityCheck]] = {}
    if samples:
        for check in session.scalars(
                select(QualityCheck)
                .where(QualityCheck.sample_id.in_([s.id for s in samples]))
                .order_by(QualityCheck.id)):
            by_sample.setdefault(check.sample_id, []).append(check)

    series = Series(kind=XBAR_R, size=size, values=[], stamps=[], keys=[],
                    checks=[], readings=[])
    for sample in samples:
        readings = by_sample.get(sample.id, [])
        if len(readings) != size:
            series.set_aside += 1
            continue
        values = [c.value for c in readings]
        mean = sum(values) / size
        spread = max(values) - min(values)
        series.values.append(mean)
        series.stamps.append(sample.ts)
        series.keys.append(sample.id)
        series.checks.append(readings[-1])
        series.readings.extend(values)
        series.ranges.append(spread)
        series.samples.append({
            "sample": sample.id, "ts": sample.ts, "n": size,
            "mean": round(mean, 4), "range": round(spread, 4),
            "readings": [{"check": c.id, "value": c.value} for c in readings],
        })
    return series


def _western_electric(values: list[float], centre: float, sigma: float) -> list[dict]:
    """The four rules worth having, each named in plain words.

    Rule 1 is the alarm; rules 2 to 4 are the early warnings that make a
    control chart worth more than a tolerance check.
    """
    if sigma <= 0:
        return []
    signals: list[dict] = []

    def zone(value: float) -> float:
        return (value - centre) / sigma

    z = [zone(v) for v in values]

    for i, score in enumerate(z):
        if abs(score) > 3:
            signals.append({"rule": 1, "index": i, "value": values[i],
                            "what": "a point beyond three sigma"})

    for i in range(2, len(z)):
        window = z[i - 2:i + 1]
        for side in (1, -1):
            if sum(1 for s in window if s * side > 2) >= 2:
                signals.append({"rule": 2, "index": i, "value": values[i],
                                "what": "two of three points beyond two sigma"})
                break

    for i in range(4, len(z)):
        window = z[i - 4:i + 1]
        for side in (1, -1):
            if sum(1 for s in window if s * side > 1) >= 4:
                signals.append({"rule": 3, "index": i, "value": values[i],
                                "what": "four of five points beyond one sigma"})
                break

    for i in range(7, len(z)):
        window = z[i - 7:i + 1]
        if all(s > 0 for s in window) or all(s < 0 for s in window):
            signals.append({"rule": 4, "index": i, "value": values[i],
                            "what": "eight points in a row on one side of centre"})

    # One signal per point, strongest rule first - a chart that flags the same
    # reading four times teaches people to ignore it.
    best: dict[int, dict] = {}
    for signal in sorted(signals, key=lambda s: s["rule"]):
        best.setdefault(signal["index"], signal)
    return [best[i] for i in sorted(best)]


def _limits(series: Series) -> dict | None:
    """The control limits for this series, by the arithmetic its kind asks for.

    `centre` and `sigma` mean the same thing on both charts: the centre line,
    and the sigma of *one plotted point*, so `centre ± 3·sigma` is always the
    drawn limit. They are not the same number on a sampled chart - a mean of
    five varies less than a reading does - which is why `sigma_within`, the
    spread of the process itself, is a separate figure and is the one
    capability is computed from. Confusing the two is how a sampled chart
    comes to claim a Cpk the process never had.

    None when there is nothing to divide by: readings that never move are not
    an in-control process, they are an un-chartable one.
    """
    values = series.values
    if not values:
        return None
    centre = sum(values) / len(values)

    if not series.sampled:
        moving = [abs(b - a) for a, b in itertools.pairwise(values)]
        mean_range = sum(moving) / len(moving) if moving else 0.0
        sigma = mean_range / D2_N2
        if sigma <= 0:
            return None
        return {
            "centre": round(centre, 4),
            "sigma": round(sigma, 4),
            "upper": round(centre + 3 * sigma, 4),
            "lower": round(centre - 3 * sigma, 4),
            "mean_moving_range": round(mean_range, 4),
            "sigma_within": round(sigma, 4),
        }

    a2, d3, d4, d2 = SUBGROUP[series.size]
    mean_range = sum(series.ranges) / len(series.ranges)
    # Three sigma on the mean, by the textbook form: X-bar-bar plus or minus
    # A2 times R-bar. A2 already carries the division by root n, so this is
    # not three times the figure below - it is three times the sigma of a
    # mean of n.
    half = a2 * mean_range
    sigma_mean = half / 3
    sigma_within = mean_range / d2
    if sigma_within <= 0:
        return None
    return {
        "centre": round(centre, 4),
        "sigma": round(sigma_mean, 4),
        "upper": round(centre + half, 4),
        "lower": round(centre - half, 4),
        "mean_range": round(mean_range, 4),
        "sigma_within": round(sigma_within, 4),
        "sample_size": series.size,
        # The second chart. Its centre is the mean range, and below n = 7 it
        # has no lower limit because D3 is zero: a sample of five identical
        # readings is unremarkable, not a signal.
        "range_chart": {
            "centre": round(mean_range, 4),
            "upper": round(d4 * mean_range, 4),
            "lower": round(d3 * mean_range, 4),
        },
        # The constants used, so a reader checking this against their own
        # table can see which row was taken.
        "constants": {"a2": a2, "d3": d3, "d4": d4, "d2": d2},
    }


# ------------------------------------------------------------- the range rule

#: The range rule's number. Rules 1 to 4 judge where the plotted point sat;
#: this one judges the *range* beside it, and there is one of those on either
#: kind of chart - the moving range between two readings on an individuals
#: chart, the spread inside one sample on a sampled one. One rule, because it
#: is one statement about a process: the range went beyond its own upper
#: limit, so the sigma the limits beside it were computed from is in
#: question. Two numbers for it would mean a plant that asked to hold on a
#: range had to ask twice, and two columns of `SpcSignal.rule` to read when
#: somebody counts how often it happens.
#:
#: The numbering is the product's, not a plant's, for the same reason 1 to 4
#: are: a plant that renumbered it would publish `SpcSignal.rule = 5` meaning
#: something nobody else means by it.
RANGE_RULE = 5

#: What rule 5 means in words, per chart. The rule is one; the sentence a
#: person reads has to name the range they are looking at.
MR_WHAT = "a moving range beyond its upper limit"
SAMPLE_RANGE_WHAT = "a sample range beyond the upper range limit"


def _moving_ranges(values: list[float]) -> list[float]:
    """The gap between each reading and the one before it, in order.

    One shorter than the readings: the first reading has nothing before it, so
    it has no range, and inventing a zero for it would pull the mean down and
    with it every limit on both charts.
    """
    return [abs(b - a) for a, b in itertools.pairwise(values)]


def _moving_range_rule(ranges: list[float], upper: float) -> list[dict]:
    """The one rule a moving-range chart has: a range beyond its upper limit.

    Only the one, and deliberately. The Western Electric zone rules are about
    a symmetric distribution around a centre line, and a moving range has
    neither - it is bounded below by zero and skewed right, so "two of three
    beyond two sigma" below the centre of a moving range is a sentence that
    looks like statistics and is not. A range *below* the limit is never a
    signal at n = 2 either: `D3_N2` is zero, and two readings cannot be close
    enough together to be news.
    """
    if upper <= 0:
        return []
    return [{"rule": RANGE_RULE, "index": i, "value": round(value, 4), "what": MR_WHAT}
            for i, value in enumerate(ranges) if value > upper]


def _moving_range(checks: list[QualityCheck], *, fewest: int, note: str | None) -> dict:
    """The moving-range chart: the series, its limits, and what fired on it.

    Each point names the *later* of the two readings it is the gap between,
    because that is the reading a person can open - `check` is the same key
    the individuals points carry and the same id the dossier read takes. The
    earlier one is named too: a reader asking *which two?* is owed both, and
    the answer must not be "count back one dot".

    Below `fewest` readings there are no limits, and the same sentence the
    individuals chart prints says why. Limits from six moving ranges move with
    every reading exactly as the individuals limits do, and a moving-range
    chart is the more easily misled of the two: one keyed-in duplicate reading
    is a range of zero that drags the centre line down and the upper limit
    with it.
    """
    values = [c.value for c in checks]
    ranges = _moving_ranges(values)
    points = [
        {"range": round(value, 4), "ts": checks[i + 1].ts,
         "check": checks[i + 1].id, "previous_check": checks[i].id}
        for i, value in enumerate(ranges)
    ]
    # The total, stated whether or not the limits could be drawn (house rule:
    # every list says what it is a list of).
    base = {"points": points, "n": len(points), "readings": len(values)}
    if len(values) < fewest or not ranges:
        return _said({**base, "centre": None, "upper": None, "lower": None,
                      "signals": [], "note": note})
    centre = sum(ranges) / len(ranges)
    return _said({
        **base,
        "centre": round(centre, 4),
        "upper": round(D4_N2 * centre, 4),
        # Nought, not absent. See `D3_N2`.
        "lower": round(D3_N2 * centre, 4),
        "signals": _moving_range_rule(ranges, D4_N2 * centre),
        "note": None,
    })


def _range_block(series: Series, control: dict | None, signals: list[dict],
                 note: str | None) -> dict:
    """The R chart: the spread inside each sample, with its limits and its own sentence.

    The lower half of a sampled chart, in the same shape and the same place in
    the envelope as `moving_range` is on an individuals one - so a reader, a
    screen or a test that knows one knows the other. Its limits are the ones
    `_limits` already worked out (`control.range_chart`), copied and not
    recomputed; `centre`, `upper` and `lower` are null when there were too few
    samples for limits at all, and `note` says why in the same words the upper
    chart uses.

    It carries no `signals` key, and that is the one place the two kinds do not
    mirror each other. A moving range is the gap between two readings, so the
    moving-range series is one shorter than the readings and has an index space
    of its own - which is exactly why it needs its own list of firings. A
    sample range is the spread inside ONE sample, so both halves of a sampled
    chart are indexed by the same samples, and rule 5's firings sit in
    `signals` beside rules 1 to 4 with the same `index`. A second copy here
    would be the same firing in two lists, which is how a reader counting them
    comes to report twice as many as the plant had.
    """
    points = [
        {"range": round(spread, 4), "ts": stamp, "sample": key, "check": check.id,
         "n": series.size}
        for spread, stamp, key, check in zip(
            series.ranges, series.stamps, series.keys, series.checks, strict=False)
    ]
    limits = (control or {}).get("range_chart") or {}
    fired = [s for s in signals if s["rule"] == RANGE_RULE]
    block = {
        "points": points,
        # Every list states its total: how many ranges are drawn, and how many
        # readings they are the spread of.
        "n": len(points),
        "readings": len(series.readings),
        "sample_size": series.size,
        "centre": limits.get("centre"),
        "upper": limits.get("upper"),
        # Nought, not absent, below n = 7: D3 is zero there, so a sample of
        # five identical readings is unremarkable rather than a signal. See
        # `SUBGROUP`.
        "lower": limits.get("lower"),
        "note": note,
        # Null, not True, when there are no limits to be inside: a chart with
        # nothing to judge against has not found the process settled.
        "stable": None if limits.get("upper") is None else not fired,
    }
    block["verdict"] = _range_verdict(block, fired)
    return block


def _range_verdict(block: dict, fired: list[dict]) -> str:
    """The range chart's own sentence, in the same shape as `_mr_verdict`'s.

    Its own, because the two halves of a sampled chart answer two questions -
    where the average of five bottles sat, and how far apart those five were -
    and one sentence for both is how a reader comes to call a process settled
    when every sample of it is spread twice as wide as the last.
    """
    if block["n"] == 0:
        return (f"no samples yet; the first {block['sample_size']} readings "
                f"recorded together make the first range")
    if block.get("note"):
        return block["note"]
    if fired:
        return (f"out of control - rule {RANGE_RULE} fired on {len(fired)} of "
                f"{block['n']} sample ranges. Those samples are spread wider "
                f"inside themselves than the rest of the process, and sigma on "
                f"both charts is estimated from the mean of this series.")
    return (f"in control - every one of {block['n']} sample ranges is inside "
            f"the upper range limit")


def _said(moving: dict) -> dict:
    """The verdict and the one-word answer, added to a moving-range block.

    Here rather than in `chart` so that every block this module hands out has
    the same keys whether or not there were enough readings to draw limits
    from. A reader of the payload that had to check which branch produced it
    before trusting `stable` is a reader who will get it wrong once.
    """
    moving["stable"] = not moving["signals"]
    moving["verdict"] = _mr_verdict(moving)
    return moving




def capability(values: list[float], lower_spec: float | None,
               upper_spec: float | None, *, fewest: int,
               samples: list[list[float]] | None = None) -> dict | None:
    """Cp, Cpk, Pp for a characteristic against its specification, by the same
    arithmetic the SPC chart uses.

    One reading at a time: sigma from the mean moving range, the overall
    spread from the sample standard deviation. `samples`, when given, is the
    readings of each subgroup in the order they were taken - then `fewest`
    counts samples rather than readings, sigma is the mean range over d2, and
    stability is judged on the means, which is the whole point of a sampling
    plan. `values` is still every individual reading, because Pp and Ppk
    describe what the customer received and the customer received pieces.

    None when there are too few, no limits, or no variation to divide by; the
    caller says why rather than printing a number nobody should trust.
    """
    if lower_spec is None or upper_spec is None:
        return None
    if samples is not None:
        series = _series_from_samples(samples)
        if series is None or len(series.values) < fewest:
            return None
        plotted = series.values
    else:
        if len(values) < fewest:
            return None
        series = Series(kind=INDIVIDUALS, size=1, values=list(values), stamps=[],
                        keys=[], checks=[], readings=list(values))
        plotted = series.values

    control = _limits(series)
    if control is None:
        return None
    sigma = control["sigma_within"]
    centre = control["centre"]
    cpu = (upper_spec - centre) / (3 * sigma)
    cpl = (centre - lower_spec) / (3 * sigma)
    readings = series.readings
    overall = (math.sqrt(sum((v - centre) ** 2 for v in readings) / (len(readings) - 1))
               if len(readings) > 1 else 0.0)
    return {
        "n": len(plotted), "centre": round(centre, 4), "sigma": round(sigma, 4),
        "cp": round((upper_spec - lower_spec) / (6 * sigma), 3), "cpk": round(min(cpu, cpl), 3),
        "cpu": round(cpu, 3), "cpl": round(cpl, 3),
        "pp": round((upper_spec - lower_spec) / (6 * overall), 3) if overall > 0 else None,
        "ppk": round(min((upper_spec - centre) / (3 * overall), (centre - lower_spec) / (3 * overall)), 3)
        if overall > 0 else None,
        "sigma_overall": round(overall, 4),
        "readings": len(readings),
        "sample_size": series.size,
        "stable": not _western_electric(plotted, centre, control["sigma"]),
    }


def _series_from_samples(samples: list[list[float]]) -> Series | None:
    """A series of means and ranges from bare lists of readings.

    For `capability`, whose callers - the certificate of analysis among them -
    hold readings and not rows.
    """
    sizes = {len(s) for s in samples if s}
    if len(sizes) != 1:
        # Subgroups of different sizes have no single set of constants, and
        # averaging their ranges would be arithmetic nobody can check.
        return None
    size = sizes.pop()
    if size == 1:
        flat = [s[0] for s in samples]
        return Series(kind=INDIVIDUALS, size=1, values=flat, stamps=[], keys=[],
                      checks=[], readings=list(flat))
    if size not in SUBGROUP:
        return None
    series = Series(kind=XBAR_R, size=size, values=[], stamps=[], keys=[],
                    checks=[], readings=[])
    for one in samples:
        series.values.append(sum(one) / size)
        series.ranges.append(max(one) - min(one))
        series.readings.extend(one)
    return series


def chart(session: Session, material: str, characteristic: str,
          limit: int | None = None) -> dict:
    """A control chart with limits, capability, and what fired.

    Individuals and moving range, or X-bar and R - whichever this
    characteristic's sampling plan asks for. `kind` says which, in a word, so
    nothing downstream has to infer it from which keys are present.
    """
    fewest = min_points(session)
    capable, marginal = cpk_bars(session)
    spec = _spec(session, material, characteristic)
    series = _series(session, spec, limit)
    values = series.values

    base = {
        "material": material,
        "characteristic": characteristic,
        "unit": spec.unit,
        "lower_spec": spec.min_value,
        "upper_spec": spec.max_value,
        # Which chart this is, and the sampling plan behind it. `imr` is one
        # piece at a time; `xbar_r` is a sample of `sample_size` pieces, whose
        # points are the sample means. A reader that does not know the word
        # must say so rather than draw the means as if they were readings.
        "kind": series.kind,
        "sample_size": series.size,
        # How many points the chart draws - readings on an individuals chart,
        # samples on a sampled one. The readings behind them are counted
        # separately, because both figures are true and a reader who is told
        # only one of them will believe the wrong thing about the other.
        "n": len(values),
        "readings": len(series.readings),
        # Each point names what it is, so a reader who wants to know why a
        # point is where it is can ask about THAT reading or THAT sample
        # rather than about the nth dot on a chart that moves every time a
        # check is recorded.
        # `/quality/spc/{material}/{characteristic}/point/{check}` is the
        # answer; `services.spc_point` assembles it.
        "points": [{"value": round(v, 4) if series.sampled else v, "ts": t, "check": c.id,
                    **({"sample": k, "range": round(r, 4)} if series.sampled else {})}
                   for v, t, c, k, r in zip(
                       values, series.stamps, series.checks, series.keys,
                       series.ranges or [0.0] * len(values), strict=False)],
        # Which rules raise a hold on this plant, and every rule number this
        # product has beside them. Stated on every chart, whatever the plant
        # chose, so a rule that fires and opens nothing is explained rather
        # than noticed: a screen that showed a firing with no hold and said
        # nothing about why is the chart lying by omission (decision 0036).
        # Five rules on either kind of chart: 1 to 4 on the points, and rule
        # 5 on the range beside them - the moving range here, the sample range
        # on a sampled chart.
        "rules": sorted(RULE_WINDOW),
        "hold_rules": list(hold_rules(session)),
        "major_rules": list(major_rules(session)),
        # The numbers this plant judges by, beside the figures they judge.
        # The browser used to hold its own copy of the Cpk bar to pick the
        # verdict's colour, so a plant that moved the bar got a green figure
        # under a sentence calling it marginal.
        "min_points": fewest,
        "history": len(values),
        "cpk_capable": capable,
        "cpk_marginal": marginal,
    }
    if series.sampled:
        # The samples themselves, so a reader can see the five readings
        # behind a point without another round trip.
        base["samples"] = series.samples
        if series.set_aside:
            base["set_aside"] = series.set_aside

    # Samples stored under a different plan than the one in force now are not
    # plotted, and the reader is told how many and why rather than left to
    # wonder where they went.
    aside = ("" if not series.set_aside else
             f"; {series.set_aside} stored sample(s) left out because they hold "
             f"a different number of readings than the {series.size} this "
             f"specification now asks for")
    what = "samples" if series.sampled else "readings"

    if len(values) < fewest:
        # Say why rather than draw limits nobody should trust. Control limits
        # from six points move with every reading.
        note = (f"{len(values)} {what}; control limits need at least "
                f"{fewest} to mean anything{aside}")
        return {**base, "control": None, "capability": None, "signals": [],
                "note": note,
                # The moving-range half says the same thing with the same
                # sentence. A reader told why one chart has no limits and left
                # to guess about the other would reasonably assume the second
                # one's limits are real. Only on an individuals chart: a
                # sampled chart's second half is the range chart, and `_limits`
                # is where that one comes from.
                **({"moving_range": _moving_range(series.checks, fewest=fewest, note=note)}
                   if not series.sampled else
                   # And the sampled chart's lower half says it too, in its own
                   # block and the same sentence. The heading, the legend and
                   # the two sentences under a sampled chart follow `kind` with
                   # or without samples - a screen reading an envelope with no
                   # limits in it still has to be told which chart it is about.
                   {"range_chart": _range_block(series, None, [], note)})}

    control = _limits(series)
    if control is None:
        # No variation to judge against. Identical readings are not an
        # in-control process, they are an un-chartable one.
        flat = (f"{len(values)} {what} with no variation between them; "
                f"there is nothing for control limits to be computed from{aside}")
        return {**base, "control": None, "capability": None, "signals": [],
                "note": flat,
                **({"range_chart": _range_block(series, None, [], flat)}
                   if series.sampled else {})}

    centre = control["centre"]
    sigma_within = control["sigma_within"]

    capability = None
    if spec.min_value is not None and spec.max_value is not None and sigma_within > 0:
        cp = (spec.max_value - spec.min_value) / (6 * sigma_within)
        cpu = (spec.max_value - centre) / (3 * sigma_within)
        cpl = (centre - spec.min_value) / (3 * sigma_within)
        # Overall spread, using the actual standard deviation of every
        # individual reading rather than the within-subgroup estimate: Pp/Ppk
        # describe what the customer received, and the customer received
        # pieces, not means.
        readings = series.readings
        overall = math.sqrt(sum((v - centre) ** 2 for v in readings) / (len(readings) - 1))
        capability = {
            "cp": round(cp, 3),
            "cpk": round(min(cpu, cpl), 3),
            "cpu": round(cpu, 3),
            "cpl": round(cpl, 3),
            "pp": round((spec.max_value - spec.min_value) / (6 * overall), 3)
            if overall > 0 else None,
            "sigma_overall": round(overall, 4),
            "sigma": round(sigma_within, 4),
            "readings": len(readings),
        }

    found = _western_electric(values, centre, control["sigma"])
    found += _range_signals(series, control)
    signals = _acted_on(session, spec, found, series.keys, sampled=series.sampled)
    # `stable` and `signals` are the UPPER chart's answer and stay that, which
    # is what every reader of this payload has meant by them since 2026-09-14.
    # On an individuals chart the moving-range half carries its own, in its own
    # block, and the verdict below names it so nobody reading one sentence can
    # miss it.
    stable = not signals
    moving = None
    if not series.sampled:
        moving = _moving_range(series.checks, fewest=fewest, note=None)
        # The same firings, each with the hold it raised when the plant holds
        # on the rule. The verdict is re-said because it counts them.
        moving["signals"] = _mr_acted_on(session, spec, moving["signals"],
                                         [c.id for c in series.checks])
        moving = _said(moving)
    return {
        **base,
        "control": control,
        "capability": capability,
        "signals": signals,
        "stable": stable,
        # The other half of an IMR chart: the gap between each reading and the
        # one before it, the series the mean moving range in `control` is the
        # mean of, with its own centre line, its own upper limit and the one
        # rule it has. A sampled chart's second half is the range chart inside
        # `control`, so this key is absent there rather than null.
        **({"moving_range": moving} if moving is not None else {}),
        # The other half of a sampled chart: the spread inside each sample,
        # with the limits `control.range_chart` holds and a sentence of its
        # own. In the same place in the envelope as `moving_range`, so one
        # screen draws both kinds from one shape (decision 0040).
        **({"range_chart": _range_block(series, control, signals, None)}
           if series.sampled else {}),
        # The sentence that keeps the two questions apart.
        "verdict": _verdict(stable, capability, signals, capable, marginal,
                            moving),
        **({"note": aside[2:]} if aside else {}),
    }


def _range_signals(series: Series, control: dict) -> list[dict]:
    """Each sample whose range is beyond the range chart's upper limit.

    Rule 5 on this kind of chart, and the reason it is a rule at all is
    arithmetic: rules 1 to 4 judge where the mean sat, against limits computed
    from the mean range. A sample whose five readings are spread much wider
    than the rest has made those limits wider too, so the mean it reports can
    look perfectly settled inside limits the sample itself inflated. The
    spread is the finding.

    The same rule number a moving range beyond its limit has on an
    individuals chart: one statement about a process, one number for it. See
    `RANGE_RULE`. Decision 0040.
    """
    if not series.sampled:
        return []
    ceiling = control["range_chart"]["upper"]
    if ceiling <= 0:
        return []
    return [{"rule": RANGE_RULE, "index": i, "value": round(spread, 4),
             "what": SAMPLE_RANGE_WHAT}
            for i, spread in enumerate(series.ranges) if spread > ceiling]


def _acted_on(session: Session, spec: QualitySpec, signals: list[dict],
              ids: list[int], *, sampled: bool = False) -> list[dict]:
    """Each signal, with the hold it raised when it raised one.

    The chart used to say a rule had fired and stop there, which left the
    reader to wonder whether anybody had been told. A signal the MES acted on
    names the non-conformance; one it did not - because the window pre-dates
    this behaviour, or because the same excursion already had a hold open -
    says so by carrying none.
    """
    if not signals:
        return []
    holds_on = hold_rules(session)
    keys = {_window_key(ids, s["index"], s["rule"], sampled=sampled): s["index"]
            for s in signals}
    rows = session.scalars(select(SpcSignal).where(
        SpcSignal.spec_id == spec.id, SpcSignal.window_key.in_(keys))).all()
    holds = {}
    for row in rows:
        nc = session.get(NonConformance, row.nonconformance_id) if row.nonconformance_id else None
        holds[(row.rule, row.window_key)] = nc.code if nc else None
    out = []
    for signal in signals:
        key = _window_key(ids, signal["index"], signal["rule"], sampled=sampled)
        out.append({**signal,
                    "nonconformance": holds.get((signal["rule"], key)),
                    # Whether this plant raises a hold on this rule at all.
                    # A firing with no hold means two different things - the
                    # excursion already had one open, or this plant does not
                    # hold on this rule - and the reader is owed which.
                    "held": _holds(signal["rule"], holds_on)})
    return out


def _holds(rule: int, holds_on: tuple[int, ...]) -> bool:
    """Whether this plant raises a hold on this signal.

    Every rule this product has, rule 5 among them, is on the list the plant
    chooses from. The chart draws and records all five whatever that list says
    - decision 0036 - and the list decides only which of them are worth
    somebody's morning. A range beyond its limit used to hold on every plant,
    whatever the list said; it does not any more, because one rule that
    answered to nobody's configuration was a rule nobody could find when they
    went looking for why their plant held.
    """
    return rule in holds_on


def _mr_acted_on(session: Session, spec: QualitySpec, signals: list[dict],
                 ids: list[int]) -> list[dict]:
    """The moving-range firings, each with the hold it raised when it raised one.

    The same question `_acted_on` answers, and it cannot be the same code: a
    moving-range signal's `index` counts ranges, and range *i* is the gap
    between readings *i* and *i + 1*. So the window - and with it the stable
    key that stops one firing being recorded twice - is those two readings,
    which is one position along from where the individuals rules would look.
    Sharing a function by passing a flag through it is how the two indexings
    would quietly come to be confused.
    """
    if not signals:
        return []
    holds_on = hold_rules(session)
    keys = {_key(ids[s["index"]], ids[s["index"] + 1]) for s in signals}
    holds: dict[str, str | None] = {}
    for row in session.scalars(select(SpcSignal).where(
            SpcSignal.spec_id == spec.id, SpcSignal.rule == RANGE_RULE,
            SpcSignal.window_key.in_(keys))).all():
        nc = session.get(NonConformance, row.nonconformance_id) if row.nonconformance_id else None
        holds[row.window_key] = nc.code if nc else None
    out = []
    for signal in signals:
        earlier, later = ids[signal["index"]], ids[signal["index"] + 1]
        out.append({**signal,
                    # The two readings the range is between, by id, so a
                    # reader can open either of them rather than counting dots.
                    "check": later, "previous_check": earlier,
                    "nonconformance": holds.get(_key(earlier, later)),
                    # Off by default (see `HOLD_RULES`), so on most plants this
                    # is False and the screen says why rather than leaving a
                    # firing that opened nothing to be noticed.
                    "held": RANGE_RULE in holds_on})
    return out


def _mr_verdict(moving: dict) -> str:
    """The moving-range chart's own sentence, in the same shape as the
    individuals chart's. Its own, because the two charts answer two questions
    and one sentence for both is how a reader comes to think a process that
    jumps is a process that is behaving."""
    if moving["n"] == 0:
        # Ahead of the note about limits, because it is the more specific
        # truth: there is no series yet, never mind limits for one.
        return "no moving ranges yet; two readings make the first one"
    if moving.get("note"):
        return moving["note"]
    if moving["signals"]:
        ranges = len(moving["signals"])
        return (f"out of control - rule {RANGE_RULE} fired on "
                f"{ranges} of {moving['n']} ranges. The process jumps further "
                f"between consecutive readings than its own variation "
                f"accounts for, and sigma on both charts is estimated from "
                f"the mean of this series.")
    return (f"in control - every one of {moving['n']} moving ranges is inside "
            f"the upper limit")


def _verdict(stable: bool, capability: dict | None, signals: list[dict],
             capable: float, marginal: float, moving: dict | None = None) -> str:
    # What the moving-range half adds to the sentence. Nothing, when the
    # individuals chart has already said *out of control* - it says it once -
    # and nothing when the moving range is quiet. The one case left is the one
    # the old sentence got wrong: individuals settled, the moving range not,
    # and a verdict that read "in control and capable" about it.
    mr = ""
    if stable and moving and moving.get("signals"):
        mr = (f" The moving range is not: rule {RANGE_RULE} fired on "
              f"{len(moving['signals'])} of {moving['n']} ranges, so the sigma "
              f"these limits come from is itself in question - read the moving "
              f"range first.")
    if not stable:
        rules = sorted({s["rule"] for s in signals})
        said = [f"rule(s) {', '.join(map(str, rules))} fired"]
        if RANGE_RULE in rules:
            # Rule 5 here is a sample range: these are the points chart's
            # signals, and an individuals chart's rule 5 is on the moving
            # range below it and says so in its own sentence. Worth spelling
            # out, because the limits beside it were computed from the mean
            # range this sample has just inflated.
            said.append("one of them is a sample range beyond the range "
                        "chart's limit, so the limits it is judged against "
                        "were widened by the sample itself")
        # The process is out of control whether or not this plant raises a
        # hold on the rule that said so. `hold_rules` decides who is called,
        # never what the chart concluded.
        return (f"out of control - {' and '.join(said)}. "
                f"Capability is not meaningful until this is settled.")
    if capability is None:
        return "in control; capability needs both specification limits to compute" + mr
    cpk = capability["cpk"]
    if cpk >= capable:
        return f"in control and capable (Cpk {cpk})" + mr
    if cpk >= marginal:
        return (f"in control but marginal (Cpk {cpk}) - the process fits, with "
                f"little room for drift") + mr
    return (f"in control but not capable (Cpk {cpk}) - the process is stable "
            f"and stably producing out-of-spec work") + mr


# --------------------------------------------------------------- acting on a signal

#: How many readings back a rule looks. Rule 1 judges one point; the others
#: judge a window ending at that point. The window is what makes a signal
#: identifiable, so it is written down once and used by both the detector and
#: the record.
#:
#: Rule 5's entry is the individuals chart's answer: a moving range is the gap
#: between two readings, so its window is those two. On a sampled chart the
#: same rule judges the spread inside one sample, which is one point - see
#: `_window_span`.
RULE_WINDOW = {1: 1, 2: 3, 3: 5, 4: 8, RANGE_RULE: 2}

#: The pseudo-tag a trigger watches to act on an SPC signal. No PLC publishes
#: it; the MES raises it. See `fsmes.services.triggers.EVENT_TAGS`.
SIGNAL_TAG = "spc.signal"


#: The two severity codes this product's own SPC path writes. They are the
#: codes, not the words: a plant renames them on its severity list and every
#: screen reads its own name for them (`fsmes.services.severities`). Which
#: *rules* earn the major one is `[quality] major_rules`, which is the
#: configurable half - a plant that treats a four-of-five trend as major
#: changes only its own triage queue.
MAJOR = "major"
MINOR = "minor"


def major_rules(session: Session) -> tuple[int, ...]:
    """Which rules open a major non-conformance rather than a minor one."""
    return _rules(session, "major_rules", MAJOR_RULES)


def hold_rules(session: Session) -> tuple[int, ...]:
    """Which rules raise a quality hold on this plant.

    Decision [0036](../../docs/decisions/0036-the-chart-draws-every-rule.md):
    the chart draws every rule whatever this returns - a rule a plant could
    switch off the chart would be a chart that lies, which is what 0035 said
    and still says. What a plant chooses is which of them are worth somebody's
    morning. The four individuals rules are the default and are what this
    product has always done.

    Rule 5 - the moving range - is the one rule that is **off** by default. It
    is drawn and recorded on every plant, like the other four; until a plant
    adds 5 to this list a range beyond its upper limit flags on the chart, is
    there in the record behind the dot, and calls nobody. A plant that has run
    for a year without a moving-range chart would get a morning of holds about
    jumps it already lived through, and would learn to switch the feature off
    rather than to read it. The record it does get starts at its next reading:
    nothing is back-filled.

    Read at the moment it is needed rather than at import, so a number changed
    on Quality's Configuration page is in force on the next chart drawn.
    """
    return _rules(session, "hold_rules", HOLD_RULES)


def _rules(session: Session, name: str, fallback: tuple[int, ...]) -> tuple[int, ...]:
    """One of the two rule lists, narrowed to the rules this product has.

    Parsed rather than trusted, exactly as `Settings._rule_list` was and for
    the same reason: anything that is not one of them is dropped, because the
    alternative is a plant refusing to draw a chart over a typo in a list that
    only ever narrows a set. `fsmes pack check` tells a person about the typo
    offline, the write path on the Configuration page refuses it outright, and
    the chart payload states what was actually parsed.

    `RULE_WINDOW` is the set, rather than a second list of rule numbers kept
    here: the rules this product has are the rules it knows a window for, and
    two lists of them would be two lists to keep in step.
    """
    written = _number(session, name, list(fallback))
    rules = [r for r in written if r in RULE_WINDOW]
    return tuple(sorted(dict.fromkeys(rules)))


def _window_key(ids: list[int], index: int, rule: int, *, sampled: bool = False) -> str:
    """Which readings this firing judged, as a stable string.

    First and last id of the rule's window. Re-running the rules over the
    same stored readings produces the same key, which is what lets the write
    path evaluate on every check without raising the same finding twice.

    On a sampled chart the ids are sample ids, and the key carries an `s` so
    they cannot collide with the check ids of the same characteristic's
    earlier life as an individuals chart. Without it, a plant that moved a
    characteristic from one piece at a time to five would have new findings
    silently swallowed as duplicates of old ones that happened to share a
    number.
    """
    span = _window_span(rule, sampled=sampled)
    first = ids[max(0, index - span + 1)]
    return _key(first, ids[index], sampled=sampled)


def _window_span(rule: int, *, sampled: bool) -> int:
    """How many plotted points a rule judges, on this kind of chart.

    `RULE_WINDOW` is the individuals chart's answer and is right for rules 1
    to 4 on either. Rule 5 is the one rule whose window depends on the chart:
    a moving range is the gap *between two readings*, and a sample range is
    the spread *inside one sample*. The rule means the same thing either way -
    a range beyond its upper limit - but the points it judged are two on one
    chart and one on the other, and the window is what goes into the record
    and into the key. A flat two would record a sampled firing as though it
    had judged the sample before it as well.
    """
    if sampled and rule == RANGE_RULE:
        return 1
    return RULE_WINDOW.get(rule, 1)


def _key(first_id: int, last_id: int, *, sampled: bool = False) -> str:
    """The window key itself, from the two points that bound it. One spelling,
    used by the individuals rules through `_window_key` and by the
    moving-range rule directly - because a key written two ways is a firing
    recorded twice.

    The `s` on a sampled chart's keys is `_window_key`'s doing and is
    explained there: sample ids and check ids are different numbers from
    different tables, and a key that could not tell them apart would swallow a
    new finding as a duplicate of an old one."""
    return f"{'s' if sampled else ''}{first_id}-{last_id}"


def _description(material: str, spec: QualitySpec, signal: dict, control: dict,
                 n: int, *, sampled: bool = False) -> str:
    what = "samples" if sampled else "readings"
    if signal["rule"] == RANGE_RULE:
        # One rule, two ranges. The hold has to name the range the person
        # will go and look at, and the limits of the chart it was drawn on -
        # a hold that quoted the points chart's limits for a rule-5 firing
        # would send them to the wrong line.
        if sampled:
            chart = control["range_chart"]
            return (f"SPC rule {RANGE_RULE} on {material}/{spec.characteristic}: "
                    f"{signal['what']} ({signal['value']}{spec.unit} against mean range "
                    f"{chart['centre']}, upper range limit {chart['upper']} from {n} "
                    f"samples of {control['sample_size']})")
        return (f"SPC rule {RANGE_RULE} on {material}/{spec.characteristic}: {signal['what']} "
                f"({signal['value']}{spec.unit} between two consecutive readings, against a "
                f"mean moving range of {control['centre']}{spec.unit} and an upper limit of "
                f"{control['upper']}{spec.unit} from {n} readings)")
    return (f"SPC rule {signal['rule']} on {material}/{spec.characteristic}: {signal['what']} "
            f"({signal['value']}{spec.unit} against centre {control['centre']}, "
            f"control limits [{control['lower']}, {control['upper']}] from {n} {what})")


def evaluate(session: Session, spec: QualitySpec, *, since_id: int | None = None,
             since_sample: int | None = None, limit: int | None = None,
             actor: str = "spc") -> list[dict]:
    """Run the rules over this characteristic's history and act on what fires.

    Called from the write path, so a rule that trips is acted on whether or
    not anybody has the chart open. What it does is raise a non-conformance -
    a quality hold a person works through, per decision record 0027. It does
    not stop a line, scrap a lot or write to a machine: those stay a person's
    act, or a trigger the plant configured on `spc.signal`.

    `since_id` is the first of the readings just written, `since_sample` the
    sample just recorded. A rule only acts on a window that ends on one of
    them. Without that, one wild reading moves the centre line and every
    settled reading behind it is suddenly eight in a row on one side of it -
    two dozen signals about a fortnight nobody was worried about until a
    moment ago. A rule fires *on a point*, and the point has to be new.

    On a sampled characteristic this runs **once per sample**, on the means,
    and at most one hold comes out of it: five readings posted together are
    one observation of the process, not five.

    Returns one entry per signal it raised, newest last. A signal whose
    window was already recorded returns nothing: the same points are the
    same finding.

    The moving-range rule is recorded here on every plant, like the other
    four. Decision 0036 is read literally: every rule is drawn *and recorded*,
    and the only thing a plant chooses is which of them raises a hold. A dot
    this screen flags pink whose signal is in no record would leave the click
    panel saying nothing fired on a flagged reading, which is a lie about the
    reading. So rule 5 writes its `spc_signal` row everywhere, and raises a
    non-conformance only where `[quality] hold_rules` names it.

    Nothing is back-filled. `since_id` is the same gate it is for rules 1-4: a
    firing acts only on a window ending on a reading just written, so a plant
    that upgrades starts recording moving-range signals from its next reading
    and its history stays as it was judged at the time.
    """
    from fsmes.services import quality

    series = _series(session, spec, limit)
    if len(series.values) < min_points(session):
        return []

    control = _limits(series)
    if control is None:
        # No variation to judge against. A chart of identical readings is not
        # in control, it is un-chartable, and saying so beats dividing by zero.
        return []

    signals = _western_electric(series.values, control["centre"], control["sigma"])
    signals += _range_signals(series, control)

    # The moving-range half's own rule, on an individuals chart only: a
    # sampled chart's second half is the chart of sample ranges, and
    # `_range_signals` above is where that one is judged.
    mr_signals: list[dict] = []
    mr_control: dict = {}
    if not series.sampled:
        ranges = _moving_ranges(series.values)
        mean_range = sum(ranges) / len(ranges) if ranges else 0.0
        mr_control = {"centre": round(mean_range, 4),
                      "upper": round(D4_N2 * mean_range, 4),
                      "lower": round(D3_N2 * mean_range, 4)}
        # Recorded on every plant, held only where the plant says so - the
        # same deal the other four rules get. See the docstring.
        mr_signals = _moving_range_rule(ranges, D4_N2 * mean_range)

    if not signals and not mr_signals:
        return []

    # Only the firings on a point just written; the rest are about points
    # that were judged when they arrived.
    since = since_sample if series.sampled else since_id
    if series.sampled and since is None and since_id is not None:
        # Handed a check id for a sampled characteristic - the bulk import
        # path does that. Translate it into the sample it belongs to rather
        # than judge the whole history again.
        since = session.scalar(
            select(QualityCheck.sample_id)
            .where(QualityCheck.spec_id == spec.id, QualityCheck.id >= since_id,
                   QualityCheck.sample_id.is_not(None))
            .order_by(QualityCheck.id).limit(1))
        if since is None:
            return []
    if since is not None:
        signals = [s for s in signals if series.keys[s["index"]] >= since]
        # For a moving range the point that has to be new is the *later* of
        # the two readings: that is the one that made the range exist.
        mr_signals = [s for s in mr_signals if series.keys[s["index"] + 1] >= since]
        if not signals and not mr_signals:
            return []

    material = spec.material.code
    sampled = series.sampled
    n = len(series.values)

    # One firing, the key it is recorded under, the point it is *on*, the
    # window behind it and the limits it was judged against - gathered here so
    # the loop below does not have to know that a moving-range index counts
    # ranges and an individuals index counts points.
    work: list[tuple[dict, str, QualityCheck, dict, dict]] = []
    for signal in signals:
        index = signal["index"]
        span = _window_span(signal["rule"], sampled=sampled)
        first = max(0, index - span + 1)
        work.append((signal,
                     _window_key(series.keys, index, signal["rule"], sampled=sampled),
                     series.checks[index],
                     _window_record(series, control, first, index),
                     control))
    for signal in mr_signals:
        index = signal["index"]
        work.append((signal,
                     _key(series.keys[index], series.keys[index + 1]),
                     series.checks[index + 1],
                     _window_record(series, mr_control, index, index + 1),
                     mr_control))
    # Newest last, and within one reading the lower rule number first, so the
    # order a caller sees does not depend on which list a firing came from.
    work.sort(key=lambda item: (item[2].id, item[0]["rule"]))
    keys = {key for _, key, _, _, _ in work}
    already = {
        (row.rule, row.window_key) for row in session.scalars(
            select(SpcSignal).where(SpcSignal.spec_id == spec.id,
                                    SpcSignal.window_key.in_(keys)))
    }

    majors = major_rules(session)
    holds_on = hold_rules(session)
    raised: list[dict] = []
    for signal, key, check, window, limits in work:
        if (signal["rule"], key) in already:
            continue
        already.add((signal["rule"], key))
        row = SpcSignal(
            spec_id=spec.id, rule=signal["rule"], window_key=key, check_id=check.id,
            value=signal["value"], what=signal["what"], window=window)
        session.add(row)

        # The signal row is written whatever the plant holds on: the chart
        # draws every rule, and a firing this plant does not act on is still a
        # firing it will want to see. Decision 0036.
        if not _holds(signal["rule"], holds_on):
            session.flush()
            raised.append({"rule": signal["rule"], "what": signal["what"],
                           "value": signal["value"], "material": material,
                           "characteristic": spec.characteristic,
                           # Not a hold this plant has yet to open: a hold it
                           # does not open. Null would read as the first.
                           "nonconformance": None, "held": False,
                           "equipment": _equipment_code(session, check),
                           "window_key": key})
            continue

        nc = _hold_for(session, spec)
        if nc is None:
            # Rule 5's severity is on the plant's own `major_rules` list,
            # the same as the other four. It used to borrow rule 1's, which
            # meant a plant that wrote a major-rules list down still could
            # not say what a range beyond its limit was worth.
            nc = quality.open_nc(
                session,
                description=_description(material, spec, signal, limits, n,
                                        sampled=sampled),
                severity=MAJOR if signal["rule"] in majors else MINOR,
                work_order_code=_order_code(session, check),
                actor=actor,
                evidence={
                    "source": "spc", "rule": signal["rule"], "what": signal["what"],
                    "material": material, "characteristic": spec.characteristic,
                    "unit": spec.unit, "value": signal["value"],
                    "equipment": _equipment_code(session, check),
                    "window": row.window,
                    **(_sample_evidence(series, signal["index"])
                       if sampled else {}),
                },
            )
        session.flush()
        row.nonconformance_id = nc.id
        raised.append({"rule": signal["rule"], "what": signal["what"], "value": signal["value"],
                       "material": material, "characteristic": spec.characteristic,
                       "nonconformance": nc.code, "held": True,
                       "equipment": _equipment_code(session, check),
                       "window_key": key})
    return raised


def _sample_evidence(series: Series, index: int) -> dict:
    """The sample a rule fired on, as it goes into the hold's evidence.

    Plain values only - the stamp as a string, the readings as numbers -
    because evidence is stored as JSON and read years later by people and by
    screens, not by this module. A person asked to work a hold on a mean has
    to be able to see the pieces behind it, so the five readings and the five
    rows they are stored in are named here rather than left to be looked up.
    """
    sample = series.samples[index]
    return {
        "sample_size": series.size,
        "sample": sample["sample"],
        "sample_at": sample["ts"].isoformat(),
        "mean": sample["mean"],
        "range": sample["range"],
        "readings": [r["value"] for r in sample["readings"]],
        "checks": [r["check"] for r in sample["readings"]],
    }


def _window_record(series: Series, control: dict, first: int, last: int) -> dict:
    """The points a firing judged, as they go into the signal row.

    The control limits in force, and then the window itself: the readings on
    an individuals chart, the samples on a sampled one - each with its mean,
    its range and the ids of the readings it was computed from, so the record
    is checkable years later without re-deriving anything.
    """
    out = {k: v for k, v in control.items() if k != "constants"}
    out["n"] = len(series.values)
    if series.sampled:
        out["sample_size"] = series.size
        out["samples"] = [{**s, "ts": s["ts"].isoformat()}
                          for s in series.samples[first:last + 1]]
        return out
    out["points"] = [{"value": c.value, "ts": c.ts.isoformat(), "check": c.id}
                     for c in series.checks[first:last + 1]]
    return out


def _hold_for(session: Session, spec: QualitySpec) -> NonConformance | None:
    """The hold this characteristic already has open, if any.

    An excursion that lasts twenty readings is one thing that went wrong, not
    twenty, and a process going out of control trips several of the four rules
    on the way. While the non-conformance is still unresolved, a further
    signal joins it - the signal rows carry every rule that fired - rather
    than opening another. Once somebody has dispositioned it, the next signal
    is a new finding.
    """
    previous = session.scalars(
        select(SpcSignal).where(SpcSignal.spec_id == spec.id,
                                SpcSignal.nonconformance_id.is_not(None))
        .order_by(SpcSignal.id.desc()).limit(1)).first()
    if previous is None:
        return None
    nc = session.get(NonConformance, previous.nonconformance_id)
    if nc is None or nc.status in (NcStatus.DISPOSITIONED, NcStatus.CLOSED):
        return None
    return nc


def _order_code(session: Session, check: QualityCheck) -> str | None:
    if not check.work_order_id:
        return None
    return session.scalar(select(WorkOrder.code).where(WorkOrder.id == check.work_order_id))


def _equipment_code(session: Session, check: QualityCheck) -> str | None:
    """The station that took the reading, or None. Never derived: a station
    this MES did not record is unknown, and unknown is not a guess."""
    if not check.equipment_id:
        return None
    return session.scalar(select(Equipment.code).where(Equipment.id == check.equipment_id))
