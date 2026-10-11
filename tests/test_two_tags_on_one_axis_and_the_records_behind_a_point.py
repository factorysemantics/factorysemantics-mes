"""Two tags on one window, and a click on one of them that answers for itself.

Scott, 2026-10-11: *"I agree. Clicks first. Run all."* — and the thing the
product's own answer key says a person cannot do today
(`labs/multiplant/bottling/line.json` → `_chain`, link 3): put a cause and
its effect on one axis, over a window that has finished, and click the point
where the second one starts to move.

This file pins the two reads behind that screen — `GET /analysis/trend/{code}`
and `GET /analysis/trend/{code}/point`, `services.trend.series` and
`services.trend.dossier` — block by block. Every test is keyed on something
the fixture *declares*: the minute the second tag starts to fall, the tag that
was never published, the order that was open, the maintenance order that was
raised and never started. None of them counts rows and hopes, because a chart
drawn on the wrong window still comes back with a plausible number of points.

The four claims worth stating out loud, because each is easy to break and
none of them shows on a screenshot:

- **One window for every series.** Two tags resolved separately clamp to their
  own first reading and come back on two axes, and twenty minutes between a
  cause and an effect is then an artefact of the chart. The test below writes
  one tag that starts an hour late and asserts both lines still land on the
  same axis and the same bucket grid.
- **One scale per unit, and never three.** Degrees and bar on one axis is a
  picture of the units and not of the plant, so a third unit is refused with a
  sentence.
- **Unknown is never zero.** A tag that said nothing is buckets of `null` with
  the reason; a tag the machine has never published is named as such, which is
  a different fact; a second with no state interval over it says so.
- **Nothing is recomputed.** The window is `analysis.machine_window`'s, the
  buckets are `tag_trend`'s, the machine's state and the maintenance are the
  same code the control-chart panel reads (`services.moment`).
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
from fsmes.services import (
    Invalid,
    NotFound,
    analysis,
    masterdata,
    moment,
    trend,
)
from fsmes.services import (
    tags as tag_service,
)

#: How often the fixture's tags arrive. Five seconds is what this product's
#: shipped configuration stores analogs at, and the bucket width is chosen
#: against that number, so a fixture on another cadence tests the fallback.
SAMPLE_SECONDS = 5

#: The window the tests draw: two hours, finished, ending an hour ago. Not a
#: trailing span — the whole point of this screen is a window that is over.
SPAN_MINUTES = 120


#: What the tag manifest says these signals are measured in. The demo plant
#: ships no manifest, so the units are declared here the way a plant declares
#: them - in config, next to the tag - and the grouping below is then a fact
#: about the plant rather than about this file.
UNITS = {"MIX01": {"meta": {
    "Temperature": {"unit": "degC", "min": 55.0, "max": 70.0},
    "OutletTemp": {"unit": "degC"},
    "Pressure": {"unit": "bar", "min": 2.0, "max": 3.0},
    "Level": {"unit": "%"},
}, "analog": "Temperature", "tags": []}}


@pytest.fixture(autouse=True)
def units(monkeypatch):
    """The tag manifest, as a plant with its units written down would have it."""
    monkeypatch.setattr(tag_service, "catalog", lambda: UNITS)


@pytest.fixture()
def shift(session):
    """Two hours of a mixer, with a cause and an effect twenty minutes apart.

    Written as rows rather than through the services, because every service
    that writes a state or a tag writes it *now*, and a window that has
    finished cannot be made out of intervals that start at this second.

    What is declared here, so a test can name it:

    * `MIX01.Temperature` across the whole two hours, stepping up 2 °C at the
      sixty-minute mark — the cause;
    * `MIX01.Pressure` in bar, which is a *different unit* and so a second
      scale, starting an hour into the window — the tag that was wired up
      late, and the one that proves both lines still share an axis;
    * `MIX01.Viscosity`, never published at all — the tag asked for that this
      machine has nothing to say about;
    * `MIX01.ReadyBit`, structural, which must not be offered or drawn;
    * a changeover that ended at the twenty-minute mark and running since,
      with one unlabelled stop in the middle — house rule 3;
    * a chiller job raised before the window and never started, which is the
      shape the chain's own key gives link 2.
    """
    mixer = masterdata.get_equipment(session, "MIX01")
    end = utcnow().replace(microsecond=0) - timedelta(hours=1)
    start = end - timedelta(minutes=SPAN_MINUTES)

    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.SETUP,
        reason="Product change", reason_code="changeover",
        started_at=start, ended_at=start + timedelta(minutes=20)))
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.DOWN,
        started_at=start + timedelta(minutes=50),
        ended_at=start + timedelta(minutes=52)))
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
        started_at=start + timedelta(minutes=20),
        ended_at=start + timedelta(minutes=50)))
    session.add(EquipmentState(
        equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
        started_at=start + timedelta(minutes=52), ended_at=end))

    at = start
    half = start + timedelta(minutes=60)
    while at <= end:
        session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Temperature",
                             ts=at, value_num=62.0 if at < half else 64.0))
        if at >= half:
            session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Pressure",
                                 ts=at, value_num=2.6))
        session.add(TagValue(equipment_id=mixer.id, tag="MIX01.ReadyBit",
                             ts=at, value_num=1.0))
        at += timedelta(seconds=SAMPLE_SECONDS)

    session.add(MaintenanceOrder(
        code="MO-CHILLER", equipment_id=mixer.id, kind=MaintenanceKind.PREVENTIVE,
        status=MaintenanceStatus.ASSIGNED, summary="Chiller condenser clean",
        reason="Needs the line stopped; no window this shift",
        raised_at=start - timedelta(hours=3)))
    session.flush()
    return {"start": start, "end": end, "half": half}


@pytest.fixture()
def two(session, shift):
    """Both tags, one window — the read the Trend tab makes."""
    return trend.series(session, "MIX01", tags=["Temperature", "Pressure"],
                        window=(shift["start"], shift["end"]))


# ------------------------------------------------------------- one window, two tags


def test_two_tags_asked_for_together_come_back_on_one_window_and_one_grid(two, shift):
    """The claim the whole screen rests on.

    `Pressure` was wired up an hour into this window. Drawn on its own it
    would start at the hour mark, and a reader laying it over `Temperature`
    would be reading a lag that the chart invented. So both series carry the
    window that was asked for, and the same bucket width, and the empty first
    hour of the second line is drawn as no reading.
    """
    assert two["shown"] == 2
    first, second = two["series"]
    assert [s["label"] for s in two["series"]] == ["Temperature", "Pressure"]
    for s in two["series"]:
        assert s["window"]["start"] == shift["start"], (
            f"{s['label']} was drawn from {s['window']['start']}, not from the "
            f"window that was asked for — two tags on two axes")
        assert s["window"]["end"] == shift["end"]
    assert first["bucket_seconds"] == second["bucket_seconds"]
    assert len(first["points"]) == len(second["points"])


def test_the_line_that_was_wired_up_late_is_empty_and_not_flat(two, shift):
    """An hour with no reading is an hour with no reading.

    A line drawn through it would be a measurement of an hour that was not
    measured. The buckets come back as readings of `null`, counted, with the
    sentence that says which of the two silences this is.
    """
    second = two["series"][1]
    empty = [p for p in second["points"] if p.get("mean") is None]
    assert second["buckets_with_no_reading"] == len(empty) > 0
    assert len(empty) == pytest.approx(len(second["points"]) / 2, abs=2), (
        "Pressure arrived for the second half of the window, so about half of "
        "its buckets should hold no reading")
    assert "no reading" in second["coverage_note"]
    assert second["coverage"] is not None and second["coverage"] < 1.0


def test_a_tag_in_different_units_gets_the_other_side_of_the_chart(two):
    """Degrees on one scale, bar on the other, each axis saying its unit.

    One shared scale would draw the pressure as a flat line along the bottom,
    which is a picture of the units and not of the plant.
    """
    assert [a["unit"] for a in two["axes"]] == ["degC", "bar"]
    assert [s["axis"] for s in two["series"]] == [0, 1]
    assert [a["side"] for a in two["axes"]] == [0, 1]
    assert two["axes"][0]["tags"] == ["Temperature"]


def test_two_tags_in_the_same_unit_share_one_scale(session, shift):
    """Which is the chain's own case: two temperatures, read against each other.

    Separate scales here would be worse than one — each line would be drawn to
    fill its own box and the two degrees between them would vanish.
    """
    mixer = masterdata.get_equipment(session, "MIX01")
    at = shift["start"]
    while at <= shift["end"]:
        session.add(TagValue(equipment_id=mixer.id, tag="MIX01.OutletTemp",
                             ts=at, value_num=8.4))
        at += timedelta(seconds=SAMPLE_SECONDS)
    session.flush()
    out = trend.series(session, "MIX01", tags=["Temperature", "OutletTemp"],
                       window=(shift["start"], shift["end"]))
    assert len(out["axes"]) == 1
    assert [s["axis"] for s in out["series"]] == [0, 0]
    assert out["axes"][0]["tags"] == ["Temperature", "OutletTemp"]


def test_tags_with_no_unit_recorded_share_one_scale_and_the_axis_says_so(
        session, shift, monkeypatch):
    """A plant with no tag manifest. Two axes on a guess would be as
    misleading as one, so they share a scale and the chart says why — and a
    plant that wants them apart names their units, which is config."""
    monkeypatch.setattr(tag_service, "catalog", dict)
    out = trend.series(session, "MIX01", tags=["Temperature", "Pressure"],
                       window=(shift["start"], shift["end"]))
    assert len(out["axes"]) == 1
    assert out["axes"][0]["unit"] is None
    assert "no unit is recorded" in out["axes"][0]["note"]
    assert [s["axis"] for s in out["series"]] == [0, 0]


def test_a_third_unit_is_refused_in_one_sentence_rather_than_drawn(session, shift):
    """A chart has two sides. The third unit has nowhere honest to go."""
    mixer = masterdata.get_equipment(session, "MIX01")
    at = shift["start"]
    while at <= shift["end"]:
        session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Level",
                             ts=at, value_num=70.0))
        at += timedelta(seconds=SAMPLE_SECONDS)
    session.flush()
    with pytest.raises(Invalid) as refused:
        trend.series(session, "MIX01", tags=["Temperature", "Pressure", "Level"],
                     window=(shift["start"], shift["end"]))
    assert "two scales" in str(refused.value)


def test_more_tags_than_the_chart_draws_is_refused_rather_than_truncated(
        session, shift):
    """Quietly dropping the fourth tag would be a chart that lies by omission."""
    with pytest.raises(Invalid) as refused:
        trend.series(session, "MIX01", tags=["A", "B", "C", "D"],
                     window=(shift["start"], shift["end"]))
    assert str(trend.MOST_SERIES) in str(refused.value)


def test_a_tag_this_machine_never_published_is_named_and_not_silently_dropped(
        session, shift):
    """Two different facts, and the reader is owed the difference.

    A tag that went quiet in the window is a line of nulls with a note. A tag
    the machine has never published is nothing at all — and a chart that drew
    one series when two were asked for, saying nothing, would leave a person
    wondering which.
    """
    out = trend.series(session, "MIX01", tags=["Temperature", "Viscosity"],
                       window=(shift["start"], shift["end"]))
    assert out["not_published"] == ["Viscosity"]
    assert out["shown"] == 1
    assert "never published" in out["note"]
    assert "Viscosity" not in out["available"]


def test_the_structural_tags_are_not_on_offer(two):
    """`ReadyBit` and the counters are the tag fabric, not the process."""
    assert "ReadyBit" not in two["available"]
    assert "Temperature" in two["available"]
    assert two["total"] == len(two["available"])


def test_with_no_tag_named_the_chart_opens_on_the_machines_process_value(
        session, shift):
    """The same answer `/analysis/tag` gives with no tag, so the tab opens on
    the signal this machine was wired up for."""
    out = trend.series(session, "MIX01", window=(shift["start"], shift["end"]))
    assert out["shown"] == 1
    assert out["series"][0]["label"] in out["available"]


def test_the_window_says_in_words_which_of_the_three_kinds_it_is(session, shift):
    """A screen that has to say what it is showing cannot work that out from
    two timestamps, so the envelope says which kind of window it is."""
    between = trend.series(session, "MIX01", tags=["Temperature"],
                           window=(shift["start"], shift["end"]))
    assert between["window"]["named"] == "between"
    trailing = trend.series(session, "MIX01", tags=["Temperature"], hours=8)
    assert trailing["window"]["named"] == "span"


def test_the_chart_states_how_much_of_the_window_the_mes_could_see(two):
    """Coverage on the chart, in the house wording, from the same ledger the
    state timeline prints — and it answers a different question from a
    series' own figure: could the MES see the machine, not did the tag speak."""
    assert two["coverage"] is not None
    assert "could see this machine" in two["coverage_note"]


