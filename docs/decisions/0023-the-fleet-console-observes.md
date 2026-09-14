# 0023 — The console manages only the plants it owns, observes every other plant, and silence is unknown

- **Status:** proposed
- **Date:** 2026-09-10, revised 2026-09-13
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

### What is also true: there are two kinds of plant, and the first draft only served one

The first version of this record, written 2026-09-10, proposed a **purely
read-only console**. The maintainer read it on 2026-09-13 and said, in his
own words, that read-only is not the safety property he needs:

> I don't think pure read-only serves that goal. If I have to go manage each
> simulated plant separately, the console isn't helping with the actual job —
> building and running a fleet of test plants.
>
> I don't want to throw out the safety reasoning — it's right for a real
> customer's plant, where a push is a remote-execution path into someone
> else's machinery. But my plants right now are mine, simulated, and local.
> Can the design split those two cases: the console stays observe-only by
> default for anything it doesn't already control, but for plants I
> explicitly own and manage locally (my own lab fleet), give it — or a
> clearly separate tool — the ability to create, start/stop, and apply a
> pack. Read-only isn't the safety property I actually need here; "can't
> touch a plant it doesn't own" is.

That is a correction with facts behind it. The near-term use of this software
is a lab fleet of simulated plants on one machine, built and torn down
repeatedly to prove that two packs with different modules enabled run from
one codebase. Every one of those plants is created by the same tooling, runs
under the same user, and belongs to the person running it. Making that person
visit each plant to start it is not safety; it is friction with no threat
model behind it.

The threat model that *is* real is the other case, unchanged: a console that
can push to a plant it did not create is a remote-execution path into
machinery, built by one maintainer, in a pre-alpha MES, for people whose
worst day involves a machine moving.

So the line is not read versus write. It is **owned versus not owned**.

## Options considered
| Option | For | Against |
|---|---|---|
| **Pure observe-only console** — the 2026-09-10 proposal: polls a list of plants, shows what each says, links to each plant's own dashboard, writes nothing anywhere | the blast radius is a stale page; it can be reviewed by reading it for any call that writes, which is the strongest reviewable property available; no credential in it can change anything | it solves half the job. The lab fleet still has to be created, started, stopped and re-packed plant by plant, by hand, which is the actual work M8 exists to make possible. **This is why it lost:** it was read-only against plants the tool itself had just created, which is a rule protecting nobody from anything |
| Console that can push configuration to any plant it can reach | one place to manage a fleet; the obvious product | it is a remote-execution path into every plant a company runs. A console that can push to anything can push to the wrong thing, and "the wrong thing" is somebody else's factory. Rejected, and the ownership rule below exists to keep it rejected |
| **A separate management CLI, with no console integration at all** — `fsmes fleet` does the managing, the console never calls it and never knows it exists | the cleanest wall there is: the page genuinely has no write path, so the old review property survives untouched; management is a person at a terminal, which is where a lab fleet is managed anyway | two tools that both hold a list of plants and disagree about it; the console can show drift and cannot say what to do about it; and nothing stops the console growing a button later with no ownership rule written down to stop it |
| **The ownership split (recommended)** — one rule, *the console may act only on plants it owns; for every other plant it observes and cannot push* — with the write verbs living in `fsmes fleet` and the console read-only in its first version | "cannot touch a plant it does not own" is the property a real customer needs, and it is the property that also gives the lab fleet its other half. One definition of ownership, checked in one place, reviewable. It does not need the rule to be relaxed later, because the lab case is inside it from the start | it is a weaker reviewable property than "no call writes": a reviewer must now check that every write path checks ownership first, rather than that no write path exists. That is a real cost and it is paid knowingly |
| Plants push telemetry to the console | works through one-way firewalls; no inbound port on the plant | it inverts the trust: the plant now needs an outbound credential and a queue, and a plant that stops sending is indistinguishable from one that is fine and quiet. It is also the first step of a cloud SaaS the roadmap ruled out |
| No console; use Prometheus and Grafana | `/metrics` already exists; it is what plants already run | it answers "is it up" and not "is this plant running the pack it should be", which is the actual M8 question. It also puts the fleet view outside the product, where the honesty rules do not reach |
| Aggregate the fleet into single numbers — one OEE, one availability | what an executive asks for | a fleet OEE is a lie unless every plant is the same shape, and no two plants are. It is [rule 1](0004-never-invent-production.md) at company scale |

