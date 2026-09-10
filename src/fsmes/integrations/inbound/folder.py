"""The first inbound driver: files dropped in a folder.

Every plant can write a file. Most cannot open a port, and none will let a
new system query the incumbent's database in week one. So the first way the
MES hears what people typed elsewhere is a CSV — or the same rows as JSON —
landing in a folder, and the same three shapes will arrive over SQL and
MQTT later without this module changing.

Three rules make it safe to point at a plant's own export:

*Never delete an input.* Every file that is read is moved, to `processed`
or to `rejected`, keeping its name. A file this MES cannot make sense of is
still the plant's evidence.

*Never lie about time.* A timestamp with no zone in it is ambiguous, and
reading one as UTC when the plant wrote local time moves every stop in a
shift by hours. The mapping says which zone a supplier writes in; a naive
timestamp with no such setting is rejected, per row, with that as the
reason.

*Say what was left out.* A run reports rows read, rows applied, rows already
seen, and rows rejected with the first reason for each — totals over the
whole file, not over what fitted on a screen.

Restart safety comes from the writer, not from here: every row carries the
supplier's own key, so a file that was committed but not yet moved is read
again after a crash and changes nothing the second time.

Clean-room note: this module knows nothing about any particular system's
export format. Which columns a plant's file has, and what they are called,
is that plant's configuration.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from pydantic import ValidationError

from fsmes.db import utcnow
from fsmes.integrations.inbound.contract import EVENT_TYPES
from fsmes.services import inbound as inbound_service

log = structlog.get_logger("inbound.folder")

#: What a file may be. Anything else in an inbox is left where it is and
#: counted, because a plant's export folder often carries a lock file, a
#: checksum or a README, and moving those would be meddling.
READABLE = (".csv", ".json")


class MappingError(Exception):
    """The mapping file cannot be used as written, and says why."""


@dataclass(frozen=True)
class StreamMapping:
    """How one plant's file becomes one kind of inbound event.

    `columns` maps a contract field onto the column that carries it.
    `defaults` supplies a contract field the export simply does not have —
    written by a person, in configuration, so that "this supplier never
    reports scrap" is a decision somebody made and signed, not something
    the code assumed.
    """

    name: str
    source: str
    source_kind: str
    columns: dict[str, str]
    defaults: dict[str, object] = field(default_factory=dict)
    #: strptime format for the supplier's timestamps; ISO 8601 when absent.
    time_format: str | None = None
    #: The zone naive timestamps are written in. No default: guessing is the
    #: error this exists to prevent.
    timezone: str | None = None

    @property
    def model(self):
        return EVENT_TYPES[self.name]


@dataclass
class Rejection:
    row: int
    reason: str


@dataclass
class FileReport:
    """What one file did. Totals are over the file, never over a page."""

    path: str
    rows: int = 0
    applied: int = 0
    duplicates: int = 0
    rejections: list[Rejection] = field(default_factory=list)
    #: Where the input ended up. Never None on a completed run: nothing is
    #: deleted, so every file that was read is somewhere.
    moved_to: str | None = None

    @property
    def rejected(self) -> int:
        return len(self.rejections)

    def render(self) -> list[str]:
        lines = [f"{self.path}: {self.rows} row{'' if self.rows == 1 else 's'} read, "
                 f"{self.applied} recorded, {self.duplicates} already seen, {self.rejected} rejected"]
        for rejection in self.rejections:
            lines.append(f"  row {rejection.row}: {rejection.reason}")
        if self.moved_to:
            lines.append(f"  moved to {self.moved_to}")
        return lines


@dataclass
class RunReport:
    """What one pass over every inbox did."""

    files: list[FileReport] = field(default_factory=list)
    #: Files left untouched because their suffix is not one this reads.
    skipped: int = 0

    @property
    def rows(self) -> int:
        return sum(f.rows for f in self.files)

    @property
    def applied(self) -> int:
        return sum(f.applied for f in self.files)

    @property
    def duplicates(self) -> int:
        return sum(f.duplicates for f in self.files)

    @property
    def rejected(self) -> int:
        return sum(f.rejected for f in self.files)

    def render(self) -> list[str]:
        lines: list[str] = []
        for report in self.files:
            lines.extend(report.render())
        lines.append(f"{len(self.files)} file{'' if len(self.files) == 1 else 's'}, "
                     f"{self.rows} rows: {self.applied} recorded, {self.duplicates} already seen, "
                     f"{self.rejected} rejected"
                     + (f"; {self.skipped} file(s) of other kinds left alone" if self.skipped else ""))
        return lines


# ---------------------------------------------------------------- the mapping


def load_mapping(path: Path) -> dict[str, StreamMapping]:
    """Read the column-mapping file, or say exactly what is wrong with it."""
    path = Path(path)
    if not path.is_file():
        raise MappingError(
            f"no inbound mapping at {path}. It is the file that says what this plant's columns are "
            "called; without it nothing can be read, and this MES will not guess column names."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MappingError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise MappingError(f"{path} should be an object keyed by stream name, one of {sorted(EVENT_TYPES)}")

    mappings: dict[str, StreamMapping] = {}
    for name, spec in raw.items():
        if name not in EVENT_TYPES:
            raise MappingError(f"{path} configures a stream {name!r}; the streams are {sorted(EVENT_TYPES)}")
        if not isinstance(spec, dict):
            raise MappingError(f"{path}: {name} should be an object")
        for required in ("source", "source_kind", "columns"):
            if not spec.get(required):
                raise MappingError(f"{path}: {name} has no {required!r}, and it is not optional")
        zone = spec.get("timezone")
        if zone:
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ModuleNotFoundError, ValueError) as exc:
                raise MappingError(
                    f"{path}: {name} names a time zone {zone!r} this machine does not know. "
                    "On Windows the IANA database comes from the `tzdata` package; if it is "
                    "missing, `pip install tzdata`."
                ) from exc
        unknown = set(spec["columns"]) - set(EVENT_TYPES[name].model_fields)
        if unknown:
            raise MappingError(
                f"{path}: {name} maps {sorted(unknown)}, which the {EVENT_TYPES[name].__name__} contract "
                f"does not have. Its fields are {sorted(EVENT_TYPES[name].model_fields)}."
            )
        mappings[name] = StreamMapping(
            name=name,
            source=str(spec["source"]),
            source_kind=str(spec["source_kind"]),
            columns={k: str(v) for k, v in spec["columns"].items()},
            defaults=dict(spec.get("defaults") or {}),
            time_format=spec.get("time_format"),
            timezone=zone,
        )
    return mappings


# ------------------------------------------------------------------- parsing


_TIME_FIELDS = frozenset({"recorded_at", "started_at", "ended_at", "when"})
_BOOL_TRUE = {"1", "true", "t", "yes", "y", "pass", "ok"}
_BOOL_FALSE = {"0", "false", "f", "no", "n", "fail"}


def _parse_time(text: str, mapping: StreamMapping, field_name: str) -> datetime:
    if mapping.time_format:
        try:
            parsed = datetime.strptime(text, mapping.time_format)
        except ValueError as exc:
            raise ValueError(f"{field_name} {text!r} does not match the configured "
                             f"time format {mapping.time_format!r}") from exc
    else:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} {text!r} is not an ISO 8601 timestamp, and the mapping "
                             "sets no time_format") from exc
    if parsed.tzinfo is not None:
        return parsed
    if not mapping.timezone:
        raise ValueError(
            f"{field_name} {text!r} carries no time zone and the {mapping.name} mapping does not say "
            "which zone this system writes in; reading it as UTC could move it by hours"
        )
    return parsed.replace(tzinfo=ZoneInfo(mapping.timezone))


def _parse_bool(text: str, field_name: str) -> bool:
    lowered = text.strip().lower()
    if lowered in _BOOL_TRUE:
        return True
    if lowered in _BOOL_FALSE:
        return False
    raise ValueError(f"{field_name} {text!r} is neither a yes nor a no; it is left unanswered rather than guessed")


def to_event(row: dict, mapping: StreamMapping):
    """One row of a plant's file as one contract event, or ValueError."""
    model = mapping.model
    payload: dict[str, object] = {"source": mapping.source, "source_kind": mapping.source_kind}
    payload.update(mapping.defaults)
    for field_name, column in mapping.columns.items():
        if column not in row:
            raise ValueError(f"the file has no column {column!r}, mapped to {field_name}")
        raw = row[column]
        if raw is None:
            continue
        text = str(raw).strip()
        if text == "":
            # Absent, not zero and not "now". A field the contract requires
            # comes back as a validation error naming it.
            continue
        if field_name in _TIME_FIELDS:
            payload[field_name] = _parse_time(text, mapping, field_name)
        elif field_name == "passed":
            payload[field_name] = _parse_bool(text, field_name)
        elif model.model_fields[field_name].annotation in (float, float | None):
            try:
                payload[field_name] = float(text)
            except ValueError as exc:
                raise ValueError(f"{field_name} {text!r} is not a number") from exc
        else:
            payload[field_name] = text
    try:
        return model(**payload)
    except ValidationError as exc:
        raise ValueError(_first_reason(exc)) from exc


