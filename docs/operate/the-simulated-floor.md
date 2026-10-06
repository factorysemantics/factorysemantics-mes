# What the simulated floor does, and the stories it plants

A plant that simulates runs two things no PLC can give it. The **line** is
machines: states, counters and process values, replayed from
`labs/kepsim/out` by `fsmes run-opc-sim --replay`. The **floor** is people:
`fsmes run-operations` records quality checks, issues material, names stops,
works through non-conformances and finishes orders — over the public API,
exactly as an operator's browser would.

Neither of them is in the product's code. The line is
[`labs/kepsim/line.json`](https://github.com/factorysemantics/factorysemantics-mes/blob/main/labs/kepsim/line.json);
the floor is a **floor script** the plant pack names with `[files] floor`
(`MES_FLOOR_SCRIPT_FILE`). A plant whose pack names no floor script records
what the tag said, names no gauge and labels no stop — which is what every
real plant wants, because it has people instead.

## Why a plant that behaves is not enough

Measured on the bottling lab plant on 2026-10-04, after a week up:

- **0 of the last 500 quality checks named a gauge.** The column was there and
  nothing wrote it, so no reading could answer the first question anybody asks
  about a point on a control chart: *did the process move, or did the gauge?*
- **Seven days of downtime, 100 % unlabelled.** The pareto was one bar.
- **887 non-conformances raised and never reviewed.** The quality screen said
  the same thing for a week, and so did every answer about the biggest problem
  on the plant.

None of that is a bug in the MES. It is a plant with no people in it, and an
analysis is only as good as what the plant recorded.

## The six stories

Each one is **data**, each leaves a **trace in the records**, and each recurs
once an hour because the line's file loops — so any hour of this plant's
history contains all six.

| The story | Where it is written | What it leaves behind |
|---|---|---|
| The filler's supply pressure falls away 10 % for six minutes, with no alarm | `line.json`, an `offset` event with an effect on `FillWeight` | every bottle in that window about four grams light, and `NozzlePressure` one tag over says why |
| The washer runs three degrees hot | `line.json`, a `scrap_burst` with an effect downstream | failing fill weights at the *filler* whose cause is a station upstream |
| The first bottles after a changeover run heavy | `line.json`, an `offset` beginning where the changeover ends | a point or two above the control limit, immediately after a **planned** stop — which the floor labels `changeover`, so it can be found |
| One of the two scales is drifting | the pack's `floor.json` | that scale's readings run about 0.6 g above the other's, until the supervisor calibrates it |
| The night shift's readings of the same process are more spread out | the pack's `floor.json` | a wider spread on the night shift with no change in the process behind it |
| The nozzle the changeover left behind fills a shade over | the pack's `floor.json`, `sampling.fill_height.after_changeover` | for twenty-five line minutes after each labelled `changeover`, the **fill-height sample means** sit about half a millimetre above the centre line while the sample **ranges** do not move: the process shifted, its spread did not. The checkweigher's own band absorbs it, so it is invisible on fill weight and plain on the height chart — which is what an X-bar chart is for. Half a millimetre is less than the limits drawn from R-bar, so it is found by splitting the means on the changeover and not by waiting for a rule to fire |

And one that is about the MES rather than the plant: **a tag goes quiet for
fifteen minutes.** `FillWeight` stops arriving while the filler runs on and
every other signal on it reports. The MES's last sample keeps the moment it
really arrived, so the floor can tell a stale value from a steady one — and it
records a plausible value with **no gauge and no station on it** rather than
writing the frozen number down once a minute. `null` in those two columns is
*not recorded*, which is a different fact from a reading nothing measured.

## What the floor will not say

**It does not label an idle machine.** This line's tag map maps *starved* and
*blocked* both onto `idle`, so by the time the MES holds the interval the
difference is gone — and the plant's vocabulary has a word for each, neither
of which anything recorded can choose between. Guessing one would put a cause
in the pareto that nothing observed. The floor says so once, in its log, as a
finding about the state map rather than as a gap in the script.

**It does not name a stop it did not see.** A stop shorter than one look
(`MES_OPS_WATCH_EVERY`, fifteen seconds) is missed, and the pareto reports
those seconds as unlabelled. That residue is real, and house rule 3 says it is
reported as unlabelled rather than filed under "other".

**It names a stop afterwards, not as it begins.** The moment a machine goes
down is the moment nobody knows why yet, and on a plant whose states arrive
from an OPC agent nobody is ever asked. `POST /equipment/{code}/stops/label`
is where somebody says what it was; it never creates an interval, because an
interval is this MES's own observation and manufacturing one from a claim
would put seconds into availability that nobody watched.

**No tool sends that route.** Naming a stop is somebody's account of why a
machine stopped, and a model choosing between six reasons from the shape of
the data would be writing fiction into the pareto with a confidence interval
on it. The assistant's honest answer is to show which stops are unnamed and
let whoever was there say. Another system's label has its own front door,
[`fsmes inbound`](inbound.md), and it records who claimed it.

## Reading the script

The bottling pack's
[`floor.json`](https://github.com/factorysemantics/factorysemantics-mes/blob/main/labs/multiplant/bottling/floor.json)
is the worked example, and every block in it carries a `_why`. The shape:

```json
{
  "measurement": {
    "stale_after_s": 150,
    "shift_spread": {"NIGHT": 2.5},
    "characteristics": {
      "fill_weight": {
        "spread": 0.3,
        "gauges": [
          {"gauge": "SCALE-FILL-01", "share": 3},
          {"gauge": "SCALE-FILL-02", "share": 1,
           "drift": {"per_day": 0.01, "max_bias": 0.6}}
        ]
      }
    }
  },
  "stops": {"micro_stop_under_s": 60, "micro_stop": "micro_stop",
            "longer_than_a_micro_stop": "breakdown", "changeover": "changeover"},
  "nonconformances": {"per_pass": 6, "leave_open": 3,
                      "by_severity": {"major": {"disposition": "scrap", "reason": "…"}},
                      "otherwise": {"disposition": "use_as_is", "reason": "…"}}
}
```

### A characteristic nobody's machine publishes

Fill height is measured by hand on a bench: five bottles come off the filler,
are carried to the QI station, are measured one after another, and the five
readings are posted together as **one sample**
([decision 0040](../decisions/0040-the-chart-type-follows-the-sampling-plan.md)).
No tag on this line carries a height, so the floor has to make the pieces —
and the arithmetic that turns a weight into a height is **this plant's bottle,
not the product's**, which is why it is in the pack:

```json
{
  "sampling": {
    "fill_height": {
      "every_line_s": 900,
      "from": {"equipment": "FILL01", "tag": "FillWeight"},
      "convert": {"offset": 12.0, "per_unit": 0.26},
      "piece_to_piece": 0.35,
      "after_changeover": {"line_minutes": 25, "offset": 0.5}
    }
  }
}
```

- `every_line_s` — line seconds between samples, divided by the replay speed
  like every other cadence here. Nine hundred is four samples an hour.
- `from` — the five pieces are the **last five stored readings** of that tag
  off that machine: five different bottles, each weighed at a different
  instant, which is what a sample of five is. If five readings newer than the
  last sample are not there, this floor **takes nothing and says so once**
  rather than measuring the same bottle five times. A sample of five that is
  one bottle copied five times has a range of zero and would make every chart
  drawn from it a lie.
- `convert` — `height_mm = offset + per_unit × weight_g`, one straight line,
  because the bottle is a cylinder over the band that matters.
- `piece_to_piece` — millimetres, one sigma: the moulding varies, so two
  bottles holding the same weight do not stand at the same height. This is
  the variation a sample of five is taken to measure, and it is deliberately
  larger than the gauge's own spread — an X-bar and R chart whose range came
  mostly from the instrument would be charting the instrument.
- `after_changeover` — the planted cause in the table above.

Each of the five pieces is then measured through the bench gauge from the
register, the same way a single reading is: its resolution rounds the value
and its drift, if it has any, biases it.

The gauges themselves are **master data**, not script: they are in the pack's
`masterdata/gauges.json` and on the plant's own register, so a calibration
recorded on the Gauges screen is in force for the next reading this floor
takes. The script only says which gauge measures what, how often each is
picked up, and which one is drifting.

A disposition's **reason** is in the script on purpose. *Use as is* on a batch
that failed its specification is a concession somebody has to defend a year
later, and a simulated plant that invented the sentence would be writing the
one field nobody can check.
