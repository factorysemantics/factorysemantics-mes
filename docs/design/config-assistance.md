# Configuration is authored by roles, and selected by operators

*Design. Written 2026-09-18 against the code at `a21f755`, before anything is
built. It states what exists, proposes a role taxonomy for configuration, a
three-tier scheme for every setting, an outline of the skill document an
assistant would read, and one pilot that proves the whole loop on one field.
It decides nothing: the decision it turns on is
[0035](../decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md),
and it was **accepted** on 2026-09-18 after one revision: the lifecycle had no *discovered* step, and now has.*

---

## 1. The problem

At 06:10 a machine stops. The operator opens the station screen, and the box
that asks why is a text input whose placeholder reads *"e.g. jam at infeed,
waiting on fitter"* (`src/fsmes/web/station.html:43`). Whatever is typed is
stored exactly as typed, in a 120-character column with nothing behind it
(`src/fsmes/domain/equipment.py:40`). The downtime pareto then groups by that
string and by nothing else — `key = state.reason or UNLABELLED`
(`src/fsmes/services/analysis.py:583`). So *jam*, *Jam*, *jam at infeed* and
*infed jam* are four separate reasons, each too small to act on, and the real
top reason on the line is invisible because it was spelled four ways. The
operator did nothing wrong. The screen asked an open question and got an open
answer.

The engineer's side is the other half of the same problem. The list the
operator should have been picking from does not exist, and there is nowhere
to put it: a reason vocabulary is not equipment, not a material and not a
routing, so `masterdata.write` — *"Define equipment, materials, BOMs and
routings"* (`src/fsmes/services/capabilities.py:34`) — does not cover it
either. Of the six built-in roles, only `admin` holds any capability that
writes configuration at all, and `admin` holds all twenty-one
(`capabilities.py:58-60`). A process engineer who wants to name six downtime
reasons must be made administrator of the whole plant, or must find one.

Both halves are one failure: **the plant's vocabulary has no author.** The
operator is asked to invent it one stop at a time, and the person who knows it
has no place to write it down and no right to.

---

## 2. Who owns what — roles and configuration domains

The kernel already has the right shape and the wrong granularity. A `Role` is
a row in `roles` with a JSON array of capability names and a `builtin` flag
(`src/fsmes/domain/masterdata.py:159-183`); an administrator can create roles
and redefine the shipped ones on the admin API
(`src/fsmes/api/routers/admin.py:64,83,185`). Capabilities are twenty-one
strings named `area.verb`, and the API, the agent tools and the screens all
gate on the same list (`src/fsmes/services/capabilities.py:24-46`). Nothing
here needs replacing.

The granularity is the problem. **One capability, `masterdata.write`, gates
everything an engineer of any kind would author**: equipment, materials, BOM
and routings (`src/fsmes/api/routers/masterdata.py:64,105,129,175`), quality
specifications (`src/fsmes/api/routers/quality.py:120`) and gauges
(`api/routers/quality.py:407`). Only `admin` holds it, and `admin` holds all
twenty-one. So there is exactly one configuration role in this MES today, and
it is the plant administrator.

**Proposal: the domain is the capability prefix; the role is a bundle an
administrator composes.** Nothing in the identity model changes shape. Roles
stay data. What changes is that `masterdata.write` stops being one word for
five jobs.

| Domain | What lives in it today | Gated by today | Proposed |
|---|---|---|---|
| **Plant administration** | accounts and roles; `[plant]`, `[modules]`, `[words]` in `plant.toml`; the module registry | `users.manage` | unchanged |
| **Manufacturing / process engineering** | equipment, materials, BOM, routings (`masterdata/*.json`, `POST /masterdata/*`); work instructions (`documents`) | `masterdata.write`, `documents.write`, `documents.approve` | `process.define`, `process.approve` |
| **Controls engineering** | `tag_map.json` — node ids, `state_map`, `cycle_seconds`, `publishes_order` (`src/fsmes/integrations/opc/tag_map.py:81-99`); `[serve] opc_endpoint`; triggers; setpoint adjustments | `triggers.write/approve`, `adjustments.propose/approve`; the tag map is gated by nothing — it is a file | `signals.define`, `signals.approve` |
| **Quality engineering** | quality specifications (`min_value`, `max_value`, `unit`, `src/fsmes/domain/quality.py:16-29`); gauges; non-conformance severity | `masterdata.write` | `quality.define`, `quality.approve` |
| **Supply chain / ERP** | `[erp] mode`; `MES_ERP_*` and `MES_ERPNEXT_*` (`src/fsmes/config.py:177-201`); inbound mappings | nothing — environment variables only | `erp.define` |
| **IT** | `[storage]`, `[serve] api_host/api_port/secret_key_env`, TLS, the fleet's `data_dir` | nothing — files and environment | **stays outside the role model** |

**Start with two, name six.** The work starts with administration and
process engineering, because those are the two that exist. The other four
are named now so the identity model does not paint itself into a corner: a
capability is a string and a role is a list of them, so adding
`signals.define` later is cheap — *changing what `masterdata.write` means*
after plants have built roles on it is not.

**IT is deliberately not a role here.** Where the database lives, which port
the plant serves on and where the secret key comes from are facts about the
machine, not the plant — the fleet file says exactly that about `data_dir`
(`src/fsmes/pack/fleet.py:16-19`), and a pack names where a secret lives and
never holds one (decision
[0022](../decisions/0022-what-a-plant-pack-may-contain.md)). Naming the
domain and leaving it empty is the honest answer.

