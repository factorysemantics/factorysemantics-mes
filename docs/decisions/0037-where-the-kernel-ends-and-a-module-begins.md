# 0037 — Where the kernel ends, what a module may do, and whether a module may be sold

- **Status:** proposed
- **Date:** 2026-09-26
- **Deciders:** maintainer

## Context

[0002](0002-kernel-and-modules.md) fixed the kernel as master data, orders,
routing, dispatch, execution, events, audit and auth, and said modules register
through the `fsmes.modules` entry-point group. It was honest about the gap in
the same sentence: *"the module boundary is a discipline, not a wall; the
kernel wheel still ships most modules today"*, and it set its own revisit
trigger — **when a second module moves out**. Nothing has moved out.

What the code actually holds, on 2026-09-26 at `4cf18361`:

- **23 modules in a static registry** (`src/fsmes/modules.py`): nine kernel,
  fourteen switchable by `MES_MODULES`, compiled from a pack's `[modules]`
  table. They declare routers, pages, Configuration sections, agent tool files,
  settings prefixes and tables.
- **One entry point** (`pyproject.toml`, group `fsmes.modules`): `erpnext`. It
  is read in three places and **none of them touches the registry** — it is an
  ERP-adapter factory hook with a general-sounding name. No installed package
  can contribute a router, a screen, a Configuration section, an agent tool, a
  setting or a table.
- **No lifecycle hooks.** `docs/ARCHITECTURE.md` describes
  `order.released`, `operation.completed`, `state.changed` and `lot.created`
  with modules subscribing. No registry, no subscription and no dispatch exist
  in the code. What exists is a transactional outbox with hard-coded consumers.
- **No module versioning of any kind.** No module API version, no kernel range
  a module can declare, no compatibility standing per module. `fsmes info`
  prints the entry-point group and labels it "modules".
- **One Alembic chain**, 39 revisions, one head, no branch labels and no
  `depends_on` anywhere. A module cannot own a migration.
- **`Module.settings` is declared and read by nothing.** The pack schema is
  closed and in-tree — 19 sections, 123 keys — so a module cannot declare a
  settings prefix that `fsmes pack check` would validate.
