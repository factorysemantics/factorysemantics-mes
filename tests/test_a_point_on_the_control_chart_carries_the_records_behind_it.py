"""Click a point on the control chart and the records behind it answer for it.

Scott, 2026-10-05: *"a UI to show me what brief non-LLM dashboards could look
like if I wanted to fully understand why a SPC datapoint is where it is just by
clicking on the SPC chart."* This file pins the read behind that panel -
`GET /quality/spc/{material}/{characteristic}/point/{check_id}` and
`services.spc_point.dossier` - block by block.

Every test is keyed on a fact the fixture *declares*: the gauge that took the
reading, the stop that was open, the maintenance order that was running, the
minute the tag stopped arriving. None of them counts rows inside a loop and
hopes, because a dossier that drew the wrong window would still return a
plausible number of things.

The two claims worth stating out loud, because both are easy to break and
neither shows on a screenshot:

- **Nothing is computed twice.** The control limits are the chart's own, the
  trends are the analysis read's, the gauge's due date is the register's. The
  tests below assert the identity rather than the arithmetic - the dossier's
  control block *is* `spc.chart`'s, and the trend's window *is* `tag_trend`'s.
- **Unknown is never zero.** A reading with no gauge recorded says so; a
  station with no state interval over that instant says so; a tag that stopped
  arriving is buckets of `null` and not a flat line.
"""

from datetime import timedelta

import pytest

from fsmes.db import utcnow
from fsmes.domain import (
    EquipmentLevel,
    EquipmentState,
    EquipmentStateName,
    MaintenanceKind,
    MaintenanceOrder,
    MaintenanceStatus,
    TagValue,
)
from fsmes.services import NotFound, analysis, gauges, masterdata, quality, spc, spc_point

#: Readings that sit still, so the one wild value below is the only thing the
#: rules can be firing on. Twelve of them, which is this plant's
#: `[quality] spc_min_points`, plus a few so the limits are not drawn from the
#: fewest points that mean anything.
STEADY = [11.0, 10.9, 11.1, 11.0, 10.95, 11.05, 11.0, 10.9,
          11.1, 11.0, 10.98, 11.02, 10.96, 11.04]

#: How often the fixture's tags arrive. Five seconds is what this product's
#: shipped configuration stores analogs at - `[controls] opc_history_ratio`
#: times the publish interval - and the panel chooses its bucket against that
#: number, so a fixture on a different cadence would be testing the fallback.
SAMPLE_SECONDS = 5

#: The reading that fires rule 1. Inside no sane control limits and outside the
#: specification (9.5-11.5 °Bx on the demo plant), so it is both a point beyond
#: three sigma and a failed check - which is the point a person clicks.
WILD = 14.0


@pytest.fixture()
def scales(session):
    """Two scales on the register: one just calibrated, one long overdue.

    The overdue one is what makes *did the process move, or did the gauge?* a
    question with two answers in it, and it is the state a real floor is in
    rather more often than a plant likes to admit.
    """
    gauges.register(session, code="SCALE-A", name="Bench scale A", kind="scale",
                    resolution=0.05, interval_days=90, location="MIX01", actor="test")
    gauges.register(session, code="SCALE-B", name="Bench scale B", kind="scale",
                    resolution=0.05, interval_days=90, location="MIX01", actor="test")
    today = utcnow().date()
    gauges.calibrate(session, "SCALE-A", result="pass", performed_by="QA-LEAD",
                     performed_on=today - timedelta(days=5), certificate="C-1", actor="test")
    gauges.calibrate(session, "SCALE-B", result="pass", performed_by="QA-LEAD",
                     performed_on=today - timedelta(days=200), certificate="C-2", actor="test")
    session.flush()
    return ("SCALE-A", "SCALE-B")


def _reading(session, value, gauge=None, equipment=None):
    check, _nc, _signals = quality.record_check(
        session, material_code="FG-COLA", characteristic="brix", value=value,
        gauge_code=gauge, equipment_code=equipment, actor="OP-NIGHT")
    session.flush()
    return check