**And where a domain does not exist yet, say so.** No maintenance
*engineering* beyond `maintenance.plan`, no printing of any kind
(`ROADMAP.md:79` is the only mention), no unit-of-measure registry —
`Material.unit` and `QualitySpec.unit` are free text with no list behind
them.

---

## 2a. Navigation: one Configuration entry per domain, not one per setting

Decided 2026-09-21, after Scott used the pilot and found the first
counter-example: PR #87 gave downtime reasons their own top-level chip
under Engineering (`FS.NAV`, `src/fsmes/web/common.js:44-52`) — *Machines,
Tags, Triggers, Downtime reasons, Adjustments, Analysis, Master data*, one
entry per thing. Scott's rule, stated for everything from here on: **a
domain gets one `Configuration` entry in its nav group; every configurable
section of that domain lives inside it.** He expects hundreds of
configurable sections to exist eventually — a nav bar with a new top-level
entry per one is the clutter this whole effort exists to prevent, and it
is worse than the free-text box it replaces if the person configuring the
plant cannot find the screen among fifty siblings.

This is a correction to how §2's domains reach the screen, not to the
domains themselves. Engineering's nav group today covers what will become
two domains (process and controls) before either is split out; until they
are, one `Configuration` entry under Engineering holds every domain's
configurable section as a list within it, and a subnav or a picker inside
that page — not a new top-level nav item — is how a second section (a
second vocabulary, a routing editor, whatever process or controls
engineering next needs to author) gets added. The same shape applies when
Quality, Supply chain or the rest are built out: one `Configuration`
chip in that group, not a chip per setting.

`reason-authoring`'s placement (#87) is retrofitted to this shape rather
than left as the exception — see `docs/design/config-assistance.md`'s own
history and the handoff that did it.

### As built — `config-nav-restructure`, 2026-09-21

The shape above, in code. Engineering's `Configuration` entry opens
`/dashboard/config/engineering`: a list of that workspace's configurable
sections, stating its total the way every list in this product does, with one
row today — the downtime vocabulary.

1. **A section is a registry entry, not a navigation change.** A module that
   adds something configurable adds a `ConfigSection` to its own entry in
   `src/fsmes/modules.py`, naming the domain it belongs to. The row appears;
   the nav bar does not grow. The first section in a **new** domain adds one
   `ConfigDomain` there and one `Configuration` entry to that group in
   `FS.NAV`, and `tests/test_configuration_sections.py` refuses either half
   without the other — which is what stops this being retrofitted a second
   time.
2. **Nothing behind the entry moved.** `/dashboard/reasons` is the same page
   at the same address, so every bookmark still opens it; only the nav entry
   pointing at it directly is gone. The screen now lights the `Configuration`
   entry it sits inside, the way a machine page lights *Machines*.
3. **The page gates nothing.** It is visible to anybody who may see the
   workspace, as the screens it lists already are; each section keeps its own
   `define`/`approve` capabilities exactly where its author put them. What the
   page adds is naming those capabilities and saying whether the person
   reading holds them — so somebody can see whether the door in front of them
   opens before walking into it.
4. **A section a plant does not serve is withheld and named**, never silently
   dropped: a workspace with one section and a workspace with one section and
   three switched off are different plants.

One domain exists today, *engineering*. The rest of §2's six arrive as their
first section does; naming a domain with nothing in it would serve a page that
says nothing.


## 3. Three tiers

- **Tier one — role-authored vocabulary.** A list this plant owns: its
  reasons, its machines, its materials, its shifts, its tolerances. Two
  honest plants answer differently. Authored by the role that owns the
  domain, and by nobody else.
- **Tier two — operator selection.** What a person on the floor chooses at
  the moment of work. Always a choice from a tier-one list, or a measurement
  against a tier-one tolerance. Never a new word.
- **Tier three — fixed.** What the product owns: equipment states, order
  statuses, capability names, role codes, event kinds, KPI names, the four
  dispositions, the four SPC rules, the field names an API response is built
  from. A plant may rename the label on a screen and may not rename the
  thing.

**The rule for deciding.** Ask what breaks if two plants answer differently.
If nothing outside the plant breaks, it is tier one. If a number, a topic or
an API field would mean something different at the two plants, it is tier
three. Tier two is not a third kind of setting at all — it is the far end of
a tier-one list, seen from the floor.

Which gives the one rule worth remembering:

> **Every tier-two field names its tier-one list. A tier-two field with no
> tier-one list behind it is free text, and free text is the bug.**

Tier three already has a machine-readable definition in this product, and it
was not written for this page: `protected_terms()` builds the list of words a
pack may not rename **by importing them from the product** — every equipment
state, order status, capability, built-in role, event kind, module name, the
KPI names, and the structural fields (`src/fsmes/pack/check.py:101-141`). A
capability added next month is protected the day it lands. The tier scheme
should read that same function rather than keep a second list.

### A dozen settings, placed

