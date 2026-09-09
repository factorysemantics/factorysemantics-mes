"""Build, execute and export the cutlery run analysis notebook.

    python3 labs/cutlery/analysis/build_notebook.py [--no-execute] [--vault <obsidian vault dir>]

The notebook is generated from this file so every figure's data path is
visible in the notebook itself (a reader sees exactly where each number
comes from), and so the whole thing re-runs from the kept evidence with no
figure typed by hand. Outputs:

    labs/cutlery/analysis/cutlery_run_analysis.ipynb     the notebook, outputs cleared
    labs/cutlery/out/analysis/cutlery_run_analysis.ipynb executed copy
    labs/cutlery/out/analysis/cutlery_run_analysis.html  interactive export
    labs/cutlery/out/analysis/figures/*.png              print copies
    labs/cutlery/out/results/scaling.json                the one derived number, with its inputs
    <vault>/labs/Cutlery Run Analysis.md + attachments   the vault note

The evidence is what a scored run leaves: its results file, its kept
PostgreSQL database, the agent's and the replay's own reports in the run's
logs, and the resource samples. Nothing here is typed in.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import viz  # noqa: E402

cells: list = []
FIGS: list[tuple[str, str]] = []   # (figure name, caption) in notebook order, for the vault note


def md(text: str) -> None:
    cells.append(new_markdown_cell(text.strip("\n")))


def code(text: str) -> None:
    cells.append(new_code_cell(text.strip("\n")))


def fig(name: str, caption: str) -> None:
    FIGS.append((name, caption))


# ---------------------------------------------------------------- numbers for the prose
RUNS = viz.runs()
R = viz.results()
BY = viz.by_speed()
SPEEDS = sorted(R)
r1 = R.get(1, {})
P = viz.plant()["_plant"]


def insp(r, side, key, default=None):
    return ((r.get("inspection") or {}).get(side) or {}).get(key, default)


def cpu(r, role="run-opc-agent"):
    return ((r.get("resources") or {}).get("processes") or {}).get(role, {}).get("cpu_mean")


def q(r, name, key="ms"):
    return ((r.get("queries") or {}).get(name) or {}).get(key)


def rate(r):
    """Inspection groups per second of wall clock the agent took in."""
    e = insp(r, "ingested", "events") or 0
    dur = (r.get("scorecard") or {}).get("duration_s") or 3600
    return e / (dur / float(r.get("speed") or 1))


def kept_up(r):
    return viz.kept_up(r)


# ================================================================= the notebook
md(f"""
# Sixty million ids a day, every one observed: what the MES did, measured

**A data-driven analysis of the cutlery plant simulation** — for the operations and quality people
who need to know how a vision station's verdict becomes a record, how a pallet's certificate is
built and what it says, and for the IT people who need to know what systems were exercised, how
hard, and where they bend.

Every figure below is computed in this notebook from the evidence a scored run leaves on disk:
the run's own PostgreSQL database, the OPC agent's and the replay's own reports in the run's logs,
the resource samples, and the simulator's per-second truth. Nothing is typed in; re-run the
notebook and the figures re-derive.

> **The question:** a disposable-cutlery manufacturer makes ten million forks, ten million spoons
> and ten million knives a day, stacks them, wraps each stack onto a plate and palletizes the
> wraps. Every piece, stack, plate and wrap carries its own id — {P['ids_per_day']:,} ids a day —
> and a vision station judges every piece and every stack on four attributes. Quality measures
> five dimensions per utensil every fifteen minutes, and each pallet leaves with a certificate of
> analysis: the Cpk of those dimensions over the window its contents were made, and every wrap,
> stack, plate and piece on it. Can the MES handle it, and what does it cost?
>
> **How the ids enter:** nothing is minted from a count. A station's vision system publishes one
> OPC UA group per unit — the serial, the four attributes, a pass word and, for a stack or a wrap,
> the members — every tag stamped with the same source time. The agent takes the group as one
> event and writes the unit, its inspection and its containment straight into the database, with
> no HTTP in the path. A piece that fails never reaches a stack.
>
> **The answer in one line:** at the real rate the agent took {insp(r1, 'ingested', 'events', 0):,}
> inspection groups in the hour ({rate(r1):,.0f}/s), every one of the {insp(r1, 'emitted', 'events', 0):,}
> the stations published, none partial, at {cpu(r1)}% of one core with the database at
> {cpu(r1, 'postgres')}%; a pallet's certificate — Cpk on fifteen characteristics and the listing of
> its {q(r1, 'contents_pallet', 'units_inside') or 0:,} units — renders in {q(r1, 'pallet_certificate_data')} ms.

Hardware for every number: one desktop (Intel i5-12600K, 16 GB, WD Blue SN570 NVMe), PostgreSQL 18
as a user service on the same machine, one database per run, the API one uvicorn process. *Speed*
is the load multiplier: 1x is the customer's real rate; 10x asks the MES for ten times that in the
same hour of line time. Each speed was run at least twice, as the simulation report standard asks.
""")

code("""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
import plotly.graph_objects as go

sys.path.insert(0, str(Path("labs/cutlery/analysis").resolve()) if Path("labs/cutlery/analysis").is_dir() else str(Path(".").resolve()))
import viz
viz.style_matplotlib(); viz.plotly_template()

RUNS = viz.runs()                 # every scored run, oldest first
R = viz.results()                 # {speed: the latest run at that speed}
BY = viz.by_speed()               # {speed: [every run at that speed]}
SPEEDS = sorted(R)
PLANT = viz.plant(); P = PLANT["_plant"]
DB = {s: viz.engine(r) for s, r in R.items()}     # kept databases (None when discarded)
E1 = DB.get(1)

prov = []
for r in RUNS:
    prov.append({"results file": r["_file"], "speed": f"{r['speed']:g}x", "stamp": r["stamp"],
                 "evidence": (r["scorecard"].get("evidence_dir") or "(results only)"),
                 "database": (r["db"].get("url") or "").split("/")[-1], "kept": viz.engine(r) is not None})
pd.DataFrame(prov)
""")

md(f"""
## 1. The plant, and what flows through it

Nine moulding lines — three each of forks, spoons and knives — each ending in a vision station that
judges every piece. Thirty-two stackers take a fork, a spoon and a knife from the lines' output and
close a stack that its own vision station judges; two stackers feed every wrapper because a stack
takes twice as long as a wrap; sixteen wrappers seal a stack onto a plate; four palletizers take 240
wraps to a pallet. Every station has a cycle and a scrap rate, and the buffers between them fill and
drain. The customer's arithmetic is {P['ids_per_day']:,} ids a day.
""")

code("""
rows = []
for ln in PLANT["lines"]:
    meta = ln["_meta"]
    for st in ln["stations"]:
        rows.append({"line": ln["name"], "kind": meta["kind"], "station": f"{ln['name']}_{st['name']}",
                     "rate / min": st["rate_per_min"], "takt (s)": round(60 / st["rate_per_min"], 3),
                     "capacity / h": round(st["rate_per_min"] * 60), "scrap %": st["scrap_pct"],
                     "inspects": ", ".join(a["name"] for a in st.get("inspection", {}).get("attributes", [])) or "-"})
stations = pd.DataFrame(rows)
kind_of = dict(zip(stations.station, stations.kind))
KINDS = ["utensil", "stacker", "wrapper", "palletizer"]
summary = (stations.groupby("kind").agg(lines=("line", "nunique"), stations=("station", "count"),
           rate_per_min=("rate / min", "sum"), takt_s=("takt (s)", "median")).reindex(KINDS))
