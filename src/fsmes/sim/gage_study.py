"""Three scripted gauge studies, and the answer each one must come to.

A gauge study is something people do on purpose, off the line, on a day a
quality engineer picks. The everyday simulated shift never does one, and
nothing in `sim/operations.py` calls this module. It runs only when somebody
asks for it (`fsmes sim-gage-study`), against a plant somebody named, and it
writes what a quality engineer and three appraisers would: a gauge on the
register and its calibration, the plan, ninety readings, and, for the third
case only, a run of production checks measured with the gauge that passed.

The causes are scripted; the arithmetic is not. Each case draws its readings
from a seeded random generator: every part's true size, and on top of it
the gauge's own spread, each appraiser's own bias, and the resolution the
reading is rounded to. Then the MES works out the study the way it would
for real readings. `ANSWER_KEY` says what the study and "gauge or process"
should conclude. The tests check that they do, for several seeds, so a key
that only holds for the seed it was written with fails.

* **worn-gauge**: a micrometer on fork thickness whose anvil has worn. The
  same person measuring the same part three times gets three different
  numbers. Repeatability dominates, and the study is not acceptable.
* **one-appraiser**: a depth gauge on spoon bowls. The gauge is good, but
  one of the three appraisers seats it differently and reads about a tenth
  of a millimetre deep. Reproducibility dominates, and the study is not
  acceptable.
* **wandering-process**: a good caliper on knife bolster width. The study
  passes, and the production checks taken with that caliper still have a
  Cpk under 1.33, because the process wanders and is wide. The answer is
  the process, not the gauge.

The materials, characteristics and tolerances are the cutlery pack's
(`labs/cutlery/masterdata/quality_specs.json`). The exception is bolster
width, which the pack does not specify and the everyday floor never
measures. Case three adds that specification, so that its production checks
are the only ones on that chart and nothing in the everyday shift changes.
A specification that is already there with other limits is refused, never
changed.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta


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
    #: Production checks taken with this gauge: (how many, wander amplitude,
    #: spread). None for a case whose point is the study alone.
    production: tuple[int, float, float] | None = None
    adds_spec: bool = False        # the pack does not have it; this case adds it
    parts: int = 10
    trials: int = 3
    extra: dict = field(default_factory=dict)


CASES: dict[str, Case] = {c.name: c for c in (
    Case(name="worn-gauge", study="GS-WORN-MIC", gauge="MIC-07",
         gauge_name="Micrometer 0-25 mm, fork cell", gauge_kind="micrometer",
         resolution=0.001, material="UT-FORK", characteristic="thickness_mm", unit="mm",
         lower=1.9, upper=2.3, part_span=0.75, repeatability_sd=0.03,
         biases={"Ana": 0.0, "Ben": 0.003, "Cleo": -0.002},
         story="The micrometer's anvil is worn: the same part, read three times by the "
               "same person, gives three different numbers."),
    Case(name="one-appraiser", study="GS-ONE-READER", gauge="DEP-03",
         gauge_name="Depth gauge, spoon bowls", gauge_kind="depth gauge",
         resolution=0.001, material="UT-SPOON", characteristic="bowl_depth_mm", unit="mm",
         lower=8.5, upper=9.5, part_span=0.8, repeatability_sd=0.006,
         biases={"Ana": 0.0, "Ben": 0.005, "Cleo": 0.12},
         story="The gauge is good; Cleo seats it differently from Ana and Ben and reads "
               "about 0.12 mm deep on every part."),
    Case(name="wandering-process", study="GS-GOOD-CAL", gauge="CAL-12",
         gauge_name="Digital caliper, knife cell", gauge_kind="caliper",
         resolution=0.001, material="UT-KNIFE", characteristic="bolster_width_mm", unit="mm",
         lower=11.5, upper=12.5, part_span=0.8, repeatability_sd=0.005,
         biases={"Ana": 0.0, "Ben": 0.002, "Cleo": -0.002},
         production=(100, 0.15, 0.14), adds_spec=True,
         story="A good caliper on a process that wanders: the study passes, and the "
               "parts it measures are still too wide for the tolerance."),
)}

#: What each case must conclude: the study's band, which source dominates it,
#: and the answer to "is it the gauge or the process?".
ANSWER_KEY: dict[str, dict] = {
    "worn-gauge": {"study": "GS-WORN-MIC", "band": "not acceptable",
                   "dominant": "repeatability", "gauge_or_process": "gauge"},
    "one-appraiser": {"study": "GS-ONE-READER", "band": "not acceptable",
                      "dominant": "reproducibility", "gauge_or_process": "gauge"},
    "wandering-process": {"study": "GS-GOOD-CAL", "band": "acceptable",
                          "dominant": "part", "gauge_or_process": "process"},
}


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


def run(client, name: str, *, seed: int = 7, today: date | None = None) -> dict:
    """Make one case on a plant, through its API, and return what the MES concluded.

    `client` is a signed-in HTTP client (httpx, or a TestClient) holding
    `masterdata.write`, `quality.close_nc`, `quality.study` and
    `quality.record`, which is a quality engineer's set.
    """
    case = CASES[name]
    today = today or date.today()
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
                          "this case uses the cutlery pack's and adds none")
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
    return {"case": name, "study": study, "gauge_or_process": answer,
            "expected": ANSWER_KEY[name]}


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