@pytest.fixture()
def point(session, scales):
    """A wild reading on the mixer, taken with scale A, with a story around it.

    Everything the dossier is meant to find is written here, each with its own
    timestamp relative to the reading, so a test can name the thing it expects
    rather than the number of things:

    * fourteen steady readings before it on scale A, and four on scale B
      reading 0.5 °Bx high - the drifting-instrument story;
    * a changeover that ended forty seconds before the reading, and the machine
      running since - the restart story;
    * `MIX01.Temperature` every five seconds across the window - the rate this
      product's shipped configuration stores analogs at;
    * `MIX01.Pressure` across the first half of it and then nothing - the
      quiet-tag story;
    * a maintenance order opened before the window and still open;
    * the non-conformance the failed reading itself raises.
    """
    mixer = masterdata.get_equipment(session, "MIX01")
    for value in STEADY:
        _reading(session, value, gauge="SCALE-A", equipment="MIX01")
    for value in STEADY[:4]:
        _reading(session, value + 0.5, gauge="SCALE-B", equipment="MIX01")
    check = _reading(session, WILD, gauge="SCALE-A", equipment="MIX01")
    at = check.ts

    # The machine: a changeover that ended forty seconds before the reading,
    # and running ever since. Written as rows rather than through `set_state`,
    # which only ever starts an interval *now*. `setup` is this product's state
    # for a changeover; the word a plant calls it by is on the interval.
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.SETUP,
        reason="Product change", reason_code="changeover",
        started_at=at - timedelta(minutes=6), ended_at=at - timedelta(seconds=40)))
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
        started_at=at - timedelta(seconds=40)))
    # And one stop nobody named, earlier in the same window. House rule 3: it
    # has to come back as unlabelled rather than folded into anything.
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.DOWN,
        started_at=at - timedelta(minutes=9), ended_at=at - timedelta(minutes=8)))
    # The MES has been watching this machine since well before the window, so
    # the twelve minutes the panel asks for are twelve minutes it can answer
    # for. Without this the window clamps to the first interval written, which
    # is correct behaviour and a different test (below).
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.IDLE,
        started_at=at - timedelta(hours=3), ended_at=at - timedelta(minutes=9)))

    # Two tags. One reports across the whole window; the other stops halfway.
    moment = at - timedelta(minutes=12)
    while moment <= at + timedelta(minutes=3):
        session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Temperature",
                             ts=moment, value_num=62.0))
        if moment <= at - timedelta(minutes=6):
            session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Pressure",
                                 ts=moment, value_num=2.6))
        # A structural tag, which must not be drawn: a chart of ReadyBit is a
        # chart of the tag fabric and not of the process.
        session.add(TagValue(equipment_id=mixer.id, tag="MIX01.ReadyBit",
                             ts=moment, value_num=1.0))
        moment += timedelta(seconds=SAMPLE_SECONDS)

    session.add(MaintenanceOrder(
        code="MO-WINDOW", equipment_id=mixer.id, kind=MaintenanceKind.CORRECTIVE,
        status=MaintenanceStatus.IN_PROGRESS, summary="Agitator seal weeping",
        raised_at=at - timedelta(hours=2), started_at=at - timedelta(hours=1)))
    session.flush()
    return check


@pytest.fixture()
def out(session, point):
    return spc_point.dossier(session, "FG-COLA", "brix", point.id)


# ----------------------------------------------------------------- the reading


def test_the_dossier_says_what_was_read_who_read_it_and_against_what(out, point):
    r = out["reading"]
    assert r["check"] == point.id
    assert r["value"] == WILD
    assert r["result"] == "fail"
    assert r["above_spec"] is True and r["below_spec"] is False
    assert r["checked_by"] == "OP-NIGHT"
    assert r["equipment"] == {"code": "MIX01", "name": "Mixer 01"}
    assert (r["lower_spec"], r["upper_spec"]) == (9.5, 11.5)


def test_a_reading_of_another_characteristic_is_refused_rather_than_drawn_under_these_limits(
        session, point):
    """The id alone would find the row; it would not answer the question.

    A dossier that fetched any check it was given would happily draw a torque
    under a brix specification's limits, and every number on the panel would
    then be wrong in a way that looks right.
    """
    quality.create_spec(session, material_code="FG-COLA", characteristic="torque",
                        unit="Nm", min_value=1.0, max_value=3.0, actor="test")
    other, _nc, _signals = quality.record_check(
        session, material_code="FG-COLA", characteristic="torque", value=2.0,
        actor="test")
    session.flush()
    with pytest.raises(NotFound):
        spc_point.dossier(session, "FG-COLA", "brix", other.id)
    with pytest.raises(NotFound):
        spc_point.dossier(session, "FG-COLA", "torque", point.id)


# ------------------------------------------------------- the chart, and the rule


def test_the_rule_that_fired_is_the_charts_own_and_the_limits_are_not_recomputed(
        session, out, point):
    """The identity, not the arithmetic. Recomputing control limits here would
    give slightly different numbers from the chart the reader is looking at,
    which is the whole reason a signal stores the window it fired on."""
    drawn = spc.chart(session, "FG-COLA", "brix")
    assert out["chart"]["control"] == drawn["control"]
    assert out["chart"]["verdict"] == drawn["verdict"]
    assert out["chart"]["on_chart"] is True
    assert out["chart"]["signal"]["rule"] == 1
    assert out["chart"]["reading"] == drawn["n"]


def test_every_point_on_the_chart_names_the_reading_it_is(session, point):
    """The nth dot is a different reading every time a check is recorded; the
    reading is not. The panel asks about the reading."""
    drawn = spc.chart(session, "FG-COLA", "brix")
    assert drawn["points"][-1]["check"] == point.id
    assert all("check" in p for p in drawn["points"])


def test_the_firings_recorded_when_the_reading_arrived_name_the_hold_they_raised(out):
    """Not the same question as what the chart says now: a rule is judged when
    a reading is recorded, and the chart is drawn over a history that has moved
    since."""
    recorded = out["recorded"]
    assert recorded["total"] >= 1
    assert {row["rule"] for row in recorded["signals"]} >= {1}
    assert any(row["nonconformance"] for row in recorded["signals"]), (
        "the first firing of an excursion raises a quality hold, and the panel "
        "has to be able to name it")