print(json.dumps(P, indent=1))
summary
""")

code("""
# The streams the plant makes, as the customer counts them: per day, per second, and how each enters the MES.
streams = pd.DataFrame([
    {"stream": "forks, spoons, knives", "ids / day": P["pieces_per_type_per_day"] * 3, "carried by": "one OPC group per piece: serial + 4 vision attributes + pass word"},
    {"stream": "stacks", "ids / day": P["stacks_per_day"], "carried by": "one group per stack: serial, its three members, 4 attributes"},
    {"stream": "plates", "ids / day": P["wraps_per_day"], "carried by": "the wrap's group names the plate"},
    {"stream": "wraps", "ids / day": P["wraps_per_day"], "carried by": "one group per wrap: serial, the stack and the plate, 4 attributes"},
    {"stream": "pallets", "ids / day": P["pallets_per_day"], "carried by": "one group per pallet: serial and its 240 wraps"},
])
streams["ids / s"] = (streams["ids / day"] / 86400).round(1)
streams.loc[len(streams)] = {"stream": "all ids", "ids / day": streams["ids / day"].sum(), "carried by": "", "ids / s": round(streams["ids / day"].sum() / 86400, 1)}
streams
""")

code("""
# The plant as ISA-95 sees it: enterprise -> site -> area -> line -> machine, from the run's own equipment table.
eq = viz.query(E1, "select id, code, name, level, parent_id, ideal_cycle_seconds from equipment")
eq["parent"] = eq.parent_id.map(dict(zip(eq.id, eq.code))).fillna("")
level_color = {"enterprise": viz.SEQ[11], "site": viz.SEQ[9], "area": viz.SEQ[7], "work_center": viz.SEQ[5], "work_unit": viz.SEQ[3]}
figi = go.Figure(go.Icicle(labels=eq.code, parents=eq.parent, values=[1] * len(eq),
                           marker=dict(colors=[level_color[l] for l in eq.level], line=dict(color=viz.SURFACE, width=1)),
                           customdata=np.stack([eq.level, eq.name, eq.ideal_cycle_seconds.fillna(0)], axis=1),
                           hovertemplate="<b>%{label}</b><br>%{customdata[1]}<br>%{customdata[0]}<br>rated cycle %{customdata[2]:.3f} s<extra></extra>",
                           branchvalues="remainder", tiling=dict(orientation="v")))
figi.update_layout(title=f"The equipment hierarchy the MES holds (ISA-95): {len(eq)} nodes, {int((eq.level == 'work_unit').sum())} machines", height=560, margin=dict(t=60, l=10, r=10, b=10))
figi.show(); viz.save_plotly(figi, "01_isa95_icicle", height=560)
""")
fig("01_isa95_icicle", "The ISA-95 hierarchy from the equipment table: one enterprise, one site, four areas (Moulding, Stacking, Wrapping, Palletizing), a work centre per line and a work unit per machine, each with its rated cycle.")

md("""
## 2. How an id enters: one group notify, one event, one record

The station is the source of truth. Its vision system decides whether the piece is good, stamps
the serial and the four attribute readings with one source time, and publishes them as one OPC UA
group. The agent subscribes to every inspection tag at full rate, assembles the tags that share a
source time into one event, and writes it in bulk: the unit, its inspection row (the four readings,
the pass word, which attribute failed) and, for a stack or a wrap, the containment of its members.
OPC UA only notifies a value that changed, so a tag that repeats from one event to the next — a
pass word of zero, an empty members field — is filled from what the station last sent; the agent
counts a group whose serial never arrived as *partial* and records it rather than dropping it.
""")

code("""
# The systems and the traffic between them, with the measured rates at 1x on the arrows.
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
r = R[1]; ins = r["inspection"]; rows = r["db"]["rows"]; dur = r["scorecard"].get("duration_s", 3600)
ing = viz.agent_reports(r, "agent ingestion"); readings = ing.readings.sum() / dur if not ing.empty else 0
figsys, ax = plt.subplots(figsize=(13, 6.4)); ax.set_xlim(0, 13); ax.set_ylim(0, 6.4); ax.set_axis_off(); ax.grid(False)
W, H = 3.2, 1.35
boxes = {"sim": (0.4, 4.4, "the stations\\n(OPC UA server replaying the\\nline's per-second truth)", viz.SERIES[2]),
         "agent": (4.9, 4.4, "OPC agent\\n(groups -> events, bulk writes;\\ncounter deltas, states)", viz.SERIES[1]),
         "db": (9.4, 4.4, "PostgreSQL\\n(one database per run,\\nthe same models as SQLite)", viz.SERIES[4]),
         "floor": (0.4, 0.7, "the floor\\n(inspectors, operators,\\na supervisor)", viz.SERIES[3]),
         "api": (4.9, 0.7, "API\\n(FastAPI / uvicorn, one process:\\nscreens, tools, certificates)", viz.SERIES[0]),
         "screens": (9.4, 0.7, "screens and agent tools\\n(people, and agents on\\ntheir behalf)", viz.NEUTRAL)}
for k, (x, y, text, c) in boxes.items():
    ax.add_patch(FancyBboxPatch((x, y), W, H, boxstyle="round,pad=0.02,rounding_size=0.12", facecolor=c, edgecolor=viz.SURFACE, alpha=0.95))
    ax.text(x + W / 2, y + H / 2, text, ha="center", va="center", fontsize=8.8, color="white" if c != viz.NEUTRAL else viz.INK)
def arrow(a, b, label, tx, ty, rad=0.0, ha="center"):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=14, color=viz.INK2, linewidth=1.2, connectionstyle=f"arc3,rad={rad}"))
    ax.text(tx, ty, label, ha=ha, va="center", fontsize=8.2, color=viz.INK2, linespacing=1.3)
arrow((0.4 + W, 5.1), (4.9, 5.1), f"OPC UA subscription\\n{ins['ingested']['events'] / dur:,.0f} inspection groups/s\\n+ {readings:,.0f} counter/state readings/s", 4.25, 5.95)
arrow((4.9 + W, 5.1), (9.4, 5.1), f"bulk inserts: {rows.get('serial_units', 0) / dur:,.0f} units/s,\\n{rows.get('unit_inspections', 0) / dur:,.0f} inspections/s,\\n{ins['ingested']['ingest_mean_ms']} ms a batch", 8.75, 5.95)
arrow((0.4 + W, 1.4), (4.9, 1.4), f"HTTP: {rows.get('quality_checks', 0)} dimensional checks,\\n{rows.get('production_logs', 0):,} bookings read back", 4.25, 2.35)
arrow((4.9 + W / 2, 0.7 + H), (4.9 + W / 2, 4.4), f"reads for every screen;\\nthe pallet certificate\\nin {r['queries'].get('pallet_certificate_data', {}).get('ms')} ms", 6.65, 2.9, ha="left")
arrow((9.4, 1.4), (4.9 + W, 1.4), "paged lists, filters,\\nthe Floor summary", 8.75, 2.35)
ax.set_title(f"What was simulated and what flowed between the parts at the real rate (1x): {ins['ingested']['events']:,} groups in the hour", fontweight="bold")
viz.save(figsys, "02_systems"); plt.show()
""")
fig("02_systems", "The processes that stood in for a plant and the measured traffic between them at the real rate: the stations' OPC UA groups into the agent, the agent's bulk writes into PostgreSQL, and the floor and the screens through the API.")

code("""
# The agent's own reports of the inspection path (every ~5 s, cumulative): groups taken, batch sizes, milliseconds a batch, groups waiting.
figa, axes = plt.subplots(2, 2, figsize=(12.5, 6.6), sharex=True, constrained_layout=True)
for s in SPEEDS:
    for k, r in enumerate(BY[s]):
        d = viz.agent_reports(r, "inspection ingestion")
        if d.empty: continue
        c = viz.SPEED_COLOR[s]; ls = "-" if k == 0 else ":"
        per_s = d.events.diff() / d.wall_s.diff()
        axes[0, 0].plot(d.t, per_s, color=c, linestyle=ls, linewidth=1.3, label=f"{s}x" + (" (repeat)" if k else ""))
        axes[0, 1].plot(d.t, d.batch_max, color=c, linestyle=ls, linewidth=1.3)
        axes[1, 0].plot(d.t, d.ingest_mean_ms, color=c, linestyle=ls, linewidth=1.3)
        axes[1, 1].plot(d.t, d.pending_groups, color=c, linestyle=ls, linewidth=1.3)
