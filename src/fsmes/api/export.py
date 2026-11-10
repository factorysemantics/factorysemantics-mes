"""A list as a spreadsheet: the one step between a list's rows and an xlsx.

The quality checks were the first list to go to Excel (#225). A supervisor
takes more than one list there - work orders, holds, stops, the audit trail -
so the step from "this endpoint's rows" to "a file that opens in Excel" is
written here once, and an export is a column spec and a line.

What every export promises, because it is written here and nowhere else:

- **Every matching row, never a page.** `every_page` walks the list's own
  endpoint until its `total` is reached, so the file holds the rows the list
  would show under the same filters - by construction, not by a second query
  that has to be kept in step with the first.
- **A bold, frozen header row,** then one row per item.
- **Times on the plant's clock,** as real date cells. A column marked `when`
  names the zone in its header, so a stop at six in the morning reads six in
  the morning and says whose six.
- **Empty is not zero.** `None` is an empty cell. A list with no rows is a
  file with its header and nothing under it, never an error.
- **The total, before anybody opens it,** in `X-Total-Count`, and the range in
  the filename.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import Any

from fastapi import Response

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: What a date box means when it is the whole of `since` or `until`. Every
#: exportable list with a date range says this in its own query description.
A_DAY_IS_A_PLANT_DAY = (
    "A bare date (2026-09-01) is a day on the plant's own clock: `since` from "
    "its first moment, `until` to its last. A date and time is an instant in "
    "UTC, as every timestamp in this API is.")


def instant(text: str | None, *, end: bool) -> tuple[datetime, bool] | None:
    """`since` or `until` as a naive-UTC bound, and whether it is inclusive.

    A bare date is a plant day, not a day in Greenwich: "to the 3rd" on a
    Chicago line means to midnight in Chicago, and a range drawn in UTC would
    move six hours of somebody's night shift into the wrong day of the file.
    The end of a day is the start of the next one, exclusive, so a reading at
    23:59:59.6 is still on it.
    """
    from fsmes import identity
    from fsmes.services import Invalid

    if not text:
        return None
    text = text.strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            if end:
                day += timedelta(days=1)
            local = datetime(day.year, day.month, day.day, tzinfo=identity.clock().tz)
            return local.astimezone(UTC).replace(tzinfo=None), not end
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise Invalid(f"{text!r} is neither a date (2026-09-01) nor a date and time "
                      f"(2026-09-01T06:00:00)") from None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment, True


def within(query, column, since: str | None, until: str | None):
    """`query` narrowed to `since`..`until` on `column`, read as `instant` does."""
    start = instant(since, end=False)
    if start:
        query = query.where(column >= start[0])
    stop = instant(until, end=True)
    if stop:
        moment, inclusive = stop
        query = query.where(column <= moment if inclusive else column < moment)
    return query


@dataclass(frozen=True)
class Column:
    """One column of an export: its header, and how to read it off a row.

    `get` is a key of the row (the list's own JSON names) or a function of the
    row. `when` marks a naive-UTC instant: it is written on the plant's clock
    and the header names the zone.
    """

    header: str
    get: str | Callable[[Any], Any]
    when: bool = False


def _read(row: Any, get: str | Callable[[Any], Any]) -> Any:
    if callable(get):
        return get(row)
    if isinstance(row, dict):
        return row.get(get)
    return getattr(row, get, None)


def _cell(value: Any, *, when: bool):
    """A row's value as one of the four things a cell can be."""
    from fsmes import identity

    if value is None:
        return None
    if isinstance(value, datetime):
        # Every timestamp in the MES is naive UTC; a cell is the plant's wall
        # clock. A column not marked `when` still never shows Greenwich.
        return identity.to_plant(value).replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list | tuple | set):
        return ", ".join(str(_cell(v, when=when)) for v in value if v is not None) or None
    return str(value)


def every_page(list_endpoint: Callable[..., dict], **filters: Any) -> list:
    """Every item a paged list endpoint would return under these filters.

    Walks the endpoint a full page at a time until its own `total` is reached,
    so an export can never hold a row the list would not show nor drop one it
    would. `filters` are passed exactly as the list's own signature names
    them - every one, since a FastAPI default called directly is a `Query`
    object rather than a value.
    """
    from fsmes.api import paging

    items: list = []
    while True:
        page = list_endpoint(**filters, limit=paging.MAX_LIMIT, offset=len(items))
        items.extend(page["items"])
        if not page["items"] or len(items) >= page["total"]:
            return items


def span(since: str | None, until: str | None) -> str:
    """The range for a filename: `2026-09-01-to-2026-10-31`, or `all`."""
    return "-to-".join(part for part in ((since or "")[:10], (until or "")[:10]) if part) or "all"


def window_span(window: dict | None) -> str:
    """A windowed analysis's range for a filename, on the plant's clock."""
    from fsmes import identity

    if not window or not window.get("start") or not window.get("end"):
        return "window"

    def stamp(value):
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return identity.to_plant(moment).strftime("%Y-%m-%d-%H%M")

    return f"{stamp(window['start'])}-to-{stamp(window['end'])}"


def spreadsheet(name: str, sheet: str, columns: Sequence[Column], rows: Iterable[Any],
                *, range_: str = "all") -> Response:
    """The xlsx response: `name-range.xlsx`, one sheet, every row.

    `name` is the list's own name in a filename (`work-orders`), `sheet` the
    tab's title in Excel. `rows` are the list's own items, in the order the
    file should read.
    """
    from fsmes import identity, xlsx

    zone = identity.clock().name
    header = [f"{c.header} ({zone})" if c.when and zone else c.header for c in columns]
    body_rows = [[_cell(_read(row, c.get), when=c.when) for c in columns] for row in rows]
    return Response(
        content=xlsx.workbook(sheet, header, body_rows),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}-{range_}.xlsx"',
                 "X-Total-Count": str(len(body_rows))},
    )
