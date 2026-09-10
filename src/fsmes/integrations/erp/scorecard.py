"""The shadow scorecard: where the two records of the same shift differ.

Running this MES beside the one in charge only answers anything if
somebody puts the two records side by side. `fsmes score` already does
that against a simulated plant, where the truth is scripted and known. A
real plant has no scripted truth. It has two records — what the incumbent
MES booked, exported by a person as a file, and what this MES would have
sent its ERP — and the honest answer is a comparison of the two.

Three rules hold this together, and they are the whole design:

1. **The report never says which side is right.** It says where they
   differ, with both sides' numbers, and leaves the plant to judge. A
   scorecard that scored itself would be worth nothing.
2. **Unknown is not zero, and unknown is not agreement.** A field only one
   side states is counted as *not compared* and named. The commonest way
   to make a shadow run look good is to quietly read a blank cell as a
   match.
3. **Every list states its total.** The largest disagreements are a
   sample; the line above them says how many there were.

What it compares, per matched operation and per matched order: good
quantity, scrap quantity, start, end, and duration. What it will not do is
derive an order total by adding operations up — good units at operation 10
and good units at operation 20 are the same units, and summing them is a
number no one should act on. Order totals are compared only when the
export carries order rows of its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fsmes.integrations.erp.contract import (
    CONFIRMATION_KINDS,
    OperationConfirmation,
    OrderCompletion,
    parse_confirmation,
)
from fsmes.integrations.erp.incumbent import Booking, Export, Mapping

#: The default slack, and why each is what it is.
#:
#: Quantities: none. A unit is a unit; two systems counting the same
#: machine should agree exactly, and a plant that needs slack here should
#: set it deliberately and see it printed at the top of its own report.
#:
#: Times: sixty seconds. Operators book an incumbent MES's start and end
#: to the minute, by hand, some time after the fact; this MES takes them
#: from a tag. Comparing those to the second would report a disagreement
#: on every row and drown the ones that matter.
EPOCH = datetime(1970, 1, 1)

DEFAULT_QUANTITY_TOLERANCE = 0.0
DEFAULT_SECONDS_TOLERANCE = 60.0


@dataclass(frozen=True)
class Tolerances:
    quantity: float = DEFAULT_QUANTITY_TOLERANCE
    seconds: float = DEFAULT_SECONDS_TOLERANCE

    @classmethod
    def from_mapping(cls, mapping: Mapping, quantity: float | None = None,
                     seconds: float | None = None) -> Tolerances:
        """The mapping file's tolerances, with an explicit argument winning."""
        return cls(
            quantity=_first(quantity, mapping.tolerances.get("quantity"),
                            DEFAULT_QUANTITY_TOLERANCE),
            seconds=_first(seconds, mapping.tolerances.get("seconds"),
                           DEFAULT_SECONDS_TOLERANCE),
        )

    def says(self) -> str:
        return (f"quantities within {self.quantity:g} "
                f"{'unit' if self.quantity == 1 else 'units'} agree; times and durations "
                f"within {self.seconds:g} seconds agree.")


def _first(*values):
    for value in values:
        if value is not None:
            return value
    return None


@dataclass(frozen=True)
class Measure:
    """One thing the two records both claim about the same piece of work."""

    name: str
    says: str
    unit: str          # "units" or "seconds"
    kind: str          # "quantity" or "seconds"


FIELDS: tuple[Measure, ...] = (
    Measure("good_qty", "good quantity", "units", "quantity"),
    Measure("scrap_qty", "scrap quantity", "units", "quantity"),
    Measure("started_at", "start", "seconds", "seconds"),
    Measure("completed_at", "end", "seconds", "seconds"),
    Measure("duration_seconds", "duration", "seconds", "seconds"),
)
BY_NAME = {f.name: f for f in FIELDS}


