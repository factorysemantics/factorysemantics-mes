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

def _bounds() -> tuple[int, int]:
    """This plant's page default and its ceiling, read once as this module is
    imported.

    **Read at start-up, and not live like the rest of `[admin]`.** These two
    numbers are not only used - they are *published*: they are the default and
    the `le=` bound of every list endpoint in this plant's own OpenAPI
    document, which a client reads once and holds. A ceiling that moved under
    a caller holding that document would make the document a lie, which is the
    same class of thing as a truncated list presented as a whole one. So the
    plant states them in its pack, the process reads them when it starts, and
    Setup > Configuration says so rather than offering an input that would
    only half work.
    """
    from fsmes.config import get_settings

    settings = get_settings()
    return settings.admin_list_default_limit, settings.admin_list_max_limit


# A page nobody asked to size. Big enough that a shift's work fits, small
# enough that no screen ever ships megabytes by accident. `[admin]
# list_default_limit` and `list_max_limit`; fifty and five hundred by default,
# which is what was written here.
DEFAULT_LIMIT, MAX_LIMIT = _bounds()

LimitQuery = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT,
                   description=f"How many to return (max {MAX_LIMIT}).")
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
