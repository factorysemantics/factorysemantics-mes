"""Shift analysis: the arithmetic behind the screens an ERP cannot draw.

The bugs worth guarding here are the ones that look plausible on a chart —
a share over 100%, a negative loss, downtime attributed to time nobody was
watching. Each of those renders as a perfectly convincing bar.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from fsmes.db import utcnow
from fsmes.domain import (
    Equipment,
    EquipmentLevel,
    EquipmentState,
    EquipmentStateName,
    ProductionLog,
    ProductionSource,
    TagValue,
    WorkOrder,
)
from fsmes.services import analysis, workorders
from fsmes.services import oee as oee_rules


@pytest.fixture()
def line(session):
    """The seeded demo line (MIX01, PACK01) with a released order to book against."""
    workorders.create(session, code="WO-AN-1", material_code="FG-COLA", quantity=1000)
    workorders.release(session, "WO-AN-1")
    session.flush()
    return "LINE1"


def _equipment(session, code: str) -> Equipment:
    return session.scalar(select(Equipment).where(Equipment.code == code))


def _state(session, code, state, *, minutes_ago, minutes, reason=None):
    unit = _equipment(session, code)
    now = utcnow()
    session.add(
        EquipmentState(
            equipment_id=unit.id,
            state=state,
            reason=reason,
            started_at=now - timedelta(minutes=minutes_ago),
            ended_at=now - timedelta(minutes=minutes_ago - minutes),
        )
    )
    session.flush()


def _book(session, code, *, good=0.0, scrap=0.0, minutes_ago=1.0):
    unit = _equipment(session, code)
    order = session.scalar(select(WorkOrder).where(WorkOrder.code == "WO-AN-1"))
    session.add(
        ProductionLog(
            work_order_id=order.id,
            operation_id=order.operations[0].id,
            equipment_id=unit.id,
            good_qty=good,
            scrap_qty=scrap,
            source=ProductionSource.OPC,
            ts=utcnow() - timedelta(minutes=minutes_ago),
        )
    )
    session.flush()


# ------------------------------------------------------------------ window


def test_the_window_never_reaches_back_before_the_mes_was_watching(session, line):
    """Time we have no record of is not downtime. Counting it would make a
    machine that started 10 minutes ago report near-zero availability."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=10, minutes=10)
    result = analysis.oee_breakdown(session, line_code=line, hours=8)
    assert result["window"]["clamped"] is True
    assert result["window"]["hours"] == pytest.approx(10 / 60, abs=0.02)
    mixer = next(s for s in result["stations"] if s["code"] == "MIX01")
    assert mixer["availability"] == pytest.approx(1.0, abs=0.02)


def test_every_panel_shares_one_axis(session, line):
    """The charts sit above each other on one page. If OEE clamps to when the
    MES started watching and the trends do not, a five-minute-old MES draws an
    eight-hour axis with everything crushed at the right edge — which reads as
    'idle all day' rather than 'we just started watching'."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=6, minutes=6)
    _book(session, "MIX01", good=120, minutes_ago=3)
    unit = _equipment(session, "MIX01")
    session.add(
        TagValue(equipment_id=unit.id, tag="MIX01.Temperature", value_num=61.0,
                 ts=utcnow() - timedelta(minutes=6))
    )
    session.flush()

    oee = analysis.oee_breakdown(session, line_code=line, hours=8)
    production = analysis.production_trend(session, line_code=line, hours=8)
    trend = analysis.tag_trend(session, "MIX01", hours=8)

    assert production["window"]["start"] == oee["window"]["start"]
    # The tag clamps to its own first reading, which is inside the same window.
    assert trend["window"]["start"] >= oee["window"]["start"]
    assert trend["window"]["start"] > utcnow() - timedelta(hours=1)


def test_a_line_never_observed_reports_unknown_not_zero(session, line):
    result = analysis.oee_breakdown(session, line_code=line, hours=8)
    assert all(station["oee"] is None for station in result["stations"])
    assert result["line_oee"] is None


# --------------------------------------------------------------------- oee


def test_losses_are_named_in_the_units_their_fix_is_measured_in(session, line):
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=60, minutes=45)
    _state(session, "MIX01", EquipmentStateName.DOWN, minutes_ago=15, minutes=15, reason="jam")
    _book(session, "MIX01", good=500, scrap=25, minutes_ago=30)

    station = next(
        s for s in analysis.oee_breakdown(session, line_code=line, hours=8)["stations"] if s["code"] == "MIX01"
    )
    assert station["loss"]["availability_seconds"] == pytest.approx(15 * 60, abs=60)
    assert station["loss"]["quality_units"] == 25  # scrap is a unit loss, not a rate
    assert station["seconds_by_state"]["down"] == pytest.approx(15 * 60, abs=60)


def test_a_station_slower_than_its_rating_reports_below_one(session, line):
    """MIX01 is rated at 4 s a unit. Thirty minutes running makes 450 units at
    rated; make 300 and performance is 300/450, not a number near 1."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=40, minutes=30)
    _book(session, "MIX01", good=300, minutes_ago=20)

    station = next(
        s for s in analysis.oee_breakdown(session, line_code=line, hours=8)["stations"] if s["code"] == "MIX01"
    )
    assert station["performance"] == pytest.approx(4.0 * 300 / 1800, rel=0.02)
    assert station["performance"] < 1.0
    assert station["performance_note"] is None
    assert station["loss"]["performance_units"] == pytest.approx(150, abs=1)


