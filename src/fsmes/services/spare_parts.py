"""The parts a repair order's close form offers: the ERP's list, or the codes
this plant has used before.

The MES keeps no parts list of its own and no price (Scott, 2026-11-28). The
list is the ERP's, read through the connection when a close form opens, and
held in memory for a few minutes so the next form opens fast - then thrown
away. Nothing here writes to the database; the only thing kept is the part's
name on the repair order that used it, written by `maintenance.complete`.

What the form is told is one of four things, in `erp`:

    listed         the ERP's list, with the time it was read
    off            no ERP is connected (the pip demo)
    no_list        the connection is one that offers no parts list
    not_answering  the ERP did not answer; type the code

Every state but `listed` comes with the codes this plant has used before, so
the box still helps. A code the ERP does not list is never refused - a real
stores list is never complete - and the form warns before it saves.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import MaintenanceOrder
from fsmes.integrations.erp.contract import SparePart

log = structlog.get_logger("spare_parts")

#: How many codes used before the box offers. Most recent first.
USED_BEFORE_SHOWN = 50

#: A lister hands back the ERP's parts, `None` when the connection offers no
#: list, or raises when the ERP does not answer.
Lister = Callable[[], "list[SparePart] | None"]


@dataclass
class _Copy:
    key: str
    good_until: float               # time.monotonic() after which it is dropped
    read_at: datetime               # the same moment on the clock, for the form
    parts: list[SparePart] = field(default_factory=list)


_copy: _Copy | None = None


def forget() -> None:
    """Drop the copy in memory; the next form reads the ERP again."""
    global _copy
    _copy = None


def _fresh(key: str | None, clock: Callable[[], float]) -> _Copy | None:
    """The copy in memory if it is still good, for this connection when
    `key` names one. A stale copy is dropped here, not merely ignored."""
    global _copy
    if _copy is not None and clock() >= _copy.good_until:
        _copy = None
    if _copy is None or (key is not None and _copy.key != key):
        return None
    return _copy


def name_of(code: str | None, *, clock: Callable[[], float] = time.monotonic) -> str | None:
    """The ERP's name for this code, from the copy in memory when it is still
    fresh; null when there is no fresh copy or the ERP does not list it.
    Never reads the ERP: a close must not wait on a system that is down."""
    if not code:
        return None
    held = _fresh(None, clock)
    if held is None:
        return None
    wanted = code.strip().upper()
    return next((p.name for p in held.parts if p.code.upper() == wanted), None)


def used_before(session: Session) -> tuple[list[dict], int]:
    """The part codes closed repair orders carry, most recent first, with the
    name kept on the latest of them. A read; the total is every distinct code."""
    total = session.scalar(select(func.count(func.distinct(MaintenanceOrder.part_code)))
                           .where(MaintenanceOrder.part_code.is_not(None))) or 0
    rows = session.execute(
        select(MaintenanceOrder.part_code, MaintenanceOrder.part_name)
        .where(MaintenanceOrder.part_code.is_not(None))
        .order_by(MaintenanceOrder.completed_at.desc().nulls_last(), MaintenanceOrder.id.desc())
        .limit(USED_BEFORE_SHOWN * 20)).all()
    seen: dict[str, dict] = {}
    for code, name in rows:
        if code not in seen and len(seen) < USED_BEFORE_SHOWN:
            seen[code] = {"code": code, "name": name}
    return list(seen.values()), int(total)


def offer(session: Session, *, connected: bool, lister: Lister | None, key: str = "",
          minutes: float = 5.0, clock: Callable[[], float] = time.monotonic) -> dict:
    """What the Part used box offers right now, and in which of the four states.

    `connected` is whether this plant has an ERP connection at all; `lister`
    reads its parts (see `Lister`). `key` names the connection, so a copy
    read from one ERP is never offered for another. `minutes` is how long a
    copy is good for.
    """
    global _copy
    before, before_total = used_before(session)
    out = {"erp": "off", "read_at": None, "items": [], "total": 0,
           "used_before": before, "used_before_total": before_total,
           "copy_minutes": minutes}
    if not connected or lister is None:
        out["erp"] = "off" if not connected else "no_list"
        return out
    held = _fresh(key, clock)
    if held is None:
        try:
            parts = lister()
        except Exception as exc:  # the ERP's failure, whatever shape it takes
            log.warning("ERP parts list not answering", error=f"{type(exc).__name__}: {exc}")
            out["erp"] = "not_answering"
            return out
        if parts is None:
            out["erp"] = "no_list"
            return out
        held = _copy = _Copy(key=key, good_until=clock() + minutes * 60.0,
                              read_at=utcnow(), parts=list(parts))
    out.update(erp="listed", read_at=held.read_at,
               items=[p.model_dump() for p in held.parts], total=len(held.parts))
    return out
