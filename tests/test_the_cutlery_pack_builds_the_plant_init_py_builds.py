"""The cutlery pack carries the plant `labs/cutlery/init.py` builds, and
cannot drift from it (2026-11-12).

The cutlery plant ships in the wheel so `fsmes demo cutlery` runs on a PC with
no checkout. A shipped plant is a pack - plant.toml and `masterdata/` - and
applying a pack runs no code (decision 0022). But the lab still builds its
plant with init.py, from the line.json and tag_map.json `build_config.py` and
`make_tag_map.py` write. Two seeders of one plant is how a copy goes stale, so
this file builds the plant both ways, in two scratch databases, and compares
them kind by kind:

* the lab's way: `build_config.py --scale 0.01` and `make_tag_map.py` into a
  scratch folder, then init.py's `seed` and `release_orders`;
* the shipped way: `fsmes.pack.masterdata.seed` over the committed
  `labs/cutlery/masterdata/`, the ideal cycles read from the committed tag map.

And it holds the committed files to their generators byte for byte, so
regenerating them (masterdata/README.md says how) is the only way to change
them.

Nothing here starts a plant, a port or a process beyond the two lab scripts.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.db import Base
from fsmes.domain import (
    Equipment,
    EquipmentLevel,
    LotConsumption,
    Material,
    MaterialLot,
    QualitySpec,
    Routing,
    WorkOrder,
)
from fsmes.integrations.opc.tag_map import load_tag_map
from fsmes.pack import masterdata

CUTLERY = Path(__file__).resolve().parents[1] / "labs" / "cutlery"
DATA = CUTLERY / "masterdata"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"cutlery_{name}_drift", CUTLERY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


init = _module("init")
build_config = _module("build_config")

OPENED: list[Session] = []


@pytest.fixture(autouse=True)
def _close_what_this_file_opens():
    OPENED.clear()
    yield
    for session in OPENED:
        session.close()
        session.get_bind().dispose()
    OPENED.clear()


def blank() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    OPENED.append(session)
    return session


@pytest.fixture(scope="module")
def lab_folder(tmp_path_factory) -> Path:
    """The lab's own path: the two scripts crew-lab runs, into a folder of its own."""
    where = tmp_path_factory.mktemp("cutlery-lab")
    for script, args in (("build_config.py", ["--scale", "0.01", "--out", str(where)]),
                         ("make_tag_map.py", [str(where)])):
        subprocess.run([sys.executable, str(CUTLERY / script), *args], check=True,
                       capture_output=True, text=True, encoding="utf-8")
    return where


def by_init(folder: Path) -> Session:
    session = blank()
    made = init.seed(session, folder)
    init.release_orders(session, made["orders"])
    session.flush()
    return session


def by_the_pack() -> Session:
    session = blank()
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(CUTLERY / "tag_map.json")}
    masterdata.seed(session, DATA, cycles)
    session.flush()
    return session


def shape(session: Session) -> dict:
    """Everything about the plant either seeder is responsible for, as plain
    values, without ids (they say which row was written first, nothing more)."""
    equipment = {e.code: (e.name, e.level.value, e.parent.code if e.parent else None,
                          e.ideal_cycle_seconds)
                 for e in session.scalars(select(Equipment))}
    materials = {m.code: (m.name, m.unit, m.type.value, m.counted_in_pieces)
                 for m in session.scalars(select(Material))}
    routings = {r.code: (r.name, r.material.code,
                         [(o.seq, o.name, o.equipment.code)
                          for o in sorted(r.operations, key=lambda o: o.seq)])
                for r in session.scalars(select(Routing))}
    specs = sorted((q.material.code, q.characteristic, q.unit, q.min_value, q.max_value)
                   for q in session.scalars(select(QualitySpec)))
    lots = {lot.code: (lot.material.code, lot.original_quantity, round(lot.quantity, 3))
            for lot in session.scalars(select(MaterialLot))}
    orders = {o.code: (o.material.code, o.quantity, o.status.value, o.priority,
                       o.work_center.code if o.work_center else None,
                       [(op.seq, op.equipment.code if op.equipment else None)
                        for op in sorted(o.operations, key=lambda op: op.seq)])
              for o in session.scalars(select(WorkOrder))}
    issued = sorted((session.get(WorkOrder, c.work_order_id).code,
                     session.get(MaterialLot, c.lot_id).code, c.quantity)
                    for c in session.scalars(select(LotConsumption)))
    return {"equipment": equipment, "materials": materials, "routings": routings,
            "quality_specs": specs, "lots": lots, "work_orders": orders, "issued": issued}


def test_the_cutlery_pack_builds_the_plant_init_py_builds_from_the_lab_s_own_scripts(lab_folder):
    theirs = shape(by_init(lab_folder))
    ours = shape(by_the_pack())
    for kind in theirs:
        assert ours[kind] == theirs[kind], (
            f"{kind} differs between labs/cutlery/init.py and labs/cutlery/masterdata/")


def test_the_shipped_plant_has_eighty_three_machines_and_two_orders_on_every_line():
    plant = shape(by_the_pack())
    machines = [c for c, e in plant["equipment"].items() if e[1] == EquipmentLevel.WORK_UNIT.value]
    lines = [c for c, e in plant["equipment"].items() if e[1] == EquipmentLevel.WORK_CENTER.value]
    assert len(machines) == 83, f"{len(machines)} machines of 83"
    per_line = {line: [o for o, row in plant["work_orders"].items() if row[4] == line] for line in lines}
    assert {len(orders) for orders in per_line.values()} == {2}, per_line
    statuses = sorted(row[2] for row in plant["work_orders"].values())
    assert statuses.count("released") == len(lines) and statuses.count("planned") == len(lines)
    # Every released order on a line that eats a raw lot has had it issued.
    issued = {order for order, _lot, _qty in plant["issued"]}
    assert issued and all(plant["work_orders"][o][2] == "released" for o in issued)


def test_the_committed_line_json_and_tag_map_are_the_lab_s_hundredth_scale_build(lab_folder):
    for name in ("line.json", "tag_map.json"):
        assert (CUTLERY / name).read_text(encoding="utf-8") == \
            (lab_folder / name).read_text(encoding="utf-8"), (
                f"labs/cutlery/{name} is not what build_config.py --scale 0.01 and "
                "make_tag_map.py write; regenerate it (masterdata/README.md)")


def test_the_committed_masterdata_is_what_init_py_write_masterdata_writes(tmp_path):
    written = init.write_masterdata(CUTLERY, tmp_path)
    assert written["equipment"] == 150 and written["work_orders"] == 122, written
    for kind in init.GENERATED_KINDS:
        assert (DATA / f"{kind}.json").read_text(encoding="utf-8") == \
            (tmp_path / f"{kind}.json").read_text(encoding="utf-8"), (
                f"labs/cutlery/masterdata/{kind}.json is stale; "
                "run init.py --write-masterdata (masterdata/README.md)")


def test_the_shipped_cutlery_pack_checks_out_offline():
    assert masterdata.problems(DATA) == []
