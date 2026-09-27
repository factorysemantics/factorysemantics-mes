# 0038 — An agent in this plant is an account with a role, a budget, a cadence and a data class, and what it produces is a proposal

- **Status:** proposed
- **Date:** 2026-09-27
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

**One narrow exception, off by default, and it is fifteen numbers.** An agent
may put a change in force without a signature only when all five of these hold:
it changes a number a screen runs at; it changes no record's meaning; it changes
nobody's capability; nothing a PLC reads changes, directly or through a cadence a
write depends on; and it is undone in one step, from the screen that shows it, by
the path that made it. Today that set is exactly the `[screens]` pack table. It
is gated by a capability of its own that an administrator grants deliberately,
per plant, and every such change writes an audit row with the agent as actor and
a trace row beside it.

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

**Nine of the 47 write tools are outside the per-person capability filter.** `add_person`, `register_gauge`, `calibrate_gauge`,
`issue_certificate`, `issue_pallet_certificate`, `produce_batch`, `pack_unit`,
`set_unit_status` and `erp_retry` appear in no `agent.NEEDS` entry and no
`assistant.SURFACES` entry, so they are offered to anybody holding `plant.read`
and only the API's gate against the `AGENT` account refuses them. More agent
kinds multiply that hole.

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
approving half is what it may never hold, and the one exception is bounded by a
five-clause test and off until an administrator says otherwise. Rule 4 — config,
not code, at plant boundaries — is why a kind's cadence, budget and role are the
plant's configuration and not constants in the wheel.
