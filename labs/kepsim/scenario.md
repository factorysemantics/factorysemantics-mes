# KepSim scenario timeline

One looping hour, 1 row/second, seed 42. `TSec` in every table is the row's
sim-time — use it to line any observation up with this timeline.

| t (mm:ss) | TSec | Event | The lesson it stages |
|---|---|---|---|
| 10:00–10:12 | 600 | LD jam, 12 s | a short stop that barely ripples |
| 15:00–25:00 | 900 | RD MotorTemp drifts 62→88 °C | the precursor signal before a failure |
| 25:00–27:00 | 1500 | **RD breakdown** | watch STARVED walk downstream and BLOCKED walk upstream, one buffer at a time |
| 30:00–35:00 | 1800 | **Washer scrap burst** (~10%), WashTemp ~3 °C hot; Refill FillWeight −9 g, no alarm on the filler | the washer story: name the cause, not the symptom |
| 40:00–44:00 | 2400 | **Changeover**, OrderId 4711→4712 | planned stop ≠ downtime (OEE availability) |
| 50:00 | 3000 | **RD counter reset** to 0 | the RB Counts Wrong classic, on demand |
| ~every 2 min | — | Palletiser micro-stops, 8–18 s | death by a thousand cuts (OEE performance) |
| every loop | 3600→0 | file wraps, all counters snap back | a free counter-reset drill every hour |

States: 0 stopped · 1 running · 2 starved · 3 blocked · 4 down · 5 changeover
