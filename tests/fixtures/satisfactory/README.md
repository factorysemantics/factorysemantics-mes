# Two evenings of a Satisfactory game, trimmed

`2026-10-28.jsonl.gz` is cut from the poll file a poller wrote on 2026-10-28
from the Ficsit Remote Monitoring mod (a file a day, one JSON line per
endpoint per poll). Ten of its polls, 21:03 to 21:15 Chicago time - twelve
moments, because twice the poller wrote the session answer a second after the
rest of its poll - with these changes and no others:

* only the `getFactory`, `getPower` and `getSessionInfo` answers are kept;
  the player and production-statistics answers are left out;
* each building loses its map position, bounding box, colours and map
  feature, and the session answer keeps only `SessionName` and `IsPaused`;
* the polls at 21:08, 21:09 and 21:10 are removed, so the file has a
  four-minute gap in it;
* the session answer at 21:13:17 is changed to `"IsPaused": true`. The game
  was never paused that evening; the change is there so a test can read a
  paused poll from the real file's shape.

The polls before 21:06 have fourteen of the eighteen buildings on a
tripped fuse; from 21:06 the fuse is reset and the factory runs.

`2026-10-29.jsonl.gz` is the next evening, the whole of it: 203 polls, 22:15 to
01:37 UTC (17:15 to 20:37 Chicago), in a different save - "fsmes", not "Hello
World" - so the two files together show the player loading another save. Only
the trimming above was done to it (the same keys kept on each building, power
circuit and session answer); no poll was removed or changed.

`2026-10-30.jsonl.gz` is the first night with the generator answer
(`getGenerators`), the whole of it: 43 polls, 09:41 to 10:23 UTC (04:41 to
05:23 Chicago), save "fsmes". The trimming above, plus: each building keeps
`RecipeClassName` too, and each generator keeps only its `ID`, `Name`,
`ClassName`, `BaseProd`, `DynamicProdCapacity`, `RegulatedDemandProd`,
`IsFullSpeed`, `CanStart`, `LoadPercentage`, `PowerProductionPotential`,
`FuelAmount`, `FuelResource`, `FuelInventory` and `PowerInfo`. The power
answer is kept whole. No poll was removed or changed. At 05:14 a second
circuit appears for one poll while the player wires one in.

`ref/getRecipes.json.gz` is the game's recipe list the poller fetched once
into its `ref/` folder on 2026-10-29, cut to the twelve recipes any of the
three files' buildings use, each keeping its `ID`, `Name`, `ClassName`,
`Products`, `ProducedIn` and `FactoryDuration`.
