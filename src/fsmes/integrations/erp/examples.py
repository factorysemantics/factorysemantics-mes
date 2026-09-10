"""Worked examples of every outbound confirmation, produced by a real run.

An ERP team reviewing a shadow-mode plant asks one question: *would what
this thing sends be correct if it were connected?* A schema answers half
of it. The other half is files — a clean step, a step with scrap and
consumption, an over-run, an order that made a lot and an order that made
none — as the file adapter would actually write them.

So these are generated rather than typed. A demo plant is seeded in memory,
three orders are run through the same services the floor uses, and the
payloads are lifted out of the outbox the ERP sync reads. Nothing here
writes an example by hand, which is the point: a hand-written example is a
statement about what somebody thinks the MES sends.

Two things are pinned rather than taken from the run:

* **The clock.** Every timestamp is replaced with a fixed one, and
  `machine_seconds` recomputed from it, so regenerating the files does not
  change them. Everything else is exactly what the run produced.
* **The demo plant's routing times and cost centre.** The seeded plant has
  neither, and an example whose set-up, labour and cost centre are all null
  would teach an ERP team nothing about the fields they care most about.
  They are set here, on the demo plant, and stated on the docs page.

The plant is the seeded demo one — ACME Beverages, Kansas City, cola syrup.
No real plant's orders, materials or cost centres appear here or ever will.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.db import Base
from fsmes.domain import Equipment, ErpMessage, MessageDirection, ProductionSource, Routing
from fsmes.integrations.erp import b2mml
from fsmes.integrations.erp.contract import ProductionRequest
from fsmes.seed import seed_demo_plant
from fsmes.services import erp, execution, workorders

#: The fixed clock. A Wednesday morning shift, so a reader of the files can
#: see a plausible day rather than 1970 or the moment somebody ran a script.
SHIFT_START = datetime(2026, 3, 4, 6, 0, 0)

#: Set-up and labour times put on the demo routing so the examples show the
#: time fields carrying numbers. Seconds, like everything in the contract.
ROUTING_TIMES = {
    10: {"setup_seconds": 900.0, "labour_seconds_per_unit": 1.5},
    20: {"setup_seconds": 300.0, "labour_seconds_per_unit": 0.8},
}

#: The cost centre the demo line's work is accounted to. Every machine
#: under the line inherits it, which is how the field is meant to be kept.
COST_CENTER = "CC-PKG-1"

#: message_key -> (started_at, completed_at) on the fixed clock.
CLOCK = {
    "WO-2026-0041:op10": (SHIFT_START, SHIFT_START + timedelta(minutes=41)),
    "WO-2026-0041:op20": (SHIFT_START + timedelta(minutes=45), SHIFT_START + timedelta(minutes=80)),
    "WO-2026-0041:completion": (SHIFT_START, SHIFT_START + timedelta(minutes=80)),
    "WO-2026-0042:op10": (SHIFT_START + timedelta(minutes=90), SHIFT_START + timedelta(minutes=98)),
    "WO-2026-0042:op20": (SHIFT_START + timedelta(minutes=100), SHIFT_START + timedelta(minutes=104)),
    "WO-2026-0042:completion": (SHIFT_START + timedelta(minutes=90), SHIFT_START + timedelta(minutes=104)),
    "WO-2026-0043:op10": (SHIFT_START + timedelta(minutes=120), SHIFT_START + timedelta(minutes=126)),
    "WO-2026-0043:op20": (SHIFT_START + timedelta(minutes=128), SHIFT_START + timedelta(minutes=130)),
    "WO-2026-0043:completion": (SHIFT_START + timedelta(minutes=120), SHIFT_START + timedelta(minutes=130)),
}


@dataclass(frozen=True)
class Example:
    """One published file, and why an ERP team should read it."""

    name: str
    title: str
    why: str
    payload: dict

    @property
    def xml(self) -> str:
        return b2mml.render_confirmation(self.payload) + "\n"

    @property
    def json(self) -> str:
        return json.dumps(self.payload, indent=2, sort_keys=False) + "\n"


#: message_key -> (file name, title, why). The order is the order the docs
#: page presents them in, which is the order they happened.
PUBLISHED: dict[str, tuple[str, str, str]] = {
    "WO-2026-0041:op10": (
        "operation-with-scrap-and-components",
        "An operation with scrap and consumed lots",
        "The fullest of the confirmations: 120 units into the mixer, 118 good, 2 scrapped, "
        "two raw lots consumed against the step, and the cost centre inherited from the line. "
        "This is the document an ERP posts labour, machine time and a back-flush against.",
    ),
    "WO-2026-0041:op20": (
        "operation-clean",
        "A clean operation",
        "The common case, and the one to read first: everything that reached the step came "
        "out of it good, nothing was scrapped, nothing was consumed. `components` is empty "
        "because nothing was booked - which is not the same as nothing having been used.",
    ),
    "WO-2026-0041:completion": (
        "completion-with-a-lot",
        "An order completion with a finished-goods lot",
        "The close of that order: 118 good against 120 ordered, 2 scrapped, and the lot the "
        "goods receipt is posted against. `over_qty` is 0 and is sent anyway.",
    ),
    "WO-2026-0042:op10": (
        "operation-over-run",
        "An operation that ran past the order",
        "One machine count carried the line to 16 good against an order for 15, so `wip_qty` "
        "is -1: more came out of the step than went into it. It is reported, not clamped. "
        "Every unit the machine counted is booked, because the machine made them.",
    ),
    "WO-2026-0042:completion": (
        "completion-with-an-over-run",
        "An order completion carrying an over-run",
        "The same order's close. 16 good against 15 ordered, and `over_qty` is 1 rather than "
        "leaving the ERP to notice that 16 of 15 is not a typo.",
    ),
    "WO-2026-0043:completion": (
        "completion-without-a-lot",
        "An order completion with no lot",
        "An order where everything scrapped. `good_qty` is 0, `lot` is null, and null is the "
        "honest value: there is no lot, and an ERP posting a receipt against one the MES "
        "never made is worse than a missing field.",
    ),
}


def _plant() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    seed_demo_plant(session)
    session.flush()

    line = session.scalar(select(Equipment).where(Equipment.code == "LINE1"))
    line.cost_center = COST_CENTER
    routing = session.scalar(select(Routing).where(Routing.code == "RT-COLA"))
    for operation in routing.operations:
        for name, value in ROUTING_TIMES[operation.seq].items():
            setattr(operation, name, value)
    session.flush()
    return session


def _order_from_the_erp(session: Session, code: str, quantity: float, reference: str) -> None:
    """A schedule arriving from the ERP, through the inbound path.

    Raising the order in the MES instead would have left `erp_reference`
    null in every example, and that field is how the ERP matches the
    confirmation back to its own document.
    """
    erp.process_inbound(session, ProductionRequest(
        code=code, material="FG-COLA", quantity=quantity, priority=20,
        due_date=SHIFT_START + timedelta(hours=8), erp_reference=reference))
    workorders.release(session, code, "examples")


def _run(session: Session) -> None:
    """Three orders, through the same services an operator's screen calls."""
    # A full order: consumption and scrap at the first step, a clean second
    # step, and a completion that made a lot.
    _order_from_the_erp(session, "WO-2026-0041", 120, "4400041")
    workorders.start_operation(session, "WO-2026-0041", 10, actor="examples")
    execution.consume(session, order_code="WO-2026-0041", lot_code="LOT-SUGAR-001",
                      quantity=42, seq=10, actor="examples")
    execution.consume(session, order_code="WO-2026-0041", lot_code="LOT-FLAVOR-001",
                      quantity=6, seq=10, actor="examples")
    execution.report(session, order_code="WO-2026-0041", seq=10, good=118, scrap=2, actor="examples")
    workorders.complete_operation(session, "WO-2026-0041", 10, actor="examples")
    workorders.start_operation(session, "WO-2026-0041", 20, actor="examples")
    execution.report(session, order_code="WO-2026-0041", seq=20, good=118, actor="examples")
    workorders.complete_operation(session, "WO-2026-0041", 20, actor="examples")

    # An over-run: one OPC counter delta carrying the line past the order,
    # which is how 16 against an order for 15 really happens.
    _order_from_the_erp(session, "WO-2026-0042", 15, "4400042")
    execution.report(session, equipment_code="MIX01", good=16, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=16, source=ProductionSource.OPC)

    # An order that made nothing good.
    _order_from_the_erp(session, "WO-2026-0043", 4, "4400043")
    workorders.start_operation(session, "WO-2026-0043", 10, actor="examples")
    execution.report(session, order_code="WO-2026-0043", seq=10, good=0, scrap=4, actor="examples")
    workorders.complete_operation(session, "WO-2026-0043", 10, actor="examples")
    workorders.start_operation(session, "WO-2026-0043", 20, actor="examples")
    workorders.complete_operation(session, "WO-2026-0043", 20, actor="examples")
    session.flush()


