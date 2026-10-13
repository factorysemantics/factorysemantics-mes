"""Reading a `plant-shape.toml`, and refusing one this release cannot read.

The shape file is written on the customer's own MES box by the
`plant-from-your-mes` skill and is the *only* thing that travels. Nothing in
this module opens a database, a socket or a credential store: it reads one
TOML file and hands back what it says.

Two formats exist.

**Format 1** is what `plant-from-your-mes 1` writes: one straight line of
stations in `sequence` order, one OPC node per machine, no hierarchy and no
routings. Every format-1 file keeps working, and is read here as the
degenerate case of format 2 - one area, one line, one node per station.

**Format 2** adds the three things a plant that is not one straight line has
to be able to say: which area and cell a station sits in (`[hierarchy]`),
what feeds what (`[routings]`), and how the tags are organised above the
machine (`[tag_nodes]`). Each section carries `found`, and `found = false`
means *the source MES had no table for this*, which is not the same as "it is
one straight line" - the difference is written into the generated plant so
nobody has to guess later which it was.

A format number this release does not know is refused rather than read
hopefully: a newer skill may have moved a field, and a converter that guesses
at a file from the future produces a plant that silently is not the
customer's.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

#: The newest shape format this release understands.
HIGHEST = 2

#: Where the format number lives. Absent means format 1: the first skill
#: never wrote one, and every file it wrote is a valid format-1 file.
FORMAT_KEY = "shape_format"


class NotAShape(Exception):
    """The file is not a plant shape this release can convert."""


@dataclass(frozen=True)
class Shape:
    """One `plant-shape.toml`, parsed and nothing more.

    Deliberately thin. The judgements - what a missing section means, what to
    do with a share that does not add up - belong to `model.from_shape`, where
    they can be stated once and listed in the generated plant. This only says
    what the file holds.
    """

    path: Path
    format: int
    raw: dict

    def table(self, name: str) -> dict:
        """One `[section]`, or an empty one. Never `None`."""
        value = self.raw.get(name)
        return value if isinstance(value, dict) else {}

    def rows(self, section: str, key: str | None = None) -> list[dict]:
        """A list of tables, either `[[section]]` or `[section] key = [...]`.

        The skill writes both shapes: stations are `[[stations]]`, while the
        stop mix is a list inside `[stops]`. One accessor for both keeps the
        caller from caring which the skill chose.
        """
        if key is None:
            value = self.raw.get(section)
        else:
            value = self.table(section).get(key)
        if not isinstance(value, list):
            return []
        return [row for row in value if isinstance(row, dict)]

    def found(self, section: str) -> bool:
        """Did the source MES have anything to say about this section?

        A section the skill did not write at all, and a section it wrote with
        `found = false`, are both "nothing was measured here". Unknown is not
        zero: the caller turns this into a stated assumption, not a default.
        """
        return bool(self.table(section).get("found"))


def read_shape(path: str | Path) -> Shape:
    """Read one shape file, or say plainly why it cannot be read."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NotAShape(f"there is no shape file at {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise NotAShape(f"{path} is not valid TOML: {exc}") from exc
    return of(raw, path=path)


def of(raw: dict, *, path: Path | None = None) -> Shape:
    """The same checks, for a shape already in memory (tests, and `--stdin`)."""
    where = path or Path("plant-shape.toml")
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    number = meta.get(FORMAT_KEY, 1)
    try:
        number = int(number)
    except (TypeError, ValueError) as exc:
        raise NotAShape(
            f"{where} says [meta] {FORMAT_KEY} = {number!r}, which is not a format number"
        ) from exc
    if number < 1:
        raise NotAShape(f"{where} says it is shape format {number}; formats start at 1")
    if number > HIGHEST:
        raise NotAShape(
            f"{where} is shape format {number} and this release reads up to {HIGHEST}. "
            "It was written by a newer plant-from-your-mes than this one: upgrade "
            "factorysemantics-mes, or re-run the skill from this release's own copy "
            "(fsmes skills unpack plant-from-your-mes)."
        )
    stations = [row for row in (raw.get("stations") or []) if isinstance(row, dict)]
    if not stations:
        raise NotAShape(
            f"{where} has no [[stations]]. A plant with no machines in it is not a "
            "shape this can build - check the skill's own output for the line that "
            "says how many stations it found."
        )
    return Shape(path=where, format=number, raw=raw)
