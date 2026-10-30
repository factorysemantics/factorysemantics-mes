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

| The game's production figures | The MES books |
|---|---|
| the building was **running** at its previous poll | its rate now (`CurrentProd`, units a minute) × the minutes since that poll, in whole units, as unassigned production: nobody ordered it |
| it was anything else at its previous poll (starved, blocked, on a tripped fuse, switched off) | nothing - `CurrentProd` is a rolling figure that keeps reading for minutes after a building stops, and booked production implies run time |
| a gap or a pause lies between the two polls | nothing - the rate at either end says nothing about the time between |

The game's own `IsProducing` decides *running*: in both evenings' polls,
every building that said it was producing had a rate above zero, and about
a third of the readings with a rate above zero came from a building that had
stopped.

## One save on the floor

The session answer names the save being played (`getSessionInfo.SessionName`).

| The polls say | The MES does |
|---|---|
| a building list under the same save as the last one | nothing more: a building missing from it is unseen, "no longer in the game's building list" (torn down, or out of the mod's reach) |
| a building list under a **different** save | every building of the game's not in the list is **retired**: off the floor and out of the "watching" count, with the reason "not in save <name>", its history kept |
| a retired building in the list again (that save loaded again) | it comes back on the floor |
| no session answer in this poll (about one poll in ten; the poller sometimes writes it a second late) | the last save named still holds: a change is a change only when a name is present and different |

The bar at the top of every page, and `/health`'s `source`, start with the
save: "save fsmes · A game factory read through ...".

## What the MES does not say

- **A paused game, a gap in the polls, or a feed that has stopped is not
  observed, never down.** The buildings' connection closes with the cause
  ("the game was paused", "no poll from the game for 19 min: the game was
  closed or the poller was off") and their state history stops, so no OEE
  figure counts the time and the downtime pareto does not list it.
- **Quality is always 100%.** The game has no scrap, so nothing is ever booked
  as scrap, and every screen says so in the bar at the top.
- **No parts mix** yet. A building whose recipe
  changes is rated at its latest recipe, so units made under an earlier,
  faster one can make its OEE say "counted work outruns the run time" -
  correctly: two parts are being counted as one.
- **Energy never enters OEE.** It is drawn beside it, in its own panel.

## Energy

Shift analysis has an **Energy** panel under OEE, and each machine's OEE tab
has one energy line, over the same window. A plant with no power readings
(every plant that is not a game) has neither.

- **The bar** is what the circuits draw now (MW), against their capacity
  (MW), with the peak demand marked - what every building would draw at
  once. Headroom is capacity less peak demand; below zero, the fuse goes
  the moment everything wants power. A tripped fuse is named with the demand
  that did it: "fuse tripped: demand 141 MW over capacity 100 MW".
- **MWh** is each building's draw integrated over the polls, booked only
  for an interval the MES saw both ends of. A pause or a gap books none, as
  it books no production and no state. Idle draw counts: a building waiting
  for input still draws.
- **kWh a part** is that energy over the parts booked in the same window.
  **Ideal** is the building's nominal draw (its `MaxPowerConsumed` at 100 %
  clock, as the feed reports it) over the recipe's `FactoryRate`, from the
  game's recipe list in `ref/getRecipes.json` beside the day files: a
  Constructor at 4 MW making 15 a minute is 4 x 1000 / 60 / 15 = 4.44 kWh a
  part. A recipe that is not in the list has ideal **unknown**, and says so;
  none is guessed. **Gap** is kWh a part less the ideal, worst first.
- **Generators** are equipment, under work centre GENERATORS: running or
  idle, with what they make (MW), what they could make, their load and
  their fuel as readings.

The readings are tags in the history like any agent's: `<CODE>.PowerMW`,
`.PowerMaxMW`, `.EnergyMWh`, `.IdealKWhPerUnit`; for a generator
`.OutputMW`, `.CapacityMW`, `.LoadPercent`, `.Fuel`, `.FuelItems`; and per
circuit `POWER.CIRCUIT-<n>.CapacityMW`, `.ConsumedMW`, `.PeakDemandMW`,
`.ProductionMW`, `.BatteryPercent`, `.BatteryCapacityMWh`, `.FuseTripped`.
The AI tab reads the same figures from `oee_breakdown`'s `energy` block.

The mod's `PowerProduction` reads 0 on every circuit so far, so nothing
treats it as the circuit's output; it is kept as a reading because it is
what the feed said. Its generators carry no `ProductionRate`;
`RegulatedDemandProd` is the figure that moves with the load.

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
