# The assistant's request suite

One file per role, in the words a person at a machine actually types. Each case
says what the assistant owes in reply. `fsmes assist eval --scripted` runs them
against the real plumbing with the model scripted; `fsmes assist eval --live`
runs them against the real model on a running plant.

Read [docs/ai/ASSIST-EVAL.md](../../docs/ai/ASSIST-EVAL.md) before adding a
case. The short version: **a bug somebody finds in the assistant becomes a case
here before it becomes a fix.**

The plant every case is asked about is the demo plant, plus what
`fsmes.services.assist_eval.arrange` puts on it: order `WO-EVAL-1` (released,
1000 of `FG-COLA`), non-conformance `NC-00001` (brix 20.0 against a 9.5–11.5
specification), corrective maintenance order `CM-00001` on `MIX01`, and
`[process] default_report_hours` changed from 8.0 to 10.0 by `ADMIN`. Machines
are `MIX01` and `PACK01` on line `LINE1`; lots are `LOT-SUGAR-001` and
`LOT-FLAVOR-001`; the routing is `RT-COLA`.
