# The style contract

What "coherent" means for these screens, written down so an agent can cite it
and `fsmes ui-check` can enforce the checkable parts. This document is the
flexibility mechanism as much as the coherence one: **evolving the design
deliberately means editing this contract and re-accepting baselines
(`fsmes ui-check --accept`) in the branch that makes the change.** Drift that
ships without touching this file or the baselines is a bug by definition.

The enforcement layer is `tests/ui/baselines/*.json` — computed styles of the
components named below, per theme, captured from the live screens. A rule
here that the baselines cannot see is still a rule; reviewers hold the line
where the crawler cannot.

## Rules

1. **A component owns its own chrome.** `.kpi` owns the card — background,
   border, radius, padding, layout. A class added *beside* it (`.kpi-action`)
   may strip browser defaults and add affordance, but may not declare any
   property the base class already sets. *(The rule that came from the
   2026-09-01 tile regression, where `border: 1px solid transparent` on the
   affordance silently deleted every card's outline. Pinned by
   `test_a_clickable_tile_still_looks_like_the_card_it_was`.)*
2. **Colour comes from the palette, nowhere else.** Every colour on every
   screen resolves to a variable defined in `themes.css`. A hex literal in a
   page stylesheet is a bug — that is what let four colours escape theming
   before the four-theme pass caught them.
3. **All four themes are first-class.** control-room, daylight,
   high-contrast, night-shift. A change is not done until it looks right in
   all four; `ui-check` snapshots every theme for exactly this reason.
   High-contrast keeps its WCAG ratios (a test computes them).
4. **Every list states its total.** "Showing 50 of 18,347" — a truncated
   list must never look complete (the same honesty rule the API's envelope
   carries).
5. **Numbers describe the plant unless labelled otherwise.** A tile, KPI or
   count scoped to the current page/filter says so. A plant-wide number and
   a page-scoped number may not sit side by side unlabelled.
6. **Affordance is structural, not decorative.** A thing you can click is a
   `<button>` or an `<a>` — never a `<div>` with a handler — so keyboards
   and screen readers work without extra code. Hover feedback moves colour
   only; nothing on the page shifts by a pixel.
7. **Interactive elements carry `data-assist` anchors** where a guide points
   at them, and renaming an anchor is a deliberate act that re-authors the
   guides (they break loudly by design).
8. **Unknown renders as "—" or "unknown", never as 0** (principle 4, on
   screen). A yield with nothing booked is unknown; a machine that reported
   nothing is unknown; zero is a measurement.
9. **No build step** (principle 5). Plain HTML/JS/CSS, hand-drawn SVG for
   charts, no bundler, no framework, no font or script fetched from outside
   the box.
10. **What you DO sits apart from what you READ.** A screen's action forms
    are one strip, first on the page, with the accent edge and surface
    `.actions` carries in `styles.css` — styled once there, so all four
    themes follow without knowing the component exists. At a hundred
    machines the floor's action forms were a screen and a half below the
    fold, which is the same as not having them.
11. **A filter bar holds one line.** A long tail of controls goes behind a
    `.popover` toggle (`FS.popover`), and the toggle says what it is
    filtering to rather than looking idle. A wrapping row of controls puts a
    slab of chrome above the table it is meant to serve.
12. **New screens join the system.** Same header/nav, same `.panel` grid,
    same `.kpi` strip when there are KPIs, themed via the palette from day
    one — and they are crawled automatically (ui-check reads the routes from
    `app.py`), so a new screen is watched the day it exists.
13. **Every chart obeys the chart contract below**, and is drawn by
    `kit.js` — `FS.kit.chart(kind, envelope, options)`. A second chart
    engine on a second screen is how two screens come to state coverage
    differently, which is the thing `kit.js` exists to make impossible.
    The kinds are `line`, `bars` (alias `pareto`), `states`, `histogram`
    and `graph`.

    **The `graph` shape**, the network of the deep-analysis design page
    (`docs/design/deep-analysis.md` §7, which lands with its own change),
    keeps four rules of its own on top of the six:

    - **Every edge is a recorded fact.** The shape draws the edges the
      envelope carries and never one between two nodes that ended up near
      each other. A picture is where "an edge nobody observed is not an
      edge" is hardest to keep and easiest to break.
    - **The holes are things with numbers on them.** `unattributed` and
      `unlabelled` are nodes, hatched, carrying their **degree** rather
      than a weight — what a hole is, is what it touches — so the reader
      sees the three turns with no person rather than a tidy graph that is
      three turns short.
    - **Empty node kinds are drawn empty.** A kind the model declares and
      this plant records nothing of is on the picture with its zero and
      its name. A graph that quietly omitted `screen` would read as a
      complete picture of a plant where the questions came from nowhere.
    - **Coverage is `absent`, always, and there is no centrality.** A graph
      of records is not a rate over a watched window. And betweenness,
      PageRank and eigenvector centrality over an edge set that is
      *whatever happens to be recorded* would be the most convincing wrong
      number this product could show, so the shape does not offer one.

    Node kinds and edge kinds map to palette variables in `styles.css` and
    nowhere in `kit.js` (rule 2). Under the night-shift palette three node
    kinds are close in hue, because that palette is deliberately low-blue;
    the legend and the node names carry the distinction there.

