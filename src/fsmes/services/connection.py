"""Whether this MES can see a machine, recorded apart from what the machine is doing.

Decision 0030. Two facts, two histories: `equipment_states` says what the
machine was doing, `equipment_connections` says whether anything could still
see it. A machine can be broken and reachable, or fine and unreachable, and a
plant that has to pick one column for both has thrown the useful half away.

The rule that makes the rest of the product honest is in `set_connection`:
**recording a disconnection closes the machine's open state interval and opens
nothing in its place.** For the length of the outage the machine has no open
state, which every screen and every API already answers as *unknown* - the
word they use for a machine they have no interval for. Nothing carries on
accruing: not run time, not downtime, not the maintenance hours that used to
tick up against a machine nobody could see.
"""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import (
    ConnectionStateName,
    Equipment,
    EquipmentConnection,
    EquipmentLevel,
    EquipmentState,
)
from fsmes.services import audit, masterdata, outbox


def set_connection(
    session: Session,
    *,
    equipment_code: str,
    state: ConnectionStateName,
    at: datetime | None = None,
    detected_at: datetime | None = None,
    reason: str | None = None,
    source: str | None = None,
    actor: str = "opc-agent",
) -> EquipmentConnection:
    """Close the open connection interval and start a new one. No-op if unchanged.

    `at` is the last moment there was positive evidence of the link, which for
    a disconnection is *earlier* than the moment it was noticed; `detected_at`
    is when it was noticed. Both are kept, because the seconds between them
    are the seconds nobody can say anything about.
    """
    equipment = masterdata.get_equipment(session, equipment_code)
    noticed = detected_at or utcnow()
    moment = at or noticed
    current = session.scalar(
        select(EquipmentConnection).where(
            EquipmentConnection.equipment_id == equipment.id,
            EquipmentConnection.ended_at.is_(None),
        )
    )
    if current is not None and current.state is state:
        return current
    # An interval can never end before it began: a clock that went backwards,
    # or evidence older than the interval it is closing, is clamped rather
    # than written as a negative stretch.
    if current is not None:
        current.ended_at = max(moment, current.started_at)
        moment = current.ended_at
    new = EquipmentConnection(
        equipment_id=equipment.id,
        state=state,
        started_at=moment,
        detected_at=max(noticed, moment),
        reason=reason,
        source=source,
    )
    session.add(new)
    session.flush()
    if state is ConnectionStateName.DISCONNECTED:
        _stop_the_state_history(session, equipment_id=equipment.id, at=moment)
    audit.record(
        session,
        actor=actor,
        action="equipment.connection_changed",
        entity_type="equipment",
        entity_id=equipment_code,
        before={"connection": current.state.value} if current else None,
        after={"connection": state.value, "reason": reason, "source": source,
               "since": moment.isoformat(), "detected_at": new.detected_at.isoformat()},
    )
    outbox.equipment_connection_changed(session, equipment=equipment, opened=new,
                                        closed=current, actor=actor)
    return new


def _stop_the_state_history(session: Session, *, equipment_id: int, at: datetime) -> None:
    """End the machine's open state interval where the evidence ends.

    Not "set it to unknown": there is no such state, and inventing one would
    put a machine nobody watched into the downtime pareto, the ERP
    confirmation and every consumer that switches on the enum. The interval
    simply stops, and the machine has no open state until something sees it
    again.
    """
    open_state = session.scalar(
        select(EquipmentState).where(
            EquipmentState.equipment_id == equipment_id,
            EquipmentState.ended_at.is_(None),
        )
    )
    if open_state is not None:
        open_state.ended_at = max(at, open_state.started_at)


def open_connections(session: Session, equipment_ids: list[int]) -> dict[int, EquipmentConnection]:
    """The connection interval each machine is in right now, in one query.
    A machine absent from the result has no connection fact at all."""
    if not equipment_ids:
        return {}
    rows = session.scalars(
        select(EquipmentConnection).where(
            EquipmentConnection.equipment_id.in_(equipment_ids),
            EquipmentConnection.ended_at.is_(None),
        )
    )
    return {row.equipment_id: row for row in rows}


def summary(connection: EquipmentConnection | None) -> dict:
    """One machine's connection, as every screen and API states it.

    `unknown` is not a failure to answer: it means no agent has ever reported
    a connection for this machine, so this MES has no basis for saying it is
    connected. A machine fed by hand or over MQTT stays unknown for ever, and
    that is the truth about it.
    """
    if connection is None:
        return {"state": "unknown", "since": None, "detected_at": None,
                "reason": None, "source": None,
                "note": "no agent has reported a connection for this machine"}
    return {
        "state": connection.state.value,
        "since": connection.started_at,
        "detected_at": connection.detected_at,
        "reason": connection.reason,
        "source": connection.source,
        "note": None,
    }


