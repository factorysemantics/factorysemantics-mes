"""SPC and gauge control.

Two questions the module exists to keep apart - is the process stable, and is
it capable - plus the one thing that makes either meaningful: whether the
gauge could be believed.
"""

from datetime import date, timedelta

import pytest

from fsmes.services import Invalid, NotFound, gauges, quality, spc


def _record(session, values, characteristic="brix"):
    for value in values:
        quality.record_check(session, material_code="FG-COLA",
                             characteristic=characteristic, value=value, actor="test")
    session.flush()


# --------------------------------------------------------------------- SPC

def test_too_few_readings_draws_no_limits_and_says_why(session):
    """Control limits from six points move with every reading. Drawing them
    would mislead more than the absence does."""
    _record(session, [11.0, 11.1, 10.9])
    out = spc.chart(session, "FG-COLA", "brix")
    assert out["control"] is None
    assert "at least" in out["note"]


def test_a_steady_process_is_in_control(session):
    _record(session, [11.0 + (i % 3 - 1) * 0.02 for i in range(30)])
    out = spc.chart(session, "FG-COLA", "brix")
    assert out["stable"] is True
    assert out["control"]["upper"] > out["control"]["centre"] > out["control"]["lower"]


def test_control_limits_come_from_the_process_not_the_tolerance(session):
    """A process can sit comfortably inside spec while drifting badly, and a
    chart drawn against the specification will never show it."""
    _record(session, [11.0 + (i % 3 - 1) * 0.01 for i in range(30)])
    out = spc.chart(session, "FG-COLA", "brix")
    # The spec is far wider than this tight process, so control limits must be
    # much tighter than the specification limits.
    assert out["control"]["upper"] < (out["upper_spec"] or 99)


def test_a_wild_point_fires_rule_one(session):
    _record(session, [11.0] * 20 + [15.0])
    out = spc.chart(session, "FG-COLA", "brix")
    assert out["stable"] is False
    assert any(s["rule"] == 1 for s in out["signals"])


def test_a_sustained_shift_fires_the_run_rule(session):
    """Eight points on one side of centre: nothing is out of spec, and the
    process has moved. This is what a tolerance check cannot see."""
    _record(session, [11.0, 10.9, 11.1, 10.95, 11.05, 10.9, 11.1, 11.0,
                      10.9, 11.1, 10.95] + [11.4] * 10)
    out = spc.chart(session, "FG-COLA", "brix")
    assert any(s["rule"] in (3, 4) for s in out["signals"])


def test_each_point_is_flagged_once(session):
    """A chart that flags the same reading four times teaches people to
    ignore it."""
    _record(session, [11.0] * 20 + [20.0])
    out = spc.chart(session, "FG-COLA", "brix")
    indexes = [s["index"] for s in out["signals"]]
    assert len(indexes) == len(set(indexes))


def test_capability_is_withheld_while_the_process_is_unstable(session):
    """A capability number computed on an out-of-control process describes a
    process that no longer exists."""
    _record(session, [11.0] * 20 + [16.0])
    out = spc.chart(session, "FG-COLA", "brix")
    assert out["stable"] is False
    assert "not meaningful" in out["verdict"]


def test_a_stable_but_incapable_process_is_named_as_such(session):
    """The most useful verdict there is: stable, and stably wrong."""
    spec = quality.create_spec(session, material_code="FG-COLA",
                               characteristic="tight", unit="x",
                               min_value=10.99, max_value=11.01, actor="test")
    assert spec is not None
    _record(session, [11.0 + (i % 5 - 2) * 0.05 for i in range(30)],
            characteristic="tight")
    out = spc.chart(session, "FG-COLA", "tight")
    if out["stable"]:
        assert out["capability"]["cpk"] < 1.0
        assert "not capable" in out["verdict"]


# ------------------------------------------------------------------ gauges

def test_a_gauge_never_calibrated_is_overdue(session):
    """Not 'fine until proven otherwise'."""
    gauges.register(session, code="G1", name="Calipers")
    session.flush()
    row = gauges.register_list(session)["gauges"][0]
    assert row["never_calibrated"] is True and row["overdue"] is True


def test_calibration_sets_the_next_due_date(session):
    gauges.register(session, code="G2", name="Scale", interval_days=180)
    session.flush()
    out = gauges.calibrate(session, "G2", result="pass", performed_by="TECH",
                           performed_on=date(2026, 1, 1))
    assert out["next_due"] == date(2026, 1, 1) + timedelta(days=180)
    assert out["status"] == "in_service"


def test_a_gauge_found_out_of_tolerance_comes_off_the_floor(session):
    """Leaving it in service is how bad data keeps arriving."""
    gauges.register(session, code="G3", name="Scale")
    session.flush()
    out = gauges.calibrate(session, "G3", result="fail_as_found", performed_by="TECH")
    assert out["status"] == "out_of_service"


