"""The run as one page somebody can read without the tooling that made it.

Self-contained on purpose: no stylesheet, no script, no font and no image
fetched from anywhere, because the point of a results directory is that it can
be copied onto a memory stick, attached to a mail or opened in six months on a
machine with no network. The shape is the cutlery analysis report's - a
conditions line, tables first, the differences up front, every number from the
evidence beside it - rendered from `scores.json` rather than from a notebook.

What it will not do:

* **Average away an unknown.** A measurement that came back `null` is printed
  as *unknown* with the reason the run gave, never as a zero or a dash.
* **Say who is right.** Where the MES and the truth disagree the page prints
  both and the difference. The script is ground truth by construction, so it
  is labelled as such - but a difference inside the replay's own overlap band,
  or smaller than the window mismatch, is printed as exactly that.
* **Draw a chart that hides the thing it exists to show.** The tables are the
  report; the one drawing is a bar per station of the difference, which is the
  shape a person reads first and the one an average would destroy.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from fsmes.lab import feedback as feedback_mod
from fsmes.lab import measure

STYLE = """
:root {
  color-scheme: light;
  --ground: #f4f6f8; --panel: #ffffff; --ink: #161b22; --muted: #5b6675;
  --line: #d6dce4; --line-strong: #aeb8c4; --accent: #d97a12; --link: #0b5fbe;
  --code-bg: #eef1f5; --bad: #b3261e; --band: #6b7c8c;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --ground: #0e1116; --panel: #161b24; --ink: #e6eaf0; --muted: #98a3b3;
    --line: #2a3240; --line-strong: #3d4757; --accent: #ffab4c; --link: #6fb1ff;
    --code-bg: #1c232e; --bad: #ff8a80; --band: #8b9aab;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --ground: #0e1116; --panel: #161b24; --ink: #e6eaf0; --muted: #98a3b3;
  --line: #2a3240; --line-strong: #3d4757; --accent: #ffab4c; --link: #6fb1ff;
  --code-bg: #1c232e; --bad: #ff8a80; --band: #8b9aab;
}
body { margin: 0; background: var(--ground); color: var(--ink);
  font-family: "Source Sans 3", "Segoe UI", system-ui, sans-serif; font-size: 16px; line-height: 1.55; }
.wrap { max-width: 1040px; margin: 0 auto; padding: 48px 24px 96px; }
h1, h2, h3 { letter-spacing: -0.01em; text-wrap: balance; margin: 0; font-weight: 700; }
h1 { font-size: clamp(30px, 4.5vw, 44px); line-height: 1.08; max-width: 20ch; }
h2 { font-size: 25px; margin: 64px 0 10px; padding-top: 18px; border-top: 1px solid var(--line); }
h3 { font-size: 17px; margin: 28px 0 6px; }
p, li { max-width: 74ch; }
.eyebrow { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11.5px;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); margin: 0 0 12px; }
.lede { font-size: 19px; line-height: 1.45; margin: 16px 0 24px; max-width: 64ch; }
.conditions { color: var(--muted); font-size: 14px; max-width: 80ch;
  border-left: 3px solid var(--line-strong); padding: 6px 0 6px 14px; margin: 20px 0 8px; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.86em; }
code { background: var(--code-bg); padding: 1px 5px; border-radius: 4px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin: 18px 0 8px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 14px 16px; }
.tile .k { font-family: ui-monospace, monospace; font-size: 11px; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--muted); }
.tile .v { font-weight: 700; font-size: 30px; line-height: 1.1; margin-top: 4px; }
.tile .s { font-size: 13px; color: var(--muted); }
.scroll { overflow-x: auto; margin: 14px 0 6px; }
table { border-collapse: collapse; width: 100%; font-size: 14.5px; background: var(--panel); }
th, td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
thead th { border-bottom: 1px solid var(--line-strong); font-size: 12.5px; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.04em; }
td.verdict, td.wide { white-space: normal; text-align: left; min-width: 22ch; }
.unknown { color: var(--muted); font-style: italic; }
.bad { color: var(--bad); font-weight: 600; }
.band { color: var(--band); }
.bar { display: inline-block; height: 9px; background: var(--accent); border-radius: 2px; vertical-align: middle; }
.bar.neg { background: var(--bad); }
.notes { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 4px 20px 16px; }
.notes h3 { color: var(--accent); }
.empty { color: var(--muted); font-style: italic; }
.said { background: var(--panel); border: 1px solid var(--line); border-left: 3px solid var(--accent);
  border-radius: 6px; padding: 12px 16px; margin: 14px 0; }