def test_a_reading_older_than_the_chart_says_so_rather_than_claiming_the_rules_were_silent(
        session, point, monkeypatch):
    """A chart draws this plant's `[quality] spc_history` readings. One older
    than that is not a point on it, and "no rule fired" would be a different
    claim from "this reading is not on the chart"."""
    monkeypatch.setattr(spc, "history", lambda _session: 2)
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id - 5)
    assert out["chart"]["on_chart"] is False
    assert out["chart"]["signal"] is None
    assert "not one of the points on it" in out["chart"]["note"]


# ------------------------------------------------------------------- the gauge


def test_the_gauge_that_took_the_reading_comes_with_the_date_it_was_last_calibrated(out):
    g = out["gauge"]
    assert g["gauge"]["code"] == "SCALE-A"
    assert g["gauge"]["overdue"] is False
    assert g["gauge"]["never_calibrated"] is False
    assert g["last_calibration"]["performed_by"] == "QA-LEAD"
    assert g["last_calibration"]["result"] == "pass"


def test_the_gauges_due_date_is_the_registers_own_answer_and_not_a_second_one(session, out):
    """One screen calling a gauge due soon while another calls it fine is the
    failure this shares a function to prevent."""
    assert out["gauge"]["gauge"] == gauges.state(session, "SCALE-A")


def test_the_panel_says_whether_this_gauge_can_judge_this_tolerance(out):
    """The rule of ten, against the tolerance this reading was judged by. A
    gauge too coarse for it means the control chart is charting the instrument,
    which would make every other block on the panel beside the point."""
    check = out["gauge"]["resolution_check"]
    assert check["tolerance"] == pytest.approx(2.0)
    assert check["adequate"] is True
    assert "adequate" in check["verdict"]


def test_a_reading_with_no_gauge_recorded_says_not_recorded_rather_than_looking_answered(
        session, point):
    """Null is *not recorded*. The first question anybody asks about a point on
    a control chart must not come back blank as though it had been answered."""
    bare = _reading(session, 11.0, equipment="MIX01")
    out = spc_point.dossier(session, "FG-COLA", "brix", bare.id)
    assert out["gauge"]["gauge"] is None
    assert "not the same as no gauge having taken it" in out["gauge"]["note"]


def test_the_other_gauge_in_the_same_hour_is_listed_with_how_far_it_sat_from_this_one(out):
    """The comparison that separates *the process moved* from *the gauge
    moved*, and the only one records alone can make."""
    rows = {row["gauge"]: row for row in out["neighbours"]["by_gauge"]}
    assert set(rows) == {"SCALE-A", "SCALE-B"}
    assert rows["SCALE-A"]["this_reading"] is True
    assert rows["SCALE-A"]["difference_to_this_gauge"] is None
    # Scale B read half a degree high on the four it took.
    assert rows["SCALE-B"]["readings"] == 4
    assert rows["SCALE-B"]["difference_to_this_gauge"] is not None
    assert out["neighbours"]["total"] == sum(r["readings"] for r in rows.values())


def test_the_gauge_comparison_says_it_bounds_the_question_rather_than_settling_it(out):
    """They measured different pieces. A panel that presented this as proof
    would be the most convincing wrong thing on the screen."""
    assert "different pieces" in out["neighbours"]["note"]
    assert out["neighbours"]["coverage"] == "absent"


# ----------------------------------------------------------------- the machine


def test_the_machine_says_what_it_was_doing_and_what_it_had_just_come_out_of(out):
    """A point above the limit forty seconds after a changeover ended is a
    point about the changeover, and the panel has to be able to say so."""
    m = out["machine"]
    assert m["state"]["state"] == "running"
    assert m["previous"]["state"] == "setup"
    assert m["previous"]["reason"] == "Product change"
    assert m["seconds_since_previous_ended"] == pytest.approx(40, abs=2)


def test_a_reading_with_no_station_recorded_has_no_machine_to_ask_about(session, scales):
    """A person with a gauge records no station, and working one out from the
    order's route would name a machine nobody stood at."""
    bare = _reading(session, 11.0, gauge="SCALE-A")
    out = spc_point.dossier(session, "FG-COLA", "brix", bare.id)
    assert out["machine"] is None
    assert out["timeline"] is None
    assert out["stops"] is None
    assert out["coverage"] is None
    assert "not a question this panel can answer" in out["coverage_note"]


def test_an_unlabelled_stop_in_the_window_is_reported_as_unlabelled(out):
    """House rule 3, on one window. A stop filed under a reason nothing
    observed is how a plant convinces itself it has data it has not got."""
    stops = out["stops"]
    assert stops["total"] >= 2            # the changeover and the unnamed stop
    assert stops["unlabelled"] >= 1
    assert stops["labelled"] + stops["unlabelled"] == stops["total"]
    assert any(row["state"] == "down" and not row["reason"] for row in stops["stops"])


