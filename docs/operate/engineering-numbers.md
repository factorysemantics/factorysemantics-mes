# This plant's own engineering numbers

Seventeen judgments in this MES used to be literals in the source: how far
through a maintenance plan counts as *coming due*, how long a window is when
nobody says, how many machines fit on one Gantt, how many times the OPC agent
retries a booking before it gives up, how long the namespace keeps trying to
reach a broker that is down. Each one was somebody's answer, made once, and
most of them had a comment beside them arguing for the number rather than
stating it — which is what a judgment call sounds like before anybody calls it
configuration.

They are this plant's now. They are seeded from `[process]`, `[controls]` and
`[oee]` in the plant pack, owned by the plant's database from then on, and
**edited on Engineering › Configuration** — with the value the plant is
actually running on beside each one. They follow one rule above all the others,
the same rule [this plant's own quality numbers](quality-numbers.md) followed
before them:

> **The literal that was in the source is the shipped default, unchanged.**
> A plant that writes none of these keys behaves exactly as it did.

## Two capabilities, because Engineering is two jobs

[Decision 0035](../decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
§2 named six configuration domains and said process engineering and controls
engineering are different jobs. Engineering's Configuration page is where that
becomes visible: eleven of its rows are gated on `process.define` and six on
`signals.define`, and each row names the capability that may write it beside
the value.

`signals.define` is new as of 2026-09-25. 0035 named it in 2026 September and
said adding it later would be cheap, because a capability is a string and a
role is a list of them. It is on the **admin** role and deliberately **not** on
the **agent** role: an agent may draft a vocabulary for a person to approve,
and retuning how hard the OPC agent argues with its own database is not
drafting — there is nobody in the loop after it. An administrator who wants an
agent to hold it grants it, per plant.

## Process engineering — eleven

| Key | Ships | What it decides |
|---|---|---|
| `[process] maintenance_due_soon_fraction` | `0.8` | How far through a plan's own interval counts as coming due. A fraction, so it scales from a weekly filter change to an annual overhaul |
| `[process] default_cycle_seconds` | `3.0` | What one unit is assumed to cost at a station with no rated cycle time. A machine's own rating always wins, and a schedule built on this says so |
| `[process] default_job_minutes` | `60.0` | How long a maintenance job with no plan behind it is assumed to take. It sizes the backlog's downtime figure |
| `[process] maintenance_plan_default_minutes` | `30.0` | The expected duration a new plan inherits. Each plan's own figure is the engineer's |
| `[process] default_report_hours` | `8.0` | How long a window is when a caller asks for none. The source called it *a shift*; a twelve-hour plant answers 12 |
| `[process] report_windows` | `[0.25, 1, 8, 24, 168]` | The window lengths every time picker in the product offers, in hours |
| `[process] gantt_screenful` | `12` | How many machines one Gantt draws. The payload says how many of the plant's total it drew |
| `[process] previous_shift_horizon_days` | `14` | How far back `shift=previous` looks for a shift that has ended |
| `[process] working_week_mask` | `"1111100"` | The days a shift pattern works when it names none: seven flags, Monday first |
| `[process] schedule_default_horizon_hours` | `24.0` | How far ahead the schedule board looks when nobody says |
| `[oee] min_observed_seconds` | `10.0` | The least observed time this MES will divide by. Below it the answer is *unknown* with the [coverage ledger](coverage.md) saying why |

`min_observed_seconds` is an `[oee]` key listed on Engineering's page rather
than a `[process]` one. It sits beside `coverage_floor`, its sibling, because
the argument that made *that* one a plant's to set — *a number vanishing off a
screen because of a threshold nobody chose is its own kind of dishonesty* — is
the argument for this one. A Configuration workspace is a place on a screen and
a pack section is a table in a file, and those two do not have to share a name.

`default_report_hours` is which of the `report_windows` a screen opens on. It
does not have to be one of them: a plant that opens on twelve hours and offers
a different five gets its twelve added to the picker beside them, the same way
a window asked for in a URL already is. Nothing refuses the combination,
because a plant that wants a default off its own quick list is answering its own
question.

```toml
[process]
# A plant on twelve-hour shifts, working Sunday to Thursday, that thinks in
# weeks and has a hundred machines on one screen.
default_report_hours = 12
report_windows = [1, 12, 24, 168]
working_week_mask = "0111110"
gantt_screenful = 40
```

## Controls engineering — six

| Keys | Ship | What they decide |
|---|---|---|
| `[controls] opc_book_attempts` / `opc_book_backoff_s` | `4` / `0.5` | How many times the OPC agent retries booking a batch of readings, and the first wait between attempts, doubling each time |
| `[controls] uns_max_attempts` / `uns_base_backoff_s` / `uns_max_backoff_s` | `8` / `5` / `3600` | How many attempts a namespace publication gets before it is recorded dead, the first wait, and the ceiling the doubling stops at |
| `[controls] trigger_reload_seconds` | `30.0` | How often a running agent re-reads its approved triggers — which is how fast an approval on screen reaches the machines |
| `[controls] trigger_default_cooldown_seconds` | `300.0` | The cooldown a newly drafted trigger inherits. Each trigger's own value is the engineer's |
| `[controls] opc_history_ratio` / `opc_min_history_ms` | `10` / `1000` | How much slower the rest of a machine's tags are sampled than the ones the MES reasons about, and the floor under that interval |
| `[controls] opc_order_sync_seconds` / `opc_adjustment_poll_seconds` | `2.0` / `5.0` | How often the agent checks whether a machine's order code changed, and how often it looks for approved setpoint adjustments to write |

```toml
[controls]
# A plant whose broker is restarted nightly for twenty minutes, and that stops
# a line on an SPC signal.
uns_max_attempts = 40
trigger_reload_seconds = 5
```

**`[controls]` is not where the broker is.** Where the OPC server and the MQTT
broker live is `[serve] opc_endpoint` and the `MES_UNS_*` settings — one line
of plumbing each, written by whoever plugged the plant in. `[controls]` is how
hard this plant argues with them and how densely it samples, which is the
controls engineer's tuning and a different question.

> **Dead is never deleted.** Raising `uns_max_attempts` does not make delivery
> more likely; it changes how long the plant keeps trying before a person has
> to decide about an event the namespace never received. A publication that
> ran out of attempts is recorded `DEAD` and is listed, not dropped.

## Two cases where *immediately* has an honest caveat

Every other key here is in force on the next reading, in every process, with
no restart. Two are not, and the page says so rather than implying otherwise:

- **`opc_history_ratio` and `opc_min_history_ms`** are read when the agent next
  subscribes. A sampling interval is a number the OPC server holds for the life
  of a subscription, so there is no honest way for a change to reach an open
  one. Restarting the agent, or any reconnection, picks up the new value.
- **`opc_order_sync_seconds` and `opc_adjustment_poll_seconds`** take effect on
  the next pass of their loop, which is at most the *old* value away.

`POST /adjustments/{code}/approve` tells a person that *the OPC agent writes
within seconds*. That promise rests on `opc_adjustment_poll_seconds`, and its
docstring now names the key: a plant raising it past a few seconds is changing
what it has told its own operators.

## What stays the product's, and why

The test is 0035's: **ask what breaks if two plants answer differently.**

- **The shift-mask format** — seven flags, Monday first. Which mask is the
  default is the plant's; a plant that reordered the days would publish a
  calendar meaning something nobody else means by it.
- **The Western Electric rule numbers** behind a trigger on an SPC signal, as
  [this plant's own quality numbers](quality-numbers.md) already says.
- **Thirty days** as the longest window any screen will draw. `fsmes pack
  check` refuses a `default_report_hours` or a `report_windows` entry above it,
  so a plant cannot offer a window the API would turn down.
- **`ShiftPattern.days`' column default and `Trigger.cooldown_seconds`'** stay
  the product's five days and five minutes. A column default cannot read a
  plant's settings, and every path a person or a pack takes fills the field in
  from the plant's own key before it reaches the column.
- **The ERP outbox's retry policy**, which is the same three numbers as the
  namespace's and is deliberately **not** the same setting. One plant's broker
  and one plant's ERP have different maintenance windows, so a single policy
  would make one of the two wrong.

## What was left out, and why

Fourteen candidates in these two domains are **not** here. They are the ones
[the configuration audit](../design/config-audit-2026-09-21.md) scoped as
`object` rather than `plant`, and the reason is the audit's own rule: *would
two honest engineers at the same plant answer differently for two different
objects?*

Tag staleness, the OPC reconnect wait, a station's inspection-group grace, how
long a cached order is trusted, the setpoint *did it follow* fraction and its
verification wait, the container member retry count, the counter-reset
threshold, and what counts as a good yield are all of that kind. A counter that
wraps at 65535 and one zeroed every shift are two tags, not two plants, and a
plant forced to give one answer for both would be wrong about one of them.
Those belong on the object — beside `state_map` and `cycle_seconds` in the tag
map, or on the machine or material's own row — and they are their own change.

## Editing them

**Engineering › Configuration** (`/dashboard/config/engineering`) lists every
section with its keys, the value this plant is running on, and whether that
value is the product's default or one the plant set.

1. Open **Engineering › Configuration**. Each row's **Set to** column holds one
   box per key, with the key's name beside it.
2. Type the new value and press **Save**. There is one Save per row, because
   some judgments are one decision written as two numbers — the namespace's
   backoff and its ceiling, the sampling ratio and its floor.
3. The change is in force at once, everywhere, with the two caveats above. It
   is in the audit trail as `plant_setting.set`, with what it was and what it
   became.

Eighteen sections is past a screenful, so the page has a search box and states
the order it is in: **grouped by the module each section belongs to**, with a
heading per group, in the order this plant serves them. There is no draft and
no approver — [decision 0035](../decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
rule three: a number that takes effect when it is saved has no pending state.
Undo is typing the old number back, and the audit row says what it was. The
downtime vocabulary sitting beside them on the same page keeps its full draft →
approve lifecycle, because its words are written onto records that outlive it.

## What is still the pack's

`fsmes pack apply` seeds each of these keys **once and never updates one** — the
same rule every other kind it seeds already keeps. So a pack file is how a new
plant starts and not how a running one is steered: editing `plant.toml` and
re-applying does not move a number this plant has taken ownership of, and
`fsmes pack status` reports the difference rather than resolving it.

One consequence worth knowing: a pack that seeds a shift and leaves `days` out
now gets **this plant's** working week rather than the product's five days. The
audit suggested refusing such a pack instead, on the grounds that seeding
Monday-to-Friday was a guess about somebody else's plant. It is not a guess any
more — it is the plant's own stated answer — so the field is filled in the way
every other missing field in that seeder is.
