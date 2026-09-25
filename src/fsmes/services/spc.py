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

from fsmes.domain import (
    Equipment,
    Material,
    NcStatus,
    NonConformance,
    QualityCheck,
    QualitySpec,
    SpcSignal,
    WorkOrder,
)
from fsmes.services import NotFound

# For an individuals chart the estimate of sigma is mean moving range over
# d2 for n=2. 1.128 is the standard constant; it is written out rather than
# imported so nobody has to go looking for what it means.
D2_N2 = 1.128
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
# Which of the four rules raise a hold, and which of those are a major finding.
# `[quality] hold_rules` and `major_rules`; all four and rule 1 alone, which is
# what this product has always done.
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


def _values(session: Session, material: str, characteristic: str,
            limit: int | None = None) -> tuple[QualitySpec, list[QualityCheck]]:
    limit = history(session) if limit is None else limit
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


def capability(values: list[float], lower_spec: float | None,
               upper_spec: float | None, *, fewest: int) -> dict | None:
    """Cp, Cpk, Pp for a series against its specification, by the same
    arithmetic the SPC chart uses - sigma from the mean moving range, the
    overall spread from the sample standard deviation. None when there are
    too few readings, no limits, or no variation to divide by; the caller
    says why rather than printing a number nobody should trust."""
    if len(values) < fewest or lower_spec is None or upper_spec is None:
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
          limit: int | None = None) -> dict:
    """An individuals chart with control limits, capability, and what fired."""
    fewest = min_points(session)
    capable, marginal = cpk_bars(session)
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
        # Which of the four rules raise a hold on this plant, and all four
        # rule numbers beside them. Stated on every chart, whatever the plant
        # chose, so a rule that fires and opens nothing is explained rather
        # than noticed: a screen that showed a firing with no hold and said
        # nothing about why is the chart lying by omission (decision 0036).
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

    if len(values) < fewest:
        # Say why rather than draw limits nobody should trust. Control limits
        # from six points move with every reading.
        return {**base, "control": None, "capability": None, "signals": [],
                "note": f"{len(values)} readings; control limits need at least "
                        f"{fewest} to mean anything"}

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

    signals = _acted_on(session, spec, _western_electric(values, centre, sigma),
                        [c.id for c in checks])
    stable = not signals
    return {
        **base,
        "control": control,
        "capability": capability,
        "signals": signals,
        "stable": stable,
        # The sentence that keeps the two questions apart.
        "verdict": _verdict(stable, capability, signals, capable, marginal),
    }


def _acted_on(session: Session, spec: QualitySpec, signals: list[dict], ids: list[int]) -> list[dict]:
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
    keys = {_window_key(ids, s["index"], s["rule"]): s["index"] for s in signals}
    rows = session.scalars(select(SpcSignal).where(
        SpcSignal.spec_id == spec.id, SpcSignal.window_key.in_(keys))).all()
    holds = {}
    for row in rows:
        nc = session.get(NonConformance, row.nonconformance_id) if row.nonconformance_id else None
        holds[(row.rule, row.window_key)] = nc.code if nc else None
    out = []
    for signal in signals:
        key = _window_key(ids, signal["index"], signal["rule"])
        out.append({**signal,
                    "nonconformance": holds.get((signal["rule"], key)),
                    # Whether this plant raises a hold on this rule at all.
                    # A firing with no hold means two different things - the
                    # excursion already had one open, or this plant does not
                    # hold on this rule - and the reader is owed which.
                    "held": signal["rule"] in holds_on})
    return out


def _verdict(stable: bool, capability: dict | None, signals: list[dict],
             capable: float, marginal: float) -> str:
    if not stable:
        rules = sorted({s["rule"] for s in signals})
        # The process is out of control whether or not this plant raises a
        # hold on the rule that said so. `hold_rules` decides who is called,
        # never what the chart concluded.
        return (f"out of control - rule(s) {', '.join(map(str, rules))} fired. "
                f"Capability is not meaningful until this is settled.")
    if capability is None:
        return "in control; capability needs both specification limits to compute"
    cpk = capability["cpk"]
    if cpk >= capable:
        return f"in control and capable (Cpk {cpk})"
    if cpk >= marginal:
        return (f"in control but marginal (Cpk {cpk}) - the process fits, with "
                f"little room for drift")
    return (f"in control but not capable (Cpk {cpk}) - the process is stable "
            f"and stably producing out-of-spec work")


# --------------------------------------------------------------- acting on a signal

#: How many readings back a rule looks. Rule 1 judges one point; the others
#: judge a window ending at that point. The window is what makes a signal
#: identifiable, so it is written down once and used by both the detector and
#: the record.
RULE_WINDOW = {1: 1, 2: 3, 3: 5, 4: 8}

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
    """Which of the four rules raise a quality hold on this plant.

    Decision [0036](../../docs/decisions/0036-the-chart-draws-every-rule.md):
    the chart draws and records all four whatever this returns - a rule a
    plant could switch off the chart would be a chart that lies, which is what
    0035 said and still says. What a plant chooses is which of them are worth
    somebody's morning. All four is the default and is what this product has
    always done.

    Read at the moment it is needed rather than at import, so a number changed
    on Quality's Configuration page is in force on the next chart drawn.
    """
    return _rules(session, "hold_rules", HOLD_RULES)