def test_a_station_faster_than_its_rating_says_so_instead_of_reporting_one(session, line):
    """Until 2026-09-14 this reported exactly 1.0, and the lab measured what
    that costs: performance read 1.0 on nine stations across two plants while
    the script said 0.943 to 0.9994. A machine that beats its rated cycle is a
    master-data finding — the rating is slower than the machine — and the
    number now says so instead of being capped away."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=40, minutes=30)
    _book(session, "MIX01", good=900, minutes_ago=20)  # twice the rated rate

    station = next(
        s for s in analysis.oee_breakdown(session, line_code=line, hours=8)["stations"] if s["code"] == "MIX01"
    )
    assert station["performance"] == pytest.approx(2.0, rel=0.02)
    assert "rating is slower than the machine" in station["performance_note"]
    # The loss is signed: it made 450 units more than its rating allows for.
    assert station["loss"]["performance_units"] == pytest.approx(-450, abs=1)
    # And the OEE that follows from it is above 1 rather than quietly trimmed.
    assert station["oee"] > 1.0


def test_a_station_with_no_rated_cycle_reports_unknown_and_says_why(session, line):
    """A blank cycle time in the worksheet is the commissioning answer for "we
    do not know". It must read unknown, never 1.0 and never 0."""
    _equipment(session, "MIX01").ideal_cycle_seconds = None
    session.flush()
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=40, minutes=30)
    _book(session, "MIX01", good=300, minutes_ago=20)

    station = next(
        s for s in analysis.oee_breakdown(session, line_code=line, hours=8)["stations"] if s["code"] == "MIX01"
    )
    assert station["performance"] is None
    assert station["performance_note"] == oee_rules.NO_RATING
    assert station["oee"] is None
    assert station["loss"]["performance_units"] is None


