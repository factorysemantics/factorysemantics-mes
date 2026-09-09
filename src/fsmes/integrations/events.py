"""The domain event contract: what the outbox carries that is not addressed
to an ERP.

The outbox began life as an ERP inbox/outbox and is now the one event stream
this MES produces. Confirmations are still the ERP's contract and still live
in `integrations/erp/contract.py`; these are the plant facts that no ERP
asked for and that a unified namespace, a historian or a Node-RED flow
wants: a machine changing state, an order going on hold, an order coming
back.

Typed for the same reason the ERP contract is typed. A consumer reads these
models to know what a topic carries, and a shape that lives only in a
`.get()` call somewhere is a shape nobody outside this repository can rely
on.

Two rules these carry from the house rules:

* **Nothing is computed.** A state change reports the interval the MES
  closed and the one it opened. A duration the MES did not observe is
  `null` - never zero, because "the machine ran for no time" and "we were
  not watching" are different facts.
* **Unmodelled is said, not guessed.** A machine that belongs to no work
  centre publishes `work_center: null`, not the name of the level above it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class EquipmentStateChange(BaseModel):
    """A machine moved from one state to another.

    The MES already keeps these as closed intervals (`equipment_states`);
    this is the same fact at the moment it happened, so a namespace does not
    have to poll for it.
    """

    kind: Literal["equipment_state_change"] = "equipment_state_change"
    message_key: str
    equipment: str                       # the machine (ISA-95 work unit)
    work_center: str | None = None       # the line it belongs to, if any
    state: str
    reason: str | None = None            # null when the change carried none
    # What the machine was in before. Null when the MES had no record at all,
    # which is the first state it ever sees for that machine.
    previous_state: str | None = None
    # How long the interval just closed had been open. Null when there was no
    # previous interval - not zero.
    previous_seconds: float | None = None
    started_at: datetime
    actor: str


class OrderHold(BaseModel):
    """An order was stopped without being finished.

    `reason` is never empty: the MES refuses a hold without one, because a
    hold with no reason is indistinguishable from an accident.
    """

    kind: Literal["order_hold"] = "order_hold"
    message_key: str
    order: str
    erp_reference: str | None = None
    material: str
    reason: str
    previous_status: str                 # what it was doing when it stopped
    status: str = "on_hold"
    at: datetime
    actor: str


class OrderResume(BaseModel):
    """A held order went back to work.

    Called *resume* and not *release*: in this MES a release is a planned
    order reaching the floor for the first time, and the two would be read
    as the same event by anyone counting them.
    """

    kind: Literal["order_resume"] = "order_resume"
    message_key: str
    order: str
    erp_reference: str | None = None
    material: str
    previous_status: str = "on_hold"
    # RUNNING if any operation had started, else RELEASED. The MES resumes to
    # where the order truly was rather than to a fixed status.
    status: str
    at: datetime
    actor: str


DomainEvent = EquipmentStateChange | OrderHold | OrderResume

# The kinds this module defines. The ERP sync selects by kind and will not
# touch these; the namespace publisher takes every outbound kind it finds,
# so a new one added here reaches the broker with no change there.
DOMAIN_KINDS = frozenset({"equipment_state_change", "order_hold", "order_resume"})


def as_payload(event: DomainEvent) -> dict:
    """What goes into the outbox and onto the topic: plain JSON."""
    return event.model_dump(mode="json")