## Decision
**The console may act only on plants it owns. For every other plant it
observes, and no path in it can push.** Ownership is not a mood and not a
configuration flag a hurried person sets; it is defined below in a way a test
can check, and the check is one function that every write path calls first.

### What "owns" means

A plant is owned by a fleet tool when **all three** of these hold. Any one
missing and the plant is observed only.

1. **This tool created it, and wrote that down.** When `fsmes fleet create`
   builds a plant from a pack it appends a line to its own **ownership
   file** — `ownership.toml`, in the fleet's `data_dir` (`environment()` and
   `data_dir()`, `src/fsmes/plant.py`). The line records the plant name, the
   pack and pack fingerprint, the host and OS user it was created under, the
   time, and an **`instance_id`**: a random identifier the tool generates and
   also writes into the plant's own data directory, where the plant reads it
   at start-up.

   The name is `ownership.toml` and not `fleet.toml`, which is what this
   record said while [0022](0022-what-a-plant-pack-may-contain.md) was still
   being built. `fleet.toml` is the **registry** now — the list of packs a
   machine runs — and the two files answer different questions: one says
   what this machine runs, the other says what this installation may
   manage. Two files of one name, in two directories, is a trap for whoever
   reads the next traceback, and a name is cheap.
2. **The plant does not contradict it.** The plant's `/health` returns its
   `plant` name (decision [0021](0021-one-database-per-plant.md), piece 1)
   and that same `instance_id`. A plant that answers with a different id, or
   with no id, **is not owned**, whatever the ownership file says. This is
   the condition that makes ownership checkable from outside the file that
   claims it, and it is what makes ownership *revocable by the plant*: delete
   the id from the plant's data directory and the fleet tool can no longer
   prove condition 2, so the plant falls back to observed.

   **A plant that is not running is silent, and silence is not a
   contradiction.** The first draft of this record said a silent plant is
   not owned for as long as it is silent. Read strictly that makes **start**
   impossible — a stopped plant answers nothing, and starting one is a verb
   on this list — so the condition is *the plant must not contradict us*,
   which resolves three ways:

   | The plant is | What the tool may do |
   |---|---|
   | answering | it must return this name and this id, or every verb refuses |
   | silent, and local | the id this tool wrote into the plant's own data directory must still be there and still match — it is the other half of condition 2, and it is readable while the plant is down. Only `start` and `apply`, the two verbs a stopped plant can take, may proceed on it |
   | silent, and remote | refused. There is no data directory to read on another host, so nothing corroborates anything |

   The thing the strict reading was protecting stays protected: **no verb
   reaches into a *running* plant that has not just said who it is.** What it
   was also forbidding — building a plant and then starting it — was not a
   danger, and a rule that forbids the tool's own first two steps is a rule
   that would be worked around rather than kept.
3. **The operator gave it a path to act on.** Either **local** — same host,
   same OS user, and the plant's process ids are the ones in this registry's
   own pid file (`pid_file`/`running_pids`, `src/fsmes/plant.py`) — or
   **remote** — the ownership file names an explicit credential for that
   host, put there by a person. A credential is never discovered, never
   defaulted, and never reused from another plant's entry.

Ownership is **per plant**, and it is never inferred from reachability.
Being able to reach a plant is not owning it. Knowing its name is not owning
it. Having its password is not owning it. `fsmes fleet disown <name>` deletes
the line; the plant keeps running and becomes an observed plant like any
other.

### What ownership does not prove, said plainly

The `instance_id` is a **continuity check, not an authentication**. It proves
the plant answering on that port is the plant this tool created and has not
been swapped for a different one — the mistake case, which is the common
case. It does not stop a person copying an id into an ownership file on
purpose. What stops a stranger is unchanged and is the plant's own business:
its accounts, its capability roles (`src/fsmes/services/capabilities.py`),
its shadow mode.

