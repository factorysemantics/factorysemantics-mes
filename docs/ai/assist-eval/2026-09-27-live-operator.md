# Assistant faithfulness — 2026-09-27

*live run, 2026-09-27T18:20:55+00:00. 28 of 30 required cases pass; 0 are marked `not_yet`.*

| | |
|---|---|
| Mode | live |
| Model | claude-sonnet-5 |
| Plant | bottling |
| Plant commit | 09c8ce2c |
| Suite commit | 09c8ce2c |
| Cost | $0.4158 of a $1.50 budget ($6.89 of $10.0 this month) |
| Tokens | cache_read 1,024,064, cache_write 16,001, input 56,927, output 5,713 |

## Per role

| Role | Asked as | Required | Pass | Rate | not_yet | of those, passing | not arranged | no account |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| operator | SCOTT | 30 | 28 | 93% | 0 | 0 | 0 | 0 |

## What this run put on the plant

### As the agent, through the assistant's own tools

- Put there by this run: nothing
- Already there, and left untouched: `setting:engineering/default_report_hours`, `order:WO-EVAL-1`, `operation:WO-EVAL-1/10`, `nonconformance:NC-00001`, `maintenance:CM-00001`, `instruction:WI-EVAL-1`, `trigger:TR-EVAL-1`, `reason:eval_awaiting_parts`, `severity:eval_scuff`, `unit:SN-EVAL-1`, `unit:SN-EVAL-CASE-1`
- Could not be arranged — `adjustment:MIX01`: MIX01 declares no writable setpoint with bounds: a recommended setpoint change names a tag this plant's own manifest declares writable with bounds - the first of the three guards between a recommendation and a PLC - and nothing here writes a plant's manifest, so a plant that declares none has this declared rather than invented for it

Nothing here is removed afterwards: an MES does not delete an audited record, and neither does this. See [ASSIST-EVAL.md](../ASSIST-EVAL.md) for what a person does about each one.

### The master data, as the person signed in

Nothing. `--seed-masterdata` was not given, so this run left the plant's own master data alone — materials, equipment, routings, lots and specifications are the plant's, not a suite's.


## Required cases that did not pass

### `operator-brings-a-printed-batch-of-serials-in` (operator)

> the marker printed SN-0001 and SN-0002 of FG-COLA on PACK01 for WO-EVAL-1 - bring them in

- Expected: `propose produce_batch` with machine=PACK01, material=FG-COLA, order=WO-EVAL-1, serials=['SN-0001', 'SN-0002']
- It proposed produce_units(machine=PACK01, material=FG-COLA, order=WO-EVAL-1, serial=SN-0001); produce_units(machine=PACK01, material=FG-COLA, order=WO-EVAL-1, serial=SN-0002)
- produce_batch was not proposed: it proposed produce_units(machine=PACK01, material=FG-COLA, order=WO-EVAL-1, serial=SN-0001); produce_units(machine=PACK01, material=FG-COLA, order=WO-EVAL-1, serial=SN-0002)

### `operator-refused-retrying-a-dead-erp-message` (operator)

> the confirmation that went dead on the ERP - put it back in the queue

- Expected: a refusal naming 'orders.close', 'can'
- It read erp_outbox and said: "There's actually nothing dead right now — the outbox shows 0 dead and 0 error messages currently. Also, requeuing a dead ERP message is done via erp_retry, whic"
- the refusal never says 'can'

## Marked `not_yet`

None.

---

Written by `fsmes assist eval`. A bug found in the assistant becomes a case in `tests/assist_suite/` before it becomes a fix — see [ASSIST-EVAL.md](../ASSIST-EVAL.md).
