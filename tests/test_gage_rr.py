"""Gauge studies: planned, read, judged - and refused where they cannot be.

The arithmetic is proved against the published example in
`test_gage_rr_worked_example.py`; these are the record around it, the
refusals, the tools the assistant uses, and the one question a quality
engineer asks first: is it the gauge or the process?
"""

from datetime import date, timedelta

import pytest
from tests.test_gage_rr_worked_example import DATA
from tests.test_mcp_product import wired  # noqa: F401 - the fixture

from fsmes import identity, mcp_server
from fsmes.services import Conflict, Invalid, gage_rr, gauges, quality


@pytest.fixture()
def bench(session):
    """A calibrated gauge and a width with a tolerance the worked example's
    parts span."""
    gauges.register(session, code="CAL-07", name="Caliper", resolution=0.01)
    gauges.calibrate(session, "CAL-07", result="pass", performed_by="lab",
                     performed_on=identity.today() - timedelta(days=10))
    quality.create_spec(session, material_code="FG-COLA", characteristic="width",
                        unit="mm", min_value=-3.0, max_value=3.0)
    session.flush()
    return session


def plan(session, **kw):
    args = dict(gauge="CAL-07", material="FG-COLA", characteristic="width",
                appraisers=["A", "B", "C"], parts=10, trials=3, actor="qe")
    args.update(kw)
    return gage_rr.plan(session, **args)


def fill(session, code, data=DATA, skip=0):
    rows = [{"part": p + 1, "appraiser": who, "trial": t + 1, "value": v}
            for who, trials in data.items() for t, row in enumerate(trials)
            for p, v in enumerate(row)]
    return gage_rr.record(session, code, readings=rows[skip:], actor="op")


def test_a_planned_study_copies_its_tolerance_from_the_specification(bench):
    study = plan(bench)
    assert (study["lower_spec"], study["upper_spec"], study["unit"]) == (-3.0, 3.0, "mm")
    assert study["state"] == "planned" and study["expected"] == 90 and study["readings"] == 0


@pytest.mark.parametrize("kw, words", [
    ({"appraisers": ["A"]}, "at least 2 appraisers"),
    ({"trials": 1}, "at least 2 trials"),
    ({"appraisers": ["A", "A"]}, "named twice"),
])
def test_a_study_that_cannot_separate_anything_is_refused_when_planned(bench, kw, words):
    with pytest.raises(Invalid, match=words):
        plan(bench, **kw)


def test_a_full_study_completes_itself_and_gives_a_verdict_in_words(bench):
    code = plan(bench)["code"]
    out = fill(bench, code)
    assert out["state"] == "complete" and out["still_to_take"] == 0
    res = gage_rr.result(bench, code)
    assert res["withheld"] == []
    assert res["analysis"]["distinct_categories"] == 4
    # 6 x 0.3024 against a tolerance of 6 is 30.2 % - over the band.
    assert res["analysis"]["band"] == "not acceptable"
    assert "Gauge R&R is 30.2 %" in res["verdict"]
    assert res["grid_total"] == 90


def test_a_reading_already_taken_is_refused_not_overwritten(bench):
    code = plan(bench)["code"]
    gage_rr.record(bench, code, readings=[{"part": 1, "appraiser": "A", "trial": 1,
                                           "value": 0.29}])
    with pytest.raises(Conflict, match="never overwritten"):
        gage_rr.record(bench, code, readings=[{"part": 1, "appraiser": "A",
                                               "trial": 1, "value": 0.5}])


@pytest.mark.parametrize("reading, words", [
    ({"part": 11, "appraiser": "A", "trial": 1, "value": 1}, "part 11 is not in"),
    ({"part": 1, "appraiser": "D", "trial": 1, "value": 1}, "D is not an appraiser"),
    ({"part": 1, "appraiser": "A", "trial": 4, "value": 1}, "trial 4 is not in"),
])
def test_a_reading_outside_the_plan_is_refused(bench, reading, words):
    code = plan(bench)["code"]
    with pytest.raises(Invalid, match=words):
        gage_rr.record(bench, code, readings=[reading])


def test_a_study_with_readings_missing_is_not_judged(bench):
    code = plan(bench)["code"]
    fill(bench, code, skip=1)
    res = gage_rr.result(bench, code)
    assert res["verdict"] is None and res["analysis"] is None
    assert "89 of 90 readings are in" in res["withheld"][0]


def test_a_study_of_too_few_parts_shows_its_numbers_but_withholds_the_verdict(bench):
    code = plan(bench, parts=4)["code"]
    fill(bench, code, data={k: [row[:4] for row in v] for k, v in DATA.items()})
    res = gage_rr.result(bench, code)
    assert res["analysis"] is not None and res["verdict"] is None
    assert any("only 4 parts" in w for w in res["withheld"])


def test_parts_that_do_not_span_the_tolerance_withhold_the_verdict(bench):
    quality.create_spec(bench, material_code="FG-COLA", characteristic="wide",
                        unit="mm", min_value=-10.0, max_value=10.0)
    code = plan(bench, characteristic="wide")["code"]
    fill(bench, code)
    res = gage_rr.result(bench, code)
    assert res["verdict"] is None
    assert any("under 50% of the tolerance" in w for w in res["withheld"])


