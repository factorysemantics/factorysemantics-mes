# Does the floor assistant do what people ask?

*The scorecard asks whether the MES told the truth about the plant. The
[evals](../agents/evals.md) ask whether an agent can find that truth through the
tools. This asks the third question, and it is the one a person standing at a
machine cares about: **I asked for something — did it happen?***

`tests/test_agent.py` pins the conversation loop. Reads run free, every write
pauses as a proposal, a confirmed write is idempotent and audited. All true, all
necessary, and none of it says whether the assistant, handed a sentence somebody
typed, reaches the right tool. Twice in two days it did not:

- **2026-09-25.** A request to change `default_job_minutes`. The proposal was
  right; the walk arrived on the Configuration page and stopped at the top of it,
  then said the control was not there "for your role". Fixed in #105.
- **2026-09-26.** A request to change the default reporting window. The setting
  exists; it was item 17 of a list of 22 that the tool result had cut at 6,000
  characters, so the assistant said the plant had no such setting, and then that
  no change was recorded. Fixed in #108.

Both were found by the maintainer, on his phone, over an evening. Nothing in the
repository would have caught either. This is what catches the next one.

## What the suite is

`tests/assist_suite/` — one TOML file per role, in the words people use. Each
case is a request as it was typed, the screen it was typed on, and one
expectation:

| Expectation | What it means |
|---|---|
| `propose` | the request is a change: offer exactly this tool, with the arguments the sentence named |
| `walk` | the request is "show me": walk them to the control, and to *that* control |
| `answer` | the request is a question: answer it from the plant |
| `read` | the request claims something changed, or asks what is recorded: look before answering |
| `refuse` | the request is not this person's to make: say so, and say who can |

Scoring is **strict on identity and loose on prose**. The tool name must match,
every argument key the request named must be present, and its value must be the
value the sentence gave — `1.33` and `"1.33"` are one number, `"1,2"` and
`"2, 1"` are one pair of rule numbers, `"CR"` and `"cr"` are one prefix, and
`55` is not `56`. Wording is matched as substrings, case-insensitively. A case
may say what it is `loose` about, and why, in `loose_about`.

## The two modes

They measure different things, and reading one as the other is the only way to
be misled by this.

### `fsmes assist eval --scripted`

Asks: **is this expectation reachable, through the real plumbing, for this
role?**

It runs the real guide router, the real tool catalogue for that role, the real
tools against a real seeded plant over the real API gates, the real surfaces, and
the real conversation loop — with the model replaced by a stand-in that plays the
expectation. So it cannot tell you whether a model would choose right, and it is
not pretending to. What it does catch:

- a request the guide router answers before the agent is ever asked;
- a tool this role is not offered, or is offered and should not be;
- arguments the tool will not take against a real plant;
- a "show me" with no walk behind it, or a walk that lands on the wrong control;
- a proposal reported as something already done;
- a conversation the hosted API would refuse from that point on — the stand-in
  validates the message history the way the real API does, which is what turns
  2026-09-26's `BadRequestError` into a failing case instead of a mystery.

It is deterministic, needs no key and no network, costs nothing, and takes about
two seconds. `tests/test_assist_suite_scripted.py` runs it, so the required
checks carry it on every pull request.

### `fsmes assist eval --live --plant <url> --user <code> --account <role>=<code>`

Asks: **does the model choose right?**

The real model, on a running plant, through `POST /assist/agent` — the same
endpoint the assistant panel posts to, so what is scored is what a person gets.
Every proposal it opens is **declined**, so a scored plant is an unchanged plant.

**One account per role.** `--account operator=SCOTT` says which account asks the
operator's cases; repeat it per role. Each one's password comes from
`MES_ASSIST_EVAL_PASSWORD_<ROLE>` — `MES_ASSIST_EVAL_PASSWORD_OPERATOR` — or from
`--password` when the code is the same as `--user`, so nobody has to put one
password in two variables. A role with **no account** is reported as such: not
asked, not scored, not paid for, and listed in the result file by name.

```bash
export MES_ASSIST_EVAL_PASSWORD=…              # ADMIN's, for --user
export MES_ASSIST_EVAL_PASSWORD_OPERATOR=…     # SCOTT's
fsmes assist eval --live --plant http://127.0.0.1:8010 --user ADMIN \
  --account admin=ADMIN --account operator=SCOTT --plant-commit "$(git rev-parse --short HEAD)"
```

