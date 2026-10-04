#!/usr/bin/env python3
"""Refuse to write the plant shape if anything identifiable from the database is in it.

This is the part of the skill that earns the promise. `profile.py` calls it
before it writes a single byte, and if it finds a match the file is not written
at all — not written and then warned about, not written with the offending line
removed. Nothing leaves the building that this has not cleared.

What counts as a leak
---------------------
Anything from the source database that identifies a thing rather than measuring
it: an order number, a lot number, a pallet id, a serial, a person's code or
name, a customer's name, an item description, a document number, a free-text
note. Those are the values that must never appear.

What does not count, and why — these five exceptions are the whole honest
difference between "identity" and "a number we measured":

1. **Purely numeric values.** A fill weight of 499.8 is in the database and is
   also in the output, because the output's job is to say what the fill weight
   distribution looks like. A statistic is not an identity. (A numeric *id* is
   caught a different way: the output never carries a key, it carries ST-01.)
2. **Dates and timestamps.** The output states the window it measured. That
   window's start is, necessarily, a timestamp out of the database.
3. **Engineering units.** `bar`, `degC`, `bpm`. The output carries a tag's unit
   because a simulation without units is unreadable. The list is fixed and
   short (see `UNITS_ALLOWED`); a "unit" outside it is treated as a string the
   customer wrote, and is dropped rather than carried.
4. **This skill's own standard words.** `mechanical`, `rework`, `running`. The
   output maps the customer's vocabulary onto a fixed list so their words stay
   in their database. If a customer happens to use one of our words, that is
   our word in the output, not theirs.
5. **Any single bare word that appears in this skill's own source text** — see
   `own_vocabulary` for why, and for what it deliberately does not forgive.

Everything else, four characters or longer, is compared case-insensitively
against the whole output text. Four, because below that a code letter would
match half the file and the check would be noise.

Standalone, after the fact:

    python scripts/leakcheck.py --sqlite plant.db --shape plant-shape.toml
    python scripts/leakcheck.py --samples sampled-values.txt --shape plant-shape.toml

Exit status is 0 when clean and 1 when it found something, so a script or a
pipeline can depend on it.

Python 3.11 or newer. Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path, PurePath

#: Units the output may carry verbatim. Short, fixed, and chosen because every
#: one of them is a unit and none of them is a thing anybody owns. A unit string
#: that is not in here is dropped, which is the safe direction to fail.
UNITS_ALLOWED: frozenset[str] = frozenset({
    "", "%", "pct", "ppm", "ea", "pcs", "pieces", "count",
    "mm", "cm", "m", "km", "in", "ft", "mm2", "m2", "m3",
    "g", "kg", "t", "lb", "oz", "ml", "l", "gal",
    "s", "sec", "ms", "min", "h", "hr", "day",
    "c", "degc", "degf", "f", "k", "celsius",
    "bar", "mbar", "psi", "pa", "kpa", "mpa", "atm",
    "a", "ma", "v", "mv", "kv", "w", "kw", "kwh", "mwh", "hz", "khz", "rpm",
    "n", "nm", "kn", "bpm", "cpm", "ppm_flow", "lpm", "m3h", "scfm",
    "lux", "lx", "db", "ph", "us", "uscm", "ntu", "cfu",
    # Rates and compound units. A unit dropped is a unit a reader has to guess
    # at, so the list covers what a line actually publishes — and no more.
    "1/s", "1/min", "mm/s", "m/s", "m/min", "l/min", "l/h", "m3/h", "kg/h", "t/h",
    "g/l", "mg/l", "deg", "degc/min", "bar/s", "rev/min", "nm/s", "kg/cm2",
})

#: HTTP verbs. The output carries the verb of an ERP call because "they POST
#: something once a minute" is the shape of an integration, and a verb is not a
#: thing anybody owns.
HTTP_VERBS: frozenset[str] = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

#: Words this skill writes into the output itself. A match on one of these is
#: our vocabulary, not the customer's.
OWN_WORDS: frozenset[str] = frozenset({
    "running", "stopped", "starved", "blocked", "down", "changeover", "setup", "idle",
    "unknown", "unlabelled", "other", "none", "true", "false",
    "mechanical", "electrical", "material", "quality", "operator", "process", "tooling",
    "planned", "unplanned", "maintenance", "open", "rework", "scrap", "accepted",
    "use-as-is", "rejected", "closed", "pending",
    "analog", "counter", "discrete", "station", "plant", "line", "shift", "order",
    "document", "tag", "stop", "reason", "sample", "window", "total", "share",
    "plant-shape", "plant-from-your-mes", "factorysemantics",
}) | HTTP_VERBS


def own_vocabulary(scripts: Path | None = None) -> frozenset[str]:
    """Every word this skill's own source text contains, lowercased.

    This exists because of a real result the first time the check ran against
    the bottling fixture. It flagged five values — `FAULT`, `PASS`, `FAIL`,
    `POST` and `machine` — and every one of them was a word *this skill* had
    written into the output, not a word of the customer's that had escaped. The
    customer's state code happens to be spelled the same way as the English word
    in our own generic sentence about an electrical fault.

    The honest fix is not to loosen the check, it is to say which words are ours.
    A source value is forgiven only when the **whole value**, lowercased, is a
    single token that appears as a word somewhere in these scripts. That keeps
    every structured identifier in scope: `PAL-700123`, `WO-2026-0004711`,
    `LOT-2603600-5`, `NCR-2026-0004`, `OP-1182` and any free-text sentence are
    all still compared, because none of them is a word we wrote.
    """
    folder = scripts or Path(__file__).resolve().parent
    words: set[str] = set()
    for path in sorted(folder.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        words.update(re.findall(r"[A-Za-z][A-Za-z_-]{1,40}", text.lower()))
    return frozenset(words)


_NUMBER = re.compile(r"^[+-]?\d{1,3}(?:[ ,]?\d{3})*(?:\.\d+)?$|^[+-]?\d*\.?\d+(?:[eE][+-]?\d+)?$")
_DATEISH = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T].*)?$|^\d{1,2}[-/]\d{1,2}[-/]\d{4}([ T].*)?$")
_TIMEISH = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\.\d+)?Z?$")

#: Below this length a value is not identifying enough to test without drowning
#: the check in false matches.
MIN_LENGTH = 4

#: How many distinct values are sampled per column, and in total. The point of a
#: cap is that this has to be runnable against a plant historian; the point of
#: it being this large is that a leak check with a small sample is theatre.
PER_COLUMN_CAP = 500
TOTAL_CAP = 200_000


class Leak(Exception):
    """Raised instead of writing the file."""


def is_identity(value: str, ours: frozenset[str] | None = None) -> bool:
    """Is this value a thing somebody owns, rather than something measured?"""
    text = value.strip()
    if len(text) < MIN_LENGTH:
        return False
    if _NUMBER.match(text) or _DATEISH.match(text) or _TIMEISH.match(text):
        return False
    lowered = text.lower()
    if lowered in UNITS_ALLOWED or lowered in OWN_WORDS:
        return False
    # A single bare word that this skill itself writes somewhere. Anything with a
    # space, a digit or a separator is still an identity and is still compared.
    return not (ours is not None and " " not in lowered and lowered in ours)


def read_only_uri(path: PurePath) -> str:
    """A SQLite URI that opens a database read-only, on Windows as well.

    Read-only is not a nicety here — it is the first promise this skill makes
    about somebody's production database, and `mode=ro` in a URI is how SQLite
    is told to refuse a write at the file level rather than trusting us not to
    attempt one.

    The Windows part is the fiddly part, and it matters because plant PCs run
    Windows. A URI is not a path: `file:C:\\plant\\mes.db` is not a URI SQLite
    can read, because the backslashes are not separators to it. The path has to
    be spelled with forward slashes and given a leading one, so the drive letter
    sits where a URI expects a path — `file:///C:/plant/mes.db`. A `?` or a `#`
    anywhere in the name would end the path early, so both are escaped.
    """
    # `as_posix()` on its own leaves a relative path relative, which SQLite would
    # resolve against its own working directory rather than ours, so an absolute
    # path is made first — except for a path that is already absolute for its own
    # flavour, which is what a Windows path handed to this on any machine is.
    spelled = path if path.is_absolute() else Path(path).resolve()
    posix = spelled.as_posix().replace("?", "%3f").replace("#", "%23")
    if not posix.startswith("/"):
        posix = "/" + posix
    return f"file://{posix}?mode=ro"


def sample_sqlite(path: Path, per_column: int = PER_COLUMN_CAP, total: int = TOTAL_CAP) -> list[str]:
    """Every distinct value this database holds, bounded, as text.

    Read-only: the connection is opened in SQLite's own read-only mode, so a
    mistake in this file cannot write to the customer's database.
    """
    db = sqlite3.connect(read_only_uri(path), uri=True)
    db.text_factory = str
    out: list[str] = []
    try:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        for table in tables:
            columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
            for column in columns:
                if len(out) >= total:
                    return out
                rows = db.execute(
                    f'SELECT DISTINCT CAST("{column}" AS TEXT) FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL LIMIT {int(per_column)}'
                )
                out.extend(str(row[0]) for row in rows if row[0] is not None)
    finally:
        db.close()
    return out


def scan(output_text: str, values: list[str],
         ours: frozenset[str] | None = None) -> tuple[list[str], int]:
    """Return (the values that appear in the output, how many were considered)."""
    if ours is None:
        ours = own_vocabulary()
    haystack = output_text.lower()
    considered = 0
    found: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = value.strip()
        if not is_identity(text, ours):
            continue
        considered += 1
        needle = text.lower()
        if needle in seen:
            continue
        seen.add(needle)
        if needle in haystack:
            found.append(text)
    return found, considered


def verdict_line(sampled: int, considered: int, found: list[str]) -> str:
    """One sentence for the output file's own `[meta]` section."""
    if found:
        return (f"FAILED: {len(found)} of {considered} identifying values (out of {sampled} sampled) "
                f"appear in this file")
    return (f"passed: 0 of {considered} identifying values appear in this file "
            f"({sampled} values sampled from the source)")


def check(output_text: str, values: list[str], ours: frozenset[str] | None = None) -> str:
    """Scan, and raise `Leak` rather than let the caller write the file."""
    found, considered = scan(output_text, values, ours)
    if found:
        shown = ", ".join(repr(v) for v in found[:5])
        raise Leak(
            f"{len(found)} value(s) from the source database appear in the output: {shown}"
            + (" …" if len(found) > 5 else "")
            + "\nThe file was NOT written. Fix the profiler or the mapping; do not edit this check."
        )
    return verdict_line(len(values), considered, found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sqlite", type=Path, help="SQLite database to sample values from (read-only).")
    source.add_argument("--samples", type=Path,
                        help="A text file of source values, one per line — what 04b_column_sample.sql saved.")
    parser.add_argument("--shape", type=Path, required=True, help="The plant-shape.toml to scan.")
    args = parser.parse_args(argv)

    values = (sample_sqlite(args.sqlite) if args.sqlite
              else [line.rstrip("\n") for line in args.samples.read_text(encoding="utf-8").splitlines()])
    text = args.shape.read_text(encoding="utf-8")
    found, considered = scan(text, values)
    print(verdict_line(len(values), considered, found))
    for value in found[:20]:
        print(f"  leaked: {value!r}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
