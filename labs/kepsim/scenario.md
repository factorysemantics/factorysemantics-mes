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

## The causes behind a point on the fill-weight chart

Scott, 2026-10-05: *"click an SPC point and see why it is there."* Four of
them are above, because they are things the machines do. The others belong to
the people and are in the plant pack's own
[`floor.json`](../multiplant/bottling/floor.json): the **drifting scale**
(`SCALE-FILL-02` reads about 0.6 g high until the supervisor calibrates it)
and the **night shift**, whose readings of the same process are more spread
out than the day's.

Every one of them recurs once an hour, because the file loops — so a shift
contains several of each, and any hour of this plant's records contains all
of them.

## And one behind a point on the fill-height chart

`fill_height` is the first characteristic on this plant inspected several
pieces at a time: five bottles every fifteen line minutes, measured on the
bench gauge at the QI station and posted as one sample, so its chart is X-bar
and R and its point is the mean of five (decision 0040).

Nothing on this line publishes a height tag, because the bottles are measured
by hand. So the simulated floor takes **the last five stored `FillWeight`
readings** off the filler — five different bottles, weighed at five instants —
and turns each into a height by one straight line:

    height_mm = 12.0 + 0.26 × fill_weight_g

500 g of cola stands 142 mm up the glass and a gram either way is a quarter of
a millimetre. The band is 139–145 mm. Then the **glass itself** is added:
0.35 mm, one sigma, piece to piece, because two bottles holding the same
weight do not stand at the same height. That is the within-sample variation
the sampling exists to measure, and it is deliberately bigger than the
gauge's own 0.1 mm — a range chart built mostly from the instrument would be
charting the instrument.

| Cause | What it does | The lesson it stages |
|---|---|---|
| **The nozzle the changeover left behind** | For 25 line minutes after each changeover (so from 44:00, after the 40:00–44:00 changeover above) every piece in the sample sits about 0.5 mm high — one within-sample sigma. The pack's `sampling.fill_height.after_changeover` | the sample **means** step up and the sample **ranges** do not: the process moved, its spread did not. That is the thing an X-bar chart shows and an individuals chart on fill weight never did, because there the same shift was inside the noise of a single bottle |

Two honest notes about it. It is **the floor's own rule, not a line event**:
the line publishes no height tag, so an `offset` row in `line.json` has
nothing to act on, and the cause has to be planted where the heights are
made. And it lasts **25 line minutes rather than the two hours** a real
nozzle setting would: this file loops hourly and changes over inside the
loop, so a two-hour window would never close and every sample on the chart
would be a post-changeover one — a planted cause visible as nothing. Twenty-five
minutes is one to two samples after each changeover, which a reader can see
against the ones before it.

**A quiet tag is an empty cell.** `fsmes sim-generate` writes nothing in that
column for those rows; the replay server writes no value, so no data change
is notified and the MES is never told anything. Through Kepware's text driver
the same cell reads as NULL. Zero would have been a reading of zero and the
last value repeated would have been a reading somebody could believe, and the
whole question downstream is whether a value is old.