axes[0, 0].set_title("inspection groups taken per second of wall clock"); axes[0, 1].set_title("largest batch written (groups)")
axes[1, 0].set_title("milliseconds per batch, running mean"); axes[1, 1].set_title("groups waiting for their tags at the report")
for ax in axes[1]: ax.set_xlabel("minutes of line time")
axes[0, 0].legend(title="speed", ncol=3); figa.suptitle("The inspection path under load: the same hour of line time at each speed", fontweight="bold")
viz.save(figa, "03_inspection_path"); plt.show()
""")
fig("03_inspection_path", "The agent's own reports of the inspection path at each speed: groups taken per second of wall clock, the largest batch it wrote, the running mean of milliseconds per batch, and the groups waiting for their remaining tags when it reported. Dotted lines are the repeat run.")

code("""
# Coverage: what the stations published against what the agent recorded, per run - the number that must be 100%.
# Two readings of it. The replay's tally is its last periodic report, a few seconds before the end, so it can sit
# either side of the agent's; the record itself is exact: every station numbers its groups, and a gap in the
# sequence in unit_inspections is a group that was published and not recorded.
def seq_gaps(r):
    if isinstance(r["db"].get("seq_gaps"), int):
        return r["db"]["seq_gaps"]
    eng = viz.engine(r)
    if eng is None:
        return None
    return int(viz.query(eng, "select coalesce(sum(mx - mn + 1 - n), 0) as g from (select equipment_id, max(seq) as mx, min(seq) as mn, "
                              "count(*) as n from unit_inspections group by equipment_id) t").g[0])
cov = pd.DataFrame([{"run": f"{r['speed']:g}x {r['stamp'][9:15]}", "speed": r["speed"], "emitted": r["inspection"]["emitted"]["events"],
                     "ingested": r["inspection"]["ingested"]["events"], "partial": r["inspection"]["ingested"]["partial"],
                     "duplicates": r["inspection"]["ingested"]["duplicates"], "unknown members": r["inspection"]["ingested"]["unknown_members"],
                     "sequence gaps": seq_gaps(r),
                     "failed pieces": r["inspection"]["emitted"]["failed"], "units": r["inspection"]["ingested"]["units"]} for r in RUNS])
cov["coverage % (replay tally)"] = (cov.ingested / cov.emitted * 100).round(3)
cov["coverage % (record)"] = (cov.ingested / (cov.ingested + cov["sequence gaps"].fillna(0)) * 100).round(4)
figc = go.Figure()
figc.add_bar(x=cov.run, y=cov.emitted, name="groups the stations published", marker_color=viz.SEQ[4], marker_line=dict(color=viz.SURFACE, width=1))
figc.add_bar(x=cov.run, y=cov.ingested, name="groups the agent recorded", marker_color=viz.SEQ[9], marker_line=dict(color=viz.SURFACE, width=1),
             customdata=np.stack([cov["coverage % (record)"], cov.partial, cov.duplicates, cov["unknown members"], cov["sequence gaps"]], axis=1),
             hovertemplate="recorded %{y:,}<br>coverage %{customdata[0]}% by sequence, %{customdata[4]} gaps<br>partial %{customdata[1]}, duplicates %{customdata[2]}, unknown members %{customdata[3]}<extra></extra>")
figc.update_layout(barmode="group", title="Coverage per run: inspection groups published against groups recorded", yaxis_title="groups in the hour", height=420)
figc.show(); viz.save_plotly(figc, "04_coverage", height=420)
cov
""")
fig("04_coverage", "Inspection groups the stations published (the replay's last periodic tally) against groups the agent recorded, per run. Coverage is read from the record: every station numbers its groups, so a gap in the sequence is a published group that was not recorded, and the hover states the gaps with the partial groups, duplicates and unknown members.")

md("""
## 3. What the vision stations judged

Every piece carries four readings and a verdict; every stack and every wrap the same. The readings
are in the inspection row (as a JSON list in the order the station declares), the verdict is a pass
word whose bits name the attribute that failed, so *why* a piece was scrapped is a query.
""")

code("""
# Pass and fail per station kind, and which attribute failed, from the inspection rows.
pf = viz.query(E1, '''select e.code as station, count(*) as n, sum(case when i.passed then 0 else 1 end) as failed
                      from unit_inspections i join equipment e on e.id = i.equipment_id group by e.code''')
pf["kind"] = pf.station.map(kind_of); pf["fail %"] = (pf.failed / pf.n * 100).round(3)
masks = viz.query(E1, '''select e.code as station, i.fail_mask, count(*) as n from unit_inspections i join equipment e on e.id = i.equipment_id
                         where not i.passed group by e.code, i.fail_mask''')
attrs_of = {f"{ln['name']}_{st['name']}": [a["name"] for a in st.get("inspection", {}).get("attributes", [])] for ln in PLANT["lines"] for st in ln["stations"]}
def which(row):
    names = attrs_of.get(row.station, [])
    return ", ".join(n for b, n in enumerate(names) if row.fail_mask & (1 << b)) or "?"
masks["attribute"] = masks.apply(which, axis=1); masks["kind"] = masks.station.map(kind_of)
figf, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 4.6), gridspec_kw={"width_ratios": [1.6, 1]}, constrained_layout=True)
sub = pf[pf.kind == "utensil"].sort_values("station")
ax1.bar(sub.station, sub["fail %"], color=[viz.KIND_COLOR["utensil"]] * len(sub), edgecolor=viz.SURFACE, linewidth=1)
declared = float(stations[stations.kind == "utensil"]["scrap %"].iloc[0])
ax1.axhline(declared, color=viz.AXIS, linestyle=":", linewidth=1)
ax1.annotate("the line's declared scrap rate", xy=(0.01, declared * 1.02), xycoords=("axes fraction", "data"), fontsize=8, color=viz.MUTED)
ax1.set_title("Pieces failing vision, per moulding line (%)"); ax1.tick_params(axis="x", rotation=45, labelsize=8); ax1.grid(axis="x", visible=False)
by_attr = masks[masks.kind == "utensil"].groupby("attribute").n.sum().sort_values()
ax2.barh(by_attr.index, by_attr.values, color=viz.SEQ[8], edgecolor=viz.SURFACE, linewidth=1); ax2.set_title("Which attribute failed (pieces, all lines)"); ax2.grid(axis="y", visible=False)
viz.save(figf, "05_vision_verdicts"); plt.show()
pf.groupby("kind").agg(stations=("station", "count"), inspected=("n", "sum"), failed=("failed", "sum")).reindex(KINDS).assign(**{"fail %": lambda d: (d.failed / d.inspected * 100).round(3)})
""")
fig("05_vision_verdicts", "Left: the share of pieces each moulding line's vision station failed, against the scrap rate the line declares. Right: which of the four attributes the failed pieces failed on, from the pass word's bits.")

code("""
# The readings themselves: a sample of each utensil's four attributes against the station's limits.
samp = {}
for kind in ("FORK", "SPOON", "KNIFE"):
    station = f"{kind}1_Mark"
    df = viz.query(E1, '''select i.values, i.passed from unit_inspections i join equipment e on e.id = i.equipment_id
                          where e.code = :c order by i.id limit 60000''', {"c": station})
    vals = pd.DataFrame(df["values"].apply(lambda v: v if isinstance(v, list) else json.loads(v)).tolist(), columns=attrs_of[station])
    vals["passed"] = df.passed.values
    samp[kind] = vals
