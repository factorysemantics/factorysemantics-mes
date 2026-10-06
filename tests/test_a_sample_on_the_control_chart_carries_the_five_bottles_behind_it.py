"""Click a point on an X-bar chart and the five bottles behind it answer for it.

The companion to
`test_a_point_on_the_control_chart_carries_the_records_behind_it.py`, for the
other kind of chart. `GET /quality/spc/{material}/{characteristic}/sample/
{sample_id}` and `services.spc_point.sample_dossier` answer the same six
questions of a SAMPLE, and two of the answers are genuinely different:

- **A point is an average.** An average above its limit because every bottle
  was high and an average above its limit because one bottle was very high are
  two different mornings on a filler. So the panel lists the n readings, each
  one's distance from the sample's own mean, and the one furthest out - and
  says, out loud, that in any sample one reading is always the furthest and on
  a settled process that means nothing.
- **The window is a stretch and not an instant.** Five bottles are measured
  over minutes. The telemetry runs from the minutes before the FIRST of them,
  and every one of the n is marked on each trend, because a window centred on
  the sample's own stamp can leave the pressure dip that caused it outside the
  picture.

Every test is keyed on a fact the fixture declares - which sample is flagged,
which reading is furthest, which stop the machine had just come out of - and
not on the number of things that came back. A dossier drawing the wrong window
would still return a plausible count of rows.
"""

from datetime import timedelta

import pytest

from fsmes.db import utcnow
from fsmes.domain import (
    EquipmentState,
    EquipmentStateName,
    MaintenanceKind,
    MaintenanceOrder,
    MaintenanceStatus,
    TagValue,
)
from fsmes.services import NotFound, gauges, masterdata, quality, spc, spc_point

#: Fourteen settled samples of five, which is past this plant's
#: `[quality] spc_min_points` counted in samples. Every one of them has a mean
#: of 142.0 mm and a range of exactly 2.0, so the limits are flat and the one
#: shifted sample below is the only thing the rules can be firing on.
STEADY_MEAN = 142.0
STEADY = [141.0, 141.5, 142.0, 142.5, 143.0]
SAMPLES = 14

#: The sample after the changeover: the whole five sit high - the planted
#: cause on the bottling floor is a filler that never came back down - and one
#: bottle sits further out than the rest. Mean 145.2, range 3.0: the mean is
#: beyond `X̿ + A2·R̄` and the range is well inside `D4·R̄`, so rule 1 fires
#: about where the average sat and rule 5 says nothing about the spread. Two
#: of the five are also outside the 139-145 mm specification.
SHIFTED = [144.0, 144.5, 145.0, 145.5, 147.0]
SHIFTED_MEAN, SHIFTED_RANGE, FURTHEST = 145.2, 3.0, 147.0

#: How often the fixture's tags arrive - what this product's shipped
#: configuration stores analogs at, so the panel chooses its bucket against a
#: real cadence rather than against the fallback.
SAMPLE_SECONDS = 5


def _spec(session, *, size: int = 5) -> None:
    quality.create_spec(session, material_code="FG-COLA",
                        characteristic="fill_height", unit="mm",
                        min_value=139.0, max_value=145.0, sample_size=size,
                        actor="test")


def _take(session, values, *, gauge="HEIGHT-01", equipment="MIX01"):
    sample, checks, _nc, _signals = quality.record_sample(
        session, material_code="FG-COLA", characteristic="fill_height",
        values=values, gauge_code=gauge, equipment_code=equipment,
        actor="OP-NIGHT")
    session.flush()
    return sample, checks


@pytest.fixture()
def height_gauge(session):
    """A height gauge on the register, calibrated a fortnight ago."""
    gauges.register(session, code="HEIGHT-01", name="Bench height gauge",
                    kind="height gauge", resolution=0.1, interval_days=180,
                    location="MIX01", actor="test")
    gauges.calibrate(session, "HEIGHT-01", result="pass", performed_by="QA-LEAD",
                     performed_on=utcnow().date() - timedelta(days=14),
                     certificate="CERT-H-1", actor="test")
    session.flush()
    return "HEIGHT-01"