`--user` is the account the arrangement is written for — its name goes on every
audited row — and whose budget the run reads. It is not a fallback for a role
nobody was named for.

Until 2026-09-27 there was one `--user` and it answered for everybody. A run of
the operator suite as the administrator measures the administrator: the
operator's refusal cases **cannot refuse** when somebody holding every capability
is typing, so they passed for the wrong reason and the number meant nothing. The
suite's four roles need four accounts to be scored honestly, and a plant that has
three of them gets three roles scored and the fourth reported as having nobody.
Nothing here creates an account: that is `create_user` on the Administration
screen, which an administrator does. If a plant is to be scored as a supervisor,
its pack should carry one.

It costs money. It stops at `--max-usd` (default $1.00) and says which cases it
did not run rather than reporting a short suite as a whole one; the month's cap
is in [BUDGET.md](BUDGET.md) and this is one line on it. It refuses to start
without `ANTHROPIC_API_KEY`, and refuses if the plant's own brain is off — with
the plant's reason, not a page of zeroes.

Run it against a **synthetic** plant. The result file quotes what the model did,
and that includes the arguments it chose; a real plant's codes are a real plant's
business.

A plant that was not built from the demo pack has almost none of what the suite's
sentences name, so most of it comes back *not arranged*. `--seed-masterdata` puts
that master data there as you, once — see below.

## What a live run puts on the plant, and what it never will

Almost every sentence in the suite names something: an order, a material, a
machine, a draft waiting for a signature. Asked "book 600 good on MIX01 against
WO-EVAL-1" on a plant that has neither, the right answer is *there is no MIX01
here* — and on 2026-09-26 the first live run scored nine of its nineteen
failures against a model that was saying exactly that. A run that does this is
measuring the plant, not the model.

So each case declares what it needs, and a live run **arranges the plant first**
(`--no-arrange` turns that off):

```toml
requires = ["material:FG-COLA", "order:WO-EVAL-1", "no reason:changeover"]
```

`no kind:code` is a requirement that the plant has *not* got one: "draft a
downtime reason `changeover`" is not a question you can put to a plant whose
vocabulary already holds one.

Anything the plant still has not got makes that case **not arranged** — it is
not asked, not paid for and not scored, and it is listed in the result file with
the reason **and with what would make it arrangeable**. Both halves, because
from the reason alone a reader cannot tell a plant that is short of one line in a
file from a suite asking for something no plant could give it, and those are
different problems with different owners. It is counted in its own column, apart
from pass and fail, so the required number cannot quietly drift.

### Arranged

Written by the **AGENT** account, naming the person the run signed in as, through
the product's own API — the same write path the suite is about, so a fixture that
stops working is a tool that stopped working. Every row lands in the audit trail
with both names on it.