@dataclass(frozen=True)
class Difference:
    """One field, on one piece of work, where the two records disagree."""

    scope: str                 # "operation" or "order"
    order: str
    operation: str | None
    field: str
    incumbent: float | str | None
    mes: float | str | None
    gap: float
    unit: str

    @property
    def where(self) -> str:
        return f"{self.order} op {self.operation}" if self.operation else self.order

    def says(self) -> str:
        return (f"{self.where}: {BY_NAME[self.field].says} — the incumbent has "
                f"{_show(self.incumbent, self.field)}, this MES has "
                f"{_show(self.mes, self.field)} ({_gap(self.gap, self.unit)} apart)")

    def as_dict(self) -> dict:
        return {"scope": self.scope, "order": self.order, "operation": self.operation,
                "field": self.field, "incumbent": self.incumbent, "mes": self.mes,
                "gap": self.gap, "unit": self.unit, "says": self.says()}


@dataclass
class Agreement:
    """How one field fared across everything that was compared."""

    field: str
    agree: int = 0
    differ: int = 0
    #: Compared to nothing, because one side or both did not state it.
    not_stated: int = 0

    @property
    def compared(self) -> int:
        return self.agree + self.differ

    @property
    def total(self) -> int:
        return self.compared + self.not_stated

    def as_dict(self) -> dict:
        return {"field": self.field, "says": BY_NAME[self.field].says, "agree": self.agree,
                "differ": self.differ, "not_stated": self.not_stated,
                "compared": self.compared, "pairs": self.total}


@dataclass
class Side:
    """What one record turned out to hold. Totals, stated."""

    name: str
    orders: int = 0
    operations: int = 0
    order_totals: int = 0
    documents: int = 0
    not_read: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"name": self.name, "orders": self.orders, "operations": self.operations,
                "order_totals": self.order_totals, "documents": self.documents,
                "not_read": list(self.not_read)}