On the remote branch of condition 3, the whole weight rests on a credential a
person typed. The product supplies no enrolment handshake, no certificate
authority and no bootstrap token, and this decision adds none.

### What "manage" means for an owned plant

Five verbs, and this list is closed:

- **create** it from a pack,
- **start** it,
- **stop** it,
- **apply** a pack — `fsmes pack check` runs first and a failing check refuses
  the apply, with the one sentence naming the problem that
  [0022](0022-what-a-plant-pack-may-contain.md) requires; applying includes
  bringing that plant's database to head, in the order the design already
  fixes (pack first, then database), and both refuse a plant that is still
  answering on its port,
- **show** its status and its drift against its pack.

### What it never means, even for an owned plant

Nothing that reaches through a plant into what the plant is *doing*:

- no writing a tag to a PLC, or any other call into machinery,
- no sending a confirmation, an order or anything else to an ERP,
- no creating, closing or changing an order; no booking production; no
  scrap, no holds, no dispositions,
- no editing master data, accounts, people or the audit trail.

Those stay the plant's own business, behind its own shadow mode
([0019](0019-count-everything-the-machine-counted.md) and the outbound
register), its own capability roles, and its own approvals. **The console
manages plants, not production.** A plant it owns can be stopped; a plant it
owns cannot be made to say it built something.

### Where the power lives

The write verbs live in **`fsmes fleet`, a local command**, not in the web
page. The console is a page that reads.

- A console is a long-running process on a port. Whatever it can do, whoever
  can reach that port can do. Ownership answers *which plants*, not *which
  people*; putting the verbs in the page widens the blast radius from one
  terminal to a network.
- The verbs already exist in command shape. `fsmes plant <name>
  init|start|stop|migrate` is what manages the lab fleet today.
  `fsmes fleet` is that command with an ownership gate and a pack in front of
  it — not a new subsystem.
- One write path in one module can be reviewed in one sitting, which is what
  makes piece 4's *done when* checkable at all.
- **The credentials differ, and this is what keeps the read-only argument
  rather than deleting it.** Managing runs as the person at the terminal,
  with local process control or that plant's own credential, for as long as
  the command takes and no longer. **No long-running process holds a
  credential that can change a plant.**
- **The console's own credential is none at all**, which is the strongest
  form of that same sentence. The first draft reached for a *read-only
  machine role* — an account that could call health, shadow, metrics,
  service liveness and the OEE reads and nothing else — so that the page
  would not have to run as a person or as `AGENT`. Everything the page
  actually shows turns out to need no account: `/health` and `/shadow` were
  already public, and `/pack` — which plant pack this plant runs, whether it
  has drifted, its schema revision, the modules it serves — is published on
  the same terms, because it is a statement about how a deployment is
  configured rather than a number a plant produced. A console that holds no
  credential cannot leak one, cannot be persuaded to spend one, and needs no
  account provisioned on twelve plants before it can show a table.

  The read-only machine role is therefore **not rejected, it is not yet
  needed**, and the day it is needed is visible from here: the first column
  a console adds that a plant will not tell a stranger — OEE, the loss
  breakdown, `/ops/services` — is the day this product grows that role. A
  role nothing uses is an account nobody rotates.

The console **may** grow buttons later — started with an explicit
`--manage`, off by default, bound to loopback, each button calling the same
gated function the command calls, never a second implementation. That is
deferred: the console's first version is exactly the read-only page §8
describes, because the milestone's *done when* needs the page and does not
need the buttons.

### Nothing starts a plant on its own

`fsmes fleet` acts when a person runs it. There is no scheduler, no
reconciliation loop, and no daemon that notices a stopped plant and brings it
back. "Desired state as intent" stays **intent displayed as drift**, never
intent enforced — a reconciler is a program that starts machinery because a
file said so, and this product does not have one. On a development machine
this is also the standing rule: the lab fleet runs when its owner starts it,
and no tooling here starts plants by itself.

