# Feeding the MES what people typed elsewhere

*How-to. Downtime labels, quality results and counts that were recorded in another system — dropped into a folder as CSV, or read straight out of that system's own database — and recorded here as what they are: told, not observed.*

The OPC agent is how this MES sees a plant. It is not how it learns why a
machine stopped, what an inspector measured, or how many units somebody
counted by hand — none of that is on a wire. Those facts live in whatever
system the plant already has people typing into.

This matters most when the MES is running in shadow mode beside an
incumbent. Machines reach both systems
through OPC UA, so both see the same stops. But the technician labels the
stop in the incumbent, the inspector records the check in the incumbent, and
the operator types the count into the incumbent. If none of that reaches the
shadow, the comparison is unfair by construction: the shadow shows an
unlabelled stop where the incumbent shows a reason code, and it looks like
the shadow has a data problem when what it has is a plumbing problem.

So there is a second front door. Three event shapes, one contract, and two
drivers: files in a folder, and [a broker](#the-broker-mqtt) the plant
already has. The contract does not change with the pipe the row came down.
drivers that fill it: files in a folder, and a read-only query against the
database that system already keeps. A row means the same thing whichever way
it arrived.

!!! note "Inbound is not gated by shadow mode"

    Shadow mode closes the paths by which this MES could change something
    *outside* itself. Being told things is the other direction, and a shadow
    that stopped listening would be comparing itself with the incumbent on
    half the evidence. Inbound keeps working with every outbound path shut.

## The three shapes

Every inbound event, whatever transport brings it, carries the same four
facts about where it came from. They are the point of the contract, not
metadata on the side.

| Field | What it is |
|---|---|
| `source` | The free name of the system that supplied it, as its operators would say it: `replay:incumbent-mes`, `lims:brookfield`, `terminal:line1`. It comes from your configuration — only you know what your systems are called. |
| `source_kind` | A fixed category for grouping a report: `replay`, `erp`, `lab`, `terminal`, `other`. |
| `external_key` | The supplying system's own identifier for the record. This is what makes dropping the same file twice a no-op. The MES never mints one. |
| `recorded_at` | When the supplying system recorded it. Not when this MES read the file. |

**A downtime label** (`downtime`) — `equipment`, `started_at`,
`ended_at` (null while the stop is open), `reason`, `note`.

**A quality result** (`quality`) — `equipment` and/or `order`,
`characteristic`, `value`, `unit`, `spec_reference`, `gauge`, `passed`,
`inspector`. At least one of equipment or order is required.

**A manual count** (`counts`) — `equipment`, `order` if the supplier knows
it, `good`, `scrap`, `when` the units were made.

Unknown fields stay null. Nothing is defaulted to zero and nothing is
defaulted to "now": a timestamp this MES made up is indistinguishable, a
week later, from one the plant supplied.

## The folders

One inbox per event type under `MES_INBOUND_DIR`, plus a processed and a
rejected folder for each:

```
inbound/
  downtime/          drop downtime label files here
  quality/           drop quality result files here
  counts/            drop count files here
  processed/         inputs that were read, keeping their names
    downtime/ quality/ counts/
  rejected/          inputs nothing could be taken from, and the rejects reports
    downtime/ quality/ counts/
```

`.csv` and `.json` are read; a `.json` file holds one object or a list of
them, with the same keys a CSV would have as columns. **Anything else in an
inbox is left exactly where it is** and counted in the run's report — a
plant's export folder usually carries a lock file, a checksum or a README,
and moving those would be meddling.

**No input file is ever deleted.** A file some of whose rows were taken
moves to `processed/`; a file nothing could be taken from moves to
`rejected/`. Either way the file keeps its name, and a second file of the
same name gets a timestamp rather than overwriting the first.

## The column mapping

Which columns your export has, and what they are called, is *your*
configuration. The MES ships an example that maps the demo plant's own CSV
shape and nothing else; it describes no commercial system's export format,
and it is not meant to be used unchanged.

```bash
MES_INBOUND_DIR=inbound
MES_INBOUND_MAPPING_FILE=config/inbound_mapping.json
MES_INBOUND_POLL_SECONDS=10
```

```json
{
  "downtime": {
    "source": "replay:incumbent-mes",
    "source_kind": "replay",
    "timezone": "America/Chicago",
    "columns": {
      "external_key": "stop_id",
      "recorded_at": "entered_at",
      "equipment": "machine",
      "started_at": "stop_start",
      "ended_at": "stop_end",
      "reason": "reason_code",
      "note": "comment"
    }
  }
}
```

`columns` maps a contract field onto the column that carries it. Optional
extras:

- `time_format` — a `strptime` format, when the supplier's timestamps are
  not ISO 8601.
- `timezone` — an IANA zone name. **A timestamp with no zone in it, in a
  stream with no `timezone` configured, is rejected row by row.** Reading a
  local timestamp as UTC would move every stop in a shift by hours, and it
  would do so silently, which is the worst way to be wrong.
- `defaults` — a contract field the export simply does not have. A supplier
  that never reports scrap says so once, here, as `{"scrap": 0}`. That is a
  decision a person wrote down and can be asked about later; the same
  assumption made in code would be invisible.

Equipment codes and order codes in the file must be the ones **this MES**
uses. If the incumbent calls the machine something else, the export is the
place to translate it.

## Running it

```bash
fsmes inbound check          # the mapping, the folders, what is waiting
fsmes inbound watch --once   # one pass, and say what it did
fsmes inbound watch          # forever; this is what systemd or Compose runs
```

Every run states its totals over the whole file, not over a page:

```
inbound/counts/counts.csv: 4 rows read, 1 recorded, 0 already seen, 3 rejected
  row 2: equipment 'NOPE' not found
  row 3: good 'two' is not a number
  row 4: external_key is missing, and the contract requires it
  moved to inbound/processed/counts/counts.csv
1 file, 4 rows: 1 recorded, 0 already seen, 3 rejected
```

The same lines, plus the totals, are written to
`inbound/rejected/counts/counts.csv.rejects.txt`. Fix the rows or the
mapping and drop the file in again: nothing already recorded is recorded
twice.

## Reading it out of a database instead

A CSV export needs a person every day. A query needs a person once. Where the
system holding these facts has a database you can be given read-only
credentials to — the incumbent MES's, a quality system's, a historian's —
`fsmes inbound poll-sql` runs your query on a schedule and feeds the same
contract.

**The query is yours.** This repository does not know, and will never
contain, what any commercial system's tables are called. What it can tell you
is the shape the query has to return: **one row per event, one column per
contract field you are supplying, plus a column that only ever goes up.**
Everything else — which tables, which joins, which of that system's status
codes count as a finished record — is the question you take to whoever
administers it.

### The configuration

`MES_INBOUND_SQL_FILE` points at a JSON file with the same four keys the
column mapping has, plus the ones that describe the query. One entry per
event type:

```json
{
  "counts": {
    "source": "replay:incumbent-mes",
    "source_kind": "replay",
    "timezone": "America/Chicago",
    "url": "postgresql+psycopg://mes_readonly:...@10.0.0.9:5432/theirdb",
    "sql": "SELECT entry_id, entered_at, machine, good_qty, scrap_qty FROM their_table WHERE entry_id > :watermark ORDER BY entry_id",
    "watermark_column": "entry_id",
    "watermark_type": "id",
    "start_from": "0",
    "poll_seconds": 60,
    "statement_timeout_ms": 30000,
    "max_rows": 500,
    "columns": {
      "external_key": "entry_id",
      "recorded_at": "entered_at",
      "equipment": "machine",
      "good": "good_qty",
      "scrap": "scrap_qty"
    }
  }
}
```

`columns`, `defaults`, `time_format` and `timezone` mean exactly what they
mean for the folder driver, and are read by the same code. The rest:

- `url` — a [SQLAlchemy URL](https://docs.sqlalchemy.org/en/20/core/engines.html#database-urls).
  The driver for your database is your install: SQLite needs nothing,
  PostgreSQL is `pip install "factorysemantics-mes[postgres]"`, and anything
  else is whatever DBAPI that database publishes. **Read-only credentials**,
  and the paragraph below says why that sentence is doing the work.
- `sql` — your query. It must carry `:watermark` and an `ORDER BY`, and it is
  refused at load if it does not: without the parameter every pass would
  re-read the whole history, and without the order the cursor could step over
  a row a database chose to return late.
- `watermark_column` — the column in the result that only goes up. It is
  usually the same column the `WHERE` compares, and it does not have to be
  one you map onto the contract.
- `watermark_type` — `id` or `timestamp`. It is a label for people; the value
  itself is carried as text either way (see below).
- `start_from` — where a cursor that has never run starts, **exclusive**.
  Required, with no default, because the two things this MES could guess are
  "now", which silently skips everything already in that system, and "the
  beginning", which pulls ten years through a plant network on a Monday.
  Which of those you want is a decision, and a decision belongs in a file
  somebody signed.
- `poll_seconds`, `statement_timeout_ms`, `max_rows` — how often, how long a
  query may take, and how many rows one pass will take. A backlog drains over
  several passes rather than one transaction that holds for an hour.

### Reading, and only reading

Three things stand between this and a system somebody else depends on, and
`fsmes inbound sql-check` prints which of them it managed:

1. **The query is inspected before it is ever sent.** It has to start with
   `SELECT` or `WITH`, it has to be one statement, and it may not contain a
   word that could change anything — including a data-modifying CTE, which is
   a `SELECT`-shaped statement that deletes rows.
2. **The connection is opened read-only where the dialect has a way to say
   so.** SQLite is opened `mode=ro` with `PRAGMA query_only`; PostgreSQL gets
   `SET TRANSACTION READ ONLY` and your `statement_timeout`; MySQL and Oracle
   get their equivalents. Where a dialect has no such setting — SQL Server has
   none — the check says so in those words rather than staying quiet, because
   silence would read as success.
3. **Read-only credentials.** This is the one that actually holds. The other
   two are this MES being careful; only the grants are a guarantee, and they
   are the plant DBA's to give.

And one more, which is about their uptime rather than their data: **every row
is fetched and the connection closed before this MES writes anything.** A
slow write here can never become a lock over there.

!!! warning "Some databases want the cursor cast"

    The cursor is handed back to your database as **the text the column
    gave**, unchanged — a timestamp re-read into this MES's own convention
    would move the boundary by your system's UTC offset, and the rows in that
    gap would be skipped with nothing to say so. SQLite compares text against
    a typed column by the column's own affinity and needs nothing. PostgreSQL
    does not, and will say so plainly the first time you run `sql-check`; the
    cast belongs in your query: `WHERE stamp > CAST(:watermark AS timestamp)`.

### The cursor, and what holds it

The cursor is how far the poller has read. It is **not** what stops a row
being recorded twice — that is still `inbound_events`, keyed on the
supplier's own `external_key`. So a cursor that is behind costs a re-read and
changes nothing, which is the direction a cursor should fail in.

**A row nothing was done with is not a row that was read.** When a row cannot
be recorded, the cursor stops at it:

```
counts from replay:incumbent-mes: 4 rows read, 3 recorded, 0 already seen, 1 rejected
  row 3 (88213): equipment 'NOPE' not found
  cursor '0' -> '2'
  HELD at '3' by row 3 (88213): equipment 'NOPE' not found
  Every later row in the batch was still recorded. The cursor stays here and this
  row is tried again next pass. Fix it where it is, or step over it deliberately
  with `fsmes inbound sql-watermark --stream counts --set <value>`, which says in
  words what it is skipping.
  the same lines are in inbound/rejected/counts/sql.counts.20260910T121545.rejects.txt
1 query, 4 rows: 3 recorded, 0 already seen, 1 rejected; 1 cursor held (counts)
```

Note what happened to the rows *after* the bad one: they were recorded.
Leaving good data unread would be its own dishonesty. Only the cursor stopped
— so the next pass asks for the bad row again, and keeps saying so until
somebody deals with it. That is the loud failure, and it is deliberate: a
poller that stepped over a row it could not read would leave a hole in the
comparison with no way to find it later.

Stepping over it is a thing a person does, in words:

```bash
fsmes inbound sql-watermark                                # where every cursor stands
fsmes inbound sql-watermark --stream counts --set 3        # says what it would skip, changes nothing
fsmes inbound sql-watermark --stream counts --set 3 --force   # and means it
```

The rejected rows are also written to
`inbound/rejected/<stream>/sql.<stream>.<time>.rejects.txt`, beside the folder
driver's reports, so there is one place to look.

### Running it

```bash
fsmes inbound sql-check       # connect, run each query, map the rows, write nothing
fsmes inbound poll-sql --once # one pass, and say what it did
fsmes inbound poll-sql        # forever, each query on its own interval
```

`--once` exits non-zero when a query could not be run at all — a source that
is down, credentials that expired, a query the database rejected — because
that is a broken interface and cron should say so. A held cursor does not:
it is a finding about one row, it is already on the page, and it would
otherwise cry wolf every pass.

### Trying it without another system

The repository ships `config/inbound_sql.json`, which reads a SQLite file
named `inbound/example_source.sqlite` that does not exist until you make one.
Every table and column name in it is this project's own invention. Make the
file and run a pass:

```bash
sqlite3 inbound/example_source.sqlite "
CREATE TABLE manual_counts (entry_id INTEGER PRIMARY KEY, entered_at TEXT,
  counted_at TEXT, machine TEXT, order_no TEXT, good_qty REAL, scrap_qty REAL);
INSERT INTO manual_counts VALUES (1,'2026-09-10 17:00:00','2026-09-10 16:30:00','MIX01','',12,1);
INSERT INTO manual_counts VALUES (2,'2026-09-10 17:05:00','2026-09-10 16:40:00','MIX01','',8,0);
"
fsmes inbound sql-check
fsmes inbound poll-sql --once
```

`MIX01` is the demo plant's mixer, so those counts land against a machine
this MES knows. Point the same configuration at a row naming a machine it
does not know and you will see the cursor hold, which is worth doing once
before you point it at anything real.

## The honesty rules

These are why the module looks the way it does. Each of them is a way the
alternative would produce a convincing wrong number.

**A label goes on a stop this MES observed. It never creates one.** The
interval stays this MES's own observation; `reason` and `reason_source`
record who named it. A label for a stop the MES never saw is *refused*, with
that as the reason, because manufacturing an interval would put seconds into
availability that nothing here ever watched — and afterwards there would be
no way to tell those seconds from the real ones. A refusal here is a real
finding: the two systems disagree about what happened.

**A supplied label never overwrites one given here.** Only intervals that
carry no reason yet are labelled.

**Both verdicts are kept when a quality result disagrees.** The `result`
stored on a check is always this MES's own, computed from this MES's spec.
When the supplier sends a verdict too it is kept beside it in
`supplied_result`. The two disagreeing is a finding about the two systems,
and throwing one away would hide it. A reading for a characteristic this MES
has no spec for is refused rather than measured against a spec invented for
it, and an instrument this MES does not know is reported as untraceable
rather than created.

**A count is booked, never guessed into an order.** Booked against an open
operation when there is one. When there is not — the incumbent had the order
and this MES does not — the units are kept as *unassigned production*
against the machine, with the supplying system named. They are not dropped:
a unit the plant made may not disappear because the MES had nowhere tidy to
put it. Where the file names an order this MES cannot open, the units go to
the machine and the report says which order was refused.

**The supplier's clock, never ours.** A count is stamped with `when` the
supplier says the units were made, falling back to `recorded_at` — its own
recording time. This MES's clock is never used for a fact it was told.

## Where the source shows up

- **`ProductionLog.source`** gains a value, `external`, beside `manual` and
  `opc`. `source_system` beside it names the system. Null means this MES
  counted it itself; there is no other system to name.
- **The machine screen's unassigned-production list** prints
  *told by `replay:incumbent-mes`* or *counted here (opc)* on the row with
  the number.
- **`EquipmentState.reason_source`** rides with the reason, and the state
  timeline returns it.
- **The downtime pareto** gives every bucket a `labelled_by` breakdown, in
  seconds: `here` is this MES's own, the rest are named by supplier. A
  pareto that cannot separate the two cannot be audited.
- **`QualityCheck.source_system`** and **`supplied_result`** carry the
  supplier's name and its own verdict.
- **`inbound_events`** is the ledger of what has already been applied, keyed
  on `(source, kind, external_key)`, with one sentence saying what each
  event did and what it produced.

Nothing existing was rewritten by the migration that added these. A row from
before it was observed by this MES, and null is exactly the right answer to
"which other system told us".

## The broker (MQTT)

`fsmes uns publish` tells your broker what this MES recorded.
`fsmes inbound subscribe` is the other direction: it listens. A plant that
moves data has a broker — United Manufacturing Hub, Node-RED, an IoT
gateway of some make — and a first real plant may well have sensors,
counters and states that never touch an OPC UA server at all.

Two different things arrive on a broker, and this keeps them apart:

- **Tag values** — a counter, a state word, a process value published by a
  gateway. That is *machine data*, and it is held to exactly the discipline
  the [OPC agent](opc-readonly.md) holds its own to.
- **Inbound events** — the three shapes above, where a broker already
  carries downtime, quality or counts as JSON.

```bash
pip install 'factorysemantics-mes[mqtt]'   # the publisher's extra, the same one

MES_INBOUND_MQTT_MODE=mqtt                 # off (default) | mqtt
MES_INBOUND_MQTT_BROKER_URL=mqtt://broker.plant.example:1883   # mqtts:// for TLS
MES_INBOUND_MQTT_USERNAME=mes
MES_INBOUND_MQTT_PASSWORD=...
MES_INBOUND_MQTT_CLIENT_ID=fsmes-inbound   # NOT the publisher's id; see below
MES_INBOUND_MQTT_SOURCE=gateway:line1      # what your plant calls this broker
MES_INBOUND_MQTT_QOS=1
MES_INBOUND_MQTT_REPORT_SECONDS=60
```

```bash
fsmes inbound check          # also prints every topic filter and what it carries
fsmes inbound subscribe      # forever; this is what systemd or Compose runs
```

!!! warning "Give it its own client id"

    A broker disconnects the older session when two clients connect with the
    same id. The publisher is `fsmes` and the subscriber is `fsmes-inbound`
    for that reason, and `fsmes inbound subscribe` refuses to start if the
    two settings are equal rather than letting the two workers evict each
    other all shift.

### Tag values: the `mqtt` section of the tag map

Topic-to-tag wiring lives in the tag map, beside the OPC machines, so that
one document describes one plant:

```json
{
  "machines": [ { "equipment": "MIX01", "object": "MIX01", "cycle_seconds": 4.0 } ],
  "mqtt": {
    "tags": [
      { "topic": "umh/v1/acme/kc1/line1/MIX01/_historian/good_count",
        "equipment": "MIX01", "tag": "GoodCount", "json_path": "value" },
      { "topic": "umh/v1/acme/kc1/line1/MIX01/_historian/state",
        "equipment": "MIX01", "tag": "State", "json_path": "value",
        "state_map": { "1": "running", "2": "idle", "3": "down", "4": "setup" } },
      { "topic": "plant/line1/+/motor_temp",
        "equipment_from": 2, "tag": "MotorTemp" }
    ]
  }
}
```

| Key | What it is |
|---|---|
| `topic` | An MQTT topic filter. `+` and `#` work. One entry per filter; two entries for one filter are refused, because they would double-count every message. |
| `equipment` | The MES equipment code every message on this filter is about. |
| `equipment_from` | Instead of `equipment`: which level of the *topic* carries the machine code, counting from zero. For a namespace laid out by machine. The code that comes out of the topic still has to be one this MES holds — a topic level is never turned into a machine. |
| `tag` | The tag name. `State`, `GoodCount` and `ScrapCount` are *acted on*; every other name is recorded as tag history and nothing else. |
| `json_path` | A dotted path into a JSON payload, e.g. `value` or `payload.count`. Leave it out when the payload is the number itself. |
| `state_map` | Raw state word → MES state, for the `State` tag. A value that is not in it is **refused**, not guessed. |

A `mqtt` section is optional: a tag map without one simply has no broker
tags, which is what most plants start with.

### Events: a `topic` on a stream

A stream in the mapping file that names a `topic` is also read from the
broker. Everything else about it — the source, the zone, the time format,
the defaults — is unchanged, because those are your decisions and they do
not depend on which pipe the row came down. `fields` is there for when the
broker's JSON calls something different from what your files call it:

```json
{
  "downtime": {
    "source": "gateway:line1",
    "source_kind": "terminal",
    "timezone": "America/Chicago",
    "topic": "plant/line1/downtime",
    "columns": { "external_key": "stop_id", "recorded_at": "entered_at",
                 "equipment": "machine", "started_at": "stop_start",
                 "ended_at": "stop_end", "reason": "reason_code" },
    "fields":  { "external_key": "id", "recorded_at": "ts",
                 "equipment": "eq", "started_at": "from",
                 "ended_at": "to", "reason": "reason" }
  }
}
```

A stream with no `topic` is read from its folder only. You can take downtime
labels off the broker and quality results out of a folder; the mapping file
is where that is said.

### Three rules this transport needs

**A counter is a total, never an increment.** MQTT at QoS 1 is
*at-least-once*: after a reconnect the broker may hand over the same message
twice, and there is nothing in it to tell the second delivery from the
first. A redelivered **total** changes nothing, because a delta is the rise
above the last value this MES saw. A redelivered **increment** would book
units the plant never made. So a mapping that declares `"counter":
"increment"` is refused at start-up, and told the two things it can do
instead: publish the machine's own running total, or publish the counts as
`counts` events, which carry your key and deduplicate on it.

The rest of the counter rule is the OPC agent's, unchanged. The first
reading is a baseline and books nothing — the units before it were made at
an unknown time, and none of them are invented. A counter that has fallen to
near zero is a gateway or PLC reset and becomes the new baseline, because
the units around the reset are unknowable.

**A retained message sets a baseline and nothing else.** A broker replays a
retained message to every new subscriber as though it had just happened, and
nothing in the message says how old it is. Booking a state change from one
would put an hour of the wrong state into availability; writing it to tag
history would put an hour-old reading on this minute's chart. So a retained
message is used for the one thing it is honestly good for — the counter
baseline that saves the first delta from being wrong — and counted in the
report. A retained message on an *event* topic is refused and said once: a
publisher retaining events is using the broker as a database.

**Nothing is published.** The subscriber holds no publish call, and a test
in the suite checks the module rather than trusting the claim. It is not
gated by shadow mode for the reason at the top of this page, and
[the register](shadow-mode.md) carries the entry.

### What a run says

The subscriber is a worker with no end, so it says what it has taken in
every `MES_INBOUND_MQTT_REPORT_SECONDS`. Every number is a total over
everything received since it started, and a refusal the broker repeats is
grouped with a count rather than logged a thousand times:

```
1204 message(s) received: 1180 reading(s) recorded, 214 booking(s),
6 state change(s) (908 repeated the open state), 3 event(s) recorded,
1 already seen, 12 refused, 8 on topics nothing is mapped to,
4 retained (baseline only).
  12 x plant/line1/MIX01/state: State=7 is not in the state_map for ...
```

`8 on topics nothing is mapped to` is in there on purpose: a plant's broker
carries far more than this MES was pointed at, and a subscriber that said
nothing about the rest would look like it was working when it had been
pointed at the wrong tree.

## What is not written yet

A **SQL poller**, against the same three shapes — a transport over a
contract that already exists, which will not change what a row means when it
lands. There is no inbound REST endpoint. **No broker has been tested
against this yet**: the suite drives the subscriber with a fake source, and
this repository does not start brokers on the machine it is developed on.
If you run it against a real Mosquitto, HiveMQ or UMH, an issue saying which
broker and what it did is the most useful thing you can send. Ask in
[Discussions](https://github.com/factorysemantics/factorysemantics-mes/discussions);
An **MQTT subscriber**, for tag values and events that never touch OPC UA. It
is a third transport over a contract that already exists, and it will not
change what a row means when it lands. There is no inbound REST endpoint
either, and no driver for a system that only offers a web API.

Nor is there any translation of codes. If the other system calls the mixer
something else, or files a stop under a reason code this MES has never heard
of, the query is where you translate it — a `CASE` or a join against a lookup
table on that side, which is where somebody already knows what those codes
mean. Building a mapping table in here would be this MES guessing about a
system it cannot see.

Ask in [Discussions](https://github.com/factorysemantics/factorysemantics-mes/discussions);
a question asked twice becomes a page.
