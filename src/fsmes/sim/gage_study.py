"""Scripted gauge studies, and the answer each one must come to.

A gauge study is something people do on purpose, off the line, on a day a
quality engineer picks. The everyday simulated shift never does one, and
nothing in `sim/operations.py` calls this module. It runs only when somebody
asks for it (`fsmes sim-gage-study <file> <case>`), against a plant somebody
named, and it writes what a quality engineer and the appraisers would: a
gauge on the register and its calibration, the plan, the readings, and, for
a case that has them, a run of production checks measured with that gauge.

The cases are a plant's data, not the product's: a JSON file in the plant's
pack names each case's gauge, material, characteristic and tolerance, the
gauge's own spread, each appraiser's bias, and the answer key (`expect`).
The causes are scripted; the arithmetic is not. Each case draws its readings
from a seeded random generator - every part's true size, and on top of it
the gauge's spread, each appraiser's bias, and the resolution the reading is
rounded to - and the MES works the study out as it would for real readings.
The tests check the key holds for several seeds, so a key that only holds
for the seed it was written with fails.

A case may add the specification it needs (`adds_spec`), so that its
production checks are the only ones on that chart and nothing in the
everyday shift changes. A specification that is already there with other
limits is refused, never changed.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True)
class Case:
    name: str
    study: str                     # the study's code, so the answer key can name it
    gauge: str
    gauge_name: str
    gauge_kind: str
    resolution: float              # what a reading is rounded to
    material: str
    characteristic: str
    unit: str
    lower: float
    upper: float
    #: Every part's true size spans this share of the tolerance, centred, so the
    #: study is never withheld for parts that all look alike.
    part_span: float
    repeatability_sd: float        # the gauge's own spread, one reading to the next
    biases: dict[str, float]       # appraiser -> what they add to every reading
    story: str
    #: What the study must conclude: band, dominant source, gauge or process.
    expect: dict = field(default_factory=dict)
    #: Production checks taken with this gauge: (how many, wander amplitude,
    #: spread). None for a case whose point is the study alone.
    production: tuple[int, float, float] | None = None
    adds_spec: bool = False        # the pack does not have it; this case adds it
    parts: int = 10
    trials: int = 3


def load(path: str | Path) -> dict[str, Case]:
    """The cases a plant's file describes, by name."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = {}
    for entry in raw["cases"]:
        entry = dict(entry)
        made = entry.pop("production", None)
        production = (int(made["count"]), float(made["wander"]), float(made["spread"])) if made else None
        case = Case(production=production, **entry)
        if set(case.expect) != {"band", "dominant", "gauge_or_process"}:
            raise ValueError(f"{case.name}: `expect` names band, dominant and gauge_or_process")
        cases[case.name] = case
    return cases


def _round(value: float, resolution: float) -> float:
    steps = round(value / resolution)
    return round(steps * resolution, 6)


def true_sizes(case: Case, rng: random.Random) -> list[float]:
    """Each part's true size: evenly across `part_span` of the tolerance, jittered."""
    middle = (case.lower + case.upper) / 2
    half = (case.upper - case.lower) * case.part_span / 2
    step = 2 * half / (case.parts - 1)
    sizes = [middle - half + i * step + rng.uniform(-step / 4, step / 4)
             for i in range(case.parts)]
    rng.shuffle(sizes)  # part numbers are not in size order on a real bench
    return sizes


def readings(case: Case, seed: int = 7) -> list[dict]:
    """Every reading of the study: part x appraiser x trial."""
    rng = random.Random(f"{case.name}:{seed}")
    sizes = true_sizes(case, rng)
    out = []
    for trial in range(1, case.trials + 1):
        for appraiser, bias in case.biases.items():
            for part in range(1, case.parts + 1):
                value = sizes[part - 1] + bias + rng.gauss(0, case.repeatability_sd)
                out.append({"part": part, "appraiser": appraiser, "trial": trial,
                            "value": _round(value, case.resolution)})
    return out


def production_checks(case: Case, seed: int = 7) -> list[float]:
    """The production readings of the case that has them: a slow wander and a
    wide spread around the middle of the tolerance."""
    if case.production is None:
        return []
    count, wander, spread = case.production
    rng = random.Random(f"{case.name}:production:{seed}")
    middle = (case.lower + case.upper) / 2
    return [_round(middle + wander * math.sin(2 * math.pi * i / 40) + rng.gauss(0, spread),
                   case.resolution) for i in range(count)]


class Refused(RuntimeError):
    """The plant said no, and the run stops there: a half-made study is worse
    than none."""