def test_the_stops_are_the_same_intervals_the_timeline_beside_them_is_drawn_from(out):
    """Two lists of the same minutes that could disagree are two lists."""
    drawn = [i for row in out["timeline"]["machines"] for i in row["intervals"]
             if i["state"] != "running"]
    assert out["stops"]["stops"] == drawn


def test_the_timeline_is_the_analysis_reads_own_envelope_over_this_window(session, out, point):
    """Reused rather than re-queried, so the Gantt on the analysis screen and
    the one in this panel cannot draw the same minutes differently."""
    window = (point.ts - timedelta(minutes=spc_point.BEFORE_MINUTES),
              point.ts + timedelta(minutes=spc_point.AFTER_MINUTES))
    expected = analysis.state_timeline(session, equipment=["MIX01"], window=window, limit=1)
    assert out["timeline"]["machines"] == expected["machines"]
    assert out["timeline"]["window"] == expected["window"]
    assert out["timeline"]["window"]["start"] == window[0]
    assert out["timeline"]["window"]["clamped"] is False
    assert out["timeline"]["ledger"]["equipment"] == "MIX01"


# -------------------------------------------------------------------- the tags


def test_the_stations_analogs_are_drawn_and_its_structural_tags_are_not(out):
    """Whatever the station published numerically that is not one of the tags
    every machine carries. A chart of `ReadyBit` is a chart of the tag fabric
    and tells a process engineer nothing."""
    names = {trend["tag"] for trend in out["tags"]["trends"]}
    assert names == {"Temperature", "Pressure"}
    assert out["tags"]["total"] == 2
    assert out["tags"]["shown"] == 2


def test_each_analog_carries_the_readings_own_time_as_a_marker(out, point):
    """The whole reason the picture answers the question: a pressure dip and a
    fill weight are an explanation only if they are read against each other."""
    for trend in out["tags"]["trends"]:
        assert trend["markers"] == [{"t": point.ts, "label": "this reading"}]


def test_a_tag_that_stopped_arriving_is_buckets_with_no_reading_and_not_a_flat_line(out):
    """A stale value is not a steady one. The pressure stops halfway through
    the window; every bucket after that has to come back `null`, which is what
    breaks the line rather than drawing one through the silence."""
    trends = {trend["tag"]: trend for trend in out["tags"]["trends"]}
    pressure, temperature = trends["Pressure"], trends["Temperature"]
    assert pressure["buckets_with_no_reading"] > 0
    assert temperature["buckets_with_no_reading"] == 0
    # The silence is the second half of the window, drawn and not interpolated.
    tail = [p for p in pressure["points"] if p["mean"] is None]
    assert len(tail) == pressure["buckets_with_no_reading"]
    assert all(p["n"] == 0 for p in tail)
    assert pressure["coverage"] < temperature["coverage"] == 1.0


def test_an_analogs_coverage_is_the_share_of_the_window_it_spoke_in_with_its_denominator(out):
    """A figure with no denominator is a number nobody can check, and this one
    is not the machine's watched share: a tag can go quiet on a machine nobody
    lost sight of."""
    for trend in out["tags"]["trends"]:
        assert 0 <= trend["coverage"] <= 1
        assert f"of {trend['buckets']} buckets" in trend["coverage_note"]
        assert trend["bucket_seconds"] > 0


def test_a_station_that_published_nothing_in_the_window_draws_nothing_and_says_so(
        session, scales):
    """Not a flat line at zero. A station with no process values in the window
    has none, which is a different fact from values of zero."""
    quiet = _reading(session, 11.0, gauge="SCALE-A", equipment="PACK01")
    out = spc_point.dossier(session, "FG-COLA", "brix", quiet.id)
    assert out["tags"]["trends"] == []
    assert out["tags"]["total"] == 0


def test_the_tag_list_states_how_many_of_the_stations_signals_it_drew(session, point,
                                                                     monkeypatch):
    """A panel showing one of a station's two signals must not look like a
    picture of the station (style rule 4)."""
    monkeypatch.setattr(spc_point, "MOST_TAGS", 1)
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    assert out["tags"]["shown"] == 1
    assert out["tags"]["total"] == 2
    assert "1 of the 2 signals" in out["tags"]["note"]


# ----------------------------------------------- what else was in the window


def test_a_maintenance_order_open_when_the_reading_was_taken_is_in_the_window(out):
    """Touching the window, not raised in it: a job that started an hour
    earlier and was still open is the answer, and a scope of twelve minutes
    would never find it."""
    codes = [row["code"] for row in out["maintenance"]["orders"]]
    assert codes == ["MO-WINDOW"]
    assert out["maintenance"]["total"] == 1
    assert out["maintenance"]["orders"][0]["status"] == "in_progress"


def test_a_finding_raised_in_the_window_is_listed_with_the_scope_it_was_found_by(out):
    """A non-conformance carries an order and a lot and never a station, so
    narrowing this to one machine would mean inventing the link. The answer
    says which scope it used rather than looking like the filler's own list."""
    found = out["findings"]
    assert found["total"] >= 1
    assert any(row["status"] == "open" for row in found["nonconformances"])
    assert "never a station" in found["scope"]
    assert found["coverage"] == "absent"


