"""Derive labs/cutlery/line_layout.json from positions/<LINE>.json - where each
of the 83 machines stands on the 3D line view's floor, and which model it is
drawn with.

The positions files are one per line, in the shape `fsmes layout from-freecad`
reads (docs/operate/packs.md, "Where the machines stand"): metres, x along the
plant, z across it, rot in degrees counter-clockwise from above. They came from
studio's own plant design on 2026-11-12, one entry per equipment code in
line.json, none missing, none extra. The seven models in `models/` are studio's
own generic massing - no vendor geometry, no logos.

This script is the importer run once per line, so the layout and the drawing
always agree: `fsmes layout from-freecad positions/FORK1.json --layout
line_layout.json --line FORK1LINE --check` says every cell is unchanged. Each
line in the drawing is named by its line.json name (FORK1); the MES work
center is that name with LINE on the end (FORK1LINE), as init.py seeds it. A
station's kind is the drawing's kind in lowercase (mold, degate, mark, stacker,
wrapper, palletizer, stretchwrap): the view has no built-in shape for those, so
without its model it would be drawn as a plain box - the model is what shows.
The belts carry the view's small default unit, not a fork: a cutlery piece on
the belt is a later job, after 2026-11-20.

    python3 labs/cutlery/make_layout.py
    python3 labs/cutlery/make_layout.py <dir>     the positions/ in <dir>, line_layout.json beside it
"""
import json
import sys
from pathlib import Path

from fsmes.services import layout_import

HERE = Path(__file__).resolve().parent

COMMENT = (
    "Geometry for the 3D line view of the cutlery plant: 83 machines on 61 lines, "
    "where studio's plant design puts them (positions/<LINE>.json, metres, x along "
    "the plant, z across it). Each machine is drawn with the model named here from "
    "models/ beside this file. Written by make_layout.py - edit the positions files "
    "or the drawing they came from, not this one."
)


def main(root: Path) -> None:
    layout = {"_comment": COMMENT, "defaults": {"unit_label": "unit"}, "lines": {}}
    files = sorted((root / "positions").glob("*.json"))
    if not files:
        sys.exit(f"no positions files in {root / 'positions'}")
    total = 0
    for path in files:
        drawn = layout_import.read_positions(path)
        kinds = {c["code"]: c.get("kind") for c in json.loads(path.read_text(encoding="utf-8-sig"))["cells"]}
        line = f"{drawn.line}LINE"
        layout, report = layout_import.apply(layout, drawn, line)
        stations = layout["lines"][line]["stations"]
        for cell in drawn.cells:
            kind = kinds.get(cell.code)
            if isinstance(kind, str) and kind:
                stations[cell.code]["kind"] = kind.lower()
        total += len(report.changes)
    out = root / "line_layout.json"
    layout_import.write_layout(out, layout)
    print(f"{out}: {len(files)} lines, {total} machines")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE)
