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
9.5–11.5 on `FG-COLA`, all of it master data a run never creates — plus the
eight rows `arrange` puts on it: `[process] default_report_hours` at 10.0,
released order `WO-EVAL-1` (1000 of `FG-COLA`), non-conformance `NC-00001` (brix
20.0), corrective maintenance order `CM-00001` on `MIX01`, and four unapproved
drafts: instruction `WI-EVAL-1`, trigger `TR-EVAL-1`, downtime reason
`eval_changeover` and severity `eval_cosmetic`.

Drafting cases keep the code a person would type (`changeover`, `WI-BRIX`,
`TR-HOT`); the drafts a run puts up carry an `EVAL-`/`eval_` prefix. The two
must never be the same code: one needs the plant to have it and the other needs
it not to, and a test fails if they ever collide.