# ------------------------------------------------------------- what we know


def test_every_block_states_its_own_coverage_or_that_it_has_no_such_figure(out):
    """Rule 2 of the chart contract, applied to a panel: a figure, "could not
    be computed", and "this is a list of records" are three different facts and
    the reader is entitled to tell them apart."""
    records = [out["neighbours"], out["maintenance"], out["findings"], out["recorded"]]
    for block in records:
        assert block["coverage"] == "absent"
        assert block["coverage_note"]
    # The blocks that *are* about a stretch of a machine's time carry a figure.
    assert 0 <= out["timeline"]["coverage"] <= 1
    assert out["coverage"] == out["timeline"]["coverage"]
    for trend in out["tags"]["trends"]:
        assert 0 <= trend["coverage"] <= 1


def test_the_window_is_the_ten_minutes_before_the_reading_and_the_two_after(out, point):
    window = out["window"]
    assert window["at"] == point.ts
    assert window["before_minutes"] == spc_point.BEFORE_MINUTES
    assert window["after_minutes"] == spc_point.AFTER_MINUTES
    assert window["start"] == point.ts - timedelta(minutes=10)
    assert window["end"] == point.ts + timedelta(minutes=2)


def test_a_reader_may_ask_for_a_window_of_their_own(session, point):
    """The window a reader wants around one point is a property of the question
    they are asking, the way `/analysis/tag?hours=` is."""
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id,
                            before_minutes=2.0, after_minutes=0.0,
                            neighbour_hours=0.001)
    assert out["window"]["start"] == point.ts - timedelta(minutes=2)
    assert out["window"]["end"] == point.ts
    assert out["neighbours"]["hours"] == 0.001


# ------------------------------------------------------------------ the route


def test_the_route_answers_the_same_envelope_the_service_built(client, session, point):
    """Decision 0023: one answer, read the same way by a screen and - when it
    is its own handoff - by an agent."""
    reply = client.get(f"/quality/spc/FG-COLA/brix/point/{point.id}")
    assert reply.status_code == 200
    body = reply.json()
    assert body["reading"]["check"] == point.id
    assert body["chart"]["signal"]["rule"] == 1
    assert body["gauge"]["gauge"]["code"] == "SCALE-A"
    assert body["tags"]["total"] == 2


def test_an_operator_may_read_a_point_the_way_they_read_the_chart(supervisor, point):
    """It is a read of the plant's own records; `plant.read` is the gate on
    every read in this product and this is not a special one."""
    reply = supervisor.get(f"/quality/spc/FG-COLA/brix/point/{point.id}")
    assert reply.status_code == 200


def test_a_reading_this_chart_does_not_have_is_a_404_and_not_an_empty_panel(client, point):
    reply = client.get(f"/quality/spc/FG-COLA/brix/point/{point.id + 9999}")
    assert reply.status_code == 404


def test_the_route_takes_the_window_as_query_parameters(client, point):
    reply = client.get(f"/quality/spc/FG-COLA/brix/point/{point.id}",
                       params={"before_minutes": 3, "after_minutes": 1})
    assert reply.status_code == 200
    assert reply.json()["window"]["before_minutes"] == 3


# ------------------------------------------------------------- the two cadences


def test_the_stored_analog_cadence_is_the_same_number_the_agent_subscribes_at():
    """Two readers of one rule, held against each other across a layer.

    The identity - analog history is stored at the publish interval times
    `[controls] opc_history_ratio`, floored at `opc_min_history_ms` - lives in
    `integrations.opc.agent.history_interval_ms`, which is where the agent
    applies it. A service may not import the agent (the ladder in
    `test_core_purity`, and the agent is a separate process besides), so
    `coverage.analog_sample_interval` says it again. Two statements of one rule
    drift apart unless something fails when they do. This is that something.
    """
    from fsmes.config import get_settings
    from fsmes.integrations.opc.agent import history_interval_ms
    from fsmes.services import coverage

    seconds, source = coverage.analog_sample_interval()
    assert seconds == pytest.approx(history_interval_ms(get_settings()) / 1000.0)
    assert "not a measurement" in source
    # And it is NOT the publish interval, which is the confusion that would
    # have a trend report nineteen samples missing out of every twenty.
    assert seconds != coverage.sample_interval()[0]


def test_a_plant_with_no_configured_cadence_falls_back_and_says_it_did(session, point,
                                                                       monkeypatch):
    """A plant fed by hand or over MQTT has no rate to choose a bucket from.
    The panel uses this product's default and prints that it did, rather than
    presenting a bucket width as a property of the plant."""
    monkeypatch.setattr("fsmes.services.coverage.analog_sample_interval",
                        lambda: (None, None))
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    trend = out["tags"]["trends"][0]
    assert trend["buckets"] == spc_point.TAG_BUCKETS
    assert trend["sample_interval_seconds"] is None
    assert "no configured cadence" in trend["coverage_note"]