.said .who { font-size: 12.5px; color: var(--muted); font-family: ui-monospace, monospace; }
.said .turn { margin: 10px 0 0; }
.said .turn .at { font-size: 12.5px; color: var(--muted); }
.said .turn .text { white-space: pre-wrap; margin: 2px 0 0; }
.said .turn.reply .text { color: var(--muted); }
footer { margin-top: 64px; color: var(--muted); font-size: 13.5px; }
"""

UNKNOWN = '<span class="unknown">unknown</span>'


def esc(value) -> str:
    return html.escape(str(value))


def num(value, digits: int = 0, suffix: str = "") -> str:
    """A number, or the word unknown. Never a zero standing in for one."""
    if value is None:
        return UNKNOWN
    if isinstance(value, bool):
        return "yes" if value else "no"
    return f"{float(value):,.{digits}f}{suffix}"


def pct(value, digits: int = 1) -> str:
    if value is None:
        return UNKNOWN
    return f"{float(value) * 100:.{digits}f}%"


def _tile(key: str, value: str, says: str = "") -> str:
    return (f'<div class="tile"><div class="k">{esc(key)}</div><div class="v">{value}</div>'
            f'<div class="s">{esc(says)}</div></div>')


def _notes(directory: Path) -> str:
    """The person's notes, rendered at the end so they travel with the numbers.

    Headings and paragraphs only - the file is a person writing prose, not a
    document format, and anything cleverer would be a second markdown renderer
    in a product that does not need one.
    """
    path = directory / "notes.md"
    if not path.is_file():
        return '<p class="empty">No notes.md in this run.</p>'
    out, paragraph = [], []

    def flush() -> None:
        if paragraph:
            out.append("<p>" + esc(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    written = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            flush()
            level = min(len(line) - len(line.lstrip("#")), 4)
            text = line.lstrip("#").strip()
            if level > 1:
                out.append(f"<h3>{esc(text)}</h3>")
        elif not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
            written = True
    flush()
    if not written:
        out.append('<p class="empty">The template is still empty - nobody has written '
                   'what they saw yet.</p>')
    return "\n".join(out)


# ----------------------------------------------------------------- sections

def _booking(booking: dict) -> str:
    rows = []
    for row in booking["stations"]:
        low, high = row["expected_range"]
        verdict = row["verdict"]
        css = ("unknown" if verdict.startswith("unknown")
               else "band" if verdict == "inside the replay's overlap band"
               else "" if verdict == "matched" else "bad")
        rows.append(
            f"<tr><td>{esc(row['station'])}</td><td class='mono'>{esc(row['equipment'] or '—')}</td>"
            f"<td>{num(row['truth_good'])}</td><td>{num(low)} to {num(high)}</td>"
            f"<td>{num(row['mes_good'])}</td><td>{num(row['difference'])}</td>"
            f"<td>{num(row['truth_scrap'])}</td><td>{num(row['mes_scrap'])}</td>"
            f"<td class='verdict {css}'>{esc(verdict)}</td></tr>")
    orders = booking["orders"]
    order_rows = "".join(
        f"<tr><td class='mono'>{esc(o['code'])}</td><td>{num(o['quantity'])}</td>"
        f"<td>{num(o['good'])}</td><td>{num(o['scrap'])}</td><td>{num(o['over'])}</td></tr>"
        for o in orders["mes_orders"])
    truth_orders = "".join(
        f"<tr><td class='mono'>{esc(code)}</td><td>{num(made)}</td></tr>"
        for code, made in orders["truth_good_by_order"].items())
    return f"""
