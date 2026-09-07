# 0004 — Never invent production: the counter discipline

- **Status:** accepted
- **Date:** 2026-08-31
- **Deciders:** @kalwei

## Context
Ported with the kernel on 2026-08-31 from the maintainer's earlier project, where booking the order quantity on completion and treating a counter reset as production had each once produced a convincing wrong number.

## Options considered
| Option | For | Against |
|---|---|---|
| Counter deltas only; resets re-baseline; unknown over zero | the booked number is always something a machine said | screens show *unknown* where other systems show a number |
| Book order quantity on completion | matches the ERP's expectation | fiction; the ERP's number is what was wanted |
| Interpolate gaps | fewer grey segments | fiction |

## Decision
Production is booked from counter deltas read over OPC UA. A counter falling toward zero is a reset: re-baseline, book nothing. Stale and duplicate notifications book nothing. A KPI that cannot be computed returns `null` with a reason. Unlabelled downtime is reported as unlabelled. The scoring harness stages a reset in every scripted hour.

## Consequences
Easy: trusting the number. Hard: the conversation with a plant whose previous system showed 0 % where this one shows *unknown*. The fix is always upstream, never a default in code.

## House rules touched
Rules 1, 2 and 3 are this decision.
