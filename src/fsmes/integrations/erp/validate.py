"""Check outbound confirmation files against the contract and the house rules.

This is what a plant's ERP team runs on a shadow-mode outbox: a folder of
files, no connection to anything, and an answer in words about whether
what this MES would send is correct.

Two kinds of finding, and the difference matters:

* a **problem** is something an ERP would be right to reject, or a number
  that contradicts another number in the same document. Any problem makes
  `fsmes erp validate` exit non-zero.
* a **note** is something true and worth reading — a negative work in
  progress, a completion with no lot, the same confirmation twice. Notes
  never fail the run, because every one of them is a fact the MES reports
  on purpose. Turning them into failures would teach a plant to make the
  MES lie.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from fsmes.integrations.erp import b2mml
from fsmes.integrations.erp.contract import (
    CONFIRMATION_KINDS,
    OperationConfirmation,
    OrderCompletion,
    parse_confirmation,
)

#: Files this reads. Anything else in the folder is counted and named
#: rather than silently passed over.
READABLE = (".json", ".xml")

#: Quantities and durations that no correct document can carry a negative
#: value in. `wip_qty` is deliberately not here: negative is reported, not
#: clamped, and it is checked for consistency instead.
NEVER_NEGATIVE = ("input_qty", "good_qty", "scrap_qty", "ordered_qty", "over_qty",
                  "setup_seconds", "machine_seconds", "labour_seconds")

TOLERANCE = 1e-6
#: A second of slack between `machine_seconds` and the times it is derived
#: from, because a file can carry whole seconds where the MES had more.
SECONDS_TOLERANCE = 1.0


@dataclass(frozen=True)
class Finding:
    severity: Literal["problem", "note"]
    says: str


@dataclass
class Checked:
    """One document, and what was found in it."""

    path: Path
    kind: str | None = None
    order: str | None = None
    message_key: str | None = None
    findings: list[Finding] = field(default_factory=list)
    payload: dict = field(default_factory=dict, repr=False)

    @property
    def problems(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "problem"]

    @property
    def notes(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "note"]

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass
class Report:
    """Every document read, every file not read, and the totals for both.

    States its totals because a list that does not say how much it left
    out reads as the whole story - including the files it skipped, which
    is where a plant's real outbox surprises differ from a test's.
    """

    documents: list[Checked] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(d.ok for d in self.documents)

    def render(self) -> list[str]:
        lines = []
        for document in self.documents:
            state = "ok" if document.ok else "not ok"
            what = document.kind or "unreadable"
            lines.append(f"  {state:<7} {document.path.name}  ({what})")
            for finding in document.findings:
                mark = "problem" if finding.severity == "problem" else "note"
                lines.append(f"            {mark}: {finding.says}")
        for path, why in self.skipped:
            lines.append(f"  skipped {path.name}  ({why})")
        good = sum(1 for d in self.documents if d.ok)
        problems = sum(len(d.problems) for d in self.documents)
        notes = sum(len(d.notes) for d in self.documents)
        lines.append(
            f"{len(self.documents)} documents checked: {good} correct, "
            f"{len(self.documents) - good} with problems ({problems} in all), "
            f"{notes} notes. {len(self.skipped)} files skipped.")
        return lines


def _moment(value) -> datetime | None:
    """A timestamp from a payload, as a naive UTC datetime.

    The MES writes naive UTC; a file from somewhere else may carry an
    offset. Comparing the two directly raises, so an offset is converted
    and dropped rather than refused.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def check_payload(payload: dict) -> tuple[str | None, list[Finding]]:
    """The kind of document this is, and everything wrong or worth saying.

    The contract models do the shape; the rules below do the arithmetic
    the models cannot express - that the quantities agree with each other,
    that time runs forwards, and that an over-run is reported as one.
    """
    findings: list[Finding] = []
    kind = payload.get("kind")
    if kind is not None and kind not in CONFIRMATION_KINDS:
        return kind, [Finding(
            "problem", f"kind is {kind!r}, which is not a confirmation. The MES's outbox is "
                       "a domain event log and also carries equipment states and holds; "
                       "those are for the namespace, not for an ERP.")]
    if kind is None:
        findings.append(Finding(
            "note", "no kind. Read as an order completion, which is what a file written "
                    "before this contract existed is."))
    try:
        confirmation = parse_confirmation(payload)
    except ValidationError as exc:
        for error in exc.errors():
            where = ".".join(str(part) for part in error["loc"]) or "the document"
            findings.append(Finding("problem", f"{where}: {error['msg'].lower()}"))
        return payload.get("kind"), findings
    except Exception as exc:
        return None, [Finding("problem", f"this is not a confirmation this contract knows: {exc}")]

    if payload.get("kind") == "production_confirmation":
        findings.append(Finding(
            "note", "this is the pre-contract spelling of an order completion. It is still "
                    "read and still delivered; new files carry kind 'order_completion'."))
    if not (confirmation.message_key or "").strip():
        findings.append(Finding(
            "problem", "message_key is empty. It is the only thing that stops the same "
                       "confirmation being posted twice."))

    for name in NEVER_NEGATIVE:
        value = _number(getattr(confirmation, name, None))
        if value is not None and value < 0:
            findings.append(Finding(
                "problem", f"{name} is {value:g}. A plant cannot make a negative number of "
                           "anything, and no ERP will post it."))

    for component in getattr(confirmation, "components", []):
        if component.quantity <= 0:
            findings.append(Finding(
                "problem", f"lot {component.lot} is consumed as {component.quantity:g}. A "
                           "consumption line is there to say something was used."))

    started, completed = _moment(confirmation.started_at), _moment(confirmation.completed_at)
    if started and completed and completed < started:
        findings.append(Finding(
            "problem", f"it finished at {completed.isoformat()}, before it started at "
                       f"{started.isoformat()}."))

    if isinstance(confirmation, OperationConfirmation):
        findings += _operation_rules(confirmation, started, completed)
    elif isinstance(confirmation, OrderCompletion):
        findings += _completion_rules(confirmation)
    return confirmation.kind, findings