### The four rules, unchanged

1. **It never acts on a plant it does not own.** For an unowned plant every
   M8 verb is a read or a comparison, and there is no flag that changes this.
2. **Silence is `unknown`.** A plant that did not answer is never rendered as
   healthy and never as down, exactly as `list_plants()` already behaves. A
   console that renders silence as green is worse than no console. On the
   page a silent plant's ownership is `unknown` too — it is shown as recorded
   here and not answering, never as owned — because nothing it did not say
   can corroborate anything. What the command may do with a silent plant is
   condition 2's table above: it may start the plant it can still prove it
   created, and it may reach into no running plant that has not just said who
   it is.
3. **It states its total.** "12 plants, 11 answered, 1 unknown; 3 owned".
   The number configured is never the number seen.
4. **It aggregates nothing that would be a lie.** Twelve OEE numbers, not
   one. This is the discipline the [shadow
   scorecard](../operate/shadow-scorecard.md) already keeps when it refuses
   to add operations into an order total.

**"Enrol" means a person adds a plant to the console's list and gives it a
credential the plant already understands** — not a handshake, not a
certificate authority, not a bootstrap token. Enrolling a plant makes it
*visible*. It does not make it owned: ownership comes from creating it, or
not at all.

Telemetry stays **pull**. The plant opens no outbound connection for the
console's sake, and nothing about a plant leaves it because a console exists.

## Consequences
Easier: the lab fleet is one tool. Create three plants from three packs,
start them, apply a new pack version to all three, see which one drifted —
which is the job M8 exists to make possible, and which the first draft of
this record left undone.

Easier, still: an unowned plant is safe by construction rather than by
politeness. The rule survives the console growing features, because the gate
is in front of the verbs and not in the page.

Harder, and this is the price: **the review question changed.** It used to be
"is there any call in this code that writes to a plant", answerable by
reading for calls. It is now "does every call that writes check ownership
first", which needs a reviewer to follow each path to its gate. Piece 4's
*done when* is written to force exactly that reading, and a test must hold
it: a fake plant that answers `/health` with the wrong `instance_id` must
make every one of the five verbs refuse.

Harder: there is still no "upgrade the fleet" button for plants somebody else
runs, and there will not be one. Twelve plants behind twelve firewalls need
twelve reachable addresses and twelve credentials, and that stays real work
for whoever runs them.

Harder: ownership is a file, and files get copied. A plant directory cloned
to make a second plant carries the first plant's `instance_id` until
something regenerates it, and two plants claiming one id must be an error
that refuses, not a coin toss. That cannot be `fsmes pack check`'s job after
all — a pack carries no instance id, by [0022](0022-what-a-plant-pack-may-contain.md),
so a check that reads only a pack directory can never see one. It is caught
where the ids actually live: the fleet tool refuses to read an ownership file
in which two entries claim one id, and says which two.

To revisit when a real fleet exists, and specifically the first time somebody
other than the maintainer runs a plant this tooling created — that is when
the remote branch of condition 3 stops being theory. Today there is a lab
fleet of simulated plants on one machine and no real plant at all.

## House rules touched
Rule 2, unknown is a valid answer and zero is not, is rules 2 and 3 above. A
plant that did not answer is unknown — and, because ownership needs the
plant's own corroboration, an unknown plant is also unmanageable rather than
managed on faith.

Rule 1, never invent production: rule 4 is that rule applied across sites. A
single fleet number invents a comparability that does not exist. The closed
list of five verbs is the same rule pointed inwards — a fleet tool that could
book production would be inventing it at the worst possible distance from the
machine.

Rule 3, unlabelled data is reported as unlabelled: a plant whose pack version
the console cannot read shows as unknown pack, not as up to date. A plant
whose `instance_id` the console cannot read shows as not owned, not as
probably fine.

Config, not code, at plant boundaries: ownership lives in `ownership.toml`
and in the plant's own data directory. No plant name, host or credential belongs in
`src/`, and the `no_tenant_literals` guard test of M8 is what keeps it out.
