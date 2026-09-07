"""The people and the quality system of the full mega-factory, through the
public API only - and then the inspectors keep working until told to stop.

    python3 labs/megafactory/full/seed_breadth.py [--once] [--rate 4] [--inspectors 4]

Reads MES_API_HOST / MES_API_PORT from the environment (the runner and
`fsmes plant start` hand every child the plant's MES_* contract), signs in as
the plant's ADMIN for master data and as FLOOR-SIM for inspections, and:

1. defines three shift patterns (POST /scheduling/calendar/shifts),
2. creates the workforce - ~290 people (POST /masterdata/personnel),
3. creates every quality specification (POST /quality/specs), one per
   (line material, characteristic), encoding each characteristic's
   measurement *kind* into the only shape the product offers: a float against
   min/max. What survived that encoding and what did not is written down,
   per kind, in seed_breadth.json beside the run's logs - that record is a
   Phase 2 finding, not a workaround,
4. runs N inspector threads that record checks (POST /quality/checks) across
   every specification at a steady rate until SIGTERM, reporting latency.

Idempotent: a 409 on any master-data POST means "already there" (a standing
plant restarted), counted and moved past. Never touches the database - the
dogfood rule (fsmes.sim.runner's docstring): a scenario this script cannot
express is a gap in the product's own surface, and the record of what it
could not express is the point.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADMIN = ("ADMIN", "admin")
FLOOR = ("FLOOR-SIM", "operator")

SHIFTS = [
    ("DAY", "Day shift", "06:00:00", "14:00:00"),
    ("SWING", "Swing shift", "14:00:00", "22:00:00"),
    ("NIGHT", "Night shift", "22:00:00", "06:00:00"),
]

FIRST = ["Ada", "Ben", "Cara", "Dev", "Eli", "Fay", "Gus", "Hana", "Ivo", "Jun", "Kai", "Lena",
         "Mo", "Nia", "Omar", "Pia", "Quin", "Rui", "Sam", "Tess", "Uma", "Vic", "Wen", "Xan",
         "Yara", "Zed"]
LAST = ["Abara", "Brandt", "Costa", "Dahl", "Ekwueme", "Fischer", "Garcia", "Huang", "Iqbal",
        "Jensen", "Kowalski", "Lindqvist", "Moreau", "Nakamura", "Okafor", "Patel", "Quist",
        "Rossi", "Singh", "Tanaka", "Ueda", "Varga", "Weber", "Xu", "Yilmaz", "Zhou"]


# ------------------------------------------------------------------ http
class Api:
    def __init__(self, base: str) -> None:
        self.base = base
        self.token: str | None = None

    def call(self, method: str, path: str, body: dict | None = None,
             timeout: float = 20.0) -> tuple[int, dict | list | None, float]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(f"{self.base}{path}", data=data, headers=headers, method=method)
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        ms = (time.perf_counter() - t) * 1000
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = {"text": raw[:200].decode(errors="replace")}
        return status, payload, ms

    def login(self, code: str, password: str, attempts: int = 60) -> None:
        for _ in range(attempts):
            try:
                status, payload, _ = self.call("POST", "/auth/login", {"code": code, "password": password})
            except (urllib.error.URLError, OSError, TimeoutError):
                status, payload = 0, None
            if status == 200 and payload:
                self.token = payload["token"]
                return
            time.sleep(1.0)
        raise RuntimeError(f"could not sign in as {code} at {self.base}")


def api_base() -> str:
    from fsmes.config import get_settings

    s = get_settings()
    return f"http://{s.api_host}:{s.api_port}"


# ------------------------------------------------------------ the plant
def load_factory() -> dict:
    return json.loads((HERE / "line.json").read_text(encoding="utf-8"))


def build_people(factory: dict) -> list[dict]:
    """Who works here. Per line and shift: three operators and a line lead.
    Plant-wide per shift: quality inspectors (one per two lines), maintenance
    technicians, a supervisor per four lines. Plus a small day-shift staff."""
    people: list[dict] = []
    n = 0

    def person(code: str, role: str, shift: str, line: str | None) -> None:
        nonlocal n
        name = f"{FIRST[n % len(FIRST)]} {LAST[(n // len(FIRST) + n) % len(LAST)]}"
        n += 1
        people.append({"code": code, "name": name, "role": role, "shift": shift, "line": line})

    lines = [ln["name"] for ln in factory["lines"]]
    for shift, *_ in SHIFTS:
        s = shift[0]
        for line in lines:
            for i in range(1, 4):
                person(f"{line}-{s}-OP{i}", "operator", shift, line)
            person(f"{line}-{s}-LEAD", "line_lead", shift, line)
        for i in range(1, len(lines) // 2 + 1):
            person(f"QC-{s}-{i:02d}", "quality_inspector", shift, None)
        for i in range(1, 5):
            person(f"MAINT-{s}-{i:02d}", "maintenance_tech", shift, None)
        for i in range(1, len(lines) // 4 + 1):
            person(f"SUP-{s}-{i:02d}", "supervisor", shift, None)
    for i in range(1, 4):
        person(f"PLAN-{i:02d}", "planner", "DAY", None)
    for i in range(1, 3):
        person(f"QE-{i:02d}", "quality_engineer", "DAY", None)
    return people


# The encoding of each measurement kind into the product's spec shape
# (material, characteristic, unit, min_value, max_value) - and the honest
# word for what happened to it.
def encode_spec(c: dict) -> tuple[dict, str]:
    kind = c["kind"]
    if kind == "variable":
        return ({"unit": c["unit"], "min_value": c["min"], "max_value": c["max"]},
                "represented: a continuous value against limits, which is what the product models")
    if kind in ("pass_fail", "go_nogo"):
        return ({"unit": "1=pass", "min_value": 1.0, "max_value": 1.0},
                "encoded: pass recorded as 1.0 and fail as 0.0 against limits [1,1]; the check "
                "result is right, but the SPC chart draws an individuals chart on a 0/1 series "
                "and the spec reads as a tolerance nobody set")
    if kind == "count":
        return ({"unit": "count", "min_value": 0.0, "max_value": float(c["max"])},
                "encoded: defects per unit against [0, max]; pass/fail is right, but counts are "
                "c/u-chart data and the individuals chart's limits and Cpk are meaningless on them")
    if kind == "ordinal":
        return ({"unit": "grade", "min_value": 0.0, "max_value": float(c["max"])},
                "encoded: grade against [0, worst acceptable]; pass/fail is right, the chart treats "
                "an ordered scale as a continuous one")
    if kind == "categorical":
        return ({"unit": "code", "min_value": None, "max_value": None},
                "flattened: a defect code has no order and no limits, so it is stored as the code's "
                "index with no limits and every check passes - the category is lost to the MES; "
                "no endpoint accepts a text value or a code list")
    raise ValueError(kind)


def specs_for(factory: dict) -> list[dict]:
    out = []
    for line in factory["lines"]:
        meta = line["_meta"]
        for c in meta["characteristics"]:
            body, verdict = encode_spec(c)
            out.append({"line": line["name"], "material": meta["material"]["code"],
                        "characteristic": c["name"], "kind": c["kind"], "body": body,
                        "verdict": verdict, "spec": c})
    return out


# ---------------------------------------------------------------- seeding
def _post_all(api: Api, path: str, bodies: list[dict], label: str) -> dict:
    created = existed = failed = 0
    latencies = []
    errors: dict[str, int] = {}
    for body in bodies:
        status, payload, ms = api.call("POST", path, body)
        latencies.append(ms)
        if status == 201:
            created += 1
        elif status == 409:
            existed += 1
        else:
            failed += 1
            detail = str((payload or {}).get("detail", payload))[:120] if isinstance(payload, dict) else str(payload)
            errors[f"{status}: {detail}"] = errors.get(f"{status}: {detail}", 0) + 1
    latencies.sort()
    return {"label": label, "requested": len(bodies), "created": created, "existed": existed,
            "failed": failed, "errors": errors,
            "ms_p50": round(latencies[len(latencies) // 2], 1) if latencies else None,
            "ms_p95": round(latencies[int(len(latencies) * 0.95)], 1) if latencies else None,
            "ms_total": round(sum(latencies), 1)}


def seed(api: Api, factory: dict, echo=print) -> dict:
    report: dict = {"base": api.base, "seeded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    report["shifts"] = _post_all(
        api, "/scheduling/calendar/shifts",
        [{"code": c, "name": n, "starts": s, "ends": e, "days": "1111111"} for c, n, s, e in SHIFTS],
        "shift patterns")

    people = build_people(factory)
    report["people"] = _post_all(
        api, "/masterdata/personnel",
        [{"code": p["code"], "name": p["name"], "role": p["role"]} for p in people],
        "personnel")
    report["people"]["roles"] = sorted({p["role"] for p in people})
    report["people"]["not_representable"] = (
        "a person's shift and line/crew are not in the product: POST /masterdata/personnel takes "
        "code, name and a free-text role, and no endpoint assigns a person to a shift pattern, a "
        "work centre or a crew - the workforce model at this headcount is a flat list"
    )

    specs = specs_for(factory)
    report["specs"] = _post_all(
        api, "/quality/specs",
        [{"material": s["material"], "characteristic": s["characteristic"], **s["body"]} for s in specs],
        "quality specifications")
    kinds: dict[str, dict] = {}
    for s in specs:
        k = kinds.setdefault(s["kind"], {"characteristics": set(), "verdict": s["verdict"]})
        k["characteristics"].add(s["characteristic"])
    report["measurement_kinds"] = {
        kind: {"distinct_characteristics": len(v["characteristics"]),
               "characteristics": sorted(v["characteristics"]), "what_happened": v["verdict"]}
        for kind, v in sorted(kinds.items())
    }
    report["distinct_characteristics"] = len({s["characteristic"] for s in specs})
    for block in ("shifts", "people", "specs"):
        r = report[block]
        echo(f"seed_breadth: {r['label']}: {r['created']} created, {r['existed']} existed, "
             f"{r['failed']} failed, p50 {r['ms_p50']} ms, p95 {r['ms_p95']} ms, total {r['ms_total']} ms")
    return report


# ------------------------------------------------------------- inspecting
class Inspectors:
    """N threads recording checks across every specification at a steady
    plant-wide rate. Values come from this script's own distributions, never
    from the MES's models; a handful of characteristics read the line's
    actual analog through the API, so a scripted upstream drift shows up as
    failing downstream checks the same way the washer story did."""

    def __init__(self, api: Api, factory: dict, rate: float, threads: int, seed: int = 11) -> None:
        self.api = api
        self.items = specs_for(factory)
        self.rate = rate
        self.threads = threads
        self.rng = random.Random(seed)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.next_index = 0
        self.latencies: list[float] = []
        self.count = self.fails = self.ncs = 0
        self.errors: dict[str, int] = {}
        self.tag_reads = self.tag_hits = 0
        self.tag_ms: list[float] = []

    def _value(self, item: dict) -> float:
        c, rng = item["spec"], self.rng
        kind = c["kind"]
        if kind == "variable":
            measured = c.get("measured")
            if measured:
                station, analog = measured
                code = f"{item['line']}_{station}"
                status, payload, ms = self.api.call("GET", f"/analysis/tag/{code}?hours=0.05&tag={analog}")
                with self.lock:
                    self.tag_reads += 1
                    self.tag_ms.append(ms)
                if status == 200 and isinstance(payload, dict):
                    points = [p for p in payload.get("points", []) if p.get("mean") is not None]
                    if points:
                        with self.lock:
                            self.tag_hits += 1
                        return round(float(points[-1]["mean"]) + rng.gauss(0.0, 0.05 * (c["max"] - c["min"])), 3)
            mid, half = (c["min"] + c["max"]) / 2, (c["max"] - c["min"]) / 2
            return round(rng.gauss(mid + 0.1 * half, 0.38 * half), 3)
        if kind in ("pass_fail", "go_nogo"):
            return 1.0 if rng.random() < 0.965 else 0.0
        if kind == "count":
            return float(rng.choices([0, 1, 2, 3, 5], weights=[80, 12, 5, 2, 1])[0])
        if kind == "ordinal":
            return float(rng.choices(range(c["top"] + 1), weights=[60, 25, 9, 4, 2, 1][: c["top"] + 1])[0])
        if kind == "categorical":
            codes = c["codes"]
            return float(rng.choices(range(len(codes)), weights=[85] + [15 // (len(codes) - 1)] * (len(codes) - 1))[0])
        raise ValueError(kind)

    def _one(self) -> None:
        with self.lock:
            item = self.items[self.next_index % len(self.items)]
            self.next_index += 1
        value = self._value(item)
        body = {"material": item["material"], "characteristic": item["characteristic"],
                "value": value, "order": f"WO-MF-{item['line']}"}
        try:
            status, payload, ms = self.api.call("POST", "/quality/checks", body)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            with self.lock:
                self.errors[type(exc).__name__] = self.errors.get(type(exc).__name__, 0) + 1
            return
        with self.lock:
            self.latencies.append(ms)
            if status == 201 and isinstance(payload, dict):
                self.count += 1
                if payload.get("result") == "fail":
                    self.fails += 1
                if payload.get("non_conformance"):
                    self.ncs += 1
            else:
                detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
                key = f"{status}: {str(detail)[:80]}"
                self.errors[key] = self.errors.get(key, 0) + 1

    def _worker(self) -> None:
        pause = self.threads / self.rate if self.rate > 0 else 1.0
        while not self.stop.is_set():
            started = time.perf_counter()
            self._one()
            left = pause - (time.perf_counter() - started)
            if left > 0:
                self.stop.wait(left)

    def run(self, echo=print, report_every: float = 15.0) -> dict:
        workers = [threading.Thread(target=self._worker, daemon=True) for _ in range(self.threads)]
        for w in workers:
            w.start()
        started = time.time()
        last = started
        while not self.stop.is_set():
            self.stop.wait(1.0)
            if time.time() - last >= report_every:
                last = time.time()
                echo("seed_breadth: " + json.dumps(self.stats(started)), flush=True)
        for w in workers:
            w.join(timeout=5.0)
        return self.stats(started)

    def stats(self, started: float) -> dict:
        with self.lock:
            lat = sorted(self.latencies)
            tag = sorted(self.tag_ms)
            elapsed = max(time.time() - started, 1e-6)
            return {
                "checks": self.count, "fails": self.fails, "non_conformances": self.ncs,
                "errors": dict(self.errors), "elapsed_s": round(elapsed, 1),
                "checks_per_s": round(self.count / elapsed, 2),
                "check_ms_p50": round(lat[len(lat) // 2], 1) if lat else None,
                "check_ms_p95": round(lat[int(len(lat) * 0.95)], 1) if lat else None,
                "check_ms_max": round(lat[-1], 1) if lat else None,
                "tag_reads": self.tag_reads, "tag_hits": self.tag_hits,
                "tag_ms_p50": round(tag[len(tag) // 2], 1) if tag else None,
                "tag_ms_p95": round(tag[int(len(tag) * 0.95)], 1) if tag else None,
            }


# ------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=None, help="API base URL (default: from MES_API_HOST/PORT)")
    parser.add_argument("--once", action="store_true", help="seed and exit; no inspectors")
    parser.add_argument("--rate", type=float, default=float(os.environ.get("MEGAFACTORY_CHECKS_PER_S", "4")),
                        help="plant-wide inspections per second")
    parser.add_argument("--inspectors", type=int, default=int(os.environ.get("MEGAFACTORY_INSPECTORS", "4")))
    args = parser.parse_args(argv)

    base = args.base or api_base()
    factory = load_factory()
    report_dir = Path(os.environ.get("MES_LOG_DIR", "."))
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "seed_breadth.json"

    def echo(msg: str, flush: bool = True) -> None:
        print(msg, flush=flush)

    admin = Api(base)
    admin.login(*ADMIN)
    t = time.perf_counter()
    report = seed(admin, factory, echo)
    report["seed_wall_s"] = round(time.perf_counter() - t, 1)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    echo(f"seed_breadth: {report['people']['created'] + report['people']['existed']} people, "
         f"{report['distinct_characteristics']} distinct characteristics over "
         f"{report['specs']['requested']} specifications, in {report['seed_wall_s']} s -> {report_path}")
    if args.once:
        return

    floor = Api(base)
    floor.login(*FLOOR)
    inspectors = Inspectors(floor, factory, rate=args.rate, threads=args.inspectors)

    def _stop(signum, frame) -> None:
        inspectors.stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    echo(f"seed_breadth: {args.inspectors} inspectors at {args.rate}/s over {len(inspectors.items)} specifications")
    final = inspectors.run(echo)
    report["inspections"] = final
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    echo("seed_breadth: final " + json.dumps(final))


if __name__ == "__main__":
    main(sys.argv[1:])