def _operation_rules(c: OperationConfirmation, started, completed) -> list[Finding]:
    findings: list[Finding] = []
    expected = c.input_qty - c.good_qty - c.scrap_qty
    if abs(c.wip_qty - expected) > TOLERANCE:
        findings.append(Finding(
            "problem", f"wip_qty says {c.wip_qty:g}, but input_qty minus good_qty minus "
                       f"scrap_qty is {expected:g}. One of the four numbers is wrong."))
    elif c.wip_qty < -TOLERANCE:
        findings.append(Finding(
            "note", f"wip_qty is {c.wip_qty:g}: this step put out {-c.wip_qty:g} more than "
                    "went into it. That is reported rather than clamped, and it is usually "
                    "one machine count that carried the line past the order."))
    if c.machine_seconds is not None and started and completed:
        measured = (completed - started).total_seconds()
        if abs(c.machine_seconds - measured) > SECONDS_TOLERANCE:
            findings.append(Finding(
                "problem", f"machine_seconds is {c.machine_seconds:g}, but the step was open "
                           f"from {started.isoformat()} to {completed.isoformat()}, which is "
                           f"{measured:g} seconds."))
    if c.cost_center is None:
        findings.append(Finding(
            "note", "no cost_center. The ERP will have to decide where this work is "
                    "accounted; set one on the line and every machine under it inherits it."))
    return findings


