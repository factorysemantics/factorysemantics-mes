"""Production execution: quantity bookings, lot consumption, genealogy.

Machine-counted quantities (source=opc) get MES behavior for free: they
auto-start a pending operation and auto-complete it when the order quantity
is reached — the machine drives, the MES keeps the books.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.domain import (
    LotConsumption,
    LotStatus,
    MaterialLot,
    OperationStatus,
    OrderStatus,
    ProductionLog,
    ProductionSource,
    WorkOrder,
    WorkOrderOperation,
)
from fsmes.services import Conflict, Invalid, NotFound, audit, masterdata, workorders


def get_lot(session: Session, code: str) -> MaterialLot:
    lot = session.scalar(select(MaterialLot).where(MaterialLot.code == code))
    if lot is None:
        raise NotFound(f"lot {code!r} not found")
    return lot


def create_lot(
    session: Session,
    *,
    code: str,
    material_code: str,
    quantity: float,
    produced_by_order: WorkOrder | None = None,
    actor: str = "system",
) -> MaterialLot:
    if quantity <= 0:
        raise Invalid("lot quantity must be positive")
    if session.scalar(select(MaterialLot).where(MaterialLot.code == code)):
        raise Conflict(f"lot {code!r} already exists")
    material = masterdata.get_material(session, material_code)
    lot = MaterialLot(
        code=code,
        material=material,
        quantity=quantity,
        original_quantity=quantity,
        produced_by_order_id=produced_by_order.id if produced_by_order else None,
    )
    session.add(lot)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="lot.created",
        entity_type="lot",
        entity_id=code,
        after={
            "material": material_code,
            "quantity": quantity,
            "produced_by": produced_by_order.code if produced_by_order else None,
        },
    )
    return lot


def consume(session: Session, *, order_code: str, lot_code: str, quantity: float,
            seq: int | None = None, equipment_code: str | None = None,
            actor: str = "system") -> MaterialLot:
    """Issue material to an order, at a station when one is named.

    `seq` or `equipment_code` records *where* it went in. Both optional,
    because a consumable nobody tracks to a station is a real case and
    inventing a station for it would be worse than leaving it null.
    """
    if quantity <= 0:
        raise Invalid("consumed quantity must be positive")
    wo = workorders.get(session, order_code)
    if wo.status not in (OrderStatus.RELEASED, OrderStatus.RUNNING):
        raise Conflict(f"work order {order_code} is {wo.status.value}; cannot consume material")
    lot = get_lot(session, lot_code)
    if lot.status is not LotStatus.AVAILABLE:
        raise Conflict(f"lot {lot_code} is {lot.status.value}")
    if quantity > lot.quantity:
        raise Conflict(f"lot {lot_code} has only {lot.quantity} left, cannot consume {quantity}")
    lot.quantity -= quantity
    if lot.quantity == 0:
        lot.status = LotStatus.EXHAUSTED
    operation = None
    if seq is not None:
        operation = next((op for op in wo.operations if op.seq == seq), None)
        if operation is None:
            raise Invalid(f"order {order_code} has no operation {seq}")
    elif equipment_code:
        operation = next(
            (op for op in wo.operations if op.equipment.code == equipment_code.upper()),
            None)
        if operation is None:
            raise Invalid(
                f"order {order_code} has no operation on {equipment_code}")

    session.add(LotConsumption(
        work_order_id=wo.id, lot_id=lot.id, quantity=quantity,
        operation_id=operation.id if operation else None,
        equipment_id=operation.equipment_id if operation else None))
    audit.record(
        session,
        actor=actor,
        action="lot.consumed",
        entity_type="lot",
        entity_id=lot_code,
        after={"order": order_code, "quantity": quantity, "remaining": lot.quantity},
    )
    return lot


def report(
    session: Session,
    *,
    order_code: str | None = None,
    seq: int | None = None,
    equipment_code: str | None = None,
    good: float = 0,
    scrap: float = 0,
    source: ProductionSource = ProductionSource.MANUAL,
    actor: str = "system",
) -> WorkOrderOperation | None:
    """Book produced quantities against an operation.

    Address it either explicitly (order_code [+ seq]) or by machine
    (equipment_code) — the latter is how OPC counter deltas arrive. Machine
    counts with no active order are dropped (returns None): the machine ran,
    but there is nothing to book against.
    """
    if good < 0 or scrap < 0:
        raise Invalid("quantities cannot be negative")
    if good == scrap == 0:
        return None

    if order_code:
        wo = workorders.get(session, order_code)
        # The machine-addressed path filters held orders out in SQL; naming
        # the order explicitly must not be a way around the hold.
        if wo.status is OrderStatus.ON_HOLD:
            raise Conflict(f"work order {order_code} is on hold; nothing books against it")
        ops = [op for op in wo.operations if seq is None or op.seq == seq]
        op = next((o for o in ops if o.status is not OperationStatus.DONE), None)
        if op is None:
            raise Conflict(f"work order {order_code} has no open operation" + (f" {seq}" if seq else ""))
    elif equipment_code:
        equipment = masterdata.get_equipment(session, equipment_code)
        op = session.scalar(
            select(WorkOrderOperation)
            .join(WorkOrder)
            .where(
                WorkOrderOperation.equipment_id == equipment.id,
                WorkOrderOperation.status != OperationStatus.DONE,
                WorkOrder.status.in_((OrderStatus.RELEASED, OrderStatus.RUNNING)),
            )
            .order_by(WorkOrder.priority, WorkOrder.id, WorkOrderOperation.seq)
        )
        if op is None:
            if source is ProductionSource.OPC:
                return None
            raise Invalid(f"no active operation on equipment {equipment_code!r}")
    else:
        raise Invalid("report needs order_code or equipment_code")

    wo = op.order
    if op.status is OperationStatus.PENDING:
        if source is ProductionSource.OPC:
            workorders.start_operation(session, wo.code, op.seq, actor=actor)
        else:
            raise Conflict(f"operation {op.seq} of {wo.code} has not been started")

    op.good_qty += good
    op.scrap_qty += scrap
    session.add(
        ProductionLog(
            work_order_id=wo.id,
            operation_id=op.id,
            equipment_id=op.equipment_id,
            good_qty=good,
            scrap_qty=scrap,
            source=source,
        )
    )
    if source is ProductionSource.MANUAL:
        audit.record(
            session,
            actor=actor,
            action="production.reported",
            entity_type="workorder",
            entity_id=wo.code,
            after={"seq": op.seq, "good": good, "scrap": scrap},
        )
    if source is ProductionSource.OPC and op.good_qty >= wo.quantity:
        workorders.complete_operation(session, wo.code, op.seq, actor=actor)
    return op


def genealogy(session: Session, order_code: str) -> dict:
    """Full trace for one order: what went in (and where it came from),
    what came out (and where it went)."""
    wo = workorders.get(session, order_code)
    consumed = session.scalars(select(LotConsumption).where(LotConsumption.work_order_id == wo.id)).all()
    produced = session.scalars(select(MaterialLot).where(MaterialLot.produced_by_order_id == wo.id)).all()

    def _lot_origin(lot: MaterialLot) -> str | None:
        if lot.produced_by_order_id is None:
            return None
        origin = session.get(WorkOrder, lot.produced_by_order_id)
        return origin.code if origin else None

    return {
        "order": wo.code,
        "material": wo.material.code,
        "consumed": [
            {
                "lot": c.lot.code,
                "material": c.lot.material.code,
                "quantity": c.quantity,
                # Where it went in. Without this a recall can only say which
                # order a lot reached, not which station or which operation.
                "operation": c.operation.name if c.operation else None,
                "seq": c.operation.seq if c.operation else None,
                "equipment": c.operation.equipment.code if c.operation else None,
                "produced_by_order": _lot_origin(c.lot),
            }
            for c in consumed
        ],
        "produced": [
            {"lot": lot.code, "material": lot.material.code, "quantity": lot.original_quantity}
            for lot in produced
        ],
    }


def wip(session: Session, order_code: str) -> dict:
    """Where an order's units are, stage by stage.

    Scott's question, and the one an ERP asks first: how much is sitting at
    each step, and whose cost center is it sitting in. It needs no new
    columns - a routing is a sequence, so what arrived at a step is what the
    step before it finished good, and what is *at* a step is what arrived
    minus what it has processed either way:

        input(1)  = the order quantity
        input(n)  = good(n-1)
        wip(n)    = input(n) - good(n) - scrap(n)

    Which sums, across the whole route, to quantity - final good - all scrap:
    everything issued that has neither come out of the end nor been thrown
    away. That identity is asserted by a test, because two ways of computing
    one number is two ways of being wrong.

    **Negative WIP is reported, not clamped.** A step cannot finish more than
    reached it, so a negative number means the counters disagree with the
    route - a miscounted PLC, a rework loop nobody modelled, a manual entry
    against the wrong operation. Rounding it up to zero would hide exactly
    the thing worth looking at, so the payload says `consistent: false` and
    leaves the arithmetic alone.
    """
    order = workorders.get(session, order_code)
    stages = []
    upstream = order.quantity

    for op in sorted(order.operations, key=lambda o: o.seq):
        good = op.good_qty or 0.0
        scrap = op.scrap_qty or 0.0
        machine = op.equipment
        stages.append({
            "seq": op.seq,
            "operation": op.name,
            "equipment": machine.code if machine else None,
            # The account this work sits in, inherited from the line unless
            # the cell overrides it. This is what makes the number an ERP
            # can post against rather than a curiosity.
            "cost_center": masterdata.cost_center(session, machine) if machine else None,
            "status": op.status.value,
            "input_qty": upstream,
            "good_qty": good,
            "scrap_qty": scrap,
            "wip_qty": upstream - good - scrap,
        })
        upstream = good

    finished = stages[-1]["good_qty"] if stages else 0.0
    scrapped = sum(s["scrap_qty"] for s in stages)
    return {
        "order": order.code,
        "material": order.material.code,
        "quantity": order.quantity,
        "finished": finished,
        "scrapped": scrapped,
        "wip_total": order.quantity - finished - scrapped,
        "consistent": all(s["wip_qty"] >= 0 for s in stages),
        "stages": stages,
    }
