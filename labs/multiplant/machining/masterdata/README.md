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