def _completion_rules(c: OrderCompletion) -> list[Finding]:
    findings: list[Finding] = []
    over = max(c.good_qty - c.ordered_qty, 0.0)
    if abs(c.over_qty - over) > TOLERANCE:
        if over > 0:
            findings.append(Finding(
                "problem", f"good_qty {c.good_qty:g} is {over:g} past the ordered "
                           f"{c.ordered_qty:g}, but over_qty says {c.over_qty:g}. An over-run "
                           "that reaches the ERP as an ordinary good quantity is how a stock "
                           "discrepancy nobody can explain starts."))
        else:
            findings.append(Finding(
                "problem", f"over_qty is {c.over_qty:g} on an order that made {c.good_qty:g} "
                           f"of {c.ordered_qty:g}. Nothing was made past the order."))
    elif over > 0:
        findings.append(Finding(
            "note", f"the line ran {over:g} past the order: {c.good_qty:g} good against "
                    f"{c.ordered_qty:g} ordered. Everything the machine counted is booked, "
                    "and over_qty says so."))
    if c.good_qty > 0 and not c.lot:
        findings.append(Finding(
            "note", "good_qty is positive but there is no lot. The ERP has nothing to post "
                    "the goods receipt against."))
    if c.good_qty == 0 and c.lot:
        findings.append(Finding(
            "note", f"lot {c.lot} on an order that yielded nothing good."))
    return findings


def _documents_in(path: Path) -> list[dict]:
    """The confirmation payloads in one file. Raises if it cannot be read."""
    if path.suffix.lower() == ".json":
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return [loaded]
        if isinstance(loaded, list) and all(isinstance(row, dict) for row in loaded):
            return loaded
        raise ValueError("a confirmation file holds one JSON object, or a list of them")
    return [b2mml.parse_confirmation(path.read_text(encoding="utf-8"))]


def check_file(path: Path) -> list[Checked]:
    try:
        payloads = _documents_in(path)
    except Exception as exc:
        return [Checked(path=path, findings=[Finding(
            "problem", f"could not be read as a confirmation: {type(exc).__name__}: {exc}")])]
    checked = []
    for payload in payloads:
        kind, findings = check_payload(payload)
        checked.append(Checked(path=path, kind=kind, order=payload.get("order"),
                               message_key=payload.get("message_key"), findings=findings,
                               payload=payload))
    return checked


def validate(path: Path) -> Report:
    """Check one file, or every confirmation in one folder."""
    path = Path(path)
    report = Report()
    if path.is_dir():
        candidates = sorted(p for p in path.iterdir() if p.is_file())
    else:
        candidates = [path]
    for candidate in candidates:
        if candidate.name.startswith(".") and candidate.name.endswith(".part"):
            report.skipped.append((candidate, "still being written"))
            continue
        if candidate.suffix.lower() not in READABLE:
            report.skipped.append((candidate, f"not {' or '.join(READABLE)}"))
            continue
        report.documents.extend(check_file(candidate))
    _check_keys_across_files(report)
    return report


def _same_facts(one: dict, other: dict) -> bool:
    """Do two payloads say the same thing?

    Compared on what is stated rather than on the dict: a field written as
    null in JSON and simply absent from the XML is the same fact told
    twice, and calling that a contradiction would make the JSON and B2MML
    renderings of one confirmation look like two.
    """
    def stated(payload: dict) -> dict:
        return {k: v for k, v in payload.items() if v is not None and v != []}

    return stated(one) == stated(other)


def _check_keys_across_files(report: Report) -> None:
    """The same confirmation twice, across a whole folder.

    A repeated key is not itself wrong - it is what the key is for, and a
    correct reader posts it once. Two documents sharing a key and saying
    different things is wrong, and it is the failure a file exchange
    actually produces: a confirmation regenerated after somebody edited it
    by hand.
    """
    seen: dict[str, Checked] = {}
    for document in report.documents:
        key = document.message_key
        if not key:
            continue
        first = seen.setdefault(key, document)
        if first is document:
            continue
        if _same_facts(document.payload, first.payload):
            document.findings.append(Finding(
                "note", f"the same message_key as {first.path.name}, and the same content. A "
                        "reader that keys on it posts this once, which is what the key is for."))
        else:
            document.findings.append(Finding(
                "problem", f"the same message_key as {first.path.name}, but not the same "
                           "content. A reader keying on it will post one of the two and "
                           "never know the other existed."))
