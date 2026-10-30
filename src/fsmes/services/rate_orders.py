"""The plant's standing rate order: read it, set it, take it away.

A rate order is a name and, per item, a target rate in units a minute
(`fsmes.domain.rate_orders` says why it is not a work order). A plant holds
at most one; setting it replaces the one there, and every change is audited.

ONE DEFINITION PER NUMBER:

* **target /min** - the order's rate for the item, as written.
* **target share** - that rate over the sum of the order's rates, in %. Never
  stored: it follows from the rates.
"""

from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain.rate_orders import RateOrder, RateOrderLine
from fsmes.services import Invalid, NotFound, audit


def current(db: Session) -> RateOrder | None:
    """The plant's rate order, or None - no order, which is no target."""
    return db.scalar(select(RateOrder).order_by(RateOrder.id).limit(1))


def targets(order: RateOrder | None) -> dict[str, float]:
    """{item: target units a minute}; empty with no order."""
    return {line.item: line.per_minute for line in order.lines} if order else {}


def as_json(order: RateOrder | None) -> dict | None:
    if order is None:
        return None
    total = sum(line.per_minute for line in order.lines)
    return {
        "code": order.code, "name": order.name, "note": order.note,
        "updated_at": order.updated_at, "updated_by": order.updated_by,
        "unit": "units/min",
        "items": [{"item": line.item, "per_minute": line.per_minute,
                   "share_percent": round(100.0 * line.per_minute / total, 1) if total else None}
                  for line in order.lines],
        "items_total": len(order.lines),
        "per_minute_total": total,
    }


def check(items: list[dict]) -> list[tuple[str, float]]:
    """The items as (name, units a minute), or `Invalid` saying what is wrong.
    An order needs at least one item; every rate is above zero; no item twice."""
    if not isinstance(items, list) or not items:
        raise Invalid("a rate order needs at least one item and its rate in units a minute")
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for row in items:
        if not isinstance(row, dict):
            raise Invalid(f"an item is a name and a rate, not {row!r}")
        name = str(row.get("item") or "").strip()
        if not name:
            raise Invalid("every item needs a name - the game's, such as `Iron Plate`")
        if len(name) > 80:
            raise Invalid(f"item name {name[:20]!r}... is longer than 80 characters")
        rate = row.get("per_minute")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) \
                or rate <= 0:
            raise Invalid(f"{name}: the rate is units a minute above zero, not {rate!r}")
        if name in seen:
            raise Invalid(f"{name} is in the order twice")
        seen.add(name)
        out.append((name, float(rate)))
    return out


def put(db: Session, *, code: str, name: str, items: list[dict], actor: str,
        note: str | None = None) -> RateOrder:
    """Set the plant's rate order, replacing the one there."""
    code, name = (code or "").strip(), (name or "").strip()
    if not code or len(code) > 40:
        raise Invalid("a rate order needs a code of 1 to 40 characters")
    if not name or len(name) > 120:
        raise Invalid("a rate order needs a name of 1 to 120 characters")
    lines = check(items)
    order = current(db)
    before = as_json(order)
    if order is None:
        order = RateOrder(code=code, name=name)
        db.add(order)
    order.code, order.name, order.note = code, name, note
    order.updated_at, order.updated_by = utcnow(), actor
    order.lines.clear()
    db.flush()
    order.lines.extend(RateOrderLine(item=item, per_minute=rate) for item, rate in lines)
    db.flush()
    audit.record(db, actor=actor, action="rate_order.set", entity_type="rate_order",
                 entity_id=code, before=_plain(before), after=_plain(as_json(order)))
    return order


def remove(db: Session, *, actor: str) -> None:
    """Take the rate order away: the plant has no target again."""
    order = current(db)
    if order is None:
        raise NotFound("this plant has no rate order")
    before = as_json(order)
    db.delete(order)
    db.flush()
    audit.record(db, actor=actor, action="rate_order.remove", entity_type="rate_order",
                 entity_id=order.code, before=_plain(before))


def _plain(block: dict | None) -> dict | None:
    if block is None:
        return None
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in block.items()}


__all__ = ["as_json", "check", "current", "put", "remove", "targets"]
