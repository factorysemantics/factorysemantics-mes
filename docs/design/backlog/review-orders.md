---
title: Orders screen — persona walkthrough and redesign
status: built
route: /dashboard/orders
branch: main
tags: [fsmes, design, review]
---

# Orders — the walkthrough this redesign is built against

## Persona A — production manager, Monday 07:40

Maria plans the week. She opens Orders with coffee in one hand.

1. She needs *her* slice in two gestures: FG-BOTTLE, open work, due this
   week. Today that is six full-size controls wrapping across three lines.
2. She clicks the "Open orders" tile and cannot tell it did anything — no
   selected state. She clicks it again to be sure and still cannot tell.
3. An order has a supplier problem. There is no way to say *stop* without
   pretending it is cancelled. (Fixed at the model: HOLD exists now.)
4. A hot order came in overnight. There is nowhere on any screen to create
   an order — she would have to ask an agent or curl.
5. For any open order she wants one glance to answer: will it finish on
   time (the plan's promise), and will any station run short (staging)?
   Both APIs exist; neither is on the screen.

## Persona B — line-side operator, mid-shift

Dev does not plan; Dev checks. "Is WO-4718 nearly done, and did my station's
scrap count land?"

6. The list defaults to newest-first with open work findable in one tap
   (the tile), and the route table answers per-station in one more.
7. Nothing on this screen should ask Dev for capabilities Dev lacks: no
   create button, no lifecycle buttons, no staging actions — those render
   only for people who hold the capability.

## The checklist the build must satisfy

- [x] Filter chrome is one 40px row; dates and the status long-tail live in
      popovers; Clear appears only when something is filtered.
- [x] A selected tile is visibly selected (aria-pressed + accent), and
      clicking it again clears the filter.
- [x] The misaligned "Due" label idiom is gone (labels above controls, in
      the popover).
- [x] "New order" exists, gated on orders.create, and can release
      immediately for holders of orders.release.
- [x] Detail shows lifecycle actions per status: Release / Hold (reason
      required) / Resume / Close / Cancel (confirm) — all capability-gated.
- [x] Detail shows the promise (finishes / on-time-or-late) and per-station
      staging with shortfalls, for open orders.
- [x] on_hold renders as a distinct planned-stop colour, not as an error.
- [x] Existing anchors survive; new guides cover create + release.
- [x] orders.js uses FS.* (first page off the duplicate helpers).

## Built — findings

Everything on the checklist landed. Two notes for the next reviewer:

- The tile test pinned the literal wiring form (`$("#card-open").addEvent…`);
  the redesign moved the three tiles into one TILE_VALUES loop so a fourth
  tile cannot be added without being wired. Test adapted to the intent.
- Out of scope, spotted while building: the create-order form offers every
  material, including raw materials that have no routing - creating an order
  for RAW-WATER will fail at the API. Filtering to materials with routings
  needs an API affordance. → new inbox note.
