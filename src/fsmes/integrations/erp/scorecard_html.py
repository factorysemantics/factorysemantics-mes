"""The scorecard as one self-contained page a plant can send to a meeting.

Self-contained on purpose: no CDN, no fonts to fetch, no script. A shadow
run happens on a plant network that often cannot reach the internet at
all, and the page is going to be mailed around, so everything it needs is
in the file.

There are no charts here, and that is a decision rather than an omission.
The one chart this report invites — a bar of "how much agreed" — averages
away the thing it exists to show. Two records that agree on 998 of 1,000
comparisons and differ by 400 units on the other two describe a plant with
a serious problem, and the bar would be 99.8% full. So the page is tables:
the totals, then every difference by name.
"""

from __future__ import annotations

import html
from datetime import datetime

from fsmes.integrations.erp.scorecard import BY_NAME, Difference, Scorecard

STYLE = """
:root {
  color-scheme: light dark;
  --ink: #16191d; --dim: #5b6470; --line: #dfe3e8; --ground: #fbfbfc;
  --card: #ffffff; --flag: #8a5a00; --flag-ground: #fff6e5;
}
@media (prefers-color-scheme: dark) {
  :root { --ink: #e8eaed; --dim: #9aa4b0; --line: #333940; --ground: #14171a;
          --card: #1b1f23; --flag: #f0b429; --flag-ground: #2a2210; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--ground); color: var(--ink);
       font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 60rem; margin: 0 auto; padding: 2.5rem 1.25rem 5rem; }
h1 { font-size: 1.7rem; margin: 0 0 .3rem; letter-spacing: -.01em; }
h2 { font-size: 1.15rem; margin: 2.5rem 0 .6rem; }
p.sub { color: var(--dim); margin: 0 0 2rem; }
p { margin: .6rem 0; }
.cards { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
        padding: .85rem 1rem; }
.card b { display: block; font-size: 1.5rem; font-weight: 600; }
.card span { color: var(--dim); font-size: .82rem; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: .45rem .7rem; border-bottom: 1px solid var(--line);
         white-space: nowrap; }
th { font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; color: var(--dim); }
td.n, th.n { text-align: right; }
tbody tr:last-child td { border-bottom: 0; }
.wrap { white-space: normal; }
ul { padding-left: 1.1rem; }
li { margin: .45rem 0; }
.flag { background: var(--flag-ground); border-left: 3px solid var(--flag);
        padding: .8rem 1rem; border-radius: 0 6px 6px 0; margin: 1.2rem 0; }
a { color: inherit; }
footer { color: var(--dim); font-size: .82rem; margin-top: 3rem;
         border-top: 1px solid var(--line); padding-top: 1rem; }
"""


def e(value) -> str:
    return html.escape("" if value is None else str(value))


def _rows(differences: list[Difference]) -> str:
    if not differences:
        return "<p>Nothing. Every comparison that could be made agreed within the tolerances above.</p>"
    body = "".join(
        f"<tr><td>{e(d.order)}</td><td>{e(d.operation) or '—'}</td>"
        f"<td>{e(BY_NAME[d.field].says)}</td><td class='n'>{e(_show(d.incumbent, d.field))}</td>"
        f"<td class='n'>{e(_show(d.mes, d.field))}</td><td class='n'>{e(_gap(d))}</td></tr>"
        for d in differences)
    return (
        "<div class='scroll'><table><thead><tr><th>Order</th><th>Operation</th><th>Measure</th>"
        "<th class='n'>The incumbent</th><th class='n'>This MES</th><th class='n'>Apart</th>"
        f"</tr></thead><tbody>{body}</tbody></table></div>")


def _show(value, field_name: str = "") -> str:
    if value is None:
        return "—"
    if field_name == "duration_seconds" and isinstance(value, int | float):
        return f"{value / 60:.1f} min" if abs(value) >= 90 else f"{value:g} s"
    return f"{value:g}" if isinstance(value, float) else str(value)


def _gap(d: Difference) -> str:
    if d.unit == "seconds":
        return f"{d.gap / 60:.1f} min" if abs(d.gap) >= 90 else f"{d.gap:g} s"
    return f"{d.gap:g}"


def _agreement(title: str, agreements) -> str:
    stated = [a for a in agreements if a.total]
    if not stated:
        return ""
    body = "".join(
        f"<tr><td>{e(BY_NAME[a.field].says)}</td><td class='n'>{a.agree}</td>"
        f"<td class='n'>{a.differ}</td><td class='n'>{a.not_stated}</td>"
        f"<td class='n'>{a.total}</td></tr>" for a in stated)
    return (f"<h2>{e(title)}</h2><div class='scroll'><table><thead><tr><th>Measure</th>"
            "<th class='n'>Agree</th><th class='n'>Differ</th><th class='n'>Not compared</th>"
            f"<th class='n'>Pairs</th></tr></thead><tbody>{body}</tbody></table></div>")


def render(card: Scorecard, title: str = "Shadow scorecard",
           generated_at: datetime | None = None) -> str:
    """One page: the totals, every difference, and what was not compared."""
    when = (generated_at or datetime.now()).isoformat(sep=" ", timespec="seconds")
    orders_total = (len(card.orders_both) + len(card.orders_only_incumbent)
                    + len(card.orders_only_mes))
    operations_total = (len(card.operations_both) + len(card.operations_only_incumbent)
                        + len(card.operations_only_mes))
    cannot = "".join(f"<li>{e(says)}</li>" for says in card.cannot_tell)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<style>{STYLE}</style></head>
<body><main>
<h1>{e(title)}</h1>
<p class="sub">Two records of the same work, side by side. Written {e(when)}.</p>

<div class="flag"><p><b>This report does not say which side is right.</b> It says where the
incumbent MES's export and this MES's confirmations differ, with both sets of numbers.
Where they disagree, the machine is the tie-breaker, not this page.</p></div>

<h2>What was read</h2>
<div class="cards">
  <div class="card"><b>{card.incumbent.documents}</b><span>rows in the incumbent's export</span></div>
  <div class="card"><b>{card.mes.documents}</b><span>confirmations from this MES</span></div>
  <div class="card"><b>{len(card.orders_both)} of {orders_total}</b><span>orders in both records</span></div>
  <div class="card"><b>{len(card.operations_both)} of {operations_total}</b><span>operations in both</span></div>
</div>
<p>Tolerances: {e(card.tolerances.says())}</p>
<p>Orders in either record: {orders_total} — {len(card.orders_both)} in both,
{len(card.orders_only_incumbent)} in the export only, {len(card.orders_only_mes)} in this MES's
confirmations only. Operations: {operations_total} — {len(card.operations_both)} in both,
{len(card.operations_only_incumbent)} in the export only, {len(card.operations_only_mes)} in this
MES's only. Only the ones in both records were compared; the rest are named under
<a href="#cannot">what this cannot tell you</a>.</p>

{_agreement("Agreement, per operation", card.operation_agreement)}
{_agreement("Agreement, per order", card.order_agreement)}

<h2>Where they differ — all {len(card.differences)}</h2>
{_rows(card.differences)}

<h2 id="cannot">What this cannot tell you</h2>
<ul>{cannot}</ul>

<footer>FactorySemantics MES · <code>fsmes shadow scorecard</code>. Every number on this page
came from the two files named to it; nothing was inferred, averaged or filled in.</footer>
</main></body></html>
"""
