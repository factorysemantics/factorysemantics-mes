# Who names the severities

Every non-conformance in this MES carries a severity, and until this feature
existed nothing said what a severity could be. The column was twenty
characters of free text with a default of `minor`, and the only two words in
the product were the two its own code wrote: a recorded check outside its
specification opened a `minor` one, an SPC rule 1 firing opened a `major`
one. Nothing validated anything else. A trigger could write any string at
all, and the trigger catalogue's own help offered `critical` — a word nothing
in this product has ever written and no plant had on any list.

That is free text by accident rather than by design, and it is the same
failure the downtime reasons had: **a field a plant fills in with no list
behind it.**

This page is the second half of [who names the
reasons](who-names-the-reasons.md), and it is deliberately the same page
again. If you have read that one you already know this one: the lifecycle,
the two capabilities, the panel, the walk and the undo are the same
machinery. What is different is three things, and they are all at the end.

## The short version

1. Somebody holding `quality.define` **drafts** a severity, on **Quality ›
   Configuration › Non-conformance severities** (`/dashboard/severities`) or
   through the API. Nothing changes on any record.
2. It appears on the *Waiting for you* panel on `/dashboard` — for the people
   who can act on it, and for nobody else.
3. Somebody holding `quality.approve` **puts it in force**. The row records
   who and when.
4. From then on a new non-conformance may only be raised at a word on the
   list.
5. Undo is one move: **approve the previous revision**. Nothing is deleted,
   and no record is re-graded.

## The two capabilities

| Capability | What it grants | Who holds it as shipped |
|---|---|---|
| `quality.define` | Draft the plant's quality vocabulary | `admin`, and the `agent` role |
| `quality.approve` | Put a quality vocabulary in force | `admin` only |

Separate for the reason every pair in this product is separate: writing down
what this plant calls a serious finding and putting that in front of every
quality record are different jobs. An administrator can compose a **quality
engineer** role that holds both without holding `users.manage` or
`masterdata.write`; a role is a named bundle of capabilities and bundles are
data, so that is an afternoon on the admin screen and no deployment.

The `agent` role holds the drafting half and never the approving half.

## The screen

**Quality › Configuration › Non-conformance severities**
(`/dashboard/severities`) is one row on the Quality workspace's
**Configuration** page (`/dashboard/config/quality`). It lists every word the
plant has ever had — on the list, retired, drafted and unsigned — with how
many non-conformances each one grades, and the form that drafts the next one
is there for somebody holding `quality.define` and absent for everybody else.

Typing a code that already exists drafts its next revision; typing one that
has a draft open edits that draft.

## A plant with no list behaves exactly as it always did

This is worth saying plainly, because it is the rule the whole configuration
effort runs on: **the literal in the source is the shipped default,
unchanged.** A plant that has approved no severity is not a plant with a
broken feature. Its `severity` column takes whatever is written, exactly as it
did before any of this existed, and nothing about a record it raises is
different.

The moment a plant has **one** word in force, the column stops being free
text: a new non-conformance may only be raised at a code on the list, and the
refusal names the list rather than the rule.

**Records raised before the list keep their word.** Nothing is read and
nothing is rewritten. A hold graded `showstopper` last March is still graded
`showstopper`, on the record and on any certificate that printed it, whatever
the plant's list says now. Retiring a word changes what may be graded next and
never what was graded before — the same sentence the downtime vocabulary
keeps about an interval it labelled.

## The two words this product writes itself

`minor` and `major` are not the plant's to remove. The product opens
non-conformances by itself:

| Word | Who writes it |
|---|---|
| `minor` | a recorded check outside its specification, and SPC rules 2, 3 and 4 |
| `major` | SPC rule 1 — a point beyond three sigma |

So a plant whose list lacked one of them would find out at the moment a
machine raised a hold, with nobody watching. Instead the refusal is moved to
the only moment a person is present:

- **Retiring one is refused** on the screen, with the sentence saying which
  code path writes it. The Retire button is absent rather than offered and
  refused.
- **A pack that seeds a list without one is refused by `fsmes pack check`**,
  offline, where a person is reading and can fix it.

It is a refusal to *retire*, never to rename. The **code** is the key; the
**name** and the sentence beside it are the plant's and always were. A plant
that calls a major finding a *Show-stopper* changes the name, keeps the code,
and every screen reads its own word.

## What a plant starts with

A pack may carry `masterdata/nc_severities.json`
([plant packs](packs.md)). The three lab packs and the cutlery demo ship
exactly two words — `minor` and `major` — because those are the two the
source writes today. Shipping a third to be helpful would be inventing a
plant: `critical` and `observation` are words some plants use, and this
product has never written either.

A packed vocabulary arrives **in force**, not as a draft. Applying a pack is a
deliberate act by a person, and `fsmes pack apply` never rewrites a code that
is already there. Every change after the first apply goes through
draft → approve like everything else.

## What this does not do

- **It does not rank.** There is no numeric order behind the words, and a
  triage queue sorted by severity is the plant's own reading of its own list.
  Asserting that one plant's `major` is another plant's `major` is exactly
  what this product will not do.
- **It does not decide which rules raise a hold.** That is
  [decision 0036](../decisions/0036-the-chart-draws-every-rule-the-plant-chooses-which-hold.md)
  and `[quality] hold_rules`, listed on the same Configuration page.
- **It does not decide which rule is a major finding.** Which SPC rules open
  a major non-conformance rather than a minor one is `[quality] major_rules`
  — rule 1 alone by default, which is what the source does. See
  [this plant's own quality numbers](quality-numbers.md).
- **There is no severity input on any screen.** There never has been: nothing
  in this product has ever offered a person a severity to pick. The places a
  severity is written are the two product code paths above and a trigger's
  `open_nc` action parameters, and that last one is now checked against the
  plant's list like everything else.

## See also

- [Who names the reasons](who-names-the-reasons.md) — the first vocabulary,
  and the same loop
- [Plant packs](packs.md) — shipping a severity list with a plant
- [This plant's own quality numbers](quality-numbers.md) — the numbers, as
  opposed to the words
- [Configuration is authored by roles](../design/config-assistance.md), and
  [decision 0035](../decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
- [What is still hard-coded](../design/config-audit-2026-09-21.md) — where
  this was row **Q3**
