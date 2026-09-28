# 0038 — An agent in this plant is an account with a role, a budget, a cadence and a data class, and what it produces is a proposal

- **Status:** accepted
- **Date:** 2026-09-27; **accepted with amendments 2026-09-28**
- **Deciders:** maintainer

## Context
On 2026-09-27 the maintainer asked how the **AI** screen becomes *"the dashboard
of an MES agentic harness"*: every person with their own agent, functional agents
for deep analysis, a continuous-improvement team crawling the plant's own records
on a configured cadence, *"making things like UI updates 100 % automated whereas
other improvements need ADMIN user approval"*. The full design is
[the agentic harness](../design/agentic-harness.md). This record is the one
question that design cannot answer for itself: **what an agent is, structurally,
and what it is allowed to produce.**

Four facts about this repository make the question sharp rather than speculative.

**There is exactly one agent today, and it is one account.** `AGENT` is a
`Person` row seeded at `src/fsmes/plant.py:84` with the `agent` role — 14 of the
product's 27 capabilities, holding the drafting half of all five draft-and-sign
pairs and never an `*.approve`
(`src/fsmes/services/capabilities.py:130-142`). Every MCP tool call signs in as
it and carries `X-On-Behalf-Of` for the person, which lands on
`AuditLog.on_behalf_of`. So **the API authorises against `AGENT`'s own role**,
and the signed-in person's capabilities are enforced one layer out, in
`services/agent.py`'s `catalogue()` and `withheld()`. Adding a second agent kind
without giving it an account and a role of its own would silently give it
`AGENT`'s fourteen capabilities.

**There is no cadence anywhere in the product.** No scheduler, no cron library,
no worker. The only recurring job inside a plant is the hourly tag-retention
sweep in `src/fsmes/api/app.py`'s lifespan, whose own comment states the only
pattern available: *"Retention runs in the API process because it is the one
long-lived process every deployment has. Hourly, in batches, and it says so on
Ops."*

**There is one budget and it is in the wrong place.** `MES_AGENT_MONTHLY_USD`
(default `10`) is an environment variable, summed from
`~/.local/share/fsmes/agent-usage.jsonl` — a file that holds **every plant on the
box**. The three per-conversation budgets (`[admin] agent_max_rounds`,
`agent_session_ttl_seconds`, `agent_result_limit`) are proper plant settings. So
the plant can say what one conversation may spend and cannot say what it spends
in a month.

**The approvals queue already exists and is the right destination.**
`GET /dashboard/pending-approvals` answers, per caller, from live capabilities,
*"what is waiting that this caller may act on, counted, with its total"*, and
`/pending-approvals/{kind}/{code}/{revision}` returns the diff, how much recorded
history carries the code, who drafted it and on whose behalf, the one-click
revert and a generated walk to the signature. Two of the five approvable kinds
are wired into `services/review.py`'s `KINDS`; the code names the other three as
*"an entry in that registry, not a new mechanism here"*.