@pytest.fixture()
def sample(session, height_gauge):
    """One shifted sample on the filler, with the story around it written down.

    Fourteen settled samples, then five bottles that all sit high forty
    seconds after a changeover ended. Everything the panel is meant to find
    carries its own stamp relative to the sample:

    * the changeover, ended 40 s before, and the machine running since;
    * a stop nobody labelled, earlier in the same window;
    * `MIX01.Temperature` across the whole window at the stored cadence, and
      `MIX01.Pressure` across the first half of it and then nothing;
    * a maintenance order opened before the window and still open;
    * the non-conformance the two out-of-specification bottles raise.

    The five readings' stamps are then spread over four minutes. `record_sample`
    stamps them together, because the floor posts a sample whole - but the row
    allows them apart, a plant feeding readings in one at a time through an
    integration will have them apart, and *the window is the stretch the
    readings span* is the claim this file has to be able to break.
    """
    _spec(session)
    mixer = masterdata.get_equipment(session, "MIX01")
    for _ in range(SAMPLES):
        _take(session, STEADY)
    row, checks = _take(session, SHIFTED)
    at = row.ts

    for offset, check in zip(range(4, -1, -1), checks, strict=True):
        check.ts = at - timedelta(minutes=offset)
    session.flush()

    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.SETUP,
        reason="Product change", reason_code="changeover",
        started_at=at - timedelta(minutes=12), ended_at=at - timedelta(seconds=40)))
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
        started_at=at - timedelta(seconds=40)))
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.DOWN,
        started_at=at - timedelta(minutes=13), ended_at=at - timedelta(minutes=12)))
    # Watched since well before the window, so the minutes the panel asks for
    # are minutes this MES can answer for rather than a clamped scope.
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.IDLE,
        started_at=at - timedelta(hours=3), ended_at=at - timedelta(minutes=13)))

    moment = at - timedelta(minutes=20)
    while moment <= at + timedelta(minutes=4):
        session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Temperature",
                             ts=moment, value_num=62.0))
        if moment <= at - timedelta(minutes=8):
            session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Pressure",
                                 ts=moment, value_num=2.6))
        # Structural, and so not drawn: a chart of ReadyBit is a chart of the
        # tag fabric and not of the process.
        session.add(TagValue(equipment_id=mixer.id, tag="MIX01.ReadyBit",
                             ts=moment, value_num=1.0))
        moment += timedelta(seconds=SAMPLE_SECONDS)

    session.add(MaintenanceOrder(
        code="MO-FILLER", equipment_id=mixer.id, kind=MaintenanceKind.CORRECTIVE,
        status=MaintenanceStatus.IN_PROGRESS, summary="Filler nozzle seal weeping",
        raised_at=at - timedelta(hours=2), started_at=at - timedelta(hours=1)))
    session.flush()
    return row


@pytest.fixture()
def out(session, sample):
    return spc_point.sample_dossier(session, "FG-COLA", "fill_height", sample.id)


# ------------------------------------------------------------------ the sample


def test_the_dossier_says_which_sample_this_is_and_what_the_plan_asked_for(out, sample):
    s = out["sample"]
    assert out["subject"] == "sample", "the panel must not infer which kind it is"
    assert s["sample"] == sample.id
    assert (s["n"], s["sample_size"]) == (5, 5)
    assert s["mean"] == SHIFTED_MEAN
    assert s["range"] == SHIFTED_RANGE
    assert s["checked_by"] == "OP-NIGHT"
    assert s["equipment"] == {"code": "MIX01", "name": "Mixer 01"}
    assert (s["lower_spec"], s["upper_spec"]) == (139.0, 145.0)
    assert s["note"] is None, "five readings where the plan asks for five"


def test_a_sample_of_another_characteristic_is_refused_rather_than_drawn_under_these_limits(
        session, sample):
    """The id alone would find the row; it would not answer the question. A
    dossier that fetched any sample it was given would draw five torques under
    a fill-height specification's limits, and every figure on the panel would
    then be wrong in a way that looks right."""
    quality.create_spec(session, material_code="FG-COLA", characteristic="cap_torque",
                        unit="Nm", min_value=1.0, max_value=3.0, sample_size=5,
                        actor="test")
    other, _checks, _nc, _signals = quality.record_sample(
        session, material_code="FG-COLA", characteristic="cap_torque",
        values=[2.0, 2.1, 2.0, 1.9, 2.0], actor="test")
    session.flush()
    with pytest.raises(NotFound):
        spc_point.sample_dossier(session, "FG-COLA", "fill_height", other.id)
    with pytest.raises(NotFound):
        spc_point.sample_dossier(session, "FG-COLA", "cap_torque", sample.id)


