---
title: Shop-floor actions are a strip of their own, first on the page
status: built
conversation: 12
turns: [35]
route: /dashboard
plant: megafactory.db
created: 2026-09-14
updated: 2026-09-14
branch: feat/ui-lists-filter
tags: [fsmes, design, backlog]
---

# Shop-floor actions are a strip of their own, first on the page

**built** — What you do here, set apart from what you read here, styled once in styles.css so all four themes follow.
## What he asked

"shop-floor actions should somehow be differentiated at the top maybe?"

## Why it matters at 108 stations

Book output, Issue material and Quality check are the three things an
operator *does* from the Floor screen. Everything else on it is something
they *read*. With a wall of machine cards above them the forms were a screen
and a half below the fold, which is the same as not having them.

## What was built

The forms are one panel, first on the page, headed "Shop-floor actions — what
you can do from here". It keeps the panel chrome every other panel has and
adds an accent edge and its own surface, drawn from `--accent` and
`--panel-2` in `styles.css`, so all four themes follow without any theme
knowing the component exists. `fsmes ui-check` in all four themes reported no
broken link, no console error and no failed request; the one style drift is
this panel's background, and the four baselines carry the new value.

The edge is an inset shadow rather than a left border: replacing one side of
the panel's 1px border leaves the panel with no computed border at all, which
ui-check caught on the first run.

The Book-output machine control is now a datalist-backed input rather than a
select. A thousand machines is not a dropdown, and any code may be typed —
the server decides whether it exists.

This is written into `docs/design/STYLE.md` as a rule, so the next screen
with actions on it does the same thing without anyone having to remember
this conversation.
