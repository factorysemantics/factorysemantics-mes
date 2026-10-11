"""Why did the plant stop? One block of the state timeline, with its records.

The machine page draws a bar of running / idle / down / setup and, until this,
it was a picture a person could only look at. Clicking one block asks a
question every plant asks a dozen times a shift, and it is always the same
five:

    what was it, and how long?                    the state records across the
                                                  stretch that was drawn
    why - and who said so?                        the reason on the interval,
                                                  the plant's own code for it,
                                                  and the audit row of somebody
                                                  naming it
    what did it belong to?                        the order that was on before
                                                  and after, and any
                                                  maintenance order covering it
    what was the machine doing either side?       its own analogs over the
                                                  minutes before and after,
                                                  and the interval each side
    what did quality see after it?                the readings taken on this
                                                  machine after the block, with
                                                  the chart's own verdict

**No model, and nothing is computed twice.** The analogs come from
`moment.analog_trends`, which is `analysis.tag_trend` with the panel's
bucketing; the maintenance orders come from `moment.maintenance_orders`, the
same rows and the same bound the panel beside the control chart reads; the
watched share comes from the ledger in `services.coverage`. The reason
vocabulary is `services.reasons`, and the control that names a stop posts to
`POST /equipment/{code}/stops/label`, which already existed. This file adds no
second way to do anything.

**It is addressed by a window, not by an id**, and that is deliberate. What a
reader clicks is a drawn block, and a drawn block is clipped to the window on
screen and may be several state records folded together where they were too
brief to draw (`analysis._merge_short`). An id would address one of them and
the panel would then be about something other than what was clicked. So the
read takes the stretch the chart drew and says how many records it covers.

Three things in here are **derived** rather than recorded, and each says so in
its own answer, because a derivation presented as a record is the one way a
panel like this can lie:

- **The order.** Nothing in this schema joins a stop to a work order. The order
  is read off what this machine booked either side of the block, and off the
  operation records open across it. A changeover between two orders is exactly
  the case where the two can disagree, so both are reported.
- **Who named the stop.** `EquipmentState.reason_source` answers *which
  system*, and is null when somebody chose the label here from the plant's own
  vocabulary. The person is only in `audit_log`, which carries no join back to
  the interval - so it is matched on the window the labeller asked about, and
  where that cannot be done honestly the answer says "labelled here, by whom is
  not recorded" rather than naming anybody.
- **Planned or broken.** A maintenance order covering the block, in progress
  across it, with the stop's own reason naming it (#158 writes `planned
  maintenance <order>`) is planned work. An order raised for this machine and
  never started is not this stop, and the answer says that too - a plant that
  read every unstarted order as an explanation would explain every stop.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from fsmes.domain import (
    AuditLog,
    Equipment,
    EquipmentState,
    Material,
    ProductionLog,
    QualityCheck,
    QualitySpec,
    SpcSignal,
    WorkOrder,
    WorkOrderOperation,
)
from fsmes.services import (
    Invalid,
    capabilities,
    coverage,
    masterdata,
    moment,
    reasons,
    spc_point,
)
from fsmes.services import connection as connection_service

#: How far either side of the block its analogs are drawn. The same minutes the
#: panel beside the control chart uses, from the same constants, because the
#: two panels are the same question asked of a stretch and of an instant and a
#: reader who learns one has learnt the other.
BEFORE_MINUTES = spc_point.BEFORE_MINUTES
AFTER_MINUTES = spc_point.AFTER_MINUTES

#: How long after the block quality readings are looked for. An hour, not two
#: minutes: fill height on the lab's own plant is sampled four times an hour by
#: design, so a two-minute window after a four-minute changeover would find
#: nothing on the one characteristic the chain is about and the panel would
#: read as *quality saw nothing* when the truth is *nobody had measured yet*.
QUALITY_AFTER_MINUTES = 60.0

#: How far either side the order on the machine is looked for. A shift, because
#: a machine that was starved for two hours between two orders still has an
#: order before it, and because a figure this panel cannot find inside a shift
#: is one a person should go to the order book for rather than be guessed at.
ORDER_HOURS = 8.0

#: How far outside the block an audit row may sit and still be about it. The
#: state change and its audit row are written in one transaction off the same
#: clock, so this only has to absorb the milliseconds between them; a label
#: applied afterwards is matched on the window the labeller asked about and
#: needs no slack at all.
LABEL_SLACK_SECONDS = 5.0

#: The most records, readings and orders one panel lists. Every one of them
#: states its own total beside the list (house rule 3), so a block of nine
#: hundred folded micro-stops reads as nine hundred and shows fifty.
MOST_ROWS = 50

#: The capability that already gates naming a stop, and the write it gates.
#: Not a new power: `POST /equipment/{code}/stops/label` has carried this since
#: the labelling went in, and the panel is a door to it.
LABEL_CAPABILITY = "equipment.state"

#: What the blocks that are lists of records say instead of a coverage figure -
#: the same sentence the control chart's panel uses, from the same constant.
RECORDS_NOT_A_RATE = spc_point.RECORDS_NOT_A_RATE

#: The states that are not production. A reason belongs on one of these; a
#: block of running time with a reason on it is not a thing this product writes.
STOPPED = {"idle", "down", "setup", "blocked", "starved", "disconnected"}


# --------------------------------------------------------- what it was, how long


def _records(session: Session, unit: Equipment, start: datetime,
             end: datetime) -> list[dict]:
    """Every state record this MES holds across the stretch, clipped to it.

    The same bound `analysis.state_timeline` draws with, so what this lists is
    what was on the screen: an interval that began before the window or runs
    past its end is in, clipped, because that is how it was drawn.
    """
    rows = session.execute(
        select(EquipmentState.id, EquipmentState.state, EquipmentState.reason,
               EquipmentState.reason_code, EquipmentState.reason_source,
               EquipmentState.started_at, EquipmentState.ended_at,
               EquipmentState.shift_code, EquipmentState.shift_day)
        .where(EquipmentState.equipment_id == unit.id,
               EquipmentState.started_at < end,
               or_(EquipmentState.ended_at.is_(None), EquipmentState.ended_at > start))
        .order_by(EquipmentState.started_at)).all()
    out = []
    for row in rows:
        shown_start = max(row.started_at, start)
        shown_end = min(row.ended_at or end, end)
        out.append({
            "id": row.id,
            "state": getattr(row.state, "value", row.state),
            "reason": row.reason,
            "reason_code": row.reason_code,
            # Which system named it. Null means this MES did - a person here,
            # from this plant's own list - and never "nobody".
            "reason_source": row.reason_source,
            "started_at": row.started_at,
            "ended_at": row.ended_at,
            "open": row.ended_at is None,
            # The seconds of it that were on the screen, and the seconds it
            # ran in all. They differ on the records at either end of the
            # window, and a panel that showed only the first would make a
            # two-hour breakdown read as the twelve minutes that were drawn.
            "seconds": round(max(0.0, (shown_end - shown_start).total_seconds()), 1),
            "record_seconds": (None if row.ended_at is None else
                               round((row.ended_at - row.started_at).total_seconds(), 1)),
            "shift": row.shift_code,
            "shift_day": row.shift_day,
            "starts_before_window": row.started_at < start,
            "ends_after_window": bool(row.ended_at and row.ended_at > end),
        })
    return out


def _gaps(session: Session, unit: Equipment, start: datetime,
          end: datetime) -> list[dict]:
    """The stretches this MES could not see the machine at all.

    They are drawn on the bar as their own hatched blocks (decision 0030), so a
    click can land on one - and the answer then is not a stop with no reason
    but *nobody was looking*, which is a different sentence.
    """
    found = connection_service.intervals(session, [unit.id], start, end)
    return [{
        "state": "disconnected",
        "reason": gap.reason,
        "started_at": gap.started_at,
        "ended_at": gap.ended_at,
        "open": gap.ended_at is None,
        "seconds": round(max(0.0, (min(gap.ended_at or end, end)
                                   - max(gap.started_at, start)).total_seconds()), 1),
        # When this MES noticed, as against when the evidence stops. The
        # seconds between them belong to neither side.
        "detected_at": gap.detected_at,
    } for gap in found.get(unit.id, [])]


def _block(records: list[dict], gaps: list[dict], start: datetime,
           end: datetime) -> dict:
    """What the reader clicked: one state, how long, and out of how many records.

    The state is the one that holds the most of the stretch, which is the one
    the bar drew. Where a block is several records of several states - what
    `_merge_short` leaves when a run of micro-stops is folded into the block
    before it - the others are counted and listed rather than dropped.
    """
    seconds = round((end - start).total_seconds(), 1)
    held: dict[str, float] = {}
    for row in records + gaps:
        held[row["state"]] = held.get(row["state"], 0.0) + (row["seconds"] or 0.0)
    state = max(held, key=lambda name: held[name]) if held else None
    same = [r for r in records if r["state"] == state]
    shown = (records + gaps)[:MOST_ROWS]
    return {
        "state": state,
        "started_at": start,
        "ended_at": end,
        "seconds": seconds,
        # Whether the stretch runs on past what was drawn, either way. A block
        # at the left edge of an eight-hour window is usually the tail of
        # something longer, and a panel that said "4 minutes" of it would be
        # answering about the window rather than about the stop.
        "runs_back_before_window": any(r["starts_before_window"] for r in same),
        "runs_on_after_window": any(r["ends_after_window"] for r in same),
        "open": any(r["open"] for r in same),
        "shift": next((r["shift"] for r in same if r["shift"]), None),
        "records": shown,
        "records_total": len(records) + len(gaps),
        "records_shown": len(shown),
        # How many of the stretch's seconds the state above actually holds, and
        # what else is in there. One record and one state is the ordinary case
        # and reads as nothing; a folded run says so out loud.
        "seconds_by_state": {name: round(value, 1) for name, value in sorted(held.items())},
        "note": _block_note(records, gaps, state, held, seconds),
    }


def _block_note(records: list[dict], gaps: list[dict], state: str | None,
                held: dict[str, float], seconds: float) -> str | None:
    """The one sentence a reader needs about what this block is made of."""
    if not records and not gaps:
        return ("this MES holds no state record across that stretch. It is not that "
                "the machine stood still - nothing was recorded here at all, and the "
                "coverage line below says what was watched")
    count = len(records) + len(gaps)
    if count == 1:
        return None
    mine = round(held.get(state, 0.0), 1)
    others = [name for name in held if name != state]
    line = (f"this block is {count} records folded together, of which "
            f"{mine} s of {seconds} s is {state}")
    if others:
        line += f"; the rest is {', '.join(sorted(others))}"
    return (line + ". The bar folds records too brief to draw into the block before "
                   "them, so what was clicked is a stretch of time rather than one record")


# ------------------------------------------------------------ why, and who said so


def _labelled_by(session: Session, unit: Equipment, start: datetime,
                 end: datetime) -> list[dict]:
    """The audit rows of somebody naming a stop on this machine across the block.

    There is no join from an audit row to an interval, so this is a match on
    time and it says so. Two actions can carry a reason:

    - `equipment.stop_labelled` - somebody named a stop afterwards, over a
      window they chose. The window is in the row (`after.from`/`after.to`),
      so the match is an overlap with the block, which is exact; `to` left out
      means *up to now*, and now was when the row was written.
    - `equipment.state_changed` - a reason given at the moment the state
      opened. The row and the interval are written in one transaction off one
      clock, so this is matched on the row's own stamp inside the block.

    Oldest first, all of them. A block labelled twice by two people is a thing
    that happens and the panel shows both rather than picking one.
    """
    slack = timedelta(seconds=LABEL_SLACK_SECONDS)
    rows = session.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "equipment",
               AuditLog.entity_id == unit.code,
               AuditLog.action.in_(("equipment.stop_labelled", "equipment.state_changed")),
               # Cheap bound first: nothing written before the block began can
               # be about it, bar the slack, and a label is written after.
               AuditLog.ts >= start - slack)
        .order_by(AuditLog.ts)).all()
    out = []
    for row in rows:
        after = row.after or {}
        if row.action == "equipment.stop_labelled":
            asked_from = _stamp(after.get("from"))
            asked_to = _stamp(after.get("to")) or row.ts
            if asked_from is None or asked_from > end or asked_to < start:
                continue
            matched = ("the window this label was applied over overlaps the block"
                       + ("" if after.get("to") else
                          ", and it was left open-ended - *up to now*, where now "
                          "was when the label was written"))
            window = {"from": asked_from, "to": _stamp(after.get("to"))}
        else:
            if row.ts > end + slack:
                continue
            if not (after.get("reason") or after.get("reason_code")):
                continue
            matched = ("the state change was recorded inside the block, which is "
                       "when this reason was given")
            window = None
        out.append({
            "actor": row.actor,
            # The person an agent acted for. Null for somebody acting as
            # themselves, which is most rows.
            "on_behalf_of": row.on_behalf_of,
            "at": row.ts,
            "action": row.action,
            "reason": after.get("reason"),
            "reason_code": after.get("reason_code"),
            # How many intervals that one act of labelling named. More than
            # one means the label was applied over a window, not to this block.
            "intervals": after.get("intervals"),
            "window": window,
            "matched_by": matched,
            "shift": row.shift_code,
        })
    return out


def _stamp(value) -> datetime | None:
    """A datetime out of an audit row's JSON, which stores it as a string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _why(session: Session, unit: Equipment, block: dict, records: list[dict],
         gaps: list[dict], *, may_label: bool | None,
         roles: dict[str, list[str]] | None) -> dict:
    """The reason the floor gave this stop, who gave it, and who may give one.

    "Nobody has said why yet" is an answer and this says it plainly. What it
    never does is fill the gap: a stop with no reason stays a stop with no
    reason, and the control to name it appears where the capability already
    allows it and names who may where it does not (`refusal-names-who-can`).
    """
    said = [r for r in records if r.get("reason") or r.get("reason_code")]
    codes = reasons.labels(session)
    reason = said[0]["reason"] if said else None
    code = said[0]["reason_code"] if said else None
    source = said[0]["reason_source"] if said else None
    label = codes.get(code) if code else None
    by = _labelled_by(session, unit, block["started_at"], block["ended_at"])
    stop = (block["state"] or "") in STOPPED
    unlabelled = [r for r in records if r["state"] != "running" and not r.get("reason")]
    gap = gaps and not records
    return {
        "reason": reason,
        "reason_code": code,
        # The plant's word for the code, from the vocabulary in force. Null
        # where a code names nothing this plant has ever approved, which is a
        # finding and not a blank.
        "reason_label": label,
        "reason_source": source,
        # Said of how many of the block's records. A folded block where one
        # record of nine carries the reason is not a labelled block.
        "records_with_a_reason": len(said),
        "records_total": len(records),
        "labelled_by": by,
        "says": _why_says(block, reason, label, code, source, by, stop, gap),
        "derived": (
            "who named a stop is not on the interval. `reason_source` answers which "
            "system, and is empty when somebody here chose the label from this "
            "plant's own list - so the person is matched out of the audit log by "
            "time, and is left unnamed rather than guessed at"),
        "labelling": {
            "capability": LABEL_CAPABILITY,
            "what": capabilities.describe(LABEL_CAPABILITY) or None,
            # Null where the read was not told who is asking - a CLI, a test,
            # an agent - which is not the same as "no".
            "may": may_label,
            "who_may": capabilities.who_holds(LABEL_CAPABILITY, roles),
            # How many of this block's records a label would land on. The write
            # names unlabelled stops only: it never overwrites a label and
            # never contradicts a running observation.
            "would_name": len(unlabelled),
            "note": _labelling_note(block, unlabelled, stop, bool(gaps and not records)),
        },
    }