# ------------------------------------------------------- the readings behind it


def test_the_readings_are_listed_in_the_order_they_were_taken_with_their_own_stamps(out):
    rows = out["readings"]["readings"]
    assert [r["position"] for r in rows] == [1, 2, 3, 4, 5]
    assert [r["value"] for r in rows] == SHIFTED
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)
    assert {r["gauge"] for r in rows} == {"HEIGHT-01"}
    assert out["readings"]["total"] == 5
    assert (out["readings"]["min"], out["readings"]["max"]) == (144.0, 147.0)


def test_each_reading_says_how_far_it_sat_from_the_samples_own_mean(out):
    """Signed, because high and low are different findings, and the absolute
    figure beside it because that is what *furthest* is measured on."""
    rows = {r["value"]: r for r in out["readings"]["readings"]}
    assert rows[144.0]["difference_to_mean"] == pytest.approx(-1.2)
    assert rows[FURTHEST]["difference_to_mean"] == pytest.approx(1.8)
    assert rows[FURTHEST]["distance_from_mean"] == pytest.approx(1.8)
    assert rows[145.0]["distance_from_mean"] == pytest.approx(0.2)


def test_the_bottle_that_pulled_the_average_is_named_and_the_others_are_not(out):
    """The question the panel exists to answer, keyed on identity: it is the
    147.0 mm bottle that is furthest out, not "one of them"."""
    rows = {r["value"]: r for r in out["readings"]["readings"]}
    assert rows[FURTHEST]["furthest"] is True
    assert [r["value"] for r in out["readings"]["readings"] if r["furthest"]] == [FURTHEST]
    assert out["readings"]["furthest"] == [rows[FURTHEST]["check"]]


def test_the_table_says_that_furthest_from_the_mean_is_arithmetic_and_not_blame(out):
    """In a sample of five one bottle is always the furthest from the mean, and
    on a settled process that means nothing whatever. A panel that named one
    without saying so would have a shift leader asking about a bottle."""
    said = out["readings"]["note"]
    assert "always the furthest" in said
    assert "where the MEAN sat against the centre line" in said


def test_a_sample_whose_readings_are_all_equal_names_none_of_them_as_furthest(
        session, sample):
    """Five identical readings have no furthest one. Naming the first of them
    would be inventing a finding out of the order they were typed in."""
    flat, _checks = _take(session, [142.0] * 5)
    out = spc_point.sample_dossier(session, "FG-COLA", "fill_height", flat.id)
    assert out["readings"]["furthest"] == []
    assert all(r["furthest"] is False for r in out["readings"]["readings"])
    assert "none of them is the one that pulled it" in out["readings"]["note"]


def test_two_readings_equally_far_out_are_both_named(session, sample):
    """A sample where two bottles sat equally far out is a different fact from
    one where a single bottle did, and the table has to be able to say which
    of the two it is."""
    tied, _checks = _take(session, [141.0, 142.0, 142.0, 142.0, 143.0])
    out = spc_point.sample_dossier(session, "FG-COLA", "fill_height", tied.id)
    assert [r["value"] for r in out["readings"]["readings"] if r["furthest"]] == [141.0, 143.0]
    assert len(out["readings"]["furthest"]) == 2
    assert "2 of them sit equally far out and all are named" in out["readings"]["note"]


def test_a_bottle_outside_the_specification_is_marked_in_its_own_row(out):
    """Beyond the control limit and outside the tolerance are two different
    findings about the same sample, and the second one is per bottle."""
    rows = {r["value"]: r for r in out["readings"]["readings"]}
    assert rows[FURTHEST]["above_spec"] is True
    assert rows[145.5]["above_spec"] is True
    assert rows[144.0]["above_spec"] is False
    assert all(r["below_spec"] is False for r in out["readings"]["readings"])
    assert rows[FURTHEST]["result"] == "fail"


# ----------------------------------------------- the chart, and what fired on it


