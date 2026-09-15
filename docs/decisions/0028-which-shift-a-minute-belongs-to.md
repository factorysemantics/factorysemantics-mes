# 0028 — A shift is half-open, belongs to the day it started, and covers nothing it does not roster

- **Status:** accepted
- **Date:** 2026-09-14
- **Deciders:** maintainer

## Context

Until 2026-09-14 this MES could say whether the plant was *meant to be
working* — `services/calendar.is_working`, built on the `shift_patterns`
table — and nothing more. No row carried a shift, and `/analysis/*` was
windowed in hours.

That was found by trying to script a shift boundary in the lab. There was
nothing to read: no production booking, state interval, check or
non-conformance said which shift it fell in, so no comparison could be made
either side of a boundary and no screen could offer one.

It matters beyond the lab. A plant is run and measured by shift — it is the
unit a supervisor is accountable for, the unit a handover is written in, and
the unit the MES a plant already has reports in. Running this one in shadow
mode beside that one means answering the same question the same way, and "the
last eight hours" is not a shift.

Three cases have to be decided before any of it can be written down, because
each of them changes a number:

1. **The boundary second.** A plant with a 22:00 handover counts units at
   22:00:00. Which shift made them?
2. **A shift that crosses midnight.** A 22:00–06:00 shift on Friday runs into
   Saturday. Which date is it filed under?
3. **An hour no pattern covers.** A plant rostering 06:00–14:00 has sixteen
   hours a day belonging to no shift, and a plant that ran on a Saturday its
   roster calls dark has a day's production outside the mask.

## Options considered

| Option | For | Against |
|---|---|---|
| Closed intervals (`starts <= t <= ends`) | Reads naturally — "ten till six" includes both ends | The boundary second belongs to two shifts, so adding the shifts up double-counts it; the two halves of a handover cannot both be right |
| A shift belongs to the calendar day the *clock* shows | One rule, no special case for nights | Two thirds of a night shift lands on the next day, splitting one shift across two reports and two supervisors — and it is not how any roster is written |
| Fill an unrostered hour with the nearest shift | No nulls; every row has a shift | Invents an attribution. It hides an unfinished calendar behind numbers that look complete, which is house rule 2 read from the wrong end |
| **Half-open, day-it-started, null when unrostered (chosen)** | One second, one shift; a night shift is one shift on one date; an unrostered hour is visibly unrostered | Screens and exports must handle a null shift, and a plant has to finish its calendar before per-shift reporting is complete |

## Decision

`fsmes.services.calendar.shift_for` is the one answer, and everything —
attribution when a row is written, `shift=` on the analysis endpoints, the
picker on the screen — goes through it.

- **A shift is half-open, `[starts, ends)`.** A unit counted at 22:00:00
  belongs to the shift that began at 22:00. Adding two shifts up never
  double-counts a second.
- **A shift belongs to the plant-local day it started.** Friday night is
  Friday's shift at two on Saturday morning. `shift_day` carries that date.
- **An instant no pattern covers is not attributed.** `shift_code` and
  `shift_day` are null, and every screen that shows them says *not
  attributed* rather than filing them somewhere tidy.
- **An overtime exception attributes; a shutdown exception does not
  un-attribute.** A Saturday the plant deliberately worked gets the shift
  whose hours it fell in, because otherwise a whole day of production leaves
  every per-shift report. A line that ran on a shutdown day still ran, and
  those units keep their shift — the exception is the finding, not a reason
  to drop the attribution. `is_working` answers *should the plant have been
  running*; `shift_for` answers *which shift did this happen in*, and they
  are different questions.
- **Where two patterns cover one instant**, the machine's own beats the
  site-wide one, and between two of equal standing the one that started later
  wins: that is the shift the people on the floor would say they are on.

Two consequences of the same rule, in the two places time is handled:

- **Storage is not split.** A state interval that runs past a boundary keeps
  the shift it began in. It is one thing the machine did, and cutting it in
  two would invent a state change that never happened.
- **Reporting is split.** A shift window clips every interval to its own two
  ends, so the seconds of a crossing interval land on both shifts in the
  proportion the machine really spent there. A run from 03:00 to 09:00 gives
  three hours to the night shift and six to the day shift, and neither report
  claims the other's.

A shift **still running is clipped to now**, never to the hour it is rostered
to end. The rest of it has not happened, and counting it as time the plant was
not running is the same mistake as counting time before the MES was watching
(the clamp in `analysis._window`).

## Consequences

**Easier.** Every number the product reports can be asked for by shift: OEE,
the state Gantt, the downtime pareto, the production trend and a machine's
process value, on the screen and through the MCP tools. A shadow-mode
comparison against the MES a plant already has can be made in the unit that
plant reports in.

**Harder.** A null shift is a real state that every consumer has to handle, and
a plant that has not finished its calendar will see it. That is the intended
cost: it is visible, and it names what to fix.

**The backfill is an assumption, and it is the weak point.** The migration
attributes existing rows from the patterns as they stand *today*, because this
product keeps no history of shift patterns. A plant that moved its boundary
last month will see last month's rows attributed to this month's pattern.
Nothing in the data can tell the difference afterwards.

**To revisit.** Give shift patterns a validity period, so a pattern change is a
new row rather than an edit and an old booking can be attributed from the
pattern that was actually in force. That is the honest fix, it is a schema
change and a migration of its own, and it should be done before any plant is
asked to trust a per-shift trend that crosses a roster change.

## House rules touched

- **2 — unknown is a valid answer; zero is not.** An unrostered hour is null
  and reads as *not attributed*, not as a shift nobody named.
- **3 — unlabelled data is reported as unlabelled.** The same rule one column
  along: a per-shift report says how much of its window no shift covers rather
  than quietly totalling what it could attribute.
- **4 — config, not code, at plant boundaries.** Which shifts a plant has is
  master data a pack carries (`shifts.json`); nothing here names a shift.