| What | Code | Notes |
|---|---|---|
| A setting put in force twice | `[process] default_report_hours` → `10.0`, then `8.0` | So "who changed this, and when?" has something to find, **and** so "change the reporting window to 10hrs" is still a change. Two audit rows, one fixture. |
| A released work order | `WO-EVAL-1` | 1000 of `FG-COLA`. |
| Its first step, started | step 10 of `WO-EVAL-1` | *Mix*, on `MIX01`. So that "produce 2 units of FG-COLA on MIX01 for WO-EVAL-1" has one right answer: on an order whose first step has not begun, starting it before booking against it is a defensible first move, and the third live run made it. Step 20 is deliberately left waiting — asking to *start* a step only means something where one is. |
| A non-conformance | `NC-00001` | Opened by the MES itself, from a brix check recorded at 20.0 against a 9.5–11.5 specification. |
| A corrective maintenance order | `CM-00001` | On `MIX01`, summary *Infeed belt slipping (assist eval fixture)*. |
| A **draft** work instruction | `WI-EVAL-1` | Unapproved. *Logging a shift handover*. |
| A **draft** trigger | `TR-EVAL-1` | Unapproved. `MIX01` pressure above 6.5. |
| A **draft** downtime reason | `eval_awaiting_parts` | Unapproved: nothing labels a stop with it. |
| A **draft** non-conformance severity | `eval_scuff` | Unapproved: nothing is graded with it. |
| A **recommended** setpoint change | numbered by the plant | Unapproved: an engineer decides it and the agent never does. Only where the plant's tag manifest declares a writable setpoint with bounds on `MIX01` — a demo plant declares none, and then the case that needs one is reported *not arranged*, saying what would fix it: one tag under `tables.<the machine's object>.tags` in the plant's `tags.json` reading `{"kind": "sp", "writable": true, "min": <low>, "max": <high>}`. A line `fsmes.sim.generate` made carries them already. |
| Two identified units | `SN-EVAL-1`, `SN-EVAL-CASE-1` | Serials of `FG-COLA`, for the cases that pack one into another and quarantine a pallet with everything in it. |

Five of those are **drafts**, and a draft changes nobody's screen until somebody
signs it. Codes carry `EVAL-`/`eval_` wherever the code is ours to choose, so
what a run left behind can be told from the plant's own work at a glance. The
plant numbers the non-conformance, the maintenance order and the recommendation
itself; if it numbers the first two something else, the cases that name them are
reported not arranged with the code it did use.

**And each fixture is about something no case asks for.** The prefix keeps the
codes apart; the words have to be kept apart too. A run that drafted
`eval_cosmetic` and then asked the model to "draft a cosmetic severity" was
answered *"there's already a draft 'cosmetic' severity"* — correct, and scored as
a failure on 2026-09-27. Two tests hold that line now, and the same rule is why
the reporting window ends a run at 8.0 rather than at the 10.0 a case asks for.

### Never arranged by the agent

**Master data.** Materials, equipment, routings, lots and specifications are the
plant's own, and an agent deployment does not define them — decision
[0035](../decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md),
and the AGENT account does not hold `masterdata.write` to do it with. A plant
without `FG-COLA` and `MIX01` gets those cases reported *not arranged* rather
than having a cola line invented on it.

**The gauge register**, for the same reason and with a consequence worth naming.
A gauge is master data, so a run cannot put one on a plant — which means there is
no case proposing `calibrate_gauge`, only an operator being refused it. The demo
pack seeds no gauges; a plant with a gauge register is what a proposal case for
that tool is waiting on, not a code change.

**A dead ERP message.** `erp_retry` puts one back in the queue, and a dead
message is what an ERP that refused one leaves behind. A run that made one would
have to break the plant's ERP link on purpose, so `erp_retry` is in the suite as
an operator's refusal too.

That is why the suite belongs against a plant built from the **demo pack**. Point
it at a bottling plant and most of it comes back not arranged, which is the
honest answer and not a useful run.

### `--seed-masterdata`: the same master data, put there by a person

A person may define master data, and that is the whole difference. On 2026-09-27
somebody put this on a plant by hand with ten `POST`s; `--seed-masterdata` is
those ten `POST`s, made **as the account you signed in with**, over the plant's
own API — `/masterdata/equipment`, `/masterdata/materials`,
`/masterdata/routings`, `/quality/specs`, `/execution/lots`. Not through the
assistant's tools, not as AGENT, and nothing in the database that the API did not
put there.

```bash
fsmes assist eval --live --plant http://127.0.0.1:9030 --user ADMIN \
    --seed-masterdata --plant-commit "$(git rev-parse --short HEAD)"