def test_both_halves_of_the_chart_are_the_charts_own_and_nothing_is_recomputed(
        session, out, sample):
    """The identity, not the arithmetic. Recomputing control limits here would
    put slightly different numbers under the chart the reader is looking at,
    which is the whole reason the chart is the one place they are worked out."""
    drawn = spc.chart(session, "FG-COLA", "fill_height")
    assert out["chart"]["on_chart"] is True
    assert out["chart"]["control"] == drawn["control"]
    assert out["chart"]["capability"] == drawn["capability"]
    assert out["chart"]["verdict"] == drawn["verdict"]
    assert out["chart"]["samples"] == drawn["n"] == SAMPLES + 1
    assert out["chart"]["readings"] == drawn["readings"] == (SAMPLES + 1) * 5
    assert out["chart"]["sample"] == SAMPLES + 1, "the newest point, counted from one"
    for key in ("centre", "upper", "lower", "verdict"):
        assert out["chart"]["range_chart"][key] == drawn["range_chart"][key]


def test_the_mean_and_the_range_are_read_off_the_two_halves_rather_than_worked_out_twice(out):
    assert out["chart"]["mean"] == SHIFTED_MEAN
    assert out["chart"]["range"] == SHIFTED_RANGE
    assert out["chart"]["mean"] == out["sample"]["mean"]


def test_the_rule_that_fired_about_the_average_is_named_and_the_spread_is_cleared(out):
    """The planted cause, found: the five bottles all sit high, so rule 1 fires
    about where the mean sat. The spread inside the sample is ordinary, so the
    range chart says nothing - and the panel has to show both, because a reader
    who saw only the firing would not know whether to look at the filler's
    setting or at its consistency."""
    fired = out["chart"]["signals"]
    assert isinstance(fired, list), (
        "a sample can break a rule about its average AND the rule about its "
        "spread, so this is a list and never one firing")
    assert [s["rule"] for s in fired] == [1]
    assert out["chart"]["range_chart"]["flagged"] is False
    assert out["chart"]["range_chart"]["stable"] is True
    assert "in control" in out["chart"]["range_chart"]["verdict"]


def test_a_sample_spread_wider_than_the_rest_is_flagged_on_the_lower_half(session, sample):
    """Rule 5, on the other chart. Five bottles ranging over 12 mm have a mean
    that can sit comfortably in the middle of the limits, which is exactly the
    sample a reader must not be told is fine."""
    wide, _checks = _take(session, [136.0, 140.0, 142.0, 144.0, 148.0])
    out = spc_point.sample_dossier(session, "FG-COLA", "fill_height", wide.id)
    assert out["chart"]["range_chart"]["flagged"] is True
    assert spc.RANGE_RULE in [s["rule"] for s in out["chart"]["signals"]]
    drawn = spc.chart(session, "FG-COLA", "fill_height")
    assert out["chart"]["range_chart"]["verdict"] == drawn["range_chart"]["verdict"]


def test_a_sample_the_chart_leaves_out_says_so_rather_than_claiming_nothing_fired(
        session, sample):
    """A plan that changes leaves samples of the old size behind. "No rule
    fired" is a different claim from "this sample is not on the chart", and the
    second one is the true one."""
    spec = quality.get_spec(session, "FG-COLA", "fill_height")
    spec.sample_size = 3
    session.flush()
    out = spc_point.sample_dossier(session, "FG-COLA", "fill_height", sample.id)
    assert out["chart"]["on_chart"] is False
    assert out["chart"]["signals"] == []
    assert "not one of the" in out["chart"]["note"]
    assert "different number of readings" in out["chart"]["note"]
    assert "holds 5 readings where the specification asks for 3" in out["sample"]["note"]


def test_the_firings_written_down_when_the_sample_arrived_are_found_on_any_of_its_readings(
        out):
    """A sampled characteristic's signal is recorded against the LAST reading
    of the sample, so a panel that asked only about that one would be right
    today and wrong the first time that changes. It asks about all five and
    says which reading each firing was written against."""
    recorded = out["recorded"]
    assert recorded["total"] >= 1
    assert {row["rule"] for row in recorded["signals"]} >= {1}
    assert all(row["check"] is not None for row in recorded["signals"])
    behind = {r["check"] for r in out["readings"]["readings"]}
    assert {row["check"] for row in recorded["signals"]} <= behind


# ----------------------------------------------------------- the gauge and the machine


def test_the_gauge_is_the_samples_own_and_the_register_is_the_one_answer(session, out):
    g = out["gauge"]
    assert g["gauge"] == gauges.state(session, "HEIGHT-01")
    assert g["last_calibration"]["certificate"] == "CERT-H-1"
    assert g["readings_note"] is None, "every reading was taken by the sample's gauge"
    assert g["resolution_check"]["tolerance"] == pytest.approx(6.0)


