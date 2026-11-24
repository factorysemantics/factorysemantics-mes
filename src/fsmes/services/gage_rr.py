"""Gauge studies (gage R&R): how much of what a gauge reports is the gauge.

A width that will not hold is one of two problems, and they have opposite
cures. Either the parts really do vary - the process - or the parts are fine
and the number moves because the gauge, or the people reading it, do. A plant
that adjusts the machine for a gauge problem makes the parts worse; a plant
that buys a new gauge for a process problem wastes the money and keeps the
scrap. The study tells them apart.

The study is crossed: every appraiser measures every part, `trials` times
each, in an order they do not see. The arithmetic is the two-way ANOVA
method: the spread is split into repeatability (the same person, the same
part, a different number - the gauge), reproducibility (different people,
the same part - the people, and the interaction of person and part when it
is significant), and part-to-part (the parts themselves). The proof the
arithmetic is right is `tests/test_gage_rr_worked_example.py`, which gives it
the published 10 parts x 3 appraisers x 3 trials data set and checks every
printed decimal.

The verdict bands - under 10 % acceptable, 10 to 30 % marginal, over 30 %
not acceptable, and at least five distinct categories - are the industry's
convention (AIAG's Measurement Systems Analysis manual is where most plants
read them), not a law of nature, and every answer says so. And the study
refuses a verdict it cannot stand behind - readings missing, too few parts,
parts that do not span the tolerance, a gauge out of calibration - and says
which, the way the control chart withholds capability.
"""

from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import GaugeStudy, Material, QualitySpec, StudyReading, StudyState
from fsmes.services import Conflict, Invalid, NotFound, audit
from fsmes.services import gauges as gauge_service

#: Below this many parts the study is computed but not judged. AIAG's
#: worked example uses ten; five is the fewest at which part-to-part
#: variation is an estimate rather than an anecdote.
MIN_PARTS = 5
#: A study needs at least two of each to separate anything at all: one
#: appraiser has no reproducibility, one trial no repeatability. Refused at
#: planning time rather than discovered after the readings.
MIN_APPRAISERS = 2
MIN_TRIALS = 2
#: The interaction of appraiser and part is kept in the model only when it
#: is significant at this level, and pooled into repeatability otherwise -
#: the convention of the common statistics packages. Named so a reader sees
#: the premise beside the answer.
INTERACTION_ALPHA = 0.05
#: The bands, as fractions of 100. Convention, not law.
ACCEPTABLE = 10.0
NOT_ACCEPTABLE = 30.0
MIN_CATEGORIES = 5
#: The parts must span at least this share of the tolerance (the range of
#: their averages). Parts picked from one good hour all look alike, and
#: then %study variation charges the gauge for a spread the parts never
#: had the chance to show.
SPAN_SHARE = 0.5
#: Study variation is six standard deviations, the width % of tolerance is
#: compared against.
SPREAD = 6.0

CONVENTION = ("bands are the industry's convention (AIAG MSA): under 10 % "
              "acceptable, 10-30 % may be acceptable depending on the use, "
              "over 30 % not acceptable; at least 5 distinct categories")

CAPABILITY_WITHHELD = ("Cp and Cpk are withheld while the control chart is out of "
                       "control, as the SPC screen withholds them: a capability figure "
                       "describes a process that no longer exists. Do not quote, "
                       "estimate or work one out; say it is withheld until the chart "
                       "is back in control.")