def test_the_window_is_the_machines_and_not_each_tags(session, shift):
    """The identity, not the arithmetic: the series' window *is* the one
    `analysis.machine_window` settled for the machine."""
    start, end, _ = analysis.machine_window(
        session, "MIX01", window=(shift["start"], shift["end"]))
    out = trend.series(session, "MIX01", tags=["Temperature", "Pressure"],
                       window=(shift["start"], shift["end"]))
    for s in out["series"]:
        assert (s["window"]["start"], s["window"]["end"]) == (start, end)


def test_a_machine_nobody_has_heard_of_is_a_404_and_not_an_empty_chart(session):
    with pytest.raises(NotFound):
        trend.series(session, "NOPE01", tags=["Temperature"])


# --------------------------------------------------------- the panel behind a point


@pytest.fixture()
def panel(session, shift):
    """The point a reader clicks: ten minutes after the step, inside the run."""
    at = shift["half"] + timedelta(minutes=10)
    return trend.dossier(session, "MIX01", tag="Temperature", at=at,
                         window=(shift["start"], shift["end"]),
                         bucket_seconds=60)


def test_the_panel_says_what_was_read_and_when(panel, shift):
    """Two honest numbers, because a point on this chart is a bucket: the
    bucket it is a point of, and the single stored sample nearest the
    instant. One alone would either present an average as a measurement or
    disagree with the dot that was clicked."""
    r = panel["reading"]
    assert r["tag"] == "Temperature"
    assert r["unit"] == "degC"
    assert r["bucket"]["seconds"] == 60
    assert r["bucket"]["n"] > 0
    assert r["bucket"]["mean"] == 64.0
    assert r["nearest"]["value"] == 64.0
    assert r["nearest"]["seconds_before"] >= 0
    assert panel["at"] == shift["half"] + timedelta(minutes=10)