def _why_says(block: dict, reason: str | None, label: str | None, code: str | None,
              source: str | None, by: list[dict], stop: bool, gap: bool) -> str:
    """One sentence: the reason, and who said it, or that nobody has."""
    if gap:
        return ("this MES lost sight of the machine across this stretch, so there is "
                "no stop here to have a reason: nobody was looking")
    # Nothing recorded here at all is not a machine that was running. The two
    # read identically on a bar with a hole in it, and only one of them is a
    # statement about the machine.
    if block["state"] is None:
        return ("this MES holds no state record across this stretch, so there is no "
                "stop here to explain. What the machine was doing is not known, and "
                "the coverage line says as much")
    if not stop:
        return "the machine was running across this block, so there is no stop to explain"
    words = label or reason or code
    if not words:
        return ("nobody has said why yet. The stop is this MES's own observation; the "
                "reason is a thing a person adds, and nobody has")
    named = [row for row in by if row["action"] == "equipment.stop_labelled"]
    opened = [row for row in by if row["action"] == "equipment.state_changed"]
    if named:
        row = named[-1]
        return (f"{words} — named by {row['actor']} at "
                f"{row['at'].isoformat(timespec='seconds')}, after the stop")
    if opened:
        row = opened[-1]
        return (f"{words} — given by {row['actor']} when the state changed at "
                f"{row['at'].isoformat(timespec='seconds')}")
    if source:
        return f"{words} — supplied by {source}, which is a system and not a person"
    return (f"{words} — labelled here, by whom is not recorded. The reason is on the "
            f"interval and this MES holds no audit row matching this stretch")


