# The assistant's request suite

One file per role, in the words a person at a machine actually types. Each case
says what the assistant owes in reply. `fsmes assist eval --scripted` runs them
against the real plumbing with the model scripted; `fsmes assist eval --live`
runs them against the real model on a running plant.

Read [docs/ai/ASSIST-EVAL.md](../../docs/ai/ASSIST-EVAL.md) before adding a
case. The short version: **a bug somebody finds in the assistant becomes a case
here before it becomes a fix.**

## What has to be on the plant

A case says so itself, in `requires`, as `kind:code` — or `no kind:code` where
the sentence only makes sense on a plant that has *not* got one (drafting a
downtime reason `changeover` is not a question you can put to a plant whose
vocabulary already holds one). Anything the plant has not got makes that case
**not arranged**: not asked, not scored, listed with the reason. The kinds are
in `fsmes.lab.assist_fixtures`, which is also what puts the arrangeable ones
there.

The plant every case is asked about is the demo plant — machines `MIX01` and
`PACK01` on line `LINE1`, materials `FG-COLA` and `RAW-SUGAR`, lots
`LOT-SUGAR-001` and `LOT-FLAVOR-001`, routing `RT-COLA`, a brix specification of
9.5–11.5 on `FG-COLA`, all of it master data a run never creates — plus the rows
`arrange` puts on it: `[process] default_report_hours` taken to 10.0 and put back
to 8.0, released order `WO-EVAL-1` (1000 of `FG-COLA`), non-conformance
`NC-00001` (brix 20.0), corrective maintenance order `CM-00001` on `MIX01`,
identified units `SN-EVAL-1` and `SN-EVAL-CASE-1`, and five unapproved drafts — instruction `WI-EVAL-1`, trigger `TR-EVAL-1`, downtime
reason `eval_awaiting_parts`, severity `eval_scuff`, and a recommended setpoint
change on `MIX01` where the plant's tag manifest declares a writable setpoint.

Two kinds are declared and never made, and each has a case that says so in its
own note: a **gauge** is master data, so nothing can calibrate one here; and a
**dead ERP message** is what a refusing ERP leaves behind, so nothing makes one.
`calibrate_gauge` and `erp_retry` are in the suite as an operator being refused
them, which is what this suite's rule of one case per write tool asks for and is
also the thing that went wrong: both were offered to an operator until
2026-09-27.

**A case never describes the arrangement it runs on.** Drafting cases keep the
code a person would type (`changeover`, `cosmetic`, `WI-BRIX`, `TR-HOT`); the
drafts a run puts up carry an `EVAL-`/`eval_` prefix *and their own words*. Both
halves matter, and the second one was learnt the hard way on 2026-09-27: asked to
"draft a cosmetic severity" on a plant carrying the run's own `eval_cosmetic`, the
model answered "there's already a draft 'cosmetic' severity" — right, and scored
as a failure. Two tests hold the line now: no drafting case and no arranged draft
may share a code, and none may read like the other. The same goes for a value —
a run leaves the reporting window at 8.0 precisely because a case asks for 10.