figh, axes = plt.subplots(3, 4, figsize=(13, 8.2), constrained_layout=True)
for i, (kind, vals) in enumerate(samp.items()):
    spec = {a["name"]: a for a in next(st for ln in PLANT["lines"] if ln["name"] == f"{kind}1" for st in ln["stations"] if st["name"] == "Mark")["inspection"]["attributes"]}
    for j, name in enumerate(attrs_of[f"{kind}1_Mark"]):
        ax = axes[i, j]; a = spec[name]
        ax.hist(vals[name], bins=70, color=viz.MATERIAL_COLOR[f"UT-{kind}"], edgecolor=viz.SURFACE, linewidth=0.3)
        for lim in (a["min"], a["max"]): ax.axvline(lim, color=viz.STATUS["critical"], linewidth=1, linestyle=":")
        ax.set_title(f"{kind.title()} {name} ({a['unit']}) - {int((vals[name] < a['min']).sum() + (vals[name] > a['max']).sum())} outside", fontsize=9)
        ax.set_yticks([])
figh.suptitle(f"The four vision readings per utensil: {sum(len(v) for v in samp.values()):,} pieces from one line of each kind, limits dotted", fontweight="bold")
viz.save(figh, "06_vision_readings"); plt.show()
""")
fig("06_vision_readings", "The four readings the vision station records on every piece, for the first sixty thousand pieces of one line of each kind, with the station's limits. A reading outside a limit is a failed piece that never reached a stack.")

md("""
## 4. The flow the ids recorded

The containment records say which marker's pieces went into which stacker's stacks, which stacker
fed which wrapper, and what reached which pallet. Because a failing piece is never offered to a
stacker, the number of stacks containing a failed piece is a query with one right answer.
""")

code("""
# Where the pieces went, from the parent links: marker -> stacker -> wrapper -> palletizer, and the buffers at the end of the hour.
ut = viz.query(E1, '''select mk.code as src, sk.code as dst, count(*) as n from serial_units u join serial_units st on st.id = u.parent_id
    join equipment mk on mk.id = u.produced_on_id join equipment sk on sk.id = st.produced_on_id where mk.code like '%_Mark' group by 1, 2''')
sw = viz.query(E1, '''select sk.code as src, wr.code as dst, count(*) * 3 as n from serial_units st join serial_units w on w.id = st.parent_id
    join equipment sk on sk.id = st.produced_on_id join equipment wr on wr.id = w.produced_on_id where sk.code like '%_Stacker' group by 1, 2''')
wp = viz.query(E1, '''select wr.code as src, coalesce(pz.code, 'wraps not yet palletized') as dst, count(*) * 3 as n from serial_units w
    join equipment wr on wr.id = w.produced_on_id left join serial_units pl on pl.id = w.parent_id left join equipment pz on pz.id = pl.produced_on_id
    join materials m on m.id = w.material_id where m.code = 'WRAP' group by 1, 2''')
un = viz.query(E1, '''select sk.code as src, 'stacks not yet wrapped' as dst, count(*) * 3 as n from serial_units st join equipment sk on sk.id = st.produced_on_id
    join materials m on m.id = st.material_id where m.code = 'STACK-3' and st.parent_id is null group by 1''')
labels, idx = [], {}
def node(name):
    if name not in idx: idx[name] = len(labels); labels.append(name)
    return idx[name]
src, dst, val = [], [], []
for df in (ut, sw, un, wp):
    for row in df.itertuples():
        src.append(node(row.src)); dst.append(node(row.dst)); val.append(int(row.n))
def colour(name):
    k = kind_of.get(name); return viz.KIND_COLOR.get(k, viz.NEUTRAL)
figk = go.Figure(go.Sankey(arrangement="snap",
    node=dict(label=labels, color=[colour(n) for n in labels], pad=8, thickness=10, line=dict(color=viz.SURFACE, width=1)),
    link=dict(source=src, target=dst, value=val, color="rgba(137,135,129,0.22)", hovertemplate="%{source.label} -> %{target.label}: %{value:,} pieces<extra></extra>")))
figk.update_layout(title="Where the pieces went, in pieces: the real-time hour's containment records (1x)", height=760, hovermode="closest")
figk.show(); viz.save_plotly(figk, "07_flow_sankey", height=760)
bad = viz.query(E1, '''select count(distinct st.id) as n from serial_units st join serial_units u on u.parent_id = st.id
    join unit_inspections i on i.unit_id = u.id join materials m on m.id = st.material_id where m.code = 'STACK-3' and not i.passed''').n[0]
print(f"{int(ut.n.sum()):,} pieces in stacks; {int(wp[wp.dst != 'wraps not yet palletized'].n.sum()):,} of them on pallets by the end of the hour; "
      f"{int(un.n.sum()) // 3} stacks and {int(wp[wp.dst == 'wraps not yet palletized'].n.sum()) // 3} wraps still in the buffers; stacks containing a failed piece: {bad}.")
""")
fig("07_flow_sankey", "Each marker's pieces into each stacker's stacks, each stacker into its wrapper, each wrapper onto its palletizer, and what was still in the buffers when the hour ended, in pieces, from the containment records.")

code("""
# Dwell from the serials' own timestamps: piece judged -> stack closed -> wrapped -> palletized.
dw = viz.query(E1, '''select u.produced_at as judged, st.produced_at as stacked, w.produced_at as wrapped, pl.produced_at as palletized
    from serial_units u join serial_units st on st.id = u.parent_id left join serial_units w on w.id = st.parent_id left join serial_units pl on pl.id = w.parent_id
    join materials m on m.id = u.material_id where m.code = 'UT-FORK' and mod(u.id, 20) = 0''')
for c in ("judged", "stacked", "wrapped", "palletized"): dw[c] = viz.ts(dw[c])
dw["piece -> stack (s)"] = (dw.stacked - dw.judged).dt.total_seconds()
dw["stack -> wrap (s)"] = (dw.wrapped - dw.stacked).dt.total_seconds()
dw["wrap -> pallet (s)"] = (dw.palletized - dw.wrapped).dt.total_seconds()
figd, axes = plt.subplots(1, 3, figsize=(13, 4))
for ax, col, c in zip(axes, ["piece -> stack (s)", "stack -> wrap (s)", "wrap -> pallet (s)"], viz.SERIES[:3]):
    ax.hist(dw[col].dropna(), bins=60, color=c, edgecolor=viz.SURFACE, linewidth=0.4); ax.set_title(col); ax.set_xlabel("seconds")