<h2 id="booking">Booking honesty</h2>
<p>{esc(booking['question'])} The line made what the script made it make; the MES booked what its
machines told it. The expected range is the truth plus what the replay's overlap could honestly add
({num(booking['overlap_line_seconds'])} line seconds of a second pass, played while the agent drained).
A number below the range is under-booking; above it is the class of fault that books 16 units against
an order for 15.</p>
<div class="scroll"><table>
<thead><tr><th>Station</th><th>Machine</th><th>Truth good</th><th>Expected</th><th>MES good</th>
<th>Difference</th><th>Truth scrap</th><th>MES scrap</th><th>Verdict</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<h3>Orders</h3>
<p>{esc(orders['why'])} — so the per-order comparison is <em>unknown</em>, and both sides are printed
instead. The MES lists {num(orders['mes_orders_total'])} order(s); the script ran
{num(orders['truth_orders_total'])}.</p>
<div class="scroll"><table>
<thead><tr><th>MES order</th><th>Ordered</th><th>Good</th><th>Scrap</th><th>Over-run</th></tr></thead>
<tbody>{order_rows or '<tr><td colspan="5" class="empty">The MES listed no orders.</td></tr>'}</tbody>
</table></div>
<div class="scroll"><table>
<thead><tr><th>Scripted order</th><th>Units the line made</th></tr></thead>
<tbody>{truth_orders or '<tr><td colspan="2" class="empty">The script names no orders.</td></tr>'}</tbody>
</table></div>
"""


def _downtime(down: dict) -> str:
    breaks = down["breakdowns"]
    stops = down["planned_stops"]
    total = down["total_down"]
    fault_rows = "".join(
        f"<tr><td class='mono'>{esc(f['equipment'] or '—')}</td>"
        f"<td>{num(f['scripted_line_seconds'])} s</td>"
        f"<td>{UNKNOWN if f['detected'] is None else ('yes' if f['detected'] else 'no')}</td>"
        f"<td>{num(f['detected_line_seconds'])}</td><td>{pct(f['recall'])}</td>"
        f"<td class='{'band' if 'within resolution' in f.get('lag_says', '') else ''}'>"
        f"{esc(f.get('lag_says') or num(f['lag_line_seconds'], 1, ' s'))}</td>"
        f"<td class='wide unknown'>{esc(f['unknown_because'] or '')}</td></tr>"
        for f in breaks["events"])
    def _offenders(stop: dict) -> str:
        named = ", ".join(f"{o['equipment']} {o['seconds']}s" for o in (stop["offenders"] or []))
        return esc(named or "—")

    stop_rows = "".join(
        f"<tr><td>{num(s['scripted_line_seconds'])} s</td>"
        f"<td>{'yes' if s['observed'] else 'no'}</td>"
        f"<td class='{'bad' if s['misclassified_as_downtime'] else ''}'>"
        f"{'yes' if s['misclassified_as_downtime'] else 'no'}</td>"
        f"<td class='wide'>{_offenders(s)}</td>"
        f"</tr>"
        for s in stops["events"])
    labels = down["labels"]
    idle = down.get("idle_stops") or {}
    def _named(event: dict) -> str:
        return ", ".join(f"{o['equipment']} {o['seconds']}s"
                         for o in (event.get("offenders") or [])) or "—"

    idle_rows = "".join(
        f"<tr><td>{esc(e['event'])}</td><td>{esc(e['station'] or '—')}</td>"
        f"<td class='mono'>{esc(e['equipment'] or '—')}</td>"
        f"<td>{num(e['scripted_line_seconds'])} s</td>"
        f"<td>{'yes' if e['observed'] else 'no'}</td>"
        f"<td class='{'bad' if e['misclassified_as_downtime'] else ''}'>"
        f"{'yes' if e['misclassified_as_downtime'] else 'no'}</td>"
        f"<td class='wide'>{esc(_named(e))}</td>"
        f"</tr>"
        for e in idle.get("events") or [])
    truth_idle = idle.get("truth_line_seconds") or {}
    return f"""
<h2 id="downtime">Downtime honesty</h2>
<p>{esc(down['question'])} A planned stop booked as downtime destroys every availability figure the
plant reports, silently — which is why it is the first row here and not the last.</p>
<div class="tiles">
{_tile("breakdowns detected", pct(breaks['recall'], 0),
       f"{breaks['scored']} of {breaks['scripted']} scripted stops could be scored")}
{_tile("planned stops misclassified", num(stops['misclassified_as_downtime']),
       f"{stops['scored']} of {stops['scripted']} scored")}
{_tile("starved or blocked, called down", num(idle.get('misclassified_as_downtime')),
       f"{idle.get('scored')} of {idle.get('scripted')} scored")}
{_tile("down, the line's clock", num(total['truth_line_seconds'], 0, " s"),
       f"the MES reports {num(total['mes_line_seconds'], 0, ' s')} in line seconds")}
{_tile("unlabelled downtime", pct(labels['mes_unlabelled_share']),
       "the MES's own share; the truth for it is unknown")}
