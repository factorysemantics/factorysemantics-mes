# KepSim scenario timeline

One looping hour, 1 row/second, seed 42. `TSec` in every table is the row's
sim-time — use it to line any observation up with this timeline.

| t (mm:ss) | TSec | Event | The lesson it stages |
|---|---|---|---|
| 02:00–08:00 | 120 | Refill NozzlePressure held 10 % low (2.60→2.34 bar), no alarm; FillWeight −4 g for the same six minutes | the easy version of the washer story: the cause is one tag over on the same machine, and a panel on a control-chart point should find it |
| 10:00–10:12 | 600 | LD jam, 12 s | a short stop that barely ripples — and one the simulated floor never sees, because it is shorter than one look |
| 15:00–25:00 | 900 | RD MotorTemp drifts 62→88 °C | the precursor signal before a failure |
| 25:00–27:00 | 1500 | **RD breakdown** | watch STARVED walk downstream and BLOCKED walk upstream, one buffer at a time |
| 30:00–35:00 | 1800 | **Washer scrap burst** (~10%), WashTemp ~3 °C hot; Refill FillWeight −9 g, no alarm on the filler | the washer story: name the cause, not the symptom |
| 40:00–44:00 | 2400 | **Changeover**, OrderId 4711→4712 | planned stop ≠ downtime (OEE availability) |
| 44:00–44:30 | 2640 | Refill FillWeight +5 g — the first bottles after the restart run heavy | a point above the limit whose cause is a *planned* stop that ended moments earlier |
| 45:00–60:00 | 2700 | Refill **FillWeight stops arriving** — the filler runs on, every other signal on it reports | a stale value is not a steady one: the MES's last sample keeps the moment it really arrived, and a floor that checks the age records a plausible value with no gauge and no station rather than writing the frozen number down once a minute |
| 50:00 | 3000 | **RD counter reset** to 0 | the RB Counts Wrong classic, on demand |
| ~every 2 min | — | Palletiser micro-stops, 8–18 s | death by a thousand cuts (OEE performance) |
| every loop | 3600→0 | file wraps, all counters snap back | a free counter-reset drill every hour |

States: 0 stopped · 1 running · 2 starved · 3 blocked · 4 down · 5 changeover

## The five causes behind a point on the fill-weight chart

Scott, 2026-10-05: *"click an SPC point and see why it is there."* Four of the
five are above, because they are things the machines do. The other two belong
to the people and are in the plant pack's own
[`floor.json`](../multiplant/bottling/floor.json): the **drifting scale**
(`SCALE-FILL-02` reads about 0.6 g high until the supervisor calibrates it)
and the **night shift**, whose readings of the same process are more spread
out than the day's.

Every one of them recurs once an hour, because the file loops — so a shift
contains several of each, and any hour of this plant's records contains all
five.

**A quiet tag is an empty cell.** `fsmes sim-generate` writes nothing in that
column for those rows; the replay server writes no value, so no data change
is notified and the MES is never told anything. Through Kepware's text driver
the same cell reads as NULL. Zero would have been a reading of zero and the
last value repeated would have been a reading somebody could believe, and the
whole question downstream is whether a value is old.