def test_a_bucket_nothing_arrived_in_says_so_rather_than_reading_zero(
        session, shift):
    """The first hour of the late tag. Zero would be a measurement."""
    out = trend.dossier(session, "MIX01", tag="Pressure",
                        at=shift["start"] + timedelta(minutes=10),
                        window=(shift["start"], shift["end"]),
                        bucket_seconds=60)
    assert out["reading"]["bucket"]["n"] == 0
    assert out["reading"]["bucket"]["mean"] is None
    assert "no reading" in out["reading"]["bucket"]["note"]
    assert out["reading"]["nearest"] is None


def test_the_panel_says_what_the_machine_was_doing_at_that_second(panel):
    """And what it had just come out of, which is the half that explains a
    number: running, thirty seconds after a changeover, is a different
    reading from running for an hour."""
    machine = panel["machine"]
    assert machine["state"]["state"] == "running"
    assert machine["previous"]["state"] == "down"


def test_a_stop_nobody_named_is_reported_as_unlabelled(session, shift):
    """House rule 3. The two-minute stop in the middle of this window was
    written with no reason on it, and it comes back saying so rather than
    folded into anything."""
    out = trend.dossier(session, "MIX01", tag="Temperature",
                        at=shift["start"] + timedelta(minutes=51),
                        window=(shift["start"], shift["end"]))
    assert out["machine"]["state"]["state"] == "down"
    assert out["machine"]["state"]["reason"] is None
    unlabelled = [s for s in out["stops"]["stops"] if s.get("reason") is None]
    assert unlabelled, out["stops"]