14. **What the reader narrowed a chart to re-states its total.** Hovering a
    mark says that mark's own `data-value` with the coverage sentence under
    it; the legend switches a kind out; the threshold moves; the time axis
    brushes. Every one of those rewrites `data-total` **and** the footer,
    because a filtered chart that kept the old total is a list that reads
    complete (rule 4). The frame does this for every shape, so a new shape
    cannot forget it. The controls are drawn inside the `<svg>` — there is
    no `<button>` in SVG, so they carry `role`, `tabindex` and an
    `aria-label`, and they answer a keyboard.

15. **A chart is presentation-ready when its footer survives the paste.**
    `FS.kit.export(chart, "svg" | "png")` is the chart node's own markup
    with the theme's resolved colours inlined and the panel colour behind
    it. An export whose coverage sentence was stripped is not an export
    this product makes.

## The chart contract

A rendered chart is the most convincing wrong thing this product can draw
(house rule 6), so the six rules below are not style — they are the honesty
rules this MES already keeps, applied to a picture. They come from
[the agentic-harness design page](agentic-harness.md), §9 M1, which is where
the reasoning lives; this is the short form a reviewer can hold in their head.

1. **A chart draws what the API measured.** It computes nothing. Every number
   on the picture is a number the envelope carried, and it appears verbatim in
   a `data-value` attribute so a test can say so. Laying numbers out is
   allowed; working one out is not — which is why the histogram refuses to bin
   a series and asks the API for the bins.
2. **Every figure carries its coverage**, because the envelope it came from
   does. `data-coverage` is a number, `null` when it could not be computed, or
   `absent` when the payload has no such field: three different facts. A row
   below this plant's coverage floor is drawn withheld at **full width** with
   its ledger, never omitted, never averaged away, and never as a shorter bar —
   a shorter bar reads as a measurement of a machine rather than of how little
   of it anybody saw ([0033](../decisions/0033-availability-is-a-share-of-what-was-watched.md)).
3. **Unknown is drawn as unknown** — a rendering of its own: hatched, marked
   `data-unknown="true"`, and the line breaks rather than being drawn through
   the hole. Not a gap, not a zero, not a smooth line (rule 8 above,
   [0030](../decisions/0030-a-lost-connection-is-unknown-time.md)).
4. **Honest axes.** A y-axis that does not start at zero says so on itself; a
   window the MES could not fill says what it truncated; a rate carries its
   denominator; and a shape that depends on a choice — a bin width, a bar
   scale — states the choice.
5. **Every chart states its total**, in text on the chart and in `data-total`,
   including the rows nobody drew (rule 4 above, applied to a picture).
6. **The palette, and all four themes** (rules 2 and 3 above). Nothing in
   `kit.js` names a colour: every mark carries a class from `styles.css`, so
   all four themes follow without the page knowing a chart exists.

And every chart carries a `<title>` and a `<desc>` assembled from the same
sentences as its visible footer, so what a screen reader is told and what a
sighted reader sees cannot drift apart.

**No build step, and nothing vendored** (rule 9 above,
[0005](../decisions/0005-plain-html-no-build-step.md)). Hand-drawn SVG,
extended in `kit.js` rather than duplicated. A charting library would have to
be vendored under `web/vendor/` with its version, licence and SHA-256, and is a
decision record when it happens — the three.js precedent was accepted because
WebGL cannot be hand-drawn. **None of the five shapes needed one, the network
graph included**: its layout is ninety hand-written lines, and the reason it is
a shape rather than a vendored engine is that the six rules are *structural*
here — `frame()` writes `data-total`, `data-coverage`, the `<title>`, the
`<desc>` and the footer for every shape before the shape draws anything, so a
new shape inherits all of it and a new engine would inherit none
(`docs/design/deep-analysis.md` §6, where that is measured against vendoring
plotly).

Pinned by `tests/test_a_chart_draws_only_what_the_api_measured.py`: every shape,
every rule, every interaction and the export, in all four themes, from fixed
envelopes.

## What ui-check watches

Components: `header`, `.kpi`, `.panel`, `table`, `.pill`, `button`,
`.filters`, `svg.fs-chart` — first instance per page, plus a count. The chart
is watched on its frame and not on a mark, for the same reason `.pill` is not
watched at all: a mark is coloured by the state it reports, which is runtime
data, and a check that fires on the plant working is a check people learn to
ignore.
Properties: color, background-color, border, border-radius, font-size,
font-weight, padding, display.

Add a component here and to `WATCHED` in `src/fsmes/sim/ui_check.py` in the
same change.

The header is watched twice over, because rule 10 is the one a person meets
first: a computed-style snapshot like every other component, **and** a
per-route fact — is it on the page, is it visible, has it a link home, does
it list any screens at all. A route that fails any of those is a `no-header`
finding, on that route in that theme, needing no baseline. A screen nobody
can leave is a bug however good it looks, and a theme that paints the header
out of existence is the same bug wearing a palette.
