# 0028 — A lost connection is a dimension of its own, and the gap it leaves is unknown time

- **Status:** accepted
- **Date:** 2026-09-14
- **Deciders:** maintainer

## Context

`EquipmentStateName` has four members: `running`, `idle`, `down`, `setup`.
Every one of them is a statement about the machine. None of them is a
statement about whether this MES could still see the machine.

That gap has a concrete cost, found on 2026-09-14 while writing the lab's
third round of experiments. The plan was to script "the OPC server goes away
for four minutes" and ask what the MES said about those four minutes. It
could not be written, because there was nothing for the MES to say. What the
code did instead:

- `integrations/opc/agent.py` caught the dropped connection, logged
  `OPC connection lost, retrying in 3s`, and slept. **No interval was closed
  and no row was written.**
- So the open `equipment_states` row stayed open. A machine that was
  `running` when the cable was pulled reads as running for as long as the
  link is down, and for ever if the agent never comes back.
- `services/equipment.state_seconds` clips an open interval to the end of the
  window, so that invented run time went straight into the availability
  numerator; `services/maintenance.runtime_hours` accrued maintenance hours
  against it at the same time.
- Availability's denominator is the whole window
  (`runtime / window_seconds`), so time nobody watched was already being
  priced as though somebody had.

A real plant loses connections weekly — a switch reboots, a certificate
expires, an engineer unplugs the wrong port. The first thing an incumbent
MES's operators test, when this one is put beside it in shadow mode, is
pulling the cable.

There is already a written answer to the same question one level up.
Decision 0023 says a plant that does not answer the fleet console is not
down, it is *unknown*. This decision is that argument applied to a machine.

## Options considered

| Option | For | Against |
|---|---|---|
| Add `disconnected` to `EquipmentStateName` | One table, one enum, every screen picks it up for free | It is not a production state, and putting it in the same column says it is: a machine can be *down and disconnected*, and a plant that must choose one of those has lost the more useful fact. It would also flow into the downtime pareto, into ERP confirmations and into every consumer that switches on that enum |
| Leave the interval open and mark the *equipment* offline with a flag | Cheap; no new history | A flag has no since-when and no history, so no window can be corrected after the fact, and the invented run time is still in the numerator |
| Infer it from silence — no reading for *N* × the publish interval | Needs nothing from the connection layer | OPC UA publishes on change. A machine standing idle sends nothing for an hour and is perfectly connected. Inferring a lost connection from silence invents an outage, which is the same fault in the other direction |
| **A second interval history for the connection (chosen)** | Two independent facts stay two facts; the gap is recorded with since-when and can be re-read later; the production history simply stops where the evidence stops | A second table, a second thing for every reader to ask about, and the agent must write something on the way down |

## Decision

A machine's connection is recorded separately from its state, in
`equipment_connections`, with the same shape the state history already has:
one row per contiguous stretch, the open row (`ended_at IS NULL`) is now.
Its own two-valued enum is `connected` | `disconnected`. A machine with no
row has **no connection fact at all** — nothing is watching it over a
connection — and that is reported as `unknown`, never as connected.

Four rules follow, and each is pinned by a test.

1. **The production history stops where the evidence stops.** When the agent
   records a disconnection it closes the machine's open `equipment_states`
   interval at the moment of the last evidence and opens nothing in its
   place. For the length of the outage the machine has no open state, so
   every screen and every API already answers `unknown` — the word they
   already use for a machine they have no interval for. Nothing is invented,
   including "it carried on doing what it was doing".

2. **Two timestamps, because the difference is real.** A disconnection
   interval carries `started_at` — the last moment the MES had positive
   evidence of the link — and `detected_at`, when it noticed. The seconds
   between them are genuinely unknown: the server may have gone at either
   end of them. Both are reported rather than averaged into one.

3. **Unknown time leaves the denominator and is stated as a share.**
   Availability is run time ÷ *observed* time, where observed time is the
   window less the seconds this machine was disconnected inside it. Every
   OEE answer carries `unknown_seconds` and `unknown_share` beside the
   figure, so a 92 % availability computed over forty observed minutes of an
   eight-hour window cannot be read as though it covered the shift. This is
   the same clamp `first_seen` already applies at the start of a window,
   applied to holes in the middle of one.

4. **A disconnection is never downtime and never idle.** It does not enter
   the downtime pareto, it does not book, it does not label a stop. The
   pareto states the unknown share of its window instead, for the same
   reason the unlabelled bucket exists: a plant that cannot see how much of
   the window it was blind for will read the pareto as complete.

Detection is positive, not inferred. The agent runs a watchdog that asks the
server whether the session is alive every `opc_health_periods` publish
intervals (three by default, config not code), and the loss of the client
session is the other trigger. Silence from a tag is not a trigger, for the
reason in the options table above.

The connection change is published to the namespace like any other domain
event (`equipment_connection_change`), because a subscriber watching a
machine's state needs to know when the MES stopped being able to see it —
otherwise the last state it received stands for ever.

`/health` gains one block, `watching`, saying how many machines this plant is
connected to, how many are disconnected, how many it has no connection fact
for, and the total. It is a statement about what this deployment can
currently see, not a number the plant produced, which is what keeps it on
the side of the public endpoints that decision 0023 draws.

**Known and not solved here:** an agent that is killed outright writes
nothing, so its machines' intervals stay open and their last state stands
until something else closes it. Closing that needs a heartbeat the API can
read and time out on its own, which is a bigger change than this one and is
written down in `docs/operate/opc-disconnections.md` rather than pretended
away.

## Consequences

**Easier.** The lab can script a lost connection and score what the MES said
about it. A shadow-mode deployment can answer the cable-pull test with a
screen. Availability figures stop quietly including time nobody watched, and
the size of what was not watched is on the same object as the figure.

**Harder.** Every reader of a machine's current state now has a second
question to ask. Inside this repository that is the floor tiles, the machine
page, the line view, the fleet console and four API routers, and they are
handled. An external consumer that switches on `state` alone will see
`unknown` during an outage where it used to see a stale value; that is the
change, and it is the point.

**To revisit.** When a plant runs an OPC server that publishes a heartbeat
tag, per-equipment staleness becomes a positive signal rather than an
inference, and this decision should gain a fifth rule for it. Not before.

## House rules touched

- **1 — never invent production.** A gap nobody watched books nothing and
  states nothing about what the machine was doing.
- **2 — unknown is a valid answer; zero is not.** A disconnected machine has
  no state, not a state of zero, and the window it interrupted says how much
  of itself was unknown.
- **3 — unlabelled data is reported as unlabelled.** The downtime pareto
  states the unknown share of its window rather than presenting itself as
  complete.
- **4 — config, not code, at plant boundaries.** How often the watchdog asks
  is a setting, expressed in the publish intervals the plant already
  configures.
