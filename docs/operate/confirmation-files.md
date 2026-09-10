# The confirmation handoff

*How to read, check and sign off what this MES sends an ERP — without
connecting it to one.*

A plant that runs this MES beside its existing one, in shadow mode, has no
live ERP link. Its people still have to answer the question that decides
whether the thing is trusted: **would what it sends be correct if it were
connected?**

This page is how you answer it from files alone. There are three parts to
it, and none of them needs a running ERP:

1. **[The contract](../reference/erp-confirmations.md)** — every field, what
   it means on the floor, where the MES gets it, and when it is null. It is
   generated from the code that writes the files, and published as
   [`erp-confirmation.schema.json`](../reference/erp-confirmation.schema.json)
   so your own tooling can validate against it.
2. **Worked examples** — real files from a run of the demo plant, JSON and
   B2MML side by side, listed
   [on the same page](../reference/erp-confirmations.md#worked-examples).
3. **`fsmes erp validate`** — point it at the outbox and read the answer in
   words.

## What is sent, and when

Two documents, and nothing else:

| Document | Sent when | Carries |
|---|---|---|
| **Operation confirmation** | a step of an order is completed | the step, the machine, its cost centre, what went in and what came out, how long the step was open, and every lot consumed at it |
| **Order completion** | the last step of an order is completed | what was ordered, what came out good, what was scrapped, how far the line ran past the order, and the finished-goods lot if there is one |

Both are keyed. `message_key` is `WO-1:op10` for a step and
`WO-1:completion` for the close, and it is the whole of the idempotency
contract: **a reader that sees a key it has already posted must post
nothing.** The MES will re-send after a failure, and a file left in a
folder is easy to collect twice.

There is no money anywhere in either document. The MES reports quantities
and time against a cost centre; the ERP owns what they are worth.

## The outbound folder

With `MES_ERP_MODE=file`, three folders make up the whole interface —
`MES_ERP_INBOX`, `MES_ERP_OUTBOX` and `MES_ERP_ARCHIVE`. Schedules arrive
in the inbox and move to the archive once read (a file nobody could parse
keeps its name plus `.rejected`, rather than vanishing). Confirmations are
written to the outbox, one file each:

```text
000123_20260910T041500Z_WO-2026-0041_op10.xml
└─┬──┘ └───────┬──────┘ └─────┬─────┘ └─┬─┘
  │            │              │         └── op<seq>, or "completion"
  │            │              └── the order
  │            └── when it was written, UTC
  └── the sequence number
```

- **Order.** Sort the folder by name and you have the order the MES wrote
  them, which is the order they happened. The sequence number is what makes
  that true; timestamps to the second are not enough.
- **Delivered.** For a file exchange, written *is* delivered. The MES marks
  the message sent once the file is closed on disk and never opens it
  again. Collecting, moving or deleting the file is yours to do, and the MES
  neither requires it nor notices it. The record that survives either way is
  the outbox in the MES's database — `fsmes erp` and the supervisor's outbox
  screen read it.
- **Half-written files.** You will never collect one. Each document is
  written to a hidden `.part` file and renamed into place, so a poller sees a
  whole document or no document.
- **Restart.** The sequence number is read back from the folder at start-up,
  so a restarted worker carries on rather than colliding. A folder you have
  emptied starts again at 1: the numbers order the files that are there
  together, they are not an audit sequence.
- **Order codes.** Anything outside `A-Za-z0-9_-` in an order code becomes a
  dash in the file name. An order code can never decide where a file lands.

## Checking a folder

```console
$ fsmes erp validate erp_exchange/outbox
Checking erp_exchange/outbox against the ERP confirmation contract.
  ok      000001_20260910T041500Z_WO-2026-0041_op10.xml  (operation_confirmation)
  ok      000002_20260910T042000Z_WO-2026-0041_op20.xml  (operation_confirmation)
  ok      000003_20260910T042000Z_WO-2026-0041_completion.xml  (order_completion)
3 documents checked: 3 correct, 0 with problems (0 in all), 0 notes. 0 files skipped.
```

It reads files and nothing else: no ERP, no connector, no database. Give it
one file or a whole folder, JSON or B2MML. It states its totals, including
what it skipped, because a list that does not say what it left out reads as
the whole story.

Two kinds of finding, and the difference is the point:

- A **problem** is something an ERP would be right to reject, or a number
  that contradicts another number in the same document: a negative quantity,
  a `wip_qty` that does not equal `input_qty - good_qty - scrap_qty`, a
  `machine_seconds` that disagrees with the two timestamps beside it, an
  over-run reported as an ordinary good quantity, two files sharing a
  `message_key` and telling different stories. Any problem exits non-zero,
  so this can gate a deployment.
- A **note** is something true and worth reading: a negative work in
  progress, a completion with no lot, a missing cost centre, the same
  confirmation twice. Notes never fail the run. Every one of them is a fact
  the MES reports on purpose, and failing on them would only teach a plant
  to make its MES lie.

What it cannot tell you is whether the numbers describe what the line really
did. That is the plant's question, not a file's, and the rules the MES
follows in answering it are in
[never invent production](../plant/never-invent-production.md).

## What this is, and what it is not

The contract is **SAP-shaped**: a confirmation per operation carrying
quantities, cost centre, times and component consumption is what CO11N
wants, and it is also what ERPNext's Job Cards and Stock Entries are built
from. One contract, every target.

Shaped for SAP is not the same as tested against SAP. **No SAP has consumed
one of these documents.** There is no SAP connector in this project — see
[compatibility](compatibility.md) for what is written, what is tested
against a real system, and what is neither. What exists is the contract, the
schema, the files and the validator, which is exactly what an ERP team needs
in order to say whether the connector somebody writes next would be right.

The B2MML here is a dialect: the two documents that matter, no namespace, no
schema reference. If your middleware needs full B2MML, the JSON payloads and
the published schema are the better starting point.
