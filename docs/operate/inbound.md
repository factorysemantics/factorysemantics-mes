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

So there is a second front door. Three event shapes, one contract, and — for
now — one driver: files in a folder.

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

## What is not written yet

A **SQL poller** and an **MQTT subscriber**, against the same three shapes.
Both are transports over a contract that already exists, and neither will
change what a row means when it lands. There is no inbound REST endpoint
either. Ask in [Discussions](https://github.com/factorysemantics/factorysemantics-mes/discussions);
a question asked twice becomes a page.