def _labelling_note(block: dict, unlabelled: list[dict], stop: bool,
                    gap: bool) -> str | None:
    if gap:
        return ("a disconnection is not a stop anybody can name: it is this MES's own "
                "record of not being able to see the machine")
    if block["state"] is None:
        return ("there is no stop here to name: this MES holds no state record across "
                "this stretch at all")
    if not stop:
        return "the machine was running, and a running observation is never labelled"
    if not unlabelled:
        return ("every record in this block already carries a reason, and a label "
                "never overwrites one somebody already gave")
    if len(unlabelled) > 1:
        return (f"{len(unlabelled)} of this block's {block['records_total']} records "
                f"carry no reason, and naming it here names all of them — the block "
                f"is a stretch of time, not one record")
    return None


# ------------------------------------------------------- what it belonged to


def _booking(session: Session, unit: Equipment, *, before: datetime | None = None,
             after: datetime | None = None, floor: datetime,
             ceiling: datetime) -> dict | None:
    """The last quantity this machine booked before a moment, or the first after.

    A booking is the strongest recorded statement this MES holds about which
    order a machine was on, because it is the machine counting: an operation
    record says a step was started and a booking says something came out.
    """
    query = (select(WorkOrder.code, Material.code, ProductionLog.ts,
                    ProductionLog.good_qty, ProductionLog.scrap_qty)
             .join(WorkOrder, WorkOrder.id == ProductionLog.work_order_id)
             .join(Material, Material.id == WorkOrder.material_id)
             .where(ProductionLog.equipment_id == unit.id,
                    ProductionLog.ts >= floor, ProductionLog.ts <= ceiling))
    if before is not None:
        row = session.execute(query.where(ProductionLog.ts < before)
                              .order_by(ProductionLog.ts.desc()).limit(1)).first()
    else:
        row = session.execute(query.where(ProductionLog.ts > after)
                              .order_by(ProductionLog.ts).limit(1)).first()
    if row is None:
        return None
    order, material, ts, good, scrap = row
    return {"order": order, "material": material, "ts": ts,
            "good_qty": good, "scrap_qty": scrap}


