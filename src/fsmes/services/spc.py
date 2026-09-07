"""Statistical process control: is the process stable, and is it capable?

Two different questions, and confusing them is the classic mistake.

*Stable* asks whether the process is behaving like itself - judged against
control limits computed from its own variation, not from the tolerance. A
process can sit comfortably inside spec while drifting badly, and a chart
drawn against the specification will never show it.

*Capable* asks whether that variation fits inside what the customer asked for.
Cpk without stability is meaningless: a capability number computed on an
out-of-control process describes a process that no longer exists.

Individuals and moving range, not X-bar and R, because a plant that inspects
one bottle at a time has no rational subgroups and pretending otherwise
produces control limits that are simply wrong.
"""

from __future__ import annotations

import itertools
import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain import Material, QualityCheck, QualitySpec
from fsmes.services import NotFound

# For an individuals chart the estimate of sigma is mean moving range over
# d2 for n=2. 1.128 is the standard constant; it is written out rather than
# imported so nobody has to go looking for what it means.
D2_N2 = 1.128
# Fewest points worth computing limits from. Below this the limits move so
# much with each new reading that they mislead more than they inform.
MIN_POINTS = 12


def _values(session: Session, material: str, characteristic: str,
            limit: int = 200) -> tuple[QualitySpec, list[QualityCheck]]:
    mat = session.scalar(select(Material).where(Material.code == material))
    if mat is None:
        raise NotFound(f"no material {material}")
    spec = session.scalar(select(QualitySpec).where(
        QualitySpec.material_id == mat.id,
        QualitySpec.characteristic == characteristic))
    if spec is None:
        raise NotFound(f"no specification for {material}/{characteristic}")

    checks = list(session.scalars(
        select(QualityCheck).where(QualityCheck.spec_id == spec.id)
        .order_by(QualityCheck.id.desc()).limit(limit)))
    checks.reverse()
    return spec, checks


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


def capability(values: list[float], lower_spec: float | None, upper_spec: float | None) -> dict | None:
    """Cp, Cpk, Pp for a series against its specification, by the same
    arithmetic the SPC chart uses - sigma from the mean moving range, the
    overall spread from the sample standard deviation. None when there are
    too few readings, no limits, or no variation to divide by; the caller
    says why rather than printing a number nobody should trust."""
    if len(values) < MIN_POINTS or lower_spec is None or upper_spec is None:
        return None
    centre = sum(values) / len(values)
    moving = [abs(b - a) for a, b in itertools.pairwise(values)]
    sigma = (sum(moving) / len(moving) / D2_N2) if moving else 0.0
    if sigma <= 0:
        return None
    cpu = (upper_spec - centre) / (3 * sigma)
    cpl = (centre - lower_spec) / (3 * sigma)
    overall = math.sqrt(sum((v - centre) ** 2 for v in values) / (len(values) - 1))
    return {
        "n": len(values), "centre": round(centre, 4), "sigma": round(sigma, 4),
        "cp": round((upper_spec - lower_spec) / (6 * sigma), 3), "cpk": round(min(cpu, cpl), 3),
        "cpu": round(cpu, 3), "cpl": round(cpl, 3),
        "pp": round((upper_spec - lower_spec) / (6 * overall), 3) if overall > 0 else None,
        "ppk": round(min((upper_spec - centre) / (3 * overall), (centre - lower_spec) / (3 * overall)), 3)
        if overall > 0 else None,
        "sigma_overall": round(overall, 4),
        "stable": not _western_electric(values, centre, sigma),
    }


def chart(session: Session, material: str, characteristic: str,
          limit: int = 200) -> dict:
    """An individuals chart with control limits, capability, and what fired."""
    spec, checks = _values(session, material, characteristic, limit)
    values = [c.value for c in checks]
    stamps = [c.ts for c in checks]

    base = {
        "material": material,
        "characteristic": characteristic,
        "unit": spec.unit,
        "lower_spec": spec.min_value,
        "upper_spec": spec.max_value,
        "n": len(values),
        "points": [{"value": v, "ts": t} for v, t in zip(values, stamps, strict=False)],
    }

    if len(values) < MIN_POINTS:
        # Say why rather than draw limits nobody should trust. Control limits
        # from six points move with every reading.
        return {**base, "control": None, "capability": None, "signals": [],
                "note": f"{len(values)} readings; control limits need at least "
                        f"{MIN_POINTS} to mean anything"}

    centre = sum(values) / len(values)
    moving = [abs(b - a) for a, b in itertools.pairwise(values)]
    mean_range = sum(moving) / len(moving) if moving else 0.0
    sigma = mean_range / D2_N2

    control = {
        "centre": round(centre, 4),
        "sigma": round(sigma, 4),
        "upper": round(centre + 3 * sigma, 4),
        "lower": round(centre - 3 * sigma, 4),
        "mean_moving_range": round(mean_range, 4),
    }

    capability = None
    if spec.min_value is not None and spec.max_value is not None and sigma > 0:
        cp = (spec.max_value - spec.min_value) / (6 * sigma)
        cpu = (spec.max_value - centre) / (3 * sigma)
        cpl = (centre - spec.min_value) / (3 * sigma)
        # Overall spread, using the actual standard deviation rather than the
        # within-subgroup estimate: Pp/Ppk describe what the customer received.
        overall = math.sqrt(sum((v - centre) ** 2 for v in values) / (len(values) - 1))
        capability = {
            "cp": round(cp, 3),
            "cpk": round(min(cpu, cpl), 3),
            "cpu": round(cpu, 3),
            "cpl": round(cpl, 3),
            "pp": round((spec.max_value - spec.min_value) / (6 * overall), 3)
            if overall > 0 else None,
            "sigma_overall": round(overall, 4),
        }

    signals = _western_electric(values, centre, sigma)
    stable = not signals
    return {
        **base,
        "control": control,
        "capability": capability,
        "signals": signals,
        "stable": stable,
        # The sentence that keeps the two questions apart.
        "verdict": _verdict(stable, capability, signals),
    }


def _verdict(stable: bool, capability: dict | None, signals: list[dict]) -> str:
    if not stable:
        rules = sorted({s["rule"] for s in signals})
        return (f"out of control - rule(s) {', '.join(map(str, rules))} fired. "
                f"Capability is not meaningful until this is settled.")
    if capability is None:
        return "in control; capability needs both specification limits to compute"
    cpk = capability["cpk"]
    if cpk >= 1.33:
        return f"in control and capable (Cpk {cpk})"
    if cpk >= 1.0:
        return (f"in control but marginal (Cpk {cpk}) - the process fits, with "
                f"little room for drift")
    return (f"in control but not capable (Cpk {cpk}) - the process is stable "
            f"and stably producing out-of-spec work")
