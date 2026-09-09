"""The 3D line view's feed: the plant's shape, and the units it has booked.

The claim this view makes to an operator is that what moves on screen actually
happened. These tests are what makes that claim checkable: a unit appears in the
feed exactly once, only after the MES booked it, and never because the renderer
needed something to draw.
"""

from pathlib import Path

import pytest
from sqlalchemy import select

from fsmes import demo_feed
from fsmes.config import get_settings
from fsmes.domain import Equipment, TagValue
from fsmes.seed_kepsim import seed_kepsim_line
from fsmes.services import line as line_service

REPO = Path(__file__).resolve().parents[1]
KEPSIM_MAP = REPO / "config" / "tag_map_kepsim.json"

LINE_ORDER = ["LD01", "RD01", "WASH01", "QI01", "FILL01", "PAL01"]


@pytest.fixture()
def kepsim(session):
    """The six-station line, seeded alongside the demo plant."""
    seed_kepsim_line(session, tag_map=KEPSIM_MAP)
    session.flush()
    return session


def _tag(session, code: str, tag: str, value: float) -> None:
    equipment = session.scalar(select(Equipment).where(Equipment.code == code))
    session.add(TagValue(equipment_id=equipment.id, tag=f"{code}.{tag}", value_num=value))
    session.flush()


def _book(client, order: str, seq: int, good: float = 0, scrap: float = 0) -> None:
    response = client.post("/execution/report", json={"order": order, "seq": seq, "good": good, "scrap": scrap})
    assert response.status_code == 200, response.text


@pytest.fixture()
def running_order(client):
    """A released cola order with its first operation started, ready to book against."""
    client.post("/workorders", json={"code": "WO-LINE", "material": "FG-COLA", "quantity": 100})
    client.post("/workorders/WO-LINE/release")
    client.post("/workorders/WO-LINE/operations/10/start")
    return "WO-LINE"


# ------------------------------------------------------------------------ access


def test_the_line_view_is_not_public(anon):
    assert anon.get("/line/layout").status_code == 401
    assert anon.get("/line/events").status_code == 401


def test_unknown_line_is_a_404_that_names_the_real_ones(client):
    response = client.get("/line/layout", params={"line": "NOPE"})
    assert response.status_code == 404
    assert "LINE1" in response.json()["detail"]


# ------------------------------------------------------------------------ layout


def test_layout_opens_on_the_line_that_has_the_machines(client, kepsim):
    """With two lines seeded, the view should open on the six-station one, not
    alphabetically on the two-machine one."""
    data = client.get("/line/layout").json()
    assert data["line"]["code"] == "SIMLINE"
    assert [s["code"] for s in data["stations"]] == LINE_ORDER


def test_layout_orders_stations_by_process_not_by_code(client, kepsim):
    """Alphabetically the line reads FILL01, LD01, PAL01... which is nobody's
    factory. Routing sequence is the only thing that knows the real order."""
    stations = client.get("/line/layout").json()["stations"]
    assert [s["code"] for s in stations] != sorted(s["code"] for s in stations)
    assert [s["seq"] for s in stations] == [10, 20, 30, 40, 50, 60]


def test_layout_gives_each_station_a_shape_and_a_place(client, kepsim):
    stations = {s["code"]: s for s in client.get("/line/layout").json()["stations"]}
    assert stations["LD01"]["kind"] == "depalletiser"
    assert stations["PAL01"]["kind"] == "palletiser"
    assert stations["WASH01"]["kind"] == "washer"
    assert stations["FILL01"]["kind"] == "filler"
    # Laid out along the line, in order, with the rated cycle time carried through.
    xs = [s["x"] for s in client.get("/line/layout").json()["stations"]]
    assert xs == sorted(xs) and len(set(xs)) == len(xs)
    assert stations["RD01"]["cycle_seconds"] == pytest.approx(0.556)


def test_layout_works_with_no_layout_file_at_all(client, kepsim, monkeypatch, tmp_path):
    """A view that needs a config file before it renders is a view nobody turns
    on. Without one, the line is laid out from its routing."""
    monkeypatch.setattr(get_settings(), "line_layout_file", tmp_path / "absent.json")
    data = client.get("/line/layout").json()
    assert [s["code"] for s in data["stations"]] == LINE_ORDER
    # Kinds still resolve — inferred from the equipment's own name.
    kinds = {s["code"]: s["kind"] for s in data["stations"]}
    assert kinds["LD01"] == "depalletiser" and kinds["PAL01"] == "palletiser"


def test_layout_lists_the_other_lines_so_the_view_can_offer_a_switch(client, kepsim):
    lines = {entry["code"]: entry["stations"] for entry in client.get("/line/layout").json()["lines"]}
    assert lines == {"SIMLINE": 6, "LINE1": 2}


# ------------------------------------------------------------------------ events