def test_a_sample_with_no_gauge_recorded_says_so_about_the_sample(session, sample):
    """Null is *not recorded*, and the sentence is about the sample rather than
    about a reading - the panel is answering for five bottles."""
    bare, _checks = _take(session, STEADY, gauge=None)
    out = spc_point.sample_dossier(session, "FG-COLA", "fill_height", bare.id)
    assert out["gauge"]["gauge"] is None
    assert "no gauge is recorded against this sample" in out["gauge"]["note"]


def test_readings_naming_an_instrument_the_sample_does_not_are_reported_rather_than_averaged(
        session, sample):
    """A sample is one act of measurement by one person with one gauge. A
    plant where the readings disagree with the row that ties them together is
    a finding, and averaging over it quietly is how a panel becomes wrong."""
    gauges.register(session, code="HEIGHT-02", name="Second height gauge",
                    kind="height gauge", resolution=0.1, interval_days=180,
                    location="MIX01", actor="test")
    session.flush()
    readings = spc_point._sample_readings(session, sample)
    readings[0].gauge_id = gauges.get(session, "HEIGHT-02").id
    session.flush()
    out = spc_point.sample_dossier(session, "FG-COLA", "fill_height", sample.id)
    assert "HEIGHT-02" in out["gauge"]["readings_note"]
    assert "one act of measurement should not" in out["gauge"]["readings_note"]


def test_the_machine_says_what_it_had_just_come_out_of(out):
    """A sample of five bottles all sitting high forty seconds after a
    changeover ended is a sample about the changeover, and this is the block
    that lets a reader say so in one click."""
    m = out["machine"]
    assert m["state"]["state"] == "running"
    assert m["previous"]["state"] == "setup"
    assert m["previous"]["reason"] == "Product change"
    assert m["seconds_since_previous_ended"] == pytest.approx(40, abs=2)


def test_an_unlabelled_stop_in_the_window_is_reported_as_unlabelled(out):
    """House rule 3, on the window five bottles span."""
    stops = out["stops"]
    assert stops["labelled"] + stops["unlabelled"] == stops["total"]
    assert stops["unlabelled"] >= 1
    assert any(row["state"] == "down" and not row["reason"] for row in stops["stops"])


def test_a_sample_with_no_station_recorded_has_no_machine_to_ask_about(session, sample):
    """A person with a gauge at a bench records no station, and working one out
    from the order's route would name a machine nobody stood at."""
    bare, _checks = _take(session, STEADY, equipment=None)
    out = spc_point.sample_dossier(session, "FG-COLA", "fill_height", bare.id)
    assert out["machine"] is None
    assert out["timeline"] is None and out["stops"] is None
    assert out["coverage"] is None
    assert "not a question this panel can answer" in out["coverage_note"]


# ------------------------------------------------------- the window and the trends


def test_the_window_is_the_stretch_the_five_bottles_span_plus_the_minutes_before(
        out, sample):
    """Not ten minutes either side of the sample's stamp. The five readings
    cover four minutes here, and a window that started ten minutes before the
    LAST of them would cut the first bottle - and the pressure dip before it -
    out of the picture."""
    window = out["window"]
    assert window["at"] == sample.ts
    assert window["last_reading"] == sample.ts
    assert window["first_reading"] == sample.ts - timedelta(minutes=4)
    assert window["spanned_seconds"] == pytest.approx(240.0)
    assert window["start"] == window["first_reading"] - timedelta(
        minutes=spc_point.BEFORE_MINUTES)
    assert window["end"] == sample.ts + timedelta(minutes=spc_point.AFTER_MINUTES)


def test_every_one_of_the_five_readings_is_marked_on_every_trend(out):
    """The whole reason the picture answers the question: a nozzle pressure and
    five fill heights are an explanation only when they are read against each
    other, and one marker in the middle of a sample would hide which bottle
    was measured when."""
    rows = out["readings"]["readings"]
    expected = [{"t": r["ts"], "label": f"reading {r['position']}"} for r in rows]
    assert out["tags"]["trends"], "the station published two analogs in this window"
    for trend in out["tags"]["trends"]:
        assert trend["markers"] == expected


