"""A full day of the cutlery plant in the database, and the recall questions
timed against it - ten million pieces, without waiting a day.

    python3 labs/cutlery/fill_day.py [--pieces 10000000] [--db labs/cutlery/.data/tenmillion.db]
                                     [--out labs/cutlery/out/results/fill_day.json]

The scored run (run_cutlery.py) measures the rate: whether the MES takes a
stack a second through the API. This measures the size: what a day's
serials cost on disk and what the trace questions cost once the table holds
a day. The rows are written the way the batch endpoint writes them - the
same tables, the same tree, one audit entry per stack, pack and pallet -
directly through SQLAlchemy Core rather than HTTP, because ten million calls
is the rate question again and a day's worth would take a day at the
customer's real rate. What is timed afterwards is the product's own service
layer, unchanged: the same code the Trace screen and the agent tools run.

Cost: about 2 GB of disk for a full day (labs/cutlery/.data is ignored).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE))

PIECES_PER_STACK_PER_TYPE = 8
UTENSILS = ("F", "S", "K")
PACKS_PER_PALLET = 240
BATCH_STACKS = 400            # stacks per transaction: 10,000 rows


def build(db_path: Path, pieces: int, echo=print) -> dict:
    import build_config
    from sqlalchemy import insert, select, text
    from sqlalchemy.orm import Session

    from fsmes.db import Base, make_engine
    from fsmes.domain import AuditLog, Material, SerialUnit, UnitStatus
    from fsmes.services import execution

    if db_path.exists():
        db_path.unlink()
        for side in ("-wal", "-shm"):
            Path(str(db_path) + side).unlink(missing_ok=True)
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    (HERE / "line.json").write_text(json.dumps(build_config.build(), indent=2) + "\n", encoding="utf-8")
    import init as cutlery_init

    with Session(engine) as session:
        made = cutlery_init.seed(session, HERE)
        cutlery_init.release_orders(session, made["orders"])
        session.commit()
        materials = {m.code: m.id for m in session.scalars(select(Material))}
        orders = {code: session.execute(text("select id from work_orders where code = :c"), {"c": code}).scalar()
                  for code in ("WO-FORK", "WO-SPOON", "WO-KNIFE", "WO-STACK1", "WO-WRAP1", "WO-PALLET")}
        machines = {code: session.execute(text("select id from equipment where code = :c"), {"c": code}).scalar()
                    for code in ("FORK_Mark", "SPOON_Mark", "KNIFE_Mark", "STACK1_Stacker", "WRAP1_Wrapper",
                                 "PALLET_Palletizer")}
        # Everything the API would issue: the resin lots to the moulding
        # orders, the plates to the wrappers - done by the seed already.
        _ = execution

    stacks_total = pieces // (PIECES_PER_STACK_PER_TYPE * len(UTENSILS))
    pallets_total = stacks_total // PACKS_PER_PALLET
    echo(f"writing {pieces:,} pieces as {stacks_total:,} stacks, {stacks_total:,} packs, {pallets_total:,} pallets")
    utensil_material = {"F": materials["UT-FORK"], "S": materials["UT-SPOON"], "K": materials["UT-KNIFE"]}
    utensil_order = {"F": orders["WO-FORK"], "S": orders["WO-SPOON"], "K": orders["WO-KNIFE"]}
    utensil_machine = {"F": machines["FORK_Mark"], "S": machines["SPOON_Mark"], "K": machines["KNIFE_Mark"]}
    good = UnitStatus.GOOD
    now = datetime.now(UTC).replace(tzinfo=None)

    t0 = time.perf_counter()
    piece_no = dict.fromkeys(UTENSILS, 1)
    next_id = 1
    with engine.begin() as con:
        con.execute(text("PRAGMA synchronous=OFF"))
        con.execute(text("PRAGMA journal_mode=WAL"))
    with engine.connect() as con:
        # A pallet row is written after the packs that point at it in the
        # same batch; the rows are consistent at commit, so the per-statement
        # check is off for the load (the product's connections keep it on).
        con.execute(text("PRAGMA foreign_keys=OFF"))
        stack_no = 1
        pack_ids: list[int] = []
        pallet_no = 1
        while stack_no <= stacks_total:
            rows: list[dict] = []
            audits: list[dict] = []
            for _ in range(min(BATCH_STACKS, stacks_total - stack_no + 1)):
                # The pack first, then the stack in it, then the pieces in the stack.
                pack_id = next_id
                rows.append({"id": pack_id, "serial": f"P-{stack_no:09d}", "material_id": materials["PACK"],
                             "status": good, "produced_by_order_id": orders["WO-WRAP1"],
                             "produced_on_id": machines["WRAP1_Wrapper"], "produced_at": now, "parent_id": None})
                next_id += 1
                stack_id = next_id
                rows.append({"id": stack_id, "serial": f"ST-{stack_no:09d}", "material_id": materials["STACK-24"],
                             "status": good, "produced_by_order_id": orders["WO-STACK1"],
                             "produced_on_id": machines["STACK1_Stacker"], "produced_at": now, "parent_id": pack_id})
                next_id += 1
                for u in UTENSILS:
                    for _ in range(PIECES_PER_STACK_PER_TYPE):
                        rows.append({"id": next_id, "serial": f"{u}-{piece_no[u]:09d}",
                                     "material_id": utensil_material[u], "status": good,
                                     "produced_by_order_id": utensil_order[u],
                                     "produced_on_id": utensil_machine[u], "produced_at": now,
                                     "parent_id": stack_id})
                        next_id += 1
                        piece_no[u] += 1
                audits.append({"actor": "FLOOR-SIM", "action": "unit.batch", "entity_type": "unit",
                               "entity_id": f"ST-{stack_no:09d}", "ts": now,
                               "after": {"units": 24, "container": f"ST-{stack_no:09d}"}})
                audits.append({"actor": "FLOOR-SIM", "action": "unit.batch", "entity_type": "unit",
                               "entity_id": f"P-{stack_no:09d}", "ts": now,
                               "after": {"units": 0, "container": f"P-{stack_no:09d}", "packed": 1}})
                pack_ids.append(pack_id)
                stack_no += 1
                if len(pack_ids) == PACKS_PER_PALLET:
                    pallet_id = next_id
                    rows.append({"id": pallet_id, "serial": f"PL-{pallet_no:07d}", "material_id": materials["PALLET"],
                                 "status": good, "produced_by_order_id": orders["WO-PALLET"],
                                 "produced_on_id": machines["PALLET_Palletizer"], "produced_at": now,
                                 "parent_id": None})
                    next_id += 1
                    audits.append({"actor": "FLOOR-SIM", "action": "unit.batch", "entity_type": "unit",
                                   "entity_id": f"PL-{pallet_no:07d}", "ts": now,
                                   "after": {"units": 0, "container": f"PL-{pallet_no:07d}", "packed": 240}})
                    # The packs written in this batch are still in `rows`; set
                    # their parent there. Packs from an earlier batch are
                    # already on disk and are updated below.
                    on_disk = []
                    in_rows = {r["id"]: r for r in rows if r["material_id"] == materials["PACK"]}
                    for pid in pack_ids:
                        if pid in in_rows:
                            in_rows[pid]["parent_id"] = pallet_id
                        else:
                            on_disk.append(pid)
                    if on_disk:
                        con.execute(text(f"update serial_units set parent_id = {pallet_id} where id in "
                                         f"({','.join(str(i) for i in on_disk)})"))
                    pack_ids = []
                    pallet_no += 1
            con.execute(insert(SerialUnit), rows)
            con.execute(insert(AuditLog), audits)
            con.commit()
            if (stack_no - 1) % (BATCH_STACKS * 25) == 0 or stack_no > stacks_total:
                done = min(stack_no - 1, stacks_total)
                rate = done * 26 / (time.perf_counter() - t0)
                echo(f"  {done:,}/{stacks_total:,} stacks ({rate:,.0f} rows/s)")
    wall = time.perf_counter() - t0
    with engine.begin() as con:
        con.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    engine.dispose()
    size = db_path.stat().st_size
    return {"pieces": pieces, "stacks": stacks_total, "pallets": pallets_total,
            "rows_serial_units": next_id - 1, "write_wall_s": round(wall, 1),
            "rows_per_s": round((next_id - 1) / wall), "db_bytes": size, "db_gb": round(size / 1e9, 2),
            "bytes_per_serial": round(size / (next_id - 1))}


def measure(db_path: Path, echo=print) -> dict:
    from run_cutlery import time_queries

    return time_queries(db_path, echo=echo)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pieces", type=int, default=10_000_000)
    parser.add_argument("--db", type=Path, default=HERE / ".data" / "tenmillion.db")
    parser.add_argument("--out", type=Path, default=HERE / "out" / "results" / "fill_day.json")
    parser.add_argument("--measure-only", action="store_true")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    os.environ.pop("MES_DATABASE_URL", None)

    result: dict = {"stamp": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"), "db": str(args.db)}
    if not args.measure_only:
        result["build"] = build(args.db, args.pieces)
        print(f"built: {result['build']}")
    print("recall questions against a day's serials:")
    result["queries"] = measure(args.db)
    args.out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"results -> {args.out}")


if __name__ == "__main__":
    main()