@dataclass
class Scorecard:
    """The comparison, and everything it could not compare."""

    incumbent: Side
    mes: Side
    tolerances: Tolerances
    orders_both: list[str] = field(default_factory=list)
    orders_only_incumbent: list[str] = field(default_factory=list)
    orders_only_mes: list[str] = field(default_factory=list)
    operations_both: list[tuple[str, str]] = field(default_factory=list)
    operations_only_incumbent: list[tuple[str, str]] = field(default_factory=list)
    operations_only_mes: list[tuple[str, str]] = field(default_factory=list)
    operation_agreement: list[Agreement] = field(default_factory=list)
    order_agreement: list[Agreement] = field(default_factory=list)
    differences: list[Difference] = field(default_factory=list)
    cannot_tell: list[str] = field(default_factory=list)

    @property
    def compared(self) -> int:
        return sum(a.compared for a in self.operation_agreement + self.order_agreement)

    @property
    def agreed(self) -> int:
        return sum(a.agree for a in self.operation_agreement + self.order_agreement)

    def largest(self, per_field: int = 5) -> list[Difference]:
        """The biggest disagreement in each field, biggest first.

        A sample, and `differences` is the whole of it. Ordered within a
        field rather than across fields, because two units of scrap and
        two hundred seconds of start time are not comparable and putting
        them in one ranking would say they were.
        """
        out: list[Difference] = []
        for known in FIELDS:
            same = [d for d in self.differences if d.field == known.name]
            out.extend(sorted(same, key=lambda d: -abs(d.gap))[:per_field])
        return out

    def as_dict(self) -> dict:
        return {
            "kind": "shadow_scorecard",
            "tolerances": {"quantity": self.tolerances.quantity,
                           "seconds": self.tolerances.seconds,
                           "says": self.tolerances.says()},
            "sides": {"incumbent": self.incumbent.as_dict(), "mes": self.mes.as_dict()},
            "orders": {
                "both": self.orders_both,
                "only_incumbent": self.orders_only_incumbent,
                "only_mes": self.orders_only_mes,
                "total": len(self.orders_both) + len(self.orders_only_incumbent)
                         + len(self.orders_only_mes),
            },
            "operations": {
                "both": [list(p) for p in self.operations_both],
                "only_incumbent": [list(p) for p in self.operations_only_incumbent],
                "only_mes": [list(p) for p in self.operations_only_mes],
                "total": len(self.operations_both) + len(self.operations_only_incumbent)
                         + len(self.operations_only_mes),
            },
            "agreement": {
                "per_operation": [a.as_dict() for a in self.operation_agreement],
                "per_order": [a.as_dict() for a in self.order_agreement],
                "compared": self.compared,
                "agree": self.agreed,
                "differ": len(self.differences),
            },
            "differences": [d.as_dict() for d in self.differences],
            "what_this_cannot_tell_you": list(self.cannot_tell),
            "note": ("This report says where two records differ. It does not say which of "
                     "them is right, and nothing in it should be read as scoring either "
                     "system against the other."),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent, default=str) + "\n"

    def render(self) -> list[str]:
        """The whole thing in words, for a terminal."""
        lines = [
            f"  tolerances  {self.tolerances.says()}",
            "",
            f"  the incumbent's export   {self.incumbent.orders} orders, "
            f"{self.incumbent.operations} operation rows, "
            f"{self.incumbent.order_totals} order-total rows",
            f"  this MES's confirmations {self.mes.orders} orders, "
            f"{self.mes.operations} operation confirmations, "
            f"{self.mes.order_totals} order completions",
            "",
            f"  orders     {len(self.orders_both)} in both, "
            f"{len(self.orders_only_incumbent)} only the incumbent booked, "
            f"{len(self.orders_only_mes)} only this MES has",
            f"  operations {len(self.operations_both)} in both, "
            f"{len(self.operations_only_incumbent)} only the incumbent booked, "
            f"{len(self.operations_only_mes)} only this MES has",
            "",
        ]
        for title, agreements in (("per operation", self.operation_agreement),
                                  ("per order", self.order_agreement)):
            stated = [a for a in agreements if a.total]
            if not stated:
                continue
            lines.append(f"  agreement, {title}")
            for agreement in stated:
                known = BY_NAME[agreement.field]
                lines.append(
                    f"    {known.says:<15} {agreement.agree} agree, {agreement.differ} differ, "
                    f"{agreement.not_stated} not compared, of {agreement.total}")
            lines.append("")

        shown = self.largest()
        head = f"  {_count(len(self.differences), 'disagreement')}"
        if not shown:
            lines.append(head + ".")
        elif len(shown) == len(self.differences):
            lines.append(head + ":")
        else:
            lines.append(head + f", the largest {len(shown)} of them:")
        for difference in shown:
            lines.append(f"    {difference.says()}")
        if shown and len(shown) < len(self.differences):
            lines.append(f"    ... and {len(self.differences) - len(shown)} more, all of them "
                         "in the JSON and the HTML report.")
        lines.append("")
        lines.append("  what this cannot tell you")
        for says in self.cannot_tell:
            lines.append(f"    - {says}")
        return lines


def _show(value, field_name: str = "") -> str:
    """One side's number, with the unit a person needs to read it."""
    if value is None:
        return "nothing stated"
    if field_name == "duration_seconds" and isinstance(value, int | float):
        return _gap(float(value), "seconds")
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _count(how_many: int, thing: str) -> str:
    """`1 order`, `2 orders`. A totals line is read by people."""
    return f"{how_many} {thing}" + ("" if how_many == 1 else "s")


def _is_are(how_many: int) -> str:
    return "is" if how_many == 1 else "are"


def _has_have(how_many: int) -> str:
    return "has" if how_many == 1 else "have"


def _gap(gap: float, unit: str) -> str:
    if unit == "seconds":
        if abs(gap) >= 90:
            return f"{gap / 60:.1f} minutes"
        return f"{gap:g} seconds"
    return f"{gap:g} {'unit' if abs(gap) == 1 else 'units'}"


# ------------------------------------------------------------------ our side

