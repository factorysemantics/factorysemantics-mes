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
| a building left out of **5** unpaused lists of the same save in a row (`satisfactory_torn_down_polls`) | it is **retired** "torn down": off the floor and out of the coverage count, its history kept. One list leaving it out is not enough; the mod has been seen to drop a building for a poll. A paused poll does not count |
| a building list under a **different** save | every building of the game's not in the list is **retired**: off the floor and out of the "watching" count, with the reason "not in save <name>", its history kept |
| a retired building in the list again (that save loaded again, or a torn-down one rebuilt with the same ID) | it comes back on the floor |
| a session or power answer a second or two after its poll's building list (the poller sometimes writes one late) | it is read with that poll: an answer within 5 seconds of a poll that has a building list, for an endpoint the poll has not answered yet, belongs to it. A poll that is only a late answer, with no building list of its own, is still skipped as "no building list" |
| no session answer in this poll | the last save named still holds: a change is a change only when a name is present and different |

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

## The last time you played

Shift analysis opens on **the last time you played** on this plant: the
latest stretch of polls with no pause and no gap in it, named with its times
("last played 04:41–05:23 today", on the plant's clock, start rounded down
and end up to the minute). The hours choices stay in the same picker, and an
address that asks for `hours=` keeps them. Every panel - OEE, Energy, Parts
mix - windows on it the same way, and the API takes it as `shift=last_play`.

The reader writes one plant reading per watched poll, `FEED.Stretch`: 0 for
the first poll of a stretch (the reader started, or the poll before was a
pause or a gap ago), 1 for a poll that continues it. Nothing is booked across
a gap or a pause, so the time between polls of one stretch is exactly the
time production could be booked in: the feed watched 100 % of its own
window. A plant no game feeds has no such reading and no such choice.

Coverage on the window keeps its rule (the 80 % floor is unchanged): a
building placed in the last minutes of the stretch was not watched before it
existed, so a line where the player was building can read below 100 %.

## Parts mix

A Satisfactory factory is not asked for 500 plates by Friday; it is asked for
a steady mix. The **rate order** says it: a name and, per item, a target rate
in units a minute. It has no quantity and no due date, and never closes. The
shares follow from the rates. A plant holds one or none.

Shift analysis has a **Parts mix** panel under Energy, over the same window:
one row per item in the order or made in the window, with target /min, actual
/min, target share, actual share and gap /min, shortest first, and the
game's own figure beside it.

- **actual /min** is the good units the MES booked for the item (the same
  bookings OEE counts, each put to the item its building was making) over
  the minutes the feed was watched in the window.
- **target share** is the item's rate over the order's total; **actual
  share** is its actual over the actual of the order's items (with no order,
  of every item made).
- An item made but not in the order says **no target**, never 0. A plant with
  no order shows no target columns. A plant with no order and no parts
  readings has no panel at all.
- **game made /min** is the check, not a second answer: the game's own
  production statistics (`getProdStats`), averaged over the window's polls,
  kept as plant readings `PARTS.<item>.MadePerMin`, `.MaxPerMin` and
  `.ConsumedPerMin`. It counts every building of the save; the MES books only
  running intervals it saw both ends of, so the two differ when a building
  starts or stops mid-window. Items the game made that no building books
  (ore from miners) are named under the table.

Each machine's OEE tab has one parts line: the item it makes, its rate over
the window, the plant's rate for the item, and the order's target. The AI
tab reads the same figures from `oee_breakdown`'s `parts_mix` block. The rate
order never feeds OEE.

**Setting the rate order.** `masterdata/rate_orders.json` ships an example -
the design page's "60 iron parts a minute, mix 40/40/20", read as 24 Iron
Plate, 24 Screws and 12 Iron Rod a minute. Edit it to what you want the
factory to make; item names are the game's (`Iron Plate`, as the panel shows
them). The pack seeds it only when the plant has no rate order, so a plant's
own order is never overwritten. To change it on a running plant, as an admin:

    curl -X PUT http://127.0.0.1:8130/rate-order -H 'Content-Type: application/json' \
        -H "Authorization: Bearer $TOKEN" \
        -d '{"code": "MIX", "name": "Iron parts mix", "items": [
              {"item": "Iron Plate", "per_minute": 40},
              {"item": "Iron Rod", "per_minute": 20}]}'

`GET /rate-order` reads it (any signed-in role); `DELETE /rate-order` takes
it away, and the plant has no target again. Every change is audited.

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
