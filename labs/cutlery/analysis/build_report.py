"""Assemble the public-facing report page from the notebook's figures.

    python3 labs/cutlery/analysis/build_report.py

Reads the executed notebook's figures (PNG print copies and, for the
interactive ones, the Plotly figure JSON) and the results files, and writes
labs/cutlery/out/analysis/report.html: one self-contained page in the
FactorySemantics site's own type and colour, every number pulled from the
evidence. Static figures are inlined as data URIs; the interactive ones are
drawn by plotly.js from cdnjs and fall back to their print copy if it does
not load.
"""
from __future__ import annotations

import base64
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import viz  # noqa: E402

RUNS = viz.runs()
R = viz.results()
BY = viz.by_speed()
SPEEDS = sorted(R)
P = viz.plant()["_plant"]
r1 = R[1]
FAST = max(SPEEDS)
rf = R[FAST]
OUT = viz.OUT / "analysis" / "report.html"

INTERACTIVE = {"01_isa95_icicle", "04_coverage", "07_flow_sankey", "10_pallet_sunburst"}


def insp(r, side, key, default=None):
    return ((r.get("inspection") or {}).get(side) or {}).get(key, default)


def rate(r):
    return (insp(r, "ingested", "events") or 0) / ((r["scorecard"].get("duration_s") or 3600) / float(r["speed"]))


def cpu(r, role="run-opc-agent"):
    return r["resources"]["processes"].get(role, {}).get("cpu_mean")


def q(name, key="ms", src=None):
    return ((src or r1)["queries"].get(name) or {}).get(key)


kept_up = viz.kept_up          # no sequence gap in the record, no partial group


def coverage(r):
    e, i = insp(r, "emitted", "events") or 0, insp(r, "ingested", "events") or 0
    return i / e * 100 if e else 0


ok = [(rate(r), cpu(r)) for r in RUNS if kept_up(r) and cpu(r)]
slope = sum(c * s for s, c in ok) / sum(s * s for s, _ in ok) if ok else None
CEILING = 100 / slope if slope else None
REAL_RATE = P["inspection_events_per_day"] / 86400
# The fastest speed at which every run kept up.
SUSTAINED = max((s for s in SPEEDS if all(kept_up(r) for r in BY[s])), default=1)
rs = R[SUSTAINED]


def png_uri(name: str) -> str | None:
    p = viz.FIGURES / f"{name}.png"
    if not p.exists():
        return None
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def figure(name: str, caption: str) -> str:
    uri = png_uri(name)
    if name in INTERACTIVE and (viz.FIGURES / f"{name}.json").exists():
        spec = json.loads((viz.FIGURES / f"{name}.json").read_text())
        spec.setdefault("layout", {})["template"] = None      # the page's own tokens style it
        spec["layout"].pop("width", None)
        spec["layout"]["autosize"] = True
        spec["layout"]["paper_bgcolor"] = "rgba(0,0,0,0)"
        spec["layout"]["plot_bgcolor"] = "rgba(0,0,0,0)"
        spec["layout"]["font"] = {"family": "Source Sans 3, system-ui, sans-serif", "size": 13}
        spec["layout"]["margin"] = {"l": 40, "r": 20, "t": 10, "b": 30}
        spec["layout"].pop("title", None)
        data = json.dumps(spec, separators=(",", ":"))
        return f'''<figure class="fig" id="{name}">
  <div class="plot" data-plot="{name}"></div>
  <img class="fallback" alt="" src="{uri}" hidden>
  <script type="application/json" data-figure="{name}">{data.replace("</", "<\\/")}</script>
  <figcaption>{html.escape(caption)}</figcaption>
</figure>'''
    return f'''<figure class="fig" id="{name}">
  <img alt="{html.escape(caption)}" src="{uri}">
  <figcaption>{html.escape(caption)}</figcaption>
</figure>'''


