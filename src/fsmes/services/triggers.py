"""Triggers: the lifecycle, the catalog of actions, and the evaluator.

The evaluator lives in the OPC agent's loop: every reading is a question
("does any approved trigger care about this tag on this machine?"), and a
condition that holds for `sustained_seconds` fires the trigger's action
once, then goes quiet for `cooldown_seconds`. Actions are product code with
tests, never plant-specific scripts; a plant adds a trigger, not a program.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import EquipmentStateName, Trigger, TriggerCondition, TriggerFiring, TriggerStatus
from fsmes.services import Conflict, Invalid, NotFound, audit, equipment, maintenance, masterdata, quality

# --------------------------------------------------------------- the catalog


def _log_event(session: Session, trigger: Trigger, equipment_code: str, tag: str, value, params: dict) -> dict:
    audit.record(session, actor="trigger", action="trigger.fired", entity_type="equipment",
                 entity_id=equipment_code,
                 after={"trigger": trigger.code, "tag": tag, "value": value,
                        "message": params.get("message") or trigger.name})
    return {"logged": True}


def _open_nc(session: Session, trigger: Trigger, equipment_code: str, tag: str, value, params: dict) -> dict:
    description = params.get("description") or f"{trigger.name}: {equipment_code} {tag}={value}"
    nc = quality.open_nc(session, description=description, severity=params.get("severity", "minor"),
                         actor="trigger")
    return {"nonconformance": nc.code}


def _create_maintenance_order(session: Session, trigger: Trigger, equipment_code: str, tag: str, value,
                              params: dict) -> dict:
    order = maintenance.raise_corrective(
        session, equipment_code=equipment_code,
        summary=params.get("summary") or trigger.name,
        reason=params.get("reason") or f"{tag}={value} ({trigger.condition.value} {trigger.threshold:g})",
        actor="trigger")
    return {"maintenance_order": order.code}


def _set_machine_down(session: Session, trigger: Trigger, equipment_code: str, tag: str, value,
                      params: dict) -> dict:
    state = equipment.set_state(session, equipment_code=equipment_code, state=EquipmentStateName.DOWN,
                                reason=params.get("reason") or f"{trigger.name} ({tag}={value})",
                                actor="trigger")
    return {"state": state.state.value, "reason": state.reason}


def _propose_adjustment(session: Session, trigger: Trigger, equipment_code: str, tag: str, value,
                        params: dict) -> dict:
    """Recommend a setpoint change - into the queue, never to the PLC."""
    from fsmes.services import adjustments

    target = params.get("setpoint")
    if not target or params.get("value") is None:
        raise Invalid("propose_adjustment needs params setpoint and value")
    rec = adjustments.propose(
        session, equipment_code=equipment_code, tag=target, value=float(params["value"]),
        rationale=params.get("rationale") or f"{trigger.name}: {tag}={value}",
        evidence={"trigger": trigger.code, "tag": tag, "value": value}, actor="trigger")
    return {"adjustment": rec.code}


def _generate_coa(session: Session, trigger: Trigger, equipment_code: str, tag: str, value, params: dict) -> dict:
    """Issue the certificate for the order this machine is running (or the
    one named in params). The end-of-line complete tag is the intended
    caller; an order that is not yet complete is certified as it stands
    only if params say allow_incomplete."""
    from fsmes.services import coa, workorders

    order = params.get("order")
    if not order:
        current = next(iter(workorders.dispatch_list(session, equipment_code)), None)
        if current is None:
            raise Invalid(f"no order is running on {equipment_code} to certify")
        order = current.order.code
    doc = coa.issue(session, order, actor="trigger", allow_incomplete=bool(params.get("allow_incomplete")))
    return {"certificate": doc.code, "revision": doc.revision}


ACTIONS: dict[str, Callable] = {
    "generate_coa": _generate_coa,
    "propose_adjustment": _propose_adjustment,
    "log_event": _log_event,
    "open_nc": _open_nc,
    "create_maintenance_order": _create_maintenance_order,
    "set_machine_down": _set_machine_down,
}

ACTION_HELP = {
    "generate_coa": "Issue the certificate of analysis for the order running on the machine "
                    "(or params.order). Params: order, allow_incomplete.",
    "propose_adjustment": "Put a setpoint change in the recommendation queue for a person to approve. "
                          "Params: setpoint (the writable tag), value, rationale.",
    "log_event": "Write an audit row naming the trigger, tag and value. Params: message.",
    "open_nc": "Open a non-conformance. Params: description, severity (minor|major|critical).",
    "create_maintenance_order": "Raise corrective maintenance on the machine. Params: summary, reason.",
    "set_machine_down": "Set the machine down with the trigger as the reason. Params: reason.",
}


# --------------------------------------------------------------- lifecycle


def create(session: Session, *, code: str, name: str, tag: str, condition: str, threshold: float,
           equipment_code: str | None = None, sustained_seconds: float = 0.0,
           cooldown_seconds: float = 300.0, action: str = "log_event", action_params: dict | None = None,
           note: str | None = None, actor: str = "system") -> Trigger:
    if session.scalar(select(Trigger).where(Trigger.code == code)):
        raise Conflict(f"trigger {code} already exists")
    try:
        kind = TriggerCondition(condition)
    except ValueError as exc:
        raise Invalid(f"unknown condition {condition!r}; expected one of "
                      f"{', '.join(c.value for c in TriggerCondition)}") from exc
    if action not in ACTIONS:
        raise Invalid(f"unknown action {action!r}; the catalog has {', '.join(ACTIONS)}")
    if equipment_code:
        masterdata.get_equipment(session, equipment_code)  # must exist
    if sustained_seconds < 0 or cooldown_seconds < 0:
        raise Invalid("sustained and cooldown seconds cannot be negative")
    trigger = Trigger(code=code, name=name, equipment_code=equipment_code, tag=tag, condition=kind,
                      threshold=threshold, sustained_seconds=sustained_seconds,
                      cooldown_seconds=cooldown_seconds, action=action, action_params=action_params or {},
                      note=note, created_by=actor)
    session.add(trigger)
    session.flush()
    audit.record(session, actor=actor, action="trigger.drafted", entity_type="trigger", entity_id=code,
                 after={"tag": tag, "condition": kind.value, "threshold": threshold, "action": action,
                        "equipment": equipment_code})
    return trigger


def get(session: Session, code: str) -> Trigger:
    trigger = session.scalar(select(Trigger).where(Trigger.code == code))
    if trigger is None:
        raise NotFound(f"no trigger {code}")
    return trigger


def approve(session: Session, code: str, actor: str = "system") -> Trigger:
    trigger = get(session, code)
    if trigger.status is not TriggerStatus.DRAFT:
        raise Invalid(f"{code} is {trigger.status.value}, not a draft")
    trigger.status = TriggerStatus.APPROVED
    trigger.approved_by = actor
    trigger.approved_at = utcnow()
    audit.record(session, actor=actor, action="trigger.approved", entity_type="trigger", entity_id=code,
                 after={"action": trigger.action, "tag": trigger.tag})
    return trigger


def withdraw(session: Session, code: str, actor: str = "system") -> Trigger:
    trigger = get(session, code)
    if trigger.status is TriggerStatus.WITHDRAWN:
        raise Invalid(f"{code} is already withdrawn")
    before = trigger.status.value
    trigger.status = TriggerStatus.WITHDRAWN
    audit.record(session, actor=actor, action="trigger.withdrawn", entity_type="trigger", entity_id=code,
                 before={"status": before}, after={"status": "withdrawn"})
    return trigger


def out(trigger: Trigger) -> dict:
    return {
        "code": trigger.code, "name": trigger.name, "equipment": trigger.equipment_code, "tag": trigger.tag,
        "condition": trigger.condition.value, "threshold": trigger.threshold,
        "sustained_seconds": trigger.sustained_seconds, "cooldown_seconds": trigger.cooldown_seconds,
        "action": trigger.action, "action_params": trigger.action_params or {}, "note": trigger.note,
        "status": trigger.status.value, "created_by": trigger.created_by, "created_at": trigger.created_at,
        "approved_by": trigger.approved_by, "approved_at": trigger.approved_at,
        "last_fired_at": trigger.last_fired_at, "fire_count": trigger.fire_count,
    }


def listing(session: Session, status: str | None = None, *, equipment: str | None = None,
            q: str | None = None) -> list[dict]:
    query = select(Trigger).order_by(Trigger.code)
    if status:
        query = query.where(Trigger.status == TriggerStatus(status))
    rows = [out(t) for t in session.scalars(query)]
    if equipment:
        rows = [r for r in rows if r.get("equipment") in (equipment, None)]
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in f"{r.get('code', '')} {r.get('name', '')} {r.get('tag', '')}".lower()]
    return rows


def firings(session: Session, code: str, limit: int = 50) -> list[dict]:
    trigger = get(session, code)
    rows = session.scalars(select(TriggerFiring).where(TriggerFiring.trigger_id == trigger.id)
                           .order_by(TriggerFiring.id.desc()).limit(limit))
    return [{"ts": f.ts, "equipment": f.equipment_code, "tag": f.tag, "value": f.value,
             "action": f.action, "ok": f.ok, "outcome": f.outcome} for f in rows]


# --------------------------------------------------------------- evaluation


def holds(condition: TriggerCondition, threshold: float, value: float, previous: float | None) -> bool:
    if condition is TriggerCondition.ABOVE:
        return value > threshold
    if condition is TriggerCondition.BELOW:
        return value < threshold
    if condition is TriggerCondition.EQUALS:
        return value == threshold
    if condition is TriggerCondition.BIT_SET:
        return (int(value) >> int(threshold)) & 1 == 1
    if condition is TriggerCondition.RISES_ABOVE:
        return previous is not None and previous <= threshold < value
    if condition is TriggerCondition.FALLS_BELOW:
        return previous is not None and previous >= threshold > value
    return False


@dataclass
class _Watch:
    """What the evaluator remembers per (trigger, machine)."""

    since: datetime | None = None      # when the condition started holding
    previous: float | None = None
    fired_at: datetime | None = None


@dataclass
class Evaluator:
    """Feed it readings; it says which approved triggers fire.

    Definitions are reloaded every `reload_seconds` so an approval on screen
    reaches the agent without a restart. State is per (trigger, machine) so a
    trigger on "any machine" watches each one separately.
    """

    scope: Callable
    reload_seconds: float = 30.0
    clock: Callable[[], datetime] = utcnow
    _triggers: list[Trigger] = field(default_factory=list)
    _loaded_at: datetime | None = None
    _watch: dict[tuple[str, str], _Watch] = field(default_factory=dict)

    def _load(self) -> None:
        now = self.clock()
        if self._loaded_at and (now - self._loaded_at).total_seconds() < self.reload_seconds:
            return
        with self.scope() as session:
            rows = session.scalars(select(Trigger).where(Trigger.status == TriggerStatus.APPROVED)).all()
            # Detach: the agent keeps these across sessions.
            self._triggers = [Trigger(id=t.id, code=t.code, name=t.name, equipment_code=t.equipment_code,
                                      tag=t.tag, condition=t.condition, threshold=t.threshold,
                                      sustained_seconds=t.sustained_seconds,
                                      cooldown_seconds=t.cooldown_seconds, action=t.action,
                                      action_params=dict(t.action_params or {}), status=t.status)
                              for t in rows]
        self._loaded_at = now

    def observe(self, equipment_code: str, tag: str, value) -> list[dict]:
        """One reading in; the firings it caused out (already executed)."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return []
        self._load()
        now = self.clock()
        fired = []
        for trigger in self._triggers:
            if trigger.tag != tag or (trigger.equipment_code and trigger.equipment_code != equipment_code):
                continue
            watch = self._watch.setdefault((trigger.code, equipment_code), _Watch())
            if holds(trigger.condition, trigger.threshold, float(value), watch.previous):
                watch.since = watch.since or now
                held = (now - watch.since).total_seconds()
                cooled = watch.fired_at is None or (now - watch.fired_at).total_seconds() >= trigger.cooldown_seconds
                if held >= trigger.sustained_seconds and cooled:
                    watch.fired_at = now
                    fired.append(self._fire(trigger, equipment_code, tag, float(value), now))
            else:
                watch.since = None
            watch.previous = float(value)
        return fired

    def _fire(self, trigger: Trigger, equipment_code: str, tag: str, value: float, now: datetime) -> dict:
        action = ACTIONS[trigger.action]
        with self.scope() as session:
            live = session.get(Trigger, trigger.id)
            try:
                outcome = action(session, live, equipment_code, tag, value, dict(live.action_params or {}))
                ok = True
            except Exception as exc:  # a failing action is a recorded firing, not a dead agent
                outcome, ok = {"error": str(exc)[:300]}, False
            live.last_fired_at = now
            live.fire_count = (live.fire_count or 0) + 1
            session.add(TriggerFiring(trigger_id=live.id, ts=now, equipment_code=equipment_code, tag=tag,
                                      value=value, action=live.action, ok=ok, outcome=outcome))
            return {"trigger": trigger.code, "equipment": equipment_code, "tag": tag, "value": value,
                    "action": trigger.action, "ok": ok, "outcome": outcome}


def since(session: Session, hours: float = 24.0, *, trigger: str | None = None,
          equipment: str | None = None, ok: bool | None = None, limit: int = 50,
          offset: int = 0) -> tuple[list[dict], int]:
    """One page of the firings in the window, newest first, and how many
    there are - the plant's trigger log."""
    start = utcnow() - timedelta(hours=hours)
    query = select(TriggerFiring).where(TriggerFiring.ts >= start)
    if trigger:
        query = query.where(TriggerFiring.trigger_id == get(session, trigger).id)
    if equipment:
        query = query.where(TriggerFiring.equipment_code == equipment)
    if ok is not None:
        query = query.where(TriggerFiring.ok.is_(ok))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(TriggerFiring.id.desc()).limit(limit).offset(offset))
    return [{"trigger": f.trigger.code, "ts": f.ts, "equipment": f.equipment_code, "tag": f.tag,
             "value": f.value, "action": f.action, "ok": f.ok, "outcome": f.outcome} for f in rows], total