| Setting | Where it lives today | Tier | Note |
|---|---|---|---|
| Downtime reason | `EquipmentState.reason`, free text `String(120)` (`domain/equipment.py:40`) | two, **with no tier one behind it** | The pilot |
| Scrap reason | nowhere — scrap is a bare quantity (`domain/execution.py`) | two, **missing entirely** | `docs/design/backlog/scrap-reason-codes.md`, open since 2026-09-01 |
| Non-conformance disposition | `NcDisposition`, four values (`domain/quality.py:80-91`) | three | The ERP reads it; four is what a plant does |
| Non-conformance severity | `String(20)`, default `"minor"` (`domain/quality.py:99`) | two, **no tier one** | Free text by accident, not by design |
| Quality specification limits | `QualitySpec.min_value/max_value/unit`; pack `masterdata/quality_specs.json` | one | Quality engineering |
| SPC rules 1–4 | `_western_electric`, hard-coded (`services/spc.py:67-112`) | three | A rule a plant can switch off is a chart that lies |
| OPC `state_map` — raw PLC value → MES state | `tag_map.json` (`integrations/opc/tag_map.py:33-37`) | one | Controls engineering. The MES state names it maps *to* are tier three |
| Shift patterns | `shift_patterns` table; pack kind `shifts` | one | Decision [0028](../decisions/0028-which-shift-a-minute-belongs-to.md) fixes what a shift *means*; the patterns are the plant's |
| Machine names | `Equipment.code`/`name`, plus `MachineMap.object` | one | Two domains, joined by the tag map |
| Units of measure | `Material.unit`, `QualitySpec.unit` — free text (`masterdata.py:66`, `quality.py:25`) | one, **missing** | No registry, no conversions |
| ERP endpoint and mode | `[erp] mode`; `MES_ERPNEXT_*` (`config.py:188-201`) | one | Supply chain. Lives in the environment today, not in the pack |
| OEE coverage floor | `[oee] coverage_floor` (`pack/format.py:177-185`) | one | May only withhold a figure, never invent one (decision [0033](../decisions/0033-availability-is-a-share-of-what-was-watched.md)) |
| Over-run behaviour | not configurable (decision [0029](../decisions/0029-an-order-does-not-finish-itself.md)) | three | Deliberately |
| Performance cap | not configurable, permanently (`services/oee.py:12-13`) | three | *"a cap is a lie in the direction of flattery"* |
| `[words]` display terms | `plant.toml` `[words]` | one | Already fenced by `protected_terms()` |

### How this meets "config, not code, at every plant boundary"

Principle 7 is already satisfied for the tier one that is **static**: what
a plant is when it is built lives in the pack and is seeded by
`fsmes pack apply`. But `pack apply` **never updates** — an entry whose code
already exists is left exactly as it is, because a pack that rewrote a
routing an order has already run against would be rewriting history
(`src/fsmes/pack/masterdata.py:36-41`).

That is right, and it means a vocabulary a plant edits during its life
cannot be pack-only. The shape that already works is `shifts`: a database
table, seeded from the pack at build time, edited afterwards through a
capability-gated endpoint, audited on change. **Tier one is seeded by the
pack and owned by the database.**

---

## 4. Draft → validated → discovered → signed off → live → undone

This lifecycle does not need inventing. The product has built it twice and a
half already, and both times for the same stated reason.

- **Work instructions.** `Document` is a revision, not a document:
  `UniqueConstraint("code", "revision")`, approving freezes it, changing it
  makes the next revision and leaves the old one readable
  (`src/fsmes/domain/documents.py:38-55`). The local model drafts the prose
  and the draft arrives as revision 1 with the model that wrote it recorded
  in `drafted_by_model`, *"because a model that could put words directly in
  front of an operator is a hazard rather than a feature"*
  (`src/fsmes/services/drafting.py:1-13`).
- **Triggers.** *"the draft → approved lifecycle work instructions have,
  because logic that fires against a live plant is a controlled thing"*
  (`src/fsmes/domain/triggers.py:1-9`).
- **Setpoint adjustments.** `adjustments.propose` and `adjustments.approve`
  — *"the human in the loop before a PLC write"*
  (`src/fsmes/services/capabilities.py:45`).

Configuration vocabulary is the fourth thing of that kind. The proposal is
to give it the same lifecycle rather than a new one — **plus the one step
none of the three has**, which is the step where somebody is told a draft is
waiting.

**Draft.** A person holding `<domain>.define` drafts, or an assistant drafts
for them. The product already knows how to say that: the audit row carries
`actor` and `on_behalf_of`, and only an account whose live role is `agent`
may set `X-On-Behalf-Of` (`src/fsmes/domain/audit.py:27`,
`src/fsmes/api/deps.py:163-181`). A draft is a row with `status = draft`. It
changes nothing on the floor.

**Validated.** Three things, and they are cheap because the product can
already do each one somewhere:

1. *Schema.* Codes are unique and topic-safe; no code collides with a term
   the product owns — the same `protected_terms()` the pack checker reads
   (`src/fsmes/pack/check.py:101-141`).
2. *Cross-reference.* Nothing still in use disappears silently. Retiring a
   code that labels live intervals is allowed; doing it without the draft
   saying how many intervals carry it is not.
3. *A dry run against this plant's own data.* Every write here already takes
   `dry_run=true` and returns the exact request it would send
   (`docs/agents/write-discipline.md`). For a vocabulary the dry run can be
   better than that: run the proposed list over the last thirty days of what
   operators actually typed and report **how much it would have covered, how
   much it would not, and which spellings fold where.** A person can judge
   that in ten seconds, and it is computed from the plant's history rather
   than asserted.