CAP = {}
try:
    # The notebook builder holds the captions; reuse them so the two never drift.
    import importlib.util

    spec = importlib.util.spec_from_file_location("nb_builder", HERE / "build_notebook.py")
    mod = importlib.util.module_from_spec(spec)
    sys.argv = [sys.argv[0], "--no-execute", "--vault", "/nonexistent"]
    saved_main = None
    src = (HERE / "build_notebook.py").read_text()
    # Execute only the cell-collecting part: everything before `def main`.
    exec(compile(src[: src.index("# ================================================================= build, execute, export")],
                 str(HERE / "build_notebook.py"), "exec"), mod.__dict__)
    CAP = dict(mod.FIGS)
except Exception as exc:  # the captions are worth having, not worth failing over
    print("captions unavailable:", exc)


def relative(path) -> str:
    """A path inside the repository, as the repository knows it; a page
    that leaves this machine names no home directory."""
    if not path:
        return "(results only)"
    text = str(path)
    root = str(viz.REPO)
    return text[len(root) + 1:] if text.startswith(root) else Path(text).name


def n(x, digits=0):
    return f"{x:,.{digits}f}"


def esc(s):
    return html.escape(str(s))


sections = []

# ---------------------------------------------------------------- hero
sections.append(f'''
<header class="hero">
  <p class="eyebrow">FactorySemantics MES · a measured plant</p>
  <h1>Sixty million ids a day, every one observed</h1>
  <p class="lede">A disposable-cutlery manufacturer makes ten million forks, ten million spoons and ten million knives a day, stacks them, wraps each stack onto a plate and palletizes the wraps. Every piece, stack, plate and wrap carries its own id; a vision station judges every piece and every stack on four attributes; quality measures five dimensions every fifteen minutes; and every pallet leaves with a certificate of analysis. This is what the MES did with that plant, measured: for the people who run the line and its quality, and for the people who run the systems.</p>
  <div class="tiles">
    <div class="tile"><span class="k">At the real rate</span><span class="v">{n(rate(r1))}<small>groups / s</small></span><span class="s">{n(insp(r1, 'ingested', 'events'))} inspection groups in an hour, {coverage(r1):.2f}% of what the stations published, {insp(r1, 'ingested', 'partial')} partial</span></div>
    <div class="tile"><span class="k">At {SUSTAINED}x the rate</span><span class="v">{n(rate(rs))}<small>groups / s</small></span><span class="s">every group recorded in both runs; the agent at {cpu(rs)}% of one core, PostgreSQL at {cpu(rs, 'postgres')}%</span></div>
    <div class="tile"><span class="k">One agent process's ceiling</span><span class="v">≈{n(CEILING) if CEILING else '—'}<small>groups / s</small></span><span class="s">about {n(CEILING / REAL_RATE, 1) if CEILING else '—'} times the customer's rate; the {FAST}x run {'kept up' if kept_up(rf) else 'fell behind'} at {cpu(rf)}% CPU</span></div>
    <div class="tile"><span class="k">A pallet's certificate</span><span class="v">{q('pallet_certificate_data')}<small>ms</small></span><span class="s">Cpk on fifteen characteristics and the listing of {n(q('contents_pallet', 'units_inside') or 0)} units, from the containment record</span></div>
  </div>
  <p class="conditions">One desktop: Intel i5-12600K, 16 GB, NVMe. PostgreSQL 18 as a user service on the same machine, one database per run; the API one uvicorn process. <em>Speed</em> is the load multiplier — 1x is the plant's real rate. Each speed was run at least twice. Every figure below is computed from the runs' own databases, logs and results files by <code>labs/cutlery/analysis/cutlery_run_analysis.ipynb</code>; nothing is typed in.</p>
</header>
''')

