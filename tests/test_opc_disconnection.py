"""A lost connection to the machine layer, and what every screen says about it.

Decision 0028. The question these pin is not "does the MES notice" - it is
what the MES *claims* about the minutes it could not see. A plant loses a
connection weekly, and the two ways to get this wrong are symmetrical: read
the gap as a breakdown and invent a fault, or read it as the last state and
invent availability.
"""

from datetime import timedelta

import pytest

from fsmes.db import utcnow
from fsmes.domain import (
    ConnectionStateName,
    EquipmentConnection,
    EquipmentState,
    EquipmentStateName,
)
from fsmes.services import analysis, connection, equipment, maintenance, masterdata


def _disconnect(session, code="MIX01", *, minutes_ago: float, for_minutes: float | None,
                reason="the OPC server did not answer"):
    """A machine that stopped being visible `minutes_ago`, for `for_minutes`
    (None means it still is), written the way the agent writes it."""
    now = utcnow()
    at = now - timedelta(minutes=minutes_ago)
    connection.set_connection(session, equipment_code=code,
                              state=ConnectionStateName.CONNECTED,
                              at=at - timedelta(minutes=1), detected_at=at - timedelta(minutes=1))
    connection.set_connection(session, equipment_code=code,
                              state=ConnectionStateName.DISCONNECTED,
                              at=at, detected_at=at + timedelta(seconds=2), reason=reason)
    if for_minutes is not None:
        back = at + timedelta(minutes=for_minutes)
        connection.set_connection(session, equipment_code=code,
                                  state=ConnectionStateName.CONNECTED, at=back, detected_at=back)
    session.flush()
    return at


# ------------------------------------------------- the history stops, nothing is invented

def test_a_machine_nobody_can_see_has_no_state_at_all(session):
    equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.RUNNING)
    _disconnect(session, minutes_ago=0, for_minutes=None)

    mixer = masterdata.get_equipment(session, "MIX01")
    open_states = [s for s in session.query(EquipmentState)
                   .filter(EquipmentState.equipment_id == mixer.id) if s.ended_at is None]
    assert open_states == [], "the running interval kept accruing through the outage"


def test_the_state_history_stops_where_the_evidence_stops(session):
    started = utcnow() - timedelta(minutes=30)
    mixer = masterdata.get_equipment(session, "MIX01")
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=started))
    session.flush()
    at = _disconnect(session, minutes_ago=10, for_minutes=None)

    state = session.query(EquipmentState).filter(EquipmentState.equipment_id == mixer.id).one()
    assert state.ended_at == pytest.approx(at, abs=timedelta(seconds=1))


def test_a_reconnection_does_not_backfill_the_gap(session):
    mixer = masterdata.get_equipment(session, "MIX01")
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=utcnow() - timedelta(minutes=40)))
    session.flush()
    _disconnect(session, minutes_ago=20, for_minutes=10)
    # Coming back is a new observation, not a claim about the ten minutes:
    # the agent's next State reading opens a fresh interval where it lands.
    back = utcnow() - timedelta(minutes=10)
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=back))
    session.flush()

    intervals = session.query(EquipmentState).filter(
        EquipmentState.equipment_id == mixer.id).order_by(EquipmentState.started_at).all()
    gap = (intervals[1].started_at - intervals[0].ended_at).total_seconds()
    assert gap == pytest.approx(10 * 60, abs=60), "the gap was filled in rather than left alone"
    assert all(i.state is EquipmentStateName.RUNNING for i in intervals)


def test_a_disconnection_is_recorded_with_when_it_started_and_when_it_was_noticed(session):
    at = _disconnect(session, minutes_ago=5, for_minutes=None)
    row = session.query(EquipmentConnection).filter(
        EquipmentConnection.state == ConnectionStateName.DISCONNECTED).one()
    assert row.started_at == pytest.approx(at, abs=timedelta(seconds=1))
    assert row.detected_at > row.started_at, "the moment we noticed is not the moment it went"
    assert row.reason == "the OPC server did not answer"


def test_an_interval_never_ends_before_it_began(session):
    """Evidence older than the interval it closes is clamped, not written as a
    negative stretch. A clock that goes backwards must not produce one."""
    connection.set_connection(session, equipment_code="MIX01",
                              state=ConnectionStateName.CONNECTED)
    connection.set_connection(session, equipment_code="MIX01",
                              state=ConnectionStateName.DISCONNECTED,
                              at=utcnow() - timedelta(hours=3), reason="a clock that slipped")
    session.flush()
    for row in session.query(EquipmentConnection):
        assert row.ended_at is None or row.ended_at >= row.started_at


def test_saying_it_twice_writes_one_interval(session):
    connection.set_connection(session, equipment_code="MIX01",
                              state=ConnectionStateName.DISCONNECTED, reason="gone")
    connection.set_connection(session, equipment_code="MIX01",
                              state=ConnectionStateName.DISCONNECTED, reason="still gone")
    assert session.query(EquipmentConnection).count() == 1


# ------------------------------------------------------------------- the numbers