**Discovered.** The step this page was missing, and the one that decides
whether any of the rest ever happens. A validated draft is worth nothing
until the person who can sign it off knows it exists.

*What this product does today, checked rather than assumed.* Three
lifecycles already run here, and the design-chat notes are a fourth kind of
thing that waits. Not one of them tells anybody.

| Waiting item | Where it is visible today | Anything tells the approver? |
|---|---|---|
| A document draft | `/dashboard/instructions`, and only by reading rows. The "draft only" filter means *never approved* (`src/fsmes/web/instructions.js:114-116`, the same test in the API at `src/fsmes/api/routers/documents.py:71-72`), so a new revision of a document already in force is **excluded from it**. No draft count anywhere on the screen | No |
| A trigger draft | `/dashboard/triggers`, on a tile — *"Drafts awaiting approval"*, counted over the plant and not the page, deliberately (`src/fsmes/web/triggers.html:28`, `src/fsmes/web/triggers.js:89-92`). The best surface in the product | No |
| A proposed adjustment | `/dashboard/adjustments`, on a tile — *"Awaiting a decision"* — counted over the loaded page of fifty (`src/fsmes/web/adjustments.js:108-111`), so a deep queue reads short | No |
| A design-chat note | Nowhere in the product. `fsmes design-pending` on the host, against `~/.local/share/fsmes/design.db` (`src/fsmes/cli.py:2496-2510`) | No |

The honest sentence is: **you have to know to go and look.** There is no
inbox, no badge, no aggregated count, no approvals endpoint and no
notification of any kind — no mail, no webhook, no push — and not one of the
four domain events the outbox emits is about an approval
(`src/fsmes/services/outbox.py:82,112,145,155`). The capability split makes
that the normal case rather than a corner: `documents.write`,
`triggers.write` and `adjustments.propose` sit with `supervisor` and with
`agent`, while all three approve capabilities are held by `admin` alone
(`src/fsmes/services/capabilities.py:55-59,93-94`). The proposer and the
approver are usually different people and nothing crosses between them — and
an approved trigger can fill the adjustment queue overnight by itself
through `propose_adjustment` (`src/fsmes/services/triggers.py:107`) with
nobody told at all.

So the gap is not this page's. It is the product's, three times over, and a
vocabulary draft would have been the fourth.

*Start where Scott started: an admin dashboard.* The nearest thing that
exists is `/dashboard/admin`, gated on `users.manage`
(`src/fsmes/web/common.js:55`), and it is people, roles and routings and
nothing else (`src/fsmes/web/admin.html:34,64,89`). The plant dashboard
`/dashboard` is the screen everybody opens, and its tiles are machines,
orders, OEE and the ERP queue (`src/fsmes/web/index.html:38-46`); the
payload behind it, `/dashboard/summary`, counts no waiting item of any kind
(`src/fsmes/api/routers/dashboard.py:191-356`).

A **pending-approvals panel** is one row per waiting item: the kind, the
code, who drafted it and on whose behalf, **how long it has waited**, the
headline of its dry run — for a vocabulary, what it would have covered —
and one action that opens it where it can be signed. Counted, and with its
total, in the envelope every list endpoint here already returns
(`src/fsmes/api/paging.py:36-47`) and the house rule the style guide states
as *"every list states its total"* (`docs/design/STYLE.md:32`).

*And then the harder half, because an admin dashboard is not where a process
engineer looks.* The whole point of §2 is that holding `process.approve`
should not mean being administrator of the plant — so the person who signs
off a vocabulary may never open an administrator's screen. The rule this
page proposes is therefore not a screen but a placement:

> **A pending item appears on the screen of the role that can act on it,
> counted, with its total — and on no screen that cannot act on it.**

That is one panel, rendered from what the caller may do rather than from who
they are, and the mechanism is already in every screen: `GET /auth/me`
returns the caller's capabilities, read live from the database rather than
taken from the token (`src/fsmes/api/routers/auth.py:57-73`); one call per
page is cached in `FS.whoami()`; `FS.can()` and `[data-needs-cap]` decide
what renders (`src/fsmes/web/common.js:204-212`). A panel that asks the
server for *everything waiting that I hold the approve capability for* is
that mechanism turned around — the server answers from the caller's
capabilities, instead of the screen hiding what the answer already
contained.

Where it goes: on the plant dashboard, because that is the screen everybody
opens, and on the domain screen beside the thing it is about. Same panel,
different scope — the plant dashboard shows every kind the caller may
approve, the triggers screen shows triggers. Somebody who only ever opens
one screen still finds their own queue on it, and nobody is shown a queue
they cannot act on.

*A draft nobody acts on waits, visibly.* It does not expire, it does not go
live by itself, and it is never quietly dropped. The only thing that changes
with time is one column — how long it has waited — so a forgotten draft
reads as *"drafted 11 days ago"* rather than falling off the end of a list.
Unknown is not zero, and neither is ignored.

*The design-chat notes belong on the same panel.* A note somebody typed on a
screen is a draft of a different kind with exactly this failure. Today it
surfaces only if a person on the host remembers to run `fsmes design-pending`
— and the module's own docstring already promises something the product does
not do: *"opening the screen's Design panel shows what happened to what you
said"* (`src/fsmes/services/design_triage.py:26-27`), while the panel calls
`/design/chat` and `/design/status` and nothing else, starting an empty
conversation on every load (`src/fsmes/web/design.js:160,213`). One more
kind on one panel, for whoever can act on it.