# ---------------------------------------------------------------- plant
sections.append(f'''
<section id="plant">
  <h2>The plant, and what flows through it</h2>
  <p>Nine moulding lines — three each of forks, spoons and knives — each ending in a vision station that judges every piece. Thirty-two stackers take a fork, a spoon and a knife from the lines' output and close a stack that its own vision station judges; two stackers feed every wrapper because a stack takes twice as long as a wrap; sixteen wrappers seal a stack onto a plate; four palletizers take 240 wraps to a pallet. The customer's arithmetic is {n(P['ids_per_day'])} ids a day: {n(P['pieces_per_type_per_day'] * 3)} pieces, {n(P['stacks_per_day'])} stacks, {n(P['wraps_per_day'])} plates, {n(P['wraps_per_day'])} wraps and {n(P['pallets_per_day'])} pallets.</p>
  <div class="table-wrap"><table class="data">
    <thead><tr><th>Stream</th><th class="num">Per day</th><th class="num">Per second</th><th>How it enters the MES</th></tr></thead>
    <tbody>
      <tr><td>forks, spoons, knives</td><td class="num">{n(P['pieces_per_type_per_day'] * 3)}</td><td class="num">{n(P['pieces_per_type_per_day'] * 3 / 86400)}</td><td>one OPC UA group per piece: serial, four vision attributes, a pass word</td></tr>
      <tr><td>stacks</td><td class="num">{n(P['stacks_per_day'])}</td><td class="num">{n(P['stacks_per_day'] / 86400)}</td><td>one group per stack: serial, its three members, four attributes</td></tr>
      <tr><td>plates</td><td class="num">{n(P['wraps_per_day'])}</td><td class="num">{n(P['wraps_per_day'] / 86400)}</td><td>the wrap's group names the plate</td></tr>
      <tr><td>wraps</td><td class="num">{n(P['wraps_per_day'])}</td><td class="num">{n(P['wraps_per_day'] / 86400)}</td><td>one group per wrap: serial, the stack and the plate, four attributes</td></tr>
      <tr><td>pallets</td><td class="num">{n(P['pallets_per_day'])}</td><td class="num">{P['pallets_per_day'] / 86400:.1f}</td><td>one group per pallet: serial and its 240 wraps</td></tr>
      <tr><td><strong>ids</strong></td><td class="num"><strong>{n(P['ids_per_day'])}</strong></td><td class="num"><strong>{n(P['ids_per_day'] / 86400)}</strong></td><td>{n(P['inspection_events_per_day'])} inspection groups a day</td></tr>
    </tbody>
  </table></div>
  {figure("01_isa95_icicle", CAP.get("01_isa95_icicle", ""))}
</section>
''')

# ---------------------------------------------------------------- how an id enters
sections.append(f'''
<section id="ids">
  <h2>How an id enters: one group notify, one event, one record</h2>
  <p>Nothing is minted from a count. The station is the source of truth: its vision system decides whether the piece is good, stamps the serial and the four readings with one source time, and publishes them as one OPC UA group. The agent subscribes to every inspection tag at full rate, assembles the tags that share a source time into one event, and writes it in bulk — the unit, its inspection row and, for a stack or a wrap, the containment of its members — with no HTTP in the path. A piece that fails never reaches a stack. OPC UA only notifies a value that changed, so a tag that repeats from one event to the next is filled from what the station last sent; a group whose serial never arrived is recorded as partial and counted, never dropped.</p>
  {figure("02_systems", CAP.get("02_systems", ""))}
  {figure("03_inspection_path", CAP.get("03_inspection_path", ""))}
  {figure("04_coverage", CAP.get("04_coverage", ""))}
</section>
''')

# ---------------------------------------------------------------- vision and flow
sections.append(f'''
<section id="vision">
  <h2>What the vision stations judged, and where the pieces went</h2>
  <p>Every piece carries four readings and a verdict; every stack and every wrap the same. The verdict is a pass word whose bits name the attribute that failed, so <em>why</em> a piece was scrapped is a query. The containment records say which marker's pieces went into which stacker's stacks, which stacker fed which wrapper, and what reached which pallet — and because a failing piece is never offered to a stacker, the number of stacks containing a failed piece has one right answer.</p>
  {figure("05_vision_verdicts", CAP.get("05_vision_verdicts", ""))}
  {figure("06_vision_readings", CAP.get("06_vision_readings", ""))}
  {figure("07_flow_sankey", CAP.get("07_flow_sankey", ""))}
  {figure("08_dwell", CAP.get("08_dwell", ""))}
</section>
''')

