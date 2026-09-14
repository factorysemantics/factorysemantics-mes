"""Equipment state intervals and the OEE math."""

from datetime import timedelta

import pytest

from fsmes.db import utcnow
from fsmes.domain import EquipmentState, EquipmentStateName, ProductionLog
from fsmes.services import equipment, masterdata, workorders
from fsmes.services import oee as oee_rules


def test_set_state_closes_previous_interval(session):
    equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.RUNNING)
    equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.DOWN, reason="jam")
    states = session.query(EquipmentState).order_by(EquipmentState.id).all()
    assert states[0].ended_at is not None
    assert states[1].ended_at is None and states[1].reason == "jam"


def test_set_state_is_idempotent(session):
    first = equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.RUNNING)
    second = equipment.set_state(session, equipment_code="MIX01", state=EquipmentStateName.RUNNING)
    assert first.id == second.id


def test_oee_ignores_time_before_the_mes_was_watching(session):
    """A machine running for the whole two minutes we have observed it is 100%
    available — the 8 hours before it existed are not downtime."""
    mixer = masterdata.get_equipment(session, "MIX01")
    session.add(
        EquipmentState(
            equipment_id=mixer.id,
            state=EquipmentStateName.RUNNING,
            started_at=utcnow() - timedelta(minutes=2),
        )
    )
    session.flush()

    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["availability"] == pytest.approx(1.0, abs=0.01)
    assert result["window_hours"] == pytest.approx(2 / 60, abs=0.005)  # clamped to real history


def test_oee_says_unknown_rather_than_zero_without_history(session):
    result = equipment.oee(session, equipment_code="MIX01", hours=8.0)
    assert result["availability"] is None and result["oee"] is None


def test_oee_math(session):
    """1h window: 30min running, 20 units at 4s ideal cycle, 18 good / 2 scrap."""
    mixer = masterdata.get_equipment(session, "MIX01")
    now = utcnow()
    session.add(
        EquipmentState(
            equipment_id=mixer.id,
            state=EquipmentStateName.RUNNING,
            started_at=now - timedelta(minutes=60),
            ended_at=now - timedelta(minutes=30),
        )
    )
    session.add(
        EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.IDLE, started_at=now - timedelta(minutes=30))
    )
    wo = workorders.create(session, code="WO-OEE", material_code="FG-COLA", quantity=20)
    session.add(
        ProductionLog(work_order_id=wo.id, equipment_id=mixer.id, good_qty=18, scrap_qty=2,
                      ts=now - timedelta(minutes=40))
    )
    session.flush()

    result = equipment.oee(session, equipment_code="MIX01", hours=1.0)
    assert result["availability"] == pytest.approx(0.5, rel=0.02)
    assert result["performance"] == pytest.approx(4.0 * 20 / 1800, rel=0.02)
    assert result["quality"] == pytest.approx(0.9)
    assert result["oee"] == pytest.approx(0.5 * (4.0 * 20 / 1800) * 0.9, rel=0.05)


def _ran_and_made(session, *, minutes_running: float, units: int) -> None:
    """MIX01 ran for this long inside the last hour and counted this many."""
    mixer = masterdata.get_equipment(session, "MIX01")
    now = utcnow()
    session.add(
        EquipmentState(
            equipment_id=mixer.id,
            state=EquipmentStateName.RUNNING,
            started_at=now - timedelta(minutes=50),
            ended_at=now - timedelta(minutes=50 - minutes_running),
        )
    )
    wo = workorders.create(session, code="WO-PERF", material_code="FG-COLA", quantity=units)
    session.add(
        ProductionLog(work_order_id=wo.id, equipment_id=mixer.id, good_qty=units, scrap_qty=0,
                      ts=now - timedelta(minutes=40))
    )
    session.flush()


def test_a_machine_slower_than_its_rating_reports_below_one(session):
    """MIX01 is rated at 4 s a unit: 30 minutes running rates 450 units."""
    _ran_and_made(session, minutes_running=30, units=300)
    result = equipment.oee(session, equipment_code="MIX01", hours=1.0)
    assert result["performance"] == pytest.approx(4.0 * 300 / 1800, rel=0.02)
    assert result["performance_note"] is None


def test_a_machine_faster_than_its_rating_is_reported_above_one_with_the_reason(session):
    """The cap this replaces made every station in the lab read exactly 1.0.
    Above rated is a master-data finding, not a score, and it is printed."""
    _ran_and_made(session, minutes_running=30, units=900)
    result = equipment.oee(session, equipment_code="MIX01", hours=1.0)
    assert result["performance"] == pytest.approx(2.0, rel=0.02)
    assert "rating is slower than the machine" in result["performance_note"]


def test_a_machine_with_no_rated_cycle_reports_performance_unknown(session):
    masterdata.get_equipment(session, "MIX01").ideal_cycle_seconds = None
    _ran_and_made(session, minutes_running=30, units=300)
    result = equipment.oee(session, equipment_code="MIX01", hours=1.0)
    assert result["performance"] is None
    assert result["performance_note"] == oee_rules.NO_RATING
    assert result["oee"] is None
