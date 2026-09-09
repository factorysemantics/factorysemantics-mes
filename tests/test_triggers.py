"""Triggers: conditions as data, actions from a catalog, and an evaluator that
fires once, after the condition has held, then goes quiet.

The plant adds a trigger, not a program. These tests hold the lifecycle
(nothing fires until approved), the evaluator's discipline (sustained,
cooldown, per-machine state, edge conditions), the catalog (each action does
its real thing and a failing action is a recorded firing), and the gates
(approval is a person's capability).
"""

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from fsmes.domain import EquipmentState, MaintenanceOrder, NonConformance, TriggerCondition
from fsmes.services import triggers


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 3, 12, 0, 0)

    def tick(self, seconds: float):
        self.now += timedelta(seconds=seconds)

    def __call__(self):
        return self.now


@pytest.fixture()
def evaluator(scope):
    clock = Clock()
    ev = triggers.Evaluator(scope=scope, reload_seconds=0, clock=clock)
    return ev, clock


def _draft(session, code="T-HOT", **kw):
    defaults = dict(name="Washer too hot", tag="WashTemp", condition="above", threshold=80.0,
                    equipment_code="MIX01", sustained_seconds=0, cooldown_seconds=60, action="log_event")
    defaults.update(kw)
    return triggers.create(session, code=code, actor="eng", **defaults)


def test_conditions_including_the_edge_ones():
    h = triggers.holds
    assert h(TriggerCondition.ABOVE, 80, 81, None) and not h(TriggerCondition.ABOVE, 80, 80, None)
    assert h(TriggerCondition.BELOW, 5, 4.9, None)
    assert h(TriggerCondition.EQUALS, 3, 3, None)
    assert h(TriggerCondition.BIT_SET, 0, 1, None) and h(TriggerCondition.BIT_SET, 3, 0b1000, None)
    assert not h(TriggerCondition.BIT_SET, 1, 0b1000, None)
    assert h(TriggerCondition.RISES_ABOVE, 80, 81, 79) and not h(TriggerCondition.RISES_ABOVE, 80, 81, 81)
    assert not h(TriggerCondition.RISES_ABOVE, 80, 81, None), "no previous reading - no edge"
    assert h(TriggerCondition.FALLS_BELOW, 5, 4, 6)


def test_a_draft_watches_nothing_and_an_approved_trigger_fires_once_then_cools(session, evaluator):
    ev, clock = evaluator
    _draft(session)
    session.flush()
    assert ev.observe("MIX01", "WashTemp", 95.0) == []          # a draft is not in force
    triggers.approve(session, "T-HOT", actor="admin")
    session.flush()
    fired = ev.observe("MIX01", "WashTemp", 95.0)
    assert [f["trigger"] for f in fired] == ["T-HOT"] and fired[0]["ok"] is True
    assert ev.observe("MIX01", "WashTemp", 96.0) == [], "cooling down"
    clock.tick(61)
    assert len(ev.observe("MIX01", "WashTemp", 96.0)) == 1, "cooled, still hot, fires again"
    assert ev.observe("PACK01", "WashTemp", 96.0) == [], "bound to one machine"
    t = triggers.get(session, "T-HOT")
    session.refresh(t)
    assert t.fire_count == 2 and t.last_fired_at == clock.now
    assert len(triggers.firings(session, "T-HOT")) == 2


def test_sustained_means_the_condition_must_hold_for_that_long(session, evaluator):
    ev, clock = evaluator
    _draft(session, "T-SUS", sustained_seconds=30)
    triggers.approve(session, "T-SUS", actor="admin")
    session.flush()
    assert ev.observe("MIX01", "WashTemp", 90.0) == []
    clock.tick(10)
    assert ev.observe("MIX01", "WashTemp", 90.0) == []
    clock.tick(5)
    assert ev.observe("MIX01", "WashTemp", 70.0) == []          # dipped: the clock restarts
    clock.tick(40)
    assert ev.observe("MIX01", "WashTemp", 90.0) == []          # held for 0 s again
    clock.tick(31)
    assert len(ev.observe("MIX01", "WashTemp", 90.0)) == 1


