---
title: /dashboard/ops (control-room): table font-size changed '12.5px' -> '14px'
status: done
conversation: 0
turns: []
route: 
plant: 
created: 2026-09-02
updated: 2026-09-02
branch: 
tags: [fsmes, design, backlog, ui-check]
---

# /dashboard/ops (control-room): table font-size changed '12.5px' -> '14px'

**inbox** — found by `fsmes ui-check` (deterministic; no model involved).

- kind: `style-drift`
- detail: /dashboard/ops (control-room): table font-size changed '12.5px' -> '14px'
- fingerprint: `bc58fb4f8e4d`

**Resolved:** the drift was deliberate - the Local AI panel (merged
2026-09-02) added a table to /dashboard/ops after the baselines were
accepted. All four theme baselines re-accepted on
design/reaccept-baselines, per the contract. Original guidance follows:

Judged by `/design-triage`. If this drift was deliberate, the fix is
`fsmes ui-check --accept` in the branch that made the change - not
closing this note by hand.
