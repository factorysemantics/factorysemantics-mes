# 0035 — Configuration is authored by the role that owns it, and selected by the operator

- **Status:** accepted (2026-09-18, by the maintainer, after one revision — the *discovered* step)
- **Date:** 2026-09-18
- **Deciders:** maintainer

## Context
The station screen asks a stopped machine's operator why, in a free-text
input whose placeholder reads *"e.g. jam at infeed, waiting on fitter"*
(`src/fsmes/web/station.html:43`). The answer is stored exactly as typed
(`EquipmentState.reason`, `String(120)`, `src/fsmes/domain/equipment.py:40`)
and the downtime pareto groups on that raw string —
`key = state.reason or UNLABELLED` (`src/fsmes/services/analysis.py:583`),
with no strip, no casefold and no alias map anywhere in the path. Four
spellings are four bars; sorting is by seconds, so a reason split four ways
sinks below reasons it outweighs. The chart truncates past fourteen
characters (`src/fsmes/web/analysis.js:179-191`), so two different typos can
render identically. An operator who types the word `unlabelled` moves
`unlabelled_share` — a headline honesty figure — with no error anywhere. The
value is published to the broker on the state-change event
(`src/fsmes/services/outbox.py:104`), so it leaves the MES.

There is no vocabulary table anywhere in this product. Twenty-six free-text
columns hold a reason, code, category or comment; sixteen are typed by a
person; none has an enum, a foreign key, a check constraint or a length
check at the API. `docs/ARCHITECTURE.md:74` has listed a `downtime_reasons`
table since the architecture was written; it has never been built.
`docs/design/backlog/scrap-reason-codes.md` has been open since 2026-09-01
asking whether such a list is master data per plant or an enum in the
product. `docs/ai/JEV.md:403-412` defers the flagship downtime-reason work
behind the same question.

The other half is who may answer it. A `Role` is a row with a JSON array of
capability names (`src/fsmes/domain/masterdata.py:159-183`) and capabilities
are twenty-one strings the API, the agent tools and the screens all gate on
(`src/fsmes/services/capabilities.py:24-46`) — the right shape. But one
capability, `masterdata.write`, gates everything an engineer of any kind
would author: equipment, materials, BOM, routings
(`src/fsmes/api/routers/masterdata.py:64,105,129,175`), quality
specifications (`src/fsmes/api/routers/quality.py:120`) and gauges
(`api/routers/quality.py:407`). Only `admin` holds it, and `admin` holds all
twenty-one (`capabilities.py:58-60`). There is exactly one configuration role in this
MES today, and it is the plant administrator.

