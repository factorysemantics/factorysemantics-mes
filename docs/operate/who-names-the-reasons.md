# Who names the reasons

At 06:10 a machine stops. Somebody has to say why, and until this feature
existed the screen asked an open question and got an open answer: a text box
whose placeholder read *"e.g. jam at infeed, waiting on fitter"*, stored
exactly as typed.

The downtime pareto then grouped on that string and on nothing else. So
*jam*, *Jam*, *jam at infeed* and *infed jam* were four separate reasons, each
too small to act on, and **the real top reason on the line was invisible
because it was spelled four ways**. The operator did nothing wrong.

The other half of the same problem is that the list they should have been
picking from did not exist, and there was nowhere to put it. A reason
vocabulary is not equipment, not a material and not a routing, so
`masterdata.write` did not cover it — and of the built-in roles only `admin`
held any capability that wrote configuration at all. A process engineer who
wanted to name six downtime reasons had to be made administrator of the whole
plant, or had to find one.

Both halves are one failure: **the plant's vocabulary had no author.** This
page is how it gets one.

## The short version

1. Somebody holding `process.define` **drafts** a reason. Nothing changes on
   the floor.
2. It appears on the *Waiting for you* panel on `/dashboard` — for the people
   who can act on it, and for nobody else.
3. Somebody holding `process.approve` **puts it in force**. The row records
   who and when.
4. The station screen offers it at the next screen load, and the pareto groups
   on it.
5. Undo is one move: **approve the previous revision**. Nothing is deleted.

## The two capabilities

| Capability | What it grants | Who holds it as shipped |
|---|---|---|
| `process.define` | Draft the plant's process vocabulary | `admin`, and the `agent` role |
| `process.approve` | Put a vocabulary in force | `admin` only |

They are separate because drafting a list and putting it in front of every
operator on the site are different jobs, and because an administrator can now
compose a **process engineer** role that holds them without holding
`users.manage` or `masterdata.write`. A role is a named bundle of
capabilities and bundles are data, so that takes an afternoon on the admin
screen and no deployment.

The `agent` role holds the drafting half and never the approving half — the
same shape work instructions, triggers and setpoint adjustments already have.
An assistant that reads a month of what operators typed and proposes six codes
is the point of the feature; an assistant that signs its own proposal is not.

## Drafting one

```bash
curl -sX POST "$MES/equipment/downtime-reasons" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"code": "jam_infeed", "name": "Jam at the infeed",
       "description": "Something stuck where product enters the machine."}'
```

The `code` is what the analysis groups on and what leaves the MES: two to
forty characters, lowercase, starting with a letter. The `name` is what the
operator reads on the button. The `description` is the sentence shown beside
it, which is how two reasons that read alike on a button can still be told
apart at the machine.

**Validated before approval, not after.** A code is unique, it is spelled the
way a topic segment is spelled, and it may not be a word the product already
owns — a state (`running`, `down`), a capability, a role, a KPI. The list is
read from the product itself rather than typed anywhere, so a word that
becomes product vocabulary next month is protected the day it lands. A reason
that spelled the same word as something the product owns would mean two things
at once, and a fleet comparing two plants could not tell which.

Drafting the same code again edits the open draft: nobody is choosing from it
yet.

## Finding one waiting

This is the step the product's three existing approval lifecycles were
missing. A work-instruction draft, a trigger draft and a proposed adjustment
each wait on a screen somebody has to know to open, and **nothing tells
anybody** — there is no inbox, no aggregate count and no mail, webhook or push
of any kind. Meanwhile drafting sits with supervisors and agents while
approving sits with administrators, so the proposer and the approver are
usually different people.

`GET /dashboard/pending-approvals` answers *what is waiting that I may act
on*, from the caller's live capabilities, and the **Waiting for you** panel on
`/dashboard` renders it: the kind, the code, what it is, who drafted it and on
whose behalf, **how long it has waited**, and one action that signs it.
Counted, and with the whole queue's total rather than the page's.

The rule it answers to:

> A pending item appears on the screen of the role that can act on it,
> counted, with its total — and on no screen that cannot act on it.

So a person who can approve nothing never sees the panel at all. And a draft
nobody acts on **waits, visibly**: it does not expire, it does not go live by
itself, and the only thing that changes with time is one column, so a
forgotten draft reads as *"11 days"* rather than falling off the end of a list.

