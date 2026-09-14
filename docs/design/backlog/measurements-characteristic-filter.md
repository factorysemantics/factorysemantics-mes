---
title: The measurements card filters its characteristics on the server
status: built
conversation: 13
turns: [37]
route: /dashboard/quality
plant: megafactory.db
created: 2026-09-14
updated: 2026-09-14
branch: feat/ui-lists-filter
tags: [fsmes, design, backlog]
---

# The measurements card filters its characteristics on the server

**built** — A characteristic search narrows the tab strip server-side; the filter selects come from facets, not from every specification in the plant.
## What he was looking at

The mega-factory's Quality screen. "way too many tags" is 127 characteristics
across the plant's materials, laid out as one strip of tabs above the chart.

## What it did

The material select and the tab strip were both built from a single fetch of
`/quality/specs`, which returned every specification the plant has. The strip
was already narrowed to one material's characteristics — but a material with
a hundred characteristics still gives you a wall, and the list behind the
select was the whole table either way.

## What was built

A characteristic search box beside the material select. It narrows the tab
strip **on the server** — `/quality/specs?material=…&q=…` — so typing "fill"
is how you reach the one you came for, and the line under it says what it is
showing: "12 of 96 characteristics matching on FG-FILL1, of 1,240 in the
plant".

The material select is built from a new `/quality/specs/facets`, which
returns the distinct materials and characteristics with a count each and the
totals, rather than from the specifications themselves. Filling a dropdown
should not mean reading the whole table, and a test counts the statements to
prove it no longer does.

The inspection history under the chart gained a date range — "what did this
shift measure" is a question about a window — and a column naming the work
order each check was taken against.

Every filter on the screen is now in the address bar, so a supervisor who has
narrowed the history to last night's failures on one characteristic can send
that screen to whoever has to answer for it.