def test_any_machine_triggers_keep_state_per_machine(session, evaluator):
    ev, _clock = evaluator
    _draft(session, "T-ANY", equipment_code=None, tag="AlarmWord", condition="bit_set", threshold=0)
    triggers.approve(session, "T-ANY", actor="admin")
    session.flush()
    assert len(ev.observe("MIX01", "AlarmWord", 1)) == 1
    assert len(ev.observe("PACK01", "AlarmWord", 1)) == 1, "another machine, its own cooldown"
    assert ev.observe("MIX01", "AlarmWord", 1) == []
    assert ev.observe("MIX01", "AlarmWord", "text") == [], "a non-numeric reading is ignored"


def test_the_catalog_does_real_things_and_a_failing_action_is_a_recorded_firing(session, evaluator):
    ev, _ = evaluator
    _draft(session, "T-NC", action="open_nc", action_params={"severity": "major"})
    _draft(session, "T-PM", action="create_maintenance_order", action_params={"summary": "Cool the washer"})
    _draft(session, "T-DOWN", action="set_machine_down")
    for code in ("T-NC", "T-PM", "T-DOWN"):
        triggers.approve(session, code, actor="admin")
    session.flush()
    fired = ev.observe("MIX01", "WashTemp", 99.0)
    assert {f["trigger"] for f in fired} == {"T-NC", "T-PM", "T-DOWN"} and all(f["ok"] for f in fired)
    nc = session.scalar(select(NonConformance).order_by(NonConformance.id.desc()))
    assert nc.severity == "major" and "WashTemp=99.0" in nc.description
    order = session.scalar(select(MaintenanceOrder).order_by(MaintenanceOrder.id.desc()))
    assert order.summary == "Cool the washer" and order.equipment.code == "MIX01"
    state = session.scalar(select(EquipmentState).where(EquipmentState.ended_at.is_(None))
                           .order_by(EquipmentState.id.desc()))
    assert state.state.value == "down" and "Washer too hot" in state.reason

    # An action that raises is recorded as a failed firing, not a crashed agent.
    triggers.ACTIONS["explode"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        _draft(session, "T-BOOM", action="explode", cooldown_seconds=0)
        triggers.approve(session, "T-BOOM", actor="admin")
        session.flush()
        ev._loaded_at = None
        [f] = [f for f in ev.observe("MIX01", "WashTemp", 99.0) if f["trigger"] == "T-BOOM"]
        assert f["ok"] is False and "boom" in f["outcome"]["error"]
    finally:
        del triggers.ACTIONS["explode"]


def test_unknown_actions_and_conditions_are_refused_at_draft_time(session):
    with pytest.raises(Exception, match="unknown action"):
        _draft(session, "T-X", action="format_disk")
    with pytest.raises(Exception, match="unknown condition"):
        _draft(session, "T-Y", condition="wobbles")


def test_the_api_gates_drafting_and_approval_separately(session, sign_in):
    sup = sign_in("SUP-TRG", role="supervisor")
    body = {"code": "T-API", "name": "Belt slow", "tag": "CycleTimeMs", "condition": "above",
            "threshold": 4000, "equipment": "MIX01", "action": "log_event"}
    r = sup.post("/triggers", json=body)
    assert r.status_code == 201, r.text
    assert sup.post("/triggers/T-API/approve").status_code == 403, "approval is not a supervisor's"
    admin = sign_in("ADM-TRG", role="admin")
    assert admin.post("/triggers/T-API/approve").json()["status"] == "approved"
    assert admin.get("/triggers?status=approved").json()[0]["code"] == "T-API"
    assert "open_nc" in admin.get("/triggers/catalog").json()["actions"]
    assert admin.get("/triggers/T-API/firings").json() == []
    assert admin.get("/triggers/firings").json()["items"] == []
    assert admin.post("/triggers/T-API/withdraw").json()["status"] == "withdrawn"
    op = sign_in("OP-TRG", role="operator")
    assert op.post("/triggers", json={**body, "code": "T-OP"}).status_code == 403


@contextmanager
def _noop():
    yield None