def _in_window(start: datetime, end: datetime):
    return (
        EquipmentConnection.started_at < end,
        or_(EquipmentConnection.ended_at.is_(None), EquipmentConnection.ended_at > start),
    )


def unknown_seconds(session: Session, equipment_ids: list[int], start: datetime,
                    end: datetime) -> dict[int, float]:
    """Seconds each machine was disconnected inside [start, end], clipped.

    This is the time no window may count and no denominator may include. Summed
    in the database for the same reason `equipment.state_seconds` is: a plant
    with a hundred stations must not load an interval per machine per refresh.
    """
    out: dict[int, float] = defaultdict(float)
    if not equipment_ids or end <= start:
        return out
    where = (
        EquipmentConnection.equipment_id.in_(equipment_ids),
        EquipmentConnection.state == ConnectionStateName.DISCONNECTED,
        *_in_window(start, end),
    )
    dialect = session.get_bind().dialect.name
    ended = EquipmentConnection.ended_at.type
    lo = case((EquipmentConnection.started_at < start, literal(start, ended)),
              else_=EquipmentConnection.started_at)
    hi = case((or_(EquipmentConnection.ended_at.is_(None), EquipmentConnection.ended_at > end),
               literal(end, ended)), else_=EquipmentConnection.ended_at)
    if dialect == "sqlite":
        seconds = (func.julianday(hi) - func.julianday(lo)) * 86400.0
    elif dialect == "postgresql":
        seconds = func.extract("epoch", hi - lo)
    else:
        seconds = None
    if seconds is not None:
        for equipment_id, total in session.execute(
            select(EquipmentConnection.equipment_id, func.sum(seconds))
            .where(*where).group_by(EquipmentConnection.equipment_id)
        ).all():
            out[equipment_id] += max(0.0, float(total or 0.0))
        return out
    for equipment_id, started_at, ended_at in session.execute(
        select(EquipmentConnection.equipment_id, EquipmentConnection.started_at,
               EquipmentConnection.ended_at).where(*where)
    ).all():
        lo_ = max(started_at, start)
        hi_ = min(ended_at or end, end)
        out[equipment_id] += max(0.0, (hi_ - lo_).total_seconds())
    return out


def first_seen(session: Session, equipment_ids: list[int]) -> dict[int, datetime]:
    """When the MES first recorded a connection fact for each machine.

    Half of "when did this MES start watching". The other half is the state
    history; a machine whose agent has never once reached its server has no
    state row at all, and a window clamped to the state history alone would
    say the MES has not been watching it - when in fact it has been watching
    it fail since the agent came up.
    """
    if not equipment_ids:
        return {}
    rows = session.execute(
        select(EquipmentConnection.equipment_id, func.min(EquipmentConnection.started_at))
        .where(EquipmentConnection.equipment_id.in_(equipment_ids))
        .group_by(EquipmentConnection.equipment_id)
    ).all()
    return {equipment_id: seen for equipment_id, seen in rows}


def intervals(session: Session, equipment_ids: list[int], start: datetime,
              end: datetime) -> dict[int, list[EquipmentConnection]]:
    """Every disconnection overlapping the window, per machine, oldest first —
    what the timeline draws as a gap."""
    if not equipment_ids or end <= start:
        return {}
    rows = session.scalars(
        select(EquipmentConnection)
        .where(
            EquipmentConnection.equipment_id.in_(equipment_ids),
            EquipmentConnection.state == ConnectionStateName.DISCONNECTED,
            *_in_window(start, end),
        )
        .order_by(EquipmentConnection.started_at)
    )
    out: dict[int, list[EquipmentConnection]] = defaultdict(list)
    for row in rows:
        out[row.equipment_id].append(row)
    return out


def watching(session: Session) -> dict:
    """How much of this plant the MES can currently see. Every list states its
    total, including this one: connected + disconnected + unknown = machines."""
    ids = list(session.scalars(
        select(Equipment.id).where(Equipment.level == EquipmentLevel.WORK_UNIT)))
    open_rows = open_connections(session, ids)
    disconnected = sum(1 for row in open_rows.values()
                       if row.state is ConnectionStateName.DISCONNECTED)
    connected = sum(1 for row in open_rows.values()
                    if row.state is ConnectionStateName.CONNECTED)
    return {
        "machines": len(ids),
        "connected": connected,
        "disconnected": disconnected,
        # Not a fault and not zero: nothing has ever reported a connection for
        # these, so this plant cannot say whether it can see them.
        "unknown": len(ids) - connected - disconnected,
    }
