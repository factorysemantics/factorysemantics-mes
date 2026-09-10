# 0023 — The fleet console observes and never pushes, and silence is unknown

- **Status:** proposed
- **Date:** 2026-09-10
- **Deciders:** @kalwei

## Context
[M8](../design/m8-packs-and-fleet.md)'s scope line asks to "adopt the
observe-don't-push control plane pattern (enrol, telemetry,
desired-state-as-intent, drift display) for multi-plant visibility", and the
milestone is done when "the console shows both" packs. `README.md`'s
provenance table records that the fleet patterns come from
factorysemantics.com as **patterns only** — re-implemented here from first
principles, with that code staying where it is. The roadmap also says, under
what is deliberately out of scope, that there is **no cloud SaaS in v1** and
that the fleet console is on-prem too.

What exists at `2474a8b`, held against those four words:

- **Enrol** — nothing. There is no machine credential of any kind: no API
  key, no device identity, no shared secret. Authentication is a person or
  the `AGENT` account, with a password, per plant
  (`src/fsmes/services/auth.py`).
- **Telemetry** — pull only, and quite a lot of it. `/health` and `/shadow`
  are public; `/shadow` reports the full outbound register — 32 paths, 16
  allowed, 11 refused, 5 restricted — and the ERP and UNS modes in force.
  `/metrics` is Prometheus text. `/ops/services` reports process liveness.
  OEE per machine and the loss breakdown are JSON, with `null` where a
  component cannot be computed honestly. **Nothing in the product pushes
  status anywhere:** no heartbeat, no webhook, no phone-home.
- **Desired state as intent** — nothing, except `fsmes db-status`, which
  compares the schema against its head and exits non-zero when behind. There
  is no config checksum and no doctor command.
- **Drift display** — nothing, and it cannot exist before a pack does
  ([0022](0022-what-a-plant-pack-may-contain.md)), because drift is measured
  against a pack.

One thing already in the product is the console in miniature.
`list_plants()` in `src/fsmes/mcp_server.py` reads the registry, calls each
plant's `/health`, and returns `shadow: None` for a plant that did not
answer — with a comment saying why: *"None means the plant did not answer,
which is not the same as not shadow."*

And one thing is not true yet: a plant cannot say which plant it is to a
reader outside itself. That is decision
[0021](0021-one-database-per-plant.md), and this one depends on it.

## Options considered
| Option | For | Against |
|---|---|---|
| **Read-only console: polls a list of plants, shows what each says, links to each plant's own dashboard, writes nothing anywhere** | the blast radius is a stale page; it can be reviewed by reading it for calls that write; it is buildable on endpoints that already exist; it is honest about a plant that did not answer | somebody has to change a plant by going to that plant; no "upgrade all"; no remote restart |
| Console that can push configuration to plants | one place to manage a fleet; the obvious product | it is a remote-execution path into every plant a company runs, built by one maintainer, in a pre-alpha MES, for people whose worst day involves a machine moving. A console that can push can push to the wrong plant |
| Plants push telemetry to the console | works through one-way firewalls; no inbound port on the plant | it inverts the trust: the plant now needs an outbound credential and a queue, and a plant that stops sending is indistinguishable from one that is fine and quiet. It is also the first step of a cloud SaaS the roadmap ruled out |
| No console; use Prometheus and Grafana | `/metrics` already exists; it is what plants already run | it answers "is it up" and not "is this plant running the pack it should be", which is the actual M8 question. It also puts the fleet view outside the product, where the honesty rules do not reach |
| Aggregate the fleet into single numbers — one OEE, one availability | what an executive asks for | a fleet OEE is a lie unless every plant is the same shape, and no two plants are. It is [rule 1](0004-never-invent-production.md) at company scale |

## Decision
The fleet console is a **read-only page**. One process, no database of its own
beyond a display cache, no credential that can change anything, and no code
path that writes to a plant.

Per plant it shows: name and label; reachable or not, and when it last
answered; shadow or live; MES version; schema revision against head; pack
version against head; whether the ERP outbox holds anything dead; whether the
UNS backlog is draining; and line OEE for the current shift. A row links to
that plant's own dashboard; the console does not re-implement a screen the
product already has.

Four rules bind it:

1. **It never writes to a plant.** Every M8 verb is a read or a comparison.
   Changing a plant means going to that plant.
2. **Silence is `unknown`.** A plant that did not answer is never rendered as
   healthy and never as down, exactly as `list_plants()` already behaves. A
   console that renders silence as green is worse than no console.
3. **It states its total.** "12 plants, 11 answered, 1 unknown". The number
   configured is never the number seen.
4. **It aggregates nothing that would be a lie.** Twelve OEE numbers, not
   one. This is the discipline the [shadow
   scorecard](../operate/shadow-scorecard.md) already keeps when it refuses to
   add operations into an order total.

**"Enrol" means a person adds a plant to the console's list and gives it a
credential the plant already understands** — not a handshake, not a
certificate authority, not a bootstrap token. What the product owes first is a
**read-only machine credential**: a role whose capabilities are health, shadow,
metrics, service liveness and the OEE reads, and nothing else, so a console
runs as neither a person nor `AGENT`.
`src/fsmes/services/capabilities.py` already expresses roles and capabilities
this way.

Telemetry stays **pull**. The plant opens no outbound connection for the
console's sake, and nothing about a plant leaves it because a console exists.

## Consequences
Easier: the console can be reviewed for safety by reading it, and the shadow
ratchet in `tests/test_shadow_mode.py` will notice if it grows an outbound
call that `fsmes.shadow.REGISTER` does not name. It can be built on endpoints
that already ship. It is useful the day the second plant exists and costs
nothing before that.

Harder: there is no "upgrade the fleet" button, and there will not be one.
Twelve plants behind twelve firewalls need twelve reachable addresses and
twelve credentials, which is real work for whoever runs them, and it is work
this decision deliberately does not automate away. A plant on a network the
console cannot reach simply reads as unknown, forever, and the console must
say that plainly rather than dropping it from the list.

Not decided here: whether the console ships inside this package as
`fsmes console`, or as a separate small dist. It should ship inside until
somebody has a reason it should not.

To revisit when a real fleet exists. Today there is one simulated pair of
plants on one laptop and no real plant at all, so every claim above is
designed against a lab — which is an argument for building the console last
of M8's four pieces, not first.

## House rules touched
Rule 2, unknown is a valid answer and zero is not, is rules 2 and 3 of the
decision. A plant that did not answer is unknown; the count of plants seen is
stated against the count configured.

Rule 1, never invent production: rule 4 is that rule applied across sites. A
single fleet number invents a comparability that does not exist.

Rule 3, unlabelled data is reported as unlabelled: a plant whose pack version
the console cannot read shows as unknown pack, not as up to date.
