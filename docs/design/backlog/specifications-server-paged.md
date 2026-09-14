---
title: The specifications card is the server's page
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

# The specifications card is the server's page

**built** — The last list in the API that handed over the whole table now answers with the standard envelope.
## What he asked

"specifications needs filtering."

## What it did

The Specifications card had a search box, a material select and a pager, and
all three worked on a list the browser had already downloaded whole. The code
said so: "the endpoint answers with the whole list (127 rows, 13 KB — fine)".
It is fine at 127. It is not a habit that survives a product catalogue ten
times the size, and it was the last list in this API still handing over the
whole table.

## What was built

`/quality/specs` answers with the same envelope every other list uses —
`items`, `total`, `limit`, `offset`, `has_more` — fifty at a time unless
asked for more, and takes `characteristic` as well as `material` and `q`. The
card reads the server's page.

This is a breaking change for anything reading that endpoint, so it is in the
changelog under Changed with the sentence that matters first, and every
reader in the tree was moved with it: master data now pages it on the server
too, the SPC picker reads to the end of it and says whether it got there, the
simulator pages through it (it is not a screen, and it inspects against every
characteristic), and the `quality` agent tool reports total and shown rather
than presenting the first two hundred as the plant.
