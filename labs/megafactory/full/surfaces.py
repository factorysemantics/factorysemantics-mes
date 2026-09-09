"""Exercise every surface of a running mega-factory and time it - the UI's
pages and the API calls behind them, the product MCP tools and the sim MCP
tools - so "does the UI/MCP still scale" is answered with numbers.

    python3 labs/megafactory/full/surfaces.py --base http://127.0.0.1:8030 \
        [--prod-mcp http://127.0.0.1:8311/mcp] [--sim-mcp http://127.0.0.1:8301/mcp] \
        [--plant megafactory] [--score 30] [--out results.json]

For each HTTP call: status, milliseconds, bytes, and whether the payload says
it is a page of something bigger (the paging envelope's total/has_more, or a
shown/total pair) - a truncated list that looks complete is the class of lie
the product refuses elsewhere, so an unstated truncation is a finding. For
each MCP tool: milliseconds and the size of what an agent would have to read.
--score launches score_plant through the sim MCP and polls job_status until
it finishes, which is a scored run of the whole factory driven the way an
agent would drive it.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LINE = "FILL1LINE"
MACHINE = "FILL1_Filler"
MATERIAL = "FG-FILL1"

PAGES = [
    "/dashboard", "/dashboard/station", "/dashboard/orders", "/dashboard/quality", "/dashboard/ops",
    "/dashboard/instructions", "/dashboard/admin", "/dashboard/line", "/dashboard/line/3d",
    "/dashboard/machines", "/dashboard/masterdata", "/dashboard/trace", "/dashboard/spc",
    "/dashboard/gauges", "/dashboard/schedule", "/dashboard/maintenance", "/dashboard/coa",
    "/dashboard/adjustments", "/dashboard/triggers", "/dashboard/tags", f"/dashboard/machine/{MACHINE}",
    "/dashboard/analysis",
]

# (path, what screen/tool asks for it, expected plant-wide count key or None)
API = [
    ("/dashboard/summary", "Floor", "machines"),
    (f"/dashboard/summary?line={LINE}", "Floor, one line", "machines"),
    ("/equipment/tree", "Machines / Line pages", None),
    ("/equipment/states", "Station", None),
    ("/equipment/alarms", "Floor / Machines", None),
    ("/equipment/tags", "Engineering tag browser", None),
    (f"/equipment/{MACHINE}", "Machine page", None),
    (f"/equipment/{MACHINE}/tags", "Machine page", None),
    (f"/equipment/{MACHINE}/oee", "Machine page", None),
    (f"/equipment/{MACHINE}/alarms?hours=8", "Machine page", None),
    ("/analysis/lines", "Analysis / Line page", None),
    ("/analysis/oee?hours=8", "Analysis, unnamed", "machines"),
    (f"/analysis/oee?hours=8&line={LINE}", "Analysis, one line", "machines"),
    ("/analysis/timeline?hours=8", "Analysis, unnamed", "machines"),
    ("/analysis/timeline?hours=8&limit=60", "Analysis, unnamed, limit 60", "machines"),
    ("/analysis/downtime?hours=8", "Analysis, unnamed", None),
    (f"/analysis/downtime?hours=8&line={LINE}", "Analysis, one line", None),
    ("/analysis/production?hours=8", "Analysis", None),
    (f"/analysis/tag/{MACHINE}?hours=8", "Machine page trend", "points"),
    ("/workorders", "Orders", "items"),
    ("/workorders?limit=500", "Orders, max page", "items"),
    ("/workorders/summary", "Floor", None),
    ("/workorders/dispatch", "Station", None),
    ("/quality/specs", "Quality workspace", None),
    ("/quality/checks?limit=200", "Quality workspace", "items"),
    ("/quality/checks?limit=500&result=fail", "Quality, failures", "items"),
    ("/quality/nonconformances", "Quality workspace", None),
    (f"/quality/spc/{MATERIAL}/fill_weight", "SPC, variable data", "points"),
    (f"/quality/spc/{MATERIAL}/visual_defect", "SPC, pass/fail data", "points"),
    ("/quality/spc/FG-EXTRUDE1/surface_defect_code", "SPC, categorical data", "points"),
    ("/quality/spc/FG-MACH1/burr_count", "SPC, count data", "points"),
    ("/quality/gauges", "Gauges", None),
    ("/masterdata/equipment", "Master data", None),
    ("/masterdata/equipment?level=work_unit", "Master data, machines", None),
    ("/masterdata/personnel", "Master data, people", None),
    ("/masterdata/materials", "Master data", None),
    ("/masterdata/routings", "Master data", None),
    ("/scheduling/calendar", "Schedule", None),
    ("/scheduling/board?hours=24", "Schedule board", None),
    ("/line/layout", "Line page / 3D", None),
    (f"/line/layout?line={LINE}", "Line page, one line", None),
    ("/line/events?hours=8", "Line page", None),
    ("/line/wip", "Line WIP, unnamed", None),
    (f"/line/wip?line={LINE}", "Line WIP, one line", None),
    ("/execution/lots?limit=200", "Station / Trace", "items"),
    ("/audit?limit=50", "Ops / Admin", None),
    ("/ops/services", "Ops", None),
    ("/ops/retention", "Ops", None),
    ("/ops/activity", "Ops", None),
    ("/metrics", "Ops / Prometheus", None),
    ("/triggers", "Triggers", None),
    ("/triggers/catalog", "Triggers", None),
    ("/adjustments", "Adjustments", None),
    ("/maintenance/due", "Maintenance", None),
    ("/maintenance/plans", "Maintenance", None),
    ("/coa", "Certificates", None),
    ("/design/status", "Design assistant", None),
    ("/admin/capabilities", "Admin", None),
    ("/documents", "Instructions", None),
]

PROD_TOOLS = [
    ("list_plants", {}),
    ("machines", {}),
    ("machine", {"machine": MACHINE}),
    ("machine_tags", {"machine": MACHINE}),
    ("machine_alarms", {"hours": 8}),
    ("people", {}),
    ("quality", {}),
    ("spc_chart", {"material": MATERIAL, "characteristic": "fill_weight"}),
    ("equipment_tree", {}),
    ("line_wip", {}),
    ("line_wip", {"line": LINE}),
    ("browse_tags", {}),
    ("browse_tags", {"machine": MACHINE}),
    ("tag_trend", {"equipment": MACHINE, "hours": 8}),
    ("downtime", {"hours": 8}),
    ("orders", {}),
    ("schedule_board", {"hours": 24}),
    ("audit", {"limit": 30}),
    ("materials", {}),
    ("routings", {}),
    ("plant_calendar", {}),
    ("lots", {}),
    ("maintenance_due", {}),
    ("triggers", {}),
    ("adjustments", {}),
    ("capabilities", {}),
]

SIM_TOOLS = [
    ("list_plants", {}),
    ("describe_line", {}),
    ("list_knobs", {}),
    ("recent_runs", {"limit": 5}),
    ("list_jobs", {}),
]


def http(base: str, path: str, token: str | None, method: str = "GET", body: dict | None = None,
         timeout: float = 120.0) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{base}{path}", data=data, headers=headers, method=method)
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status, ctype = r.read(), r.status, r.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        raw, status, ctype = exc.read(), exc.code, exc.headers.get("content-type", "")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"path": path, "status": 0, "ms": round((time.perf_counter() - t) * 1000), "bytes": 0,
                "error": str(exc)[:120]}
    ms = (time.perf_counter() - t) * 1000
    out = {"path": path, "status": status, "ms": round(ms, 1), "bytes": len(raw)}
    if ctype.startswith("application/json"):
        with contextlib.suppress(json.JSONDecodeError):
            out["json"] = json.loads(raw)
    return out


def describe(payload, count_key: str | None) -> dict:
    """What the payload says about its own completeness."""
    info: dict = {}
    if isinstance(payload, list):
        info["items"] = len(payload)
        info["envelope"] = "bare list (no total, no paging)"
        return info
    if not isinstance(payload, dict):
        return info
    if "total" in payload and "items" in payload:
        info["items"] = len(payload["items"])
        info["total"] = payload["total"]
        info["envelope"] = "paged (total, has_more)" + (" - has_more" if payload.get("has_more") else "")
    if "machines_shown" in payload or "machines_total" in payload:
        info["shown"] = payload.get("machines_shown")
        info["total"] = payload.get("machines_total")
        info["envelope"] = "shown/total stated"
    if count_key and isinstance(payload.get(count_key), list):
        info["items"] = len(payload[count_key])
    if "line" in payload and isinstance(payload["line"], str):
        info["line"] = payload["line"]
    if "truncated" in payload:
        info["truncated"] = payload["truncated"]
    if "error" in payload:
        info["error"] = str(payload["error"])[:120]
    if "detail" in payload:
        info["detail"] = str(payload["detail"])[:120]
    return info


def run_http(base: str, token: str) -> dict:
    pages = []
    for path in PAGES:
        r = http(base, path, token)
        pages.append({k: v for k, v in r.items() if k != "json"})
    api = []
    for path, screen, count_key in API:
        r = http(base, path, token)
        row = {k: v for k, v in r.items() if k != "json"}
        row["screen"] = screen
        row.update(describe(r.get("json"), count_key))
        api.append(row)
    return {"pages": pages, "api": api}


async def run_mcp(url: str, plant: str, tools: list[tuple[str, dict]], plant_arg: bool = True) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    out: dict = {"url": url, "calls": []}
    t0 = time.perf_counter()
    async with streamable_http_client(url) as streams:
        read, write, *_ = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            out["tools_listed"] = len(listed.tools)
            out["tool_names"] = sorted(t.name for t in listed.tools)
            out["schema_chars"] = sum(len(json.dumps(t.model_dump(mode="json"), default=str)) for t in listed.tools)
            out["connect_ms"] = round((time.perf_counter() - t0) * 1000)
            for name, args in tools:
                call_args = dict(args)
                if plant_arg and name not in ("capabilities", "list_plants", "list_knobs", "list_jobs"):
                    call_args = {"plant": plant, **call_args}
                t = time.perf_counter()
                try:
                    res = await asyncio.wait_for(session.call_tool(name, call_args), timeout=180)
                    ms = (time.perf_counter() - t) * 1000
                    text = "".join(getattr(c, "text", "") for c in res.content)
                    failed = bool(getattr(res, "is_error", getattr(res, "isError", False)))
                    row = {"tool": name, "args": args, "ms": round(ms, 1), "chars": len(text),
                           "approx_tokens": len(text) // 4, "is_error": failed}
                    try:
                        payload = json.loads(text) if text else None
                    except json.JSONDecodeError:
                        payload = None
                    if isinstance(payload, dict):
                        if "error" in payload:
                            row["error"] = str(payload["error"])[:160]
                        for key in ("machines", "people", "items", "specs", "checks", "tags", "lines"):
                            if isinstance(payload.get(key), list):
                                row[f"n_{key}"] = len(payload[key])
                        for key in ("total", "shown", "truncated", "has_more", "note"):
                            if key in payload:
                                row[key] = payload[key] if not isinstance(payload[key], str) else payload[key][:120]
                    elif isinstance(payload, list):
                        row["n_items"] = len(payload)
                except (TimeoutError, Exception) as exc:
                    row = {"tool": name, "args": args, "ms": round((time.perf_counter() - t) * 1000),
                           "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                out["calls"].append(row)
    return out


async def score_via_mcp(url: str, plant: str, speed: float, echo=print) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as streams:
        read, write, *_ = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            t = time.perf_counter()
            res = await session.call_tool("score_plant", {"plant": plant, "speed": speed})
            text = "".join(getattr(c, "text", "") for c in res.content)
            started = json.loads(text)
            job = started.get("job")
            echo(f"score_plant -> {started}")
            if not job:
                return {"error": started}
            polls = 0
            while True:
                await asyncio.sleep(10)
                polls += 1
                res = await session.call_tool("job_status", {"job": job, "log_tail": 4})
                status = json.loads("".join(getattr(c, "text", "") for c in res.content))
                echo(f"  job {job}: {status.get('state')} " + " | ".join(status.get("log", [])[-2:]))
                if status.get("state") in ("done", "failed"):
                    break
                if polls > 120:
                    status["error"] = "gave up after 20 minutes"
                    break
            wall = round(time.perf_counter() - t, 1)
            out = {"job": job, "state": status.get("state"), "wall_s": wall, "polls": polls,
                   "result": status.get("result"), "error": status.get("error")}
            run_id = (status.get("result") or {}).get("run_id")
            if run_id:
                t = time.perf_counter()
                res = await session.call_tool("run_scorecard", {"run_id": run_id})
                text = "".join(getattr(c, "text", "") for c in res.content)
                out["run_scorecard"] = {"ms": round((time.perf_counter() - t) * 1000), "chars": len(text)}
                card = json.loads(text)
                out["scorecard_metrics"] = (card.get("card") or card).get("metrics")
                out["scorecard_pipeline"] = (card.get("card") or card).get("pipeline")
            return out


def table(rows: list[dict], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--user", default="ADMIN")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--prod-mcp", default=None)
    parser.add_argument("--sim-mcp", default=None)
    parser.add_argument("--plant", default="megafactory")
    parser.add_argument("--score", type=float, default=None, help="also score_plant via the sim MCP at this speed")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    login = http(args.base, "/auth/login", None, "POST", {"code": args.user, "password": args.password})
    token = (login.get("json") or {}).get("token")
    if not token:
        sys.exit(f"could not sign in at {args.base}: {login}")

    result: dict = {"base": args.base, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    t = time.perf_counter()
    result["http"] = run_http(args.base, token)
    result["http"]["wall_s"] = round(time.perf_counter() - t, 1)

    if args.prod_mcp:
        result["prod_mcp"] = asyncio.run(run_mcp(args.prod_mcp, args.plant, PROD_TOOLS))
    if args.sim_mcp:
        result["sim_mcp"] = asyncio.run(run_mcp(args.sim_mcp, args.plant, SIM_TOOLS))
        if args.score:
            result["sim_mcp"]["score_plant"] = asyncio.run(score_via_mcp(args.sim_mcp, args.plant, args.score))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    print("\n## Pages\n")
    print(table(result["http"]["pages"], ["path", "status", "ms", "bytes"]))
    print("\n## API\n")
    print(table(result["http"]["api"], ["path", "screen", "status", "ms", "bytes", "items", "total",
                                        "envelope", "line", "error", "detail"]))
    for key in ("prod_mcp", "sim_mcp"):
        if key in result:
            r = result[key]
            print(f"\n## {key}: {r.get('tools_listed')} tools, schema {r.get('schema_chars')} chars, "
                  f"connect {r.get('connect_ms')} ms\n")
            print(table(r["calls"], ["tool", "args", "ms", "chars", "approx_tokens", "n_machines",
                                     "n_items", "total", "error"]))
            if r.get("score_plant"):
                print("\nscore_plant via MCP: " + json.dumps(r["score_plant"], default=str)[:1500])
    if args.out:
        print(f"\nwritten to {args.out}")


if __name__ == "__main__":
    main()