def _first_reason(exc: ValidationError) -> str:
    """One sentence, not a traceback: the first thing wrong with the row."""
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first["loc"]) or "the row"
    message = first["msg"].removeprefix("Value error, ")
    if first["type"] == "missing":
        return f"{where} is missing, and the contract requires it"
    return f"{where}: {message}"


def read_rows(path: Path) -> list[dict]:
    """The rows of a CSV or JSON file, in file order."""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        rows = payload if isinstance(payload, list) else [payload]
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("a JSON file should hold one object, or a list of objects, one per event")
        return rows
    return list(csv.DictReader(text.splitlines()))


# ------------------------------------------------------------------ the run


@dataclass(frozen=True)
class Folders:
    """Where a stream's files arrive and where they go afterwards."""

    inbox: Path
    processed: Path
    rejected: Path

    def make(self) -> None:
        for folder in (self.inbox, self.processed, self.rejected):
            folder.mkdir(parents=True, exist_ok=True)


def folders_for(root: Path, stream: str) -> Folders:
    root = Path(root)
    return Folders(inbox=root / stream, processed=root / "processed" / stream,
                   rejected=root / "rejected" / stream)


def _move(path: Path, into: Path) -> Path:
    """Move a file, never overwriting one already there."""
    into.mkdir(parents=True, exist_ok=True)
    target = into / path.name
    if target.exists():
        stamp = utcnow().strftime("%Y%m%dT%H%M%S")
        target = into / f"{path.stem}.{stamp}{path.suffix}"
        counter = 1
        while target.exists():
            target = into / f"{path.stem}.{stamp}-{counter}{path.suffix}"
            counter += 1
    path.rename(target)
    return target