def _on_the_fixed_clock(payload: dict) -> dict:
    """The run's real timestamps replaced with the fixed ones.

    `machine_seconds` follows from the fixed times rather than being left
    at what a test machine happened to take, so the number in the file and
    the two timestamps beside it agree - which is exactly what `fsmes erp
    validate` checks.
    """
    started, completed = CLOCK[payload["message_key"]]
    payload = dict(payload)
    payload["started_at"] = started.isoformat()
    payload["completed_at"] = completed.isoformat()
    if payload.get("machine_seconds") is not None:
        payload["machine_seconds"] = (completed - started).total_seconds()
    return payload


def confirmations() -> dict[str, dict]:
    """Every confirmation the scripted run queues, on the fixed clock.

    `generate()` publishes the six worth reading as examples; this is all
    nine, which is what anything comparing a whole run against another
    record of it needs. Same run, same clock, same payloads.
    """
    session = _plant()
    try:
        _run(session)
        queued = {
            message.message_key: message.payload
            for message in session.scalars(
                select(ErpMessage).where(ErpMessage.direction == MessageDirection.OUT)
                .order_by(ErpMessage.id))
        }
    finally:
        session.close()
    return {key: _on_the_fixed_clock(payload) for key, payload in queued.items()}


def generate() -> list[Example]:
    """Run the demo plant and lift the published confirmations out of it."""
    queued = confirmations()

    missing = [key for key in PUBLISHED if key not in queued]
    if missing:  # pragma: no cover - a broken generator, caught by its test
        raise RuntimeError(f"the run did not produce {missing}")
    return [Example(name=name, title=title, why=why, payload=queued[key])
            for key, (name, title, why) in PUBLISHED.items()]


def write(directory: Path) -> list[Path]:
    """Write every example as JSON and as B2MML. Returns what it wrote."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for example in generate():
        for suffix, text in ((".json", example.json), (".xml", example.xml)):
            path = directory / f"{example.name}{suffix}"
            path.write_text(text, encoding="utf-8")
            written.append(path)
    return written