The lifecycle this needs already exists three times: work instructions
(`documents.write` / `documents.approve`, revisions that supersede rather
than overwrite, `src/fsmes/domain/documents.py:38-55`), triggers (*"the
draft → approved lifecycle work instructions have, because logic that fires
against a live plant is a controlled thing"*, `src/fsmes/domain/triggers.py:1-9`)
and setpoint adjustments. The `agent` role is deliberately built to hold the
drafting half of each pair and never the approving half
(`capabilities.py:88-97`).

The design page is [configuration assistance](../design/config-assistance.md).

## Options considered
| Option | For | Against |
|---|---|---|
| **Three tiers: role-authored vocabulary, operator selection, fixed — with the domain expressed as a capability prefix, and the draft→approve lifecycle the product already has** | Nothing new to invent: the lifecycle, the audit spine, the capability gate and the protected-term list all exist. An engineer authors in their own domain without being made plant administrator. The operator's screen strictly narrows | Splitting `masterdata.write` touches roles plants may already have built. Four domains are named before anything uses them |
| A fixed enum of reasons in the product | One list, no drift between plants, comparable across a fleet | Two honest plants do not stop for the same reasons. It is house rule 4 (config, not code, at every plant boundary) answered in the wrong direction |
| Vocabulary in the plant pack only | Already validated offline, fingerprinted, reviewable, no schema change | `fsmes pack apply` never updates an entry that exists (`src/fsmes/pack/masterdata.py:36-41`), so a list a plant edits monthly cannot live there alone |
| Leave the text box and clean the data on read — normalise, casefold, fuzzy-merge | No schema change, fixes the existing history too | Guessing that two strings meant one thing is inventing a record nobody made. Principle 4 in reverse |

## Decision
Every setting in this MES belongs to exactly one of three tiers.
**Role-authored vocabulary** is a list this plant owns — its reasons, its
machines, its materials, its shifts, its tolerances — authored by the role
that owns the domain and by nobody else. **Operator selection** is what a
person on the floor chooses at the moment of work: always a choice from a
tier-one list or a measurement against a tier-one tolerance, never a new
word. **Fixed** is what the product owns — equipment states, order statuses,
capability names, role codes, event kinds, KPI names, the four dispositions,
the four SPC rules, and every field an API response is built on. The test is
what breaks if two plants answer differently; the fixed list is not written
out again but read from `protected_terms()`
(`src/fsmes/pack/check.py:101-141`), which builds it by importing the
product's own vocabularies. The rule that follows: **every tier-two field
names its tier-one list, and a tier-two field with no tier-one list behind
it is free text, and free text is the bug.**

A configuration domain is expressed as a capability prefix, not as a new
concept. Roles stay data. `masterdata.write` stops being one word for five
jobs: `process.define`/`process.approve`, `signals.define`/`signals.approve`,
`quality.define`/`quality.approve` and `erp.define` are named now;
`users.manage` is unchanged; where the database lives and which port a plant
serves on stay outside the product's role model, because they are facts about
the machine.

A change to tier-one configuration follows the lifecycle this product
already uses for instructions and triggers: a person holding
`<domain>.define` drafts, or an assistant drafts **for** them with
`on_behalf_of` recorded; the draft is validated for schema, for
cross-references, and by a dry run against the plant's own data that states
what it would and would not cover, with the total; the validated draft is
**discovered** — it appears, counted and with its total, on the screen of
every role that holds the matching `<domain>.approve` capability and on no
screen that cannot act on it, stating how long it has waited; a person
holding `<domain>.approve` signs it off, never the assistant; the approved
revision
supersedes the previous one and the operator's screen changes at the next
load; and undo is approving the previous revision. **Retiring a code changes
what may be chosen next and never what was chosen before** — an interval
labelled with a retired code keeps that label, and the analysis keeps showing
it.

Tier one is seeded by the pack and owned by the database, in the shape
`shifts` already has.

## Consequences
Easier: an engineer configures their own domain without being made
administrator of the whole plant, which is the failure this is aimed at.
Easier: an assistant can be given real configuration work, because the
boundary it must not cross is a list the product computes rather than a
paragraph of prose. Easier: the pareto stops lying.

Harder: `masterdata.write` has to be split, and a plant that has already
built a role on it must be migrated deliberately rather than silently.
Harder: four domains are named before anything uses them, which is a bet
that the names are right — taken because adding a capability is cheap and
redefining one is not.

Harder: discovery is a surface that has to be built, not a free ride on one
that exists. **A lifecycle whose last step is that somebody remembers to look
is the failure this decision is aimed at** — and all three of the lifecycles
this one copies have exactly that gap today. A document draft is visible only
to someone who opens the instructions screen and reads the rows, and its
"draft only" filter means *never approved*, so a pending new revision of a
document already in force is excluded from it
(`src/fsmes/web/instructions.js:114-116`,
`src/fsmes/api/routers/documents.py:71-72`). A trigger draft is a tile on the
triggers screen (`src/fsmes/web/triggers.js:89-92`). A proposed adjustment is
a tile on the adjustments screen counted over the loaded page rather than the
queue (`src/fsmes/web/adjustments.js:108-111`). Nothing tells an approver
anything: there is no inbox, no aggregate count, no approvals endpoint and no
notification channel of any kind in this product, while the drafting
capabilities sit with `supervisor` and `agent` and all three approve
capabilities are held by `admin` alone
(`src/fsmes/services/capabilities.py:55-59,93-94`). So the pilot carries the
discovery half rather than deferring it.

Honest gaps, named rather than half-built: approval here is an account code
and a timestamp. There is no qualification record, no training matrix and no
electronic signature in this product, so "a qualified role signs off" means,
today, "a role an administrator granted the approve capability to". And
configuration changes are not audited at all outside the database —
`fsmes pack apply` writes no audit row; its only record is the `.pack.json`
fingerprint stamp.

To revisit: when the first real plant says which of the six domains it
actually staffs; when a second vocabulary (scrap reasons) is due, which is
the test of whether the loop generalised; and if a regulated plant needs
signature meaning, which is a decision of its own.

## House rules touched
**3 — agent-native, and an agent acts for a person.** The drafting half of
each capability pair is what an assistant may hold; the approving half is
what it may never hold, and the audit row says who it acted for.

**4 — never invent production; unknown is not zero.** No number moves
because of anything here. A drafted vocabulary is a draft, so
`unlabelled_share` reports what it reports today. Existing typed reasons are
kept exactly as typed and never folded into codes automatically, because
guessing what somebody meant is inventing a record. Every list this produces
states its total, including how much of a window came from the vocabulary
and how much did not.

**7 — config, not code, at every plant boundary.** A reason list is the
plant's, not the product's. It is seeded by the pack and owned by the
plant's database, because `pack apply` deliberately never updates what is
already there.