def test_availability_excludes_the_minutes_nobody_watched(session):
    """Thirty minutes watched, twenty of them running, ten of them blind:
    availability is two thirds of the watched time, not half the window."""
    mixer = masterdata.get_equipment(session, "MIX01")
    start = utcnow() - timedelta(minutes=40)
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=start, ended_at=start + timedelta(minutes=20)))
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.DOWN,
                               started_at=start + timedelta(minutes=30),
                               ended_at=start + timedelta(minutes=40)))
    session.flush()
    _disconnect(session, minutes_ago=20, for_minutes=10)

    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["unknown_seconds"] == pytest.approx(600, abs=30)
    assert result["availability"] == pytest.approx(20 / 30, abs=0.03)


def test_every_oee_answer_says_how_much_of_its_window_was_unknown(session):
    mixer = masterdata.get_equipment(session, "MIX01")
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=utcnow() - timedelta(minutes=40),
                               ended_at=utcnow() - timedelta(minutes=20)))
    session.flush()
    _disconnect(session, minutes_ago=20, for_minutes=10)

    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["unknown_share"] == pytest.approx(600 / (40 * 60), abs=0.05)
    assert result["observed_hours"] < result["window_hours"]


def test_a_window_the_mes_was_blind_for_is_unknown_and_not_zero(session):
    """An agent that has never once reached its server. The MES has been
    watching for half an hour and has seen nothing; that is not 0% available,
    and reporting it as such would put a working machine at the top of the
    worst-performer list."""
    connection.set_connection(session, equipment_code="MIX01",
                              state=ConnectionStateName.DISCONNECTED,
                              at=utcnow() - timedelta(minutes=30),
                              reason="the OPC server did not answer")
    session.flush()

    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["availability"] is None, "a blind window must not report 0% available"
    assert result["oee"] is None
    assert result["unknown_share"] == pytest.approx(1.0, abs=0.02)


def test_the_line_breakdown_prices_the_loss_against_watched_time(session):
    """An outage is not a loss the machine caused. Pricing it as one bills a
    plant in units for the minutes its network was down."""
    mixer = masterdata.get_equipment(session, "MIX01")
    start = utcnow() - timedelta(minutes=30)
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=start, ended_at=start + timedelta(minutes=20)))
    session.flush()
    _disconnect(session, minutes_ago=10, for_minutes=None)

    breakdown = analysis.oee_breakdown(session, hours=8.0)
    row = next(s for s in breakdown["stations"] if s["code"] == "MIX01")
    assert row["unknown_seconds"] == pytest.approx(600, abs=60)
    assert row["loss"]["availability_seconds"] == pytest.approx(row["observed_seconds"]
                                                                - row["runtime_seconds"], abs=1)
    assert breakdown["machines_disconnected_now"] == 1
    assert breakdown["machines_total"] >= 1


def test_the_downtime_pareto_says_how_blind_its_window_was(session):
    """A disconnection is never a bucket - it is not downtime and nobody
    named it - but the pareto has to say how much of the window it covered,
    or it reads as complete."""
    _disconnect(session, minutes_ago=10, for_minutes=None)
    pareto = analysis.downtime_pareto(session, hours=8.0)
    assert pareto["unknown_seconds"] > 0
    assert all(bucket["reason"] != "disconnected" for bucket in pareto["reasons"])
    assert pareto["machines_total"] >= 1


def test_the_gap_is_drawn_on_the_timeline_rather_than_left_as_white_space(session):
    mixer = masterdata.get_equipment(session, "MIX01")
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=utcnow() - timedelta(minutes=30),
                               ended_at=utcnow() - timedelta(minutes=10)))
    session.flush()
    _disconnect(session, minutes_ago=10, for_minutes=5)

    timeline = analysis.state_timeline(session, hours=8.0, equipment=["MIX01"])
    row = next(r for r in timeline["machines"] if r["code"] == "MIX01")
    gaps = [i for i in row["intervals"] if i["state"] == "disconnected"]
    assert gaps, "the outage is invisible on the chart the shift is read from"
    assert gaps[0]["reason_source"] == "connection"


def test_maintenance_hours_stop_accruing_against_a_machine_nobody_can_see(session):
    """The sharpest version of the bug: an interval left open by a dropped
    link ran a machine's service plan forward while it sat unplugged."""
    mixer = masterdata.get_equipment(session, "MIX01")
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=utcnow() - timedelta(hours=4)))
    session.flush()
    _disconnect(session, minutes_ago=180, for_minutes=None)

    hours = maintenance.runtime_hours(session, mixer.id)
    assert hours == pytest.approx(1.0, abs=0.05), "hours accrued while the machine was unseen"


# --------------------------------------------------------------------- the screens

def test_a_machine_with_no_agent_is_unknown_and_not_connected(session, client):
    """Nothing has ever reported a connection for a machine fed by hand or
    over MQTT. Calling that `connected` would be a claim nobody made."""
    body = client.get("/equipment/connections").json()
    mine = next(m for m in body["machines"] if m["equipment"] == "MIX01")
    assert mine["state"] == "unknown"
    assert mine["since"] is None
    assert body["unknown"] >= 1