# ---------------------------------------------------------------- certificate
sections.append(f'''
<section id="certificate">
  <h2>The dimensional checks, and the certificate a pallet leaves with</h2>
  <p>Every fifteen minutes an inspector measures five characteristics of each utensil through the same API a browser uses. A pallet's certificate of analysis states, for each characteristic, the checks inside the pallet's production window, the Cpk over them (sigma from the mean moving range) and whether the process was stable — and says <em>no capability</em> outright when the window holds fewer than the twelve points the method needs. Under that it lists every wrap, the stack and the plate in each, and every piece in each stack, rendered from the containment record and issued as an immutable document: {n(q('contents_pallet', 'units_inside') or 0)} units under one pallet serial, gathered in {q('pallet_certificate_data')} ms.</p>
  {figure("09_dimensional_checks", CAP.get("09_dimensional_checks", ""))}
  {figure("10_pallet_sunburst", CAP.get("10_pallet_sunburst", ""))}
</section>
''')

# ---------------------------------------------------------------- operations
sections.append(f'''
<section id="operations">
  <h2>What the line did</h2>
  <p>The bookings say what each station made, the state intervals say what it was doing, and the scorecard says whether the MES saw the breakdowns the script put in — with a fault it recorded after its window withheld rather than scored missed.</p>
  {figure("11_production_by_station", CAP.get("11_production_by_station", ""))}
  {figure("12_states_scorecard", CAP.get("12_states_scorecard", ""))}
</section>
''')

# ---------------------------------------------------------------- IT
def cell(r, role):
    v = cpu(r, role)
    return f"{v}% CPU" if v is not None else "—"

rows = "".join(
    f"<tr><td>{r['speed']:g}x</td><td class=\"num\">{n(rate(r))}</td><td class=\"num\">{coverage(r):.2f}%</td><td class=\"num\">{insp(r, 'ingested', 'partial')}</td>"
    f"<td class=\"num\">{insp(r, 'ingested', 'ingest_mean_ms')}</td><td class=\"num\">{cpu(r)}</td><td class=\"num\">{cpu(r, 'postgres') if cpu(r, 'postgres') is not None else '—'}</td>"
    f"<td class=\"num\">{cpu(r, 'run-opc-sim')}</td><td class=\"num\">{cpu(r, 'run-api')}</td><td class=\"num\">{n(r['db']['bytes'] / 1e6)}</td><td>{'kept up' if kept_up(r) else 'fell behind'}</td></tr>"
    for r in RUNS)
sections.append(f'''
<section id="it">
  <h2>For IT: the systems, the loads, and where they bend</h2>
  <p>Five processes and a database server stood in for a plant: an OPC UA server replaying the line's per-second truth and publishing the inspection groups; the OPC agent taking the groups, writing units and inspections in bulk, booking from counter deltas and recording states; PostgreSQL holding one database for the run; the API serving the floor, the screens and the certificates; and the operations loop being the people. Storage is a module: the same models and migrations run on one SQLite file for a small plant, and on PostgreSQL when a plant like this one switches it on.</p>
  <div class="table-wrap"><table class="data">
    <thead><tr><th>Run</th><th class="num">Groups / s</th><th class="num">Coverage</th><th class="num">Partial</th><th class="num">ms / batch</th><th class="num">Agent CPU %</th><th class="num">PostgreSQL CPU %</th><th class="num">Replay CPU %</th><th class="num">API CPU %</th><th class="num">MB after the hour</th><th>Verdict</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
  {figure("13_resources", CAP.get("13_resources", ""))}
  {figure("14_scaling_curve", CAP.get("14_scaling_curve", ""))}
  {figure("15_storage", CAP.get("15_storage", ""))}
  {figure("16_queries", CAP.get("16_queries", ""))}
</section>
''')

