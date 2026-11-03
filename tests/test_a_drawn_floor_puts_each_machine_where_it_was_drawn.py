"""`fsmes layout from-freecad` puts each machine where the drawing has it.

A plant engineer draws the floor in a CAD program; its export macro writes a
positions file (x along the line, z across, metres, rot in degrees
counter-clockwise from above). The command writes each cell's x, z, rot and
model into the plant's `line_layout.json` and touches nothing else in it.

The positions file here is a hand copy of the eight-cell sample the export
macro writes, kept in `tests/fixtures/positions/`; no model file is involved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fsmes.cli import app
from fsmes.config import get_settings
from fsmes.seed_kepsim import seed_kepsim_line
from fsmes.services import layout_import

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "tests" / "fixtures" / "positions" / "eight-cells.json"
KEPSIM_MAP = REPO / "config" / "tag_map_kepsim.json"
CELLS = ["UB10", "UB20", "FR10", "FR20", "RS10", "RS20", "MF10", "QI10"]

runner = CliRunner()


def _layout() -> dict:
    """A layout as a pack ships it: a comment, defaults, two lines, a kind and
    a name the drawing knows nothing about, and a station it never drew."""
    return {
        "_comment": "kept as it is",
        "defaults": {"spacing": 12.0, "unit_label": "body"},
        "lines": {
            "BS1": {
                "unit_label": "body",
                "stations": {
                    **{code: {"kind": "cell", "x": i * 12.0, "z": 0.0} for i, code in enumerate(CELLS)},
                    "UB10": {"kind": "cell", "name": "Underbody", "x": 0.0, "z": 0.0},
                    "RW10": {"kind": "generic", "x": 96.0, "z": 0.0},
                },
            },
            "OTHER": {"stations": {"A1": {"kind": "washer"}}},
        },
    }


def _write(path: Path, data, *, bom: bool = False, crlf: bool = False) -> Path:
    text = json.dumps(data, indent=2)
    if crlf:
        text = text.replace("\n", "\r\n")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return path


def _run(*args: str):
    return runner.invoke(app, ["layout", "from-freecad", *args])


# --------------------------------------------------------------- the writing


def test_the_eight_drawn_cells_land_in_the_layout_with_their_place_turn_and_model(tmp_path):
    layout = _write(tmp_path / "line_layout.json", _layout())
    result = _run(str(SAMPLE), "--layout", str(layout), "--line", "BS1")
    assert result.exit_code == 0, result.output

    stations = json.loads(layout.read_text(encoding="utf-8"))["lines"]["BS1"]["stations"]
    drawn = json.loads(SAMPLE.read_text(encoding="utf-8"))["cells"]
    for cell in drawn:
        got = stations[cell["code"]]
        assert (got["x"], got["z"], got["rot"], got["model"]) == (
            cell["x"], cell["z"], cell["rot"], cell["model"]), cell["code"]
    assert stations["QI10"]["rot"] == 90.0


def test_everything_the_drawing_does_not_say_is_kept_in_its_place_and_order(tmp_path):
    before = _layout()
    layout = _write(tmp_path / "line_layout.json", before)
    assert _run(str(SAMPLE), "--layout", str(layout), "--line", "BS1").exit_code == 0

    after = json.loads(layout.read_text(encoding="utf-8"))
    assert list(after) == list(before)
    assert after["_comment"] == before["_comment"] and after["defaults"] == before["defaults"]
    assert after["lines"]["OTHER"] == before["lines"]["OTHER"]
    assert list(after["lines"]["BS1"]["stations"]) == list(before["lines"]["BS1"]["stations"])
    assert after["lines"]["BS1"]["stations"]["UB10"]["name"] == "Underbody"
    assert all(after["lines"]["BS1"]["stations"][c]["kind"] == "cell" for c in CELLS)


def test_a_station_the_drawing_leaves_out_is_kept_where_it_was_and_named(tmp_path):
    layout = _write(tmp_path / "line_layout.json", _layout())
    result = _run(str(SAMPLE), "--layout", str(layout), "--line", "BS1")
    assert "1 station in the layout not in the drawing, kept where they were: RW10" in result.output
    kept = json.loads(layout.read_text(encoding="utf-8"))["lines"]["BS1"]["stations"]["RW10"]
    assert kept == {"kind": "generic", "x": 96.0, "z": 0.0}


def test_a_drawn_cell_the_layout_has_never_heard_of_is_added_as_a_plain_box(tmp_path):
    layout = _write(tmp_path / "line_layout.json", {"lines": {"BS1": {"stations": {}}}})
    result = _run(str(SAMPLE), "--layout", str(layout))
    assert result.exit_code == 0, result.output
    assert "8 cells in the drawing - 0 changed, 8 new, 0 unchanged" in result.output
    stations = json.loads(layout.read_text(encoding="utf-8"))["lines"]["BS1"]["stations"]
    assert list(stations) == CELLS
    assert all(s["kind"] == "generic" for s in stations.values())


def test_the_file_s_own_line_is_used_when_the_layout_has_it_and_the_only_line_otherwise():
    drawn = layout_import.read_positions(SAMPLE)          # it names BIW1
    _, report = layout_import.apply({"lines": {"BIW1": {}, "X": {}}}, drawn)
    assert report.line == "BIW1"
    _, report = layout_import.apply({"lines": {"BS1": {}}}, drawn)
    assert report.line == "BS1"
    with pytest.raises(layout_import.PositionsError, match="--line"):
        layout_import.apply({"lines": {"BS1": {}, "X": {}}}, drawn)


def test_running_it_twice_writes_the_second_time_nothing(tmp_path):
    layout = _write(tmp_path / "line_layout.json", _layout())
    _run(str(SAMPLE), "--layout", str(layout), "--line", "BS1")
    first = layout.read_bytes()
    result = _run(str(SAMPLE), "--layout", str(layout), "--line", "BS1")
    assert "0 changed, 0 new, 8 unchanged" in result.output
    assert "Nothing to write" in result.output
    assert layout.read_bytes() == first


def test_the_layout_beside_the_drawing_is_the_one_written_when_none_is_named(tmp_path):
    positions = tmp_path / "positions.json"
    positions.write_bytes(SAMPLE.read_bytes())
    layout = _write(tmp_path / "line_layout.json", _layout())
    assert _run(str(positions), "--line", "BS1").exit_code == 0
    assert json.loads(layout.read_text(encoding="utf-8"))["lines"]["BS1"]["stations"]["QI10"]["rot"] == 90.0


# ----------------------------------------------------------------- the check


def test_check_prints_what_would_change_and_writes_nothing(tmp_path):
    layout = _write(tmp_path / "line_layout.json", _layout())
    before = layout.read_bytes()
    result = _run(str(SAMPLE), "--layout", str(layout), "--line", "BS1", "--check")
    assert result.exit_code == 0, result.output
    assert "changed    QI10: x 84, z 0, rot 0  ->  x 84, z 0, rot 90, QI10.glb" in result.output
    assert "nothing written" in result.output
    assert layout.read_bytes() == before


def test_check_against_a_layout_that_does_not_exist_yet_creates_nothing(tmp_path):
    layout = tmp_path / "pack" / "line_layout.json"
    result = _run(str(SAMPLE), "--layout", str(layout), "--check")
    assert result.exit_code == 0, result.output
    assert "(new file)" in result.output
    assert not layout.exists() and not layout.parent.exists()


# ---------------------------------------------------------------- refusals


@pytest.mark.parametrize("frame_change", [
    {"z": "FreeCAD Y"},                       # mirrored: the macro's sign left out
    {"x": "FreeCAD Y", "z": "FreeCAD X"},     # swapped axes
    {"rot": "degrees about vertical, CW from above"},
])
def test_a_file_in_another_frame_is_refused_and_the_layout_is_untouched(tmp_path, frame_change):
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["frame"] = {**data["frame"], **frame_change}
    positions = _write(tmp_path / "positions.json", data)
    layout = _write(tmp_path / "line_layout.json", _layout())
    before = layout.read_bytes()
    result = _run(str(positions), "--layout", str(layout), "--line", "BS1")
    assert result.exit_code == 2
    assert "NOT OK" in result.output and "frame" in result.output
    assert layout.read_bytes() == before


def test_a_file_in_millimetres_is_refused(tmp_path):
    data = {**json.loads(SAMPLE.read_text(encoding="utf-8")), "units": "mm"}
    with pytest.raises(layout_import.PositionsError, match="metres"):
        layout_import.read_positions(_write(tmp_path / "p.json", data))


def test_a_cell_drawn_twice_or_with_no_place_is_refused(tmp_path):
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    twice = {**data, "cells": data["cells"] + [data["cells"][0]]}
    with pytest.raises(layout_import.PositionsError, match="UB10 is in the file twice"):
        layout_import.read_positions(_write(tmp_path / "twice.json", twice))
    placeless = {**data, "cells": [{"code": "UB10", "z": 0.0}]}
    with pytest.raises(layout_import.PositionsError, match="UB10: x must be a number"):
        layout_import.read_positions(_write(tmp_path / "placeless.json", placeless))


# ------------------------------------------------------------------ Windows


def test_a_file_saved_with_a_byte_order_mark_and_windows_line_ends_reads_the_same(tmp_path):
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    windows = _write(tmp_path / "positions.json", data, bom=True, crlf=True)
    assert layout_import.read_positions(windows) == layout_import.read_positions(SAMPLE)


def test_a_model_given_as_a_windows_path_keeps_only_its_file_name(tmp_path):
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["cells"][0]["model"] = "C:\\Users\\plant\\models\\UB10.glb"
    data["cells"][1]["model"] = "models/UB20.glb"
    drawn = layout_import.read_positions(_write(tmp_path / "positions.json", data))
    assert [c.model for c in drawn.cells[:2]] == ["UB10.glb", "UB20.glb"]


def test_the_written_layout_has_unix_line_ends_and_reads_back_as_utf_8(tmp_path):
    layout = _write(tmp_path / "line_layout.json", _layout(), bom=True, crlf=True)
    assert _run(str(SAMPLE), "--layout", str(layout), "--line", "BS1").exit_code == 0
    raw = layout.read_bytes()
    assert b"\r\n" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw.decode("utf-8"))["lines"]["BS1"]["stations"]["QI10"]["rot"] == 90.0


# ------------------------------------------------------------- the line view


def test_the_line_view_draws_an_imported_station_where_and_how_it_was_drawn(client, session, monkeypatch, tmp_path):
    seed_kepsim_line(session, tag_map=KEPSIM_MAP)
    session.flush()
    layout = tmp_path / "line_layout.json"
    monkeypatch.setattr(get_settings(), "line_layout_file", layout)
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["line"] = "SIMLINE"
    data["cells"] = [{"code": "FILL01", "x": 30.0, "z": -4.0, "rot": 90.0},
                     {"code": "PAL01", "x": 40.0, "z": 2.5, "rot": 450.0}]
    result = _run(str(_write(tmp_path / "positions.json", data)), "--layout", str(layout))
    assert result.exit_code == 0, result.output

    stations = {s["code"]: s for s in
                client.get("/line/layout", params={"line": "SIMLINE"}).json()["stations"]}
    assert (stations["FILL01"]["x"], stations["FILL01"]["z"], stations["FILL01"]["rot"]) == (30.0, -4.0, 90.0)
    assert stations["PAL01"]["rot"] == 90.0          # one full turn and a quarter is a quarter
    assert stations["FILL01"]["placed"] == "drawn"
    assert stations["WASH01"]["rot"] == 0.0          # a station nobody turned
