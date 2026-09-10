# Feeding the MES what people typed elsewhere

*How-to. Downtime labels, quality results and counts that were recorded in another system, dropped into a folder as CSV, and recorded here as what they are: told, not observed.*

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
a question asked twice becomes a page.