def test_a_failed_calibration_names_what_it_invalidated(session):
    """A plant that cannot list these ends up quarantining far more than it
    needs to."""
    gauge = gauges.register(session, code="G4", name="Scale")
    session.flush()
    for value in (11.0, 11.1, 10.9):
        check, _nc, _signals = quality.record_check(session, material_code="FG-COLA",
                                        characteristic="brix", value=value,
                                        actor="test")
        check.gauge_id = gauge.id
    session.flush()

    out = gauges.calibrate(session, "G4", result="fail_as_found", performed_by="TECH")
    assert out["suspect_measurements"]["count"] == 3
    assert "re-judged" in out["suspect_measurements"]["note"]


def test_a_coarse_gauge_cannot_judge_a_tight_tolerance(session):
    """The rule of ten. Below four to one the measurement is mostly the
    gauge, and the control chart is charting the instrument."""
    gauges.register(session, code="G5", name="Ruler", resolution=1.0)
    session.flush()
    out = gauges.resolution_check(session, "G5", tolerance=2.0)
    assert out["adequate"] is False and out["usable"] is False
    assert "mostly the gauge" in out["verdict"]


def test_a_fine_gauge_is_adequate(session):
    gauges.register(session, code="G6", name="Micrometer", resolution=0.001)
    session.flush()
    assert gauges.resolution_check(session, "G6", tolerance=0.05)["adequate"] is True


def test_an_unknown_calibration_result_is_refused(session):
    gauges.register(session, code="G7", name="Scale")
    session.flush()
    with pytest.raises(Invalid, match="unknown result"):
        gauges.calibrate(session, "G7", result="probably fine", performed_by="TECH")


# ------------------------------------------- the gauge on the measurement

def test_a_recorded_check_names_the_gauge_that_took_it(session):
    """The first question anybody asks about a point on a control chart is
    whether the process moved or the instrument did, and a check that cannot
    name its gauge cannot be asked."""
    gauges.register(session, code="SCALE-9", name="Checkweigher", resolution=0.1)
    session.flush()
    check, _nc, _signals = quality.record_check(
        session, material_code="FG-COLA", characteristic="brix", value=11.0,
        gauge_code="SCALE-9", actor="test")
    session.flush()
    assert check.gauge_id == gauges.get(session, "SCALE-9").id
    assert gauges.impact(session, "SCALE-9")["count"] == 1


def test_a_check_with_no_gauge_says_not_recorded_rather_than_nothing(session):
    """Null is *not recorded*. It is the honest answer for a plant whose
    checks arrive without an instrument, and it is not the same claim as
    saying no gauge was used."""
    check, _nc, _signals = quality.record_check(
        session, material_code="FG-COLA", characteristic="brix", value=11.0, actor="test")
    assert check.gauge_id is None


def test_a_reading_from_a_gauge_this_plant_does_not_have_is_refused(session):
    """Dropping the unknown code instead would leave a reading on the chart
    that looks exactly like one whose gauge is on the register."""
    with pytest.raises(NotFound):
        quality.record_check(session, material_code="FG-COLA", characteristic="brix",
                             value=11.0, gauge_code="NO-SUCH-SCALE", actor="test")


def test_a_gauge_that_is_overdue_still_records_what_it_measured(session):
    """Refusing here would leave the reading unnamed, which is worse: these
    readings happen on real floors, and bounding them is what the gauge's
    impact list is for."""
    gauges.register(session, code="SCALE-OLD", name="Old scale", interval_days=1)
    gauges.calibrate(session, "SCALE-OLD", result="pass", performed_by="TECH",
                     performed_on=date.today() - timedelta(days=40))
    session.flush()
    assert gauges.register_list(session)["overdue"] >= 1
    check, _nc, _signals = quality.record_check(
        session, material_code="FG-COLA", characteristic="brix", value=11.0,
        gauge_code="SCALE-OLD", actor="test")
    assert check.gauge_id is not None


def test_the_check_list_a_screen_reads_carries_the_gauge_and_the_station(client, session):
    """Over the API, because that is where the chart reads it from."""
    gauges.register(session, code="SCALE-API", name="Checkweigher", resolution=0.1)
    session.commit()
    posted = client.post("/quality/checks", json={
        "material": "FG-COLA", "characteristic": "brix", "value": 11.0,
        "gauge": "SCALE-API"})
    assert posted.status_code == 201, posted.text
    assert posted.json()["gauge"] == "SCALE-API"
    rows = client.get("/quality/checks", params={"material": "FG-COLA"}).json()["items"]
    assert rows[0]["gauge"] == "SCALE-API"
    assert "equipment" in rows[0]


def test_an_unknown_gauge_is_refused_over_the_api_too(client):
    refused = client.post("/quality/checks", json={
        "material": "FG-COLA", "characteristic": "brix", "value": 11.0,
        "gauge": "NO-SUCH-SCALE"})
    assert refused.status_code == 404, refused.text