def test_the_headline_is_the_constraint_not_an_average(session, line):
    """Averaging a line's OEE hides the station worth fixing."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=60, minutes=60)
    _book(session, "MIX01", good=600, minutes_ago=30)
    _state(session, "PACK01", EquipmentStateName.RUNNING, minutes_ago=60, minutes=30)
    _state(session, "PACK01", EquipmentStateName.DOWN, minutes_ago=30, minutes=30)
    _book(session, "PACK01", good=200, minutes_ago=30)

    result = analysis.oee_breakdown(session, line_code=line, hours=8)
    assert result["constraint"] == "PACK01"
    assert result["line_oee"] == min(s["oee"] for s in result["stations"] if s["oee"] is not None)


# ---------------------------------------------------------------- downtime


def test_downtime_shares_never_exceed_one(session, line):
    """Rounding each bucket before dividing lets a single reason report 101%."""
    _state(session, "MIX01", EquipmentStateName.DOWN, minutes_ago=30, minutes=0.35, reason=None)
    _state(session, "PACK01", EquipmentStateName.DOWN, minutes_ago=20, minutes=0.35, reason=None)

    result = analysis.downtime_pareto(session, line_code=line, hours=8)
    assert result["reasons"]
    for bucket in result["reasons"]:
        assert 0.0 <= bucket["share"] <= 1.0
        assert 0.0 <= bucket["cumulative"] <= 1.0
    assert result["reasons"][-1]["cumulative"] == pytest.approx(1.0, abs=1e-4)


def test_unlabelled_downtime_is_named_not_hidden(session, line):
    """On a line fed by OPC alone nothing labels a stop. A pareto that quietly
    files that under 'other' is how a plant convinces itself it has data."""
    _state(session, "MIX01", EquipmentStateName.DOWN, minutes_ago=30, minutes=10)
    _state(session, "PACK01", EquipmentStateName.DOWN, minutes_ago=20, minutes=2, reason="blade change")

    result = analysis.downtime_pareto(session, line_code=line, hours=8)
    reasons = {bucket["reason"]: bucket for bucket in result["reasons"]}
    assert "unlabelled" in reasons
    assert reasons["unlabelled"]["machines"] == {"MIX01": pytest.approx(600, abs=5)}
    assert "blade change" in reasons
    assert result["reasons"][0]["reason"] == "unlabelled"  # worst first
    assert result["unlabelled_share"] > 0.8


def test_no_downtime_is_reported_as_no_downtime(session, line):
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=30, minutes=30)
    result = analysis.downtime_pareto(session, line_code=line, hours=8)
    assert result["reasons"] == []
    assert result["total_seconds"] == 0.0


# ---------------------------------------------------------------- timeline


def test_timeline_intervals_are_clipped_to_the_window(session, line):
    """The client lays out directly from these, so anything off-screen would
    push every other interval into the wrong place."""
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=600, minutes=600)
    result = analysis.state_timeline(session, line_code=line, hours=1)
    [interval] = next(m for m in result["machines"] if m["code"] == "MIX01")["intervals"]
    assert interval["start"] >= result["window"]["start"]
    assert interval["end"] <= result["window"]["end"]


def test_an_open_interval_is_marked_open(session, line):
    unit = _equipment(session, "MIX01")
    session.add(
        EquipmentState(
            equipment_id=unit.id,
            state=EquipmentStateName.RUNNING,
            started_at=utcnow() - timedelta(minutes=5),
        )
    )
    session.flush()
    result = analysis.state_timeline(session, line_code=line, hours=8)
    [interval] = next(m for m in result["machines"] if m["code"] == "MIX01")["intervals"]
    assert interval["open"] is True
    assert interval["end"] == result["window"]["end"]


def test_named_machines_are_found_across_the_whole_plant(session, line):
    """A factory has several lines. Naming a machine on another line must
    return it, not silently nothing - the scored run of a five-line factory
    lost four of five breakdowns to exactly that."""
    other = Equipment(code="LINE2", name="Second line", level=EquipmentLevel.WORK_CENTER)
    session.add(other)
    session.flush()
    unit = Equipment(code="EXT01", name="Extruder", level=EquipmentLevel.WORK_UNIT, parent_id=other.id)
    session.add(unit)
    session.flush()
    _state(session, "EXT01", EquipmentStateName.DOWN, minutes_ago=30, minutes=10)
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=30, minutes=30)

    result = analysis.state_timeline(session, line_code=line, hours=8, equipment=["MIX01", "EXT01"])
    drawn = {m["code"]: m["intervals"] for m in result["machines"]}
    assert set(drawn) == {"MIX01", "EXT01"}
    assert drawn["EXT01"][0]["state"] == "down"
    assert result["machines_total"] == 2, "the total counts what was named, not one line"

    unasked = analysis.state_timeline(session, line_code=line, hours=8)
    assert "EXT01" not in {m["code"] for m in unasked["machines"]}, "unnamed, the line's own machines"


# --------------------------------------------------------------------- tags


def test_tag_trend_buckets_keep_the_excursion_a_mean_would_hide(session, line):
    unit = _equipment(session, "MIX01")
    now = utcnow()
    for i in range(60):
        session.add(
            TagValue(
                equipment_id=unit.id,
                tag="MIX01.Temperature",
                value_num=120.0 if i == 30 else 60.0,
                ts=now - timedelta(minutes=10) + timedelta(seconds=i),
            )
        )
    session.flush()

    result = analysis.tag_trend(session, "MIX01", hours=1, buckets=4)
    assert result["tag"] == "Temperature"
    assert max(point["max"] for point in result["points"]) == 120.0
    assert max(point["mean"] for point in result["points"]) < 120.0  # the mean smooths it
    assert sum(point["n"] for point in result["points"]) == 60


def test_the_process_value_is_discovered_not_assumed(session, line):
    """Machines call it MotorTemp, WashTemp, FillWeight. Defaulting to
    'Temperature' would silently chart nothing on most real lines."""
    unit = _equipment(session, "MIX01")
    now = utcnow()
    session.add_all(
        [
            TagValue(equipment_id=unit.id, tag="MIX01.GoodCount", value_num=5, ts=now),
            TagValue(equipment_id=unit.id, tag="MIX01.MotorTemp", value_num=71.5, ts=now),
        ]
    )
    session.flush()
    assert analysis.tag_trend(session, "MIX01", hours=1)["tag"] == "MotorTemp"


def test_a_machine_with_no_tag_history_says_so(session, line):
    result = analysis.tag_trend(session, "MIX01", hours=1)
    assert result["tag"] is None and result["points"] == []


# ---------------------------------------------------------------------- api


def test_analysis_endpoints_require_a_signed_in_user(anon):
    for path in ("/analysis/lines", "/analysis/oee", "/analysis/downtime", "/analysis/timeline"):
        assert anon.get(path).status_code == 401


def test_analysis_endpoints_serve_the_page_data(client, session, line):
    _state(session, "MIX01", EquipmentStateName.RUNNING, minutes_ago=30, minutes=30)
    _book(session, "MIX01", good=400, scrap=8, minutes_ago=15)

    oee = client.get("/analysis/oee?hours=8")
    assert oee.status_code == 200
    body = oee.json()
    assert body["line"]["code"] == "LINE1"
    assert {s["code"] for s in body["stations"]} == {"MIX01", "PACK01"}

    assert client.get("/analysis/lines").json()[0]["code"] == "LINE1"
    assert client.get("/analysis/production?hours=8").json()["points"]
    assert client.get("/analysis/downtime?hours=8").status_code == 200
    assert client.get("/analysis/tag/MIX01?hours=8").status_code == 200


def test_an_unreasonable_window_is_refused_rather_than_served(client):
    assert client.get("/analysis/oee?hours=100000").status_code == 422
    assert client.get("/analysis/oee?hours=0").status_code == 422


def test_an_unknown_line_is_a_404(client, line):
    assert client.get("/analysis/oee?line=NOPE").status_code == 404