def _operations(session: Session, unit: Equipment, floor: datetime,
                ceiling: datetime) -> list[dict]:
    """The operation records open on this machine anywhere near the block.

    Only ones that were actually started: an operation with no `started_at` is
    a plan, and a plan is not evidence about what the machine was doing.
    """
    rows = session.execute(
        select(WorkOrder.code, Material.code, WorkOrderOperation.seq,
               WorkOrderOperation.name, WorkOrderOperation.status,
               WorkOrderOperation.started_at, WorkOrderOperation.completed_at,
               WorkOrderOperation.good_qty, WorkOrderOperation.scrap_qty)
        .join(WorkOrder, WorkOrder.id == WorkOrderOperation.work_order_id)
        .join(Material, Material.id == WorkOrder.material_id)
        .where(WorkOrderOperation.equipment_id == unit.id,
               WorkOrderOperation.started_at.is_not(None),
               WorkOrderOperation.started_at <= ceiling,
               or_(WorkOrderOperation.completed_at.is_(None),
                   WorkOrderOperation.completed_at >= floor))
        .order_by(WorkOrderOperation.started_at)).all()
    return [{
        "order": row[0], "material": row[1], "seq": row.seq, "name": row.name,
        "status": getattr(row.status, "value", row.status),
        "started_at": row.started_at, "completed_at": row.completed_at,
        "good_qty": row.good_qty, "scrap_qty": row.scrap_qty,
    } for row in rows]