- **Four Configuration domains, 55 sections** (Engineering 18, Quality 12,
  Supply chain 6, Setup 19), one entry per domain per
  [0035](0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
  and §2a of the configuration-assistance design. There is no Maintenance
  domain and no Floor domain.

Three things made this a decision rather than a backlog item:

1. On **2026-09-26** the maintainer asked for Floor and Maintenance
   Configuration tabs, for a much more configurable UI, for themes where "the
   overall UI can be completely different", for graphs and reports as an
   engineer's everyday surface, and for "versioning and everything included" —
   and said he did not know where the kernel should end.
2. In the same message he raised **a private repository of modules he could
   sell as add-ons, or charge to install**. The project's own planning notes of
   2026-09-05 say, under *what not to do*: **"No 'open core' split of the MES
   itself"**, with any commercial layer placed at factorysemantics.com as
   products that consume the MES's events. **No decision record exists on a
   commercial model at all** — only that strategy note.
3. On the same morning, the floor assistant told him a setting did not exist
   because Setup's 19 sections and 44 keys exceeded a 6,000-character result
   limit. A Configuration workspace is now also a unit of *reading*, which is
   new evidence bearing on how many domains there should be.

The full argument, with file paths and the research behind it, is
[the design page](../design/kernel-and-modules.md).

## Options considered

| Option | For | Against |
|---|---|---|
| **Leave the seam as it is** — discipline, not a wall; revisit when someone external asks | costs nothing; 0002 already sanctions it; the architecture page calls splitting "a later, mechanical step" | every question the maintainer asked on 2026-09-26 needs an answer the seam cannot give: no module versioning, no themes, no report extension point, and no way to say what a paid module would even be |
| **Specify the seam now, and move one existing module out to prove it** (this record) | reaches 0002's own revisit trigger deliberately; the first move can fail with nothing but an unmerged branch; it is the prerequisite for reports, for themes-as-layout, and for either answer to the commercial question | it is real work with no visible feature at the end of it, spent from an evening budget |
| **Build the reports module first and let the seam follow it** | the highest day-one value, and the clearest unclaimed ground in the field | it needs the module API, module-owned migrations and the report contract all at once, and its screens would become load-bearing before the mechanism under them is proven |
| **Declare the kernel finished and ship everything as features** | simplest; matches how the product is actually built today | it forecloses module maintainers — the named 24-month goal is two who are not the maintainer — and makes the bus factor permanent |

## Decision

*Proposed, in two parts. The second part has two alternative resolutions and
the maintainer picks one; nothing here is accepted until he does.*

**Part one — the boundary and the contract.** A thing is kernel if removing it
makes the production record untrue or unreadable; otherwise it is a plant pack
if it is data and a module if it is code, and a thing that changes what an
existing number means is neither and needs its own record. Under that test
`kpis` becomes kernel, `adjustments` is either kernel or switchable-on-purpose
with its reason written down, and `analysis` is the clearest genuine module. A
module declares routers, screens, Configuration sections, agent tools,
settings prefixes, pack keys and its own tables on its own Alembic branch; it
may not import another module's internals, rename the kernel's words, compute
a kernel number, bypass the audit, widen a capability, or fetch anything from
outside the box. It declares the kernel range it needs in the grammar
`[pack] requires` already uses, the kernel declares a `MODULE_API` integer, and
a module that does not fit is refused at start-up with a sentence rather than
disabled quietly. The three words *supported*, *contributed* and *experimental*
apply to modules exactly as
[0020](0020-what-supported-means-for-an-erp-connector.md) defines them for ERP
connectors, with the same rule that every row states the version tested and the
date. A module change that alters what a number means goes in the changelog's
Honesty section like any other. Maintenance becomes a fifth Configuration
domain with a `maintenance.define` capability; the Floor does not become one,
because the floor selects and never authors — its real complaint is answered
by splitting a `screens.define` capability out of `users.manage`. A theme is a
pack artefact, a layout is a module, and there is no UI plug-in point. A report
declares a question and a rendering and receives the kernel's own envelope,
coverage and totals included; a report surface that cannot express *unknown* is
refused at registration. The first work is to move `analysis` out to its own
distribution, changing nothing a plant can see.

**Part two — whether a module may be sold. Two alternatives; one must be
chosen.**

**Resolution A — the kernel stays whole.** The 2026-09-05 position is promoted
from a strategy note to this record: there is no paid tier inside the MES, no
private add-on modules, and no dual licence. Any commercial layer is a separate
product that consumes the MES's events. The entry-point contract stays an
internal seam that hardens when a second module needs it, per 0002's trigger.
Nothing in the community pages changes, and
[0016](0016-no-trademark-registration.md) stands.

**Resolution B — paid private modules are allowed.** A module may be published
from a private repository under any licence, because it links over a published
contract rather than by inclusion. Four things then become obligations rather
than preferences: the entry-point contract becomes a **public API with a
support promise**, so `MODULE_API`, the `requires` range and a per-module
compatibility row must exist before the first paid module ships; 0016 is
reopened, because its own revisit trigger is *"if a company forms around the
project"*; the contributor ladder, the `adopt-a-module` issues and the
integrator programme's no-fee listing are rewritten to say which modules a
stranger may own and which he may not; and the changelog's Honesty rule binds
paid modules identically, because a plant should not have to know which side of
the seam a number came from.

Both resolutions leave the licence of this repository at Apache-2.0 with the
DCO ([0001](0001-apache-2-with-dco.md)), which is what makes either one
possible without a relicence.

## Consequences

**Easier.** A module author can be told what a module is in one page and be
refused at start-up rather than at runtime. `fsmes info` starts telling the
truth. The compatibility page gains rows nobody has to interpret. The
Maintenance tab that was expected exists, and the assistant's read of a
workspace gets small enough to fit in a tool result. Reports, themes-as-layout
and any future community module all arrive through one mechanism instead of
three.

**Harder.** Module-owned Alembic branches are new machinery against a schema
that has had exactly one head since the beginning, and the promise that *off
means not served, never not stored* must survive them. The pack schema must
learn to accept a section it does not ship. Two distributions mean two release
paths and a CI matrix that proves they work together. Under Resolution B the
kernel also loses the freedom to change the `Module` shape in a minor release,
which is a real and permanent cost.

**What must be revisited, and when.** Part one is revisited when `analysis` has
actually shipped as its own distribution — if the move needed a kernel change
this record did not predict, the contract was wrong and says so. Part two is
revisited if a company forms, which is also 0016's trigger, and in any case
before a second paid module exists. The Floor question is revisited when a
real plant says who tunes its screens. **This record replaces nothing in
0002; it is what 0002 asked for when it said "revisit when a second module
moves out".**

## House rules touched

**Rule 4 — config, not code, at plant boundaries.** The whole page is rule 4
read as a boundary: data is a pack, code is a module, and a palette is data
even though it looks like design.

**Rules 1, 2 and 3 — never invent production, unknown is not zero, unlabelled
is reported as unlabelled.** These are the reason the kernel keeps the
arithmetic. A module may present a number and may not compute one, and a report
surface that cannot render *unknown* is refused at registration rather than
trusted to behave. This is the same argument
[0023](0023-the-fleet-console-observes.md) made against putting the fleet view
where the honesty rules do not reach, applied to reports, to themes, and — if
Resolution B is chosen — to code the project does not publish.