**Accepted on 2026-09-28, with two amendments**, both of them the maintainer's
answers to the design page's own questions. His words are quoted in full in
[the design page](../design/agentic-harness.md#decided-2026-09-28); in summary:

- **Amendment A — the harness changes the product's code.** *"I was thinking a
  person should literally be able to change the code to improve the UI. Move
  cards, change graphs, etc. This should be as maliable as possible."* The
  fifteen `[screens]` numbers this record called "one narrow exception" are a
  floor, not the thing. The exception below is rewritten as **three tiers**, and
  the middle one is an agent that edits the product's UI code and lands the
  change as a pull request a person merges.
- **Amendment B — the harness is the engine of all product improvement.**
  *"This should become the bottle neck for driving all other improvements on that
  list. For example, if I wanted to integrate with SAP or fix any ERPNext
  connection, then it should be this harness that allows be to fix or build it."*
  So the roles the maintainer runs on his own machine are what runs inside the
  product, against the product's own repository — with a person's merge as the
  signature, and with the checkout, the CI and the compiler anywhere but the
  plant.

Two facts recorded below have been fixed since this record was written, both on
2026-09-27: the nine ungated write tools (**PR #119**) and the AI tab's Status
blanking without a local model (**PR #122**). They are kept in *Consequences*
because they are why the first field of the row is the account.

Decision [0035](0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
already settled who authors and who signs. Decision
[0031](0031-a-judgment-is-a-proposal.md) already settled that a model's answer is
a proposal and never an input to a graded number. Decision
[0032](0032-a-hosted-judgment-and-the-shadow.md) already settled that a question
declares the class of state it sends and that shadow mode refuses by class. What
none of them settles is **the shape of the thing doing the asking**, now that
there is to be more than one.

## Options considered
| Option | For | Against |
|---|---|---|
| **An agent kind is a row with six fields — account and role, tool set, cadence, budget, data class, and where its work lands — and what it produces is a proposal in a flow that already exists** | every field already has a mechanism or a named precedent; a kind cannot exist without saying what leaves the box; the queue, the drafts and the audit spine are reused rather than duplicated; a plant can switch a kind off by not granting its role | six fields is five more than "a prompt", and the cadence field has one precedent rather than a framework behind it |
| One agent, more prompts: keep `AGENT` and vary the system prompt per job | nothing to build; the catalogue filter already exists | every kind then holds `AGENT`'s fourteen capabilities, including `production.book` and `equipment.state`. An analysis agent that may book production is a worse answer than no analysis agent |
| An agent kind is a module (`fsmes.modules`) | modules already declare routers, screens, config sections, tools and settings; `MES_MODULES` already switches them off | a module is a unit of *code shipped in the wheel*; an agent kind is a unit of *configuration in a plant*. Two plants running the same wheel must be able to run different crews. It also decides a question [PR #107](https://github.com/factorysemantics/factorysemantics-mes/pull/107) has open |
| Agents may act directly, gated by a plant setting | the automation people ask for; fewer screens | *"the plant decided"* is not a defence when a change on the shift report came from an agent and nothing said so. It is also 0035's own rejected option in a new costume |

## Decision
An agent in this plant is an **account with a role, a tool set, a cadence, a
budget, a declared data class, and one place its work lands** — six fields, held
as this plant's configuration rather than as code. Concretely, four rules.

**A kind has an account and a role of its own, or it does not exist.** Its
capabilities are a bundle like any other role's, its tool set is what
`catalogue()` computes for that bundle, narrowed further by the kind, and the
narrowing is asserted by a test rather than described — an analysis agent is a
kind whose catalogue contains no tool carrying a `dry_run` parameter, which is
the product's own definition of a write tool and is derived independently in two
places that agree.

**What an agent produces is a proposal, and it goes where proposals already go.**
A draft revision, a trigger, a recommended adjustment, an instruction, a
vocabulary entry — authored under the `*.define` half of an existing pair,
discovered through `/dashboard/pending-approvals` counted and with its total, and
signed by a person holding the `*.approve` half. **No agent holds an `*.approve`
capability, and no new approval mechanism is created.**

**Three tiers, and only the first two can ever stop needing a person** (amended
2026-09-28).

*Tier (a) — the numbers a screen runs at.* An agent may put one in force without
a signature only when all five of these hold: it changes a number a screen runs
at; it changes no record's meaning; it changes nobody's capability; nothing a PLC
reads changes, directly or through a cadence a write depends on; and it is undone
in one step, from the screen that shows it, by the path that made it. Today that
set is the fifteen `[screens]` keys plus `[process] gantt_screenful`. It is gated
by a capability of its own that an administrator grants deliberately, per plant,
it runs **warn-only for a release first**, and every such change writes an audit
row with the agent as actor and a trace row beside it.

*Tier (b) — the product's own UI code.* An agent may **propose** a change to the
operator UI's own files as a branch and a pull request, proved by the
repository's existing gates — `test`, `postgres`, `browser`, `lab`, `lockfile`,
`wheel-demo` and the DCO sign-off — and carrying a test that fails before the
change and passes after it. **The merge is the signature**, held by a person on a
repository the plant does not control, which keeps 0035's rule exactly. Nothing
is applied to a running plant: a code change reaches a plant when the plant
upgrades, and there is no other path. After the warn-only release, this tier may
automate only within a **declared file set** — the per-screen `web/*.html|js|css`
files, and explicitly not `common.js`, `assist.js`, `assist-record.js`, `kit.js`,
`styles.css`, `themes.css`, `themes.js`, `web/vendor/` or `web/line/` — and not
for any diff that removes a `data-assist` anchor, moves a computation into the
browser, or fetches anything from outside the box. One question is open and named
rather than assumed: `fsmes ui-check --accept` makes the current look the
accepted look, so either a person re-accepts the baselines as part of the merge
or tier (b) never automates.

*Tier (c) — everything else.* The kernel, the API, the domain services, the
capability model, migrations, packs, connectors, and any doc that states a rule.
An agent may propose any of it — that is what amendment B is for, and it is how
an SAP or ERPNext fix would arrive. **None of it ever applies itself.** There is
no release after which tier (c) automates and no setting that turns it on. A
person's capability, a password and the deletion of a record are not automatable
at any tier, and the product already has no tool for them.

**The line between the tiers is this plant's configuration, and it is signed.**
It lives in the `[ai]` domain with the budgets and cadences, the agent may read
it and propose changes to it through the settings tools that already exist, and
the section that holds it is gated on an `*.approve` rather than taking effect
when saved — because a section that took effect when saved would let an agent
widen its own boundary and then use the widened one. `screens.define` is granted
and revoked through the same tools, on the same signature.

**Cadence, budget and class are the plant's, and stated.** Each kind's interval
and monthly budget are plant settings; a scheduled kind runs behind a
no-model check for whether anything it is about has changed, and advances its own
watermark, so a pass that found nothing is a recorded pass rather than silence. A
pass that did not happen is **unknown, never clean**. Each kind declares one of
0032's four classes, so `fsmes shadow` prints what would and would not leave the
box per agent in the vocabulary a plant asks the question in.

## Consequences
Easy: adding a kind becomes configuration — a role, a subset, two numbers and a
class — rather than a feature. Easy: switching one off, because a plant that does
not grant the role does not have the agent. Easy: answering *"what has the AI
done to my plant"*, because `GET /ops/activity` already answers it from one spine
(*"Humans and agents are in the same spine, so `actor='AGENT'` answers it and
nothing else has to be built for it"*).

Hard: three things this decision requires that do not exist. A cadence inside the
plant process, which has one precedent and no framework. A plant-scoped budget,
which replaces an environment variable. And the three unwired `review.KINDS`
entries, without which a drafted trigger, instruction or adjustment reaches
nobody — *"a lifecycle whose last step is that somebody remembers to look"* is
the failure 0035 was aimed at, and it is still half-built.

Harder, and it must be fixed first — two things, and both are why the first field
of the row is the account.

**A scheduled agent signing in as `AGENT` can already put twenty-four settings in
force with nobody in the loop.** Twenty-two of the 56 Configuration sections have
`approve=None` — they take effect when saved, rule three of 0035 — and a `define`
the `agent` role holds (`process.define`, `quality.define`). They include
`[quality] hold_rules`, `[quality] cpk_capable`, `[quality] cpk_marginal` and
`[oee] min_observed_seconds`: numbers the plant is judged by. Nothing exploits it
today only because the floor assistant never writes unattended — every write is a
card a person presses. A functional agent has nobody in front of it, so it must
have a role of its own rather than `AGENT`'s, and the plant must declare which
keys an unattended agent may write rather than inferring it from a capability.

**Nine of the 47 write tools were outside the per-person capability filter —
closed by PR #119 on 2026-09-27.** `add_person`, `register_gauge`,
`calibrate_gauge`, `issue_certificate`, `issue_pallet_certificate`,
`produce_batch`, `pack_unit`, `set_unit_status` and `erp_retry` appeared in no
`agent.NEEDS` entry and no `assistant.SURFACES` entry, so they were offered to
anybody holding `plant.read` and only the API's gate against the `AGENT` account
refused them. Measured against `main` at `ad5219d3`: `NEEDS ∪ PER_CALL_NEEDS` is
now 47 of the 47 writes rather than 38, and the catalogue offered on `plant.read`
alone is 51 read tools rather than 60 with nine writes among them. It is recorded
here because more agent kinds would have multiplied it, and because it is the
second reason the first field of the row is the account.

### The build plan this decision commits to (2026-09-28)

Four milestones, in the order the maintainer's answers imply, each sized as
handoffs. They are consequences of this record, not a schedule, and none has been
opened.

| Milestone | What it is | What it proves |
|---|---|---|
| **M1 — the analysis agent, with graphing** | the four analyses as read tools returning the screens' own envelope; a kind with its own account and role and no write tool, asserted by a test; a chart contract of six rules — a chart draws what the API measured, every figure carries its coverage, unknown is drawn as unknown, axes are honest, every chart states its total, and the palette and all four themes; hand-drawn SVG through `kit.js`, no build step, nothing fetched from outside the box; off in shadow mode and saying so | an account and role of its own, a tool set defined by what it may not call, a per-conversation budget, and a place on the AI tab |
| **M2 — the `[ai]` domain and the person's own agent** | a fifth Configuration domain in the AI nav group holding budgets, cadences and the automation line; a `my agent` view behind `plant.read` that says out loud that a supervisor can read what you type; a durable transcript plus a short remembered-facts list the person can read and delete, with its total; per-agent budget shares | a budget that is the plant's rather than the box's, and the tab as a person's own place |
| **M3 — the improvement crew, warn-only** | the three unwired `review.KINDS` entries; a cadence in the API lifespan behind a no-model delta that advances its own watermark; proposals into the flows that exist and nothing that applies itself, for a release; `screens.define`, off by default, granted through the settings tools on a signature | all six fields against a real plant, including the three hardest: a cadence in a long-lived process, a plant-scoped budget, and a roster honest about a pass that did not happen |
| **M4 — the harness as the engine** | the checkout, the branch per proposal and the pull-request body that carries the finding; tier (b) UI changes as draft pull requests with a `browser` test that fails first; the crew's roles as kinds inside the product; and, after the warn-only release, the declared file set and the `ui-check` baseline question answered | amendment B — or disproves it |

**And the three numbers that would show amendment B wrong**, which do not exist
today and will after M3's warn-only release: how many of the harness's proposals
a person signed against how many were declined and ignored; how many evenings a
merged proposal cost end to end against what the same change would have cost by
hand; and whether anybody other than the maintainer ever merged one. If the first
is small, the second is not better than one-for-one and the third is zero, the
project's own strategy pages were right to rank this fifth of five.

Revisit when: a kind has run on a cadence against a real plant for a month and
the number of its proposals that were signed, declined and ignored is written
down. Not before — the field's own comparable projects split between blanket
approval of everything (approval fatigue on every routine action) and delegating
authorisation upward entirely, and the case for a tiered boundary has to arrive
with this plant's own numbers rather than with an argument.

## House rules touched
Rule 2 — unknown is a valid answer — is why a scheduled pass that did not run is
`unknown` and never a quiet *clean*, and why a roster row says `stale` rather
than `ok`. Rule 3 — agent-native, and an agent acts for a person — is the whole
of the second and third rules: the drafting half is what an agent may hold, the
approving half is what it may never hold, and the exceptions are bounded — tier
(a) by a five-clause test and off until an administrator says otherwise, tier (b)
by a declared file set, a test that fails first and a person's merge, and tier
(c) not at all. Rule 4 — config,
not code, at plant boundaries — is why a kind's cadence, budget and role are the
plant's configuration and not constants in the wheel.
