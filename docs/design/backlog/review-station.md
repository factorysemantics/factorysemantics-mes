---
title: Station view — persona walkthrough
status: built
route: /dashboard/station
branch: main
tags: [fsmes, design, review]
---

# Station — the walkthrough this screen is built against

## Persona B — line-side operator, standing up

Dev runs FILL01. Gloves sometimes, safety glasses always, screen at arm's
length beside the machine. Dev does four things a shift and none of them
should take more than three taps:

1. **The machine stops.** Dev hits the state that is true — usually Down —
   and the screen *demands a reason before accepting it*, because unlabelled
   downtime is the thing the pareto cannot explain. Two taps + a short text.
2. **A batch comes off.** Book output: the order is already selected (it is
   the one this machine is running), type good/scrap on big inputs, Book.
3. **Material arrives.** Issue the lot to this machine's station in the
   running order.
4. **A PM comes due on this machine.** Dev sees it on this screen — not on a
   management list — starts it, does it, closes it with findings.

Rules the layout must obey:
- One machine. Chosen once, remembered (localStorage), changeable in one tap.
- Everything about *this* machine; nothing about the rest of the plant.
- Touch targets ≥ 48px. No dropdowns where four big buttons will do.
- Capability-gated: a viewer sees state and queue, no action buttons.
- The screen states its data sources (principle 4).

## Persona A — the manager's stake in this screen

Every reason Dev types here is a row in the downtime pareto; every explicit
order booking is month-end reconciliation that never happens. The manager
never opens this screen — they consume its honesty.

## Checklist

- [x] State buttons: the four states as ≥48px buttons; current state shown;
      entering `down` requires a reason (input appears, confirm disabled
      until non-empty). Gated `equipment.state`.
- [x] Queue: this machine's dispatch list with op start/complete
      (`production.book`), the running op first.
- [x] Book output: order preselected from the queue, seq sent, big inputs.
- [x] Issue material: lot + qty to the running order at this station
      (`production.consume`).
- [x] Maintenance: open orders for this machine with start / complete-with-
      findings (`maintenance.perform`); due-soon plans visible.
- [x] Machine picker remembered per browser; deep-linkable (?m=FILL01).
- [x] Guides authored: label-a-stop, do-a-maintenance-job.
- [x] In the nav manifest; PAGES/ROUTES covered by tests.

## Findings (2026-08-31, built)

- All checklist items hold on both plants: machine remembered per browser,
  deep link ?m=CODE works, down demands a reason before the confirm button
  lives, book/issue/maintenance each within three taps of page load.
- The queue and book-output select share one dispatch fetch; the running op
  is preselected, so the common case is type-quantity-tap.
- Issue material deliberately targets the running order at this station
  rather than asking the operator to pick one - the station knows.
- Out of scope, filed separately: nothing new; maintenance list is empty on
  machines with no plans, which reads fine ("Nothing owed on this machine.").