def test_the_bucket_is_wide_enough_that_an_ordinary_cadence_is_not_a_hole(session, point):
    """Two stored samples to a bucket. A plant that drops one sample now and
    then must not read as a plant with holes in its history, or the one thing
    this block exists to show - a tag that actually stopped - is lost in noise
    nobody can act on."""
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    trend = next(t for t in out["tags"]["trends"] if t["tag"] == "Temperature")
    assert trend["bucket_seconds"] >= SAMPLE_SECONDS * 2
    assert trend["buckets_with_no_reading"] == 0


# ----------------------------- what looking at real data found (2026-10-05)
#
# Two of these came off the first throwaway bottling replay and neither was
# visible from the code. They are here rather than in the browser file because
# both are facts about the read.


def test_a_tag_with_nothing_in_the_window_drawn_reports_unknown_and_not_zero(
        session, scales):
    """Two things look identical here, and the panel has to say so.

    A value that has not changed publishes nothing on a fabric that notifies on
    change; a window the MES had not started watching holds nothing either.
    Either way the chart is empty - and on the bottling replay the filler's
    fill-weight **setpoint**, set once when the order started, was drawn exactly
    like a tag nobody had been watching, with a coverage of **0 %** on it. That
    was the panel's fault and not the plant's: `null` is *could not be
    computed*, zero is *nobody watched any of it*, and those are different
    claims.

    `PACK01` is the reproduction: this MES holds no state interval for it at
    all, so the window realised is empty however much of one was asked for.
    """
    from fsmes.domain import TagValue
    from fsmes.services import masterdata

    packer = masterdata.get_equipment(session, "PACK01")
    check = _reading(session, 11.0, gauge="SCALE-A", equipment="PACK01")
    session.add(TagValue(equipment_id=packer.id, tag="PACK01.Setpoint",
                         ts=check.ts - timedelta(minutes=4), value_num=11.0))
    session.flush()

    out = spc_point.dossier(session, "FG-COLA", "brix", check.id)
    quiet = next(t for t in out["tags"]["trends"] if t["tag"] == "Setpoint")
    assert quiet["samples"] == 0
    assert quiet["coverage"] is None, "silence is reported as zero coverage"
    assert "has not changed publishes nothing" in quiet["coverage_note"]
    assert "had not started watching" in quiet["coverage_note"]
    last = quiet["last_before_window"]
    assert last is not None and last["value"] == 11.0
    assert last["seconds_before_window"] > 0


def test_an_analogs_buckets_are_laid_against_the_window_the_trend_actually_drew(
        session, point, scales):
    """The first thing real data found, and it was arithmetic.

    `tag_trend` clamps its start to when the tag was first recorded. A bucket
    count worked out against the window that was *asked for* then lands a grid
    of the wrong width on the window that comes back - on the replay, seventy-two
    buckets across a hundred and seventy-two seconds, so every other bucket
    read as a bucket nothing arrived in and the tag looked half dead.
    """
    from fsmes.domain import TagValue
    from fsmes.services import masterdata

    mixer = masterdata.get_equipment(session, "MIX01")
    check = _reading(session, 11.0, gauge="SCALE-A", equipment="MIX01")
    # A tag whose history begins only two minutes before the reading, against a
    # ten-minute request: the window realised is a third of the window asked
    # for, and - because the tag reports right across it - every bucket in it
    # holds a sample. Before the fix it did not: the grid was 72 buckets wide
    # on a window a fifth that long, and every other bucket read as empty.
    moment = check.ts - timedelta(minutes=2)
    while moment <= check.ts + timedelta(minutes=3):
        session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Late",
                             ts=moment, value_num=5.0))
        moment += timedelta(seconds=SAMPLE_SECONDS)
    session.flush()

    out = spc_point.dossier(session, "FG-COLA", "brix", check.id)
    late = next(t for t in out["tags"]["trends"] if t["tag"] == "Late")
    assert late["samples"] > 0
    assert late["bucket_seconds"] >= SAMPLE_SECONDS * 2, (
        "the grid is finer than the cadence, so an arriving tag reads as a "
        f"tag with holes in it: {late['bucket_seconds']} s buckets")
    assert late["buckets_with_no_reading"] == 0
    assert late["coverage"] == 1.0


# --------------------------------------------------- the rest of the line