def test_the_panel_names_the_maintenance_that_was_open_and_why_it_is_waiting(panel):
    """Link 2 of the chain's own shape: raised before the window, assigned to
    somebody, never started, and the reason it has not been. A panel that
    only listed jobs *raised* inside the window would never find it."""
    orders = panel["maintenance"]["orders"]
    codes = [o["code"] for o in orders]
    assert "MO-CHILLER" in codes, panel["maintenance"]
    job = next(o for o in orders if o["code"] == "MO-CHILLER")
    assert job["status"] == "assigned"
    assert job["started_at"] is None
    assert "stopped" in (job["reason"] or "")
    assert panel["maintenance"]["total"] == len(codes)


def test_the_panel_says_what_every_other_tag_said_at_that_instant(panel):
    """The last thing each one said at or before the second clicked, and how
    long before. There is no reading AT an instant on a fabric that notifies
    on change, and nothing here is interpolated between two samples."""
    rows = panel["tags"]["tags"]
    names = [t["tag"] for t in rows]
    assert "Temperature" not in names, "the tag that was clicked is the reading"
    assert "ReadyBit" not in names
    pressure = next(t for t in rows if t["tag"] == "Pressure")
    assert pressure["value"] == 2.6
    assert pressure["seconds_before"] is not None
    assert "interpolated" in panel["tags"]["says"]
    assert panel["tags"]["total"] >= panel["tags"]["shown"]


def test_the_quality_block_says_what_the_chart_said_at_the_time(panel):
    """No readings were taken on this machine in this window, so the block is
    empty, states its total, and says it has no coverage figure to give
    rather than printing a nought."""
    assert panel["quality"]["total"] == 0
    assert panel["quality"]["measurements"] == []
    assert panel["quality"]["coverage"] == "absent"
    assert panel["quality"]["coverage_note"] == trend.RECORDS_NOT_A_RATE
    assert "recorded at the time" in panel["quality"]["says"]