@dataclass
class OurRecord:
    """This MES's own confirmations, keyed the way they are compared."""

    operations: dict[tuple[str, str], OperationConfirmation] = field(default_factory=dict)
    completions: dict[str, OrderCompletion] = field(default_factory=dict)
    documents: int = 0
    not_read: list[str] = field(default_factory=list)

    @property
    def orders(self) -> set[str]:
        return set(self.completions) | {order for order, _ in self.operations}

    def add(self, payload: dict, where: str) -> None:
        kind = payload.get("kind", "production_confirmation")
        if kind not in CONFIRMATION_KINDS:
            # The outbox is a domain event log and also carries equipment
            # states and holds. They are not confirmations and are not a
            # fault; they are simply not what this compares.
            return
        try:
            confirmation = parse_confirmation(payload)
        except Exception as exc:
            self.not_read.append(f"{where}: {type(exc).__name__}: {exc}")
            return
        self.documents += 1
        if isinstance(confirmation, OperationConfirmation):
            self.operations.setdefault((confirmation.order, str(confirmation.seq)), confirmation)
        else:
            self.completions.setdefault(confirmation.order, confirmation)


def read_confirmations(path: Path) -> OurRecord:
    """This MES's side, from a file or a folder of confirmation files.

    The same confirmation may be in the folder twice — the file adapter
    writes XML, and the published examples write JSON beside it. They are
    keyed by order and operation, so the second one read is the same fact
    and is not counted twice.
    """
    from fsmes.integrations.erp import validate as validator

    path = Path(path)
    record = OurRecord()
    candidates = sorted(p for p in path.iterdir() if p.is_file()) if path.is_dir() else [path]
    for candidate in candidates:
        if candidate.suffix.lower() not in validator.READABLE:
            continue
        try:
            payloads = validator.documents_in(candidate)
        except Exception as exc:
            record.not_read.append(f"{candidate.name}: {type(exc).__name__}: {exc}")
            continue
        for payload in payloads:
            record.add(payload, candidate.name)
    return record


def read_outbox(session) -> OurRecord:
    """This MES's side, straight out of its own outbox.

    The folder is what an ERP team collects; the outbox is what the MES
    knows. They should say the same thing, and after the ERP side has
    collected and deleted the files, only this one still does.
    """
    from sqlalchemy import select

    from fsmes.domain import ErpMessage, MessageDirection

    record = OurRecord()
    rows = session.scalars(
        select(ErpMessage).where(ErpMessage.direction == MessageDirection.OUT)
        .order_by(ErpMessage.id))
    for row in rows:
        record.add(row.payload or {}, f"outbox message {row.id}")
    return record


# ------------------------------------------------------------------ compare

def _naive(moment: datetime | None) -> datetime | None:
    """A timestamp both sides can be subtracted from.

    This MES writes naive UTC; a file from elsewhere may carry an offset.
    An offset is converted and dropped rather than refused, which is what
    the confirmation validator does with the same problem.
    """
    if moment is None or moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _measured(known: Measure, source) -> tuple[float | None, float | str | None]:
    """One measure off either side: a number to compare, and what to show.

    The two differ for a timestamp — the gap between two starts is in
    seconds, and the number a person needs to read is the timestamp
    itself. Returning both is what lets the report say "12:04 and 12:31,
    27 minutes apart" instead of one or the other.
    """
    started = _naive(getattr(source, "started_at", None))
    completed = _naive(getattr(source, "completed_at", None))
    if known.name == "duration_seconds":
        if started is None or completed is None:
            return None, None
        seconds = (completed - started).total_seconds()
        return seconds, seconds
    if known.name in ("started_at", "completed_at"):
        moment = started if known.name == "started_at" else completed
        if moment is None:
            return None, None
        # Seconds from a fixed epoch rather than `timestamp()`, which
        # reads a naive datetime as local time and would put an hour
        # between two records either side of a daylight-saving change.
        return (moment - EPOCH).total_seconds(), moment.isoformat(sep=" ")
    value = getattr(source, known.name, None)
    return (None, None) if value is None else (float(value), float(value))