def test_the_connections_list_states_its_total(session, client):
    body = client.get("/equipment/connections").json()
    assert body["connected"] + body["disconnected"] + body["unknown"] == body["machines_total"]
    assert len(body["machines"]) == body["machines_total"]


def test_health_says_how_many_machines_this_plant_cannot_see(session, client):
    _disconnect(session, minutes_ago=2, for_minutes=None)
    watching = client.get("/health").json()["watching"]
    assert watching["disconnected"] == 1
    assert watching["connected"] + watching["disconnected"] + watching["unknown"] \
        == watching["machines"]


def test_the_floor_tile_carries_the_connection_beside_the_state(session, client):
    equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.RUNNING)
    _disconnect(session, minutes_ago=2, for_minutes=None)

    summary = client.get("/dashboard/summary").json()
    machines = summary["machines"]["items"] if isinstance(summary["machines"], dict) \
        else summary["machines"]
    mine = next(m for m in machines if m["code"] == "MIX01")
    assert mine["connection"]["state"] == "disconnected"
    assert mine["connection"]["since"] is not None
    # And the state itself is no longer a claim: the interval was closed.
    assert mine["state"] == "unknown"


def test_the_machine_page_says_the_link_is_gone(session, client):
    equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.RUNNING)
    _disconnect(session, minutes_ago=2, for_minutes=None, reason="the OPC server did not answer")

    head = client.get("/equipment/MIX01").json()
    assert head["connection"]["state"] == "disconnected"
    assert head["connection"]["reason"] == "the OPC server did not answer"


def test_the_connection_change_reaches_the_namespace(session):
    from fsmes.domain import ErpMessage

    _disconnect(session, minutes_ago=2, for_minutes=None)
    kinds = [m.kind for m in session.query(ErpMessage)]
    assert "equipment_connection_change" in kinds
    payload = next(m.payload for m in session.query(ErpMessage)
                   if m.kind == "equipment_connection_change"
                   and m.payload["connection"] == "disconnected")
    assert payload["equipment"] == "MIX01"
    assert payload["detected_at"] >= payload["started_at"]


# -------------------------------------------------------------- the agent's link

def test_the_watchdog_asks_the_server_rather_than_inferring_from_silence():
    """OPC UA publishes on change: a machine standing idle correctly sends
    nothing for an hour. Inferring an outage from that invents one, which is
    the same fault as missing a real one, in the other direction."""
    import asyncio

    from fsmes.integrations.opc import agent as opc_agent

    class _Server:
        def __init__(self):
            self.asked = 0

        async def check_connection(self):
            self.asked += 1
            if self.asked >= 3:
                raise OSError("connection refused")

    server = _Server()
    link = opc_agent._Link([], "opc.tcp://127.0.0.1:4840/x")
    with pytest.raises(OSError):
        asyncio.run(opc_agent._health_watchdog(server, link, 0.001))
    assert server.asked == 3, "the watchdog stopped asking, or never started"
    assert link.seen is not None, "a successful check is the evidence the link was alive"


def test_how_often_the_watchdog_asks_is_config_and_never_faster_than_a_second():
    from fsmes.config import Settings
    from fsmes.integrations.opc.agent import health_seconds

    assert health_seconds(Settings(opc_publish_ms=500, opc_health_periods=3)) == 1.5
    assert health_seconds(Settings(opc_publish_ms=2000, opc_health_periods=2)) == 4.0
    # A plant that asks for it faster than a second gets a second: a check per
    # 50 ms buys nothing anybody can act on and costs a round trip each time.
    assert health_seconds(Settings(opc_publish_ms=50, opc_health_periods=1)) == 1.0


def test_the_link_writes_one_interval_however_long_the_server_stays_away(session, scope, monkeypatch):
    """The agent retries every three seconds. A row per retry would be a
    thousand intervals for an outage nobody fixed over a weekend."""
    from fsmes.integrations.opc import agent as opc_agent

    monkeypatch.setattr(opc_agent, "session_scope", scope)
    link = opc_agent._Link(["MIX01"], "opc.tcp://127.0.0.1:4840/x")
    link.connected()
    for _ in range(5):
        link.disconnected("the OPC server did not answer")

    rows = session.query(EquipmentConnection).order_by(EquipmentConnection.id).all()
    assert [r.state for r in rows] == [ConnectionStateName.CONNECTED,
                                       ConnectionStateName.DISCONNECTED]
    assert rows[1].source == "opc.tcp://127.0.0.1:4840/x"


def test_coming_back_closes_the_outage_rather_than_starting_a_third_interval(session, scope,
                                                                             monkeypatch):
    from fsmes.integrations.opc import agent as opc_agent

    monkeypatch.setattr(opc_agent, "session_scope", scope)
    link = opc_agent._Link(["MIX01"], "opc.tcp://127.0.0.1:4840/x")
    link.connected()
    link.disconnected("the OPC server did not answer")
    link.connected()

    rows = session.query(EquipmentConnection).order_by(EquipmentConnection.id).all()
    assert len(rows) == 3
    assert rows[1].ended_at is not None, "the outage was left open after the link came back"
    assert rows[2].state is ConnectionStateName.CONNECTED and rows[2].ended_at is None