```

**Off unless you ask for it.** It writes master data to a real plant, and the
run's result file lists every code that arrived, so whoever operates that plant
can see what turned up and when.

| What | Code | As the demo pack has it |
|---|---|---|
| A work centre | `LINE1` | Packaging Line 1. **No parent** — see below. |
| A work unit | `MIX01` | Mixer 01, under `LINE1`, ideal cycle 4.0 s |
| A work unit | `PACK01` | Packer 01, under `LINE1`, ideal cycle 3.0 s |
| A raw material | `RAW-SUGAR` | Sugar, kg |
| A raw material | `RAW-FLAVOR` | Cola Flavor Concentrate, l |
| A finished material | `FG-COLA` | Cola Syrup 1L, ea |
| A routing | `RT-COLA` | Make Cola Syrup, for `FG-COLA`: Mix 10 on `MIX01`, Pack 20 on `PACK01` |
| A specification | `FG-COLA` / `brix` | 9.5–11.5 °Bx |
| A lot | `LOT-SUGAR-001` | 500 kg of `RAW-SUGAR` |
| A lot | `LOT-FLAVOR-001` | 100 l of `RAW-FLAVOR` |

Those codes and those numbers are **not written down in the seeding**. They are
read back out of `fsmes.seed.seed_demo_plant` — the one place the demo plant is
defined — in a throwaway in-memory database, so what lands on a real plant is
what the demo pack says and cannot drift from it.

Three of the ten are not named by any case. `RT-COLA` is there because a work
order cannot be created against a material with no routing, so `WO-EVAL-1` —
which the AGENT account arranges — needs it first; `RAW-FLAVOR` and
`LOT-FLAVOR-001` are there because the demo pack ships a lot per raw material and
half a demo reads as a mistake to whoever finds it. Nothing else is seeded, and a
test fails if that list grows a row with no reason written beside it.

**`LINE1` arrives with no parent.** The demo pack hangs it under an area, under a
site, under an enterprise; a run has no business inventing four levels of
somebody else's hierarchy, so it lands as a root work centre with `MIX01` and
`PACK01` beneath it. Move it under your own site if you want it there — by hand,
because this does not.

**It creates, and never updates.** Every code is looked for first, and one that
is already there is reported *already there* and left exactly as the plant has
it. If your `FG-COLA` is a different product measured in litres, it stays that;
correcting somebody's master data to match a test suite is the last thing this
should do. A second run on a seeded plant writes nothing at all.

**It is refused, in one sentence, when the account may not do it.** The plant
asks for `masterdata.write` before it will take nine of these ten, so an account
without it is told that and nothing is written on the way to finding out. The two
**lots** are stock rather than a definition, and this product guards those with
`production.consume`; an account holding one capability and not the other gets
the eight definitions and is told which capability the lots wanted. An admin
holds both.

### Removing it afterwards

**It does not remove any of it, and there is no `--clean`.** An MES does not
delete an audited record; that is most of what an MES is for. A run that tidied
up after itself would have to reach round the write discipline the suite exists
to measure, and the tidying would not be in the trail.

What a person does instead, on a plant they want back:

- the **drafts** — retire them on their own screens (`/dashboard/reasons`,
  `/dashboard/severities`, `/dashboard/instructions`, `/dashboard/triggers`).
  They were never in force, so retiring one changes nothing that was running.
- the **recommended setpoint change**, if the plant could carry one — reject it
  on `/dashboard/adjustments`. Nothing was ever written to the machine: a
  recommendation waits for a person, and rejecting one is the person.
- `WO-EVAL-1` — cancel or close it. Its step 10 is running; closing the order is
  the one action, and nothing was booked against the step.
- `CM-00001` — complete it, or leave it: it is a corrective job on a mixer with
  the words *assist eval fixture* in its summary.
- `NC-00001` — disposition and close it. It is a real non-conformance about a
  real recorded check, and it stays in the record, as every non-conformance does.
- `default_report_hours` — a run leaves it at the product's default of 8.0. Set
  it back to what your plant had; `setting_changes` says what that was.

And about the **master data**, honestly: **the API has no way to remove any of
it.** There is no `DELETE` and no `PATCH` on `/masterdata/equipment`,
`/masterdata/materials`, `/masterdata/routings`, `/quality/specs` or
`/execution/lots` — not as an oversight this page is working round, but because
nothing in this product has needed to unmake a definition yet, and a machine or a
material with production booked against it is not a row anybody should be able to
drop. So a plant that has been seeded keeps `LINE1`, `MIX01`, `PACK01`,
`RAW-SUGAR`, `RAW-FLAVOR`, `FG-COLA`, `RT-COLA`, the brix specification and the
two lots until somebody with database access removes them, or until the plant is
rebuilt or restored from a backup taken before the run (`fsmes backup` /
`fsmes restore`). Nor can the two lots be put beyond use: `LotStatus` has a
`blocked` state and, as of 2026-09-27, nothing in this product sets it. The one
thing that softens this is that the codes are the demo plant's, so what a person
finds on their plant is recognisable rather than mysterious. **Seed a plant you
are willing to rebuild.**

Both halves are **idempotent**: a second run on an arranged, seeded plant creates
nothing twice and fails nothing. Point it at the same plant as often as you like.

## Reading a result

A live run writes `docs/ai/assist-eval/<date>.md`, the way a calibration run
writes `docs/ai/calibration/<date>/`:

- the mode, the model, the plant, the plant's commit and the suite's commit;
- what it cost in dollars and in tokens;
- a pass rate per role, **and which account answered for each** — a per-role
  number is about whoever was typing;
- every required case that did not pass, with the expectation, **what it did
  instead**, and a sentence per thing that was wrong;
- every case marked `not_yet`, and which handoff it is waiting on;
- every case that was **not arranged**, what the plant has not got, why, and
  what would make it arrangeable —
  followed by what the run put on the plant, what was already there, and
  anything it could not arrange, in the plant's own words;
- every case that was **not asked at all** because this plant has no account
  holding the role.
  anything it could not arrange, in the plant's own words. That last part comes
  in two halves, because two different accounts wrote them: what the **agent**
  arranged through the assistant's tools, and what **master data** the person
  seeded. When `--seed-masterdata` was not given, the file says so — an operator
  reading a run against their plant should be able to see that its master data
  was theirs.

A plant does not report the commit it is running, so `--plant-commit` is how that
gets into the file. A result with no build behind it cannot be compared to next
month's. `--json` writes the same run as data, for a month that wants to diff
rather than read.

`assist-eval/2026-09-27-scripted.md` is a **scripted** run, kept as the example
of the shape. It scores no model, and its file name says so — a live run writes
`<date>.md`.

## `not_yet`, and the ratchet

A case the current code *cannot* pass yet is marked in the suite:

```toml
expected = "not_yet"
handoff = "assist-one-brain"
note = """Why, in plain words."""
```

Those are counted separately, so the required number never drifts because
somebody wrote a case for a thing that does not exist. **The ratchet works both
ways**: the test fails if a `not_yet` case starts passing. That is not pedantry —
it is how a fix that landed gets recorded as a fix you can rely on, instead of
quietly improving a number nobody re-read.

## Adding a case

**A bug somebody finds in the assistant becomes a case here before it becomes a
fix.** That order matters: a fix with no case is a fix that can be undone by the
next refactor, and a case written after the fix tends to describe the fix rather
than the thing the person asked for.

1. Open `tests/assist_suite/<role>.toml`.
2. Paste the request **exactly as it was typed**, typing and all. `set it to4` is
   not a typo in that file.
3. Say the screen it was typed on, and the expectation.
4. `source` says where the words came from — a `questions/` file, a journal
   entry, an issue.
5. If the tool needs arguments the sentence did not name, put them in `fills`.
   They are never scored: "create an account for Jo Patel as an operator" does
   not contain a password, and scoring one would be scoring the suite.
6. If the expectation needs a read narrowed to something, `lookup` says what.
   It is offered to every read the case names and filtered to the arguments that
   tool takes.
7. Run `fsmes assist eval --scripted --case <id>`. If it cannot pass yet, mark it
   `not_yet` and name the handoff.

8. Say what the plant has to have for the sentence to mean anything, in
   `requires`. The kinds live in `fsmes.lab.assist_fixtures`, and a typo in one
   fails the build rather than quietly retiring the case.
9. Check the case does not describe the arrangement it will run on. A request to
   draft the word a run drafts, or to set the value a run sets, is a request the
   model is right to answer *that is already there* — and five of the six
   failures in the 2026-09-27 live run were exactly that. Two tests check the
   codes and the words; the values are yours to keep apart.

The plant every case is asked about is the demo plant plus what
`fsmes.lab.assist_fixtures.arrange` puts on it — the rows in *What a live run
puts on the plant* above. A scripted run arranges the same plant the same way,
so the arrangement is exercised on every pull request rather than only when
somebody spends money.

## Why this is worth shipping in the open

The field publishes capability. A vendor announces how many agents, how many
integrations, which copilot; checked on 2026-09-25, nobody publishes a rerunnable
score for whether the assistant does what a *named role* asks, with the failures
listed — see [the landscape](../LANDSCAPE.md). Tool counts are inventory.
Faithfulness is what a plant is actually buying.

This MES already keeps its judgment model's calibration in public under
`docs/ai/`, on the same argument: a number you publish and can be held to is
worth more than a claim.

## See also

- [The judgment model in the build loop](JUDGMENT-IN-THE-BUILD-LOOP.md) — the
  same discipline, pointed at the build rather than the assistant.
- [Budget](BUDGET.md) — the monthly cap a live run draws on.
- [Evals](../agents/evals.md) — can an agent find the truth through the tools.
- [The write discipline](../agents/write-discipline.md) — why every change is a
  proposal.
- Decision [0035](../decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md)
  — who may do what, and whose the approval is.
