# A Satisfactory game, as a plant

A game factory read through the Ficsit Remote Monitoring mod; simulated by the
game, not by us.

Satisfactory is a factory-building game. The Ficsit Remote Monitoring mod
answers HTTP requests about the running game: its buildings, its power
circuits, its session. A poller outside this repository asks once a minute and
writes the answers to one file a day, `YYYY-MM-DD.jsonl` (gzipped once the day
is over), one JSON line per endpoint per poll. This plant reads those files and
nothing else. It never talks to the game or the mod, and it never writes to the
poller's folder.

    FSMES_PLANT_REGISTRY=labs/satisfactory/fleet.toml fsmes plant satisfactory init
    FSMES_PLANT_REGISTRY=labs/satisfactory/fleet.toml fsmes plant satisfactory run

`run` starts the dashboard and `fsmes run-satisfactory`, which follows the
folder named in `plant.toml` (`[inbound] satisfactory_feed`). To read a folder
once and stop: `fsmes run-satisfactory <folder> --once`.

## What the MES makes of a building

| The game says | The MES writes |
|---|---|
| the building's circuit has tripped its fuse | **down**, reason `fuse` (every building on the circuit) |
| a recipe, but no power line | down, `no_power` |
| the player switched the building off | idle, `paused` |
| placed, no recipe | idle, `no_recipe` |
| producing | running |
| output too full for another cycle | idle, `blocked` |
| an input short of one cycle | idle, `starved` |
| none of those | idle |

Production is the game's own rate (`CurrentProd`, units a minute) times the
minutes since the building's previous poll, booked in whole units as
unassigned production: nobody ordered it.

## What the MES does not say

- **A paused game, a gap in the polls, or a feed that has stopped is not
  observed, never down.** The buildings' connection closes with the cause
  ("the game was paused", "no poll from the game for 19 min: the game was
  closed or the poller was off") and their state history stops, so no OEE
  figure counts the time and the downtime pareto does not list it.
- **Quality is always 100%.** The game has no scrap, so nothing is ever booked
  as scrap, and every screen says so in the bar at the top.
- **No energy figure and no parts mix** in this first slice.

## The master data

`masterdata/` was generated from one evening's file and committed:

    fsmes satisfactory-pack ~/Work/box/data/frm/2026-10-28.jsonl.gz labs/satisfactory/masterdata \
        --enterprise "FICSIT:FICSIT (a Satisfactory save)" --site "SAVE:The save" \
        --area FACTORY:Factory

One machine per building, coded from the game's own ID
(`Build_ConstructorMk1_C_2146677430` → `CONSTRUCTORMK1-2146677430`) and named
for what it makes ("Constructor · Iron Rod"). There is one work centre per kind
of building. A building placed later is created by the reader the first time a
poll lists it, filed under `[inbound] satisfactory_area`, and a building whose
recipe changes is renamed. The product names none of the plant's codes: the
enterprise, site and area are given to the generator, and the area to the pack.