def test_a_fresh_client_gets_a_cursor_and_no_backlog(client, running_order):
    """Opening the page must not replay history: the renderer would have to
    invent positions for units that finished travelling hours ago."""
    _book(client, running_order, 10, good=5)
    first = client.get("/line/events", params={"line": "LINE1"}).json()
    assert first["units"] == []
    assert first["cursor"] > 0


def test_units_arrive_once_and_only_once(client, running_order):
    cursor = client.get("/line/events", params={"line": "LINE1"}).json()["cursor"]

    _book(client, running_order, 10, good=2, scrap=1)
    data = client.get("/line/events", params={"line": "LINE1", "since": cursor}).json()
    assert [(u["equipment"], u["good"], u["scrap"]) for u in data["units"]] == [("MIX01", 2.0, 1.0)]

    # Same cursor advanced: the booking must not come round again.
    again = client.get("/line/events", params={"line": "LINE1", "since": data["cursor"]}).json()
    assert again["units"] == []


def test_the_feed_reports_nothing_when_nothing_was_booked(client, running_order):
    cursor = client.get("/line/events", params={"line": "LINE1"}).json()["cursor"]
    assert client.get("/line/events", params={"line": "LINE1", "since": cursor}).json()["units"] == []


def test_units_from_another_line_do_not_leak_in(client, kepsim, running_order):
    """The cola line's production must not appear on the simulated line's belts."""
    cursor = client.get("/line/events", params={"line": "SIMLINE"}).json()["cursor"]
    _book(client, running_order, 10, good=3)
    data = client.get("/line/events", params={"line": "SIMLINE", "since": cursor}).json()
    assert data["units"] == []
    # ...but the cursor still moves past it, so the client never re-reads it.
    assert data["cursor"] >= cursor


def test_stations_carry_their_live_state_and_order(client, running_order):
    client.post("/equipment/MIX01/state", json={"state": "running"})
    stations = {s["code"]: s for s in client.get("/line/events", params={"line": "LINE1"}).json()["stations"]}
    assert stations["MIX01"]["state"] == "running"
    assert stations["MIX01"]["order"] == running_order
    assert stations["MIX01"]["operation"] == "Mix"


# ------------------------------------------------------- the machine's own analog


def test_the_analog_comes_from_the_tag_map_when_it_knows_the_machine(client, session):
    """MIX01 is in config/tag_map.json, which says its process value is called
    Temperature. That declaration wins over anything else the machine happens to
    have published."""
    _tag(session, "MIX01", "Temperature", 57.5)
    _tag(session, "MIX01", "SomeOtherReading", 999.0)  # later, but not what the map names

    stations = {s["code"]: s for s in client.get("/line/events", params={"line": "LINE1"}).json()["stations"]}
    assert stations["MIX01"]["analog"] == {"name": "Temperature", "value": 57.5}


def test_a_machine_that_calls_it_something_else_is_still_read(client, kepsim, session):
    """LD01 publishes FeedRate, not Temperature, and the cola tag map has never
    heard of LD01. Asking every machine for '.Temperature' is why this used to
    come back blank on the whole six-station line."""
    _tag(session, "LD01", "FeedRate", 109.2)
    _tag(session, "LD01", "State", 1)  # structural — must not be mistaken for the process value

    stations = {s["code"]: s for s in client.get("/line/events", params={"line": "SIMLINE"}).json()["stations"]}
    assert stations["LD01"]["analog"] == {"name": "FeedRate", "value": 109.2}
    assert stations["RD01"]["analog"] is None  # published nothing yet, and we do not guess


def test_the_dashboard_reads_the_same_process_value(client, kepsim, session):
    """The regression this guards: the operator screen hardcoded '.Temperature',
    so every KepSim station showed a blank reading."""
    _tag(session, "WASH01", "WashTemp", 71.4)
    machines = {m["code"]: m for m in client.get("/dashboard/summary").json()["machines"]}
    assert machines["WASH01"]["analog"] == {"name": "WashTemp", "value": 71.4}


def test_analog_reading_is_the_one_helper_both_screens_use(session, kepsim):
    _tag(session, "FILL01", "FillWeight", 500.6)
    filler = session.scalar(select(Equipment).where(Equipment.code == "FILL01"))
    assert line_service.analog_reading(session, filler) == {"name": "FillWeight", "value": 500.6}


# ------------------------------------------------------------------- provenance


def _connected(session, *, endpoint: str, tag_map: str, security: str = "none (anonymous)") -> None:
    """Stand in for the OPC agent announcing which machine layer it reached."""
    from fsmes.services import audit

    audit.record(
        session,
        actor="opc-agent",
        action="opc.connected",
        entity_type="machine_layer",
        entity_id=endpoint,
        after={"endpoint": endpoint, "tag_map": tag_map, "security": security, "addressing": "browse"},
    )
    session.flush()