</div>
<h3>Scripted breakdowns</h3>
<p>The shortest event this run could have noticed at all is
{num(down.get('resolution_line_seconds'), 0, ' s')} of line time — one sampling interval at this
speed. A lag smaller than that is quantisation and is printed as <em>within resolution</em> rather
than as a signed number somebody could trend.</p>
<div class="scroll"><table>
<thead><tr><th>Machine</th><th>Scripted</th><th>Detected</th><th>Seconds seen</th><th>Recall</th>
<th>Lag</th><th>Unknown because</th></tr></thead>
<tbody>{fault_rows or '<tr><td colspan="7" class="empty">The script wrote no breakdowns.</td></tr>'}</tbody>
</table></div>
<h3>Planned stops</h3>
<div class="scroll"><table>
<thead><tr><th>Scripted</th><th>Observed</th><th>Counted as downtime</th><th>Which machines</th></tr></thead>
<tbody>{stop_rows or '<tr><td colspan="4" class="empty">The script wrote no planned stops.</td></tr>'}</tbody>
</table></div>
<h3>Starved and blocked</h3>
<p>{esc(idle.get('question') or '')}. The line spent
{num(truth_idle.get('starved'), 0, ' s')} starved and {num(truth_idle.get('blocked'), 0, ' s')}
blocked in total — most of that is the knock-on from whatever was scripted, because starving one
station starves the next one on its own. The table is the scripted windows only.</p>
<div class="scroll"><table>
<thead><tr><th>Event</th><th>Station</th><th>Machine</th><th>Scripted</th><th>Observed</th>
<th>Counted as downtime</th><th>Which machines</th></tr></thead>
<tbody>{idle_rows or '<tr><td colspan="7" class="empty">The script starved and blocked nothing.</td></tr>'}</tbody>
</table></div>

<h3>Labels</h3>
<p class="unknown">{esc(labels['unknown_because'])}</p>
"""


def _cycle_note(row: dict, truth: dict, said: dict | None) -> str:
    """Whether the two sides priced the machine's ideal cycle the same way, and
    how far apart they measured the run time performance divides by."""
    if not row["performance_like_for_like"]:
        mes = (said or {}).get("ideal_cycle_seconds")
        return esc(f"rated cycle differs: script {truth['rated_cycle_seconds']} s, MES {mes} s")
    band = row.get("performance_resolution")
    if band is None:
        return "like for like"
    return esc(f"like for like; run times differ by {band:.1%}, which is the band")


def _oee(oee: dict) -> str:
    window = oee["window"]
    mismatch = window["mismatch_share"]
    rows = []
    for row in oee["stations"]:
        said, truth, diff = row["mes"], row["truth"], row["difference"] or {}
        loud = (diff.get("availability") is not None
                and abs(diff["availability"]) > max(0.02, mismatch or 0.0))
        rows.append(
            f"<tr><td>{esc(row['station'])}</td><td class='mono'>{esc(row['equipment'] or '—')}</td>"
            f"<td>{pct(truth['availability'])}</td>"
            f"<td>{pct(said and said['availability'])}</td>"
            f"<td class='{'bad' if loud else ''}'>{pct(diff.get('availability'))}</td>"
            f"<td>{pct(truth['performance'])}</td>"
            f"<td>{pct(said and said['performance_line_seconds'])}</td>"
            f"<td>{pct(truth['quality'])}</td><td>{pct(said and said['quality'])}</td>"
            f"<td class='wide'>{_cycle_note(row, truth, said)}</td>"
            f"</tr>")
    return f"""
