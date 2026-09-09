"""One shape for every list this API returns.

Measured against a year of a sixty-machine plant, /workorders returned 5.5 MB
in 2.4 seconds because it returned everything ever. The fix is not a limit on
that one endpoint - it is that every list has the same envelope, so a screen,
an agent tool or a report can page through any of them without learning a new
convention each time.

The envelope carries `total` deliberately. "Showing 50 of 18,347" is the
sentence that tells someone the list is not the whole story; without it a
truncated list looks like a complete one, which is the same class of lie this
product refuses everywhere else.
"""

from __future__ import annotations

from typing import Any, TypeVar

from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

T = TypeVar("T")

# A page nobody asked to size. Big enough that a shift's work fits, small
# enough that no screen ever ships megabytes by accident.
DEFAULT_LIMIT = 50
MAX_LIMIT = 500

LimitQuery = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT,
                   description="How many to return (max 500).")
OffsetQuery = Query(0, ge=0, description="How many to skip.")


def page(items: list[Any], total: int, limit: int, offset: int) -> dict:
    """The envelope. Every list endpoint returns exactly this."""
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        # Saves every caller from re-deriving it, and saves the disagreement
        # about whether the arithmetic is inclusive.
        "has_more": offset + len(items) < total,
    }


def paginate(session: Session, query: Select, limit: int, offset: int) -> tuple[list, int]:
    """Run a query for one page, and count the whole thing.

    Two round trips on purpose. Window functions would do it in one, and would
    make the count wrong the moment a caller adds a GROUP BY - a correct
    number matters more here than a saved query.
    """
    total = session.scalar(
        select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = list(session.scalars(query.limit(limit).offset(offset)))
    return rows, total