def test_the_view_says_it_has_no_source_until_an_agent_connects(client, kepsim):
    """Silence would let the picture imply a machine layer it has never spoken
    to. Nothing has connected, so the view must say so."""
    assert client.get("/line/layout").json()["source"] is None


def test_the_view_names_kepware_when_that_is_what_fed_it(client, kepsim, session):
    _connected(session, endpoint="opc.tcp://127.0.0.1:49320", tag_map="tag_map_kepware.json",
               security="Basic256Sha256,SignAndEncrypt")
    source = client.get("/line/layout").json()["source"]
    assert source["name"] == "KEPServerEX"
    assert source["endpoint"] == "opc.tcp://127.0.0.1:49320"
    assert source["security"] == "Basic256Sha256,SignAndEncrypt"


def test_the_view_names_the_replay_server_when_that_is_what_fed_it(client, kepsim, session):
    _connected(session, endpoint="opc.tcp://127.0.0.1:4840/mes-twin/sim", tag_map="tag_map_kepsim.json")
    assert client.get("/line/layout").json()["source"]["name"] == "replay server"


def test_the_latest_connection_wins(client, kepsim, session):
    """Switching the agent from the replay to Kepware must change what the view
    claims — otherwise the badge is a decoration, not a fact."""
    _connected(session, endpoint="opc.tcp://127.0.0.1:4840/mes-twin/sim", tag_map="tag_map_kepsim.json")
    assert client.get("/line/layout").json()["source"]["name"] == "replay server"

    _connected(session, endpoint="opc.tcp://127.0.0.1:49320", tag_map="tag_map_kepware.json")
    source = client.get("/line/layout").json()["source"]
    assert source["name"] == "KEPServerEX"
    assert source["tag_map"] == "tag_map_kepware.json"


def test_an_unrecognised_map_is_not_guessed_at(client, kepsim, session):
    _connected(session, endpoint="opc.tcp://plc.example:4840", tag_map="tag_map_sitea.json")
    assert client.get("/line/layout").json()["source"]["name"] == "OPC UA server"


# ------------------------------------------------------------ the recorded hour


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> Path:
    """One generated KepSim line, shared by the recording tests."""
    from fsmes.sim.generate import generate

    out = tmp_path_factory.mktemp("demo_out")
    generate(REPO / "labs" / "kepsim" / "line.json", out, write_docs=False)
    return out


def test_the_recording_describes_the_line_exactly_as_the_live_endpoint_does(session, kepsim, generated):
    """The demo has to be the same thing, recorded — not a lookalike. It carries
    the layout the live endpoint would have served, so one renderer drives both
    and the demo cannot quietly drift into fiction."""
    payload = demo_feed.build(session, replay_dir=generated, tag_map=KEPSIM_MAP)
    assert payload["layout"] == line_service.layout(session, line_code="SIMLINE")


def test_the_recording_carries_every_station_for_the_whole_hour(session, kepsim, generated):
    payload = demo_feed.build(session, replay_dir=generated, tag_map=KEPSIM_MAP)
    assert [s["code"] for s in payload["stations"]] == LINE_ORDER
    assert payload["ticks"] == 3600
    assert all(len(s["rows"]) == 3600 for s in payload["stations"])
    assert payload["tick_seconds"] == 1.0


def test_the_recording_stores_mes_states_not_raw_plc_integers(session, kepsim, generated):
    """A PLC says 2 and 3; only the tag map knows those mean the machine is
    healthy but idle. Storing the integers would make the demo show colours the
    MES never agreed to."""
    payload = demo_feed.build(session, replay_dir=generated, tag_map=KEPSIM_MAP)
    seen = {row[0] for station in payload["stations"] for row in station["rows"]}
    assert seen <= {"running", "idle", "down", "setup"}

    rd = next(s for s in payload["stations"] if s["code"] == "RD01")
    assert rd["analog"] == "MotorTemp"
    assert all(rd["rows"][t][0] == "down" for t in range(1500, 1620)), "the RD breakdown is missing"
    assert all(rd["rows"][t][0] == "setup" for t in range(2400, 2640)), "the changeover is missing"


def test_the_recording_keeps_the_counter_reset_that_the_scenario_stages(session, kepsim, generated):
    """t=3000 resets RD01's counter. The recording must preserve it — it is what
    exercises the renderer's own never-invent-production rule."""
    payload = demo_feed.build(session, replay_dir=generated, tag_map=KEPSIM_MAP)
    rd = next(s for s in payload["stations"] if s["code"] == "RD01")
    assert rd["rows"][2999][1] > 0
    assert rd["rows"][3000][1] == 0


def test_recording_a_line_the_tag_map_does_not_describe_is_refused(session, kepsim, generated):
    """Silently recording an empty line would produce a demo that plays nothing."""
    with pytest.raises(ValueError, match="not in the tag map"):
        demo_feed.build(session, replay_dir=generated, tag_map=KEPSIM_MAP, line_code="LINE1")
