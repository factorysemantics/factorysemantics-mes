"""Run the full mega-factory as one ephemeral scored run and measure what it
cost - the Phase 2 harness.

    python3 labs/megafactory/full/run_full.py [--speed 30] [--keep] [--rate 4]
                                              [--results labs/megafactory/full/out/results]

What it does, in order:

1. rebuilds line.json and tag_map.json (both derived, never hand-edited) and
   regenerates the factory's CSVs, timing the generation;
2. runs the same ephemeral scored run every plant uses (fresh database, ports
   from the reserved ranges, the registry's `post_boot` script seeding the
   workforce and quality system through the API while the hour plays), with
   the run's working directory on real disk rather than /tmp (tmpfs on main);
3. samples memory, disk and each child process's CPU/RSS every few seconds
   while it runs;
4. after scoring, reads the run's database size and row counts (evidence,
   read after the MES is stopped - not a surface the MES exposes), then
   removes the working directory unless --keep;
5. writes scorecard + samples + costs to one JSON under --results.

Never registered in labs/multiplant/plants.toml: the scale test is a
measurement, not a fourth lab plant. Its own registry (labs/megafactory/
registry.toml, used via FSMES_PLANT_REGISTRY) is what the standing
show-and-tell instance and the sim MCP use.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent))   # the spike's make_tag_map
sys.path.insert(0, str(HERE))          # this directory's build_config wins

# The ephemeral run's working directory comes from tempfile.mkdtemp. /tmp on
# main is tmpfs, so a 100-station run's database would live in RAM; point the
# whole process at real disk before anything asks for a temp dir.
TMP = HERE / "out" / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMPDIR"] = str(TMP)
tempfile.tempdir = str(TMP)

TABLES = ("tag_values", "equipment_states", "production_log", "quality_checks",
          "non_conformances", "audit_entries", "tag_alarm_history", "persons", "quality_specs")


def _sh(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


def sample_resources(pid: int) -> dict:
    """free/df plus each child of `pid` that is part of the run."""
    mem = _sh(["free", "-m"]).splitlines()
    disk = _sh(["df", "-m", "/"]).splitlines()
    used = avail = disk_free = None
    if len(mem) > 1:
        cols = mem[1].split()
        used, avail = int(cols[2]), int(cols[-1])
    if len(disk) > 1:
        disk_free = int(disk[1].split()[3])
    procs = []
    for line in _sh(["ps", "-eo", "pid,ppid,pcpu,rss,args"]).splitlines()[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5 or parts[1] != str(pid):
            continue
        args = parts[4]
        for tag in ("run-opc-sim", "run-opc-agent", "run-api", "seed_breadth"):
            if tag in args:
                procs.append({"role": tag, "pid": int(parts[0]), "cpu_pct": float(parts[2]),
                              "rss_mb": round(int(parts[3]) / 1024, 1)})
                break
    return {"t": round(time.time(), 1), "mem_used_mb": used, "mem_avail_mb": avail,
            "disk_free_mb": disk_free, "procs": procs}


class Sampler(threading.Thread):
    def __init__(self, pid: int, every: float) -> None:
        super().__init__(daemon=True)
        self.pid, self.every = pid, every
        self.samples: list[dict] = []
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            self.samples.append(sample_resources(self.pid))
            self.stop.wait(self.every)


def db_stats(db: Path) -> dict:
    out: dict = {"bytes": db.stat().st_size if db.exists() else None}
    for suffix in ("-wal", "-shm"):
        side = db.with_name(db.name + suffix)
        if side.exists():
            out[f"bytes{suffix}"] = side.stat().st_size
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        out["rows"] = {t: con.execute(f"select count(*) from {t}").fetchone()[0]
                       for t in TABLES if t in names}
        con.close()
    except sqlite3.Error as exc:
        out["error"] = str(exc)
    return out


def _count_tags(manifest) -> int | None:
    """However the manifest nests, the number of leaf tag entries."""
    if isinstance(manifest, dict):
        if "kind" in manifest:
            return 1
        return sum(_count_tags(v) or 0 for v in manifest.values()) or None
    if isinstance(manifest, list):
        return sum(_count_tags(v) or 0 for v in manifest) or None
    return None


def summarise(samples: list[dict], baseline: dict) -> dict:
    roles: dict[str, dict] = {}
    for s in samples:
        for p in s["procs"]:
            r = roles.setdefault(p["role"], {"cpu_max": 0.0, "cpu_sum": 0.0, "n": 0, "rss_max_mb": 0.0})
            r["cpu_max"] = max(r["cpu_max"], p["cpu_pct"])
            r["cpu_sum"] += p["cpu_pct"]
            r["n"] += 1
            r["rss_max_mb"] = max(r["rss_max_mb"], p["rss_mb"])
    for r in roles.values():
        r["cpu_mean"] = round(r["cpu_sum"] / r["n"], 1) if r["n"] else None
        del r["cpu_sum"]
    used = [s["mem_used_mb"] for s in samples if s["mem_used_mb"] is not None]
    free = [s["disk_free_mb"] for s in samples if s["disk_free_mb"] is not None]
    base_used = baseline.get("mem_used_mb")
    return {
        "processes": roles,
        "mem_used_mb_baseline": base_used,
        "mem_used_mb_peak": max(used) if used else None,
        "mem_added_mb_peak": (max(used) - base_used) if used and base_used is not None else None,
        "disk_free_mb_baseline": baseline.get("disk_free_mb"),
        "disk_free_mb_min": min(free) if free else None,
        "samples": len(samples),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--speed", type=float, default=30.0)
    parser.add_argument("--keep", action="store_true", help="keep the run's working directory")
    parser.add_argument("--rate", type=float, default=4.0, help="inspections per second (MEGAFACTORY_CHECKS_PER_S)")
    parser.add_argument("--sample-every", type=float, default=5.0)
    parser.add_argument("--results", type=Path, default=HERE / "out" / "results")
    parser.add_argument("--skip-generate", action="store_true")
    args = parser.parse_args()

    import build_config
    import make_tag_map

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
    kinds = build_config.characteristic_kinds(config)
    print(f"factory: {n_lines} lines, {n_stations} stations, {len(tag_map['machines'])} machines in the tag map, "
          f"{sum(len(v) for v in kinds.values())} distinct characteristics")

    out_dir = HERE / "out"
    if not args.skip_generate:
        t = time.perf_counter()
        generate(HERE / "line.json", out_dir)
        timings["generate_s"] = round(time.perf_counter() - t, 1)
        csv_bytes = sum(p.stat().st_size for p in out_dir.glob("*.csv"))
        timings["generated_csv_mb"] = round(csv_bytes / 1e6, 1)
        print(f"generated {n_stations} stations in {timings['generate_s']} s ({timings['generated_csv_mb']} MB of CSV)")

    from fsmes.kernel.tags import MANIFEST_NAME

    manifest = json.loads((out_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    tags_total = _count_tags(manifest)

    cfg = {
        "replay_dir": str(out_dir),
        "tag_map": str(HERE / "tag_map.json"),
        "init": str(HERE / "init.py"),
        "post_boot": str(HERE / "seed_breadth.py"),
        "api_port": 0,
        "opc_port": 0,
    }
    os.environ["MEGAFACTORY_CHECKS_PER_S"] = str(args.rate)

    baseline = sample_resources(os.getpid())
    print(f"baseline: mem used {baseline['mem_used_mb']} MB, avail {baseline['mem_avail_mb']} MB, "
          f"disk free {baseline['disk_free_mb']} MB")
    sampler = Sampler(os.getpid(), args.sample_every)
    sampler.start()
    t = time.perf_counter()
    card = scored_run("megafactory", cfg, REPO, speed=args.speed, line_json=HERE / "line.json",
                      keep_evidence=True, echo=print)
    timings["run_wall_s"] = round(time.perf_counter() - t, 1)
    sampler.stop.set()
    sampler.join(timeout=10)
    after = sample_resources(os.getpid())

    workdir = Path(card["evidence_dir"])
    db = db_stats(workdir / "run.db")
    seed_report = None
    seed_path = workdir / "logs" / "seed_breadth.json"
    if seed_path.exists():
        seed_report = json.loads(seed_path.read_text(encoding="utf-8"))
    log_bytes = sum(p.stat().st_size for p in workdir.rglob("*.log"))

    sim_hours = card.get("duration_s", 3600) / 3600
    result = {
        "stamp": stamp, "speed": args.speed, "lines": n_lines, "stations": n_stations,
        "tags_in_manifest": tags_total,
        "characteristics": {k: sorted(v) for k, v in kinds.items()},
        "timings": timings,
        "resources": summarise(sampler.samples, baseline),
        "after": after,
        "db": {**db, "mb_per_sim_hour": round((db["bytes"] or 0) / 1e6 / sim_hours, 1),
               "rows_per_sim_hour": {k: round(v / sim_hours) for k, v in (db.get("rows") or {}).items()}},
        "log_mb": round(log_bytes / 1e6, 1),
        "seed_breadth": seed_report,
        "scorecard": card,
        "samples": sampler.samples,
    }
    result_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    m = card["metrics"]
    print("")
    print(f"  {n_stations} stations at {args.speed:g}x: wall {timings['run_wall_s']} s")
    print(f"  breakdown_recall {m.get('breakdown_recall')} ({m.get('faults_scored')}/{m.get('faults_scripted')}), "
          f"planned_stop_misclassified {m.get('planned_stop_misclassified')}, "
          f"pipeline sustained {card['pipeline'].get('sustained')} "
          f"(replay behind {card['pipeline'].get('replay_max_behind_s')} s, "
          f"agent lag {card['pipeline'].get('agent_max_lag_s')} s)")
    r = result["resources"]
    print(f"  memory added at peak {r['mem_added_mb_peak']} MB; processes: "
          + ", ".join(f"{k} cpu mean {v['cpu_mean']}% max {v['cpu_max']}% rss {v['rss_max_mb']} MB"
                      for k, v in r["processes"].items()))
    print(f"  run.db {round((db['bytes'] or 0) / 1e6, 1)} MB ({result['db']['mb_per_sim_hour']} MB/sim-hour), "
          f"rows {db.get('rows')}")
    if seed_report:
        ins = seed_report.get("inspections") or {}
        print(f"  people {seed_report['people']['created']}, specs {seed_report['specs']['created']}, "
              f"checks {ins.get('checks')} at {ins.get('checks_per_s')}/s, p95 {ins.get('check_ms_p95')} ms, "
              f"NCs {ins.get('non_conformances')}")
    print(f"  results -> {result_path}")
    if args.keep:
        print(f"  evidence kept at {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
