# The shadow scorecard: how well did the shadow match?

*How-to. For the engineer who has run this MES beside the one in charge for
a week or two and now has to answer "was it any good?".*

The whole point of a shadow run is the comparison at the end of it, and an
impression is not a comparison. This command takes two records of the same
period — what the MES in charge booked, and what this MES would have sent
your ERP — and reports, per order and per operation, where they agree and
where they do not.

```bash
fsmes shadow scorecard \
  --incumbent bookings-week-37.csv \
  --map our-mapping.json \
  --ours /var/lib/fsmes/erp/outbox \
  --html scorecard.html \
  --json scorecard.json
```

It reads files. It contacts nothing, changes nothing, and needs no
connection to either system.

!!! note "It does not say which side is right"

    It says where the two differ, with both sets of numbers, and it says
    what it could not compare. Which record to believe is a question about
    your plant, usually settled by walking out to the machine. A tool that
    answered it would be guessing, and the guess would always flatter the
    tool.

## The incumbent's side: what to export

Your MES in charge will not have an API you can point this at, and it does
not need one. Someone exports the period's bookings from a report screen as
**CSV**, or your integrator writes them as **JSON** (a list of objects, one
per booking). What matters is the columns, not the product:

| Column | Required | What it is |
|---|---|---|
| `order` | **yes** | the order the work was booked against |
| `operation` | no | the operation's sequence or code within that order. **Leave it blank** for a row that is the order's own total rather than one step of it. |
| `equipment` | no | the machine or work centre the step ran on |
| `good_qty` | no | units booked good |
| `scrap_qty` | no | units booked scrap |
| `started_at` | no | when the step started |
| `completed_at` | no | when it finished |

Only `order` is required, because only `order` is a row's identity.
Everything else is compared when both records state it and reported as
**not compared** when either does not.

A blank cell is *unknown*, never zero. An export with no scrap column does
not mean a week with no scrap, and a scorecard that read it that way would
invent agreement — so the report names the missing column instead and
counts nothing for it.

Extra columns are ignored. Rows with no order code are counted and named
rather than silently dropped.

An export like this compares cleanly:

```csv
order,operation,equipment,good_qty,scrap_qty,started_at,completed_at
WO-2026-0041,10,MIXER-1,118,2,2026-03-04 06:00:00,2026-03-04 06:41:00
WO-2026-0041,20,FILLER-1,118,0,2026-03-04 06:45:00,2026-03-04 07:20:00
WO-2026-0041,,,118,2,2026-03-04 06:00:00,2026-03-04 07:20:00
```

## The mapping file: your codes, not ours

Two things will differ and both are config, never code: the headings your
export uses, and the order and equipment codes inside it. Put them in one
JSON file and pass it with `--map`.

```json
{
  "columns": {
    "order": "ORDER_NO",
    "operation": "OPER",
    "equipment": "WORK_CTR",
    "good_qty": "QTY_OK",
    "scrap_qty": "QTY_SCRAP",
    "started_at": "DT_START",
    "completed_at": "DT_END"
  },
  "orders":     { "4700041": "WO-2026-0041" },
  "operations": { "0010": "10", "0020": "20" },
  "equipment":  { "MIX01": "MIXER-1" },
  "time_format": "%d.%m.%Y %H:%M:%S",
  "tolerances": { "quantity": 0, "seconds": 60 }
}
```

Every section is optional:

- **`columns`** — this MES's column name on the left, your export's heading
  on the right. Omit a column and its own name is used.
- **`orders`**, **`operations`**, **`equipment`** — the incumbent's code on
  the left, this MES's on the right. An order code that is not listed is
  used as it stands, which is what you want when the two systems already
  share a code.
- **`time_format`** — a
  [`strptime` format](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes)
  for exports whose timestamps are not ISO 8601. Say it rather than leaving
  it to be guessed: `03/04` is the fourth of March in one country and the
  third of April in another, and guessing wrong shifts a whole shift.
- **`tolerances`** — `quantity` in units, `seconds` in seconds. Defaults are
  **0 units** and **60 seconds**: two systems counting the same machine
  should agree on units exactly, while starts and ends booked by hand at the
  end of a shift will not agree to the second. Whatever you set is printed
  at the top of your own report.

A section this reader does not use — a misspelling, most often — is refused
outright, and nothing is mapped. A mapping file that half worked would be
worse than one that did not run.

`--quantity-tolerance` and `--seconds-tolerance` on the command line
override the file for one run.

## This MES's side

Either the folder of confirmation files a shadow run writes:

```bash
fsmes shadow scorecard --incumbent theirs.csv --ours /var/lib/fsmes/erp/outbox
```

…or, with `--ours` left off, this MES's own outbox in its database — which
still holds every confirmation after your ERP team has collected the files
and emptied the folder.

Both sides read the same
[confirmation contract](confirmation-files.md) the file adapter writes, in
JSON or in B2MML XML. The same confirmation present as both is one
confirmation, not two.

## Reading the report

The terminal prints all of it. `--html` writes one self-contained page —
no internet, no scripts, safe to mail around — and `--json` writes the same
figures for whatever you do next with them.

Four parts, in order:

1. **What was read**, with the totals: rows in the export, confirmations
   from this MES, and how many orders and operations are in both records.
2. **Agreement**, per operation and per order, for good quantity, scrap
   quantity, start, end and duration. Each row says how many agreed, how
   many differed, how many could not be compared, and out of how many.
3. **Where they differ** — every one of them, by order and operation, with
   both records' numbers and how far apart they are.
4. **What this cannot tell you** — read this one. Orders only one record
   holds, operations with nothing to compare against, fields one side never
   stated, rows that could not be read, and the two limits no file
   comparison can escape.

There are no charts, on purpose. The chart this invites is a bar showing
how much agreed, and that bar averages away exactly what you are looking
for: two records agreeing on 998 of 1,000 comparisons and differing by 400
units on the other two describe a plant with a real problem, and the bar
would look 99.8% full.

## What it will not do

**It will not add operations up into an order total.** Good units at
operation 10 and good units at operation 20 are usually the same units, and
summing them produces a number that looks like a total and is not one. Order
totals are compared only against order rows your export actually carries;
if it carries none, the report says so.

**It cannot see a period neither record covers.** A shift this MES was not
connected for, a stop nobody labelled, a counter that reset between two
readings: the comparison is bounded by what is in the two files, and it says
so rather than letting silence read as agreement.

**It does not judge.** Every difference is stated as "the incumbent has X,
this MES has Y". The tie-breaker is the machine.

## Related

- [Running beside an existing MES](shadow-mode.md) — the setting that makes
  a shadow run safe in the first place.
- [The confirmation handoff](confirmation-files.md) — what this MES's side
  of the comparison is, file by file.
- [What to expect in the first week](first-week.md) — what is worth
  comparing against the system in charge, and what is not.
- [Running FactorySemantics MES beside your existing MES](first-plant.md) —
  the whole afternoon, in order.
