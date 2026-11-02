# Kestrel Motors body shop — master data, as data

Read by `fsmes pack apply`, checked offline by `fsmes pack check`. Invented,
like the rest of this pack.

- **Equipment.** Kestrel Motors → Kestrel Assembly (KM1) → Body Shop (BIW) →
  Body Line 1 (BS1) → eight cells (`work_unit`) → twenty robots
  (`equipment_module`, children of their cell). Rated cycle times are left out
  and read from `tag_map.json`, as in every other pack.
- **Materials.** Two panel sets in, two bodies in white out: `BIW-S4` (saloon)
  and `BIW-E5` (estate), one body a carrier.
- **Routings.** One per style, eight operations, one on each cell.

## The order book, and the arithmetic behind it

Orders are numbered the way an assembly plant numbers its build — `BLD-`
and a six-digit build sequence — not as ACME's or Northgate's work orders.

- The slowest cell in `tag_map.json` sets the line: FR10 at **70.6 s** a
  body, which is **51 an hour**.
- **Twelve orders, 1,260 bodies**, which is **24.7 hours** of that rating;
  styles alternate, 60 to 120 bodies an order.
- `BLD-110201` is released; the other eleven are planned, due 3 to 23 hours
  out. The simulated supervisor finishes an order at its quantity and
  releases the next; an empty book is said once and never filled.
