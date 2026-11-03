"""Putting machines where somebody drew them: a positions file into `line_layout.json`.

A plant engineer draws the floor in a CAD program and exports one small JSON
file - the *positions file* - that says where each machine stands. This module
reads that file and writes each machine's place into the plant's layout file,
the one the 3D line view reads. Nothing else in the layout changes: a machine's
`kind`, its `name`, the line's defaults, other lines, the order of the keys.

The positions file, as documented in `docs/operate/packs.md`:

    {"units": "m",
     "line": "LINE1",
     "frame": {"x": "FreeCAD X", "z": "-FreeCAD Y", "y": "up", "front": "-Z",
               "rot": "degrees about vertical, CCW from above"},
     "source": "floor.FCStd",
     "cells": [{"code": "FILL01", "x": 0.0, "z": 0.0, "rot": 0.0, "model": "FILL01.glb"}]}

The numbers are already in the line view's floor coordinates - x along the
line, z across it, metres, rotation in degrees counter-clockwise seen from
above - so they are read as given and never flipped. The `frame` block is the
exporter saying so, and a file whose frame says anything else is refused
rather than guessed at: a mirrored plant looks right until somebody walks it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

#: The one frame this reader accepts, word for word as the exporter writes it.
#: The axis names are the drawing program's own; they are part of the file format.
FRAME = {
    "x": "FreeCAD X",
    "z": "-FreeCAD Y",
    "y": "up",
    "front": "-Z",
    "rot": "degrees about vertical, CCW from above",
}

PLACE_KEYS = ("x", "z", "rot", "model")


class PositionsError(ValueError):
    """The positions file, or the layout it is going into, cannot be used as it is."""


@dataclass
class Cell:
    code: str
    x: float
    z: float
    rot: float
    model: str | None


@dataclass
class Positions:
    line: str | None
    source: str | None
    cells: list[Cell]


@dataclass
class Change:
    code: str
    added: bool
    before: dict
    after: dict

    @property
    def moved(self) -> bool:
        return self.added or self.before != self.after


@dataclass
class Report:
    line: str
    changes: list[Change] = field(default_factory=list)
    not_in_drawing: list[str] = field(default_factory=list)

    @property
    def added(self) -> list[Change]:
        return [c for c in self.changes if c.added]

    @property
    def changed(self) -> list[Change]:
        return [c for c in self.changes if not c.added and c.moved]

    @property
    def unchanged(self) -> list[Change]:
        return [c for c in self.changes if not c.moved]


def _read_json(path: Path) -> object:
    # utf-8-sig: a file saved by Notepad starts with a byte-order mark, and
    # plain utf-8 would refuse it with an error nobody can read.
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise PositionsError(f"{path} does not exist") from None
    except UnicodeDecodeError as err:
        raise PositionsError(f"{path} is not UTF-8 text: {err}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise PositionsError(f"{path} is not valid JSON: {err}") from None


def _number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PositionsError(f"{what} must be a number, not {value!r}")
    return float(value)


def read_positions(path: Path) -> Positions:
    """The positions file at `path`, checked. Raises PositionsError saying what is wrong."""
    data = _read_json(Path(path))
    if not isinstance(data, dict):
        raise PositionsError(f"{path} must hold one JSON object")
    units = data.get("units")
    if units != "m":
        raise PositionsError(f"units must be \"m\" (metres), not {units!r}")
    frame = data.get("frame")
    if frame != FRAME:
        raise PositionsError(
            "the file's frame is not the one this reads - x along the line, z across it "
            f"(-FreeCAD Y), y up, front -Z, rot in degrees CCW from above. It says: {frame!r}. "
            "Nothing was flipped to make it fit; export it again so the frame matches.")
    cells_in = data.get("cells")
    if not isinstance(cells_in, list) or not cells_in:
        raise PositionsError("cells must be a list with at least one cell in it")

    cells: list[Cell] = []
    seen: set[str] = set()
    for index, raw in enumerate(cells_in):
        if not isinstance(raw, dict):
            raise PositionsError(f"cell {index + 1} is not an object")
        code = raw.get("code")
        if not isinstance(code, str) or not code.strip():
            raise PositionsError(f"cell {index + 1} has no code")
        code = code.strip()
        if code in seen:
            raise PositionsError(f"{code} is in the file twice")
        seen.add(code)
        model = raw.get("model")
        if model is not None and not isinstance(model, str):
            raise PositionsError(f"{code}: model must be a file name, not {model!r}")
        # A Windows exporter may write C:\plant\models\FILL01.glb; the layout
        # names a file in the pack's models/ folder, so keep the name alone.
        model = PureWindowsPath(model).name if model else None
        cells.append(Cell(
            code=code,
            x=_number(raw.get("x"), f"{code}: x"),
            z=_number(raw.get("z"), f"{code}: z"),
            rot=_number(raw.get("rot", 0.0), f"{code}: rot") % 360.0,
            model=model or None,
        ))
    line = data.get("line")
    source = data.get("source")
    return Positions(line=line if isinstance(line, str) else None,
                     source=source if isinstance(source, str) else None, cells=cells)


def read_layout(path: Path) -> dict:
    """The layout file at `path`, or an empty one if there is none yet."""
    path = Path(path)
    if not path.exists():
        return {}
    data = _read_json(path)
    if not isinstance(data, dict):
        raise PositionsError(f"{path} must hold one JSON object")
    return data


def _which_line(layout: dict, positions: Positions, line: str | None) -> str:
    known = list((layout.get("lines") or {}).keys())
    if line:
        return line
    if positions.line and (positions.line in known or not known):
        return positions.line
    if len(known) == 1:
        return known[0]
    if not known:
        raise PositionsError("the file names no line and the layout has none: say which with --line")
    raise PositionsError(
        f"the file's line {positions.line!r} is not in the layout, which has {len(known)}: "
        f"{', '.join(known)}. Say which with --line.")


def apply(layout: dict, positions: Positions, line: str | None = None) -> tuple[dict, Report]:
    """The layout with every drawn cell's x, z, rot and model written in.

    `layout` is not changed; the new layout is returned with a report of what
    moved. A cell the layout does not know is added as `generic`; a station the
    file does not name is kept as it was and listed in `not_in_drawing`.
    """
    out = json.loads(json.dumps(layout))  # a deep copy that keeps key order
    code = _which_line(out, positions, line)
    lines = out.setdefault("lines", {})
    line_conf = lines.setdefault(code, {})
    if not isinstance(line_conf, dict):
        raise PositionsError(f"the layout's entry for {code} is not an object")
    stations = line_conf.setdefault("stations", {})

    report = Report(line=code)
    for cell in positions.cells:
        added = cell.code not in stations
        conf = stations.setdefault(cell.code, {"kind": "generic"})
        before = {k: conf[k] for k in PLACE_KEYS if k in conf}
        conf["x"] = cell.x
        conf["z"] = cell.z
        conf["rot"] = cell.rot
        if cell.model:
            conf["model"] = cell.model
        after = {k: conf[k] for k in PLACE_KEYS if k in conf}
        if not added and "rot" not in before and cell.rot == 0.0:
            before = {**before, "rot": 0.0}  # no rot and rot 0 are the same place
        report.changes.append(Change(cell.code, added, before, after))
    drawn = {cell.code for cell in positions.cells}
    report.not_in_drawing = [c for c in stations if c not in drawn and not c.startswith("_")]
    return out, report


def write_layout(path: Path, layout: dict) -> None:
    """Write the layout as UTF-8 JSON with LF line ends, on every platform."""
    text = json.dumps(layout, indent=2, ensure_ascii=False) + "\n"
    with open(Path(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def describe(report: Report) -> list[str]:
    """What `report` means, as lines a person reads. Every list states its total."""
    def place(values: dict) -> str:
        parts = [f"x {values['x']:g}" if "x" in values else "x -",
                 f"z {values['z']:g}" if "z" in values else "z -",
                 f"rot {values.get('rot', 0.0):g}"]
        if values.get("model"):
            parts.append(values["model"])
        return ", ".join(parts)

    total = len(report.changes)
    out = [f"Line {report.line}: {total} cell{'s' if total != 1 else ''} in the drawing - "
           f"{len(report.changed)} changed, {len(report.added)} new, {len(report.unchanged)} unchanged."]
    for change in report.changed:
        out.append(f"  changed    {change.code}: {place(change.before)}  ->  {place(change.after)}")
    for change in report.added:
        out.append(f"  new        {change.code}: {place(change.after)} (drawn as generic)")
    missing = report.not_in_drawing
    if missing:
        out.append(f"{len(missing)} station{'s' if len(missing) != 1 else ''} in the layout "
                   f"not in the drawing, kept where they were: {', '.join(missing)}")
    else:
        out.append("0 stations in the layout are missing from the drawing.")
    return out