figd.suptitle("Dwell along the chain, from the units' own timestamps (every twentieth fork, 1x run)", fontweight="bold")
viz.save(figd, "08_dwell"); plt.show()
dw[["piece -> stack (s)", "stack -> wrap (s)", "wrap -> pallet (s)"]].describe(percentiles=[.5, .9]).round(1).T[["count", "mean", "50%", "90%", "max"]]
""")
fig("08_dwell", "How long a judged fork waited to be stacked, a stack to be wrapped, and a wrap to be palletized, from the timestamps the stations put on the events. The pallet wait is the palletizer collecting 240 wraps.")

md("""
## 5. The dimensional checks, and the certificate a pallet leaves with

Every fifteen minutes an inspector measures five characteristics of each utensil — length, width,
thickness, weight and a shape dimension — through the same API a browser uses. Those checks are
the process's own record of itself while the pieces on a pallet were being made; a pallet's
certificate of analysis states, for each characteristic, the checks inside the pallet's production
window, the Cpk over them (sigma from the mean moving range), and whether the process was stable.
Under that it lists every wrap, the stack and the plate in each, and every piece in each stack,
rendered from the containment record and issued as an immutable document.
""")

code("""
# The fifteen-minute checks through the hour, per characteristic, against their limits.
qc = viz.query(E1, '''select q.ts, q.value, q.result, s.characteristic, s.min_value, s.max_value, m.code as material
    from quality_checks q join quality_specs s on s.id = q.spec_id join materials m on m.id = s.material_id order by q.ts''')
qc["ts"] = viz.ts(qc.ts); t0 = qc.ts.min()
qc["minute"] = (qc.ts - t0).dt.total_seconds() / 60
mats = ["UT-FORK", "UT-SPOON", "UT-KNIFE"]
figq, axes = plt.subplots(len(mats), 5, figsize=(14, 7.6), constrained_layout=True)
for i, mat in enumerate(mats):
    cs = sorted(qc[qc.material == mat].characteristic.unique())[:5]
    for j, c in enumerate(cs):
        ax = axes[i, j]; sub = qc[(qc.material == mat) & (qc.characteristic == c)]
        ax.plot(sub.minute, sub.value, marker="o", markersize=3.5, linewidth=1, color=viz.MATERIAL_COLOR[mat])
        ax.axhline(sub.min_value.iloc[0], color=viz.STATUS["critical"], linestyle=":", linewidth=1); ax.axhline(sub.max_value.iloc[0], color=viz.STATUS["critical"], linestyle=":", linewidth=1)
        ax.set_title(f"{mat[3:].title()} {c} (n={len(sub)})", fontsize=9); ax.set_xlabel("minute" if i == len(mats) - 1 else "")
figq.suptitle(f"The dimensional checks recorded through the hour: {len(qc):,} checks, {qc.characteristic.nunique()} characteristics, limits dotted", fontweight="bold")
viz.save(figq, "09_dimensional_checks"); plt.show()
print(f"{len(qc):,} checks in {qc.ts.dt.floor('15min').nunique()} quarter-hours; {(qc.result == 'fail').sum()} failed.")
""")
fig("09_dimensional_checks", "The five characteristics measured on each utensil every fifteen minutes of line time through the hour, against their specification limits, from the quality checks the floor recorded through the API.")

code("""
# One pallet's certificate, from the same service the API renders it with: the Cpk block and the listing.
from fsmes.services import coa
pallet = viz.query(E1, "select serial from serial_units u join materials m on m.id = u.material_id where m.code = 'PALLET' order by u.id desc limit 1").serial[0]
with viz.session(R[1]) as ses:
    cert = coa.gather_pallet(ses, pallet)
    body = coa.render_pallet(cert, issued_by="notebook", issued_at=pd.Timestamp.utcnow().to_pydatetime(), revision=1, supersedes=None)
cpk = pd.DataFrame(cert["characteristics"])[["material", "characteristic", "unit", "lower_spec", "upper_spec", "n", "mean", "cpk", "cp", "ppk", "stable", "note"]]
print(f"Certificate for {pallet}: window {cert['window']['start']} -> {cert['window']['end']}, {cert['units_inside']:,} units inside, "
      f"{cert['wraps']} wraps, {cert['inspections']['units_inspected']:,} inspected on the way, {cert['inspections']['failed_on_pallet']} failed; "
      f"rendered certificate {len(body):,} characters.")
print(body[:1400] + "\\n...")
cpk.round(3)
""")

code("""
# The same pallet as a sunburst: the pallet, its wraps, each wrap's stack and plate, each stack's pieces.
tree = viz.query(E1, '''
    with recursive down(id, serial, material_id, parent_id, depth) as (
        select id, serial, material_id, parent_id, 0 from serial_units where serial = :s
        union all select u.id, u.serial, u.material_id, u.parent_id, d.depth + 1 from serial_units u join down d on u.parent_id = d.id)
    select d.id, d.serial, d.parent_id, d.depth, m.code as material from down d join materials m on m.id = d.material_id''', {"s": pallet})
parent_serial = dict(zip(tree.id, tree.serial))
tree["parent"] = tree.parent_id.map(parent_serial).fillna("")
figs = go.Figure(go.Sunburst(labels=tree.serial, parents=tree.parent, values=[1] * len(tree), branchvalues="total", maxdepth=3,
                             marker=dict(colors=[viz.MATERIAL_COLOR.get(m, viz.NEUTRAL) for m in tree.material], line=dict(color=viz.SURFACE, width=0.5)),
                             customdata=tree.material, hovertemplate="<b>%{label}</b><br>%{customdata}<br>%{value} units under it<extra></extra>"))
figs.update_layout(title=f"Pallet {pallet}: {tree.depth.max()} levels, {len(tree) - 1:,} units - click a wrap to open it", height=640, margin=dict(t=60, l=10, r=10, b=10))
figs.show(); viz.save_plotly(figs, "10_pallet_sunburst", height=640)
tree.groupby(["depth", "material"]).size().rename("units").reset_index()
""")
fig("10_pallet_sunburst", "One pallet from the real-time hour as the certificate lists it: the pallet at the centre, its wraps, in each wrap a stack and a plate, in each stack a fork, a spoon and a knife. Colour is what the unit is.")

md("""
## 6. Operations: what the line did