No notification channel is proposed here. Mail, a webhook or a push is a
question for later (§7, question 6) and not a proposal: this product has no
such channel at all, and the cheap honest fix is that the screen a person
already opens tells them the truth when they open it.

**Signed off.** A person holding `<domain>.approve`. Never the assistant:
the `agent` role is built to hold the drafting half and never the approving
half, deliberately and in one place
(`src/fsmes/services/capabilities.py:88-97`). Approval records who and when
(`approved_by`, `approved_at`), exactly as a document's does.

*What this honestly is not:* approval here is an account code and a
timestamp. This product has no qualification record, no training matrix and
no electronic signature anywhere in it. "A **qualified** role signs off"
therefore means, today, "a role an administrator granted the approve
capability to". If it should mean more, that is a decision and a schema, and
it is not in this page.

**Live.** The approved revision supersedes the one before it, and the
operator's dropdown changes at the next screen load — the screens already
fetch what they may show from the API on load and gate on it
(`src/fsmes/web/common.js:204-212`). The audit spine gets the row: who, when,
before and after (`src/fsmes/services/audit.py:8-30`).

**Undone.** Cheaply, and in one move: approve the previous revision, which
supersedes the current one. Nothing is deleted and no history is rewritten.
And the part that makes it genuinely cheap:

> **Retiring a code changes what may be chosen next. It never changes what
> was chosen before.** An interval labelled `jam-infeed` keeps that label
> when `jam-infeed` leaves the list, and the analysis screen keeps showing
> it. A record is not un-made — the same principle decision
> [0029](../decisions/0029-an-order-does-not-finish-itself.md) applies to an
> order that over-ran.

So the worst outcome of a bad vocabulary is a month of stops labelled with
words somebody regrets — which is exactly the situation today.

### What the assistant may never do

Decisions [0031](../decisions/0031-a-judgment-is-a-proposal.md) and
[0032](../decisions/0032-a-hosted-judgment-and-the-shadow.md) already answer
this, and this page does not reopen either.

A model's answer is a proposal, never an input to a graded number (0031). A
drafted vocabulary is a draft; `EquipmentState.reason` stays as it is until a
person selects from an approved list, so `unlabelled_share` reports today
what it reports today.

And 0032 decides *where the work runs*. A plant's reason vocabulary is named
in that decision as **configuration** class. But the free text an assistant
would read *in order to propose one* is **production** class — *"anything a
person typed"* — refused in shadow mode and off by default everywhere. So the
clustering step runs on the **local** model, the same `qwen3:8b` over Ollama
that already drafts work instructions
(`src/fsmes/services/drafting.py:27-28`), and sends nothing anywhere. Not a
workaround: the decision applied.

---

## 5. What the MES's own skill document must cover

A skill document is what lets an assistant work a system confidently and
safely without being told everything each time. The interesting question is
not what to write first — it is **where it lives**, and decision
[0032](../decisions/0032-a-hosted-judgment-and-the-shadow.md) already
answers that.

**Both, and the class decides which half.** What is identical at every plant
— the tiers, the lifecycle, the capability names, the refusals — is
**catalogue** class: text that ships in the wheel, in the repository at
`docs/agents/configuring-a-plant.md`, beside the write discipline it
extends. What is *this* plant — its machines, materials, shifts, current
vocabulary and how much of the last month that vocabulary covers — is
**configuration** class, generated from the running plant, shipped nowhere.

**How it stays true: a test**, in the shape this repository already uses
twice. `tests/test_capabilities.py` asserts every `require("…")` literal
names a real capability; `tests/test_shadow_mode.py` scans the source and
fails on an unregistered outbound call site. Every capability, tier-three
term and refusal the document names is read back from `CAPABILITIES`,
`protected_terms()` and the register, so a document that has drifted from
the product fails the build.

The outline, section by section. **This page does not write it.**

1. **What this MES is, and the one rule.** Three tiers in six lines, and:
   every tier-two field names its tier-one list.
2. **The domains and who owns them.** Generated: the capability list with
   its one-sentence descriptions, and this plant's roles with what each
   holds.
3. **This plant's vocabulary.** Generated: machines, materials, shift
   patterns, the approved reason list, and — the part that matters — how
   much of the last thirty days each list actually covers, with the total.
4. **The tier test.** How to decide which tier a setting is, and the
   protected list, read from the product.
5. **How to propose.** Always a draft; always `dry_run` first; always
   `on_behalf_of` a named person. Nothing here is new — it is
   [the write discipline](../agents/write-discipline.md) applied to
   configuration.
6. **How to validate before proposing.** The three checks of §4, and how to
   compute the coverage dry run.
7. **What it must refuse, and how to say so.** Anything tier three. Anything
   that would change a number already booked. Any state class this plant has
   not turned on. Approving its own draft, always.
8. **How to hand a draft over.** What the person is shown before they sign:
   the diff, the coverage, what is retired and how many records carry it —
   and that handing over means the draft now stands in that person's own
   pending queue, not that a message was sent somewhere.
9. **How to undo**, and what undo does not do — it changes what may be
   chosen next, never what was chosen before.
