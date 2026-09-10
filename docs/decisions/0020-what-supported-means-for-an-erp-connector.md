# 0020 — What "supported" means for an ERP connector, and what it is called before that

- **Status:** accepted
- **Date:** 2026-09-10
- **Deciders:** @kalwei

## Context
The ERP port has been three typed methods — `fetch_orders`, `acknowledge`,
`send_confirmation` — over the models in `contract.py`, with a connector
plugging in as a `fsmes.modules` entry point. A connector published on its
own works exactly like the one that ships. That much has been true since
decision [0008](0008-erp-connectors-are-modules.md), and none of it changes here.

Then the ERPNext connector met a real ERPNext, and three days produced three
facts.

**2026-09-09, morning.** The connector needed five custom fields on ERPNext's
Work Order doctype. `docs/operate/erpnext.md` said nothing was needed on the
ERPNext side. The only thing that had ever created those fields was a demo
seeding script in `labs/`, which is not in the wheel — so nobody who
installed the package could create them at all. Fixed in PR #16.

**2026-09-09, evening.** PR #17 ran a real ERPNext v15.120.0 in CI and
measured what Frappe does with a write naming a field the doctype does not
have. It answers **`200`**, returns a normal document, and the value is
neither stored nor returned. The run log prints it:
`MISSING FIELD PROBE: refused=None value_after=None`. PR #16 had reasoned
exactly that from Frappe's source and had to write it down as unverified,
because nothing may start ERPNext on the maintainer's machine. It reasoned
correctly. That is not a method to rely on twice.

**Both days.** `fsmes erp check` was written, and it knew about ERPNext
specifically. `fsmes erp setup` refused every `MES_ERP_MODE` but `erpnext`.

Behind all of it is a question Scott asked on 2026-09-09: can today's
lessons be turned into public documents, so that SAP and Oracle go smoothly
and are close to plug and play?

What the three methods never said is: what has to exist on the far side,
how a write is proved to have landed, and how a person checks it before
trusting it. Each of those was fixed once, inside the ERPNext adapter. The
next connector would have rediscovered all three, at somebody's plant.

As of 2026-09-10 no ERP connector but ERPNext exists. This decision is made
before there is a queue of them, on purpose.

## Options considered
| Option | For | Against |
|---|---|---|
| **Three obligations on the port, a conformance suite in the package, and three words for how far a connector has got** | the bar is checkable rather than claimed; a contributor finds out in their own CI, not in a plant; "not tested" stays sayable out loud | more surface on the port; three words to keep honest as connectors arrive |
| Document it and trust contributors — what we had on the morning of 2026-09-09 | nothing to build; contributors are not treated as suspects | it is exactly what produced a documented connector that could not work, and the documentation was confidently wrong for three days. Prose cannot fail a build |
| One word, `supported`, granted by the maintainer's judgement | simple; no tiers to explain | it makes one person the bottleneck for every connector, and it gives a reader no way to tell a connector nobody has run from one that runs nightly against a pinned version |
| Only connectors maintained in this repository are listed at all | everything listed is genuinely tested | it is the opposite of the wedge. A person with a real SAP is the only one who can prove an SAP connector, and this would give them nowhere to put it |
| Call a connector supported when its unit tests pass | cheap, automatic | a fake that lies in the same direction the connector does passes every unit test. That is the `200`-and-drop failure exactly |

## Decision
The ERP port carries three more methods, each with a default so that a
transport needing nothing on the far side, and a connector written against
the older three-method port, both keep working:

- **`requirements()`** — what must exist on the ERP side, as data a person
  can act on: what it is called, where it lives, what it is, why the MES
  cannot work without it, and whether the MES can create it.
- **`setup()`** — create or verify those requirements, idempotently, naming
  each one and what happened to it.
- **`check()`** — connectivity, credentials and requirements, in plain
  language, with `unknown` as a first-class verdict, exiting non-zero when
  something is wrong.

`fsmes erp requirements`, `fsmes erp setup` and `fsmes erp check` are those
three methods and nothing else. They act on whatever connector
`MES_ERP_MODE` names and no longer know that ERPNext exists.

`fsmes.integrations.erp.conformance` ships **inside the package** — not in
`tests/` — so a connector published on its own is held to the same eight
obligations without vendoring this project's test tree.

And three words, which `docs/operate/compatibility.md` uses and nothing
else may:

- **supported** — implements the port; passes the conformance suite; is
  tested against a **stated version** of a real system by a job somebody
  maintains; has a page saying what it does *not* cover; has a
  compatibility row carrying the version tested and the date.
- **contributed** — implements the port and passes the conformance suite.
  Nobody here has run it against a real system. Its row says so.
- **experimental** — exists. Its row says what is missing.

A connector with no live test is a connector nobody has run, and its row
says that in those words. Today exactly one connector is supported:
**ERPNext v15.120.0**, as of 2026-09-09. Odoo, SAP, Oracle and NetSuite are
not written — the contract exists, the connectors do not — and that stays
true until somebody with one of those systems writes and proves one.

`docs/develop/erp-connectors.md` is this decision as instructions.

## Consequences
Easier: a connector author finds out in their own CI that their `check`
cannot fail, or that their adapter returns dicts, or that their write is
never read back. All three were found here the expensive way. A reader of
the compatibility table can tell a connector that runs against a pinned
version every week from one that has only ever met a fake.

Harder: the port is six methods, not three, and every connector that ships
must implement them or inherit the defaults deliberately. `check()` must
have no side effects, which means some things it would like to prove it can
only report as `unknown` — and a `Ready.` with an unknown in it has to say
so rather than reading as a guarantee.

Not decided here: whether a connector may be listed as supported when the
live job runs on somebody else's infrastructure rather than this
repository's CI. Nobody has offered one yet. When they do, the answer goes
in this file's successor, with what they offered.

To revisit when the second connector arrives. Three words fitted one
connector and a guess; the guess is the part to check.

## House rules touched
Rule 2 — unknown is a valid answer, zero is not — twice over. `check()`
reports `unknown` for what it could not prove rather than counting it as
working, and `contributed` exists so that "nobody here has run this" is a
thing the table can say instead of quietly reading as supported.

Rule 1, never invent production, is what obligation 4 protects: a
connector that reports a dropped write as success makes the MES record
production that reached no ERP.

Rule 5, tests are prose: the eight obligations are named after the promise
each one pins, and a failure prints the obligation's name.
