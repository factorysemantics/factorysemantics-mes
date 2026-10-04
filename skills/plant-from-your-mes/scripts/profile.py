#!/usr/bin/env python3
"""Turn read-only query results into one file: plant-shape.toml.

What this does
--------------
It runs (or reads the saved results of) the numbered queries in `queries/`, and
writes a single TOML file describing the *shape* of a plant: how many stations,
how fast they run, how much they scrap, what every tag looks like
statistically, how stops are distributed and categorised, what the quality loop
does, how big the orders are, the shift pattern, the numbering grammar of the
documents, the ERP traffic as a shape, and a list of things the MES tracks that
this file cannot carry.

What it never does
------------------
Copy an identity. No order number, lot, pallet, serial, person, customer, item
description, document number or free-text note is written. Stations come out as
ST-01…, tags as AN-01…, vocabularies mapped onto a fixed standard list, strings
as patterns (`AAA-AA-###`) derived from values that are then thrown away.
`leakcheck.py` is run against the finished text before anything is written, and
if it finds one of the source's own identifying strings in there, the file is
not written at all.

Two ways in
-----------
    # SQLite, straight through (this is what CI proves against bottling)
    python scripts/profile.py --sqlite plant.db --mapping mapping.toml --out plant-shape.toml

    # SQL Server and everything else: the agent runs the queries in the
    # customer's own client, saves each result as CSV, and points this at them
    python scripts/profile.py --csv-dir results/ --dialect mssql --mapping mapping.toml \
        --out plant-shape.toml

    # And before either: a draft mapping, from the catalogue alone
    python scripts/profile.py --sqlite plant.db --propose-mapping mapping.toml
    python scripts/profile.py --csv-dir results/ --dialect mssql --propose-mapping mapping.toml

    # And what to run by hand on a database this cannot reach
    python scripts/profile.py --mapping mapping.toml --dialect mssql --emit-sql ./to-run

The proposed mapping is a *draft*. It guesses from table and column names and
from nothing else, and it is wrong often enough that the instructions tell the
agent to read it back to the person before using it. A wrong mapping does not
leak anything — it produces a plant shape that is not their plant, which they
will notice when `explain.py` reads it back to them.

Python 3.11 or newer. Standard library only — no driver, no dependency, nothing
to install. The SQLite connection is opened read-only.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import re
import sqlite3
import statistics
import sys
import tomllib
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import leakcheck  # same folder, deliberately: this ships as a folder, not as a package

VERSION = "1"
HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent

# ---------------------------------------------------------------- vocabulary
#
# The customer's words stay in the customer's database. Each of these maps their
# wording onto a fixed list, longest and most specific pattern first, and
# anything that matches nothing becomes "other" — never a guess, and never their
# string passed through.

STATE_WORDS: tuple[tuple[str, str], ...] = (
    ("wait-in", "starved"), ("wait_in", "starved"), ("waitin", "starved"),
    ("wait-out", "blocked"), ("wait_out", "blocked"), ("waitout", "blocked"),
    ("starv", "starved"), ("hungry", "starved"), ("no-feed", "starved"),
    ("block", "blocked"), ("full", "blocked"), ("backed", "blocked"),
    ("fault", "down"), ("down", "down"), ("break", "down"), ("fail", "down"), ("alarm", "down"),
    ("setup", "changeover"), ("chang", "changeover"), ("c/o", "changeover"), ("rig", "changeover"),
    ("maint", "maintenance"), ("service", "maintenance"), ("pm", "maintenance"),
    ("run", "running"), ("prod", "running"), ("auto", "running"),
    ("stop", "stopped"), ("halt", "stopped"), ("idle", "stopped"), ("off", "stopped"),
)

STOP_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("chang", "changeover"), ("setup", "changeover"), ("clean", "changeover"), ("cip", "changeover"),
    ("maint", "maintenance"), ("planned", "maintenance"), ("service", "maintenance"),
    ("tool", "tooling"), ("die", "tooling"), ("mould", "tooling"), ("mold", "tooling"),
    ("blade", "tooling"), ("knife", "tooling"),
    ("mech", "mechanical"), ("jam", "mechanical"), ("wear", "mechanical"), ("bearing", "mechanical"),
    ("belt", "mechanical"), ("chain", "mechanical"), ("seal", "mechanical"),
    ("elec", "electrical"), ("drive", "electrical"), ("motor", "electrical"), ("vfd", "electrical"),
    ("sensor", "electrical"), ("plc", "electrical"), ("power", "electrical"), ("control", "electrical"),
    ("mat", "material"), ("supply", "material"), ("component", "material"), ("infeed", "material"),
    ("preform", "material"), ("feed", "material"), ("starv", "material"),
    ("qual", "quality"), ("qa", "quality"), ("spec", "quality"), ("reject", "quality"), ("lab", "quality"),
    ("oper", "operator"), ("staff", "operator"), ("crew", "operator"), ("break", "operator"),
    ("train", "operator"), ("manning", "operator"),
    ("process", "process"), ("recipe", "process"), ("temp", "process"),
)

DISPOSITIONS: tuple[tuple[str, str], ...] = (
    ("use as is", "use-as-is"), ("use-as-is", "use-as-is"), ("useasis", "use-as-is"),
    ("concession", "use-as-is"), ("accept", "accepted"),
    ("rework", "rework"), ("repair", "rework"), ("sort", "rework"),
    ("scrap", "scrap"), ("discard", "scrap"), ("destroy", "scrap"), ("reject", "rejected"),
    ("open", "open"), ("pending", "open"), ("new", "open"), ("review", "open"),
    ("clos", "closed"), ("complete", "closed"), ("done", "closed"),
)

VERDICTS: tuple[tuple[str, str], ...] = (
    ("fail", "fail"), ("nok", "fail"), ("reject", "fail"), ("bad", "fail"), ("out", "fail"),
    ("pass", "pass"), ("ok", "pass"), ("good", "pass"), ("accept", "pass"), ("in spec", "pass"),
)

#: The states a plant records a reason against. Being starved or blocked is not
#: a fault and not anybody's reason code — it is the machine upstream or the one
#: downstream — so counting them as stops nobody labelled overstates the
#: unlabelled share badly. On the bottling fixture it was the difference between
#: 79% unlabelled and 20%, and 79% would have been a wrong number that read as a
#: finding. They are still reported in full, under `states`.
REASON_BEARING: tuple[str, ...] = ("stopped", "down", "changeover", "maintenance", "other")
WAITING: tuple[str, ...] = ("starved", "blocked")

#: One generic sentence per stop category, written here, trimmed or padded to the
#: median length of the customer's own notes. Scott asked for "similar sounding
#: explanations for down times": this is how that is done without carrying one
#: word of theirs.
GENERIC_WORDING: dict[str, str] = {
    "mechanical": "Mechanical stop on the machine, part replaced and the line restarted by the shift",
    "electrical": "Electrical fault on the drive, reset at the panel and the machine put back to auto",
    "material": "Nothing arriving at the infeed, the line waited for material from the warehouse",
    "quality": "Held for a quality check, the line waited for the result before it ran again",
    "operator": "Nobody on the machine for the period, the station stood while the crew was away",
    "changeover": "Planned changeover to the next product, set up and proved out before running",
    "maintenance": "Planned maintenance on the machine, done on the stop and signed off after",
    "tooling": "Tooling changed on the machine, the worn set swapped out and the line restarted",
    "process": "Process ran outside its window, corrected and the machine returned to normal running",
    "other": "Stop recorded against a reason this file does not carry, kept as a category only",
    "unlabelled": "Stop with no reason recorded against it in the source system at all",
}


def _map_word(value: Any, table: tuple[tuple[str, str], ...], fallback: str = "other") -> str:
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if not text:
        return fallback
    for needle, standard in table:
        if needle in text:
            return standard
    return fallback


# ------------------------------------------------------------------ numbers
def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    number = _f(value)
    return None if number is None else round(number)


def _percentile(values: list[float], fraction: float) -> float | None:
    """The value at `fraction` through the sorted list. Nearest rank, no
    interpolation: a stop duration is a measurement, not a smooth curve."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _round(value: float | None, places: int = 3) -> float | None:
    return None if value is None else round(value, places)