def _ok(response, what: str):
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise Refused(f"{what}: {response.status_code} {detail}")
    return response.json()


def run(client, case: Case, *, seed: int = 7, today: date | None = None) -> dict:
    """Make one case on a plant, through its API, and return what the MES concluded.

    `client` is a signed-in HTTP client (httpx, or a TestClient) holding
    `masterdata.write`, `quality.close_nc`, `quality.study` and
    `quality.record`, which is a quality engineer's set.
    """
    today = today or date.today()
    # Asked before anything is written: on a plant without the module the
    # gauge would be registered and calibrated and then the study refused.
    if client.get("/quality/gage-studies").status_code == 404:
        raise Refused("gauge studies are switched off on this plant - nothing was "
                      "written. Its pack switches them on with `[modules] "
                      "gauge_studies = true` (or MES_MODULES=all,gauge_studies).")
    gauges = _ok(client.get(f"/quality/gauges?q={case.gauge}"), "read the gauge register")
    if not any(g["code"] == case.gauge for g in gauges["gauges"]):
        _ok(client.post("/quality/gauges", json={
            "code": case.gauge, "name": case.gauge_name, "kind": case.gauge_kind,
            "interval_days": 365, "resolution": case.resolution}), f"register {case.gauge}")
        _ok(client.post(f"/quality/gauges/{case.gauge}/calibrate", json={
            "result": "pass", "performed_by": "metrology lab",
            "performed_on": (today - timedelta(days=30)).isoformat(),
            "notes": "simulated: calibrated before the study"}), f"calibrate {case.gauge}")
    specs = _ok(client.get("/quality/specs", params={
        "material": case.material, "characteristic": case.characteristic}),
        "read the specifications")
    spec = next(iter(specs["items"]), None)
    if spec is None:
        if not case.adds_spec:
            raise Refused(f"{case.material} has no {case.characteristic} specification; "
                          "this case uses the plant's own and adds none")
        _ok(client.post("/quality/specs", json={
            "material": case.material, "characteristic": case.characteristic,
            "unit": case.unit, "min_value": case.lower, "max_value": case.upper}),
            f"specify {case.material}/{case.characteristic}")
    elif (spec.get("min_value"), spec.get("max_value")) != (case.lower, case.upper):
        raise Refused(f"{case.material}/{case.characteristic} is specified "
                      f"{spec.get('min_value')}..{spec.get('max_value')}, not "
                      f"{case.lower}..{case.upper}; this case changes no specification")
    _ok(client.post("/quality/gage-studies", json={
        "code": case.study, "gauge": case.gauge, "material": case.material,
        "characteristic": case.characteristic, "appraisers": list(case.biases),
        "parts": case.parts, "trials": case.trials,
        "purpose": f"simulated ({case.name}): {case.story}"}), f"plan {case.study}")
    taken = readings(case, seed)
    # One post per appraiser per trial: the way a bench sheet comes back.
    for trial in range(1, case.trials + 1):
        for appraiser in case.biases:
            batch = [r for r in taken if r["trial"] == trial and r["appraiser"] == appraiser]
            _ok(client.post(f"/quality/gage-studies/{case.study}/readings",
                            json={"readings": batch}), f"record {case.study}")
    for value in production_checks(case, seed):
        _ok(client.post("/quality/checks", json={
            "material": case.material, "characteristic": case.characteristic,
            "value": value, "gauge": case.gauge}), "record a production check")
    study = _ok(client.get(f"/quality/gage-studies/{case.study}"), f"read {case.study}")
    answer = _ok(client.get("/quality/gage-studies/gauge-or-process",
                            params={"material": case.material,
                                    "characteristic": case.characteristic}),
                 "ask gauge or process")
    return {"case": case.name, "study": study, "gauge_or_process": answer,
            "expected": case.expect}


def matches_key(outcome: dict) -> list[str]:
    """What the plant concluded that the answer key did not. Empty is a pass."""
    key, study = outcome["expected"], outcome["study"]
    analysis = study.get("analysis") or {}
    wrong = []
    if study.get("verdict") is None:
        wrong.append(f"{study['code']} was not judged: {study.get('withheld')}")
    if analysis.get("band") != key["band"]:
        wrong.append(f"band {analysis.get('band')!r}, key says {key['band']!r}")
    if analysis.get("dominant") != key["dominant"]:
        wrong.append(f"dominant {analysis.get('dominant')!r}, key says {key['dominant']!r}")
    said = outcome["gauge_or_process"].get("answer")
    if said != key["gauge_or_process"]:
        wrong.append(f"gauge or process said {said!r}, key says {key['gauge_or_process']!r}")
    return wrong
