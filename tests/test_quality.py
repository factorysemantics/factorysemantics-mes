"""Quality checks: spec evaluation, automatic non-conformances, and their life.

A non-conformance is not a flag that gets unset. Somebody picks it up, somebody
decides what happens to the material, and only then is it closed — and the
record says who did each of those and when. Decision record 0024.
"""

import pytest

from fsmes.domain import CheckResult, NcDisposition, NcStatus
from fsmes.services import Conflict, quality


def test_check_in_spec_passes(session):
    check, nc = quality.record_check(
        session, material_code="FG-COLA", characteristic="brix", value=10.2, actor="scott"
    )
    assert check.result is CheckResult.PASS
    assert nc is None


def test_check_out_of_spec_opens_nonconformance(session):
    check, nc = quality.record_check(session, material_code="FG-COLA", characteristic="brix",
                                     value=13.0, actor="ines")
    assert check.result is CheckResult.FAIL
    assert nc is not None and nc.status is NcStatus.OPEN
    assert "brix=13.0" in nc.description
    assert nc.raised_by == "ines"


def raise_one(session, actor="ines"):
    _check, nc = quality.record_check(session, material_code="FG-COLA", characteristic="brix",
                                      value=13.0, actor=actor)
    return nc


def test_a_nonconformance_walks_open_review_disposition_closed(session):
    nc = raise_one(session)
    quality.review_nc(session, nc.code, actor="marek")
    assert nc.status is NcStatus.UNDER_REVIEW and nc.reviewed_by == "marek"

    quality.disposition_nc(session, nc.code, disposition="rework",
                           reason="re-blend and re-test the batch", actor="priya")
    assert nc.status is NcStatus.DISPOSITIONED
    assert nc.disposition is NcDisposition.REWORK and nc.disposition_by == "priya"

    quality.close_nc(session, nc.code, actor="scott")
    assert nc.status is NcStatus.CLOSED and nc.closed_by == "scott"


def test_closing_is_refused_until_somebody_has_decided_about_the_material(session):
    nc = raise_one(session)
    with pytest.raises(Conflict) as refused:
        quality.close_nc(session, nc.code, actor="scott")
    assert "disposition" in str(refused.value)
    assert nc.status is NcStatus.OPEN


def test_a_disposition_without_a_reason_is_refused(session):
    nc = raise_one(session)
    with pytest.raises(Conflict):
        quality.disposition_nc(session, nc.code, disposition="use_as_is", reason="  ", actor="scott")
    assert nc.disposition is None


def test_a_disposition_the_plant_does_not_have_is_refused_by_name(session):
    nc = raise_one(session)
    with pytest.raises(Conflict) as refused:
        quality.disposition_nc(session, nc.code, disposition="ignore", reason="because", actor="scott")
    assert "use_as_is" in str(refused.value) and "scrap" in str(refused.value)


def test_a_nonconformance_may_be_dispositioned_without_a_review_first(session):
    """Review is a step, not a gate. A supervisor standing at the line who
    already knows the answer should not have to click twice to say it."""
    nc = raise_one(session)
    quality.disposition_nc(session, nc.code, disposition="scrap", reason="lost the batch",
                           actor="marek")
    assert nc.status is NcStatus.DISPOSITIONED and nc.reviewed_by is None


def test_the_history_names_who_took_each_step_and_when(session):
    nc = raise_one(session, actor="ines")
    quality.review_nc(session, nc.code, actor="marek")
    quality.disposition_nc(session, nc.code, disposition="use_as_is",
                           reason="customer accepted the deviation in writing", actor="priya")
    quality.close_nc(session, nc.code, actor="scott")

    steps = nc.history()
    assert [s["step"] for s in steps] == ["opened", "under_review", "dispositioned", "closed"]
    assert [s["by"] for s in steps] == ["ines", "marek", "priya", "scott"]
    assert all(s["at"] is not None for s in steps)
    assert "customer accepted" in steps[2]["detail"]


def test_a_nonconformance_from_before_this_mes_asked_who_says_so(session):
    """The migration left the old rows' new columns null. An unknown reviewer
    reads as unknown; inventing "system" there would be a lie on a quality
    record."""
    nc = raise_one(session, actor="ines")
    nc.raised_by = None
    assert nc.history()[0]["by"] is None


def test_a_dispositioned_nonconformance_cannot_be_dispositioned_again(session):
    nc = raise_one(session)
    quality.disposition_nc(session, nc.code, disposition="scrap", reason="bin it", actor="marek")
    with pytest.raises(Conflict) as refused:
        quality.disposition_nc(session, nc.code, disposition="rework", reason="second thoughts",
                               actor="marek")
    assert "scrap" in str(refused.value)


