# Explanation — what happens when the MES loses sight of a machine

A plant loses its connection to the machine layer most weeks. A switch
reboots, a certificate expires, an engineer unplugs the wrong port, a PLC is
swapped. The line does not stop. The machines are fine. The MES just cannot
see any of it.

This page is what this MES does about that, and — more usefully — what it
refuses to do. The decision behind it is
[0030](../decisions/0030-a-lost-connection-is-unknown-time.md).

## The two wrong answers

They are symmetrical, and both survive for years because the number still
looks plausible.

**Read the gap as a stop.** The machine goes into `down` for the length of
the outage. Now the plant has a breakdown that never happened, on a machine
that was running the whole time, and somebody walks out to look at it.

**Read it as the last state we heard.** The machine stays `running` — or
`down`, or whatever it was — until the link comes back. Now the plant has
availability nobody was watching. This is the worse of the two, because it
reads *better*, so it is believed. It is also what this MES did before
2026-09-14: the agent logged the dropped connection and slept, the open
state interval stayed open, and the invented run time went straight into
availability's numerator and the machine's maintenance hours.

## What it does instead

**The connection is its own history.** `equipment_connections` holds one row
per contiguous stretch of being able — or unable — to see a machine, the same
shape `equipment_states` has. A machine can be *down and reachable* or *fine
and unreachable*, and a plant that has to pick one column for both has thrown
away the more useful half.

**The state history stops where the evidence stops.** When the agent records
a disconnection it closes the machine's open state interval at the last
moment there was evidence, and opens nothing in its place. For the length of
the outage the machine has **no state**, so every screen and every API
answers `unknown` — the word they already use for a machine they have no
interval for. Nothing carries on accruing: not run time, not downtime, not
maintenance hours.

**Two timestamps, because the difference is real.** Each interval carries
`started_at` — the last moment there was positive evidence of the link — and
`detected_at`, when the agent noticed. The server may have gone at either end
of the seconds between them, so both are recorded and neither is averaged
into the other.

**Unknown time leaves the denominator.** Availability is run time ÷
*observed* time, where observed time is the window less the seconds this
machine was disconnected inside it. Every OEE answer carries
`unknown_seconds` and `unknown_share` beside the figure, so 92 % availability
over forty observed minutes of an eight-hour window cannot be read as though
it covered the shift. The downtime pareto states the unknown share of its
window for the same reason the unlabelled bucket exists.

**A disconnection is never a downtime reason.** It is not a bucket in the
pareto, it does not book, it does not label a stop, and it never reaches an
ERP confirmation as a stop.

## How it is detected

Positively. The agent asks the server whether the session is still alive every
`opc_health_periods` publish intervals — three by default, so about a second
and a half on a real line, and never faster than once a second. Losing the
client session is the other trigger.

**Silence from a tag is not a trigger**, and this is worth being clear about
because it is the obvious thing to do and it is wrong. OPC UA notifies on
*change*. A machine standing idle correctly sends nothing for an hour.
Inferring an outage from that invents one, which is the same fault as missing
a real one, in the other direction. When a plant's server publishes a real
heartbeat tag, per-equipment staleness becomes a positive signal and this MES
should use it; not before.

## What you will see

| Where | What it says |
|---|---|
| Floor tiles, line view | the machine's tile in the grey the product uses for unknown, with a dashed edge, and a strip saying `no connection since 09:12 — the OPC server did not answer` |
| Machine page | the same, in place of the state pill and its "since" line |
| State timeline | the outage drawn as its own interval, rather than as white space — a hole in a Gantt reads as *nothing happened*, and this one means *nobody was looking* |
| `GET /equipment/connections` | every machine, its connection, since when and why, with the total stated |
| `GET /health` | a `watching` block: machines, connected, disconnected, and how many have no connection fact at all |
| Fleet console | a **Watching** column, so a plant that is up and blind to nine machines cannot hide behind the light that says it is up |
| The namespace | an `equipment_connection_change` event, so a subscriber knows when the last state it heard stopped meaning anything |

`unknown` in that list is not a failure to answer. It means nothing has ever
reported a connection for that machine — a machine fed by hand, or over MQTT,
or one whose agent has never run. Calling it *connected* would be a claim
nobody made.

## What this does not solve

**An agent that is killed outright writes nothing.** `kill -9`, a power cut
on the box the agent runs on, a container evicted: no disconnection is
recorded, so those machines' state intervals stay open and their last state
stands until something closes it. That is the old behaviour, still present in
that one case.

Closing it needs a heartbeat the API can read and time out on its own — the
agent saying "I am still here" often enough that the API can decide it is
not. That is a bigger change than this one and it is not made. Until it is,
the practical answer is the one every plant already uses: watch the agent
process, and alert on `/health` saying the plant can see fewer machines than
it has.

**A server that answers but says nothing** is not detected either. The
session is alive, so the watchdog is satisfied; only a heartbeat tag or a
publish-rate expectation would catch it, and both belong with the change
above.

## See also

- [Decision 0030 — a lost connection is a dimension of its own](../decisions/0030-a-lost-connection-is-unknown-time.md)
- [Read-only OPC UA commissioning](opc-readonly.md)
- [How to read OEE](../plant/reading-oee.md)
- [Experiments in the lab](../develop/experiments.md) — `labs/experiments/lost-connection.toml` scripts an outage and measures what was said about it
