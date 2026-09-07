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
10. **New screens join the system.** Same header/nav, same `.panel` grid,
    same `.kpi` strip when there are KPIs, themed via the palette from day
    one — and they are crawled automatically (ui-check reads the routes from
    `app.py`), so a new screen is watched the day it exists.

## What ui-check watches

Components: `header`, `.kpi`, `.panel`, `table`, `.pill`, `button`,
`.filters` — first instance per page, plus a count.
Properties: color, background-color, border, border-radius, font-size,
font-weight, padding, display.

Add a component here and to `WATCHED` in `src/fsmes/sim/ui_check.py` in the
same change.
