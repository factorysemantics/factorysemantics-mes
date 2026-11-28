"""A simulated plant that was paused overnight wakes without a burst.

The crew's lab pauses its plant every evening (SIGSTOP on the replay, the
floor and the reader) and wakes it at 01:30. On 2026-11-12 the replay, finding
every tick since the evening overdue, played the whole paused stretch in one
burst: ten hours of counts against half an hour of running, and OEE withheld
on all 83 machines for the next eight hours. Since then the lab has reseeded
every night and lost the day's history.

`wake_gap_seconds` (off by default) fixes it at the two places that saw the
pause: the replay resumes from now instead of playing the gap, and the reader
records the gap as time nobody watched. These tests are the answer key:
nothing is stamped inside the gap, the counts do not jump, and OEE is reported
for the hours after the wake. With the setting off, nothing changes.
"""

import asyncio
import time
from datetime import timedelta

import pytest

from fsmes.config import Settings
from fsmes.db import utcnow
from fsmes.domain import (
    ConnectionStateName,
    EquipmentConnection,
    EquipmentState,
    EquipmentStateName,
    ProductionLog,
)
from fsmes.integrations.opc import agent as opc_agent
from fsmes.integrations.opc.csv_replay import _resume_after_pause, _wait_for_tick
from fsmes.services import equipment, masterdata, workorders

PERIOD = 0.005          # seconds a tick; a fast line so the test is quick
PAUSED_TICKS = 7200     # the pause, in ticks: "two hours" of this line


async def _play(ticks: int, *, wake_gap_s: float, paused_for_ticks: int) -> list[float]:
    """Run the replay's own schedule for `ticks` ticks, starting just after a
    pause of `paused_for_ticks`, and return how late each tick was played.

    The schedule is the replay's: `_wait_for_tick` then `_resume_after_pause`,
    in the order `csv_replay.run` calls them."""
    loop = asyncio.get_running_loop()
    started = loop.time() - paused_for_ticks * PERIOD
    late = []
    for tick in range(1, ticks + 1):
        behind = await _wait_for_tick(started, tick, PERIOD)
        started, not_played = _resume_after_pause(started, behind, PERIOD, wake_gap_s)
        late.append(0.0 if not_played else behind)
    return late


def test_the_wake_gap_is_off_unless_a_plant_sets_it():
    assert Settings().wake_gap_seconds == 0.0


def test_with_the_gap_off_a_woken_replay_plays_every_overdue_tick_as_before():
    """The behaviour the lab had, kept byte for byte for every other plant:
    every tick of the next twenty is overdue and is played at once."""
    late = asyncio.run(_play(20, wake_gap_s=0.0, paused_for_ticks=PAUSED_TICKS))
    assert all(behind > 30 * PERIOD for behind in late)


def test_with_the_gap_set_a_woken_replay_resumes_from_now_and_plays_nothing_of_the_pause():
    began = time.monotonic()
    late = asyncio.run(_play(20, wake_gap_s=1.0, paused_for_ticks=PAUSED_TICKS))
    took = time.monotonic() - began
    # Twenty ticks after the wake take twenty ticks of wall clock (less the
    # first, which is now): a burst would have played them in no time at all.
    assert took >= 18 * PERIOD, f"twenty ticks in {took:.4f} s is the paused time played in a burst"
    assert max(late) < 30 * PERIOD


def test_a_slow_tick_is_not_mistaken_for_a_pause():
    """A line running a few seconds behind is behind, and says so; only a
    stretch longer than the gap moves the schedule."""
    assert _resume_after_pause(100.0, 4.0, 1.0, wake_gap_s=60.0) == (100.0, 0)
    assert _resume_after_pause(100.0, 7200.0, 1.0, wake_gap_s=60.0) == (7300.0, 7200)
    assert _resume_after_pause(100.0, 7200.0, 1.0, wake_gap_s=0.0) == (100.0, 0)


def test_a_reader_woken_past_the_gap_ends_its_connection_rather_than_carrying_on():
    class _Server:
        asked = 0

        async def check_connection(self):
            self.asked += 1

    server = _Server()
    link = opc_agent._Link([], "opc.tcp://127.0.0.1:4844/x")
    link.seen = link._now() - timedelta(hours=10)
    with pytest.raises(opc_agent.SleptThroughIt, match="paused for 36000 s"):
        asyncio.run(opc_agent._health_watchdog(server, link, 0.001, wake_gap_s=60.0))
    assert server.asked == 0, "a reader that slept asked the server as if nothing had happened"


