"""The gauge-study simulator: three scripted causes, and the MES finds each.

The causes are scripted (`sim/gage_study.py`); the conclusions are the
MES's own, worked out from the readings the way they would be for real ones.
Each case is checked against its answer key for several seeds, so a key that
held only for the seed it was written with would fail here.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from fsmes.services import gage_rr
from fsmes.sim import gage_study

CASES = gage_study.load(Path(__file__).resolve().parents[1] / "labs" / "cutlery" / "gage_studies.json")

SEEDS = (7, 11, 2026)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("name", sorted(CASES))
def test_each_case_s_readings_come_to_the_band_and_the_source_its_key_names(name, seed):
    case = CASES[name]
    values = {(r["part"], r["appraiser"], r["trial"]): r["value"]
              for r in gage_study.readings(case, seed)}
    assert len(values) == case.parts * len(case.biases) * case.trials
    arith = gage_rr.anova(values, case.parts, list(case.biases), case.trials)
    judged = gage_rr.judge(arith, case.upper - case.lower)
    key = CASES[name].expect
    assert judged["band"] == key["band"], (name, seed, judged["gauge_rr_pct"])
    by = {c["source"]: c["variance"] for c in judged["components"]}
    biggest_measurement = max(("repeatability", "reproducibility"), key=by.get)
    if key["dominant"] == "part":
        assert judged["distinct_categories"] >= gage_rr.MIN_CATEGORIES
    else:
        assert biggest_measurement == key["dominant"], (name, seed, by)


def test_the_same_seed_gives_the_same_readings_and_another_seed_does_not():
    case = CASES["worn-gauge"]
    assert gage_study.readings(case, 7) == gage_study.readings(case, 7)
    assert gage_study.readings(case, 7) != gage_study.readings(case, 8)


def test_every_reading_is_rounded_to_its_gauge_s_resolution():
    for case in CASES.values():
        for r in gage_study.readings(case):
            steps = r["value"] / case.resolution
            assert abs(steps - round(steps)) < 1e-6, (case.name, r)


def test_only_the_wandering_process_writes_production_checks():
    assert [n for n, c in CASES.items() if gage_study.production_checks(c)] == [
        "wandering-process"]


def test_the_everyday_shift_never_runs_a_gauge_study():
    """A study is somebody's decision on a chosen day; the floor never makes one."""
    from fsmes.sim import operations

    assert "gage_study" not in inspect.getsource(operations)


@pytest.fixture()
def engineer(admin, session):
    """The demo plant, with the cutlery pack's three materials and the two
    specifications the first two cases borrow from it."""
    for code in ("UT-FORK", "UT-SPOON", "UT-KNIFE"):
        r = admin.post("/masterdata/materials", json={"code": code, "name": code, "type": "finished"})
        assert r.status_code in (200, 201), r.text
    for material, characteristic, low, high in (("UT-FORK", "thickness_mm", 1.9, 2.3),
                                                ("UT-SPOON", "bowl_depth_mm", 8.5, 9.5)):
        r = admin.post("/quality/specs", json={"material": material, "characteristic": characteristic,
                                                "unit": "mm", "min_value": low, "max_value": high})
        assert r.status_code in (200, 201), r.text
    return admin


@pytest.mark.parametrize("name", sorted(CASES))
def test_on_a_plant_each_case_comes_to_its_answer_through_the_api(engineer, name):
    outcome = gage_study.run(engineer, CASES[name])
    assert gage_study.matches_key(outcome) == [], outcome["gauge_or_process"]["verdict"]


def test_a_specification_with_other_limits_is_refused_not_changed(engineer):
    r = engineer.post("/quality/specs", json={"material": "UT-KNIFE", "characteristic": "bolster_width_mm",
                                              "unit": "mm", "min_value": 11.0, "max_value": 13.0})
    assert r.status_code in (200, 201), r.text
    with pytest.raises(gage_study.Refused, match="changes no specification"):
        gage_study.run(engineer, CASES["wandering-process"])
    spec = engineer.get("/quality/specs", params={"material": "UT-KNIFE",
                                                  "characteristic": "bolster_width_mm"}).json()
    assert (spec["items"][0]["min_value"], spec["items"][0]["max_value"]) == (11.0, 13.0)
