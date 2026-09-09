"""Run the cutlery plant as one ephemeral scored run, serialising every
piece, and measure what ten million a day costs the MES.

    python3 labs/cutlery/run_cutlery.py [--speed 10] [--keep] [--poll 2]
                                        [--results labs/cutlery/out/results]

In order:

1. rebuilds line.json and tag_map.json (derived, never hand-edited) and
   regenerates the plant's CSVs;
2. runs the same ephemeral scored run every plant uses - fresh database,
   ports from the reserved ranges, the registry's `post_boot` script
   (trace_driver.py) serialising through the API while the hour plays;
3. samples memory, disk and each child process every few seconds;
4. after scoring, reads the driver's own report (serials minted, how far
   behind the counters it fell, call latencies), the run database's size
   and row counts, and times the recall questions against that database
   through the service layer - a pallet's contents, a piece's trace, a
   resin lot's where-used, a pallet quarantined with everything in it;
5. writes scorecard + driver report + costs + query timings to one JSON
   under --results.

Speed is the load: at 1x the plant makes 289 stacks a minute, which is the
customer's real rate; at 10x the same hour of line time asks the MES for ten
times that. The driver's `behind_max_s` says whether the serials kept up.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent / "megafactory" / "full"))   # the mega-factory's sampler
sys.path.insert(0, str(HERE.parent / "megafactory"))            # and its tag-map derivation
sys.path.insert(0, str(HERE))

# The ephemeral run's working directory comes from tempfile.mkdtemp. /tmp on
# main is tmpfs, so a serialised hour's database would live in RAM; point the
# whole process at real disk before anything asks for a temp dir.
TMP = HERE / "out" / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMPDIR"] = str(TMP)
tempfile.tempdir = str(TMP)

TABLES = ("serial_units", "unit_inspections", "unit_components", "serial_sequences", "audit_log", "tag_values",
          "equipment_states", "production_logs", "work_orders", "lot_consumptions", "quality_checks", "documents")


def db_stats(url: str) -> dict:
    """Rows and bytes per table, whichever database the run used: SQLite's
    page accounting, or PostgreSQL's relation sizes."""
    from sqlalchemy import text

    from fsmes.db import make_engine

    engine = make_engine(url)
    out: dict = {"url": url.split("@")[-1] if "@" in url else url, "rows": {}, "bytes_by_table": {}}
    with engine.connect() as con:
        if engine.dialect.name == "sqlite":
            db = Path(url.removeprefix("sqlite:///"))
            out["bytes"] = db.stat().st_size if db.exists() else None
            names = {r[0] for r in con.execute(text("select name from sqlite_master where type='table'"))}
            sizes = dict(con.execute(text("select name, sum(pgsize) from dbstat group by name")).all())
            idx = {}
            for name, tbl in con.execute(text("select name, tbl_name from sqlite_master where type='index'")):
                idx.setdefault(tbl, []).append(name)
            for t in TABLES:
                if t in names:
                    out["rows"][t] = con.execute(text(f"select count(*) from {t}")).scalar()
                    out["bytes_by_table"][t] = sizes.get(t, 0) + sum(sizes.get(i, 0) for i in idx.get(t, []))
        else:
            out["bytes"] = con.execute(text("select pg_database_size(current_database())")).scalar()
            for t in TABLES:
                exists = con.execute(text("select to_regclass(:t)"), {"t": t}).scalar()
                if exists:
                    out["rows"][t] = con.execute(text(f"select count(*) from {t}")).scalar()
                    out["bytes_by_table"][t] = con.execute(text("select pg_total_relation_size(:t)"),
                                                           {"t": t}).scalar()
    # Coverage from the record itself: a station's sequence numbers run
    # without gaps when every group it published was recorded, whatever the
    # replay's last periodic report happened to say.
    try:
        with engine.connect() as con:
            gaps = con.execute(text("select coalesce(sum(max_seq - min_seq + 1 - n), 0) from (select equipment_id, "
                                    "max(seq) as max_seq, min(seq) as min_seq, count(*) as n from unit_inspections "
                                    "group by equipment_id) g")).scalar()
        out["seq_gaps"] = int(gaps or 0)
    except Exception as exc:  # a measurement; whatever failed is the finding
        out["seq_gaps"] = f"{type(exc).__name__}: {exc}"[:120]
    engine.dispose()
    return out