One kind is behind that endpoint today — the downtime vocabulary. Work
instructions, triggers, setpoint adjustments and the design-chat notes join
the same panel next; each already has a lifecycle, and joining is a row in
`PENDING_KINDS` and a reader, not a new mechanism.

## Approving, and undoing

```bash
curl -sX POST "$MES/equipment/downtime-reasons/jam_infeed/approve/1" \
  -H "Authorization: Bearer $TOKEN"
```

The approved revision supersedes the one before it and the operator's dropdown
changes at the next screen load. To undo, approve the previous revision. The
revision that was in force becomes *superseded*; nothing is deleted and no
history is rewritten.

## Retiring a code

A vocabulary shrinks as well as grows. Retiring is a revision like any other,
with `"retires": true`, and it is refused unless the draft says how many
recorded intervals carry the code:

```
jam_infeed labels 412 recorded intervals. Say so in the draft
(`labels_intervals`) before retiring it: a code leaves the list with somebody
having looked at what it already labels, or it leaves it blind.
```

Because of the rule that makes this cheap:

> **Retiring a code changes what may be chosen next. It never changes what was
> chosen before.**

An interval labelled `jam_infeed` keeps that label when `jam_infeed` leaves the
list, and the pareto keeps showing it. A record is not un-made — the same
principle [decision 0029](../decisions/0029-an-order-does-not-finish-itself.md)
applies to an order that over-ran. So the worst outcome of a vocabulary
somebody regrets is a month of stops labelled with words they regret, which is
exactly the situation a plant with a text box is already in.

## On the floor

Once the plant has an approved vocabulary, **going down takes a code from
it**. The station screen shows a select instead of the text box, and the API
refuses a typed sentence with the reason why. That is deliberate: a list
nobody has to use is a suggestion sitting beside the text box that caused the
problem.

It is not a demand to invent something. The shipped starting vocabulary
carries an explicit **not yet determined** — a stop labelled that way is
counted, can be found again and can be re-labelled, which is more than a blank
box ever gave anybody.

A plant that has approved nothing is unchanged: there is no list, so the text
box is what it has and the text is what it gets.

**Three of the four writers are untouched.** A trigger composing
`"Infeed jam (Alarm_Word=1)"`, an inbound feed carrying the incumbent system's
own reason column, and an agent tool all keep writing text, and the pareto
says how much of the window came from each rather than pretending. (The OPC
agent is a fifth thing and supplies no reason at all: every stop a machine
reports by itself lands unlabelled. That is the cold start, and it is why the
vocabulary has to come from somewhere other than the machines.)

## What the pareto says afterwards

`GET /analysis/downtime` groups by code where there is one and by text where
there is not — the two never merge — and states **both totals**:

| Field | What it is |
|---|---|
| `from_the_list_seconds` | downtime labelled with a code from the approved list |
| `typed_seconds` | downtime labelled with text somebody typed |
| `unlabelled_share` | downtime nothing labelled at all |
| `vocabulary_total` | how many reasons are currently on the list |

With the unlabelled share, the three account for every second of downtime in
the window. One number alone would let a plant with six codes and a thousand
typed sentences look like a plant with six reasons, and *how much of this
window came from the list* is the number that says whether the vocabulary is
being used.

## What a plant starts with

A pack may carry `masterdata/downtime_reasons.json`
([plant packs](packs.md)). The three lab packs and the cutlery demo ship six
words — breakdown, changeover, micro stop, starved, blocked, and not yet
determined — shaped after the ISO 22400 categories a plant will recognise.
They are a **starting** vocabulary: the plant owns the list from its first
edit, and `fsmes pack apply` never rewrites a code that is already there.

## What this does not do yet

- **The clustering step.** The assistant reading the last thirty days of what
  operators typed and proposing *"these 40 spellings look like 6 reasons"* is
  the follow-up. It needs a plant with a month of people typing; the lab plants
  are driven by the OPC agent, which supplies no reason, so they hold no typed
  history to cluster.
- **The coverage dry run** that would report what a proposed list *would have*
  covered, for the same reason.
- **The analysis screen** does not yet print the two totals above; the API
  carries them today.
- **Scrap** has no reason column at all. That is a second pilot.

## See also

- [Reading OEE](../plant/reading-oee.md) — where the unlabelled bar comes from
- [Plant packs](packs.md) — shipping a vocabulary with a plant
- [Configuration is authored by roles](../design/config-assistance.md) — the
  design this is the first piece of, and
  [decision 0035](../decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