def _write_rejects(report: FileReport, folders: Folders) -> None:
    """The rejects report: totals first, then one line per rejected row."""
    folders.rejected.mkdir(parents=True, exist_ok=True)
    target = folders.rejected / f"{Path(report.path).name}.rejects.txt"
    lines = [
        f"{Path(report.path).name}",
        f"read {report.rows}, recorded {report.applied}, already seen {report.duplicates}, "
        f"rejected {report.rejected}",
        "",
    ]
    lines += [f"row {r.row}: {r.reason}" for r in report.rejections]
    lines.append("")
    lines.append("Nothing above was recorded. The input file is kept; fix the rows or the mapping "
                 "and drop it in again — anything already recorded will not be recorded twice.")
    target.write_text("\n".join(lines), encoding="utf-8")


def process_file(session_scope, path: Path, mapping: StreamMapping, folders: Folders) -> FileReport:
    """Read one file, write every row it can, and move it. Never deletes."""
    report = FileReport(path=str(path))
    try:
        rows = read_rows(path)
    except Exception as exc:
        report.rejections.append(Rejection(row=0, reason=f"the file could not be read: {exc}"))
        _write_rejects(report, folders)
        report.moved_to = str(_move(path, folders.rejected))
        return report

    report.rows = len(rows)
    write = inbound_service.WRITERS[mapping.name]
    with session_scope() as session:
        for number, row in enumerate(rows, start=1):
            savepoint = session.begin_nested()
            try:
                event = to_event(row, mapping)
                outcome = write(session, event)
            except (ValueError, inbound_service.Refused) as exc:
                savepoint.rollback()
                report.rejections.append(Rejection(row=number, reason=str(exc)))
                continue
            except Exception as exc:  # one bad row must not stall the interface
                savepoint.rollback()
                log.error("inbound row failed", file=path.name, row=number, error=str(exc))
                report.rejections.append(Rejection(row=number, reason=f"{type(exc).__name__}: {exc}"))
                continue
            savepoint.commit()
            if outcome.duplicate:
                report.duplicates += 1
            else:
                report.applied += 1

    # The move happens after the transaction closes. A crash in between
    # leaves the file where it is, it is read again, and every row is a
    # duplicate the second time - which is the whole reason the ledger
    # keys on the supplier's own id.
    if report.rejections:
        _write_rejects(report, folders)
    everything_rejected = report.rows == 0 or report.rejected == report.rows
    report.moved_to = str(_move(path, folders.rejected if everything_rejected else folders.processed))
    return report


def run_once(session_scope, root: Path, mappings: dict[str, StreamMapping]) -> RunReport:
    """One pass over every configured inbox, oldest file first."""
    run = RunReport()
    for stream, mapping in sorted(mappings.items()):
        folders = folders_for(root, stream)
        folders.make()
        for path in sorted(folders.inbox.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in READABLE:
                run.skipped += 1
                continue
            run.files.append(process_file(session_scope, path, mapping, folders))
    return run