<h2 id="oee">OEE against the script</h2>
<p>{esc(oee['question'])} The script is ground truth by construction — it is the input the generator
obeyed. The two sides do not measure over the same window: the MES watched
{num(window['mes_line_seconds'], 0, ' s')} of line time, the script is
{num(window['truth_line_seconds'], 0, ' s')} long, a mismatch of {pct(mismatch)}. An availability
difference smaller than that is not evidence, and is not marked as one.</p>
<p>The <strong>P MES</strong> column is the MES's own performance put back on the line's clock — its
rating, its counts, its run time multiplied by the {esc(str(oee['speed']))}x this run replayed at.
Availability and quality are ratios of two wall-clock numbers, so the replay speed cancels out of
them; performance divides line seconds by wall-clock seconds, so it does not. The last column carries
each station's own band: both sides divide by run time, and they did not measure run time over the
same stretch.</p>
<div class="scroll"><table>
<thead><tr><th>Station</th><th>Machine</th><th>A truth</th><th>A MES</th><th>A diff</th>
<th>P truth</th><th>P MES</th><th>Q truth</th><th>Q MES</th>
<th>Performance basis</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
"""


# ----------------------------------------------------------------- feedback

def _turn(turn: dict) -> str:
    """One thing somebody said, verbatim, with the second it was said at.

    Never summarised and never tidied: a remark that turns out to be wrong is
    still what the person watching said while they were watching.
    """
    reply = turn.get("role") != "user"
    said = esc(turn.get("moment_says") or "")
    if reply:
        model = f" · {esc(turn['model'])}" if turn.get("model") else ""
        said = f"the assistant answered — {said}{model}"
    return (f'<div class="turn{" reply" if reply else ""}">'
            f'<div class="at">{said}</div>'
            f'<p class="text">{esc(turn.get("text") or "")}</p></div>')


def _conversation(row: dict) -> str:
    head = (f'<div class="who">{esc(row.get("screen") or row.get("route"))}'
            f' · {esc(row.get("who") or "somebody")}'
            f' · conversation {esc(row.get("conversation"))}</div>')
    return '<div class="said">' + head + "".join(_turn(t) for t in row.get("turns") or []) + "</div>"


def _said(rows: list[dict] | None, where: str) -> str:
    """The notes that belong beside one section, or nothing at all.

    Deliberately silent when there are none: an empty "no feedback" box under
    every heading would train the eye to skip the place the feedback appears.
    """
    if not rows:
        return ""
    return (f'<h3>What was said while watching {esc(where)}</h3>'
            '<p>Left by whoever watched this run. The numbers above are for the whole run; '
            'each note carries the line second of the run it was made at.</p>'
            + "".join(_conversation(r) for r in rows))


def _differences(plant: dict) -> str:
    """Where the MES and the truth disagree, biggest first.

    The rows come from `measure.differences`, which `fsmes lab review` reads
    too - one definition, so a page and a roll-up of several pages cannot
    come to disagree about the same run.
    """
    found = measure.differences(plant)
    if not found:
        return ('<p class="empty">Nothing outside the bands this run can vouch for. That is not the '
                'same as "everything matched" — read the unknowns.</p>')
    biggest = found[0]["size"] or 1
    rows = "".join(
        f"<tr><td>{esc(row['where'])}</td><td>{esc(row['what'])}</td>"
        f"<td class='wide'>{esc(row['says'])}</td>"
        f"<td><span class='bar' style='width:{max(4, int(120 * row['size'] / biggest))}px'></span></td></tr>"
        for row in found)
    return ('<div class="scroll"><table><thead><tr><th>Where</th><th>What</th><th>Difference</th>'
            f'<th></th></tr></thead><tbody>{rows}</tbody></table></div>')


def _plant(plant: dict, said: list[dict] | None = None) -> str:
    parts = [f'<h2 id="{esc(plant["plant"])}">{esc(plant["plant"])}'
             f'{" — " + esc(plant["label"]) if plant.get("label") else ""}</h2>']
    pipeline = plant.get("pipeline") or {}
    sustained = pipeline.get("sustained")
    parts.append('<div class="tiles">' + "".join([
        _tile("seed", esc(plant["seed"]), "the same seed twice is the same run"),
        _tile("line time", num(plant["duration_line_seconds"], 0, " s"),
              f"replayed at {plant['speed']}x"),
        _tile("overlap band", num(plant["replay_overlap_line_seconds"], 0, " s"),
              "a second pass the agent drained through"),
        _tile("harness kept up", UNKNOWN if sustained is None else ("yes" if sustained else "no"),
              "replay and agent both inside one sampling interval"),
    ]) + "</div>")
    if plant.get("verdict_withheld"):
        parts.append(f'<p class="bad">Every headline number for this plant is withheld: '
                     f'{esc(plant["verdict_withheld"])}. The per-event detail below is still '
                     f'worth reading; the numbers are not worth trending.</p>')
    if plant.get("overlay"):
        parts.append(f'<p>Overlay applied to <code>{esc(", ".join(sorted(plant["overlay"])))}</code>; '
                     f'the pack on disk was not touched.</p>')
    if plant.get("views_refused"):
        parts.append("<p>Views that did not answer: " + ", ".join(
            f"<code>{esc(k)}</code> ({esc(v)})" for k, v in plant["views_refused"].items()) + "</p>")
    groups = feedback_mod.by_section(said or [], plant["plant"])
    parts.append("<h3>Differences, biggest first</h3>")
    parts.append(_differences(plant))
    parts.append(_said(groups.get(None), "this plant's other screens"))
    if plant["measurements"].get("booking"):
        parts.append(_booking(plant["measurements"]["booking"]))
        parts.append(_said(groups.get("booking"), "the work orders screen"))
    if plant["measurements"].get("downtime"):
        parts.append(_downtime(plant["measurements"]["downtime"]))
        parts.append(_said(groups.get("downtime"), "the operations screen"))
    if plant["measurements"].get("oee"):
        parts.append(_oee(plant["measurements"]["oee"]))
        parts.append(_said(groups.get("oee"), "the shift analysis screen"))
    # A note about a measurement this run did not take still has to be read.
    orphaned = [row for key, rows in groups.items() if key
                and not plant["measurements"].get(key) for row in rows]
    parts.append(_said(orphaned, "a screen this run took no measurement for"))
    triage = plant.get("triage") or {}
    findings = triage.get("findings") or []
    if findings:
        parts.append("<h3>What nobody asserted</h3><ul>" + "".join(
            f"<li>{esc(f.get('says') or f)}</li>" for f in findings) + "</ul>")
    return "\n".join(parts)


def render(scores: dict, directory: Path, said: list[dict] | None = None) -> str:
    """The whole page, from the run's own scores and notes."""
    plants = scores.get("plants", [])
    withheld = [p for p in plants if p.get("verdict_withheld")]
    conditions = (
        f"FactorySemantics MES {esc(scores.get('product_version'))}, plan "
        f"<code>{esc(Path(scores.get('plan', '')).name)}</code>, started "
        f"{esc(scores.get('started_at'))} UTC and finished in "
        f"{num(scores.get('wall_seconds'), 0, ' s')} of wall clock. "
        f"{len(plants)} of {scores.get('plants_total')} plant(s) ran, each on loopback with its own "
        f"database and its own ports, torn down when the questions had been asked. Every number on "
        f"this page came out of that run: nothing here was typed in."
    )
    if said is None:
        said = feedback_mod.read(directory)
    notes = sum(1 for c in said for t in c.get("turns") or [] if t.get("role") == "user")
    sections = "\n".join(_plant(p, said) for p in plants)
    toc = "".join(f'<li><a href="#{esc(p["plant"])}">{esc(p["plant"])}</a></li>' for p in plants)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(scores.get('experiment'))} — lab run</title>