def test_the_panel_states_how_much_of_the_window_was_watched(panel):
    assert panel["coverage"] is not None
    assert "could see this machine" in panel["coverage_note"]
    assert panel["watched"]["window_seconds"] == SPAN_MINUTES * 60


def test_an_instant_outside_the_chart_is_refused_rather_than_answered(session, shift):
    """A panel answering about a second the chart is not showing would be
    answering a question nobody asked."""
    with pytest.raises(Invalid) as refused:
        trend.dossier(session, "MIX01", tag="Temperature",
                      at=shift["start"] - timedelta(hours=4),
                      window=(shift["start"], shift["end"]))
    assert "outside the window" in str(refused.value)


def test_the_panel_recomputes_none_of_the_machines_blocks(session, shift, panel):
    """The identity. The state, the maintenance and the stops *are*
    `services.moment`'s — the same code the control-chart panel reads, which
    is why the two panels cannot disagree about a minute of one machine."""
    mixer = masterdata.get_equipment(session, "MIX01")
    at = panel["at"]
    start, end, _ = analysis.machine_window(
        session, "MIX01", window=(shift["start"], shift["end"]))
    assert panel["machine"] == moment.state_at(session, mixer, at)
    assert panel["maintenance"] == moment.maintenance_orders(session, mixer, start, end)


# ------------------------------------------------------------------- the routes


def test_the_route_draws_two_tags_over_a_window_that_has_finished(client, shift):
    r = client.get("/analysis/trend/MIX01", params={
        "tags": ["Temperature", "Pressure"],
        "start": shift["start"].isoformat(), "end": shift["end"].isoformat()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["window"]["named"] == "between"
    assert [s["label"] for s in body["series"]] == ["Temperature", "Pressure"]
    assert len(body["axes"]) == 2


def test_a_window_with_one_end_is_refused_rather_than_guessed_at(client, shift):
    r = client.get("/analysis/trend/MIX01",
                   params={"start": shift["start"].isoformat()})
    assert r.status_code == 400
    assert "both of its ends" in r.json()["detail"]


def test_a_window_named_twice_is_refused(client, shift):
    r = client.get("/analysis/trend/MIX01", params={
        "shift": "current", "start": shift["start"].isoformat(),
        "end": shift["end"].isoformat()})
    assert r.status_code == 400


def test_a_timestamp_with_an_offset_is_converted_and_one_without_is_read_as_utc(
        client, shift):
    """This system's single timestamp convention is naive UTC, and a browser
    sends whatever its own clock says. Both ends of the same window, one
    written as UTC and one as an offset from it, have to name one window."""
    plain = client.get("/analysis/trend/MIX01", params={
        "tags": ["Temperature"], "start": shift["start"].isoformat(),
        "end": shift["end"].isoformat()}).json()
    offset = client.get("/analysis/trend/MIX01", params={
        "tags": ["Temperature"],
        "start": shift["start"].isoformat() + "+00:00",
        "end": shift["end"].isoformat() + "+00:00"}).json()
    assert plain["window"]["start"] == offset["window"]["start"]
    assert plain["window"]["end"] == offset["window"]["end"]


def test_the_point_route_answers_for_one_second_of_that_window(client, shift):
    at = shift["half"] + timedelta(minutes=10)
    r = client.get("/analysis/trend/MIX01/point", params={
        "tag": "Temperature", "at": at.isoformat(), "bucket_seconds": 60,
        "start": shift["start"].isoformat(), "end": shift["end"].isoformat()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reading"]["bucket"]["mean"] == 64.0
    assert body["machine"]["state"]["state"] == "running"
    assert [o["code"] for o in body["maintenance"]["orders"]] == ["MO-CHILLER"]


def test_the_point_route_refuses_a_second_the_chart_is_not_showing(client, shift):
    r = client.get("/analysis/trend/MIX01/point", params={
        "tag": "Temperature",
        "at": (shift["start"] - timedelta(hours=4)).isoformat(),
        "start": shift["start"].isoformat(), "end": shift["end"].isoformat()})
    assert r.status_code == 400
    assert "outside the window" in r.json()["detail"]


def test_both_reads_are_closed_to_a_caller_with_no_session(anon, shift):
    assert anon.get("/analysis/trend/MIX01").status_code in (401, 403)
    assert anon.get("/analysis/trend/MIX01/point", params={
        "tag": "Temperature", "at": shift["half"].isoformat()}
    ).status_code in (401, 403)
