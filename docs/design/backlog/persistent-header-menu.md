---
title: The header menu persists on every page
status: done
conversation: 8
turns: [19, 21, 23]
route: /dashboard/line
plant: bottling.db
created: 2026-09-14
updated: 2026-09-14
branch: feat/ui-nav-menu-and-crumbs
tags: [fsmes, design, backlog]
---

# The header menu persists on every page

**done** — every screen already carried the shared header; nothing watched
that it kept doing so, and the audit is now a test that reads the folder.

## The idea

> "I'd like the menu to persist on the dashboard/line page. I can't get back
> to the main site from here." (turn 19)
>
> "the header menu that persists throughtout the site." (turn 21)
>
> Asked which pages: "all pages". (turn 23)

Said on `/dashboard/line` on 2026-09-02, on the bottling plant. It sat in the
design chat's queue untriaged for twelve days.

## Assessment

The complaint was true when it was made. The fix landed before it was read:
`common.js` renders one header from one manifest (`FS.NAV`), a page opts in
with `<header data-nav="...">`, and the screens that had grown their own
bespoke head were migrated. `test_every_page_uses_the_shared_header` in
`tests/test_web.py` has held the 22 pages it lists ever since.

What was still missing was the *audit*. Three things could have quietly
undone it and nothing would have said so:

1. That test parametrises over `PAGES`, a list typed by hand in the test
   file. A new screen added to `src/fsmes/web/` and forgotten there would
   have been audited by nothing at all.
2. It reads the HTML. A theme that painted the header out of existence, or
   a header that rendered with an empty menu because `/auth/me` changed
   shape, would pass it — and look exactly like the dead end Scott met.
3. `fsmes ui-check` crawls every screen in every theme and had no opinion
   about the header beyond a computed-style snapshot of whatever `header`
   it found first.

## What was done

Branch `feat/ui-nav-menu-and-crumbs`.

- `tests/test_web.py`: `test_no_screen_can_quietly_appear_without_the_header`
  reads `src/fsmes/web/*.html` rather than a list, and holds the hand-typed
  list to the folder. A page that is not a plant screen is excused by name
  with its reason in `NOT_PLANT_SCREENS`; `fleet.html` is the only one — the
  fleet console is a separate server reading many plants, with no plant
  session and no plant screens to link to.
- `src/fsmes/sim/ui_check.py`: the crawl records, for every route in every
  theme, whether the header is present, visible, has a link home, and lists
  any screens at all — and `compare()` files a `no-header` finding when it
  does not. Verified by deleting the Line page's header and watching the
  crawl name it.
- `tests/test_ui_nav.py`: a browser opens six screens and asserts the header
  is there, visible, with a link home and a menu of at least two screens.

The audit's answer, from a crawl of the demo plant on 2026-09-14: 22 routes
× 4 themes, no `no-header` finding on any of them. The header was on every
page already; now it stays there.
