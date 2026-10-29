"""A Satisfactory game factory, read as a plant.

Satisfactory is a factory-building game. The Ficsit Remote Monitoring mod
serves the running game's buildings, power and session over HTTP, and a
poller outside this product asks it once a minute and writes what it said to
one JSON line per endpoint per poll, a file a day. This package reads those
files - never the game, never the mod's port - and turns them into what any
other plant's reader writes: equipment states, whether the MES could see a
machine, and production.

Three modules, smallest first:

`feed`    the poll files, read as polls. Nothing about the MES.
`rules`   one building in one poll, read as a machine state. Pure.
`reader`  the polls, written into the MES through the services the OPC agent
          uses: `equipment.set_state`, `connection.set_connection`,
          `execution.report`, `counters.save`.

and `pack`, which writes a plant pack's master data from one day's file.

It is read only by construction. There is no order tag and nothing to
command: the game is played by a person, and the MES watches.
"""
