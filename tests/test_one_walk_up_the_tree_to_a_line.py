"""A machine's line is found by one walk, however the tree was read.

Structure report 2026-11-18, clean-up C1: "line lookup, four copies". #290
took the Floor grid's copy out of the dashboard router. Two more walked up
an equipment tree they had already read in one query: the plant-wide OEE
list (`analysis.line_of_each`) and the people rollup
(`masterdata.home_work_centers`), each with its own loop and its own guard
against a loop in the tree. Both now ask `masterdata.work_center_in`, the
bulk form of `work_center_of`, so the three cannot drift apart on what "the
line above" means.
"""

import ast
from pathlib import Path

from sqlalchemy import select

from fsmes.domain import Equipment, EquipmentLevel
from fsmes.services import analysis, masterdata

SRC = Path(__file__).resolve().parents[1] / "src" / "fsmes"


def _walks_to_a_line() -> set[tuple[str, str]]:
    """(file, function) for every place that compares one node's level with
    a work centre's - the step a walk up the tree takes. A query's
    `Equipment.level == ...` is a filter, not a walk, and is left out."""
    found = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef):
                continue
            for node in ast.walk(func):
                if not (isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute)):
                    continue
                left = node.left
                if left.attr != "level" or (isinstance(left.value, ast.Name)
                                            and left.value.id == "Equipment"):
                    continue
                if any(isinstance(c, ast.Attribute) and c.attr == "WORK_CENTER"
                       for c in node.comparators):
                    found.add((path.relative_to(SRC).as_posix(), func.name))
    return found


def test_only_masterdata_walks_up_the_tree_to_a_line():
    assert _walks_to_a_line() == {
        ("services/masterdata.py", "work_center_of"),
        ("services/masterdata.py", "work_center_in"),
    }, "find a machine's line with masterdata.work_center_of, or work_center_in over a tree already read"


def test_the_plant_wide_list_says_the_line_work_center_of_says(session):
    """A machine on a cell on a line belongs to the line, in both."""
    line = session.scalar(select(Equipment).where(Equipment.level == EquipmentLevel.WORK_CENTER))
    cell = Equipment(code="CELL9", name="Cell 9", level=EquipmentLevel.AREA, parent=line)
    deep = Equipment(code="DEEP9", name="Deep machine", level=EquipmentLevel.WORK_UNIT, parent=cell)
    session.add_all([cell, deep])
    session.flush()

    lines_of = analysis.line_of_each(session)
    assert lines_of[deep.id] is line
    for unit in session.scalars(select(Equipment).where(masterdata.on_the_floor())):
        assert lines_of.get(unit.id) is masterdata.work_center_of(session, unit), unit.code


def test_a_loop_in_the_tree_ends_the_walk_instead_of_hanging_it(session):
    a = Equipment(code="LOOPA", name="Loop A", level=EquipmentLevel.AREA)
    b = Equipment(code="LOOPB", name="Loop B", level=EquipmentLevel.WORK_UNIT, parent=a)
    session.add_all([a, b])
    session.flush()
    a.parent_id = b.id
    session.flush()

    nodes = {e.id: e for e in session.scalars(select(Equipment))}
    assert masterdata.work_center_in(nodes, b) is None
    assert b.id not in analysis.line_of_each(session)
