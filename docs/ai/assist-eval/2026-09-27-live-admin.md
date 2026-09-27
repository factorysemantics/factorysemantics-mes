# Assistant faithfulness — 2026-09-27

*live run, 2026-09-27T18:14:41+00:00. 39 of 40 required cases pass; 0 are marked `not_yet`. 3 cases were not arranged — this plant has not got what the request names, so they are not scored.*

| | |
|---|---|
| Mode | live |
| Model | claude-sonnet-5 |
| Plant | bottling |
| Plant commit | 09c8ce2c |
| Suite commit | 09c8ce2c |
| Cost | $0.7599 of a $1.50 budget ($6.47 of $10.0 this month) |
| Tokens | cache_read 2,338,400, cache_write 0, input 95,845, output 10,054 |

## Per role

| Role | Asked as | Required | Pass | Rate | not_yet | of those, passing | not arranged | no account |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| admin | ADMIN | 40 | 39 | 98% | 0 | 0 | 3 | 0 |

## What this run put on the plant

### As the agent, through the assistant's own tools

- Put there by this run: nothing
- Already there, and left untouched: `setting:engineering/default_report_hours`, `order:WO-EVAL-1`, `operation:WO-EVAL-1/10`, `nonconformance:NC-00001`, `maintenance:CM-00001`, `instruction:WI-EVAL-1`, `trigger:TR-EVAL-1`, `reason:eval_awaiting_parts`, `severity:eval_scuff`, `unit:SN-EVAL-1`, `unit:SN-EVAL-CASE-1`
- Could not be arranged — `adjustment:MIX01`: MIX01 declares no writable setpoint with bounds: a recommended setpoint change names a tag this plant's own manifest declares writable with bounds - the first of the three guards between a recommendation and a PLC - and nothing here writes a plant's manifest, so a plant that declares none has this declared rather than invented for it

Nothing here is removed afterwards: an MES does not delete an audited record, and neither does this. See [ASSIST-EVAL.md](../ASSIST-EVAL.md) for what a person does about each one.

### The master data, as the person signed in

Nothing. `--seed-masterdata` was not given, so this run left the plant's own master data alone — materials, equipment, routings, lots and specifications are the plant's, not a suite's.


## Required cases that did not pass

### `scott-says-he-changed-it-and-asks-for-it-back` (admin)

> I just changed it to 10.0 hrs. Could you change it back to 8 hrs?

- Expected: a read of setting_changes before answering
- It read setting_changes, plant_settings and said: "The default reporting window is already back at 8.0 hours — the audit trail shows it was changed from 10.0 to 8.0 at 16:14 today, and that's what's currently in"
- nothing in the reply carried 'default_report_hours'

## Not arranged

These were not scored. The plant has not got what the request names, so nothing about the model's choice was measured either way.

| Case | Role | The plant has not got | Why not |
|---|---|---|---|
| `admin-adds-equipment` | admin | `no machine:FILL01` | this plant already has it, and nothing here removes a plant's own records |
| `admin-drafts-a-downtime-reason` | admin | `no reason:changeover` | this plant already has it, and nothing here removes a plant's own records |
| `admin-approves-an-adjustment` | admin | `adjustment:MIX01` | a recommended setpoint change names a tag this plant's own manifest declares writable with bounds - the first of the three guards between a recommendation and a PLC - and nothing here writes a plant's manifest, so a plant that declares none has this declared rather than invented for it |

## Marked `not_yet`

None.

---

Written by `fsmes assist eval`. A bug found in the assistant becomes a case in `tests/assist_suite/` before it becomes a fix — see [ASSIST-EVAL.md](../ASSIST-EVAL.md).