@pytest.fixture()
def line(session, point):
    """The packer beside the mixer, changing over inside the reading's window.

    The story #143 and #147 both found and neither panel could tell: the thing
    that explains the reading happened on the machine next to it. Three facts
    are written here and each one is a test below:

    * `PACK01` in setup, ended three minutes before the reading — inside the
      window, and the row a reader has to see;
    * `PACK01` down for two minutes an hour earlier — outside the window, and a
      row that must not appear;
    * `AUX01`, a third work unit under the same line that this material's
      routing says nothing about and that recorded nothing at all.
    """
    packer = masterdata.get_equipment(session, "PACK01")
    masterdata.create_equipment(session, code="AUX01", name="Auxiliary 01",
                                level=EquipmentLevel.WORK_UNIT, parent_code="LINE1",
                                actor="test")
    at = point.ts
    session.add_all([
        EquipmentState(equipment_id=packer.id, state=EquipmentStateName.IDLE,
                       started_at=at - timedelta(hours=3),
                       ended_at=at - timedelta(minutes=8)),
        EquipmentState(equipment_id=packer.id, state=EquipmentStateName.SETUP,
                       reason="Product change", reason_code="changeover",
                       started_at=at - timedelta(minutes=8),
                       ended_at=at - timedelta(minutes=3)),
        EquipmentState(equipment_id=packer.id, state=EquipmentStateName.RUNNING,
                       started_at=at - timedelta(minutes=3)),
        # An hour earlier, well outside the twelve minutes the panel asks for.
        EquipmentState(equipment_id=packer.id, state=EquipmentStateName.DOWN,
                       reason="Infeed jam", started_at=at - timedelta(hours=1),
                       ended_at=at - timedelta(minutes=58)),
    ])
    session.flush()
    return packer


def test_a_changeover_on_the_station_beside_this_one_is_in_the_window_with_its_name_on_it(
        session, point, line):
    """The block this panel was missing. A reading at the mixer whose cause is
    the packer changing over five minutes earlier is a reading about the
    changeover, and a panel scoped to one station cannot say so."""
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    block = out["line"]
    assert block["parent"] == {"code": "LINE1", "name": "Packaging Line 1"}
    drawn = {station["code"]: station for station in block["stations"]}
    assert "PACK01" in drawn, block["note"]
    packer = drawn["PACK01"]
    # The station's name is on the row: a code with no name is a row a reader
    # has to look up elsewhere.
    assert packer["name"] == "Packer 01"
    assert len(packer["changeovers"]) == 1
    assert packer["changeovers"][0]["reason"] == "Product change"
    assert packer["changeovers"][0]["state"] == "setup"


def test_the_station_itself_is_not_in_the_block_about_the_rest_of_the_line(
        session, point, line):
    """Its own stops are the block above. Drawing them twice would have a
    reader counting one changeover as two."""
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    codes = ([station["code"] for station in out["line"]["stations"]]
             + [station["code"] for station in out["line"]["quiet"]])
    assert "MIX01" not in codes
    assert out["line"]["total"] == 2        # PACK01 and AUX01


def test_a_stop_outside_the_window_is_not_drawn_in_it(session, point, line):
    """The packer jammed an hour before this reading. That is a true record and
    not an answer to this question, and a block that reached for it would make
    every reading look explained.

    Two stretches of the packer's are inside the window and both are drawn: the
    tail of the idle it was in when the window opened, clipped to the window,
    and the changeover after it. The jam is a third record and is not one.
    """
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    drawn = {station["code"]: station for station in out["line"]["stations"]}
    reasons = [row["reason"] for row in drawn["PACK01"]["stops"]]
    assert "Infeed jam" not in reasons
    assert drawn["PACK01"]["not_running_total"] == 2
    assert [row["state"] for row in drawn["PACK01"]["stops"]] == ["idle"]
    for row in drawn["PACK01"]["stops"] + drawn["PACK01"]["changeovers"]:
        assert row["start"] >= out["window"]["start"]
        assert row["end"] <= out["window"]["end"]


def test_a_station_that_recorded_nothing_is_named_rather_than_left_out(
        session, point, line):
    """House rule 3, on a list of machines: the auxiliary recorded no stretch
    of not running in this window, so it comes back by name in `quiet` with the
    sentence saying that is a statement about records rather than about the
    machine."""
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    block = out["line"]
    assert [row["code"] for row in block["quiet"]] == ["AUX01"]
    assert block["shown"] == 1
    assert block["with_events"] == 1
    assert "1 of the 2 other work units under LINE1 drawn" in block["note"]
    assert "not about what the machine did" in block["note"]


def test_the_line_is_ordered_by_this_materials_routing_and_says_which_one(
        session, point, line):
    """Routing order where the routing names the station, the rest after it by
    code — and the routing named out loud, because this is a statement about
    how `FG-COLA` is made and not about the line's plumbing.

    Deliberately not read as upstream and downstream: bottling's own routing
    runs Inspect before Fill while the bottles come off the filler to the
    bench, so a step number is a step number here and nothing more.
    """
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    block = out["line"]
    assert block["routings"] == ["RT-COLA"]
    assert "RT-COLA" in block["note"]
    packer = block["stations"][0]
    assert (packer["code"], packer["routing_seq"]) == ("PACK01", 20)
    # The auxiliary is not in the routing, so it has no step number and sorts
    # after the ones that do rather than into a position it never had.
    assert block["quiet"][0]["routing_seq"] is None


def test_each_station_on_the_line_carries_how_much_of_the_window_was_watched(
        session, point, line):
    """A stop list with no watched share is a list that reads complete. The
    figure is the station's own, from the same timeline the intervals came
    from, and the block around them says it is a list of records and has no
    figure of its own."""
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    block = out["line"]
    assert block["coverage"] == "absent"
    assert "not a rate over a watched one" in block["coverage_note"]
    for station in block["stations"]:
        assert 0 <= station["coverage"] <= 1
        assert station["coverage_note"]