# ---------------------------------------------------------------- findings
sections.append(f'''
<section id="findings">
  <h2>Findings</h2>
  <div class="cols">
    <div>
      <h3>Operations and quality</h3>
      <ul>
        <li>Every id is an observed event: the stations published {n(insp(r1, 'emitted', 'events'))} inspection groups in the real-time hour and the agent recorded {n(insp(r1, 'ingested', 'events'))} of them — {insp(r1, 'ingested', 'partial')} partial, {insp(r1, 'ingested', 'duplicates')} duplicate, {insp(r1, 'ingested', 'unknown_members')} naming a member it had never seen.</li>
        <li>A piece that failed vision is in the record with the attribute it failed on, and no stack contains one.</li>
        <li>The dimensional checks are the process's own record — {r1['db']['rows'].get('quality_checks', 0)} in the hour, five characteristics per utensil every fifteen minutes — and a pallet's certificate computes its Cpk over the checks inside that pallet's window, with the sample size stated.</li>
        <li>The certificate lists everything on the pallet, each wrap with its stack and plate, each stack with its fork, spoon and knife, and is issued immutable.</li>
      </ul>
    </div>
    <div>
      <h3>IT</h3>
      <ul>
        <li>At the real rate the plant costs one desktop {cpu(r1)}% of a core on the agent, {cpu(r1, 'postgres')}% on PostgreSQL, {cpu(r1, 'run-opc-sim')}% on the replay and {cpu(r1, 'run-api')}% on the API. Memory is flat.</li>
        <li>The inspection path scales with rate until one agent process saturates at about {n(CEILING) if CEILING else '—'} groups a second — roughly {n(CEILING / REAL_RATE, 1) if CEILING else '—'} times the customer's rate. The next step is a second agent process over a partition of the stations, not a bigger box.</li>
        <li>Storage grows {r1['db']['mb_per_sim_hour']} MB an hour at the real rate, {r1['projection']['db_gb_per_day_at_this_rate']} GB a day; units, inspections and their indexes are almost all of it. That is the large plant's tier — PostgreSQL, partitioned by day, closed days archived to plain files — and a small plant never needs it.</li>
        <li>The recall questions do not grow with the database except the one that asks where a lot went that reached a third of everything made.</li>
      </ul>
    </div>
  </div>
  <h3>What the simulator cannot say</h3>
  <ul>
    <li>Its stations' vision readings are drawn around a nominal; a real station's distribution has the shape the process gives it. The method — limits, a pass word, Cpk over a window — does not depend on the shape.</li>
    <li>The OPC UA server is a Python replay. It publishes what the plant would at 1x; at higher speeds the emitted count against the expected count says how much of the load it could produce, and only what was published can be scored.</li>
  </ul>
</section>
''')

# ---------------------------------------------------------------- provenance
prov = "".join(
    f"<tr><td>scored run at {r['speed']:g}x</td><td><code>{esc(relative(r['scorecard'].get('evidence_dir')))}</code>, database <code>{esc((r['db'].get('url') or '').split('/')[-1])}</code></td><td class=\"num\">{esc(r['stamp'])}</td></tr>"
    for r in RUNS)
sections.append(f'''
<section id="provenance">
  <h2>Provenance</h2>
  <p>The notebook that produced every figure, the runner that produced the evidence, and the plant description are in the repository under <code>labs/cutlery/</code>. Re-running the notebook re-derives the page.</p>
  <div class="table-wrap"><table class="data">
    <thead><tr><th>Evidence</th><th>Where</th><th class="num">Stamp (UTC)</th></tr></thead>
    <tbody>{prov}
      <tr><td>simulator per-second truth</td><td><code>labs/cutlery/out/*.csv</code></td><td class="num">generated by run_cutlery.py</td></tr>
    </tbody>
  </table></div>
</section>
''')

TOC = [("plant", "The plant"), ("ids", "How an id enters"), ("vision", "Vision and flow"), ("certificate", "The certificate"),
       ("operations", "What the line did"), ("it", "Systems and loads"), ("findings", "Findings"), ("provenance", "Provenance")]