The bookings say what each station made, the state intervals say what it was doing, and the
scorecard says whether the MES saw the breakdowns the script put in — with a fault it recorded
after its window withheld rather than scored missed.
""")

code("""
pl = viz.query(E1, '''select p.ts, e.code as machine, p.good_qty, p.scrap_qty from production_logs p join equipment e on e.id = p.equipment_id''')
pl["ts"] = viz.ts(pl.ts); t0 = pl.ts.min()
pl["minute"] = ((pl.ts - t0).dt.total_seconds() // 300) * 5
pl["kind"] = pl.machine.map(kind_of)
panels = [("utensil", "moulding lines (pieces / 5 min)"), ("stacker", "stackers (stacks / 5 min)"), ("wrapper", "wrappers (wraps / 5 min)"), ("palletizer", "palletizers (pallets / 5 min)")]
figp, axes = plt.subplots(2, 2, figsize=(12.5, 7.4), constrained_layout=True)
for ax, (kind, title) in zip(axes.flat, panels):
    sub = pl[pl.kind == kind].groupby(["minute", "machine"]).good_qty.sum().unstack(fill_value=0)
    for m in sub.columns:
        ax.plot(sub.index, sub[m], color=viz.KIND_COLOR[kind], linewidth=1, alpha=0.55 if len(sub.columns) > 8 else 0.9)
    ax.set_title(f"{title}: {len(sub.columns)} stations"); ax.set_xlabel("minutes of line time"); ax.set_ylim(bottom=0)
figp.suptitle("Good production booked per station, five-minute buckets, the real-time hour", fontweight="bold")
viz.save(figp, "11_production_by_station"); plt.show()
ach = pl.groupby("machine").agg(good=("good_qty", "sum"), scrap=("scrap_qty", "sum")).reset_index().merge(stations[["station", "capacity / h", "kind"]], left_on="machine", right_on="station")
ach["utilisation %"] = (ach.good / ach["capacity / h"] * 100).round(1)
ach.groupby("kind").agg(stations=("machine", "count"), good=("good", "sum"), scrap=("scrap", "sum"), utilisation_pct=("utilisation %", "mean")).reindex(KINDS).round(1)
""")
fig("11_production_by_station", "Good production booked per station in five-minute buckets across the real-time hour, one line per station. The dips are the scripted breakdowns, micro-stops and the plant-wide changeover.")

code("""
# Machine states: minutes in each state per kind, and the scorecard per run.
st = viz.query(E1, '''select e.code as machine, s.state, s.started_at, s.ended_at from equipment_states s join equipment e on e.id = s.equipment_id''')
st["started_at"] = viz.ts(st.started_at); st["ended_at"] = viz.ts(st.ended_at).fillna(st.started_at.max() + pd.Timedelta(seconds=60))
st["minutes"] = (st.ended_at - st.started_at).dt.total_seconds() / 60; st["kind"] = st.machine.map(kind_of)
share = st.groupby(["kind", "state"]).minutes.sum().unstack(fill_value=0).reindex(KINDS)
share = share.div(share.sum(axis=1), axis=0) * 100
figs2, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.4), gridspec_kw={"width_ratios": [1.2, 1]}, constrained_layout=True)
left = np.zeros(len(share))
for state in ["running", "idle", "setup", "down"]:
    if state in share:
        ax1.barh(share.index, share[state], left=left, color=viz.STATE_COLOR[state], label=state, height=0.6, edgecolor=viz.SURFACE, linewidth=1.5); left = left + share[state].values
ax1.invert_yaxis(); ax1.set_xlabel("% of station-minutes"); ax1.grid(axis="y", visible=False); ax1.legend(ncol=4, loc="lower right", title="state")
ax1.set_title("Share of the hour in each state, per station kind (1x)")
sc = pd.DataFrame([{"run": f"{r['speed']:g}x {r['stamp'][9:15]}", "speed": r["speed"], **{k: r["scorecard"]["metrics"].get(k) for k in ("faults_scripted", "faults_scored", "faults_recorded_late", "breakdown_recall", "planned_stop_misclassified")},
                    "agent lag (s)": r["scorecard"]["pipeline"].get("agent_max_lag_s"), "sustained": r["scorecard"]["pipeline"].get("sustained")} for r in RUNS])
x = np.arange(len(sc)); w = 0.28
ax2.bar(x - w, sc.faults_scripted, w, color=viz.SEQ[3], label="faults scripted", edgecolor=viz.SURFACE)
ax2.bar(x, sc.faults_scored * sc.breakdown_recall.fillna(0), w, color=viz.SEQ[9], label="seen in their window", edgecolor=viz.SURFACE)
ax2.bar(x + w, sc.faults_recorded_late, w, color=viz.STATUS["warning"], label="recorded late (withheld)", edgecolor=viz.SURFACE)
for xi, row in zip(x, sc.itertuples()):
    if row.breakdown_recall is None or pd.isna(row.breakdown_recall):
        ax2.text(xi, 0.4, "verdict\\nwithheld", ha="center", va="bottom", fontsize=7, color=viz.INK2, rotation=90)
ax2.set_xticks(x); ax2.set_xticklabels(sc.run, fontsize=8, rotation=30); ax2.legend(fontsize=8); ax2.set_title("The breakdown scorecard, per run"); ax2.grid(axis="x", visible=False)
viz.save(figs2, "12_states_scorecard"); plt.show()
sc
""")
fig("12_states_scorecard", "Left: the share of the hour each kind of station spent running, idle, in setup or down, from the state intervals the agent recorded. Right: per run, the faults the script put in, the ones the MES recorded inside their window, and the ones it recorded after the window and so withheld from the score. A run whose own pipeline lagged its sample has its verdict withheld outright, which is what the 5x, 8x and 10x runs show.")

md("""
## 7. IT: the systems, the loads, and where they bend

Five processes and a database server stood in for a plant. The **OPC UA server** replayed the
line's per-second truth as live tags and published the inspection groups; the **OPC agent** took
the groups, wrote units and inspections in bulk, booked production from counter deltas and
recorded state changes; **PostgreSQL** held one database for the run; the **API** served the floor,
the screens and the certificates; the **operations loop** was the people.
""")

code("""
# Resources: CPU and memory of each process through each run.
figr, axes = plt.subplots(2, len(SPEEDS), figsize=(13, 6.4), sharey="row", constrained_layout=True, squeeze=False)
for j, s in enumerate(SPEEDS):
    rows = []
    for smp in R[s]["samples"]:
        for p in smp["procs"]:
            rows.append({"t": smp["t"], "role": p["role"], "cpu": p["cpu_pct"], "rss": p["rss_mb"]})
    df = pd.DataFrame(rows); df["t"] = (df.t - df.t.min()) / 60
    for role, sub in df.groupby("role"):
        axes[0, j].plot(sub.t, sub.cpu, color=viz.PROCESS_COLOR.get(role, viz.NEUTRAL), label=role, linewidth=1.3)
        axes[1, j].plot(sub.t, sub.rss, color=viz.PROCESS_COLOR.get(role, viz.NEUTRAL), linewidth=1.3)
    axes[0, j].set_title(f"{s}x - CPU % of one core"); axes[1, j].set_title(f"{s}x - resident memory (MB)")
    axes[1, j].set_xlabel("minutes of wall clock")
axes[0, 0].legend(loc="upper left", fontsize=8)
viz.save(figr, "13_resources"); plt.show()
pd.DataFrame([{"run": f"{r['speed']:g}x {r['stamp'][9:15]}", **{f"{role} cpu %": v["cpu_mean"] for role, v in r["resources"]["processes"].items()},
               "memory added at peak (MB)": r["resources"]["mem_added_mb_peak"]} for r in RUNS]).round(1)
""")
fig("13_resources", "CPU and resident memory of each process through each run, the PostgreSQL backends serving the run summed as one (their resident figures each count the shared buffers, so the sum overstates what the server holds). The agent and the database grow with the inspection rate; the replay grows with the tag rate and is the first thing at its limit past 5x; the API and the floor are flat.")

code("""
# The scaling curve: agent CPU against inspection groups per second, through the runs that kept up.
kept_up = viz.kept_up          # no sequence gap in the record, no partial group
def rate(r):
    return r["inspection"]["ingested"]["events"] / ((r["scorecard"].get("duration_s") or 3600) / r["speed"])