10. **When to say it does not know.** Unknown is not zero. A vocabulary the
    assistant cannot justify from the plant's own history is not proposed.

---

## 6. The pilot: reason codes

### Today

Twenty-six free-text columns in this MES hold a reason, a code, a category
or a comment; sixteen of them are typed by a person. Not one has an enum, a
foreign key, a check constraint, a pattern or a length check at the API. The
pilot takes the one that matters most and leaves the other twenty-five
alone.

**Where a downtime reason enters, and from whom.** Four writers, not one:

| Writer | What it writes | Where |
|---|---|---|
| The station screen | whatever the operator types | `src/fsmes/web/station.html:43` → `POST /equipment/{code}/state` (`api/routers/equipment.py:57-63`) |
| A trigger | `params["reason"]`, or a composed string like `"Infeed jam (Alarm_Word=1)"` | `src/fsmes/services/triggers.py:52-56` |
| An inbound feed | the incumbent system's own `reason_code` column, verbatim | `config/inbound_mapping.json:12`; `integrations/inbound/contract.py:116-133` |
| An agent tool | any string a model chooses | `src/fsmes/mcp_server.py:434-446` |

And a fifth thing that writes nothing: **the OPC agent never supplies a
reason at all** (`src/fsmes/integrations/opc/agent.py:589-591`). Every stop
a machine reports by itself lands unlabelled. That is the cold start, and it
is why the vocabulary has to come from somewhere other than the machines.

**What a typo does.** The pareto groups on the raw string —
`key = state.reason or UNLABELLED` (`src/fsmes/services/analysis.py:583`) —
with no strip, no casefold, no alias map anywhere in the path. So:

- Four spellings are four bars. Sorting is by seconds
  (`analysis.py:594`), so a reason split four ways sinks below reasons it
  actually outweighs. **The pareto inverts, silently.**
- The chart truncates anything over fourteen characters
  (`src/fsmes/web/analysis.js:179-191`), so two different typos can render
  as the same bar.
- An operator who types the word `unlabelled` merges into the machine's
  unlabelled bucket and moves `unlabelled_share` — a headline honesty
  number (`analysis.py:619`) — with no error anywhere.
- The value is published to the broker on the state-change event
  (`src/fsmes/services/outbox.py:104`), so the typo leaves the MES.

The project has already written this down and deferred it:
`docs/ai/JEV.md:403-412` says the reason vocabulary *"only exists if the
plant has already labelled stops… the answer is probably the ISO 22400 /
incumbent categories as a starting vocabulary a plant edits, which is a
plant pack question."* This page is that answer, and it agrees with the
guess.

### Proposed, end to end

1. **Author.** The process engineer holds `process.define`. The assistant
   reads the last thirty days of typed reasons with their durations — on the
   box, on the local model, because that text is **production** class — and
   comes back with: *"these 40 spellings look like 6 reasons. Here is a
   draft, with what each one would have covered. You decide."*
2. **Validate.** Codes unique and topic-safe; none collides with a term the
   product owns (`protected_terms()`); and the coverage dry run: how many
   intervals and how many hours the draft would have covered, how many it
   would not, and which spellings fold where. Stated as a total, per
   house rule.
3. **Sign off.** A person holding `process.approve`. Not the assistant, ever
   — and one draft at a time. `docs/ai/JEV.md:409-412` makes the point
   against itself: *"a proposal a supervisor accepts a hundred times a shift
   becomes a booking by fatigue"*. A vocabulary is signed off once, which is
   exactly why it is the right place to start.
4. **Live.** The station's reason box becomes a `<select>` fed by a catalog
   endpoint, in the exact shape `GET /triggers/catalog` already has — a
   server-owned `{code: sentence}` map the screen renders
   (`src/fsmes/api/routers/triggers.py:29-43`). That this is the right shape
   is not a matter of taste: the only two selects in the whole reason family
   today are hardcoded `<option>` blocks duplicating a Python enum in HTML
   *and* in JavaScript, and one of them is already wrong — the schedule
   screen offers `shutdown`/`overtime` where the enum is
   `non_working`/`working` (`src/fsmes/web/schedule.html:108-112` against
   `src/fsmes/domain/calendar.py:24-26`), so both of its options 400.
5. **Undo.** Approve the previous revision. Intervals already labelled keep
   their labels.

### The smallest PR that proves it

- One table, `downtime_reasons` — `code`, `name`, `description`,
  `revision`, `status`, `created_by/at`, `approved_by/at` — shaped exactly
  like `documents`, with one Alembic migration. `docs/ARCHITECTURE.md:74`
  already lists this table as a module table; it has never been built.
- A **nullable `reason_code` beside the existing `reason`**, never in place
  of it. `str_enum` in this codebase emits no check constraint
  (`src/fsmes/domain/common.py:11-19`), so this would be the first
  database-level constraint on any reason here — which is a reason to add it
  additively and leave every existing row untouched.
- `GET /equipment/downtime-reasons`, in the `/triggers/catalog` shape.
- Two capabilities, `process.define` and `process.approve`, added to
  `CAPABILITIES` and to the `admin` bundle. Nothing is re-gated yet.
- The station screen: a `<select>` from the catalog when a vocabulary is
  approved; today's text box when there is none.