def _rules(session: Session, name: str, fallback: tuple[int, ...]) -> tuple[int, ...]:
    """One of the two rule lists, narrowed to the four rules this product has.

    Parsed rather than trusted, exactly as `Settings._rule_list` was and for
    the same reason: anything that is not one of the four is dropped, because
    the alternative is a plant refusing to draw a chart over a typo in a list
    that only ever narrows a set. `fsmes pack check` tells a person about the
    typo offline, the write path on the Configuration page refuses it outright,
    and the chart payload states what was actually parsed.
    """
    written = _number(session, name, list(fallback))
    rules = [r for r in written if r in (1, 2, 3, 4)]
    return tuple(sorted(dict.fromkeys(rules)))


def _window_key(ids: list[int], index: int, rule: int) -> str:
    """Which readings this firing judged, as a stable string.

    First and last check id of the rule's window. Re-running the rules over
    the same stored readings produces the same key, which is what lets the
    write path evaluate on every check without raising the same finding
    twice.
    """
    span = RULE_WINDOW.get(rule, 1)
    first = ids[max(0, index - span + 1)]
    return f"{first}-{ids[index]}"


def _description(material: str, spec: QualitySpec, signal: dict, control: dict, n: int) -> str:
    return (f"SPC rule {signal['rule']} on {material}/{spec.characteristic}: {signal['what']} "
            f"({signal['value']}{spec.unit} against centre {control['centre']}, "
            f"control limits [{control['lower']}, {control['upper']}] from {n} readings)")


def evaluate(session: Session, spec: QualitySpec, *, since_id: int | None = None,
             limit: int | None = None, actor: str = "spc") -> list[dict]:
    """Run the rules over this characteristic's readings and act on what fires.

    Called from the write path, so a rule that trips is acted on whether or
    not anybody has the chart open. What it does is raise a non-conformance -
    a quality hold a person works through, per decision record 0027. It does
    not stop a line, scrap a lot or write to a machine: those stay a person's
    act, or a trigger the plant configured on `spc.signal`.

    `since_id` is the first of the readings just written. A rule only acts on
    a window that ends on one of them. Without that, one wild reading moves
    the centre line and every settled reading behind it is suddenly eight in
    a row on one side of it - two dozen signals about a fortnight nobody was
    worried about until a moment ago. A rule fires *on a reading*, and the
    reading has to be new.

    Returns one entry per signal it raised, newest last. A signal whose
    window was already recorded returns nothing: the same readings are the
    same finding.
    """
    from fsmes.services import quality

    checks = list(session.scalars(
        select(QualityCheck).where(QualityCheck.spec_id == spec.id)
        .order_by(QualityCheck.id.desc())
        .limit(history(session) if limit is None else limit)))
    checks.reverse()
    if len(checks) < min_points(session):
        return []

    values = [c.value for c in checks]
    ids = [c.id for c in checks]
    centre = sum(values) / len(values)
    moving = [abs(b - a) for a, b in itertools.pairwise(values)]
    mean_range = sum(moving) / len(moving) if moving else 0.0
    sigma = mean_range / D2_N2
    if sigma <= 0:
        # No variation to judge against. A chart of identical readings is not
        # in control, it is un-chartable, and saying so beats dividing by zero.
        return []

    control = {"centre": round(centre, 4), "sigma": round(sigma, 4),
               "upper": round(centre + 3 * sigma, 4), "lower": round(centre - 3 * sigma, 4)}
    signals = _western_electric(values, centre, sigma)
    if not signals:
        return []

    # Only the firings on a reading just written; the rest are about readings
    # that were judged when they arrived.
    if since_id is not None:
        signals = [s for s in signals if ids[s["index"]] >= since_id]
        if not signals:
            return []

    material = spec.material.code
    keys = {_window_key(ids, s["index"], s["rule"]) for s in signals}
    already = {
        (row.rule, row.window_key) for row in session.scalars(
            select(SpcSignal).where(SpcSignal.spec_id == spec.id,
                                    SpcSignal.window_key.in_(keys)))
    }

    holds_on = hold_rules(session)
    majors = major_rules(session)
    raised: list[dict] = []
    for signal in signals:
        key = _window_key(ids, signal["index"], signal["rule"])
        if (signal["rule"], key) in already:
            continue
        already.add((signal["rule"], key))
        check = checks[signal["index"]]
        span = RULE_WINDOW.get(signal["rule"], 1)
        window = checks[max(0, signal["index"] - span + 1):signal["index"] + 1]
        row = SpcSignal(
            spec_id=spec.id, rule=signal["rule"], window_key=key, check_id=check.id,
            value=signal["value"], what=signal["what"],
            window={**control, "n": len(values),
                    "points": [{"value": c.value, "ts": c.ts.isoformat(), "check": c.id} for c in window]})
        session.add(row)

        # The signal row is written whatever the plant holds on: the chart
        # draws every rule, and a firing this plant does not act on is still a
        # firing it will want to see. Decision 0036.
        if signal["rule"] not in holds_on:
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
            nc = quality.open_nc(
                session,
                description=_description(material, spec, signal, control, len(values)),
                severity=MAJOR if signal["rule"] in majors else MINOR,
                work_order_code=_order_code(session, check),
                actor=actor,
                evidence={
                    "source": "spc", "rule": signal["rule"], "what": signal["what"],
                    "material": material, "characteristic": spec.characteristic,
                    "unit": spec.unit, "value": signal["value"],
                    "equipment": _equipment_code(session, check),
                    "window": row.window,
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
