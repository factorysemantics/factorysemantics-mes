# Cutlery Works — master data, as data

The cutlery plant's equipment, materials, routings, quality specs, lots and
order book, as the files `fsmes pack apply` reads and `fsmes pack check`
validates offline. This is what lets `fsmes demo cutlery` build the plant on
a PC with nothing cloned and no script run.

## Which command regenerates it

Six of the files are generated and must never be edited by hand:
`equipment.json`, `materials.json`, `routings.json`, `quality_specs.json`,
`lots.json`, `work_orders.json`. From the repository root:

    python3 labs/cutlery/build_config.py --scale 0.01
    python3 labs/cutlery/make_tag_map.py
    python3 labs/cutlery/init.py --write-masterdata

The first two rewrite `line.json` and `tag_map.json` beside this folder at the
lab's teaching pace (a hundredth of the customer's rate); the third reads them
and writes these six files. Commit all eight together.

The other two, `downtime_reasons.json` and `nc_severities.json`, are written
by a person: they are the words people read on the floor, and no generator
should be inventing those.

## Why files, and why now

Until 2026-11-12 this plant's master data was written by `init.py` straight
into a database, and a pack carries no code (decision 0022) - so the plant
could only be built from a checkout, by a person who knew to run the script.
Bottling had the same problem and solved it the same way on 2026-09-14
(`labs/multiplant/bottling/masterdata/README.md`). Scott wanted to show this
plant from his own PC on 2026-11-20, so it ships in the package now.

`init.py` still works and the lab still runs it: it finds the plant already
built by the pack and says so.

## How to tell it has not drifted

`tests/test_the_cutlery_pack_builds_the_plant_init_py_builds.py` builds one
database from these files and one with `init.py`, from the same `line.json`,
and compares them: the 83 machines and the tree above them, the materials,
the routings, the specs, the lots and how much of each is left, the orders
and which are released. It also regenerates these files from the committed
`line.json` and fails if they differ by a byte.

## What is in it

- **150 equipment rows**: one enterprise, one site, four areas (Moulding,
  Stacking, Wrapping, Palletizing), 61 lines and **83 machines**. Rated cycle
  times are left out; they are read from `tag_map.json`, so OEE performance
  is measured against the same rate the line was generated at.
- **8 materials**: the three utensils, the stack, the wrap, the pallet, and
  the two things consumed - polypropylene resin and paper plates.
- **61 routings**, one per line, each a line's own stations in order.
- **15 quality specs**: five dimensions per utensil, which the inspector
  records every fifteen minutes and the pallet certificate states a Cpk for.
- **25 lots**: a resin lot for each moulding line and a plate lot for each
  wrapper, each sized for thirty days of its line at this pace.
- **122 orders**: two per line, `WO-<line>-0001` released and issued its
  line's lot, `WO-<line>-0002` planned behind it. Each is two hours of its
  line at this pace. `floor.json`'s `planning.each_line` keeps one planned
  order behind every line for as long as the plant runs, so the book never
  empties.

An order names its line (`"line": "FORK1LINE"`) because three lines make the
same fork, and carries what is issued to it when it is released
(`"issue": {"lot": ..., "quantity": ..., "seq": 10}`), so every piece traces
back to a resin lot through the order that made it. Both fields arrived in
the pack format with this plant; see `docs/operate/packs.md`.