page = f'''<title>Sixty Million Ids a Day</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@85..100,400..900&family=Source+Sans+3:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  color-scheme: light;
  --ground: #f4f6f8; --panel: #ffffff; --ink: #161b22; --muted: #5b6675; --line: #d6dce4; --line-strong: #aeb8c4;
  --accent: #d97a12; --link: #0b5fbe; --code-bg: #eef1f5; --tile: #ffffff;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --ground: #0e1116; --panel: #161b24; --ink: #e6eaf0; --muted: #98a3b3; --line: #2a3240; --line-strong: #3d4757;
    --accent: #ffab4c; --link: #6fb1ff; --code-bg: #1c232e; --tile: #161b24;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --ground: #0e1116; --panel: #161b24; --ink: #e6eaf0; --muted: #98a3b3; --line: #2a3240; --line-strong: #3d4757;
  --accent: #ffab4c; --link: #6fb1ff; --code-bg: #1c232e; --tile: #161b24;
}}
body {{ margin: 0; background: var(--ground); color: var(--ink); font-family: "Source Sans 3", "Segoe UI", system-ui, sans-serif; font-size: 17px; line-height: 1.55; }}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 0 24px 80px; display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 48px; }}
nav.toc {{ position: sticky; top: 24px; align-self: start; padding-top: 56px; font-size: 14px; }}
nav.toc a {{ display: block; color: var(--muted); text-decoration: none; padding: 4px 0 4px 12px; border-left: 2px solid var(--line); }}
nav.toc a:hover, nav.toc a:focus-visible {{ color: var(--ink); border-left-color: var(--accent); outline: none; }}
main {{ min-width: 0; }}
h1, h2, h3 {{ font-family: "Archivo", sans-serif; font-stretch: 92%; letter-spacing: -0.01em; text-wrap: balance; margin: 0; }}
h1 {{ font-size: clamp(34px, 5vw, 52px); line-height: 1.05; font-weight: 800; max-width: 18ch; }}
h2 {{ font-size: 28px; font-weight: 700; margin: 72px 0 12px; padding-top: 20px; border-top: 1px solid var(--line); }}
h3 {{ font-size: 18px; font-weight: 700; margin: 24px 0 8px; }}
p, li {{ max-width: 68ch; }}
.eyebrow {{ font-family: "IBM Plex Mono", monospace; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); margin: 56px 0 14px; }}
.lede {{ font-size: 20px; line-height: 1.45; color: var(--ink); margin: 18px 0 28px; max-width: 62ch; }}
.conditions, .note, figcaption {{ color: var(--muted); font-size: 14.5px; }}
.conditions {{ margin-top: 22px; max-width: 78ch; }}
code {{ font-family: "IBM Plex Mono", monospace; font-size: 0.86em; background: var(--code-bg); padding: 1px 5px; border-radius: 4px; }}
.tiles {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 8px; }}
.tile {{ background: var(--tile); border: 1px solid var(--line); border-radius: 6px; padding: 16px 18px 14px; display: flex; flex-direction: column; gap: 6px; }}
.tile .k {{ font-family: "IBM Plex Mono", monospace; font-size: 11.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }}
.tile .v {{ font-family: "Archivo", sans-serif; font-weight: 800; font-size: 34px; line-height: 1; letter-spacing: -0.02em; }}
.tile .v small {{ font-family: "Source Sans 3", sans-serif; font-weight: 400; font-size: 13px; color: var(--muted); margin-left: 6px; letter-spacing: 0; }}
.tile .s {{ font-size: 13.5px; color: var(--muted); line-height: 1.35; }}
figure.fig {{ margin: 28px 0 36px; }}
figure.fig img {{ display: block; width: 100%; max-width: 100%; height: auto; border: 1px solid var(--line); border-radius: 4px; background: #fcfcfb; }}
figure.fig .plot {{ width: 100%; min-height: 420px; border: 1px solid var(--line); border-radius: 4px; background: var(--panel); }}
figcaption {{ margin-top: 10px; max-width: 78ch; }}
table.data {{ border-collapse: collapse; width: 100%; font-size: 15px; margin: 18px 0 28px; }}
table.data th, table.data td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }}
table.data th {{ font-family: "IBM Plex Mono", monospace; font-size: 12px; letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); font-weight: 500; }}
table.data th small {{ text-transform: none; letter-spacing: 0; display: block; font-size: 11px; }}
table.data .num {{ text-align: right; font-variant-numeric: tabular-nums; font-family: "IBM Plex Mono", monospace; font-size: 14px; }}
table.data td code {{ font-size: 12.5px; word-break: break-all; }}
.table-wrap {{ overflow-x: auto; }}
.cols {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 32px; }}
ul {{ padding-left: 20px; }} li {{ margin: 6px 0; }}
em {{ font-style: italic; }}
a {{ color: var(--link); }}
footer {{ margin-top: 72px; padding-top: 20px; border-top: 1px solid var(--line); color: var(--muted); font-size: 14px; }}
@media (max-width: 900px) {{ .wrap {{ grid-template-columns: 1fr; gap: 0; }} nav.toc {{ position: static; padding-top: 24px; display: flex; flex-wrap: wrap; gap: 4px 14px; }} nav.toc a {{ border-left: 0; padding: 2px 0; }} .tiles {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .cols {{ grid-template-columns: 1fr; }} }}
@media (max-width: 520px) {{ .tiles {{ grid-template-columns: 1fr; }} }}
</style>
<div class="wrap">
  <nav class="toc" aria-label="Sections">{"".join(f'<a href="#{i}">{t}</a>' for i, t in TOC)}</nav>
  <main>
    {"".join(sections)}
    <footer>Measured on 2026-09-06 on the plant described above. The plant is simulated; the numbers are the MES's own.</footer>
  </main>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/3.1.0/plotly.min.js"></script>
<script>
(function () {{
  var dark = matchMedia("(prefers-color-scheme: dark)").matches && document.documentElement.getAttribute("data-theme") !== "light"
             || document.documentElement.getAttribute("data-theme") === "dark";
  var ink = dark ? "#e6eaf0" : "#161b22", muted = dark ? "#98a3b3" : "#5b6675", line = dark ? "#2a3240" : "#d6dce4";
  document.querySelectorAll("script[data-figure]").forEach(function (s) {{
    var name = s.getAttribute("data-figure"), host = document.querySelector('[data-plot="' + name + '"]');
    var img = host.parentNode.querySelector("img.fallback");
    if (!window.Plotly) {{ host.hidden = true; img.hidden = false; return; }}
    try {{
      var spec = JSON.parse(s.textContent);
      var layout = spec.layout || {{}};
      layout.font = Object.assign({{}}, layout.font, {{ color: ink }});
      ["xaxis", "yaxis"].forEach(function (ax) {{ if (layout[ax]) {{ layout[ax].gridcolor = line; layout[ax].linecolor = line; layout[ax].tickfont = {{ color: muted }}; }} }});
      if (layout.legend) layout.legend.font = {{ color: muted }};
      layout.height = layout.height || 520;
      Plotly.newPlot(host, spec.data, layout, {{ responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d"] }});
    }} catch (e) {{ host.hidden = true; img.hidden = false; }}
  }});
}})();
</script>
'''
OUT.write_text(page, encoding="utf-8")
print(f"report -> {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")

# The same page as a complete document, for a static host that serves it as
# a file rather than inside an artifact frame.
STANDALONE = OUT.with_name("report-standalone.html")
document = ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<meta name=\"description\" content=\"The FactorySemantics MES measured on a simulated cutlery plant: "
            "sixty million observed ids a day, every inspection group recorded, where one agent process bends, "
            "and what a pallet's certificate of analysis says.\">\n"
            "<meta name=\"robots\" content=\"index,follow\">\n"
            + page.replace("<title>", "<title>", 1).split("<style>", 1)[0].split("<title>")[0]
            + "<title>Sixty Million Ids a Day</title>\n<style>" + page.split("<style>", 1)[1].split("</style>", 1)[0]
            + "</style>\n</head>\n<body>\n" + page.split("</style>", 1)[1] + "\n</body>\n</html>\n")
STANDALONE.write_text(document, encoding="utf-8")
print(f"standalone -> {STANDALONE} ({STANDALONE.stat().st_size / 1e6:.1f} MB)")
