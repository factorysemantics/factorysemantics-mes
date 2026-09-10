"""The incumbent MES's bookings, as a person can actually export them.

A plant running this MES in shadow mode already has an MES in charge, and
the only honest way to say how well the shadow did is to compare it with
what that system booked. That record does not arrive over an API. It
arrives as a file somebody exported: a CSV out of a report screen, or a
JSON extract a plant's own integrator wrote.

So this reads a **generic** shape rather than any product's:

===============  ========  =============================================
column           required  what it is
===============  ========  =============================================
`order`          yes       the order the work was booked against
`operation`      no        the operation's sequence or code within the
                           order. Blank means the row is the order's own
                           total rather than one step of it.
`equipment`      no        the machine or work centre the step ran on
`good_qty`       no        units booked good
`scrap_qty`      no        units booked scrap
`started_at`     no        when the step started
`completed_at`   no        when the step finished
===============  ========  =============================================

Only `order` is required, because only `order` is a row's identity.
Everything else is compared when both sides state it and reported as
**unknown** when either does not. A blank scrap cell is not zero scrap; it
is a plant that did not export the column, and a scorecard that read it as
zero would invent agreement.

The incumbent will not use those column names, and will not use this MES's
order or equipment codes. Both are plant boundaries, so both are config:
a mapping file renames the columns and translates the codes. There is no
code in this package that knows any particular system's spelling of
anything, and there never will be — see the provenance rule in
CONTRIBUTING.md.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

#: The canonical column names, and whether a row can do without one.
COLUMNS = ("order", "operation", "equipment", "good_qty", "scrap_qty",
           "started_at", "completed_at")
REQUIRED = ("order",)

#: The sections a mapping file may carry. Anything else is refused rather
#: than ignored: a mapping file with a misspelled section is a plant that
#: thinks it has translated its codes and has not.
MAPPING_SECTIONS = ("columns", "orders", "operations", "equipment",
                    "time_format", "tolerances")

QUANTITY_COLUMNS = ("good_qty", "scrap_qty")
TIME_COLUMNS = ("started_at", "completed_at")


class MappingError(ValueError):
    """A mapping file this reader will not guess at."""


@dataclass(frozen=True)
class Mapping:
    """Config for one incumbent's export. Never code.

    * `columns` — canonical name -> the header this export actually uses.
    * `orders` — the incumbent's order code -> this MES's.
    * `operations` — the incumbent's operation code -> this MES's sequence.
    * `equipment` — the incumbent's machine code -> this MES's.
    * `time_format` — a `strptime` format, when the export's timestamps are
      not ISO 8601. Stated rather than sniffed: guessing between `03/04`
      as March the fourth and the fourth of March is how a comparison
      quietly shifts a whole shift.
    * `tolerances` — `quantity` in units and `seconds` in seconds.
    """

    columns: dict[str, str] = field(default_factory=dict)
    orders: dict[str, str] = field(default_factory=dict)
    operations: dict[str, str] = field(default_factory=dict)
    equipment: dict[str, str] = field(default_factory=dict)
    time_format: str | None = None
    tolerances: dict[str, float] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> Mapping:
        """No translation: the export already speaks this MES's codes."""
        return cls()

    @classmethod
    def load(cls, path: Path | None) -> Mapping:
        if path is None:
            return cls.empty()
        path = Path(path)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MappingError(f"{path.name} is not readable as JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise MappingError(f"{path.name} holds a {type(loaded).__name__}; a mapping file "
                               "is one JSON object.")
        unknown = sorted(set(loaded) - set(MAPPING_SECTIONS))
        if unknown:
            raise MappingError(
                f"{path.name} has {', '.join(unknown)}, which this reader does not use. It "
                f"reads {', '.join(MAPPING_SECTIONS)}. Nothing was mapped, rather than part "
                "of it being mapped silently.")
        columns = _as_str_dict(loaded.get("columns"), "columns", path)
        bad = sorted(set(columns) - set(COLUMNS))
        if bad:
            raise MappingError(
                f"{path.name} maps {', '.join(bad)}, which is not a column this reads. The "
                f"keys are this MES's names — {', '.join(COLUMNS)} — and the values are the "
                "headings your export uses.")
        tolerances = loaded.get("tolerances") or {}
        if not isinstance(tolerances, dict):
            raise MappingError(f"{path.name}: tolerances is one JSON object.")
        return cls(
            columns=columns,
            orders=_as_str_dict(loaded.get("orders"), "orders", path),
            operations=_as_str_dict(loaded.get("operations"), "operations", path),
            equipment=_as_str_dict(loaded.get("equipment"), "equipment", path),
            time_format=loaded.get("time_format") or None,
            tolerances={str(k): float(v) for k, v in tolerances.items()},
        )

    def heading(self, column: str) -> str:
        return self.columns.get(column, column)

    def order(self, code: str) -> str:
        return self.orders.get(code, code)

    def machine(self, code: str | None) -> str | None:
        return self.equipment.get(code, code) if code else code

    def operation(self, code: str) -> str:
        return self.operations.get(code, code)


def _as_str_dict(value, section: str, path: Path) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MappingError(f"{path.name}: {section} is one JSON object of "
                           f"'theirs': 'ours' pairs.")
    return {str(k): str(v) for k, v in value.items()}