def test_a_gauge_out_of_calibration_withholds_the_verdict(bench):
    gauges.register(bench, code="CAL-OLD", name="Old caliper", interval_days=30)
    gauges.calibrate(bench, "CAL-OLD", result="pass", performed_by="lab",
                     performed_on=date(2020, 1, 1))
    code = plan(bench, gauge="CAL-OLD")["code"]
    fill(bench, code)
    res = gage_rr.result(bench, code)
    assert res["verdict"] is None
    assert any("CAL-OLD was out of calibration" in w for w in res["withheld"])


def test_with_no_study_the_gauge_or_the_process_is_not_known(bench):
    out = gage_rr.gauge_or_process(bench, "FG-COLA", "width")
    assert out["answer"] == "not known" and "no complete gauge study" in out["verdict"]


def test_a_bad_study_says_it_is_the_gauge(bench):
    code = plan(bench)["code"]
    fill(bench, code)
    out = gage_rr.gauge_or_process(bench, "FG-COLA", "width")
    assert out["answer"] == "gauge" and out["study"]["code"] == code



def test_a_good_gauge_on_a_process_out_of_control_says_it_is_the_process(bench, monkeypatch):
    """The chart withholds Cpk while the process is out of control. With a
    gauge that passed its study, that is the process's trouble - said as
    such, not as not known."""
    from dataclasses import replace

    from fsmes.services import spc
    from fsmes.sim import gage_study

    case = replace(gage_study.CASES["wandering-process"], lower=-3.0, upper=3.0,
                   biases={"A": 0.0, "B": 0.0, "C": 0.0}, repeatability_sd=0.01)
    code = plan(bench)["code"]
    gage_rr.record(bench, code, readings=gage_study.readings(case), actor="op")
    monkeypatch.setattr(spc, "chart", lambda *a, **k: {
        "stable": False, "capability": None, "verdict": "out of control"})
    out = gage_rr.gauge_or_process(bench, "FG-COLA", "width")
    assert out["study"]["band"] == "acceptable"
    assert out["answer"] == "process" and "not in control" in out["verdict"]

# ---------------------------------------------------------------- the tools


def test_the_agent_previews_a_study_plan_but_planning_one_is_a_persons(wired, bench):  # noqa: F811
    preview = mcp_server.plan_gage_study("testplant", gauge="CAL-07", material="FG-COLA",
                                         characteristic="width", appraisers=["A", "B"],
                                         dry_run=True)
    assert preview["dry_run"] is True
    assert preview["request"]["path"] == "/quality/gage-studies"
    refused = mcp_server.plan_gage_study("testplant", gauge="CAL-07", material="FG-COLA",
                                         characteristic="width", appraisers=["A", "B"])
    assert "error" in refused and "403" in refused["error"] and "quality.study" in refused["error"]


def test_the_assistant_records_a_whole_study_and_reads_its_verdict_through_tools(wired, bench):  # noqa: F811
    code = plan(bench, code="GS-TOOL")["code"]
    bench.commit()
    for who, trials in DATA.items():
        for t, row in enumerate(trials):
            for p, v in enumerate(row):
                out = mcp_server.record_study_reading("testplant", study=code, part=p + 1,
                                                      appraiser=who, trial=t + 1, value=v)
                assert out.get("audited_as") == "AGENT", out
    listed = mcp_server.gage_studies("testplant", material="FG-COLA")
    assert listed["total"] == 1 and listed["studies"][0]["state"] == "complete"
    res = mcp_server.gage_study("testplant", study=code)
    assert res["analysis"]["band"] == "not acceptable" and res["verdict"]
    answer = mcp_server.gauge_or_process("testplant", material="FG-COLA",
                                         characteristic="width")
    assert answer["answer"] == "gauge"


def test_a_duplicate_reading_through_the_tool_is_a_conflict_the_assistant_can_read(wired, bench):  # noqa: F811
    code = plan(bench, code="GS-DUP")["code"]
    bench.commit()
    args = dict(study=code, part=1, appraiser="A", trial=1, value=0.29)
    assert "error" not in mcp_server.record_study_reading("testplant", **args)
    again = mcp_server.record_study_reading("testplant", **args)
    assert "error" in again and "409" in again["error"]


def test_the_ai_tab_can_read_every_gauge_study_and_ask_gauge_or_process_but_never_plan_one():
    """The AI tab's chat is the analysis agent: the three reads are on its
    list, and the two writes are not, by the same exclusion every write
    falls under. The Assistant on the screens offers the writes, each to the
    person holding its capability."""
    from fsmes.services import agent
    from fsmes.services import capabilities as caps

    analyst = {"plant.read", "audit.read"}
    offered = {t["name"] for t in agent.catalogue(analyst, for_kind=agent.ANALYSIS)}
    assert {"gage_studies", "gage_study", "gauge_or_process"} <= offered
    assert not {"plan_gage_study", "record_study_reading"} & offered

    engineer = {"plant.read", "quality.study", "quality.record"}
    assert caps.holders("quality.study") == ["Administrator"]
    floor = {t["name"] for t in agent.catalogue(engineer, for_kind=agent.FLOOR)}
    assert {"plan_gage_study", "record_study_reading"} <= floor
    operator = {t["name"] for t in agent.catalogue({"plant.read", "quality.record"},
                                                   for_kind=agent.FLOOR)}
    assert "record_study_reading" in operator and "plan_gage_study" not in operator
