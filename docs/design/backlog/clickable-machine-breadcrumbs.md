---
title: Every level of the machine page's tree is clickable
status: done
conversation: 14
turns: [39, 41]
route: /dashboard/machine/{code}
plant: megafactory.db
created: 2026-09-14
updated: 2026-09-14
branch: feat/ui-nav-menu-and-crumbs
tags: [fsmes, design, backlog]
---

# Every level of the machine page's tree is clickable

**done** — the machine page's crumbs were already links at every level; the
Machines tree drew the same trail by its own rules, and the two disagreed.

## The idea

> "why can't i click along the tree Mega-Factory › Mega-Factory Works ›
> Assembly › ASSEM1LINE › ASSEM1_Kit? I really like that structure and it
> makes sense to me and I want to be able to click those." (turn 39)
>
> "I can only click the ASSEM1LINE, not any of the others. I should be able
> to though." (turn 41)

Said on `/dashboard/machine/ASSEM1_Kit` on 2026-09-04, on the mega-factory.

## Assessment

On `main` as it stands, `machine.js` already made every ancestor a link: a
work center opens the Line view, and an enterprise, site or area opens the
Machines tree scoped to that node (`FS.link("equipment", …)` →
`/dashboard/machines?under=CODE`). Confirmed in a browser against the demo
plant, whose hierarchy is the same five levels Scott was reading:
ACME › KC1 › PKG › LINE1 › MIX01, four links and the machine as text.

Two things were still wrong, and one of them is Scott's complaint in
miniature.

**The two screens disagreed.** `machines.js` drew the same trail for
`?under=`, with its own rule: *every* ancestor went to
`/dashboard/machines?under=`, including a work center — so standing on the
tree screen, a line was not a line, while standing on the machine page it
was. Which level was clickable, and where it went, depended on which screen
you were on. That is how the original divergence happened.

**Nothing held it.** No test rendered a breadcrumb, so both trails could
drift again without a failure.

## What was done

Branch `feat/ui-nav-menu-and-crumbs`.

- `src/fsmes/web/common.js`: `FS.crumbs(container, path, current)` — one
  renderer, one rule about which screen shows which level, used by both
  screens. The node you are on is text with `aria-current="page"` rather
  than a link to the page you are already looking at.
- `src/fsmes/web/machine.js`, `machines.js`: both call it. The tree screen
  now sends a work center to the Line view, as the machine page always did,
  and its ancestors gained the level names in their tooltips.
- `tests/test_ui_nav.py`: a browser opens the machine page, asserts all four
  ancestors are links and the machine is not, asserts where each level goes,
  and then **clicks every one of them** and checks the page it lands on
  renders with its own header. A separate test stands on the tree screen and
  asserts it draws the same trail.

No level is without a screen: an enterprise, a site and an area each open
the Machines tree scoped to themselves, which is the screen that shows a
node and everything under it. If any of them gets a page of its own later,
`FS.crumbs` is the one line that has to change.