@dataclass(frozen=True)
class Booking:
    """One line of the incumbent's record, in this MES's codes.

    `seq` is the operation's sequence number when it reads as one, and
    `None` when the row is an order total or the incumbent numbers its
    operations with something that is not a number. `operation` keeps the
    label either way, so a report can name the step the plant knows.
    """

    order: str
    operation: str | None
    seq: int | None
    equipment: str | None
    good_qty: float | None
    scrap_qty: float | None
    started_at: datetime | None
    completed_at: datetime | None
    row: int                      # 1-based, counting the header as row 1
    raw_order: str = ""

    @property
    def is_order_total(self) -> bool:
        return self.operation is None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


@dataclass
class Export:
    """Everything read, everything not read, and the totals for both."""

    path: Path
    bookings: list[Booking] = field(default_factory=list)
    #: Rows that could not become a booking at all, and why.
    unusable: list[tuple[int, str]] = field(default_factory=list)
    #: Cells that were there and could not be read, and so are unknown
    #: rather than absent. The row around them is still compared.
    unreadable: list[tuple[int, str]] = field(default_factory=list)
    #: Canonical columns the export simply does not carry. A field nobody
    #: exported cannot disagree, and saying so is the difference between
    #: "they agree" and "nobody looked".
    absent_columns: list[str] = field(default_factory=list)
    rows_read: int = 0

    @property
    def orders(self) -> set[str]:
        return {booking.order for booking in self.bookings}

    @property
    def operations(self) -> list[Booking]:
        return [b for b in self.bookings if not b.is_order_total]

    @property
    def order_totals(self) -> list[Booking]:
        return [b for b in self.bookings if b.is_order_total]


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _quantity(value) -> tuple[float | None, str | None]:
    """A quantity, or None for unknown, or a reason it could not be read."""
    text = _text(value)
    if text == "":
        return None, None
    try:
        return float(text.replace(",", "")), None
    except ValueError:
        return None, f"{text!r} does not read as a number"


def _moment(value, time_format: str | None) -> tuple[datetime | None, str | None]:
    """A timestamp, as a naive UTC datetime.

    An offset is converted and dropped, because this MES writes naive UTC
    and comparing the two kinds directly raises. A timestamp with no offset
    is taken at face value: the plant's two systems are on the same clock,
    and if they are not, that is a finding for the plant, not something a
    file reader can fix.
    """
    text = _text(value)
    if text == "":
        return None, None
    if time_format:
        try:
            return datetime.strptime(text, time_format), None
        except ValueError:
            return None, (f"{text!r} does not match the mapping file's time_format "
                          f"{time_format!r}")
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, (f"{text!r} is not ISO 8601. Set time_format in the mapping file if "
                      "your export writes timestamps another way.")
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment, None


def _rows(path: Path) -> list[dict]:
    """The export's rows as dicts, whatever kind of file it is."""
    if path.suffix.lower() == ".json":
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            loaded = [loaded]
        if not isinstance(loaded, list) or not all(isinstance(row, dict) for row in loaded):
            raise ValueError("an incumbent export in JSON is a list of objects, one per booking")
        return loaded
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def read(path: Path, mapping: Mapping | None = None) -> Export:
    """Read one incumbent export into bookings in this MES's codes."""
    path = Path(path)
    mapping = mapping or Mapping.empty()
    rows = _rows(path)
    export = Export(path=path, rows_read=len(rows))

    present = set().union(*(set(row) for row in rows)) if rows else set()
    export.absent_columns = [c for c in COLUMNS if mapping.heading(c) not in present]

    for index, row in enumerate(rows, start=2):     # row 1 is the header
        raw_order = _text(row.get(mapping.heading("order")))
        if raw_order == "":
            export.unusable.append((index, "no order code, so there is nothing to compare it to"))
            continue
        raw_operation = _text(row.get(mapping.heading("operation")))
        operation = mapping.operation(raw_operation) if raw_operation else None
        seq: int | None = None
        if operation is not None:
            try:
                seq = int(str(operation).strip())
            except ValueError:
                seq = None

        problems: list[str] = []
        quantities: dict[str, float | None] = {}
        for column in QUANTITY_COLUMNS:
            value, why = _quantity(row.get(mapping.heading(column)))
            if why:
                problems.append(f"{column}: {why}")
            quantities[column] = value
        times: dict[str, datetime | None] = {}
        for column in TIME_COLUMNS:
            value, why = _moment(row.get(mapping.heading(column)), mapping.time_format)
            if why:
                problems.append(f"{column}: {why}")
            times[column] = value
        for problem in problems:
            # The row is still used. One unreadable cell is unknown; it does
            # not throw away the order code and the quantities beside it.
            export.unreadable.append((index, problem))

        export.bookings.append(Booking(
            order=mapping.order(raw_order),
            operation=str(operation) if operation is not None else None,
            seq=seq,
            equipment=mapping.machine(_text(row.get(mapping.heading("equipment"))) or None),
            good_qty=quantities["good_qty"],
            scrap_qty=quantities["scrap_qty"],
            started_at=times["started_at"],
            completed_at=times["completed_at"],
            row=index,
            raw_order=raw_order,
        ))
    return export