def test_the_stations_analogs_are_drawn_and_its_structural_tags_are_not(out):
    names = {trend["tag"] for trend in out["tags"]["trends"]}
    assert names == {"Temperature", "Pressure"}
    assert (out["tags"]["shown"], out["tags"]["total"]) == (2, 2)


def test_a_tag_that_stopped_arriving_is_buckets_with_no_reading_and_not_a_flat_line(out):
    """A stale value is not a steady one. The pressure stops partway through
    the window; the buckets after that come back `null`, which is what breaks
    the line rather than drawing one through the silence."""
    trends = {trend["tag"]: trend for trend in out["tags"]["trends"]}
    assert trends["Pressure"]["buckets_with_no_reading"] > 0
    assert trends["Temperature"]["buckets_with_no_reading"] == 0
    assert trends["Pressure"]["coverage"] < trends["Temperature"]["coverage"] == 1.0


def test_a_reader_may_ask_for_a_window_of_their_own_around_the_sample(session, sample):
    """The window a reader wants around a point is a property of the question
    they are asking, and it is measured from the bottles rather than the row."""
    out = spc_point.sample_dossier(session, "FG-COLA", "fill_height", sample.id,
                                   before_minutes=2.0, after_minutes=0.0,
                                   neighbour_hours=0.001)
    assert out["window"]["start"] == sample.ts - timedelta(minutes=6)
    assert out["window"]["end"] == sample.ts
    assert out["neighbours"]["hours"] == 0.001


# ------------------------------------------------------------- what else was there


def test_a_maintenance_order_open_while_the_sample_was_taken_is_in_the_window(out):
    codes = [row["code"] for row in out["maintenance"]["orders"]]
    assert codes == ["MO-FILLER"]
    assert out["maintenance"]["total"] == 1


def test_the_hold_the_sample_raised_is_in_the_findings_with_the_scope_it_was_found_by(out):
    """Two bottles outside the tolerance raise one hold for the sample, not one
    per bottle. A non-conformance carries an order and a lot and never a
    station, so the block says which scope it used rather than looking like the
    filler's own list."""
    found = out["findings"]
    assert found["total"] >= 1
    assert any(row["status"] == "open" for row in found["nonconformances"])
    assert "never a station" in found["scope"]
    assert found["coverage"] == "absent"


def test_every_block_states_its_own_coverage_or_that_it_has_no_such_figure(out):
    """Rule 2 of the chart contract, applied to a panel: a figure, "could not
    be computed" and "this is a list of records" are three different facts."""
    for block in (out["readings"], out["neighbours"], out["maintenance"],
                  out["findings"], out["recorded"]):
        assert block["coverage"] == "absent"
        assert block["coverage_note"]
    assert 0 <= out["timeline"]["coverage"] <= 1
    assert out["coverage"] == out["timeline"]["coverage"]


# ------------------------------------------------------------------------ the route


def test_the_route_answers_the_same_envelope_the_service_built(client, sample):
    """Decision 0023: one answer, read the same way by a screen and by an
    agent."""
    reply = client.get(f"/quality/spc/FG-COLA/fill_height/sample/{sample.id}")
    assert reply.status_code == 200
    body = reply.json()
    assert body["subject"] == "sample"
    assert body["sample"]["sample"] == sample.id
    assert [s["rule"] for s in body["chart"]["signals"]] == [1]
    assert [r["value"] for r in body["readings"]["readings"] if r["furthest"]] == [FURTHEST]
    assert body["tags"]["total"] == 2


def test_an_operator_may_read_a_sample_the_way_they_read_the_chart(supervisor, sample):
    """It is a read of the plant's own records; `plant.read` is the gate on
    every read in this product and this is not a special one."""
    reply = supervisor.get(f"/quality/spc/FG-COLA/fill_height/sample/{sample.id}")
    assert reply.status_code == 200


def test_a_sample_this_chart_does_not_have_is_a_404_and_not_an_empty_panel(client, sample):
    reply = client.get(f"/quality/spc/FG-COLA/fill_height/sample/{sample.id + 9999}")
    assert reply.status_code == 404


def test_the_route_takes_the_window_as_query_parameters(client, sample):
    reply = client.get(f"/quality/spc/FG-COLA/fill_height/sample/{sample.id}",
                       params={"before_minutes": 3, "after_minutes": 1})
    assert reply.status_code == 200
    assert reply.json()["window"]["before_minutes"] == 3