- The pareto groups by code where there is one and by text where there is
  not, and **states both totals** — every list states its total, and "how
  much of this window came from the list" is the number that says whether
  the pilot worked.
- One pack masterdata kind, `downtime_reasons`, so a vocabulary can ship
  with a plant (`src/fsmes/pack/masterdata.py:53-90` refuses any file that
  is not one of its nine kinds).
- **The discovery half, or the pilot has not tested the loop that fails.**
  Three small things: `GET /equipment/downtime-reasons?status=draft`
  returning the paging envelope with its total, the way `/adjustments`
  already does (`src/fsmes/api/routers/adjustments.py:28-39`) and the two
  older lifecycles do not; one endpoint that answers *what is waiting that
  I may approve* from the caller's capabilities, which in this pilot has
  exactly one kind behind it; and one panel on `/dashboard` that renders it
  — kind, code, drafted by and for whom, how long it has waited, the
  coverage headline, one link.

**What it would not touch.** Not the OEE math. Not the definition of
`unlabelled_share`. Not scrap — scrap has no reason column at all and that
is a second pilot, already written up in
`docs/design/backlog/scrap-reason-codes.md`. Not the other three writers:
triggers, inbound feeds and agent tools keep writing text, and the pareto
says how much of the window came from the list and how much did not, rather
than pretending. Not `masterdata.write` — splitting it is its own change.
Not any screen but the station's and the plant dashboard's.
Not the three existing lifecycles: documents, triggers and adjustments keep
the surfaces they have, and join the panel when the second vocabulary does.

**What that adds, honestly:** one endpoint, one panel and their tests —
call it a third again on top of the rest of this PR. It is still the right
size. A pilot that proves drafting and signing but not finding has tested
everything except the thing that went wrong.

### How it shows on the two lab plants

**Half of it, and the honest half.** Both lab plants are driven by the OPC
agent, which supplies no reason, so their downtime is entirely unlabelled and
**there is no typed history on either to cluster.** The clustering step needs
a plant with a month of people typing: the first real plant in shadow mode,
or a lab script that deliberately types a messy set.

What they *can* show today is the rest of the loop — seed a vocabulary into
each pack, apply it, sign it off, watch the dropdown appear, label stops from
the list, and see the pareto group by code with both totals stated. Worth
doing first, because it is the part the product has to get right whether or
not a model is ever involved.

*(Stated for the record: this page did not read the running lab plants'
data. The question it would have answered — do they hold free-text reasons
worth clustering — is answered in the code above: they hold none.)*

---

## 7. Questions for Scott

Six, each with the option this page would take, and why.

**1. Are those six domains the right six for a real plant?** Administration,
process engineering, controls, quality, supply chain, IT. *Proposed: yes,
with IT named and left outside the product's role model.* No amount of
reading the code answers this one, and it is the expensive one to change
later: a capability name is cheap to add and painful to redefine once plants
have built roles on it.

**2. Does the product ship a starting reason vocabulary, or does every
plant start empty?** *Proposed: ship one, ISO 22400-shaped, and let the
plant own it from its first edit.* Seven terms already exist in this
repository with operator-readable descriptions —
`src/fsmes/sim/labelled.py:62-86`, written for the calibration work — and a
plant that starts empty gets no dropdown on day one, which is the failure
this whole effort is about. The pack seeds it; the plant edits it; the
product never edits it again.

**3. Once a plant has a vocabulary, is a reason required when a machine
stops?** *Proposed: required, with an explicit "not yet determined" code in
the list rather than an allowed blank.* Required will annoy people in week
one. But a blank reads as an omission where the truth is "nobody has looked
yet", and nothing else in this product lets a blank stand for an unknown.

**4. What happens to the reasons people have already typed?** *Proposed:
keep every one of them, exactly as typed, and never rewrite one.* The new
code sits beside the old text; the pareto groups by code where there is one
and by text where there is not, and states both totals. Folding old text
into new codes automatically would be inventing a record nobody made.

**5. Should "a qualified role signs off" mean more than "a role an
administrator gave the approve capability to"?** *Proposed: not yet.* This
product has no training record, no qualification matrix and no electronic
signature — approval is an account code and a timestamp. Those are real
features for a regulated plant and each is a decision of its own; building
half of one inside this pilot would be worse than naming the gap.

**6. Where does the approving role see what is waiting?** *Proposed: Scott's
admin dashboard as the place to start, extended by one rule — a pending item
appears on the screen of the role that can act on it, counted, with its
total, and on no screen that cannot.* This is not a new worry: the product
has the gap three times already, and §4 sets out what each of the three does
today. Starting on the plant dashboard puts the panel where everybody
already looks; rendering it from the caller's capabilities rather than from
a role name is what keeps it useful to a process engineer who will never
open an administrator's screen. The sub-question this page deliberately does
not answer: whether any channel outside the screen — mail, a webhook, a push
— is ever worth having. There is none in this product today, and adding the
first one is a decision of its own.

---

## 8. What this is not

- **Not a generic configuration engine.** One table, one field, one screen,
  one loop. If the loop is right, the second vocabulary is a small change;
  if it is wrong, one table is what has to be undone.
- **Not free-form chat that edits settings.** Every change is a draft, and
  every draft waits for a person with a capability — visibly, on that
  person's own screen.
- **Not a notification system.** No mail, no webhook, no push is proposed
  here. A screen somebody already opens says what is waiting for them when
  they open it; whether anything should ever reach further than that is
  question 6.