scl = pd.DataFrame([{"run": f"{r['speed']:g}x {r['stamp'][9:15]}", "speed": r["speed"], "groups_s": rate(r), "agent_cpu": r["resources"]["processes"]["run-opc-agent"]["cpu_mean"],
                     "postgres_cpu": r["resources"]["processes"].get("postgres", {}).get("cpu_mean"), "sim_cpu": r["resources"]["processes"]["run-opc-sim"]["cpu_mean"],
                     "ingest_ms_per_batch": r["inspection"]["ingested"]["ingest_mean_ms"], "kept_up": kept_up(r)} for r in RUNS])
ok = scl[scl.kept_up]
slope = float((ok.agent_cpu * ok.groups_s).sum() / (ok.groups_s ** 2).sum())
ceiling = 100 / slope
real = P["inspection_events_per_day"] / 86400
figsc, ax = plt.subplots(figsize=(8.5, 4.8))
ax.plot([0, ceiling * 1.05], [0, ceiling * 1.05 * slope], color=viz.AXIS, linewidth=1, linestyle=":")
labelled = set()
for row in scl.itertuples():
    ax.scatter(row.groups_s, row.agent_cpu, s=90, color=viz.SPEED_COLOR[int(row.speed)], edgecolor=viz.SURFACE, linewidth=1.5, zorder=3)
    key = (int(row.speed), row.kept_up)
    if key in labelled: continue          # one label per speed; the repeats sit on the same point
    labelled.add(key)
    below = not row.kept_up
    ax.annotate(f"{row.speed:g}x" + ("" if row.kept_up else " (fell behind)"), (row.groups_s, row.agent_cpu),
                xytext=(8, -14 if below else 6), textcoords="offset points", fontsize=9)
ax.axhline(100, color=viz.STATUS["critical"], linewidth=1, linestyle=":")
ax.annotate(f"one agent process saturates at ~{ceiling:,.0f} groups/s\\n~ {ceiling / real:.1f} times the customer's rate", xy=(ceiling, 100), xytext=(-170, -60), textcoords="offset points", fontsize=9, arrowprops=dict(arrowstyle="->", color=viz.INK2))
ax.set_xlabel("inspection groups per second"); ax.set_ylabel("agent process CPU, mean % of one core"); ax.set_ylim(0, 110); ax.set_xlim(0, max(ceiling * 1.15, scl.groups_s.max() * 1.12))
ax.set_title("The scaling curve: agent CPU against group rate, fitted through the runs that kept up")
viz.save(figsc, "14_scaling_curve"); plt.show()
scl.round(1)
""")
fig("14_scaling_curve", "Agent CPU against inspection groups per second for every run, with a line fitted through the origin over the runs that kept up. Where the line meets 100% is one agent process's ceiling.")

code("""
# Storage: what each table and its indexes weigh per run, and what a day would.
siz = []
for r in RUNS:
    for t, b in r["db"]["bytes_by_table"].items():
        if r["db"]["rows"].get(t, 0) > 1000:
            siz.append({"run": f"{r['speed']:g}x {r['stamp'][9:15]}", "table": t, "rows": r["db"]["rows"][t], "MB": b / 1e6, "bytes/row": b / r["db"]["rows"][t]})
siz = pd.DataFrame(siz)
top = siz[siz.run == siz.run.iloc[0]].sort_values("MB", ascending=False).table.tolist()[:8]
figz, ax = plt.subplots(figsize=(11, 4.8))
runs_ = list(dict.fromkeys(siz.run)); w = 0.8 / len(runs_)
for k, run in enumerate(runs_):
    sub = siz[siz.run == run].set_index("table").reindex(top)
    ax.bar(np.arange(len(top)) + (k - len(runs_) / 2 + 0.5) * w, sub.MB, w * 0.95, color=viz.SPEED_COLOR[int(float(run.split('x')[0]))], edgecolor=viz.SURFACE, linewidth=0.8, label=run, alpha=0.9 if k % 2 == 0 else 0.6)
ax.set_xticks(np.arange(len(top))); ax.set_xticklabels([f"{t}\\n{int(siz[(siz.run == runs_[0]) & (siz.table == t)].rows.iloc[0]):,} rows" for t in top], fontsize=8)
ax.set_ylabel("MB, table and indexes"); ax.legend(fontsize=8, ncol=3); ax.grid(axis="x", visible=False); ax.set_title("Storage per run: what the large tables and their indexes weigh (PostgreSQL's own accounting)")
viz.save(figz, "15_storage"); plt.show()
proj = pd.DataFrame([{"run": f"{r['speed']:g}x {r['stamp'][9:15]}", "MB this hour": round(r["db"]["bytes"] / 1e6), "ids this hour": r["projection"]["ids_this_sim_hour"],
                      "ids/day at this rate": r["projection"]["ids_per_day_at_this_rate"], "target ids/day": r["projection"]["target_ids_per_day"],
                      "GB/day at this rate": r["projection"]["db_gb_per_day_at_this_rate"]} for r in RUNS])
proj
""")
fig("15_storage", "What the large tables and their indexes weigh after each run's hour, from PostgreSQL's own accounting, and the projection of each run's rate to a day. Units, inspections and their indexes are the day; everything else is master data and bookings.")

code("""
# The recall questions against each run's database, in milliseconds.
ql = pd.DataFrame([{"run": f"{r['speed']:g}x {r['stamp'][9:15]}", **{k: v.get("ms") for k, v in r["queries"].items() if isinstance(v, dict) and "ms" in v}} for r in RUNS]).set_index("run")
figql, ax = plt.subplots(figsize=(11, 4.4))
x = np.arange(len(ql.columns)); w = 0.8 / len(ql)
for k, (run, row) in enumerate(ql.iterrows()):
    ax.bar(x + (k - len(ql) / 2 + 0.5) * w, row.values, w * 0.95, color=viz.SPEED_COLOR[int(float(run.split('x')[0]))], edgecolor=viz.SURFACE, linewidth=0.8, label=run, alpha=0.9 if k % 2 == 0 else 0.6)
ax.set_xticks(x); ax.set_xticklabels(ql.columns, fontsize=8, rotation=20); ax.set_yscale("log"); ax.set_ylabel("ms (log)"); ax.legend(fontsize=8, ncol=3); ax.grid(axis="x", visible=False)
ax.set_title("The recall questions against each run's database: a pallet's contents, its trace, where a lot went, the certificate, a quarantine cascade")
viz.save(figql, "16_queries"); plt.show()
ql
""")
fig("16_queries", "Milliseconds for each recall question against each run's database: what is in a pallet, its trace back, where a resin lot and a plate lot went, the certificate's data, and quarantining a pallet with everything under it.")

# ---------------------------------------------------------------- findings (data-driven prose)
ceiling_guess = None
try:
    ok = [(rate(r), cpu(r)) for r in RUNS if kept_up(r) and cpu(r)]
    slope = sum(c * s for s, c in ok) / sum(s * s for s, _ in ok)
    ceiling_guess = 100 / slope
except Exception:
    pass
real_rate = P.get("inspection_events_per_day", 0) / 86400
fast = max(SPEEDS) if SPEEDS else 1
rf = R.get(fast, {})

md(f"""
## 8. Findings

**For operations and quality**

- Every id is an observed event. The stations published {insp(r1, 'emitted', 'events', 0):,} inspection
  groups in the real-time hour and the agent recorded {insp(r1, 'ingested', 'events', 0):,} of them,
  {insp(r1, 'ingested', 'partial', 0)} partial, {insp(r1, 'ingested', 'duplicates', 0)} duplicate,
  {insp(r1, 'ingested', 'unknown_members', 0)} naming a member it had never seen, and the stations'
  sequence numbers run without a gap. A piece that failed vision is in the record with the attribute it
  failed on, and no stack contains one.