def _side(booking: dict | None, operation: dict | None, which: str,
          *, open_across: frozenset[str] = frozenset()) -> dict | None:
    """One side of the block: the order, and what says so.

    `open_across` is the orders whose operation records were open across the
    block itself. They settle a disagreement that is not one: the last
    operation record to COMPLETE before a stop is often an earlier order than
    the one the machine was booking, because the newer order is still running
    - and it is right there in "open across it". Only a conflict the records
    really hold is reported as one.
    """
    if booking is None and operation is None:
        return None
    order = booking["order"] if booking else operation["order"]
    material = booking["material"] if booking else operation["material"]
    agrees = bool(booking and operation and operation["order"] == order)
    if booking and agrees:
        from_what = (f"the last quantity this machine booked {which} the block, and the "
                     f"operation record that ran up to it" if which == "before" else
                     f"the first quantity this machine booked {which} the block, and the "
                     f"operation record that started after it")
    elif booking:
        from_what = f"a quantity this machine booked {which} the block"
    else:
        from_what = (f"an operation record on this machine {which} the block; it booked "
                     f"nothing inside the stretch looked at")
    return {
        "order": order,
        "material": material,
        "booked": booking,
        "operation": operation,
        "from": from_what,
        # Where the two kinds of record name different orders AND nothing says
        # they are the same job seen twice. A changeover is exactly where that
        # happens, and picking one silently would make the panel's cleanest
        # sentence its least trustworthy.
        "operation_names_another_order": (
            operation["order"] if operation and operation["order"] != order
            and order not in open_across else None),
    }