# ------------------------------------------------------------- over the API

def raise_over_api(client):
    made = client.post("/quality/checks",
                       json={"material": "FG-COLA", "characteristic": "brix", "value": 99.0})
    assert made.status_code == 201
    code = made.json()["non_conformance"]
    assert code
    return code


def test_the_api_walks_a_nonconformance_to_closed_and_says_what_is_next(supervisor):
    code = raise_over_api(supervisor)

    listed = supervisor.get(f"/quality/nonconformances?q={code}").json()["items"][0]
    assert listed["status"] == "open" and listed["next_steps"] == ["review", "disposition"]

    under_review = supervisor.post(f"/quality/nonconformances/{code}/review").json()
    assert under_review["status"] == "under_review" and under_review["next_steps"] == ["disposition"]

    too_early = supervisor.post(f"/quality/nonconformances/{code}/close")
    assert too_early.status_code == 409 and "disposition" in too_early.json()["detail"]

    decided = supervisor.post(f"/quality/nonconformances/{code}/disposition",
                          json={"disposition": "rework", "reason": "re-blend and re-test"}).json()
    assert decided["disposition"] == "rework" and decided["next_steps"] == ["close"]

    closed = supervisor.post(f"/quality/nonconformances/{code}/close").json()
    assert closed["status"] == "closed" and closed["next_steps"] == []
    assert [s["step"] for s in closed["history"]] == [
        "opened", "under_review", "dispositioned", "closed"]


def test_a_disposition_the_plant_does_not_have_is_refused_by_the_api(supervisor):
    code = raise_over_api(supervisor)
    refused = supervisor.post(f"/quality/nonconformances/{code}/disposition",
                          json={"disposition": "ignore", "reason": "because"})
    assert refused.status_code == 422


def test_one_nonconformance_can_be_read_with_its_order_and_its_history(supervisor):
    code = raise_over_api(supervisor)
    supervisor.post(f"/quality/nonconformances/{code}/disposition",
                json={"disposition": "scrap", "reason": "lost the batch"})
    one = supervisor.get(f"/quality/nonconformances/{code}").json()
    assert one["code"] == code and one["disposition"] == "scrap"
    assert one["disposition_reason"] == "lost the batch"
    assert [s["step"] for s in one["history"]] == ["opened", "dispositioned"]


def test_a_nonconformance_that_does_not_exist_is_a_404(supervisor):
    assert supervisor.get("/quality/nonconformances/NC-99999").status_code == 404


# ------------------------------------------------ what the station card reads

def test_the_dispatch_list_names_what_each_operation_is_making(supervisor):
    """The station's quality card needs it. A specification is held against a
    material, not against a machine, so a screen that only knows the machine
    cannot tell which characteristics judge the job in front of it."""
    made = supervisor.post("/workorders", json={
        "code": "WO-Q1", "material": "FG-COLA", "quantity": 10})
    assert made.status_code == 201, made.text
    assert supervisor.post("/workorders/WO-Q1/release").status_code == 200

    rows = supervisor.get("/workorders/dispatch").json()
    assert rows, "a released order should be dispatched"
    assert all(row.get("material") for row in rows)
    assert {row["material"] for row in rows} == {"FG-COLA"}


def test_a_station_can_ask_for_only_the_specs_of_what_it_is_running(supervisor):
    """A measurement with nothing to judge it against cannot pass or fail, so
    the card offers the characteristics that have a spec for this material and
    no others."""
    mine = supervisor.get("/quality/specs?material=FG-COLA").json()
    assert mine and all(s["material"] == "FG-COLA" for s in mine)
    assert "brix" in {s["characteristic"] for s in mine}


def test_the_recent_results_for_one_characteristic_say_how_many_there_are(supervisor):
    """House rule: every list states its total. The card shows the last few and
    the envelope says what they are the last few of."""
    for value in (10.0, 10.1, 10.3):
        supervisor.post("/quality/checks",
                        json={"material": "FG-COLA", "characteristic": "brix", "value": value})
    page = supervisor.get("/quality/checks?material=FG-COLA&characteristic=brix&limit=2").json()
    assert len(page["items"]) == 2 and page["total"] >= 3
    assert page["has_more"] is True


def test_recording_a_check_from_the_station_names_the_non_conformance_it_raised(supervisor):
    """The operator who recorded it is told which one, by code, so a supervisor
    can be sent to it. Raising it is the MES working, not an error."""
    out = supervisor.post("/quality/checks", json={
        "material": "FG-COLA", "characteristic": "brix", "value": 99.0}).json()
    assert out["result"] == "fail"
    assert out["non_conformance"], "an out-of-spec reading must name the record it opened"