def _parse_ts(value: Any) -> float | None:
    """Seconds, from whatever the customer's column holds."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    candidate = text.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return _f(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat().replace("+00:00", "Z")


def _decimals(values: list[str]) -> int:
    best = 0
    for text in values[:500]:
        if "." in text:
            best = max(best, min(6, len(text.split(".", 1)[1].rstrip())))
    return best


# ----------------------------------------------------------------- patterns
def pattern_of(value: str) -> str:
    """`WO-2026-0004711` -> `AA-####-#######`. A shape, never an instance."""
    out: list[str] = []
    for char in str(value):
        if char.isdigit():
            out.append("#")
        elif char.isalpha():
            out.append("A" if char.isupper() else "a")
        else:
            out.append(char)
    return "".join(out)


def grammar_of(what: str, values: list[str], top: int = 4) -> list[dict[str, Any]]:
    """The numbering grammar of a column: its patterns and how many of each.

    Every list states its total, so `of_total` is on each row.
    """
    counts = Counter(pattern_of(v) for v in values if str(v).strip())
    total = sum(counts.values())
    rows = []
    for pattern, seen in counts.most_common(top):
        rows.append({"what": what, "pattern": pattern, "seen": seen, "of_total": total})
    if len(counts) > top:
        rows.append({"what": what, "pattern": f"<{len(counts) - top} further patterns>",
                     "seen": total - sum(r["seen"] for r in rows), "of_total": total})
    return rows


def path_shape(value: str) -> str:
    """An ERP endpoint as a shape: `/Aaa/Aaaaaaaa/AA-####-####/Aaaaaaa`.

    The endpoint's own words are the customer's integration, so they are not
    carried. What is carried is enough to say "they post something with four
    path segments, one of which is an id, about once a minute".
    """
    text = str(value).split("?", 1)[0]
    return "/".join(pattern_of(part) for part in text.split("/"))


# --------------------------------------------------------- SQL and mapping
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]{0,126}$")
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\.?([A-Za-z_][A-Za-z0-9_]*)?(\?)?(?:@([A-Za-z_]+))?\}\}")


class Unmapped(Exception):
    """A query needs a table or column the mapping does not name."""


def quote(dialect: str, name: str) -> str:
    """Quote an identifier, after checking it *is* one.

    The check is the security boundary: every identifier in every query comes
    from the mapping file, and nothing that is not a plain identifier gets
    through. Combined with read-only connections, there is no path from a
    mapping file to a write.
    """
    if not _IDENTIFIER.match(str(name)):
        raise Unmapped(f"{name!r} is not a plain identifier — the mapping may only name tables and columns")
    return f"[{name}]" if dialect == "mssql" else f'"{name}"'


def expand(sql: str, mapping: dict[str, Any], dialect: str, row_cap: int,
           extra: dict[str, Any] | None = None) -> str:
    """Fill a query template from the mapping. Raises `Unmapped` when a required
    name is missing, so the caller can record the section as not profiled rather
    than quietly produce a zero."""
    scopes: dict[str, Any] = dict(mapping)
    if extra:
        scopes.update(extra)

    def replace(match: re.Match[str]) -> str:
        section, key, optional, alias = match.groups()
        if section == "row_cap" and key is None:
            return str(int(row_cap))
        scope = scopes.get(section)
        if not isinstance(scope, dict):
            if optional:
                return "NULL"
            raise Unmapped(f"the mapping has no [{section}] section")
        name = scope.get(key)
        if not name:
            if optional:
                return "NULL"
            raise Unmapped(f"the mapping has no {section}.{key}")
        quoted = quote(dialect, name)
        return f"{alias}.{quoted}" if alias else quoted

    return _PLACEHOLDER.sub(replace, sql)


def load_query(dialect: str, name: str, queries: Path) -> str:
    path = queries / dialect / f"{name}.sql"
    if not path.exists():
        raise Unmapped(f"no query {name}.sql for dialect {dialect}")
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ sources
class Source:
    """Where rows come from. Two implementations, one contract: `rows(name)`
    returns a list of dicts keyed by the query's own column aliases."""

    def rows(self, name: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class SqliteSource(Source):
    """Straight through a read-only SQLite connection."""

    def __init__(self, path: Path, mapping: dict[str, Any], queries: Path, row_cap: int) -> None:
        # Read-only, and spelled as a URI the way SQLite needs it on Windows
        # too — see `leakcheck.read_only_uri`. Plant PCs run Windows.
        self.db = sqlite3.connect(leakcheck.read_only_uri(path), uri=True)
        self.db.row_factory = sqlite3.Row
        self.mapping = mapping
        self.queries = queries
        self.row_cap = row_cap
        self.path = path

    def rows(self, name: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        sql = expand(load_query("sqlite", name, self.queries), self.mapping, "sqlite", self.row_cap, extra)
        return [dict(row) for row in self.db.execute(sql)]

    def close(self) -> None:
        self.db.close()


class CsvSource(Source):
    """Results the agent saved out of the customer's own client.

    One CSV per query, named after the query: `05b_tag_samples.csv`. The
    per-table and per-column templates take their arguments in the file name
    after a double underscore: `03_window__asset_state_log.csv`,
    `04b_column_sample__work_order__wo_number.csv`.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _candidates(self, name: str, extra: dict[str, Any] | None) -> list[Path]:
        if extra:
            scope = extra.get("table", {})
            parts = [scope.get(k) for k in ("table", "at", "column") if scope.get(k)]
            stem = "__".join([name, *[str(p) for p in parts]])
            return [self.directory / f"{stem}.csv"]
        return [self.directory / f"{name}.csv"]

    def rows(self, name: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        for path in self._candidates(name, extra):
            if path.exists():
                with path.open(encoding="utf-8-sig", newline="") as handle:
                    return list(csv.DictReader(handle))
        raise Unmapped(f"no saved result for {name} in {self.directory}")

    def glob(self, prefix: str) -> list[tuple[list[str], Path]]:
        out = []
        for path in sorted(self.directory.glob(f"{prefix}__*.csv")):
            out.append((path.stem.split("__")[1:], path))
        return out


# ------------------------------------------------------- mapping proposal
#: What each logical section is likely to be called, and what its columns are
#: likely to be called. Pure name-matching: it has read no data when it guesses.
SECTION_HINTS: dict[str, dict[str, Any]] = {
    "assets": {
        "tables": ("asset", "machine", "equipment", "resource", "workcenter", "work_center",
                   "station", "device", "unit", "cell"),
        "columns": {
            "id": ("id", "key", "no", "pk"),
            "code": ("code", "name", "ident", "tag", "number"),
            "line": ("line", "area", "cell", "dept", "section", "plant"),
            "sequence": ("seq", "sequence", "position", "sort", "order_no", "step"),
        },
        "required": ("id", "code"),
    },
    "tags": {
        "tables": ("tag", "signal", "point", "parameter", "variable", "item_def", "datapoint"),
        "columns": {
            "id": ("id", "key", "no"),
            "asset": ("asset", "machine", "equipment", "resource", "device", "parent", "station"),
            "path": ("path", "name", "address", "code", "tag"),
            "unit": ("unit", "uom", "eng", "dim"),
        },
        "required": ("id", "asset", "path"),
    },
    "samples": {
        "tables": ("sample", "history", "hist", "reading", "trend", "archive", "value", "datalog"),
        "columns": {
            "tag": ("tag", "point", "signal", "item", "parameter"),
            "at": ("ts", "time", "date", "stamp", "at"),
            "value": ("value", "val", "num", "measure", "reading", "float"),
        },
        "required": ("tag", "at", "value"),
    },
    "states": {
        "tables": ("state", "status", "downtime", "down_time", "availability", "mode", "event_log"),
        "columns": {
            "asset": ("asset", "machine", "equipment", "resource", "device", "station"),
            "state": ("state", "status", "mode", "word", "condition"),
            "at": ("start", "from", "begin", "ts", "at", "date"),
            "until": ("end", "until", "to", "finish", "stop_at"),
            "seconds": ("second", "sec", "dur", "elapsed", "length", "minute"),
            "reason": ("reason", "cause", "code_id", "downtime_code"),
            "note": ("note", "comment", "text", "desc", "remark", "free"),
        },
        "required": ("asset", "state", "at"),
    },
    "reasons": {
        "tables": ("reason", "cause", "downtime_code", "stop_code"),
        "columns": {
            "id": ("id", "key", "no"),
            "category": ("categ", "group", "class", "type", "kind", "family"),
            "code": ("code", "ident"),
            "text": ("text", "desc", "name", "wording"),
        },
        "required": ("id", "category"),
    },
    "production": {
        "tables": ("output", "production", "booking", "confirm", "produced", "yield", "count", "report"),
        "columns": {
            "asset": ("asset", "machine", "equipment", "resource", "device", "station"),
            "at": ("at", "ts", "time", "date", "stamp"),
            "good": ("good", "ok_qty", "accept", "produced", "yield", "qty_ok"),
            "scrap": ("scrap", "reject", "waste", "nok", "bad", "loss"),
        },
        "required": ("asset", "at", "good", "scrap"),
    },
    "quality": {
        "tables": ("qa", "quality", "inspect", "check", "test", "measurement", "result", "nonconform"),
        "columns": {
            "at": ("at", "ts", "time", "date", "stamp"),
            "verdict": ("verdict", "result", "judg", "pass", "eval", "outcome", "status"),
            "nonconformance": ("nc_", "ncr", "nonconf", "defect", "deviation"),
            "disposition": ("disp", "decision", "action", "status"),
            "characteristic": ("charact", "feature", "attribute", "param", "test", "spec"),
        },
        "required": ("at", "verdict"),
    },
    "orders": {
        "tables": ("order", "workorder", "work_order", "job", "batch", "production_order"),
        "columns": {
            "quantity": ("qty", "quantity", "amount", "planned", "target", "ordered"),
            "opened": ("release", "create", "start", "open", "plan", "issued"),
            "closed": ("clos", "finish", "complete", "end", "actual_end"),
        },
        "required": ("quantity", "opened"),
    },
    "shifts": {
        "tables": ("shift", "calendar", "crew", "roster", "pattern"),
        "columns": {
            "code": ("code", "name", "shift", "label"),
            "starts": ("start", "from", "begin"),
            "ends": ("end", "until", "to", "finish"),
        },
        "required": ("code", "starts", "ends"),
    },
    "documents": {
        "tables": ("doc", "document", "sop", "instruction", "spec", "register", "procedure"),
        "columns": {
            "number": ("number", "no", "code", "ident", "doc"),
            "revision": ("rev", "version", "ver"),
        },
        "required": ("number",),
    },
    "erp": {
        "tables": ("erp", "interface", "integration", "outbound", "api", "call", "message",
                   "transaction", "queue"),
        "columns": {
            "at": ("at", "ts", "time", "date", "stamp"),
            "verb": ("verb", "method", "op", "action"),
            "endpoint": ("endpoint", "url", "uri", "path", "service", "resource", "topic"),
            "bytes": ("byte", "size", "len", "payload"),
            "status": ("status", "http", "code", "result"),
        },
        "required": ("endpoint",),
    },
}


def _score_table(name: str, synonyms: tuple[str, ...]) -> int:
    lowered = name.lower()
    best = 0
    for synonym in synonyms:
        if lowered == synonym:
            best = max(best, 4)
        elif lowered.startswith(synonym) or lowered.endswith(synonym):
            best = max(best, 3)
        elif synonym in lowered:
            best = max(best, 2)
    return best


def _pick_column(columns: list[str], synonyms: tuple[str, ...]) -> str | None:
    for synonym in synonyms:
        for column in columns:
            if column.lower() == synonym:
                return column
    for synonym in synonyms:
        for column in columns:
            if synonym in column.lower():
                return column
    return None


def propose_mapping(catalogue: dict[str, list[str]], dialect: str) -> tuple[dict[str, Any], list[str]]:
    """A draft mapping from table and column names alone, plus the notes a
    person needs in order to correct it."""
    notes: list[str] = []
    taken: set[str] = set()
    mapping: dict[str, Any] = {}
    for section, hints in SECTION_HINTS.items():
        ranked = sorted(
            ((_score_table(table, hints["tables"]), -len(table), table) for table in catalogue),
            reverse=True,
        )
        chosen = None
        for score, _, table in ranked:
            if score == 0 or table in taken:
                continue
            columns = catalogue[table]
            found = {key: _pick_column(columns, syns) for key, syns in hints["columns"].items()}
            if all(found.get(key) for key in hints["required"]):
                chosen = (table, {k: v for k, v in found.items() if v})
                break
        if chosen is None:
            notes.append(f"[{section}]: nothing in this database looked like it. Ask the person: "
                         f"does this MES record that at all?")
            continue
        table, columns = chosen
        taken.add(table)
        mapping[section] = {"table": table, **columns}
        missing = [k for k in hints["columns"] if k not in columns]
        if missing:
            notes.append(f"[{section}] -> {table}: no column found for {', '.join(sorted(missing))}")
    unclaimed = sorted(set(catalogue) - taken)
    if unclaimed:
        notes.append(f"{len(unclaimed)} table(s) of {len(catalogue)} are not mapped and will be "
                     f"reported as 'also tracked': {', '.join(unclaimed)}")
    mapping["dialect"] = dialect
    return mapping, notes


def write_mapping(path: Path, mapping: dict[str, Any], notes: list[str]) -> None:
    lines = [
        "# A DRAFT mapping, guessed from table and column names alone.",
        "#",
        "# Read it back to the person who knows this plant before you profile with",
        "# it. Every line is a question: is that really the machine list? is that",
        "# really where tag history lives? A wrong mapping leaks nothing — it just",
        "# describes a plant that is not theirs.",
        "#",
    ]
    for note in notes:
        lines.append(f"# {note}")
    lines.append("")
    lines.append(f'dialect = "{mapping.get("dialect", "sqlite")}"')
    for section, body in mapping.items():
        if not isinstance(body, dict):
            continue
        lines.append("")
        lines.append(f"[{section}]")
        for key, value in body.items():
            lines.append(f'{key} = "{value}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------ TOML writing
def _toml_scalar(value: Any) -> str:
    if value is None:
        return '"not measured"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return '"not measured"'
        return repr(round(value, 6))
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return f'"{text}"'


def _toml_inline(row: dict[str, Any]) -> str:
    return "{ " + ", ".join(f"{k} = {_toml_scalar(v)}" for k, v in row.items()) + " }"


class Doc:
    """A tiny TOML writer. The standard library reads TOML and does not write
    it, and this file is not worth a dependency."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def comment(self, text: str) -> None:
        for line in text.splitlines():
            self.lines.append(f"# {line}" if line else "#")

    def table(self, name: str) -> None:
        self.lines.append("")
        self.lines.append(f"[{name}]")

    def array_table(self, name: str) -> None:
        self.lines.append("")
        self.lines.append(f"[[{name}]]")

    def key(self, name: str, value: Any) -> None:
        self.lines.append(f"{name} = {_toml_scalar(value)}")

    def keys(self, values: dict[str, Any]) -> None:
        for name, value in values.items():
            self.key(name, value)

    def rows(self, name: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            self.lines.append(f"{name} = []")
            return
        self.lines.append(f"{name} = [")
        for row in rows:
            self.lines.append(f"  {_toml_inline(row)},")
        self.lines.append("]")

    def text(self) -> str:
        return "\n".join(self.lines).strip() + "\n"


# ------------------------------------------------------------- the profiler
class Profiler:
    def __init__(self, source: Source, mapping: dict[str, Any], dialect: str,
                 row_cap: int, keep_vocabulary: bool) -> None:
        self.source = source
        self.mapping = mapping
        self.dialect = dialect
        self.row_cap = row_cap
        self.keep_vocabulary = keep_vocabulary
        self.skipped: list[str] = []
        self.station_of: dict[str, int] = {}
        self.stations: list[dict[str, Any]] = []
        self.window: tuple[float | None, float | None] = (None, None)
        self.rows_read = 0

    # ---- helpers
    def _rows(self, name: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rows = self.source.rows(name, extra)
        self.rows_read += len(rows)
        return rows

    def _try(self, name: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
        try:
            return self._rows(name, extra)
        except Unmapped as exc:
            self.skipped.append(f"{name}: {exc}")
            return None
        except sqlite3.Error as exc:
            self.skipped.append(f"{name}: the database refused the query ({exc})")
            return None

    def _note_window(self, seconds: float | None) -> None:
        if seconds is None:
            return
        low, high = self.window
        self.window = (seconds if low is None else min(low, seconds),
                       seconds if high is None else max(high, seconds))

    # ---- step 1 and 2: the catalogue
    def catalogue(self) -> dict[str, list[str]]:
        tables = self._try("01_inventory") or []
        columns = self._try("02_columns") or []
        out: dict[str, list[str]] = {}
        for row in tables:
            name = str(row.get("table_name") or row.get("name") or "").strip()
            if name:
                out[name] = []
        for row in columns:
            table = str(row.get("table_name") or "").strip()
            column = str(row.get("column_name") or "").strip()
            if table and column:
                out.setdefault(table, []).append(column)
        return out

    # ---- step 5: stations
    def read_stations(self) -> None:
        rows = self._try("05_assets")
        if not rows:
            return
        def sort_key(row: dict[str, Any]) -> tuple[float, str]:
            seq = _f(row.get("seq"))
            return (seq if seq is not None else float("inf"), str(row.get("asset_code") or ""))
        for index, row in enumerate(sorted(rows, key=sort_key), start=1):
            key = str(row.get("asset_key"))
            self.station_of[key] = index
            self.stations.append({
                "name": f"ST-{index:02d}",
                "sequence": index,
                "analogs": [],
                "good_total": None,
                "scrap_total": None,
                "running_seconds": None,
            })

    # ---- step 5: tags
    def read_tags(self) -> dict[int, list[dict[str, Any]]]:
        rows = self._try("05b_tag_samples")
        if rows is None:
            rows = self._try("05c_tag_samples_nostate")
        if not rows:
            return {}

        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(str(row.get("asset_key")), str(row.get("tag_path")))].append(row)

        per_station: dict[int, list[dict[str, Any]]] = defaultdict(list)
        counters: dict[int, int] = defaultdict(int)
        for (asset_key, _path), samples in sorted(grouped.items()):
            station = self.station_of.get(asset_key)
            if station is None:
                continue
            tag = self._one_tag(samples)
            if tag is None:
                continue
            counters[station] += 1
            prefix = {"analog": "AN", "counter": "CT", "discrete": "DS"}[tag["kind"]]
            tag["name"] = f"{prefix}-{counters[station]:02d}"
            per_station[station].append(tag)
        return per_station

    def _one_tag(self, samples: list[dict[str, Any]]) -> dict[str, Any] | None:
        pairs: list[tuple[float, float, str]] = []
        raw_text: list[str] = []
        for row in samples:
            at = _parse_ts(row.get("sample_at"))
            value = _f(row.get("num_value"))
            if at is None or value is None:
                continue
            self._note_window(at)
            pairs.append((at, value, _map_word(row.get("state_word"), STATE_WORDS, "unknown")))
            raw_text.append(str(row.get("num_value")))
        if len(pairs) < 3:
            return None
        pairs.sort()
        values = [p[1] for p in pairs]
        gaps = [b[0] - a[0] for a, b in itertools.pairwise(pairs) if b[0] > a[0]]
        interval = _percentile(gaps, 0.5) if gaps else None

        states = Counter(p[2] for p in pairs)
        modal = states.most_common(1)[0][0]
        in_modal = [p[1] for p in pairs if p[2] == modal]
        out_modal = [p[1] for p in pairs if p[2] != modal]

        kind = self._kind(values)
        base = statistics.median(in_modal) if in_modal else statistics.median(values)
        noise = statistics.pstdev(in_modal) if len(in_modal) > 1 else 0.0
        running_only = bool(out_modal) and abs(statistics.median(out_modal)) < max(abs(base) * 0.01, 1e-9)

        unit = str(samples[0].get("eng_unit") or "").strip()
        unit_carried = unit.lower() in leakcheck.UNITS_ALLOWED
        return {
            "kind": kind,
            "unit": unit if unit_carried else "",
            "unit_dropped": bool(unit) and not unit_carried,
            "base": _round(base, 4),
            "noise": _round(noise, 4),
            "decimals": _decimals(raw_text),
            "running_only": running_only,
            "samples": len(pairs),
            "sample_interval_s": _round(interval, 2),
            "observed_min": _round(min(values), 4),
            "observed_max": _round(max(values), 4),
            "modal_state": modal,
            "modal_state_share": _round(states[modal] / len(pairs), 3),
            "state_correlation": _round(self._correlation(pairs, modal), 3),
        }

    @staticmethod
    def _kind(values: list[float]) -> str:
        """analog, counter or discrete, from the values alone.

        Discrete is tested first and deliberately. A ready bit that happens to
        start at 0 and reach 1 inside the sampled window never falls, which is
        exactly the test a counter passes — so asking "is it a counter?" first
        files every ready bit on the line as a counter. It did, on bottling, the
        first time this ran: thirty counters and no discrete tag at all.
        """
        distinct = {round(v, 6) for v in values}
        if len(distinct) <= 4 and all(float(v).is_integer() for v in distinct):
            return "discrete"
        steps = list(itertools.pairwise(values))
        if steps:
            falls = sum(1 for a, b in steps if b < a)
            if falls <= max(1, int(0.02 * len(steps))) and max(values) > min(values):
                return "counter"
        return "analog"

    @staticmethod
    def _correlation(pairs: list[tuple[float, float, str]], modal: str) -> float | None:
        """How much this tag moves with the machine being in its usual state.

        A point-biserial correlation, which is what Pearson's r is when one side
        is a yes/no. Written out rather than imported because this script has no
        dependencies.
        """
        flags = [1.0 if p[2] == modal else 0.0 for p in pairs]
        values = [p[1] for p in pairs]
        if len(set(flags)) < 2 or len(set(values)) < 2:
            return None
        mean_f, mean_v = statistics.fmean(flags), statistics.fmean(values)
        num = sum((f - mean_f) * (v - mean_v) for f, v in zip(flags, values, strict=True))
        den = math.sqrt(sum((f - mean_f) ** 2 for f in flags) * sum((v - mean_v) ** 2 for v in values))
        return num / den if den else None

    # ---- step 6: states and stops
    def read_states(self) -> dict[str, Any]:
        rows = self._try("06_states")
        if not rows:
            return {"found": False,
                    "why": "no state or downtime log was mapped — ask the person where stops are recorded"}
        per_state: dict[str, list[float]] = defaultdict(list)
        per_station_running: dict[int, float] = defaultdict(float)
        reason_stops: Counter[str] = Counter()
        reason_seconds: Counter[str] = Counter()
        note_lengths: dict[str, list[float]] = defaultdict(list)
        unlabelled = 0
        total = 0
        for row in rows:
            seconds = _f(row.get("seconds"))
            if seconds is None:
                continue
            total += 1
            self._note_window(_parse_ts(row.get("started_at")))
            state = _map_word(row.get("state_word"), STATE_WORDS, "other")
            per_state[state].append(seconds)
            station = self.station_of.get(str(row.get("asset_key")))
            if state == "running" and station is not None:
                per_station_running[station] += seconds
            if state == "running" or state in WAITING:
                continue
            category = row.get("reason_category")
            if category is None or str(category).strip() == "":
                unlabelled += 1
                reason_stops["unlabelled"] += 1
                reason_seconds["unlabelled"] += seconds
            else:
                standard = _map_word(category, STOP_CATEGORIES, "other")
                reason_stops[standard] += 1
                reason_seconds[standard] += seconds
                length = _f(row.get("note_length"))
                if length:
                    note_lengths[standard].append(length)

        waiting_intervals = sum(len(per_state.get(w, [])) for w in WAITING)
        waiting_seconds = sum(sum(per_state.get(w, [])) for w in WAITING)
        for index, seconds in per_station_running.items():
            self.stations[index - 1]["running_seconds"] = seconds

        all_seconds = sum(sum(v) for v in per_state.values())
        states_out = []
        for state, durations in sorted(per_state.items(), key=lambda kv: -sum(kv[1])):
            states_out.append({
                "state": state,
                "intervals": len(durations),
                "seconds": _round(sum(durations), 1),
                "share": _round(sum(durations) / all_seconds if all_seconds else None, 4),
                "p50_seconds": _round(_percentile(durations, 0.5), 1),
                "p90_seconds": _round(_percentile(durations, 0.9), 1),
                "max_seconds": _round(max(durations), 1),
            })
        stop_count = sum(reason_stops.values())
        stop_seconds = sum(reason_seconds.values())
        reasons_out = []
        for category, stops in reason_stops.most_common():
            median_length = _percentile(note_lengths.get(category, []), 0.5)
            reasons_out.append({
                "category": category,
                "stops": stops,
                "share_of_stops": _round(stops / stop_count if stop_count else None, 4),
                "seconds": _round(reason_seconds[category], 1),
                "share_of_stop_seconds": _round(
                    reason_seconds[category] / stop_seconds if stop_seconds else None, 4),
                "note_length_p50": _i(median_length),
                "generic_wording": self._wording(category, median_length),
            })
        return {
            "found": True,
            "intervals_total": total,
            "seconds_total": _round(all_seconds, 1),
            "stops_total": stop_count,
            "stop_seconds_total": _round(stop_seconds, 1),
            "unlabelled_stops": unlabelled,
            "unlabelled_share": _round(unlabelled / stop_count if stop_count else None, 4),
            "stops_counted_as": ", ".join(REASON_BEARING),
            "waiting_intervals": waiting_intervals,
            "waiting_seconds": _round(waiting_seconds, 1),
            "waiting_note": ("starved and blocked are reported but not counted as stops needing a reason — "
                             "they are the machine upstream or downstream, not a reason code"),
            "vocabulary_kept": self.keep_vocabulary,
            "states": states_out,
            "reasons": reasons_out,
        }

    def _wording(self, category: str, length: float | None) -> str:
        sentence = GENERIC_WORDING.get(category, GENERIC_WORDING["other"])
        if not length:
            return sentence
        want = int(length)
        if want <= 0:
            return ""
        if len(sentence) >= want:
            cut = sentence[:want].rstrip(" ,.;-")
            return cut
        return (sentence + " " + GENERIC_WORDING["other"])[:want].rstrip(" ,.;-")

    # ---- step 6: production
    def read_production(self) -> dict[str, Any]:
        rows = self._try("06b_production")
        if not rows:
            return {"found": False,
                    "why": "no production booking table was mapped — ask the person where counts are kept"}
        booked = 0
        for row in rows:
            station = self.station_of.get(str(row.get("asset_key")))
            if station is None:
                continue
            good = _f(row.get("good_total")) or 0.0
            scrap = _f(row.get("scrap_total")) or 0.0
            self._note_window(_parse_ts(row.get("first_seen")))
            self._note_window(_parse_ts(row.get("last_seen")))
            record = self.stations[station - 1]
            record["good_total"] = int(good)
            record["scrap_total"] = int(scrap)
            record["bookings"] = _i(row.get("bookings"))
            booked += 1
        return {"found": True, "stations_with_production": booked, "stations_total": len(self.stations)}

    def finish_stations(self, tags: dict[int, list[dict[str, Any]]]) -> None:
        for station in self.stations:
            index = station["sequence"]
            station["analogs"] = tags.get(index, [])
            good = station.get("good_total")
            scrap = station.get("scrap_total")
            running = station.get("running_seconds")
            made = (good or 0) + (scrap or 0)
            if good is None:
                station["rate_per_min"] = None
                station["rate_basis"] = "no production rows for this station"
            elif running:
                station["rate_per_min"] = _round(made / (running / 60.0), 2)
                station["rate_basis"] = "good + scrap divided by minutes in the running state"
            else:
                station["rate_per_min"] = None
                station["rate_basis"] = "production found but no running time — the state log did not cover it"
            if made:
                station["scrap_pct"] = _round(100.0 * (scrap or 0) / made, 3)
            else:
                station["scrap_pct"] = None

    # ---- step 6: quality
    def read_quality(self) -> dict[str, Any]:
        rows = self._try("06c_quality")
        if not rows:
            return {"found": False,
                    "why": "no quality result table was mapped — ask the person whether checks are recorded here"}
        checks = 0
        fails = 0
        nonconformances = 0
        dispositions: Counter[str] = Counter()
        verdicts: Counter[str] = Counter()
        for row in rows:
            seen = _i(row.get("rows_seen")) or 0
            checks += seen
            verdict = _map_word(row.get("verdict"), VERDICTS, "other")
            verdicts[verdict] += seen
            if verdict == "fail":
                fails += seen
            nonconformances += _i(row.get("with_nonconformance")) or 0
            raw = row.get("disposition")
            if raw is None or str(raw).strip() == "":
                dispositions["none recorded"] += seen
            else:
                dispositions[_map_word(raw, DISPOSITIONS, "other")] += seen
        return {
            "found": True,
            "checks_total": checks,
            "fail_share": _round(fails / checks if checks else None, 4),
            "nonconformances_total": nonconformances,
            "nonconformances_per_1000_checks": _round(1000.0 * nonconformances / checks if checks else None, 2),
            "verdicts": [{"verdict": k, "checks": v, "share": _round(v / checks if checks else None, 4)}
                         for k, v in verdicts.most_common()],
            "dispositions": [{"disposition": k, "rows": v,
                              "share": _round(v / checks if checks else None, 4)}
                             for k, v in dispositions.most_common()],
        }

    # ---- step 6: orders
    def read_orders(self) -> dict[str, Any]:
        rows = self._try("06d_orders")
        if not rows:
            return {"found": False, "why": "no order table was mapped — ask the person what an order is called here"}
        quantities: list[float] = []
        hours: list[float] = []
        open_ended = 0
        for row in rows:
            quantity = _f(row.get("qty_ordered"))
            if quantity is not None:
                quantities.append(quantity)
            opened = _parse_ts(row.get("opened_at"))
            closed = _parse_ts(row.get("closed_at"))
            self._note_window(opened)
            self._note_window(closed)
            if opened is not None and closed is not None and closed >= opened:
                hours.append((closed - opened) / 3600.0)
            elif opened is not None:
                open_ended += 1
        return {
            "found": True,
            "orders_seen": len(rows),
            "bounded_by": self.row_cap,
            "qty_p50": _round(_percentile(quantities, 0.5), 1),
            "qty_p90": _round(_percentile(quantities, 0.9), 1),
            "qty_min": _round(min(quantities), 1) if quantities else None,
            "qty_max": _round(max(quantities), 1) if quantities else None,
            "open_hours_p50": _round(_percentile(hours, 0.5), 2),
            "open_hours_p90": _round(_percentile(hours, 0.9), 2),
            "still_open": open_ended,
        }

    # ---- step 6: shifts
    def read_shifts(self) -> dict[str, Any]:
        rows = self._try("06e_shifts")
        if not rows:
            return {"found": False, "why": "no shift calendar was mapped — ask the person for the shift pattern"}
        codes: Counter[str] = Counter()
        patterns: Counter[tuple[str, float]] = Counter()
        for row in rows:
            codes[str(row.get("shift_code") or "")] += 1
            starts = _parse_ts(row.get("starts_at"))
            ends = _parse_ts(row.get("ends_at"))
            if starts is None or ends is None or ends <= starts:
                continue
            clock = datetime.fromtimestamp(starts, tz=UTC).strftime("%H:%M")
            patterns[(clock, round((ends - starts) / 3600.0, 2))] += 1
        return {
            "found": True,
            "rows_seen": len(rows),
            "distinct_codes": len(codes),
            "pattern": [{"starts_at_utc": clock, "hours": hours, "seen": seen}
                        for (clock, hours), seen in sorted(patterns.items())],
        }

    # ---- step 6: documents
    def read_documents(self) -> dict[str, Any]:
        rows = self._try("06f_documents")
        if not rows:
            return {"found": False,
                    "why": "no document register was mapped — ask the person how procedures are numbered"}
        numbers = [str(row.get("doc_number") or "") for row in rows]
        revisions = [str(row.get("revision") or "") for row in rows if row.get("revision")]
        return {
            "found": True,
            "documents_seen": len(numbers),
            "grammar": grammar_of("document number", numbers, top=6),
            "revision_grammar": grammar_of("revision", revisions, top=3),
        }

    # ---- step 7: ERP
    def read_erp(self) -> dict[str, Any]:
        rows = self._try("07_erp")
        if not rows:
            return {"found": False,
                    "why": ("no call or interface log was mapped. Ask the person: how does this MES talk to the "
                            "ERP, and does it record that it did? Write their answer here by hand.")}
        shapes: dict[tuple[str, str], dict[str, Any]] = {}
        calls_total = 0
        for row in rows:
            verb = str(row.get("verb") or "").strip().upper()[:8]
            shape = path_shape(str(row.get("endpoint") or ""))
            calls = _i(row.get("calls")) or 0
            calls_total += calls
            record = shapes.setdefault((verb, shape), {
                "verb": verb if verb.isalpha() else "", "path_shape": shape,
                "calls": 0, "min_bytes": None, "max_bytes": None, "failures": 0,
            })
            record["calls"] += calls
            record["failures"] += _i(row.get("failures")) or 0
            low, high = _f(row.get("min_bytes")), _f(row.get("max_bytes"))
            if low is not None:
                record["min_bytes"] = low if record["min_bytes"] is None else min(record["min_bytes"], low)
            if high is not None:
                record["max_bytes"] = high if record["max_bytes"] is None else max(record["max_bytes"], high)
        low, high = self.window
        hours = (high - low) / 3600.0 if low is not None and high is not None and high > low else None
        out = []
        for record in sorted(shapes.values(), key=lambda r: -r["calls"]):
            record["calls_per_hour"] = _round(record["calls"] / hours if hours else None, 2)
            record["failure_share"] = _round(
                record["failures"] / record["calls"] if record["calls"] else None, 4)
            out.append(record)
        return {
            "found": True,
            "calls_total": calls_total,
            "endpoint_words_carried": False,
            "touchpoints": out,
        }

    # ---- what we found and could not use
    def read_also_tracked(self, catalogue: dict[str, list[str]]) -> list[dict[str, Any]]:
        mapped = {str(body.get("table")) for body in self.mapping.values()
                  if isinstance(body, dict) and body.get("table")}
        #: Generic engineering words. A column name is reported only when it is
        #: one of these — our word, not theirs — so Scott can read a feature
        #: list without the file carrying anybody's schema.
        words = (
            "tool", "die", "mould", "mold", "blade", "insert", "fixture",
            "cost", "price", "amount", "spend", "labour", "labor",
            "cycle", "count", "hours", "runtime", "life", "wear",
            "energy", "power", "kwh", "steam", "air", "water", "gas",
            "temperature", "pressure", "weight", "speed", "torque", "vibration",
            "operator", "crew", "skill", "training", "certificate",
            "material", "batch", "supplier", "vendor", "receipt",
            "maintenance", "repair", "spare", "part", "inspection", "calibration",
            "schedule", "plan", "forecast", "setup", "changeover",
            "alarm", "event", "audit", "signature", "comment",
        )
        out = []
        for table in sorted(set(catalogue) - mapped):
            columns = catalogue.get(table, [])
            recognised = sorted({word for word in words
                                 for column in columns if word in column.lower()})
            rows_seen = None
            try:
                counted = self._rows("01b_row_count", {"table": {"table": table}})
                rows_seen = _i(counted[0].get("row_count")) if counted else None
            except (Unmapped, sqlite3.Error, IndexError, AttributeError):
                rows_seen = None
            out.append({
                "columns_total": len(columns),
                "rows_seen": rows_seen,
                "words_recognised": ", ".join(recognised) if recognised else "none of ours",
                "note": ("Something this MES tracks that FactorySemantics does not model. "
                         "Described by shape only; no column name of theirs is carried."),
            })
        return out


# ------------------------------------------------------------------- output
def build_document(profiler: Profiler, catalogue: dict[str, list[str]], dialect: str,
                   grammar: list[dict[str, Any]], sections: dict[str, Any],
                   also: list[dict[str, Any]]) -> Doc:
    doc = Doc()
    doc.comment(
        "plant-shape.toml — the shape of a plant, and nothing that identifies it.\n"
        "\n"
        "Written by plant-from-your-mes from read-only queries. Read this before it\n"
        "leaves your building. Everything in here is a count, a share, a\n"
        "distribution or a pattern. There is no order number, lot, pallet, serial,\n"
        "person, customer, item or free-text note, and the file was checked against\n"
        "the source database's own strings before it was written — see [meta].\n"
        "\n"
        "Stations are ST-01, ST-02 … in line order. Tags are AN-/CT-/DS- by kind.\n"
        "Vocabularies (stop reasons, dispositions, states) are mapped onto a fixed\n"
        "standard list. Strings appear only as patterns: AAA-AA-### means three\n"
        "capitals, a dash, two capitals, a dash, three digits.\n"
        "\n"
        "`not measured` means exactly that. It does not mean zero."
    )
    low, high = profiler.window
    doc.table("meta")
    doc.keys({
        "written_by": f"plant-from-your-mes {VERSION}",
        "written_at": datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_dialect": dialect,
        "read_only": True,
        "window_start": _iso(low),
        "window_end": _iso(high),
        "window_hours": _round((high - low) / 3600.0, 2) if low is not None and high is not None else None,
        "rows_read": profiler.rows_read,
        "row_cap": profiler.row_cap,
        "tables_total": len(catalogue),
        "tables_mapped": len({str(b.get("table")) for b in profiler.mapping.values()
                              if isinstance(b, dict) and b.get("table")} & set(catalogue)),
        "tables_not_understood": len(also),
        "vocabulary_kept": profiler.keep_vocabulary,
        "leak_check": "pending",
    })
    doc.rows("queries_not_run", [{"query": s.split(":", 1)[0], "why": s.split(":", 1)[1].strip()}
                                 for s in profiler.skipped])

    doc.table("plant")
    doc.keys({
        "stations_total": len(profiler.stations),
        "naming_note": "the grammar below is a shape derived from values that were then discarded",
    })
    doc.rows("naming_grammar", grammar)

    for station in profiler.stations:
        doc.array_table("stations")
        doc.keys({
            "name": station["name"],
            "sequence": station["sequence"],
            "rate_per_min": station["rate_per_min"],
            "rate_basis": station["rate_basis"],
            "scrap_pct": station["scrap_pct"],
            "good_total": station.get("good_total"),
            "scrap_total": station.get("scrap_total"),
            "running_seconds": _round(station.get("running_seconds"), 1),
            "analogs_total": len(station["analogs"]),
        })
        for tag in station["analogs"]:
            doc.array_table("stations.analogs")
            doc.keys({
                "name": tag["name"], "kind": tag["kind"], "unit": tag["unit"],
                "base": tag["base"], "noise": tag["noise"], "decimals": tag["decimals"],
                "running_only": tag["running_only"], "samples": tag["samples"],
                "sample_interval_s": tag["sample_interval_s"],
                "observed_min": tag["observed_min"], "observed_max": tag["observed_max"],
                "modal_state": tag["modal_state"], "modal_state_share": tag["modal_state_share"],
                "state_correlation": tag["state_correlation"],
                "unit_dropped": tag["unit_dropped"],
            })

    stops = sections["stops"]
    doc.table("stops")
    doc.keys({k: v for k, v in stops.items() if k not in ("states", "reasons")})
    if stops.get("found"):
        doc.rows("states", stops["states"])
        doc.rows("reasons", stops["reasons"])

    quality = sections["quality"]
    doc.table("quality")
    doc.keys({k: v for k, v in quality.items() if k not in ("verdicts", "dispositions")})
    if quality.get("found"):
        doc.rows("verdicts", quality["verdicts"])
        doc.rows("dispositions", quality["dispositions"])

    doc.table("orders")
    doc.keys(sections["orders"])

    shifts = sections["shifts"]
    doc.table("shifts")
    doc.keys({k: v for k, v in shifts.items() if k != "pattern"})
    if shifts.get("found"):
        doc.rows("pattern", shifts["pattern"])

    documents = sections["documents"]
    doc.table("documents")
    doc.keys({k: v for k, v in documents.items() if k not in ("grammar", "revision_grammar")})
    if documents.get("found"):
        doc.rows("grammar", documents["grammar"])
        doc.rows("revision_grammar", documents["revision_grammar"])

    erp = sections["erp"]
    doc.table("erp")
    doc.keys({k: v for k, v in erp.items() if k != "touchpoints"})
    if erp.get("found"):
        doc.rows("touchpoints", erp["touchpoints"])

    doc.table("also_tracked_note")
    doc.keys({
        "what": "tables this MES holds that this file could not use",
        "tables_total": len(also),
        "why_it_matters": ("each one is a candidate feature for FactorySemantics. Described by shape; "
                           "the customer's own column names are not carried."),
    })
    for index, table in enumerate(also, start=1):
        doc.array_table("also_tracked")
        doc.key("name", f"UNUSED-{index:02d}")
        doc.keys(table)
    return doc


def _grammar_from_samples(profiler: Profiler, catalogue: dict[str, list[str]],
                          source: Source) -> tuple[list[dict[str, Any]], list[str]]:
    """Patterns for the identifying columns, and the values they were derived
    from — which go straight to the leak check and nowhere else."""
    wanted = [
        ("orders", "order number", ("number", "code", "ident")),
        ("production", "lot number", ("lot", "batch")),
        ("production", "pallet or container id", ("pallet", "container", "unit_id")),
        ("quality", "non-conformance number", ("nonconformance",)),
        ("documents", "document number", ("number",)),
    ]
    grammar: list[dict[str, Any]] = []
    values: list[str] = []
    for section, label, keys in wanted:
        body = profiler.mapping.get(section)
        if not isinstance(body, dict):
            continue
        table = body.get("table")
        if not table:
            continue
        columns = catalogue.get(str(table), [])
        column = None
        for key in keys:
            column = body.get(key) or _pick_column(columns, (key,))
            if column:
                break
        if not column:
            continue
        try:
            rows = source.rows("04b_column_sample",
                               {"table": {"table": table, "column": column}})
        except (Unmapped, sqlite3.Error):
            continue
        sampled = [str(row.get("value") or "") for row in rows]
        values.extend(sampled)
        grammar.extend(grammar_of(label, sampled, top=3))
    return grammar, values


def run(args: argparse.Namespace) -> int:
    queries = Path(args.queries) if args.queries else SKILL_ROOT / "queries"
    mapping: dict[str, Any] = {}
    if args.mapping and Path(args.mapping).exists():
        mapping = tomllib.loads(Path(args.mapping).read_text(encoding="utf-8"))
    dialect = args.dialect or str(mapping.get("dialect") or ("sqlite" if args.sqlite else "mssql"))

    if args.emit_sql:
        return emit_sql(Path(args.emit_sql), mapping, dialect, queries, args.row_cap)

    if args.sqlite:
        source: Source = SqliteSource(Path(args.sqlite), mapping, queries, args.row_cap)
    elif args.csv_dir:
        source = CsvSource(Path(args.csv_dir))
    else:
        print("give either --sqlite or --csv-dir (or --emit-sql with a mapping)", file=sys.stderr)
        return 2

    try:
        profiler = Profiler(source, mapping, dialect, args.row_cap, args.keep_vocabulary)
        catalogue = profiler.catalogue()

        if args.propose_mapping:
            proposed, notes = propose_mapping(catalogue, dialect)
            write_mapping(Path(args.propose_mapping), proposed, notes)
            print(f"draft mapping written to {args.propose_mapping}")
            print(f"  {len([s for s in proposed if s != 'dialect'])} of {len(SECTION_HINTS)} sections guessed, "
                  f"from {len(catalogue)} tables")
            for note in notes:
                print(f"  - {note}")
            print("Read it back to the person who knows this plant before you profile with it.")
            return 0

        if not mapping:
            print("no mapping given. Run --propose-mapping first, then read it with a person.",
                  file=sys.stderr)
            return 2

        profiler.read_stations()
        tags = profiler.read_tags()
        stops = profiler.read_states()
        profiler.read_production()
        profiler.finish_stations(tags)
        sections = {
            "stops": stops,
            "quality": profiler.read_quality(),
            "orders": profiler.read_orders(),
            "shifts": profiler.read_shifts(),
            "documents": profiler.read_documents(),
            "erp": profiler.read_erp(),
        }
        also = profiler.read_also_tracked(catalogue)
        grammar, sampled = _grammar_from_samples(profiler, catalogue, source)
        doc = build_document(profiler, catalogue, dialect, grammar, sections, also)
        text = doc.text()

        # The leak check runs on the finished text, against everything the source
        # holds — not only the columns this run happened to read.
        if args.sqlite:
            sampled = sampled + leakcheck.sample_sqlite(Path(args.sqlite))
        elif args.samples and Path(args.samples).exists():
            sampled = sampled + Path(args.samples).read_text(encoding="utf-8").splitlines()
        try:
            verdict = leakcheck.check(text, sampled)
        except leakcheck.Leak as leak:
            print(f"REFUSED: {leak}", file=sys.stderr)
            return 1
        text = text.replace('leak_check = "pending"', f"leak_check = {json.dumps(verdict)}", 1)

        out = Path(args.out)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out} — {len(profiler.stations)} stations, "
              f"{sum(len(s['analogs']) for s in profiler.stations)} tags, "
              f"{len(also)} table(s) reported as also tracked")
        print(f"  {verdict}")
        if profiler.skipped:
            print(f"  {len(profiler.skipped)} query(ies) did not run; the file says which and why")
        return 0
    finally:
        source.close()


def emit_sql(target: Path, mapping: dict[str, Any], dialect: str, queries: Path, row_cap: int) -> int:
    """Write the filled-in queries out for somebody to run by hand.

    This is the SQL Server path: there is no driver here and there should not
    be one. The agent (or the person) runs these in whatever client the plant
    already trusts, saves each result as `<name>.csv`, and comes back with
    `--csv-dir`.
    """
    target.mkdir(parents=True, exist_ok=True)
    written = templates = 0
    for path in sorted((queries / dialect).glob("*.sql")):
        raw = path.read_text(encoding="utf-8")
        try:
            sql = expand(raw, mapping, dialect, row_cap)
        except Unmapped as exc:
            # The per-table and per-column templates cannot be filled in from a
            # mapping alone — there is one per table, or one per column, and
            # which ones to run is a judgement. They are written out with their
            # placeholders intact and a line saying so, rather than dropped:
            # a query silently missing from the folder is a step silently
            # skipped.
            note = (f"-- TEMPLATE, not ready to run: {exc}.\n"
                    f"-- Replace each {{{{table.…}}}} with one table or column name, and run it once\n"
                    f"-- per table (or per column) you care about. Save each result as\n"
                    f"-- {path.stem}__<table>.csv (or {path.stem}__<table>__<column>.csv).\n")
            (target / f"{path.stem}.template.sql").write_text(note + raw, encoding="utf-8")
            templates += 1
            continue
        (target / path.name).write_text(sql, encoding="utf-8")
        written += 1
    print(f"{written} query(ies) written to {target}, ready to run; "
          f"{templates} written as templates to fill in per table or per column")
    print("Every one is SELECT-only and bounded. Save each result as <query name>.csv, "
          "then run profile.py --csv-dir.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sqlite", type=Path, help="Profile a SQLite database directly (read-only).")
    parser.add_argument("--csv-dir", type=Path, help="Profile saved query results (any dialect).")
    parser.add_argument("--mapping", type=Path, help="The mapping file: which table and column is which.")
    parser.add_argument("--out", type=Path, default=Path("plant-shape.toml"), help="Where to write the shape.")
    parser.add_argument("--queries", type=Path, help="Query folder (default: the skill's own).")
    parser.add_argument("--dialect", choices=("sqlite", "mssql"), help="Which query set to use.")
    parser.add_argument("--row-cap", type=int, default=200_000,
                        help="The bound every query carries. Lower it on a busy box.")
    parser.add_argument("--propose-mapping", type=Path, metavar="FILE",
                        help="Guess a mapping from the catalogue and write it here, then stop.")
    parser.add_argument("--emit-sql", type=Path, metavar="DIR",
                        help="Write the filled-in queries for somebody to run by hand, then stop.")
    parser.add_argument("--samples", type=Path,
                        help="A file of source values for the leak check (CSV-dir mode).")
    parser.add_argument("--keep-vocabulary", action="store_true",
                        help="Carry the customer's own reason wording instead of a standard list. "
                             "Off by default, and only ever on because the person said so.")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
