# Kestrel Motors body shop — a simulated body-in-white line

**Nothing here is a real plant.** Kestrel Motors, its assembly plant and its
body line are invented. The robots, their welds, their faults and the hour
they play are generated from `line.json` by `fsmes sim-generate`; no
production in this pack ever happened.

## What the plant is

One body line making bodies in white — the welded steel shell of a car before
paint — in two styles, a 4-door saloon (`BIW-S4`) and a 5-door estate
(`BIW-E5`). Eight cells in a straight run, twelve metres apart, three bodies
of buffer between each, a body every 60 to 75 seconds:

| Cell | What it does | Robots |
|---|---|---|
| UB10 | underbody, first cell | 4 |
| UB20 | underbody respot | 3 |
| FR10 | framing — the bottleneck, 70.6 s | 4 |
| FR20 | framing respot | 3 |
| RS10 | roof and side respot | 3 |
| RS20 | closures respot | 2 |
| MF10 | metal finish: people, slower and noisier | 0 |
| QI10 | gap-and-flush vision; rejects go to rework | 1 |

Twenty robots, each a **part of its cell** (`FR10-R1` …), not a machine of
its own: a robot has a state, a fault number, a cycle time, its weld current
and its tip wear (welds since its last dress), and it counts nothing — its cell
counts the bodies. In the master data a cell is a `work_unit` and a robot an
`equipment_module`, so the robots are listed under their cells and stay off
the floor's machine lists, the OEE and the 3D line.

**A cell's state follows its robots.** One robot down is the cell down, with
that robot's fault as the reason (101 *Weld gun fault*, 102 *Robot collision
stop*); one robot away for its tip dress is the cell in **setup**, not down;
a cell with nothing to work on is **idle**, starved or blocked, in the same
words as bottling.

## The hour it plays

`fsmes sim-generate labs/multiplant/bodyshop/line.json` writes `out/` (about
4 MB, never committed). Seed 26, one hour, looping:

- **15:00–23:00** FR10's robot 2 has a weld gun fault. FR10 is down eight
  minutes with it; FR20 and everything after it starve; UB10 and UB20 fill
  their buffers and block.
- **40:00–42:00** RS10's robot 1 stops on a collision; RS10 is down two
  minutes.
- **All hour** every welding robot dresses its tips every 300 welds, 8 s
  each, staggered — two to five dresses a robot an hour — and MF10's cycles
  wander ±18 % around their mean.
- 47 bodies come off QI10 in the hour.

`out/scenario.md` lists it again, generated with the data.

## Your own models

The 3D tab draws every cell as the built-in `generic` shape. To show your own,
put GLB files in a `models/` folder **beside `line_layout.json`** — one per
cell, named for its code: `models/UB10.glb`, `models/FR10.glb` and so on, or
`models/generic.glb` for all eight. Metres, origin at the centre of the
footprint, front toward −Z. The rules are in the repository README,
*Your own machine models*, and in `docs/operate/packs.md`.

## Running it

```bash
fsmes pack check bodyshop
fsmes plant bodyshop init
fsmes plant bodyshop start     # http://127.0.0.1:8030/dashboard
fsmes plant bodyshop stop
```