<style>{STYLE}</style></head>
<body><div class="wrap">
<p class="eyebrow">FactorySemantics MES · lab experiment</p>
<h1>{esc(scores.get('experiment'))}</h1>
<p class="lede">{esc(scores.get('note') or 'What the plant recorded, beside what the line actually did.')}</p>
<p class="conditions">{conditions}</p>
<div class="tiles">
{_tile("plants", num(scores.get('plants_run')), f"of {scores.get('plants_total')} planned")}
{_tile("speed", f"{esc(scores.get('speed'))}x", "line time per second of wall clock")}
{_tile("measurements", esc(", ".join(scores.get('measurements_asked_for', []))), "asked for by the plan")}
{_tile("verdicts withheld", num(len(withheld)), "runs whose own harness fell behind")}
{_tile("notes from the screens", num(notes),
       f"in {len(said)} conversation(s) tagged to this run")}
</div>
<p>Plants in this run: <ul>{toc}</ul></p>
{sections}
<h2 id="notes">Notes</h2>
<p>What the numbers did not capture, from whoever watched it run.</p>
<div class="notes">{_notes(directory)}</div>
<footer>Written by <code>fsmes lab</code> from <code>scores.json</code> and <code>notes.md</code> in
this directory. The directory is the unit: the plan, the scripts, the generated line data, what each
plant recorded, the scores and this page all travel together. Re-render it after editing the notes
with <code>fsmes lab open {esc(directory.name)}</code>.</footer>
</div></body></html>
"""


def write(directory: Path) -> Path:
    """Render `report.html` from the run in this directory."""
    directory = Path(directory)
    scores = json.loads((directory / "scores.json").read_text(encoding="utf-8"))
    out = directory / "report.html"
    out.write_text(render(scores, directory, feedback_mod.read(directory)), encoding="utf-8")
    return out