- **Not an assistant with a right to sign anything off.** The `agent` role
  is built to hold drafting capabilities and no approving one
  (`src/fsmes/services/capabilities.py:88-97`); that stays true.
- **Not a loosening of what operators may do.** The only change on the floor
  is that a text box becomes a list. Where the list does not yet exist,
  nothing changes at all.
- **Not a change to any number.** No OEE component, no booked count, no
  coverage figure, no `unlabelled_share` moves because of anything in this
  page.
- **Not built.** Nothing here is a schema, an endpoint or a screen until
  decision [0035](../decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
  is accepted.

---

## 9. Built — what the pilot taught, 2026-09-19

The pilot in §6 is built: the table, the two capabilities, the catalogue, the
station's select, the pareto's two totals, the pack kind, and the discovery
half. The page above is not amended, and neither is decision 0035. Six things
it got wrong or left out, recorded where the next piece will look:

1. **Three statuses were not enough.** §6 proposed draft / approved /
   superseded. The build needed a fourth, `retired`, because *superseded*
   means another revision of this code took over and *retired* means this code
   is no longer offered. A pareto that could not tell those apart could not
   explain why a bar it still shows is on nobody's screen.

2. **`?status=draft` on the catalogue is two shapes on one path.** §4 spelled
   the waiting list as a query on the catalogue; it was built as
   `GET /equipment/downtime-reasons/drafts`, because the catalogue is a map a
   screen renders and the queue is a list a person works through, and one
   route returning either is a route a caller has to guess at.

3. **The panel signs rather than opens.** §4 asked for "one action that opens
   it where it can be signed". A vocabulary has no screen of its own in this
   pilot, so the row's action *is* the signature, taken on the panel; the
   server hands each row the path to post. That is honest for a vocabulary —
   the row shows the whole draft — and it is what makes one panel able to sign
   several kinds. A kind whose draft cannot be read in a row will want a link
   instead, and the shape already carries one.

4. **Two of the seven shipped terms do not describe a stop.** §6 pointed at
   the seven reasons in `src/fsmes/sim/labelled.py`, but `none` ("the machine
   did not stop in this window") and `counter_reset` ("without the machine
   stopping") are answers about a *window*, not labels for a stop. Shipping
   them would have invited a wrong label, so the starting vocabulary is the
   other five plus the explicit `not_yet_determined` that §7's question 3
   asked for.

5. **A packed vocabulary arrives in force, not as a draft.** The page did not
   say which, and "a draft never goes live by itself" reads like it should be
   a draft. Applying a pack is a deliberate act by a person, and a plant whose
   station screen offered nothing until somebody approved six seeded rows
   would have shipped with a text box after all. Every change after the first
   apply goes through the lifecycle.

6. **`protected_terms()` is in the wrong layer.** It lives with the pack
   checker (`src/fsmes/pack/check.py`), which is an edge, and the vocabulary
   service reads it — so the layering test carries a written allowance for one
   call-time import. Moving it down to the services layer is a change of its
   own, and worth making before a second domain reads it.

**And what the pilot did not do**, beyond what §6 already excluded: the
analysis screen does not yet print the two totals the API now carries, because
§6 said to touch no screen but the station's and the dashboard's. That is the
smallest next piece, and it is one paragraph of JavaScript.

---

## 10. The audit — what else is still hard-coded, 2026-09-21

This page decided *how*. The next question is *what else*, and it is answered
somewhere that can go stale without taking a decision with it:
**[What is still hard-coded that a plant might want to own](config-audit-2026-09-21.md)**,
produced by `fsmes config-audit`, which applies §3's two-plants test to the
whole tree mechanically and can be rerun any day.

Three things from it belong here rather than there, because they bind
everything built from now on and are not a snapshot of one day's source:

1. **The literal that is in the source today becomes the shipped default,
   unchanged.** Every candidate the audit finds already has a value, so making
   one configurable must not also move it. A plant that configures nothing
   behaves exactly as it does now.
2. **A list is honest as a plain list up to twelve; past twelve it needs search
   and a stated, stable sort.** Twelve because this product already answered
   "how many rows is a screenful" once, at `services/analysis.py:440`. A
   scrolled list without search reproduces the failure the free-text box had:
   the word is in there, the person cannot find it, and they add a duplicate.
3. **Kind-tagging is a rule about the PR, not about the literal.** The PR that
   makes any setting configurable adds a `ConfigSection` in `modules.py` in the
   same PR — every setting, including one that will only ever be a key in
   `plant.toml` — and adds a `KINDS` entry in `services/review.py` in the same
   PR *if and only if* it has a draft-then-approve step. Neither is ever added
   afterwards. The nav was retrofitted once (§2a); that is what this rule
   exists to prevent happening again.

One correction to §3's table came out of the audit and is recorded here rather
than left in a dated page. §3 places "SPC rules 1–4" in tier three with the
note *"A rule a plant can switch off is a chart that lies."* That reasoning is
about the **chart**, and it holds. Whether a plant may choose which rules raise
a **hold** is a different question that §3 did not ask, and it is open. The
rule *windows* — 2-of-3, 4-of-5, 8-in-a-row — are tier three either way: a
plant that changed them would publish `SpcSignal.rule = 3` while meaning
something nobody else means by rule 3.
