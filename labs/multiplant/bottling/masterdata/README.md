# ACME bottling — master data, as data

The six-station reference line, as the nine files `fsmes pack apply` reads
and `fsmes pack check` validates offline.

## Why this exists now, when it deliberately did not before

This line ships *inside the product*: `fsmes seed-kepsim` builds it, because
it is the twin's own reference line and the demo plant's. The argument for
leaving it out of the pack was that copying it here would be one copy that
could drift from the other.

What that argument cost, measured on 2026-09-14: `fsmes fleet create` and
`fsmes plant bottling init` both left this plant with **zero equipment** and
nothing anywhere said so; `fsmes score bottling` and `fsmes sweep bottling`
died on their first read — `GET /analysis/timeline` → 404, *"no line has any
machines on it yet — seed a plant first"* — because the ephemeral plant a
scored run builds applies the pack and nothing else; and the one step that
did seed it, `init.py`, was a script the registry had stopped naming on
2026-09-13 and which a person therefore had to know to run.

A plant that needs a step nobody can see is house rule 4's own example of a
bug. So the line is here, and `init.py` is gone: `fleet create`, `plant
init`, `score` and `sweep` now build the same plant the same way.

## Where it came from, and how to tell it has not drifted

Generated once by running `fsmes.seed_kepsim.seed_kepsim_line` into a scratch
database and reading the rows back, so these files started life as exactly
what the code they replace produces.
`tests/test_bottling_pack.py::test_the_bottling_pack_builds_the_line_the_products_own_seeder_builds`
keeps them that way: it seeds one database each way and compares them, and
fails if either side changes alone.

## What is not here

- **Rated cycle times.** An equipment entry leaves `ideal_cycle_seconds` out
  and it is read from this pack's own `tag_map.json`, exactly as machining's
  are. OEE performance is ideal cycle × count ÷ runtime, so a rate repeated
  here that drifted from the line that generated the data would produce a
  performance figure that means nothing.
- **Anything generated.** Line data lives in `../../kepsim/out`, comes from
  `fsmes sim-generate`, and is never committed.

`RAW-CARTON` is 24 bottles to a case, so its BOM quantity is one twenty
fourth and is written to the last digit rather than rounded: a quantity that
is nearly right is a shortage report that is nearly right.