def _compare(scope: str, order: str, operation: str | None, theirs, ours,
             tolerances: Tolerances, agreements: dict[str, Agreement],
             differences: list[Difference]) -> None:
    for known in FIELDS:
        theirs_number, theirs_shown = _measured(known, theirs)
        ours_number, ours_shown = _measured(known, ours)
        agreement = agreements[known.name]
        if theirs_number is None or ours_number is None:
            agreement.not_stated += 1
            continue
        gap = abs(theirs_number - ours_number)
        allowed = tolerances.quantity if known.kind == "quantity" else tolerances.seconds
        if gap <= allowed:
            agreement.agree += 1
            continue
        agreement.differ += 1
        differences.append(Difference(
            scope=scope, order=order, operation=operation, field=known.name,
            incumbent=theirs_shown, mes=ours_shown, gap=gap, unit=known.unit))


def compare(export: Export, ours: OurRecord, tolerances: Tolerances | None = None) -> Scorecard:
    """Put the two records side by side and say where they differ."""
    tolerances = tolerances or Tolerances()

    theirs_operations: dict[tuple[str, str], Booking] = {}
    repeated: list[str] = []
    for booking in export.operations:
        key = (booking.order, str(booking.operation))
        if key in theirs_operations:
            repeated.append(f"{booking.order} op {booking.operation}")
            continue
        theirs_operations[key] = booking
    theirs_totals: dict[str, Booking] = {}
    for booking in export.order_totals:
        theirs_totals.setdefault(booking.order, booking)

    card = Scorecard(
        incumbent=Side(name="the incumbent MES's export", orders=len(export.orders),
                       operations=len(export.operations),
                       order_totals=len(export.order_totals),
                       documents=export.rows_read,
                       not_read=[f"row {row}: {why}" for row, why in export.unusable]),
        mes=Side(name="this MES's confirmations", orders=len(ours.orders),
                 operations=len(ours.operations), order_totals=len(ours.completions),
                 documents=ours.documents, not_read=list(ours.not_read)),
        tolerances=tolerances,
    )

    their_orders, our_orders = export.orders, ours.orders
    card.orders_both = sorted(their_orders & our_orders)
    card.orders_only_incumbent = sorted(their_orders - our_orders)
    card.orders_only_mes = sorted(our_orders - their_orders)

    both = sorted(set(theirs_operations) & set(ours.operations))
    card.operations_both = both
    card.operations_only_incumbent = sorted(set(theirs_operations) - set(ours.operations))
    card.operations_only_mes = sorted(set(ours.operations) - set(theirs_operations))

    card.operation_agreement = [Agreement(field=f.name) for f in FIELDS]
    card.order_agreement = [Agreement(field=f.name) for f in FIELDS]
    by_operation = {a.field: a for a in card.operation_agreement}
    by_order = {a.field: a for a in card.order_agreement}

    for order, operation in both:
        _compare("operation", order, operation, theirs_operations[(order, operation)],
                 ours.operations[(order, operation)], tolerances, by_operation, card.differences)
    for order in sorted(set(theirs_totals) & set(ours.completions)):
        _compare("order", order, None, theirs_totals[order], ours.completions[order],
                 tolerances, by_order, card.differences)

    card.cannot_tell = _cannot_tell(card, export, ours, repeated,
                                    orders_with_totals=set(theirs_totals))
    return card