def time_queries(url: str, echo=print) -> dict:
    """The recall questions, timed against the run's database through the
    service layer - the same code the API runs, without the HTTP."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from fsmes.db import make_engine
    from fsmes.domain import Material, SerialUnit
    from fsmes.services import coa, serialization

    engine = make_engine(url)
    out: dict = {}
    with Session(engine) as session:
        # The subjects by what they are, not by how their serial starts: a
        # plate's serial starts like a pallet's.
        def latest(material: str) -> str | None:
            return session.scalar(select(SerialUnit.serial).join(Material, Material.id == SerialUnit.material_id)
                                  .where(Material.code == material).order_by(SerialUnit.id.desc()))

        pallet, piece, pack = latest("PALLET"), latest("UT-FORK"), latest("WRAP")
        out["subjects"] = {"pallet": pallet, "piece": piece, "wrap": pack}

        def timed(name: str, fn, *args, **kwargs) -> None:
            t = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
                ms = (time.perf_counter() - t) * 1000
                summary = {}
                if isinstance(result, dict):
                    for key in ("units_inside", "contains_total", "units_affected", "verdict"):
                        if key in result:
                            summary[key] = result[key]
                    if "packages_to_hold" in result:
                        summary["packages"] = len(result["packages_to_hold"])
                    if "components" in result:
                        summary["components"] = [(c["lot"], c["basis"], c["units"]) for c in result["components"]]
                elif isinstance(result, list):
                    summary["units"] = len(result)
                out[name] = {"ms": round(ms, 1), **summary}
                echo(f"  {name}: {ms:.0f} ms {summary}")
            except Exception as exc:  # a measurement; whatever failed is the finding
                out[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
                echo(f"  {name}: failed - {exc}")

        if piece:
            timed("lookup_piece", serialization.get, session, piece)
            timed("trace_back_piece", serialization.trace_back, session, piece)
        if pack:
            timed("contents_pack", serialization.contents, session, pack)
        if pallet:
            timed("contents_pallet", serialization.contents, session, pallet)
            timed("trace_back_pallet", serialization.trace_back, session, pallet)
        timed("where_used_resin_lot", serialization.where_used, session, "LOT-PP-FORK1-001")
        timed("where_used_plate_lot", serialization.where_used, session, "LOT-PLATE-WRAP01-001")
        if pallet:
            timed("pallet_certificate_data", coa.gather_pallet, session, pallet)
            timed("quarantine_pallet_cascade", serialization.set_status, session, pallet, "quarantined",
                  note="recall drill", cascade=True)
            session.rollback()
    engine.dispose()
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--speed", type=float, default=10.0)
    parser.add_argument("--keep", action="store_true", help="keep the run's working directory")
    parser.add_argument("--poll", type=float, default=2.0, help="seconds between the driver's counter reads")
    parser.add_argument("--sample-every", type=float, default=5.0)
    parser.add_argument("--results", type=Path, default=HERE / "out" / "results")
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--sqlite", action="store_true",
                        help="a fresh SQLite file even if the registry names PostgreSQL")
    args = parser.parse_args()

    import build_config
    import make_tag_map
    import run_full
    from run_full import Sampler, summarise

    # The mega-factory's sampler knows its own processes; teach it the
    # database server, which is nobody's child: every PostgreSQL backend
    # serving this run's database, summed as one role.
    _orig = run_full.sample_resources

    def sample_resources(pid: int) -> dict:
        sample = _orig(pid)
        cpu, rss, n = 0.0, 0, 0
        for line in run_full._sh(["ps", "-eo", "pid,ppid,pcpu,rss,args"]).splitlines()[1:]:
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            if parts[4].startswith("postgres:") and "fsmes_run_" in parts[4]:
                cpu += float(parts[2])
                rss += int(parts[3])
                n += 1
            elif parts[1] == str(pid) and "run-operations" in parts[4]:
                sample["procs"].append({"role": "run-operations", "pid": int(parts[0]), "cpu_pct": float(parts[2]),
                                        "rss_mb": round(int(parts[3]) / 1024, 1)})
        if n:
            sample["procs"].append({"role": "postgres", "pid": n, "cpu_pct": round(cpu, 1),
                                    "rss_mb": round(rss / 1024, 1)})
        return sample

    run_full.sample_resources = sample_resources
    # run_full points temp files at its own out/tmp on import; this plant's
    # evidence belongs under its own.
    os.environ["TMPDIR"] = str(TMP)
    tempfile.tempdir = str(TMP)

    from fsmes.sim.generate import generate
    from fsmes.sim.runner import scored_run

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.results.mkdir(parents=True, exist_ok=True)
    result_path = args.results / f"run-{stamp}-{args.speed:g}x.json"

    timings: dict = {}
    config = build_config.build()
    (HERE / "line.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    tag_map = make_tag_map.build(HERE / "line.json")
    (HERE / "tag_map.json").write_text(json.dumps(tag_map, indent=2) + "\n", encoding="utf-8")
    n_lines = len(config["lines"])
    n_stations = sum(len(ln["stations"]) for ln in config["lines"])
    print(f"plant: {n_lines} lines, {n_stations} stations; {config['_plant']}")

    out_dir = HERE / "out"
    if not args.skip_generate:
        t = time.perf_counter()
        generate(HERE / "line.json", out_dir)
        timings["generate_s"] = round(time.perf_counter() - t, 1)

    cfg = {
        "replay_dir": str(out_dir),
        "tag_map": str(HERE / "tag_map.json"),
        "init": str(HERE / "init.py"),
        "api_port": 0,
        "opc_port": 0,
    }

    # The large plant's database for this run: made fresh, named for the run,
    # dropped afterwards unless --keep. The registry's URL says where.
    import tomllib

    registry = tomllib.loads((HERE / "registry.toml").read_text(encoding="utf-8"))["plants"]["cutlery"]
    # The floor's cadence is the registry's, so the scored hour inspects the
    # way the standing plant does.
    for key in ("inspect_every", "issue_every", "inspect_all"):
        if registry.get(key) is not None:
            cfg[key] = registry[key]
    database_url = None
    run_db_name = None
    if registry.get("database_url") and not args.sqlite:
        from fsmes import plant as plants
        base_url = plants.database_url("cutlery", registry, REPO)
        run_db_name = f"fsmes_run_{stamp.lower()}_{args.speed:g}x".replace(".", "_")
        admin_url = base_url.rsplit("/", 1)[0] + "/postgres"
        _pg(admin_url, f'create database "{run_db_name}"')
        database_url = base_url.rsplit("/", 1)[0] + "/" + run_db_name
        print(f"database: {run_db_name} on {database_url.split('@')[-1]}")

    baseline = sample_resources(os.getpid())
    print(f"baseline: mem used {baseline['mem_used_mb']} MB, avail {baseline['mem_avail_mb']} MB, "
          f"disk free {baseline['disk_free_mb']} MB")
    sampler = Sampler(os.getpid(), args.sample_every)
    sampler.start()
    t = time.perf_counter()
    card = scored_run("cutlery", cfg, REPO, speed=args.speed, line_json=HERE / "line.json",
                      keep_evidence=True, echo=print, database_url=database_url)
    timings["run_wall_s"] = round(time.perf_counter() - t, 1)
    sampler.stop.set()
    sampler.join(timeout=10)

    workdir = Path(card["evidence_dir"])
    run_url = database_url or f"sqlite:///{workdir / 'run.db'}"
    db = db_stats(run_url)
    # What the replay emitted and what the agent made of it, from their own
    # reports: every id here entered as an observed inspection group.
    inspection = inspection_report(workdir / "logs")
    print("recall questions against the run's database:")
    queries = time_queries(run_url, echo=print)

    sim_hours = card.get("duration_s", 3600) / 3600
    plant = config["_plant"]
    result = {
        "stamp": stamp, "speed": args.speed, "lines": n_lines, "stations": n_stations,
        "plant": plant,
        "timings": timings,
        "resources": summarise(sampler.samples, baseline),
        "db": {**db, "mb_per_sim_hour": round((db["bytes"] or 0) / 1e6 / sim_hours, 1),
               "rows_per_sim_hour": {k: round(v / sim_hours) for k, v in (db.get("rows") or {}).items()}},
        "inspection": inspection,
        "queries": queries,
        "scorecard": card,
        "samples": sampler.samples,
    }
    ids = (db.get("rows") or {}).get("serial_units", 0)
    result["projection"] = {
        "ids_this_sim_hour": ids,
        "ids_per_day_at_this_rate": round(ids / sim_hours * 24),
        "target_ids_per_day": plant["ids_per_day"],
        "inspections_this_sim_hour": (db.get("rows") or {}).get("unit_inspections", 0),
        "db_gb_per_day_at_this_rate": round((db["bytes"] or 0) / 1e9 / sim_hours * 24, 2),
    }
    result_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    m = card["metrics"]
    print("")
    print(f"  {n_stations} stations at {args.speed:g}x: wall {timings['run_wall_s']} s")
    print(f"  breakdown_recall {m.get('breakdown_recall')} ({m.get('faults_scored')}/{m.get('faults_scripted')}), "
          f"planned_stop_misclassified {m.get('planned_stop_misclassified')}, "
          f"pipeline sustained {card['pipeline'].get('sustained')}")
    print(f"  inspection: {inspection.get('summary')}")
    print(f"  projection: {result['projection']}")
    r = result["resources"]
    print(f"  memory added at peak {r['mem_added_mb_peak']} MB; processes: "
          + ", ".join(f"{k} cpu mean {v['cpu_mean']}% max {v['cpu_max']}% rss {v['rss_max_mb']} MB"
                      for k, v in r["processes"].items()))
    print(f"  database {round((db['bytes'] or 0) / 1e6, 1)} MB ({result['db']['mb_per_sim_hour']} MB/sim-hour), "
          f"rows {db.get('rows')}")
    print(f"  results -> {result_path}")
    if args.keep:
        print(f"  evidence kept at {workdir}" + (f", database {run_db_name}" if run_db_name else ""))
    else:
        shutil.rmtree(workdir, ignore_errors=True)
        if run_db_name:
            _pg(admin_url, f'drop database "{run_db_name}"')


def _pg(admin_url: str, statement: str) -> None:
    """One statement on the server, outside a transaction (CREATE/DROP DATABASE)."""
    from sqlalchemy import create_engine, text

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as con:
        con.execute(text(statement))
    engine.dispose()


def inspection_report(log_dir: Path) -> dict:
    """The replay's last 'inspection groups emitted' line and the agent's last
    'inspection ingestion' line, plus the agent's worst lag: the two ends of
    the pipeline every id crossed."""
    import json as _json

    from fsmes.sim.runner import log_files

    def last(path: Path, event: str) -> dict | None:
        found = None
        for part in log_files(path):
            if not part.exists():
                continue
            for line in part.read_text(encoding="utf-8", errors="replace").splitlines():
                if event in line:
                    try:
                        found = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
        return found

    emitted = last(log_dir / "opc-replay.jsonl", "inspection groups emitted") or {}
    ingested = last(log_dir / "opc-agent.jsonl", "inspection ingestion") or {}
    out = {"emitted": {k: emitted.get(k) for k in ("events", "failed", "starved")},
           "ingested": {k: ingested.get(k) for k in ("events", "units", "partial", "duplicates", "unknown_members",
                                                     "batches", "batch_max", "ingest_mean_ms", "ingest_max_ms",
                                                     "pending_groups")}}
    e, i = emitted.get("events"), ingested.get("events")
    out["summary"] = (f"{e:,} groups emitted, {i:,} ingested ({(i / e * 100) if e else 0:.2f}%), "
                      f"{ingested.get('partial', 0)} partial, {ingested.get('unknown_members', 0)} unknown members, "
                      f"ingest mean {ingested.get('ingest_mean_ms')} ms, max {ingested.get('ingest_max_ms')} ms"
                      if e and i else "no inspection reports found")
    return out


if __name__ == "__main__":
    main()
