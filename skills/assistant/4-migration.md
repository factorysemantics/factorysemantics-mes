# Stage 4 — What they have, what is not built, and what to tell us

**This page is not a plan. It is the honest list.** Read it before you answer
any question about switching an old MES off, and give them its words rather
than your own.

If they ask *"the scorecard matches — can I turn the old system off now?"* the
answer today is **no**, and here is why, in four parts.

## What they have when the scorecard matches

- A second, independent record of what their line produced, built from the
  line's own signals rather than from the incumbent's bookings — and a
  document that says where the two agree and where they do not, per order and
  per operation, with both numbers side by side.
- OEE, downtime and quality figures that state their own coverage: the part of
  the window actually watched, so an unknown is an unknown rather than a zero.
- Orders arriving as files and confirmations leaving as files, which means
  their ERP was never connected to anything and nothing in it was changed.
- A plant pack describing their line, in config rather than code, that other
  people can read.
- Proof that the shape of their plant can be described without any of their
  data leaving the building.

That is worth having on its own. It is a measurement system. It is not yet a
replacement for the system that runs their plant.

## What is built around it

- **File adapters both ways.** Orders in as B2MML-lite `ProductionSchedule`
  XML or JSON; confirmations out as `ProductionPerformance` XML, one per
  confirmation, idempotent on a message key.
- **An ERPNext / Frappe connector** for orders in and confirmations back,
  when an ERP is in charge.
- **Typed facts in by file or by read-only SQL poll** — downtime labels,
  quality results, counts people enter somewhere else.
- **The recommendation queue** (`adjustments`) — the only path from this MES
  towards a PLC, and it is a queue a human approves. Nothing in it is
  automatic, and an agent can neither approve nor reject.
- **Shadow mode itself**, as five separately-checked closed paths rather than
  a promise.

## What is not built

Say these plainly. Each is real work with no date on it.

- **A cutover procedure.** There is no documented, tested sequence for running
  the old system down and this one up. Nobody has done it.
- **History migration.** Nothing moves years of production history, downtime
  records, or genealogy out of their existing MES into this one. The database
  schema here is not stable enough to receive it, and there is no importer.
- **Write-back to a PLC beyond the queue.** This MES does not set a setpoint,
  download a recipe, or command a machine. The recommendation queue stops at a
  person.
- **Confirmations back to an ERP that is in charge of the plant**, as a
  system of record rather than as files a person compares. The connector
  exists; being trusted with it is a different thing.
- **Anything proven in production.** This is pre-alpha software that has never
  run a real plant. Shadow mode has not been run on a real plant either: the
  page that scripts stage 3 is written and untried.
- **Upgrades across a schema change** on a pip install. The project cannot
  migrate a database for you yet. It is written down rather than papered over.
- **Scheduling, labour, maintenance and warehouse modules** of the depth a
  plant replacing an incumbent would expect. Check the project's `ROADMAP.md`
  for what is open rather than taking a number from me.

If they need any of those, they need them from the project, with their name on
the request. Which is the next section.

## What to tell us, when the scorecard has matched for a month

Three questions. They are the ones the project cannot answer without a real
plant, and a plant that has run a month of shadow mode is the only place the
answers exist. **No data is needed to answer any of them.**

1. **Where did the two records disagree, and which was right?** Not the
   numbers — the *kind* of disagreement, and what caused it. "Changeovers were
   counted as downtime on one side" is the answer that changes the product.
2. **What would have to be true before you would let this book production as
   the system of record?** In their own words, as conditions. Not features —
   conditions.
3. **What does the old system do that this one has no answer for at all?** The
   `[[also_tracked]]` list from their `plant-shape.toml` is a start; a month of
   use will have added to it.

Send them to the project's **Discussions** on
`github.com/factorysemantics/factorysemantics-mes`. Tell them twice: **do not
put real plant data in an issue or a discussion** — no order numbers, no
customers, no exports. There is one maintainer, answering in days rather than
hours, and that is worth saying so nobody feels ignored.

## How to answer the question they will actually ask

> *"So what do I do with it?"*

Run it beside the old system for as long as it is telling you something you
did not know. Use the scorecard as the argument in the room — it is a document
that says where two systems disagree, which is the hardest thing to get in a
plant and the thing that gets a project funded. Treat migration as a separate
decision that this project cannot yet support, and tell us what you learned.

That is the whole honest answer. It stops here.