def _cannot_tell(card: Scorecard, export: Export, ours: OurRecord, repeated: list[str],
                 orders_with_totals: set[str]) -> list[str]:
    """Everything the comparison did not cover, named rather than left out.

    This section is the reason the rest of the report can be trusted. A
    scorecard that prints an agreement percentage and stops has told a
    plant that the part it did not look at agreed.
    """
    says: list[str] = []
    total_orders = (len(card.orders_both) + len(card.orders_only_incumbent)
                    + len(card.orders_only_mes))
    if card.orders_only_incumbent:
        how_many = len(card.orders_only_incumbent)
        says.append(
            f"{how_many} of {_count(total_orders, 'order')} {_is_are(how_many)} only in the "
            f"incumbent's export ({', '.join(card.orders_only_incumbent[:8])}"
            f"{', ...' if how_many > 8 else ''}). Nothing about "
            f"{'it' if how_many == 1 else 'them'} was compared. That may be work this MES was "
            "not watching, an order outside the export's period, or a code the mapping file "
            "does not translate.")
    if card.orders_only_mes:
        how_many = len(card.orders_only_mes)
        says.append(
            f"{how_many} of {_count(total_orders, 'order')} {_is_are(how_many)} only in this "
            f"MES's confirmations ({', '.join(card.orders_only_mes[:8])}"
            f"{', ...' if how_many > 8 else ''}). Nothing about "
            f"{'it' if how_many == 1 else 'them'} was compared, and the export not carrying "
            f"{'it' if how_many == 1 else 'them'} is not evidence that the incumbent did not "
            "book the work.")
    if card.operations_only_incumbent:
        how_many = len(card.operations_only_incumbent)
        says.append(f"{_count(how_many, 'operation row')} in the export "
                    f"{_has_have(how_many)} no confirmation from this MES to compare against.")
    if card.operations_only_mes:
        how_many = len(card.operations_only_mes)
        says.append(f"{_count(how_many, 'operation confirmation')} from this MES "
                    f"{_has_have(how_many)} no row in the export to compare against.")
    if not orders_with_totals:
        says.append("The export carries no order-total rows, so no order total was compared. "
                    "Operation quantities are not added up to make one: good units at two "
                    "operations of the same order are usually the same units, and summing them "
                    "produces a number that looks like a total and is not one.")
    else:
        missing = sorted(set(ours.completions) - orders_with_totals)
        if missing:
            says.append(f"{_count(len(missing), 'order')} this MES completed "
                        f"{_has_have(len(missing))} no order-total row in the export, so "
                        "those totals were not compared.")
    if export.absent_columns:
        says.append("The export does not carry " + ", ".join(export.absent_columns) +
                    ". A field one side never states cannot agree or disagree, and is counted "
                    "as not compared rather than as a match.")
    not_stated = sum(a.not_stated for a in card.operation_agreement + card.order_agreement)
    if not_stated:
        total = sum(a.total for a in card.operation_agreement + card.order_agreement)
        not_an_agreement = ("It is not an agreement." if not_stated == 1
                            else "They are not agreements.")
        says.append(f"{not_stated} of {total} field-by-field comparisons could not be made, "
                    "because one side or the other did not state the value. "
                    + not_an_agreement)
    if repeated:
        says.append(f"{_count(len(repeated), 'operation')} "
                    f"{'appears' if len(repeated) == 1 else 'appear'} more than once in the export "
                    f"({', '.join(repeated[:5])}{', ...' if len(repeated) > 5 else ''}). The "
                    "first row of each was compared and the rest were not; a partial "
                    "confirmation booked twice is a real thing an incumbent MES does, and "
                    "this comparison cannot tell that from a duplicated export.")
    for row, why in export.unreadable:
        says.append(f"Row {row} of the export: {why}. That value was read as unknown.")
    for row, why in export.unusable:
        says.append(f"Row {row} of the export was not used: {why}.")
    for why in ours.not_read:
        says.append(f"A confirmation was not read: {why}.")
    says.append(
        "Neither record is evidence of what the line actually did. Both are records of it. "
        "Where they differ, this report names the difference and stops; which side to believe "
        "is the plant's judgement, usually made by looking at the machine.")
    says.append(
        "Nothing here can see a period neither record covers — a shift this MES was not "
        "connected for, a stop nobody labelled, a counter that reset between two readings. "
        "The comparison is bounded by what the two files hold.")
    return says