- The dimensional checks are the process's own record: {(r1.get('db', {}).get('rows') or {}).get('quality_checks', 0)} checks in the hour,
  five characteristics per utensil every fifteen minutes, and a pallet's certificate computes its Cpk over
  the checks inside that pallet's window — with the sample size stated, and *no capability* said outright
  when the window holds fewer than the twelve points the method needs.
- The certificate lists everything: {q(r1, 'contents_pallet', 'units_inside') or 0:,} units under one pallet
  serial, each wrap with its stack and its plate, each stack with its fork, spoon and knife, rendered from the
  containment record in {q(r1, 'pallet_certificate_data')} ms and issued immutable.

**For IT**

- At the real rate the plant costs one desktop {cpu(r1)}% of a core on the agent, {cpu(r1, 'postgres')}% on
  PostgreSQL, {cpu(r1, 'run-opc-sim')}% on the replay and {cpu(r1, 'run-api')}% on the API. Memory is flat.
- The inspection path scales with rate until one agent process saturates, at about
  {ceiling_guess and f'{ceiling_guess:,.0f}'} groups a second — roughly {ceiling_guess and real_rate and f'{ceiling_guess / real_rate:.1f}'}
  times the customer's rate. The fastest run here ({fast}x) {'kept up' if kept_up(rf) else 'fell behind'}:
  {insp(rf, 'ingested', 'events', 0):,} of {insp(rf, 'emitted', 'events', 0):,} groups recorded.
  The next step is a second agent process over a partition of the stations, not a bigger box.
- Storage grows {r1.get('db', {}).get('mb_per_sim_hour')} MB an hour at the real rate,
  {(r1.get('projection') or {}).get('db_gb_per_day_at_this_rate')} GB a day; units and inspections with their
  indexes are almost all of it. That is the large plant's tier — PostgreSQL, partitioned by day, closed days
  archived to plain files — and a small plant never needs it.
- The recall questions do not grow with the database except the one that asks where a lot went that
  reached a third of everything made.

**What the simulator cannot say**

- Its stations' vision readings are drawn around a nominal; a real station's distribution has a shape the
  process gives it. The method — limits, pass word, Cpk over a window — does not depend on the shape.
- The OPC UA server is a Python replay. It publishes what the plant would at 1x; at higher speeds the
  emitted count against the expected count says how much of the load it could produce, and only what was
  published can be scored.
""")


# ================================================================= build, execute, export
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-execute", action="store_true")
    parser.add_argument("--vault", type=Path, default=None,
                        help="An Obsidian vault to write the analysis note and figures into (optional).")
    args = parser.parse_args()

    nb = new_notebook(cells=cells, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                                             "language_info": {"name": "python"}})
    path = HERE / "cutlery_run_analysis.ipynb"
    if not args.no_execute:
        from nbclient import NotebookClient
        client = NotebookClient(nb, timeout=1800, kernel_name="python3", resources={"metadata": {"path": str(viz.REPO)}})
        client.execute()
    # The executed copy, outputs and all, lives with the other exports; the
    # committed copy is the same notebook with its outputs cleared, so the
    # repository holds the code and the evidence directory holds the run.
    executed = viz.OUT / "analysis" / "cutlery_run_analysis.ipynb"
    executed.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, executed)
    cleared = nbformat.v4.new_notebook(cells=[c if c.cell_type != "code" else new_code_cell(c.source) for c in nb.cells],
                                       metadata=nb.metadata)
    nbformat.write(cleared, path)
    print(f"notebook -> {executed} ({'not executed' if args.no_execute else 'executed'}), {path} (cleared)")

    # The one derived number the report states - one agent process's ceiling,
    # from the scaling fit - written as a results file with its inputs, so a
    # claim can point at it rather than at an estimate in prose.
    if ceiling_guess is not None:
        fit_points = [{"speed": r["speed"], "groups_per_s": round(rate(r), 1), "agent_cpu_mean_pct": cpu(r),
                       "kept_up": kept_up(r), "results": r["_file"]} for r in RUNS]
        scaling = {
            "what": "One OPC agent process's ceiling for the inspection path, fitted from the scored runs.",
            "method": ("Least-squares line through the origin, agent CPU (mean % of one core) against inspection groups "
                       "per second of wall clock, over the runs whose agent recorded every group the stations published "
                       "with none partial; the ceiling is the rate at which the line reaches 100%."),
            "fit_points": fit_points,
            "slope_cpu_pct_per_group_per_s": round(100 / ceiling_guess, 5),
            "ceiling_groups_per_s": round(ceiling_guess),
            "customer_rate_groups_per_s": round(real_rate, 1),
            "ceiling_over_customer_rate": round(ceiling_guess / real_rate, 1) if real_rate else None,
            "hardware": "Intel i5-12600K, 16 GB, WD Blue SN570 NVMe; PostgreSQL 18 user service; one agent process",
        }
        (viz.RESULTS / "scaling.json").write_text(__import__("json").dumps(scaling, indent=2) + "\n", encoding="utf-8")
        print(f"scaling -> {viz.RESULTS / 'scaling.json'} (ceiling {scaling['ceiling_groups_per_s']} groups/s)")

    if not args.no_execute:
        from nbconvert import HTMLExporter
        exporter = HTMLExporter(template_name="lab")
        exporter.exclude_input_prompt = True
        exporter.exclude_output_prompt = True
        body, _ = exporter.from_notebook_node(nb)
        html = viz.OUT / "analysis" / "cutlery_run_analysis.html"
        html.write_text(body, encoding="utf-8")
        print(f"html -> {html} ({html.stat().st_size / 1e6:.1f} MB)")

    if args.vault and args.vault.is_dir():
        att = args.vault / "labs" / "attachments" / "cutlery-run-analysis"
        att.mkdir(parents=True, exist_ok=True)
        for old in att.glob("*.png"):
            old.unlink()
        lines = ["---", "date: 2026-09-06", "type: lab", "tags: [fsmes, cutlery, analysis, benchmark]",
                 f"source: {path}", "---", "", "# Cutlery Run Analysis", "",
                 "The figures of `labs/cutlery/analysis/cutlery_run_analysis.ipynb`, the data-driven analysis of the",
                 "[[Cutlery 10M Serialisation]] runs - the observed-event plant of [[One Live Plant]] - exported as print",
                 "copies. The interactive version (Plotly, with the pallet sunburst and the flow Sankey clickable) is",
                 "`labs/cutlery/out/analysis/cutlery_run_analysis.html` in the worktree; re-run",
                 "`python3 labs/cutlery/analysis/build_notebook.py` to regenerate everything from the kept evidence.",
                 "Every number here comes from a results file or a run database; none is typed.", ""]
        for name, caption in FIGS:
            src = viz.FIGURES / f"{name}.png"
            if src.exists():
                shutil.copy(src, att / src.name)
                lines += [f"![[labs/attachments/cutlery-run-analysis/{src.name}]]", "", caption, ""]
        note = args.vault / "labs" / "Cutlery Run Analysis.md"
        note.write_text("\n".join(lines), encoding="utf-8")
        print(f"vault note -> {note} ({sum(1 for n, _ in FIGS if (viz.FIGURES / f'{n}.png').exists())} figures)")


if __name__ == "__main__":
    main()
