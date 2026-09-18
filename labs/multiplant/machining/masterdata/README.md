# Northgate Machining — master data, as data

What `seed.py` used to build in Python. Six files, read by `fsmes pack
apply`, checked offline by `fsmes pack check`.

Two things are deliberately **not** here:

- **Rated cycle times.** An equipment entry leaves `ideal_cycle_seconds` out
  and it is read from this pack's own `tag_map.json`. OEE performance is
  ideal cycle × count ÷ runtime, so a rate repeated here that drifted from
  the line that generated the data would produce a performance figure that
  means nothing.
- **Anything generated.** A pack is what a person wrote. Line data lives in
  `out/`, comes from `fsmes sim-generate`, and is never committed.

## The order book, and the arithmetic behind it

`work_orders.json` is a schedule, not a single order — since 2026-09-18, when
a bottling plant next door reported one order seventy times over because
nothing ever released a second one.

- The slowest station in `tag_map.json` sets the cell: the mill at **3.333 s**
  a bracket, which is **1,080 an hour**, or 8,640 in an eight-hour shift.
- **Nine orders, 27,600 brackets**, which is **25.6 hours** of that rating.
  Two to three orders a shift, each 2–4½ hours.
- `WO-NG-7001` keeps its code, its 600 and its place at the head of the book.
- **One product.** `FG-BRACKET` is the only finished material `RT-BRACKET`
  makes, so every order is for it.

One order is released; the other eight are planned, due 2 to 27 hours out.
The simulated shift supervisor finishes an order at its quantity and releases
the next; an empty book is said once and never filled with an invented order.
