---
title: Roles can be edited on the admin page
status: built
conversation: 5
turns: [12]
route: /dashboard/admin
plant: bottling.db
created: 2026-09-14
updated: 2026-09-14
branch: feat/admin-edit-roles
tags: [fsmes, design, backlog]
---

# Roles can be edited on the admin page

**built** — The edit affordance existed; on the roles the product ships the edit was silently reverted by the screen's own refresh.
## The idea

At `/dashboard/admin`, looking at the Roles panel: "I should be able to edit
roles." One sentence, twelve days in the queue.

## Assessment

The screen and the API had both moved on since the conversation. When Scott
said this on 2026-09-02 there was no edit affordance at all — the qwen reply
in the transcript says so, correctly. By the time the idea was triaged,
`src/fsmes/api/routers/admin.py` had `PUT /roles/{code}` under
`require("users.manage")`, `src/fsmes/web/admin.js` had an Edit button on each
role card that fills the create form in place, and `src/fsmes/mcp/masterdata.py`
had `update_role` wrapping the same endpoint. On paper the idea was already
done.

It was not. Three things were wrong, and the first one made the whole feature
a lie.

**1. An edit to a shipped role did not survive the screen's own refresh.**
`GET /admin/roles` calls `auth.ensure_builtin_roles`, which exists so that a
plant installed before a capability existed still picks it up on the roles the
product ships. It does that by rewriting `capabilities` on any role marked
`builtin` whose bundle differs from the current spec — which is exactly what an
edit produces. So: redefine `supervisor`, get a 200 and a toast saying
"redefined — effective immediately", and watch the card show the old bundle
again eight seconds later when `refresh()` runs. Nothing anywhere said it had
happened. Six of the seven roles a fresh plant has are `builtin`, so on a plant
nobody had customised yet, editing a role essentially did not work.

The fix is to decide who owns a role. Redefining what a shipped role grants now
clears its `builtin` mark: it becomes this plant's own role, the card stops
calling it built-in, and it drops out of the top-up. A role nobody has changed
still gets the top-up, which is the whole point of it. Saving a shipped role
back without changing its capabilities — a better description, say — leaves it
in the product's hands. No schema change: `builtin` is a column that already
exists and already means "one of ours to maintain".

**2. `admin` could be emptied.** `PROTECTED = ("admin",)` stopped the role being
*deleted*, for a stated reason: an MES with no administrator is a plant nobody
can administer. `update_role` checked for misspelt capabilities and nothing
else, so `PUT /admin/roles/admin` with `["plant.read"]` returned 200 and took
`users.manage` off the only role that had it — and the screen that could grant
it back is the screen you have just locked yourself out of. Same destination,
different door. Now a 400 that says so. (In practice the lock-out healed itself
on the next roles listing, because the top-up in (1) wrote the capabilities
back — one bug covering for another is not a guard.)

**3. Edit mode could not be left.** Pressing Edit sets `code` read-only and
retitles the button; there was no way back. The panel stayed stuck redefining
that one role until the page was reloaded, and pressing the button again
(still labelled `Save <code>`) wrote over that role rather than creating the
new one you had just typed. The form now names the role it is editing and
carries a Cancel.

Judged against 300 people and 60 machines: this is the screen where a plant
decides who may book production and who may close a non-conformance. A change
that appears to save and then silently reverts is worse than no edit button at
all, because the admin walks away believing the plant is configured.

## What was done

Branch `feat/admin-edit-roles`, on `public/main`.

- `services/capabilities.py`: `REQUIRED` (what a protected role may not be
  saved without), `missing_required()`, `differs_from_shipped()`.
- `api/routers/admin.py`: `update_role` refuses a protected role that would
  lose a required capability, clears `builtin` when the bundle changes, and
  audits `builtin` alongside the capabilities in `before`/`after`.
- `services/auth.py`: `ensure_builtin_roles` says in its docstring that it
  only maintains roles the plant has left alone.
- `web/admin.html`, `web/admin.js`: `startRoleEdit` / `endRoleEdit`, a titled
  form, a Cancel, and a save message that says when a role has just left the
  set the product maintains.
- `mcp/masterdata.py`: `update_role`'s docstring says the list replaces rather
  than adds, and what redefining a shipped role costs.

Nine prose-named tests in `tests/test_capabilities.py`, four of which fail on
the code as it was. The suite: 1480 passed, 2 skipped; ruff clean;
`mkdocs build --strict` clean. Looked at in a browser against a seeded demo
plant: supervisor edited to drop `orders.close`, still without it after two
refresh cycles, its built-in badge gone while viewer keeps its own; the admin
refusal read on screen in its own words.

No schema change, no migration.