def test_a_station_with_no_line_above_it_says_so_rather_than_drawing_an_empty_block(
        session, scales):
    """A machine hanging outside any line is a real state. *The rest of the
    line* is then not a question this MES can answer about it, which is a
    different sentence from a line that was quiet."""
    masterdata.create_equipment(session, code="LONE01", name="Lone cell 01",
                                level=EquipmentLevel.WORK_UNIT, actor="test")
    reading = _reading(session, 11.0, gauge="SCALE-A", equipment="LONE01")
    out = spc_point.dossier(session, "FG-COLA", "brix", reading.id)
    block = out["line"]
    assert block["parent"] is None
    assert block["stations"] == [] and block["total"] == 0
    assert "hangs under nothing" in block["note"]


def test_a_reading_with_no_station_recorded_says_the_block_cannot_be_drawn(
        session, scales):
    """A person with a gauge records no station, so there is no line either.
    The panel already says that about the machine block; it has to say it here
    too rather than showing an empty list of stations."""
    reading = _reading(session, 11.0, gauge="SCALE-A")
    out = spc_point.dossier(session, "FG-COLA", "brix", reading.id)
    assert out["line"]["parent"] is None
    assert "no station is recorded against this reading" in out["line"]["note"]


def test_a_line_of_one_work_unit_says_there_is_no_rest_of_it(session, point):
    """The demo plant's line holds the mixer and the packer. A plant whose line
    holds one machine has no rest of the line, and saying so is not the same as
    saying the rest of it was quiet."""
    packer = masterdata.get_equipment(session, "PACK01")
    packer.parent = None
    session.flush()
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    assert out["line"]["parent"] == {"code": "LINE1", "name": "Packaging Line 1"}
    assert out["line"]["total"] == 0
    assert "only work unit under LINE1" in out["line"]["note"]


def test_the_block_draws_a_bounded_number_of_stations_and_states_how_many_it_left(
        session, point, line, monkeypatch):
    """A line of sixty work units is a real plant. The ones with something in
    the window are drawn to a ceiling and the count of the rest is stated
    (style rule 4) — the same bound the stops table and the analogs carry."""
    at = point.ts
    for n in range(2):
        unit = masterdata.create_equipment(
            session, code=f"EXTRA0{n}", name=f"Extra 0{n}",
            level=EquipmentLevel.WORK_UNIT, parent_code="LINE1", actor="test")
        session.add(EquipmentState(
            equipment_id=unit.id, state=EquipmentStateName.DOWN, reason="Blocked",
            started_at=at - timedelta(minutes=5), ended_at=at - timedelta(minutes=4)))
    session.flush()
    monkeypatch.setattr(spc_point, "MOST_LINE_STATIONS", 1)
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    block = out["line"]
    assert block["shown"] == 1
    assert block["with_events"] == 3
    assert block["total"] == 4
    assert "2 more of which did and are not drawn" in block["note"]


def test_each_row_says_where_it_sits_relative_to_the_reading_and_the_panel_does_no_sums(
        session, point, line):
    """*Ended five minutes before this reading* is what makes a row on another
    machine mean anything, and it is arithmetic — so the answer carries it and
    the panel lays it out (house rule 6).

    Three different facts and three keys: the reading fell inside the stretch,
    the stretch ended before it, the stretch began after it. The packer's
    changeover ended three minutes before; the idle it was in when the window
    opened ended before that; neither was running when the reading was taken.
    """
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    drawn = {station["code"]: station for station in out["line"]["stations"]}
    changeover = drawn["PACK01"]["changeovers"][0]
    assert changeover["ended_seconds_before"] == pytest.approx(180, abs=2)
    assert changeover["over_the_reading"] is False
    assert changeover["started_seconds_after"] is None
    idle = drawn["PACK01"]["stops"][0]
    assert idle["ended_seconds_before"] > changeover["ended_seconds_before"]


def test_a_stretch_the_reading_fell_inside_says_so_rather_than_claiming_it_ended(
        session, point):
    """A machine still down when the reading was taken is the strongest row on
    the block, and "ended 0 min before" would be the wrong sentence for it."""
    packer = masterdata.get_equipment(session, "PACK01")
    at = point.ts
    session.add(EquipmentState(
        equipment_id=packer.id, state=EquipmentStateName.DOWN, reason="Infeed jam",
        started_at=at - timedelta(minutes=4), ended_at=at + timedelta(minutes=1)))
    session.flush()
    out = spc_point.dossier(session, "FG-COLA", "brix", point.id)
    drawn = {station["code"]: station for station in out["line"]["stations"]}
    jam = [row for row in drawn["PACK01"]["stops"] if row["reason"] == "Infeed jam"]
    assert len(jam) == 1
    assert jam[0]["over_the_reading"] is True
    assert jam[0]["ended_seconds_before"] is None