def _orders(session: Session, unit: Equipment, start: datetime,
            end: datetime) -> dict:
    """The job that was on: before the block, across it, and after.

    Derived, and the answer says so in `derived`: nothing in this schema joins
    a stop to an order. What it has is what the machine booked and which
    operation records were open, and those are the two statements it reports.
    """
    floor = start - timedelta(hours=ORDER_HOURS)
    ceiling = end + timedelta(hours=ORDER_HOURS)
    ops = _operations(session, unit, floor, ceiling)
    during_ops = [o for o in ops
                  if o["started_at"] <= end and (o["completed_at"] is None
                                                 or o["completed_at"] >= start)]
    before_ops = [o for o in ops if o["completed_at"] and o["completed_at"] <= start]
    after_ops = [o for o in ops if o["started_at"] >= end]
    before_booking = _booking(session, unit, before=start, floor=floor, ceiling=ceiling)
    after_booking = _booking(session, unit, after=end, floor=floor, ceiling=ceiling)
    during = session.execute(
        select(WorkOrder.code, Material.code, func.min(ProductionLog.ts),
               func.max(ProductionLog.ts), func.sum(ProductionLog.good_qty),
               func.sum(ProductionLog.scrap_qty), func.count(ProductionLog.id))
        .join(WorkOrder, WorkOrder.id == ProductionLog.work_order_id)
        .join(Material, Material.id == WorkOrder.material_id)
        .where(ProductionLog.equipment_id == unit.id,
               ProductionLog.ts >= start, ProductionLog.ts <= end)
        .group_by(WorkOrder.code, Material.code)
        .order_by(func.min(ProductionLog.ts))).all()
    booked_during = [{
        "order": row[0], "material": row[1], "first_booking": row[2],
        "last_booking": row[3], "good_qty": row[4], "scrap_qty": row[5],
        "bookings": row[6],
    } for row in during]
    open_across = frozenset(o["order"] for o in during_ops)
    return {
        "before": _side(before_booking, before_ops[-1] if before_ops else None, "before",
                        open_across=open_across),
        "after": _side(after_booking, after_ops[0] if after_ops else None, "after",
                       open_across=open_across),
        # Across the block itself: an order can be open through a stop without
        # booking anything, which is what a changeover looks like from here.
        "during": during_ops[:MOST_ROWS],
        "during_total": len(during_ops),
        "booked_during": booked_during[:MOST_ROWS],
        "booked_during_total": len(booked_during),
        "searched_hours": ORDER_HOURS,
        "derived": (
            "a stop carries no link to an order anywhere in this MES. The order either "
            "side is read off what this machine booked and which operation records were "
            "open, inside "
            f"{ORDER_HOURS:g} hours either way — derived from what was running, not a "
            "recorded link"),
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


# --------------------------------------------------- planned work, or a breakdown


def _maintenance(session: Session, unit: Equipment, block: dict, why: dict,
                 start: datetime, end: datetime) -> dict:
    """Any maintenance order covering the block, and whether this stop was planned.

    The rows are `moment.maintenance_orders` - the same list, the same bound
    - with the one verdict this panel exists to give: a planned stop must read
    as planned work and not as a breakdown with a note attached.

    Planned means a recorded order that was *in progress* across the block, not
    merely raised, and one that could have been the stop: a preventive job this
    plant says needs the line stopped, or one the stop's own reason names. An
    order raised and never started is reported too, and named as what it is:
    not this stop. So is corrective work, which is raised after something broke
    and is therefore the repair of a stop rather than a reason for one.
    """
    found = moment.maintenance_orders(session, unit, start, end)
    orders = found["orders"]
    reason = (why.get("reason") or "").lower()
    in_progress = [o for o in orders
                   if o["started_at"] and o["started_at"] <= end
                   and (o["completed_at"] is None or o["completed_at"] >= start)]
    waiting = [o for o in orders if o["started_at"] is None]
    named = [o["code"] for o in orders if o["code"] and o["code"].lower() in reason]
    stop = (block["state"] or "") in STOPPED
    # Planned is not the same as "maintenance was happening". A corrective
    # order is raised *after* something broke, and a preventive one this plant
    # says does not need the line stopped cannot be why the line stopped - so
    # neither of those makes a stop planned work. Both are still reported, as
    # what they are. Found 2026-10-11 on a replayed shift, where a preventive
    # order with `needs_stop` false was worked for seven minutes while the
    # machine ran, and would have been offered as the reason for a jam.
    planned = None if not stop else bool(
        [o for o in in_progress if o["code"] in named and o["kind"] == "preventive"]
        or [o for o in in_progress if o["kind"] == "preventive" and o["needs_stop"]])
    return {
        **found,
        "planned": planned,
        "in_progress": [o["code"] for o in in_progress],
        "named_in_the_reason": named,
        "never_started": [o["code"] for o in waiting],
        "says": _planned_says(block, orders, in_progress, waiting, named, stop),
    }


def _planned_says(block: dict, orders: list[dict], in_progress: list[dict],
                  waiting: list[dict], named: list[str], stop: bool) -> str:
    if block["state"] is None:
        return ("this MES holds no state record across this stretch, so there is no "
                "stop here to place against a maintenance order")
    if not stop:
        return "the machine was running across this block, so there is no stop to place"
    named_live = [o for o in in_progress if o["code"] in named]
    if named_live:
        order = named_live[0]
        by = (f", done by {order['performed_by']}" if order["performed_by"] else
              (f", given to {order['assigned_to']}" if order["assigned_to"] else ""))
        if order["kind"] != "preventive":
            return (f"this stop's own reason names {order['code']}, a {order['kind']} "
                    f"maintenance order that is {order['status']}{by} — and corrective "
                    f"work is raised after something broke, so this stop is that repair "
                    f"and not work anybody planned")
        return (f"planned work: this stop's own reason names {order['code']}, a "
                f"{order['kind']} maintenance order that is {order['status']}{by}"
                + ". This is the job, not a breakdown")
    if named:
        # The reason names an order whose own record does not place its work
        # across the block. Saying "no start recorded" there would be false of a
        # job that was started an hour later, so the two are separate sentences.
        away = next((o for o in orders if o["code"] in named and o["started_at"]), None)
        if away is not None:
            return (f"this stop's own reason names maintenance order {away['code']}, and "
                    f"that order's own record places its work outside this block — so "
                    f"the reason says planned work and the order record does not put "
                    f"that work here")
        return (f"this stop's own reason names maintenance order {named[0]}, and that "
                f"order has no start recorded on it — so the reason says planned work "
                f"and the order record does not confirm it")
    if in_progress:
        # Preferring the order that could actually have stopped the line keeps
        # this sentence about the stop rather than about whoever was nearest.
        order = next((o for o in in_progress
                      if o["kind"] == "preventive" and o["needs_stop"]), in_progress[0])
        what = (f" ({order['summary']})" if order["summary"] else "")
        if order["kind"] != "preventive":
            return (f"{order['code']}, a {order['kind']} order that is "
                    f"{order['status']}, was in progress on this machine across this "
                    f"block{what} — corrective work is raised after something broke, so "
                    f"this reads as the repair of this stop and not as planned work")
        if not order["needs_stop"]:
            return (f"{order['code']}, a preventive order that is {order['status']}, was "
                    f"in progress on this machine across this block{what}, but this plant "
                    f"says that job does not need the line stopped — so it is not what "
                    f"stopped it, and the stop's own reason does not name it")
        return (f"planned work: {order['code']}, a {order['kind']} order that is "
                f"{order['status']}, was in progress on this machine across this block"
                + what
                + ". The stop's own reason does not name it, so this is placed by time")
    if waiting:
        codes = ", ".join(o["code"] for o in waiting)
        return (f"not this work: {codes} "
                f"{'is' if len(waiting) == 1 else 'are'} raised for this machine and "
                f"never started, so {'it' if len(waiting) == 1 else 'they'} cannot be "
                f"what stopped it. A stop explained by an order nobody started would "
                f"explain every stop")
    if orders:
        return ("maintenance orders touch this window but none was in progress across "
                "the block, so this MES has nothing saying the stop was planned work")
    return ("no maintenance order covers this block at all, so this MES holds nothing "
            "saying this stop was planned work. That is not the same as a breakdown: "
            "it is a stop with no maintenance record against it")


# ------------------------------------------------------ what quality saw after it


def _quality(session: Session, unit: Equipment, after: datetime,
             minutes: float) -> dict:
    """Readings taken on this machine after the block, with the chart's verdict.

    Scoped to the station, because a check records the station that took it and
    narrowing by anything else would mean inventing the link. `result` is this
    MES's own verdict against its own specification; `signals` are the control
    chart's rules as they fired at the time, which is the sentence a reader
    came for - "the setup was followed by two low means".
    """
    until = after + timedelta(minutes=minutes)
    rows = session.execute(
        select(QualityCheck.id, QualityCheck.value, QualityCheck.result, QualityCheck.ts,
               QualityCheck.checked_by, QualityCheck.sample_id, QualityCheck.spec_id,
               QualitySpec.characteristic, QualitySpec.unit, QualitySpec.min_value,
               QualitySpec.max_value, Material.code, WorkOrder.code)
        .join(QualitySpec, QualitySpec.id == QualityCheck.spec_id)
        .join(Material, Material.id == QualitySpec.material_id)
        .join(WorkOrder, WorkOrder.id == QualityCheck.work_order_id, isouter=True)
        .where(QualityCheck.equipment_id == unit.id,
               QualityCheck.ts > after, QualityCheck.ts <= until)
        .order_by(QualityCheck.ts)).all()
    shown = rows[:MOST_ROWS]
    signals: dict[int, list[dict]] = {}
    if shown:
        for row in session.execute(
                select(SpcSignal.check_id, SpcSignal.rule, SpcSignal.what, SpcSignal.ts,
                       SpcSignal.value)
                .where(SpcSignal.check_id.in_([r.id for r in shown]))
                .order_by(SpcSignal.ts)).all():
            signals.setdefault(row.check_id, []).append(
                {"rule": row.rule, "what": row.what, "ts": row.ts, "value": row.value})
    readings = []
    for row in shown:
        value = row.value
        readings.append({
            "check": row.id,
            "material": row[11],
            "characteristic": row.characteristic,
            "unit": row.unit,
            "value": value,
            "result": getattr(row.result, "value", row.result),
            "ts": row.ts,
            "seconds_after_the_block": round((row.ts - after).total_seconds(), 1),
            # Null means not recorded, on each of these.
            "checked_by": row.checked_by,
            "sample": row.sample_id,
            "work_order": row[12],
            "lower_spec": row.min_value,
            "upper_spec": row.max_value,
            "below_spec": row.min_value is not None and value < row.min_value,
            "above_spec": row.max_value is not None and value > row.max_value,
            # What the control chart said about it when it was written - the
            # recorded signal, not a rule re-run now. Empty is "no rule fired",
            # which on a reading inside the limits is the answer.
            "signals": signals.get(row.id, []),
        })
    return {
        "readings": readings,
        "shown": len(readings),
        "total": len(rows),
        "minutes": minutes,
        "failed": sum(1 for r in readings if r["result"] == "fail"),
        "with_a_signal": sum(1 for r in readings if r["signals"]),
        "scope": (f"every reading recorded against this machine in the {minutes:g} "
                  f"minutes after the block. A check carries the station that took it, "
                  f"and a reading taken by a person with a gauge records none — those "
                  f"are not here, and there is no honest way to place them"),
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


# ------------------------------------------------ what it was doing either side


def _either_side(session: Session, unit: Equipment, start: datetime,
                 end: datetime) -> dict:
    """The interval before this block and the one after, as records.

    *Running, then setup, then running* is the shape of a changeover and
    *down, then down again* is the shape of a machine nobody fixed. The bar
    shows it; this says it in words, with the seconds between.
    """
    def said(row: EquipmentState | None, gap_seconds: float | None) -> dict | None:
        if row is None:
            return None
        return {
            "state": getattr(row.state, "value", row.state),
            "reason": row.reason,
            "reason_code": row.reason_code,
            "reason_source": row.reason_source,
            "started_at": row.started_at,
            "ended_at": row.ended_at,
            "open": row.ended_at is None,
            "seconds": (None if row.ended_at is None else
                        round((row.ended_at - row.started_at).total_seconds(), 1)),
            "seconds_from_the_block": gap_seconds,
        }

    before = session.scalars(
        select(EquipmentState)
        .where(EquipmentState.equipment_id == unit.id,
               EquipmentState.ended_at.is_not(None),
               EquipmentState.ended_at <= start)
        .order_by(EquipmentState.ended_at.desc()).limit(1)).first()
    after = session.scalars(
        select(EquipmentState)
        .where(EquipmentState.equipment_id == unit.id,
               EquipmentState.started_at >= end)
        .order_by(EquipmentState.started_at).limit(1)).first()
    return {
        "before": said(before, None if before is None else
                       round((start - before.ended_at).total_seconds(), 1)),
        "after": said(after, None if after is None else
                      round((after.started_at - end).total_seconds(), 1)),
        "note": ("the record either side of the stretch that was drawn. Null is this "
                 "MES holding nothing there, which is not the machine standing still"),
        "coverage": "absent", "coverage_note": RECORDS_NOT_A_RATE,
    }


# ------------------------------------------------------------------- the whole


def dossier(session: Session, equipment_code: str, start: datetime, end: datetime, *,
            before_minutes: float = BEFORE_MINUTES,
            after_minutes: float = AFTER_MINUTES,
            quality_minutes: float = QUALITY_AFTER_MINUTES,
            may_label: bool | None = None,
            roles: dict[str, list[str]] | None = None) -> dict:
    """Everything this MES holds about one block of one machine's timeline.

    One read, so the panel opens on one round trip rather than on seven - and
    so an agent, when it gets this as a tool, asks one question and is given
    one answer with every block's coverage already on it.
    """
    if end <= start:
        raise Invalid("a block ends after it starts: `end` must be later than `start`")
    unit = masterdata.get_equipment(session, equipment_code)
    records = _records(session, unit, start, end)
    gaps = _gaps(session, unit, start, end)
    block = _block(records, gaps, start, end)
    why = _why(session, unit, block, records, gaps, may_label=may_label, roles=roles)
    marks = [{"t": start, "label": "the block starts"},
             {"t": end, "label": "the block ends"}]
    ledger = coverage.ledger(session, equipment_code=unit.code, equipment_id=unit.id,
                             start=start, end=end)
    return {
        "equipment": {"code": unit.code, "name": unit.name},
        "window": {
            "start": start, "end": end,
            "seconds": round((end - start).total_seconds(), 1),
            "before_minutes": before_minutes, "after_minutes": after_minutes,
            "quality_minutes": quality_minutes,
        },
        "block": block,
        "why": why,
        "orders": _orders(session, unit, start, end),
        "maintenance": _maintenance(session, unit, block, why, start, end),
        # The machine's own analogs across the block and the minutes either
        # side, marked where the block begins and ends - drawn by the same
        # block the panel beside the control chart draws, through the same
        # bucketing, so the two screens cannot draw one tag two ways.
        "tags": moment.analog_trends(
            session, unit,
            start - timedelta(minutes=before_minutes),
            end + timedelta(minutes=after_minutes), marks),
        "quality": _quality(session, unit, end, quality_minutes),
        "either_side": _either_side(session, unit, start, end),
        # The block's own window, watched or not - the one figure the panel's
        # header states, like every chart in this product.
        "coverage": None if ledger.coverage is None else round(ledger.coverage, 4),
        "ledger": ledger.as_json(),
        "coverage_note": ("how much of the block's own stretch this MES was watching "
                          "the machine for. A stop nobody was watching is still a "
                          "stop, and the ledger below says where the unwatched "
                          "seconds went"),
    }
