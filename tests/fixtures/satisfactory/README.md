# One evening of a Satisfactory game, trimmed

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
