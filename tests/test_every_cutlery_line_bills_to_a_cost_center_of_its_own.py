"""Every cutlery line bills to a cost center of its own (2026-11-13).

The routing rows on Setup > Admin > Routings name the cost center each step's
machine bills to (#246). Until this pack set one, every row on the cutlery
plant said "cost center unknown". The pack now sets one per line, and the
machines under a line inherit it through `services.masterdata.cost_center`.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.db import Base
from fsmes.domain import Equipment, EquipmentLevel
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import masterdata as pack_masterdata
from fsmes.services import masterdata

CUTLERY = Path(__file__).resolve().parents[1] / "labs" / "cutlery"


def _applied_to_an_empty_database() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(CUTLERY / "tag_map.json")}
    pack_masterdata.seed(session, CUTLERY / "masterdata", cycles)
    session.flush()
    return session


def test_fork1_mold_bills_to_its_line_s_cost_center_and_no_two_lines_share_one():
    session = _applied_to_an_empty_database()
    try:
        mold = session.scalar(select(Equipment).where(Equipment.code == "FORK1_Mold"))
        line = session.scalar(select(Equipment).where(Equipment.code == "FORK1LINE"))
        assert line.cost_center == "CC-MOULD01"
        assert mold.cost_center is None, "the machine inherits; the pack sets it on the line"
        assert masterdata.cost_center(session, mold) == "CC-MOULD01"

        lines = list(session.scalars(select(Equipment)
                                     .where(Equipment.level == EquipmentLevel.WORK_CENTER)))
        assert len(lines) == 61
        billed = [ln.cost_center for ln in lines]
        assert all(billed), [ln.code for ln in lines if not ln.cost_center]
        assert len(set(billed)) == 61

        machines = list(session.scalars(select(Equipment)
                                        .where(Equipment.level == EquipmentLevel.WORK_UNIT)))
        assert len(machines) == 83
        assert all(masterdata.cost_center(session, m) == m.parent.cost_center for m in machines)
    finally:
        session.close()
        session.get_bind().dispose()


def test_a_pack_whose_cost_center_is_not_a_short_code_does_not_check_out(tmp_path):
    folder = tmp_path / "masterdata"
    folder.mkdir()
    (folder / "equipment.json").write_text(
        '[{"code": "L1", "name": "Line 1", "level": "work_center", "cost_center": 12}]',
        encoding="utf-8")
    found = pack_masterdata.problems(folder)
    assert any("cost center" in p for p in found), found