def test_with_the_gap_off_a_reader_woken_after_hours_carries_on_as_before():
    class _Server:
        asked = 0

        async def check_connection(self):
            self.asked += 1
            if self.asked >= 3:
                raise OSError("stop the test")

    server = _Server()
    link = opc_agent._Link([], "opc.tcp://127.0.0.1:4844/x")
    link.seen = link._now() - timedelta(hours=10)
    with pytest.raises(OSError):
        asyncio.run(opc_agent._health_watchdog(server, link, 0.001))
    assert server.asked == 3


def test_a_night_paused_and_woken_reports_oee_for_the_hours_after_the_wake(session, scope,
                                                                           monkeypatch):
    """The answer key. MIX01 (4 s a unit) ran 30 minutes before the pause at
    a steady 300 units, was paused for ten hours, and has run one hour since
    the wake at 600 units. The reader records the pause the way the watchdog
    makes it: disconnected from its last proof, connected again at the wake.

    Over the last eight hours: nothing is stamped inside the gap, only the
    hour after the wake is run time, and OEE is a figure, not withheld."""
    monkeypatch.setattr(opc_agent, "session_scope", scope)
    mixer = masterdata.get_equipment(session, "MIX01")
    now = utcnow()
    woke = now - timedelta(hours=1)
    paused = woke - timedelta(hours=10)
    ran_from = paused - timedelta(minutes=30)
    wo = workorders.create(session, code="WO-WAKE", material_code="FG-COLA", quantity=10_000)

    link = opc_agent._Link(["MIX01"], "opc.tcp://127.0.0.1:4844/x")
    link._now = lambda: ran_from
    link.connected()
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=ran_from))
    session.add(ProductionLog(work_order_id=wo.id, equipment_id=mixer.id, good_qty=300,
                              scrap_qty=0, ts=paused - timedelta(minutes=1)))
    session.flush()
    link.seen = paused                   # the last health check before the SIGSTOP
    link._now = lambda: woke             # the watchdog notices on waking ...
    assert opc_agent._slept_for(link, 60.0) == pytest.approx(36000)
    link.disconnected("the reader was paused")
    link.connected()                     # ... and the next subscription is live
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=woke))
    for minute in range(0, 60, 10):      # a resumed replay: 100 units each ten minutes
        session.add(ProductionLog(work_order_id=wo.id, equipment_id=mixer.id, good_qty=100,
                                  scrap_qty=0, ts=woke + timedelta(minutes=minute + 5)))
    session.flush()

    inside = (session.query(ProductionLog)
              .filter(ProductionLog.ts > paused, ProductionLog.ts < woke).count())
    assert inside == 0, "units were stamped inside the paused hours"
    connections = (session.query(EquipmentConnection)
                   .filter(EquipmentConnection.equipment_id == mixer.id)
                   .order_by(EquipmentConnection.id).all())
    assert [c.state for c in connections] == [ConnectionStateName.CONNECTED,
                                              ConnectionStateName.DISCONNECTED,
                                              ConnectionStateName.CONNECTED]
    assert connections[1].started_at == paused and connections[1].ended_at == woke

    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["oee"] is not None, f"OEE withheld after the wake: {result.get('performance_note')}"
    assert result["performance_note"] is None
    # One hour run, 600 units at 4 s: two thirds of the rated speed.
    assert result["performance"] == pytest.approx(600 * 4 / 3600, rel=0.05)
    assert result["availability"] == pytest.approx(1.0, abs=0.02)
    assert result["unknown_seconds"] == pytest.approx(7 * 3600, rel=0.02)


def test_the_burst_the_lab_saw_is_withheld_which_is_why_the_wake_gap_exists(session):
    """What the lab had before this setting: the paused hours' counts all
    booked in the hour after the wake, against a running interval carried
    through the pause - and OEE withheld. Kept so the reason is on record."""
    mixer = masterdata.get_equipment(session, "MIX01")
    now = utcnow()
    woke = now - timedelta(hours=1)
    session.add(EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=woke))
    wo = workorders.create(session, code="WO-BURST", material_code="FG-COLA", quantity=100_000)
    session.add(ProductionLog(work_order_id=wo.id, equipment_id=mixer.id, good_qty=9_000,
                              scrap_qty=0, ts=woke + timedelta(seconds=30)))
    session.flush()

    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["oee"] is None
    assert "will not fit inside the run time" in (result["performance_note"] or "")