# --------------------------------------------------------------------------
# The arithmetic. Pure: numbers in, numbers out, nothing read or written.


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """The regularised incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def f_upper_tail(f: float, df1: int, df2: int) -> float:
    """P(F > f) for an F distribution with (df1, df2) degrees of freedom."""
    if f <= 0:
        return 1.0
    return _betai(df2 / 2.0, df1 / 2.0, df2 / (df2 + df1 * f))


def anova(values: dict[tuple[int, str, int], float], parts: int,
          appraisers: list[str], trials: int) -> dict:
    """The crossed gauge study by the ANOVA method, from a full grid.

    `values` maps (part, appraiser, trial) to the reading, parts and trials
    counted from 1. Every cell must be there: a balanced design is what the
    method assumes, and the caller withholds rather than calls this short.
    """
    p, a, r = parts, len(appraisers), trials
    n = p * a * r
    cells = {(i, j): [values[(i, j, k)] for k in range(1, r + 1)]
             for i in range(1, p + 1) for j in appraisers}
    grand = sum(values.values()) / n
    part_mean = {i: sum(sum(cells[(i, j)]) for j in appraisers) / (a * r)
                 for i in range(1, p + 1)}
    appr_mean = {j: sum(sum(cells[(i, j)]) for i in range(1, p + 1)) / (p * r)
                 for j in appraisers}
    cell_mean = {key: sum(v) / r for key, v in cells.items()}

    ss_part = a * r * sum((m - grand) ** 2 for m in part_mean.values())
    ss_appr = p * r * sum((m - grand) ** 2 for m in appr_mean.values())
    ss_int = r * sum((cell_mean[(i, j)] - part_mean[i] - appr_mean[j] + grand) ** 2
                     for i in range(1, p + 1) for j in appraisers)
    ss_total = sum((v - grand) ** 2 for v in values.values())
    ss_err = sum((v - cell_mean[(i, j)]) ** 2
                 for (i, j, _k), v in values.items())

    df_part, df_appr = p - 1, a - 1
    df_int, df_err = df_part * df_appr, p * a * (r - 1)
    ms_part, ms_appr = ss_part / df_part, ss_appr / df_appr
    ms_int, ms_err = ss_int / df_int, ss_err / df_err

    def _f(num: float, den: float) -> float | None:
        return num / den if den > 0 else None

    f_int = _f(ms_int, ms_err)
    p_int = f_upper_tail(f_int, df_int, df_err) if f_int is not None else None
    with_interaction = p_int is not None and p_int < INTERACTION_ALPHA
    table = [
        {"source": "part", "df": df_part, "ss": ss_part, "ms": ms_part,
         "f": _f(ms_part, ms_int)},
        {"source": "appraiser", "df": df_appr, "ss": ss_appr, "ms": ms_appr,
         "f": _f(ms_appr, ms_int)},
        {"source": "appraiser_x_part", "df": df_int, "ss": ss_int, "ms": ms_int,
         "f": f_int, "p": p_int},
        {"source": "repeatability", "df": df_err, "ss": ss_err, "ms": ms_err},
        {"source": "total", "df": n - 1, "ss": ss_total},
    ]

    if with_interaction:
        var_repeat = ms_err
        var_int = max(0.0, (ms_int - ms_err) / r)
        var_appr = max(0.0, (ms_appr - ms_int) / (p * r))
        var_part = max(0.0, (ms_part - ms_int) / (a * r))
    else:
        pooled = (ss_int + ss_err) / (df_int + df_err)
        var_repeat = pooled
        var_int = 0.0
        var_appr = max(0.0, (ms_appr - pooled) / (p * r))
        var_part = max(0.0, (ms_part - pooled) / (a * r))
    var_repro = var_appr + var_int
    var_grr = var_repeat + var_repro
    var_total = var_grr + var_part

    return {
        "table": table,
        "interaction_significant": with_interaction,
        "interaction_p": p_int,
        "variance": {"repeatability": var_repeat, "reproducibility": var_repro,
                     "appraiser": var_appr, "interaction": var_int,
                     "gauge_rr": var_grr, "part": var_part, "total": var_total},
        "grand_mean": grand,
        "part_means": [part_mean[i] for i in range(1, p + 1)],
        "appraiser_means": {j: appr_mean[j] for j in appraisers},
    }


def judge(arith: dict, tolerance: float | None) -> dict:
    """The percentages, distinct categories and the band, from `anova`."""
    var = arith["variance"]
    total_sd = math.sqrt(var["total"])
    rows = []
    for source in ("repeatability", "reproducibility", "appraiser", "interaction",
                   "gauge_rr", "part", "total"):
        sd = math.sqrt(var[source])
        rows.append({
            "source": source,
            "variance": var[source],
            "sd": sd,
            "study_var": SPREAD * sd,
            "pct_contribution": 100.0 * var[source] / var["total"] if var["total"] else None,
            "pct_study_var": 100.0 * sd / total_sd if total_sd else None,
            "pct_tolerance": (100.0 * SPREAD * sd / tolerance) if tolerance else None,
        })
    grr_sd = math.sqrt(var["gauge_rr"])
    ndc = (math.floor(1.41 * math.sqrt(var["part"]) / grr_sd) if grr_sd > 0 else None)
    by = {row["source"]: row for row in rows}
    grr = by["gauge_rr"]
    pct = max(x for x in (grr["pct_study_var"], grr["pct_tolerance"]) if x is not None) \
        if grr["pct_study_var"] is not None else None
    band = (None if pct is None else "acceptable" if pct < ACCEPTABLE
            else "marginal" if pct <= NOT_ACCEPTABLE else "not acceptable")
    return {"components": rows, "distinct_categories": ndc, "gauge_rr_pct": pct,
            "band": band}


# --------------------------------------------------------------------------
# The record.


def _material(session: Session, code: str) -> Material:
    mat = session.scalar(select(Material).where(Material.code == code))
    if mat is None:
        raise NotFound(f"no material {code}")
    return mat


def get(session: Session, code: str) -> GaugeStudy:
    study = session.scalar(select(GaugeStudy).where(GaugeStudy.code == code))
    if study is None:
        raise NotFound(f"no gauge study {code}")
    return study


def _next_code(session: Session) -> str:
    count = session.scalar(select(func.count()).select_from(GaugeStudy)) or 0
    while True:
        count += 1
        code = f"GS-{count:04d}"
        if not session.scalar(select(GaugeStudy.id).where(GaugeStudy.code == code)):
            return code


def plan(session: Session, *, gauge: str, material: str, characteristic: str,
         parts: int = 10, appraisers: list[str], trials: int = 3,
         purpose: str | None = None, code: str | None = None,
         actor: str = "system") -> dict:
    """Plan a crossed study. The tolerance is copied from the specification."""
    instrument = gauge_service.get(session, gauge)
    mat = _material(session, material)
    spec = session.scalar(select(QualitySpec).where(
        QualitySpec.material_id == mat.id, QualitySpec.characteristic == characteristic))
    if spec is None:
        raise NotFound(f"no specification for {material}/{characteristic}")
    if spec.counted:
        raise Invalid(f"{characteristic} is counted, not measured: a gauge study "
                      "needs readings, and an attribute study is not built yet")
    names = [str(n).strip() for n in appraisers if str(n).strip()]
    if len(set(names)) != len(names):
        raise Invalid("an appraiser is named twice")
    if len(names) < MIN_APPRAISERS:
        raise Invalid(f"a study needs at least {MIN_APPRAISERS} appraisers: with one "
                      "there is no reproducibility to measure")
    if trials < MIN_TRIALS:
        raise Invalid(f"a study needs at least {MIN_TRIALS} trials: with one there "
                      "is no repeatability to measure")
    if parts < 2:
        raise Invalid("a study needs at least 2 parts")
    if code and session.scalar(select(GaugeStudy.id).where(GaugeStudy.code == code)):
        raise Conflict(f"gauge study {code} already exists")
    study = GaugeStudy(
        code=code or _next_code(session), gauge_id=instrument.id, material_id=mat.id,
        characteristic=characteristic, lower_spec=spec.min_value,
        upper_spec=spec.max_value, unit=spec.unit or "", parts=parts, trials=trials,
        appraisers=names, purpose=purpose, planned_by=actor)
    session.add(study)
    session.flush()
    audit.record(session, actor=actor, action="gauge_study.planned",
                 entity_type="gauge_study", entity_id=study.code,
                 after={"gauge": gauge, "material": material,
                        "characteristic": characteristic, "parts": parts,
                        "appraisers": names, "trials": trials})
    return summary(session, study)


def record(session: Session, code: str, *, readings: list[dict],
           actor: str = "system") -> dict:
    """Record study readings. A reading already taken is never overwritten."""
    study = get(session, code)
    if study.state is StudyState.COMPLETE:
        raise Conflict(f"gauge study {code} is complete; its readings are closed")
    if not readings:
        raise Invalid("no readings given")
    taken = {(r.part, r.appraiser, r.trial) for r in study.readings}
    added = []
    for given in readings:
        try:
            part, trial = int(given["part"]), int(given["trial"])
            appraiser = str(given["appraiser"]).strip()
            value = float(given["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise Invalid("each reading needs part, appraiser, trial and value") from exc
        if not math.isfinite(value):
            raise Invalid("a reading must be a number")
        if not 1 <= part <= study.parts:
            raise Invalid(f"part {part} is not in this study (1 to {study.parts})")
        if not 1 <= trial <= study.trials:
            raise Invalid(f"trial {trial} is not in this study (1 to {study.trials})")
        if appraiser not in study.appraisers:
            raise Invalid(f"{appraiser} is not an appraiser on this study "
                          f"({', '.join(study.appraisers)})")
        key = (part, appraiser, trial)
        if key in taken:
            raise Conflict(f"part {part}, {appraiser}, trial {trial} is already "
                           "recorded; a study reading is never overwritten")
        taken.add(key)
        row = StudyReading(study_id=study.id, part=part, appraiser=appraiser,
                           trial=trial, value=value, recorded_by=actor)
        session.add(row)
        added.append(row)
    expected = study.parts * len(study.appraisers) * study.trials
    study.state = (StudyState.COMPLETE if len(taken) == expected
                   else StudyState.COLLECTING)
    if study.state is StudyState.COMPLETE:
        study.completed_at = utcnow()
    session.flush()
    session.expire(study, ["readings"])
    audit.record(session, actor=actor, action="gauge_study.readings",
                 entity_type="gauge_study", entity_id=code,
                 after={"recorded": len(added), "state": study.state.value})
    return {"study": code, "recorded": len(added), "readings": len(taken),
            "expected": expected, "still_to_take": expected - len(taken),
            "state": study.state.value}


def summary(session: Session, study: GaugeStudy) -> dict:
    expected = study.parts * len(study.appraisers) * study.trials
    have = session.scalar(select(func.count()).select_from(StudyReading)
                          .where(StudyReading.study_id == study.id)) or 0
    return {
        "code": study.code, "gauge": study.gauge.code,
        "material": session.get(Material, study.material_id).code,
        "characteristic": study.characteristic, "unit": study.unit,
        "lower_spec": study.lower_spec, "upper_spec": study.upper_spec,
        "parts": study.parts, "appraisers": list(study.appraisers),
        "trials": study.trials, "state": study.state.value,
        "purpose": study.purpose, "planned_by": study.planned_by,
        "planned_at": study.planned_at, "completed_at": study.completed_at,
        "readings": have, "expected": expected,
    }


def studies(session: Session, *, material: str | None = None,
            characteristic: str | None = None, gauge: str | None = None,
            state: str | None = None) -> dict:
    """Every study, newest first, with the total."""
    query = select(GaugeStudy).order_by(GaugeStudy.id.desc())
    if material:
        query = query.where(GaugeStudy.material_id == _material(session, material).id)
    if characteristic:
        query = query.where(GaugeStudy.characteristic == characteristic)
    if gauge:
        query = query.where(GaugeStudy.gauge_id == gauge_service.get(session, gauge).id)
    if state:
        try:
            query = query.where(GaugeStudy.state == StudyState(state))
        except ValueError as exc:
            raise Invalid(f"unknown state {state!r}: planned, collecting or complete") from exc
    rows = [summary(session, s) for s in session.scalars(query)]
    return {"studies": rows, "total": len(rows)}


def _withheld(session: Session, study: GaugeStudy, arith: dict | None,
              have: int, expected: int) -> list[str]:
    reasons = []
    if have < expected:
        reasons.append(f"{have} of {expected} readings are in; a study with readings "
                       "missing is not judged")
    if study.parts < MIN_PARTS:
        reasons.append(f"only {study.parts} parts; at least {MIN_PARTS} are needed for "
                       "part-to-part variation to mean anything")
    tolerance = _tolerance(study)
    if arith is not None and tolerance:
        means = arith["part_means"]
        span = max(means) - min(means)
        if span < SPAN_SHARE * tolerance:
            reasons.append(
                f"the parts span {span:.4g} {study.unit}".rstrip() +
                f", under {SPAN_SHARE:.0%} of the tolerance ({tolerance:.4g}); parts "
                "that all look alike make any gauge look poor - pick parts from "
                "across the range the process makes")
    gauge = study.gauge
    last = max((r.recorded_at for r in study.readings), default=None)
    state = gauge_service.state(session, gauge.code,
                                today=(last.date() if isinstance(last, datetime) else None))
    if state["never_calibrated"]:
        reasons.append(f"gauge {gauge.code} has never been calibrated; a study of an "
                       "uncalibrated gauge measures nothing anybody can rely on")
    elif state["overdue"]:
        reasons.append(f"gauge {gauge.code} was out of calibration (due "
                       f"{state['due_on']}) when the readings were taken")
    if state["status"] in ("out_of_service", "lost"):
        reasons.append(f"gauge {gauge.code} is {state['status'].replace('_', ' ')}")
    return reasons


def _tolerance(study: GaugeStudy) -> float | None:
    if study.lower_spec is None or study.upper_spec is None:
        return None
    width = study.upper_spec - study.lower_spec
    return width if width > 0 else None


def _r(x: float | None, places: int = 6) -> float | None:
    return None if x is None else round(x, places)


def _sentence(study: GaugeStudy, judged: dict, arith: dict) -> tuple[str, str]:
    """The verdict in words, and which source dominates."""
    by = {c["source"]: c for c in judged["components"]}
    repeat = by["repeatability"]["variance"]
    repro = by["reproducibility"]["variance"]
    pct = judged["gauge_rr_pct"]
    band = judged["band"]
    if repeat >= repro:
        source = "repeatability"
        why = ("most of it is repeatability - the same appraiser on the same part "
               "does not get the same reading twice: the gauge itself (wear, "
               "fixturing, resolution)")
    else:
        source = "reproducibility"
        grand = arith["grand_mean"]
        odd = max(arith["appraiser_means"].items(), key=lambda kv: abs(kv[1] - grand))
        why = (f"most of it is reproducibility - the appraisers disagree; {odd[0]} "
               f"reads {odd[1] - grand:+.4g} {study.unit} from the average".rstrip()
               + ": method or training, not the instrument")
    ndc = judged["distinct_categories"]
    head = (f"Gauge R&R is {pct:.1f} % - {band}. ")
    if band == "acceptable":
        tail = (f"The gauge can be trusted for this characteristic; what varies is "
                f"the parts ({ndc} distinct categories).")
        source = "part"
    else:
        tail = why[0].upper() + why[1:] + f". {ndc} distinct categories"
        tail += ("." if ndc is not None and ndc >= MIN_CATEGORIES
                 else f" (fewer than {MIN_CATEGORIES}: the gauge cannot tell the "
                      "parts apart well enough to control them).")
    return head + tail, source


def result(session: Session, code: str) -> dict:
    """The study, its grid of readings, and the analysis - or why not."""
    study = get(session, code)
    base = summary(session, study)
    have, expected = base["readings"], base["expected"]
    grid = [{"part": r.part, "appraiser": r.appraiser, "trial": r.trial,
             "value": r.value, "recorded_by": r.recorded_by,
             "recorded_at": r.recorded_at} for r in study.readings]
    arith = None
    if have == expected:
        values = {(r.part, r.appraiser, r.trial): r.value for r in study.readings}
        arith = anova(values, study.parts, list(study.appraisers), study.trials)
    withheld = _withheld(session, study, arith, have, expected)
    out = {**base, "grid": grid, "grid_total": len(grid), "convention": CONVENTION,
           "withheld": withheld}
    if arith is None:
        return {**out, "analysis": None, "verdict": None,
                "note": "the analysis is computed once every reading is in"}
    tolerance = _tolerance(study)
    judged = judge(arith, tolerance)
    analysis = {
        "method": "ANOVA, crossed",
        "interaction_significant": arith["interaction_significant"],
        "interaction_p": _r(arith["interaction_p"], 4),
        "interaction_alpha": INTERACTION_ALPHA,
        "anova": [{k: (_r(v, 6) if isinstance(v, float) else v) for k, v in row.items()}
                  for row in arith["table"]],
        "components": [{k: (_r(v, 6) if isinstance(v, float) else v) for k, v in row.items()}
                       for row in judged["components"]],
        "tolerance": tolerance,
        "distinct_categories": judged["distinct_categories"],
        "gauge_rr_pct": _r(judged["gauge_rr_pct"], 2),
        "band": judged["band"],
        "part_means": [_r(m) for m in arith["part_means"]],
        "appraiser_means": {k: _r(v) for k, v in arith["appraiser_means"].items()},
        "grand_mean": _r(arith["grand_mean"]),
    }
    if not tolerance:
        analysis["tolerance_note"] = ("no tolerance on the specification, so % of "
                                      "tolerance is not known; judged on % of study "
                                      "variation alone")
    sentence, source = _sentence(study, judged, arith)
    analysis["dominant"] = source
    if withheld:
        return {**out, "analysis": analysis, "verdict": None,
                "note": "the numbers are shown; the verdict is withheld: "
                        + "; ".join(withheld)}
    return {**out, "analysis": analysis, "verdict": sentence}


def gauge_or_process(session: Session, material: str, characteristic: str) -> dict:
    """Is it the gauge or the process? The newest complete study, and the chart.

    Four answers: the gauge (its study is marginal or worse), the process (the
    gauge passed and capability is short), neither, or not known - no study,
    a withheld one, or no capability - said as not known and never as fine.
    """
    from fsmes.services import spc

    mat = _material(session, material)
    named = set(session.scalars(select(QualitySpec.characteristic)
                                .where(QualitySpec.material_id == mat.id)))
    studied = set(session.scalars(select(GaugeStudy.characteristic)
                                  .where(GaugeStudy.material_id == mat.id)))
    if characteristic not in named | studied:
        # A name the plant does not use is not a characteristic with no
        # study: say which names it does use, so the next question is right.
        known = sorted(named | studied)
        return {"material": material, "characteristic": characteristic, "study": None,
                "process": None, "answer": "not known", "known_characteristics": known,
                "convention": CONVENTION,
                "verdict": (f"Not known: {material} has no characteristic called "
                            f"{characteristic!r}. Its characteristics are "
                            + (", ".join(known) if known else "none") + " - ask again "
                            "with one of those names.")}
    newest = session.scalar(
        select(GaugeStudy).where(GaugeStudy.material_id == mat.id,
                                 GaugeStudy.characteristic == characteristic,
                                 GaugeStudy.state == StudyState.COMPLETE)
        .order_by(GaugeStudy.completed_at.desc(), GaugeStudy.id.desc()).limit(1))
    capable, _marginal = spc.cpk_bars(session)
    try:
        chart = spc.chart(session, material, characteristic)
        # The chart's own verdict decides, as on the SPC screen: no capability
        # figure leaves here while the process is out of control.
        stable = chart.get("stable")
        cap = chart.get("capability") if stable is not False else None
        process = {"stable": stable,
                   "cpk": cap.get("cpk") if cap else None,
                   "cp": cap.get("cp") if cap else None,
                   "cpk_capable": capable, "chart_verdict": chart.get("verdict")}
        if stable is False:
            process["capability_note"] = CAPABILITY_WITHHELD
    except NotFound:
        process = {"stable": None, "cpk": None, "cp": None, "cpk_capable": capable,
                   "chart_verdict": "no production readings charted"}
    out = {"material": material, "characteristic": characteristic, "process": process,
           "convention": CONVENTION}
    if newest is None:
        return {**out, "study": None, "answer": "not known",
                "verdict": (f"Not known: there is no complete gauge study for "
                            f"{characteristic} on {material}. Until there is, a bad Cpk "
                            "could be the gauge as easily as the process - plan one.")}
    study = result(session, newest.code)
    brief = {"code": study["code"], "gauge": study["gauge"],
             "gauge_rr_pct": (study["analysis"] or {}).get("gauge_rr_pct"),
             "band": (study["analysis"] or {}).get("band"),
             "dominant": (study["analysis"] or {}).get("dominant"),
             "distinct_categories": (study["analysis"] or {}).get("distinct_categories"),
             "verdict": study["verdict"], "withheld": study["withheld"]}
    out["study"] = brief
    if study["verdict"] is None:
        return {**out, "answer": "not known",
                "verdict": f"Not known: study {newest.code} is withheld - "
                           + "; ".join(study["withheld"]) + "."}
    cpk = process["cpk"]
    if brief["band"] != "acceptable":
        who = ("the gauge itself" if brief["dominant"] == "repeatability"
               else "the people reading it")
        return {**out, "answer": "gauge",
                "verdict": (f"The gauge: study {newest.code} puts {brief['gauge_rr_pct']:.1f} % "
                            f"of the variation in the measurement, mostly {who}. Fix the "
                            "measurement before adjusting the process - a Cpk read through "
                            "this gauge is partly the gauge's.")}
    if cpk is None and process["stable"] is False:
        # The chart withholds capability while the process is out of
        # control - and an out-of-control process measured by a gauge that
        # passed is the process's trouble, not an unknown.
        return {**out, "answer": "process",
                "verdict": (f"The process: the gauge passed its study ({newest.code}, "
                            f"{brief['gauge_rr_pct']:.1f} %), and the chart says the process "
                            "is not in control, so capability is withheld until it is. Look "
                            "at the machine, the material and the method - not the gauge.")}
    if cpk is None:
        return {**out, "answer": "not known",
                "verdict": (f"The gauge is fine (study {newest.code}, "
                            f"{brief['gauge_rr_pct']:.1f} %), but capability is not known - "
                            "too few production readings or no tolerance.")}
    if cpk < capable:
        drift = "" if process["stable"] else " and the chart says it is not stable"
        return {**out, "answer": "process",
                "verdict": (f"The process: the gauge passed its study ({newest.code}, "
                            f"{brief['gauge_rr_pct']:.1f} %), so the spread is real. Cpk is "
                            f"{cpk:.2f} against {capable:g}{drift}. Look at the machine, the "
                            "material and the method - not the gauge.")}
    return {**out, "answer": "neither",
            "verdict": (f"Neither: the gauge passed its study ({newest.code}, "
                        f"{brief['gauge_rr_pct']:.1f} %) and Cpk is {cpk:.2f}, at or above "
                        f"{capable:g}.")}
