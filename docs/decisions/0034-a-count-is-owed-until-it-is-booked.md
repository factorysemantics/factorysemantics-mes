# 0034 — A count the machine made is owed until it is booked, and a reading that fails is retried rather than forgotten

- **Status:** accepted
- **Date:** 2026-09-18
- **Deciders:** maintainer

## Context

House rule 1 has always been read one way: never invent production. A counter
delta books production; a counter falling toward zero is a PLC reset that
re-baselines and books nothing; a stale or duplicate reading counts for
nothing. Every one of those protects the plant from a number that is too big.

On 2026-09-18 a lab plant showed the other direction, and nothing in the
product had ever named it.

Two plants ran six hours at replay speed 10. The bottling plant's log held
**18,564** `sqlite3.OperationalError: database is locked`. Among them, in the
last 150,000 lines alone, **seventy** `failed to book production` lines, each
naming a machine and eight to ten good units. Those units were counted by a
machine, arrived at the MES, and never reached a book.

They were not merely dropped. The agent measures a delta against the last
counter value it saw, and it moved that baseline as it computed the delta —
before the transaction that booked the delta had committed. So a booking that
failed took its units with it permanently, because the next reading was
measured from a baseline that had already advanced past them. The retry that
exists precisely to stop a dropped batch made it worse: re-running `_book`
recomputed the delta from the moved baseline, found zero, committed nothing
and reported **success**. A lock failure did not even leave a trace in the
counts. Sixty seconds of the reproduction, before the fix: twelve units
counted by six machines, **zero** booked.

`failed to book decisions` — the line that fires when all four attempts are
spent — said how many rows it gave up on and nothing about what they were
worth.

A plant that under-reports its own output is as wrong about its day as one
that over-reports it, and it is harder to notice: nobody queries a number for
being too small. The MES had a rule against one and no position on the other.

## Options considered

| Option | For | Against |
|---|---|---|
| Leave it: a failed write is logged, and the operator can reconcile | Nothing to build; the log does say so | The log said `good=9` at ERROR ten times a second among 282-line tracebacks. Nobody reconciles from that, and the number on the screen is wrong meanwhile |
| Book first, un-book on failure | Keeps one code path | Un-booking is a compensating write that itself needs the lock that just failed, and a crash between the two leaves invented production — the failure this product least tolerates |
| Queue failed deltas and replay them later | Nothing is ever retried from scratch | A durable queue is a second store of production facts, and two stores that can disagree about what a machine made is the problem this MES exists to remove |
| **Move the baseline only when the booking commits** | A counter is absolute, so the next reading re-measures the whole delta; late, never lost. No second store, no compensating write | The units arrive a tick late, and a machine whose readings stop entirely still owes what it was owed |

## Decision

The agent's counter baseline moves only when the transaction that booked the
delta has committed. Each attempt measures its deltas into a pending set;
that set becomes the baseline on commit and is discarded on failure, so a
failed attempt leaves the counter where it was and the next reading measures
the whole delta from there. A machine whose own booking fails inside its
savepoint while the rest of the batch commits is dropped from the pending set
by itself.

`failed to book decisions` and `failed to book production` say how many units
are still owed. Owed is the word: the plant made them, the MES has not
recorded them yet, and it will when the next reading arrives.

What does not change: a counter that falls toward zero still re-baselines and
books nothing, because the units around a PLC reset are unknowable. Nothing
here books a unit the machine did not count.

## Consequences

Easier: a write that fails under contention costs a tick's delay rather than
the production it was carrying, and the log says what is outstanding rather
than only that something went wrong.

Harder: a reading that never arrives — an agent killed, a machine unplugged —
still leaves its last delta unbooked, and the counter's absolute value is what
will recover it when the agent next sees the machine. That is the right
behaviour and it is not instant; decision 0030 already names the wider case of
a machine nobody was watching.

To revisit: the MQTT inbound driver keeps its own counter baseline
(`integrations/inbound/mqtt.py`) and has the same shape. It books one reading
at a time rather than a batch, so the window is narrower, but the rule here
applies to it and it has not yet been changed to follow it.

## House rules touched

**1 — never invent production**, read in the direction it had not been read.
Counter deltas still book production and a reset still books nothing; what is
added is that production the machine counted may not be quietly lost either.

**2 — unknown is a valid answer; zero is not.** A booking that failed used to
leave the plant's total silently short. It now leaves a line saying how many
units are owed, and the units themselves arrive on the next reading.
