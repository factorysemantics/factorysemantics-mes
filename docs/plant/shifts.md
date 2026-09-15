# Shifts: which shift a minute belongs to

A plant is run and measured by shift. It is the unit a supervisor is
accountable for, the unit a handover is written in, and the unit the MES you
already have almost certainly reports in. This page is what this MES means by
a shift, what it writes down, and how to ask a screen or an API for one.

The rules on this page are decided in
[decision 0028](../decisions/0028-which-shift-a-minute-belongs-to.md).

## What a shift is here

A shift is a **pattern plus a date**. There is no table of shift occurrences;
`shift_patterns` holds the pattern — a code, a name, a start time, an end time
and a seven-character day mask, Monday first — and the occurrence is worked out
from it. `1111100` is weekdays; `1111111` is every day.

A pattern belongs to the whole site, or to one line when it names a machine. A
line's own pattern beats the site-wide one.

Shifts are master data, so they come from a [plant pack](../operate/packs.md)
in `masterdata/shifts.json`:

```json
[
  {"code": "DAY",   "name": "Day shift",   "starts": "06:00", "ends": "22:00", "days": "1111111"},
  {"code": "NIGHT", "name": "Night shift", "starts": "22:00", "ends": "06:00", "days": "1111111"}
]
```

`ends` earlier than or equal to `starts` means the shift crosses midnight. No
flag to set; it follows from the two times.

## Which clock

The plant's. Every instant this MES stores is UTC, and every one of them is
turned into the plant's own wall clock before it is compared with a shift time.
Set `MES_PLANT_TIMEZONE` to an IANA name — `America/Chicago`, `Europe/Berlin`.

If nothing sets it, the process's own zone is used and **every screen and API
says the zone was defaulted**. A shift boundary drawn in the wrong zone is off
by hours and looks exactly like a correct one, so it is never guessed silently.

One consequence worth expecting: on the night the clocks go forward, a
22:00–06:00 shift is **seven hours**, not eight. That is what the plant lived
through, and it is what the reports say.

## The three rules

**A shift is half-open.** `[starts, ends)`. A unit counted at 22:00:00 belongs
to the shift that began at 22:00, not to the one that ended. One second belongs
to one shift, so adding two shifts up never counts anything twice.

**A shift belongs to the day it started.** Friday night is Friday's shift at
two on Saturday morning, which is how the roster is written. `shift_day`
carries that date, and `2026-09-11/NIGHT` is how you name it.

**An hour no pattern covers is not attributed.** A plant rostering 06:00–14:00
and nothing else has sixteen hours a day belonging to no shift. Those rows
carry a null shift and every screen says *not attributed*. They are not pushed
into the nearest shift: an hour nobody rostered is something to fix in the
calendar, and filling it in would hide it.

**Calendar exceptions.** An *overtime* day attributes normally — a Saturday the
plant deliberately worked gets the shift whose hours it fell in, because
otherwise a whole day of production leaves every per-shift report. A *shutdown*
day does **not** un-attribute: a line that ran on a day the calendar called
dark still ran, and those units keep their shift. The exception is the finding.

## What carries a shift

Four tables, each with `shift_code` and `shift_day`, written once when the row
is written:

| Row | The instant it is read from |
|---|---|
| Production booking (`production_logs`) | when the units were counted — including a `ts` another system supplied, so a count replayed from last night lands on last night's shift |
| Equipment state interval (`equipment_states`) | when the interval **began** |
| Quality check (`quality_checks`) | when the reading was taken |
| Non-conformance (`non_conformances`) | when it was **raised** — review, disposition and close happen on other shifts and keep their own timestamps |

A state interval that runs past a boundary is **not split**. It is one thing
the machine did, and cutting it in two would invent a state change that never
happened. The *reporting* splits instead: a shift window clips every interval
to its own two ends, so a run from 03:00 to 09:00 gives three hours to the
night shift and six to the day shift, and neither report claims the other's.

## Asking for a shift

Every `/analysis/*` endpoint takes `shift=` beside `hours=`:

```
GET /analysis/oee?shift=current
GET /analysis/downtime?shift=previous
GET /analysis/timeline?shift=2026-09-14/NIGHT&line=LINE1
```

Three spellings and nothing else: `current`, `previous`, and
`YYYY-MM-DD/CODE` on the plant's own date.

The two window kinds are **alternatives**. A request that names a shift gets
that shift; `hours` is ignored rather than averaged in. A shift that names
nothing — the plant is between shifts, the code is not a pattern, the pattern
does not run that day — is a `400` with one sentence saying which, never a
quiet fall-back to eight hours.

`GET /analysis/shifts` is what a picker reads: the shifts of the last few days
with their keys, which one is running, the total, and the clock the boundaries
are drawn on. The **Analysis** screen's *Shift* selector is driven by it, and
keeps its choice in the address bar so a screen can be handed to somebody else.

The MCP tools `downtime` and `tag_trend` take the same `shift` argument, and
`shifts` lists them.

### A shift still running

It is clipped to now, not to the hour it is rostered to end. Asking for
`shift=current` two hours into an eight-hour shift reports two hours. The other
six have not happened, and counting them as time the plant was not running
would call a line that never stopped 25 % available. The response says
`in_progress: true` and how much of the shift has run.

## Upgrading a plant that already has history

The migration that adds these columns backfills existing rows from the shift
patterns **as they stand when you upgrade**.

!!! warning "The backfill is an assumption"

    This product keeps no history of shift patterns. If you moved a shift
    boundary last month, last month's rows are attributed to this month's
    pattern, and nothing in the data can tell the difference afterwards. Rows
    no pattern covers stay null and read as *not attributed*.

    If a per-shift trend across a roster change has to be trusted, treat the
    rows before the change as unattributed rather than believing the backfill.
    Giving shift patterns a validity period is the honest fix and is recorded
    as the thing to revisit in decision 0028.

Run `fsmes db-status` after upgrading; it says which revision the database is
at and whether anything is outstanding.
