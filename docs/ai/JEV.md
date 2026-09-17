# Jev in this project

**Status: analysis, 2026-09-17. Nothing here is built and nothing here is
decided.** Two decisions are proposed alongside it —
[0031](../decisions/0031-a-judgment-is-a-proposal.md), what a probability may
touch, and [0032](../decisions/0032-a-hosted-judgment-and-the-shadow.md), what
leaves the box — and this page is the survey they rest on. The open question
it exists to answer is the one that has not been settled: whether a typed
judgment model belongs **in the product**, **in the development of the
project**, in both, or in neither. It is written so that each candidate can be
taken or refused on its own, and so that refusing all of them is a defensible
outcome that leaves a record of why.

## What Jev is, in this project's words

TypeSafe published Jev on 2026-09-16. It is a *System One* model: you send
application state and a map of named, typed questions, and it returns typed
answers with probabilities. It writes nothing, reasons out loud about nothing,
and cannot be asked an open question.

Three primitives, and the whole surface:

| Primitive | Returns | The shape of question it answers |
|---|---|---|
| `Choice` | the chosen option, a probability distribution over every option, and a confidence | which of these named things is it — up to 255 options |
| `Noul` | one probability, 0 to 1 | does this condition hold |
| `Score` | a probability-weighted position on ordered, described levels, plus the distribution and a confidence | how far along a described dimension — 2 to 10 levels |

The published numbers: about 100 ms median, $0.042 per million input tokens
with output free, a combined budget of roughly 32,000 tokens for state and
questions together, and no documented cap on the number of questions in one
request. Questions over the same state are evaluated in parallel and cannot
see each other's answers, which is why TypeSafe's own measurement puts 13
batched questions at 12x cheaper and 10x faster than 13 calls with identical
answers.

The reason it is worth a page here rather than a line in the backlog is that
this codebase is already written in its idiom. `src/fsmes/services/oee.py`
enumerates three candidate explanations for counted work that will not fit
inside the run time and declines to pick one — decision
[0026](../decisions/0026-counts-that-outrun-the-run-time.md) is that refusal,
ratified. `src/fsmes/services/analysis.py` files every stop with no reason
into `UNLABELLED` and reports the share.
`src/fsmes/integrations/opc/tag_map.py` raises on a PLC state code it was not
given rather than call it idle, because "silently calling it 'idle' would
quietly corrupt every availability figure". Over and over, the source writes
out the options and then honourably refuses to choose. A `Choice` is precisely
an answer to a question of that form, with the refusal preserved as a
distribution rather than thrown away.

## What Jev cannot do, and which ideas that kills

Worth stating before the catalogue, because four of the obvious ideas die here.

- **It selects; it does not extract.** Values must already exist as options.
  Anything that needs a number, a date, an id or free text pulled out of a
  string needs code or an LLM to produce the candidates first, with Jev used
  as the verifier. TypeSafe's own extraction cookbook is built that way.
- **Text and JSON only.** No images. So the standing wish in
  `CONTRIBUTING.md` house rule 6 — that a rendered chart gets checked by
  looking at it — is not answered by this model. What *can* be judged is the
  structured state behind the chart, which is a different and weaker claim.
- **It is not deterministic.** TypeSafe's consistency cookbooks report a
  per-question probability standard deviation near 0.01 and label flips on 2
  of 8 questions across repeated identical calls, before thresholding. This
  repository's CI gate is a deterministic simulated plant on two operating
  systems and two Python versions. Those two facts do not go in the same
  process.
- **Confidence is not accuracy.** It is a statistic over the answer's own
  distribution, and TypeSafe does not publish the formula. In their worked
  classification example, answers above 0.9 confidence were right 90 % of the
  time — which is a useful separation and a poor licence.
- **There is no on-premises option.** Hosted, US-only, as of 2026-09-17. See
  the egress section.

## What it would cost here

Input tokens are the entire bill and the state is re-sent on every call, so
cost is set by how often a question is asked and how much state it needs, and
by nothing else. Rounded, at the published $0.042 per million:

| Workload | Calls | State per call | Per day |
|---|---|---|---|
| Scored-run log triage, every run | ~24 | ~3,000 tokens (the 12 KB log tail already sent to qwen) | under a cent |
| Downtime reason proposals, a busy line | ~200 stops | ~2,000 tokens | about 2 cents |
| Assistant guide routing | ~500 questions | ~1,500 tokens | about 3 cents |
| Nightly rollup, design triage, sweeps | tens | small | rounding error |
| **Per-unit inspection judgment, cutlery plant** | **10,000,000 pieces** | ~500 tokens | **about $210** |

The last row is the useful one: it is the only candidate in this document that
cost alone refuses, and it refuses it decisively. Everything else is inside the
existing $10 a month cloud cap in [BUDGET.md](BUDGET.md) with room to spare,
which means cost is not an argument for or against any of the rest — the
arguments are all about correctness and consent.

The dependency is not free even where the calls are. The SDK is
`typesafe-sdk`, Python 3.10+, built on `msgspec` structs and an `httpx2`
client, where this codebase is pydantic and `httpx`. That is an adapter at the
boundary and two more trees in the lock file, and by README
principle 5 — runs anywhere — it belongs in an optional extra,
`fsmes[judgment]` alongside `fsmes[agent]`, and never in the base install a
plant PC gets. The SDK's
defaults also need overriding rather than inheriting: a 10-second default
timeout with two retries is a 30-second worst case in front of a service whose
median is 100 ms, which on an operator screen at 06:00 is an outage.

---

# Part one — the build loop

Everything in this part sends **synthetic or repo-internal state**: a
deterministic simulated plant, run logs, scorecards, baselines, backlog notes,
Scott's own design chat. No customer's data is involved, so the consent
question that dominates part two does not arise at all, and the only real
constraints are decision 0031's rule that nothing runs inside the gate, and
the flat fact that every one of these is currently answered either by a
threshold somebody tuned or by a human reading output. One exception is marked
where it occurs.

### D1 · Run-log triage, which today is an LLM and a regex
**Today.** `src/fsmes/sim/triage.py` hands about 12 KB of log tail to
`qwen3:8b` and asks for a JSON array of findings; `_parse_findings` scrapes
`[...]` out of whatever the model wrapped it in, and an unparseable reply is
recorded as *no findings*. The run's `worst` field is then `max()`
over severity strings the model chose itself.
**The judgment.** A fixed battery of `Noul`s per run — retry storm, counter
went backwards, a component stopped reporting, silent exception, deadlock —
plus one `Score` for run health on the `high | medium | low` ladder already in
the code. The battery is the schema, so there is nothing to parse.
**If it's wrong.** Feeds `sim/autoloop.py` and the night brief, bounded by
`MAX_BUILDS = 3`. A false finding costs one night's build slot.
**For.** This is the only place in the build loop where free text from a model
is already load-bearing, and its failure mode is silent: a parse error and a
clean run are indistinguishable today. Typed answers delete
`_parse_findings`, the "no findings" ambiguity and the string ladder in one
change, and the state is the most obviously safe input in the repository.
**Against.** The battery is fixed, so a novel failure nobody wrote a question
for goes unseen — where an open prompt can at least be surprising. The honest
mitigation is to keep one open-ended qwen pass beside the battery and compare
what each finds for a few weeks.
**Verdict.** The cheapest true win in the document. Start here, keep the
qwen pass running beside it, and let the comparison be the evidence.

### D2 · Whether a scored event was scoreable at all
**Today.** `src/fsmes/sim/score.py` decides with `SAMPLES_TO_RESOLVE = 2` and a
`late` heuristic whether a missed event counts against the MES. The comments
record that decision being wrong twice, each time discovered by a person
reading the output; `sim/runner.py`'s `withhold_verdict` blanks every
headline metric on one trigger.
**The judgment.** A `Choice` over `{ MES was blind, MES was late, harness
behind its own sample, inside a scripted disconnect, event too brief to
resolve, genuine miss }`, with a distribution, replacing a cascade of booleans
that collapses to one answer.
**If it's wrong.** It corrupts the number the entire harness exists to
produce. The highest blast radius in this part.
**For.** House rule 2 is straining hardest here. "Unknown" in the scorer is
currently a binary produced by thresholds, when the thing it wants to express
is a probability — and this is the one place in the project where a calibrated
number could gate what a raw instrument reading cannot. Every other figure in
the apparatus is downstream of it.
**Against.** Decision 0031 says a judgment may not touch a graded number, and
scoreability *is* the graded number's gate. Taken literally that rules this
out, and the literal reading is right: the harness is the instrument that
would otherwise be measuring TypeSafe.
**Verdict.** Build it as a second opinion recorded beside the existing
thresholds, never in place of them, and compare the two over many scored hours.
If the judgment and the thresholds disagree and the judgment is right, that is
the evidence that earns a later argument. It is not an argument to start with.

### D3 · Agent-eval scoring, which is a regex over uppercase tokens
**Today.** `src/fsmes/sim/agent_eval.py` decides pass or fail with
`re.findall(r"[A-Z][A-Z0-9_-]{1,}", answer.upper())` — and because the string
is upper-cased first, the character class is inert and any uppercase token
counts. An agent that names the right machine in a sentence containing a
distractor scores zero.
**The judgment.** One `Noul`: does this reply name exactly the truth set and
no distractor.
**If it's wrong.** It misleads the "is the MES getting more usable by agents"
trend. Gates nothing.
**For.** The measurement is currently partly a measure of answer formatting,
which makes the trend it feeds hard to read. Each run is already a paid Claude
session, so a fraction of a cent per answer is not the objection. Small,
self-contained, obviously better.
**Against.** It replaces a broken deterministic check with a good
non-deterministic one, so the trend line gains a noise floor of its own. Worth
recording the probability, not just the boolean, so the noise is visible.
**Verdict.** Yes, if anything in this part ships. Lowest risk of the lot.

### D4 · Style-drift triage on UI baselines
**Today.** `src/fsmes/sim/ui_check.py` files every computed-style delta on a
watched component as a backlog note with `status: inbox` — "ui-check never
judges - it only reports". `WATCHED` has been pruned by hand twice because
runtime data leaked into snapshots and filed false findings.
**The judgment.** A `Choice` per drift over `{ deliberate restyle — accept,
regression, runtime data and not a style fact }`, given the delta, the route,
the theme and `docs/design/STYLE.md`.
**If it's wrong.** A false "deliberate — accept" hides a real regression
behind an accepted baseline, and the night agent may auto-merge UI-scoped
changes, so this one has a path to `main`.
**For.** The third class — runtime data mistaken for a style fact — is exactly
what the two manual prunings were, and it is a semantic distinction a diff
cannot make.
**Against.** The auto-merge path means a wrong accept is durable. Should
propose a status, never write `accepted`.
**Verdict.** Worth it as a ranking that decides which notes a person reads
first. Not worth it as a status.

### D5 · Judging a screen without looking at it
**Today.** Screenshots "are captured and kept as evidence beside the run, but
they are NOT diffed for pass/fail", because the plants are alive and every
screenshot differs. House rule 6 — charts get checked by looking at them — is
enforced by one person's eyes.
**The judgment.** Not the picture: Jev takes no images. But the state behind
it can be interrogated — a `Noul` for "this KPI strip shows a number outside
the range the API returned", another for "this table is empty when the
endpoint returned rows", another for "this axis range makes the comparison
misleading" — assembled from the page's fetched JSON and the DOM text the
crawler already has.
**If it's wrong.** Misleads a human. Gates nothing.
**For.** It converts part of house rule 6 from a discipline into a check,
which nothing else in the repository does. And the local GPU has no second
slot for a vision model, so a structured check is the only one available.
**Against.** It answers a weaker question than the rule asks. A chart can be
convincing, wrong, and perfectly consistent with the JSON behind it — bad axis
choices and misleading encodings survive this check. Claiming rule 6 is
covered would be worse than leaving it uncovered.
**Verdict.** Useful, and must be named for what it is: a consistency check
between a screen and its own API, not a look at the chart.

### D6 · Design-chat triage, which is a whole session per batch
**Today.** `src/fsmes/services/design_triage.py` records
`TRIAGE_MODEL = "claude-code-triage"` — a verdict is "written by a Claude Code
session, not by a model the server called". The skill requires reading
`ARCHITECTURE.md`, `ROADMAP.md`, `src/fsmes/domain/` and the screen source
before picking one of seven statuses.
**The judgment.** A `Choice` over the existing `STATUSES`, plus the two
`Noul`s the contract actually turns on — does this need a schema change, does
this contradict a ratified decision — plus a `Score` for "clear what right
looks like without asking Scott".
**If it's wrong.** Misroutes an idea to `rejected`, or wastes a night's build
budget. Every verdict lands back in the conversation that produced it, so it
is reviewable.
**For.** This is the closest thing in the repository to the thing Scott
actually asked for out loud: queue the ideas and have the hard ones surface.
Used as a pre-rank rather than a decision, it means a session reads the
ambiguous ones and rubber-stamps nothing.
**Against.** The statuses turn on whether an idea contradicts a *ratified
decision*, which means the state has to include the decisions — thirty-two of
them now — and that is a real chunk of the 32,000-token budget. Judging
against a summary of a decision is not judging against the decision.
**Verdict.** Strong candidate, as a pre-rank feeding the existing session.
Never as the verdict; `TRIAGE_MODEL` should keep saying who decided.

### D7 · Night-shift build selection
**Today.** `src/fsmes/sim/autoloop.py` assembles the brief deterministically —
"deliberately all machinery and no judgment" — and a headless session picks
"the three clearest wins" under a prose contract.
**The judgment.** A `Score` per brief item for how clear a win it is, and one
hard `Noul`: does this item touch the data schema, the API contract,
`src/fsmes/sim/`, auth or capabilities — which is the contract's
non-negotiable bound.
**If it's wrong.** A false "does not touch the schema" lets the night agent
build against a bound it was meant to respect. Partly contained by the merge
path filter.
**For.** It gives an unattended prose judgment a number that can be logged,
thresholded and reviewed the next morning. Right now the bound exists only in
a skill file.
**Against.** A bound enforced by a probability is not a bound. The correct
implementation of that `Noul` is a path filter in code, and the honest reading
of this candidate is that finding it is an argument for writing the filter,
not for asking a model.
**Verdict.** Take the `Score`. Refuse the `Noul` and write the path check
instead.

### D8 · Semantic checks `fsmes pack check` cannot make
**Today.** `src/fsmes/pack/masterdata.py` is "structure only. Whether
`RT-BRACKET` names a routing that makes sense for this plant is the plant's
question", and `check.py` reports four things as `Unknown` by design.
**The judgment.** One `Noul` per semantic assertion a validator cannot reach —
an operation order that is physically impossible, `ideal_cycle_seconds` that
disagree with the tag map (the exact condition the module's own comment says
makes OEE performance meaningless), a shift pattern that does not cover the
line, a BOM quantity an order of magnitude out — surfaced as a new tier
between `Problem` and `Unknown`: a probable problem.
**If it's wrong.** A false positive that refuses a pack blocks a plant coming
up, which is why it must be advisory only — a shape the existing `Unknown`
type already models.
**For.** This is the artefact a controls engineer drafts on site, and the
failures it cannot catch are the ones that make every downstream number
meaningless while looking fine.
**Against.** **This is the one candidate in this part whose state may be a
customer's.** A lab pack is synthetic; a real plant's `masterdata/` is that
plant's configuration, and decision 0032 classifies it accordingly. It also
runs in `fsmes pack check`, which decision 0031 names as a gate.
**Verdict.** Build the tier, gate it behind an explicit flag and outside
`check`'s exit status, and keep it off for any pack that is not a lab's.

### D9 · Lab review, where a blocklist stands in for judgment
**Today.** `src/fsmes/lab/review.py` asks the local model for an eight-word
cluster heading and drops any title containing one of fifteen `FORBIDDEN`
words, because "a model asked for a label will happily offer a verdict". The
only ranking `findings.md` has is clusters-seen-in-most-runs-first.
**The judgment.** A `Score` per cluster: how strongly do these rows look like
one mechanism rather than a coincidence. An ordering is not a verdict about
which side is right, so the module's own rule survives — and the tests that
pin it should be read before assuming so.
**If it's wrong.** Misleads a human reading `findings.md`. The lowest-stakes
item in this document.
**For.** It replaces a keyword blocklist, which is a poor proxy for the thing
it is protecting, with a number that does not name a culprit.
**Against.** Very low value. Cluster ordering is not where the time goes.
**Verdict.** Fine. Last.

### D10 · What the nightly rollup decides is worth surfacing
**Today.** `src/fsmes/sim/rollup.py` computes everything and asks qwen for at
most six sentences. But *what counts as a change* is three hardcoded
thresholds — `abs(delta) >= 0.01`, `> was * 1.25`.
**The judgment.** Jev cannot write the note. It can replace the constants: a
`Score` per plant for "worth opening this morning", given today's numbers, the
prior window and the triage findings.
**If it's wrong.** A missed item is a missed morning read, recoverable that
night.
**For.** Three magic constants currently decide what Scott sees at 05:30, and
they were chosen without data.
**Against.** A tuned threshold with a known failure mode may be better than an
untuned probability with an unknown one. Worth a month of logging both before
switching.
**Verdict.** Worth doing after D1, because the triage findings it would
consume get better first.

### D11 · Difference-significance bands in the lab
**Today.** `src/fsmes/lab/measure.py` uses `WINDOW_TOLERANCE` and
`SAMPLES_TO_SEE` to decide whether a MES-versus-truth disagreement is reported
at all: "a difference inside a band the run itself cannot vouch for is not in
here".
**The judgment.** A `Noul`: is this difference explained by the stated band —
carrying the run's own overlap and window facts, which a fixed share cannot.
**If it's wrong.** Corrupts `findings.md` in both directions: suppresses a
real difference, or cries wolf.
**For.** The band is honest and blunt; the facts that would sharpen it are
already computed and thrown away.
**Against.** This is measurement, and measurement wants arithmetic. If the
overlap and window facts can sharpen the band, they can sharpen it in code.
**Verdict.** Refuse, and write the arithmetic. A good example of a seam that
*looks* like a judgment and is not one.

### D12 · Sweeps, whose output nobody interprets
**Today.** `src/fsmes/sim/sweep.py` exists because "a sweep says which
conditions it stops being honest under — which is the question that tells you
what to build next". The command writes a comparison JSON and prints it;
finding the cliff is a human read of N scorecards.
**The judgment.** A `Noul` per variant for "honest at this setting" — a
monotone curve a probability can express and a pass/fail cannot — and a
`Choice` over the variants for where honesty breaks.
**If it's wrong.** Misleads what gets built next. Gates nothing.
**For.** The sweep's stated purpose has no automated consumer at all today.
**Against.** The cliff is visible in the numbers if anything plots them, and a
chart is cheaper than an integration.
**Verdict.** Plot it first. Revisit if the plot is genuinely ambiguous.

---

# Part two — the product

Every candidate here sends a plant's data, so each one carries a state class
from decision [0032](../decisions/0032-a-hosted-judgment-and-the-shadow.md):
**catalogue** (product text, identical everywhere), **configuration** (what
this plant is), **observation** (what the line did), **production** (counts,
orders, measurements, and anything a person typed). Shadow mode allows
`catalogue` and refuses the rest; `production` is off by default everywhere.
That ordering, not the interest of the idea, is what decides how early a
candidate can be reached.

They are ranked by the same test: how much the refusal costs today, against
how little a wrong answer costs.

### P1 · A proposed reason for a stop nobody labelled — the flagship
**Class: observation.**
**Today.** `src/fsmes/services/analysis.py` files a DOWN interval whose
`reason` is null into the `unlabelled` bucket and reports
`unlabelled_share`. `services/equipment.py`'s `label_stop` only ever writes a
label another system supplied and refuses to create an interval. On a line
with OPC UA and no downtime terminal, every stop is unlabelled, forever.
**The judgment.** A `Choice` over the reason vocabulary *this plant has
already used* — the distinct labels on its own history, not a taxonomy the
product invented — with an explicit `none of these` option, given the decoded
alarm word, the state and its duration, the neighbouring machines' states, the
operation running, and the plant's own past (reason, alarm bits, duration)
triples. Plus a `Noul` for "this is the same event as the upstream machine's
stop", which is what turns twelve rows into one cause.
**If it's wrong.** Under decision 0031, nothing: the proposal is a new row
with its own source, `EquipmentState.reason` stays null, `unlabelled_share`
reports exactly what it reports today, and the pareto is unmoved until a
person accepts. A wrong proposal costs a supervisor one glance.
**For.** House rule 3 is the most honest rule in the project and also the one
that helps a plant least — it refuses to lie and then offers nothing. This is
the seam where "unlabelled" could become "probably a blocked infeed, and here
is the alarm bit that says so, and you decide". The options are the plant's
own words, so the model is not being asked to know manufacturing — it is being
asked to match this week's stop against last month's labelled ones, which is
the kind of narrow judgment the primitive is for. Cost is around two cents a
day on a busy line.
**Against.** The reason vocabulary only exists if the plant has already
labelled stops, so the plant that needs this most — OPC UA and no terminal —
is the plant where the `Choice` has nothing to choose from. That is a real
cold-start problem and the answer is probably the ISO 22400 / incumbent
categories as a starting vocabulary a plant edits, which is a plant pack
question and therefore house rule 4, not a model question. Separately, a
proposal a supervisor accepts a hundred times a shift becomes a booking by
fatigue; the accept path must be one row at a time, with no bulk accept, and
that is a UI decision as much as an architectural one.
**Verdict.** The strongest product candidate, and the one worth building
first — *after* the vocabulary question is answered, and with the scoring
harness as the evaluation set, because the simulated plants script their own
breakdowns and therefore know the true reason for every stop. That is an
unusually good position to be in: the accuracy number can be produced before
any plant is asked for anything.

### P2 · Which of the three explanations applies, where the code already lists them
**Class: observation** (cycle-time master data and counts, no order codes).
**Today.** `src/fsmes/services/oee.py` holds `COUNTS_OUTRUN_RUN_TIME` and
`COUNTED_OUTSIDE` and names only the cause it has evidence for; decision 0026
is the ratified refusal to name a culprit.
**The judgment.** A `Choice` over `{ rating too slow, run time undersampled,
units counted outside the run time }` with a distribution, given the rating,
the counted units, the observed run time, the OPC publish interval, the
machine's state-change frequency and the unknown seconds.
**If it's wrong.** Display only, if it stays in `performance_note`.
**For.** This is the purest fit in the repository. The options are already
enumerated in the source, in English, as a tuple. The distribution *is* the
honest answer the decision wanted and could not express: not a culprit, a
weighting. And on a shadow-mode plant the OPC publish interval is exactly the
kind of thing nobody looks at until it has been wrong for a month.
**Against.** Small prize. It changes a sentence on a screen. And decision 0026
deliberately chose to name the disagreement rather than a cause, so this
reopens a settled call — which is fine if the distribution is presented as a
weighting rather than as an answer, and not fine if it reads as one.
**Verdict.** Build it as the demonstration case. Cheap, low-stakes, and it
shows the pattern — enumerated refusal becomes a distribution — in the place
where the project has already written the options down and can therefore
check the answers.

### P3 · How serious an excursion is
**Class: production** (measurements, material codes, quantity at risk).
**Today.** `src/fsmes/services/spc.py` sets
`severity="major" if signal["rule"] == 1 else "minor"`, and
`services/quality.py` defaults to `minor`. Nothing looks at the size of the
excursion, the material, or how many units are at risk.
**The judgment.** A `Score` on described levels — minor, major, critical — over
the window blob the code already stores, the spec limits, the quantity at
risk, whether serialisation is in play, and the open NC history for that spec.
**If it's wrong.** It is a stored field on `non_conformances` that reaches a
customer's certificate through `services/coa.py`, so a wrong severity is a
wrong document, not just a wrong sort order.
**For.** The primitive is an exact match for the thing severity is: an ordered
ladder of described situations. A two-branch map on rule number is not a
severity model, and everyone using it knows that.
**Against.** It reaches a CoA, and it is `production` class, so it is off by
default at every plant and refused in shadow mode. Also worth asking whether
the honest fix is arithmetic: severity as a function of the excursion in sigma
and the units at risk is a formula a quality engineer can argue with, and a
probability is not.
**Verdict.** Try the formula first. If the formula needs the material's
history and the spec's context to be right, that is when the `Score` earns
its place.

### P4 · Which guide the assistant should show, and whether to show one at all
**Class: catalogue.**
**Today.** `src/fsmes/services/assistant.py` decides show-versus-answer with a
hand-written regex alternation of English phrasings, and picks a guide with
`_lexical_match` — "crude on purpose: overlapping words", a two-word set
intersection — when Ollama is down. The docstring records that qwen3:8b was
tried as the gate and kept routing wrongly, which is why the hard regex gate
exists.
**The judgment.** A `Noul` for "this person wants to be walked through a task
rather than told an answer", and a `Choice` over the visible guide catalogue.
Typed output deletes the id-parsing entirely.
**If it's wrong.** Display only: a wrong guide is closed by the reader.
**For.** **This is the only product candidate that is `catalogue` class**, and
therefore the only one reachable on a shadow-mode plant. The state is the
question plus guide titles and their `when` lines — product text that ships in
the wheel. It replaces two hand-written heuristics with one call, and the
distribution is what would let the hard regex gate relax rather than grow
another phrasing every month.
**Against.** The question text is an operator's free text and could name an
order or a machine, which means the class is only `catalogue` if the code
proves it — the question goes in the state verbatim, so a strict reading makes
this `production` the moment somebody types "why did WO-100235 stop". That is
either a redaction step with its own failure modes, or an honest
reclassification. Do not hand-wave it.
**Verdict.** The best first product build *if* the free-text problem is
answered honestly. If it cannot be, this becomes a mid-ranked `production`
candidate and P2 goes first.

### P5 · Whether the assistant's facts actually contain the answer
**Class: production** (the facts blob is live plant state).
**Today.** `services/assistant.py` hands a facts blob to qwen with "Never
invent a number" in the prompt and takes up to four sentences back.
**The judgment.** Not the prose — Jev writes nothing. The two guardrails: a
`Noul` for "the supplied facts contain the answer to this question", which
drives an honest refusal instead of a confabulation, and a `Noul` for "this
answer restates only numbers present in the facts", run on the reply before it
reaches the screen.
**If it's wrong.** A false pass lets through the exact failure the prompt is
fighting. A false fail shows a refusal where an answer was available.
**For.** A hallucinated number on a floor screen is the worst failure mode in
the product, and it is currently prevented by a sentence in a prompt.
TypeSafe's citation-check and cascade cookbooks are both this shape, and the
second `Noul` is the one that matters: verifying an answer against the facts
it came from is a narrow, checkable judgment.
**Against.** It puts a cloud call in front of an operator screen, on a path
that is `production` class and refused in shadow mode — so on the sites where
this matters most the guardrail is absent and the local model answers
unguarded, which is worse than not having built it. The deterministic version
of the second check is to extract the numbers from the reply and assert each
appears in the facts, which is regex work and needs no model.
**Verdict.** Write the deterministic number check. Keep the first `Noul` on
the list for later; it is the one code cannot do.

### P6 · Alarm bits the manifest never named
**Class: configuration.**
**Today.** `services/tags.py` surfaces an unnamed alarm bit as the literal
string `bit 7`, deliberately — a bit the manifest forgot is still an alarm —
and it reaches the alarms screen and the history uninterpreted.
**The judgment.** A `Noul` per candidate meaning, or a `Choice` over the
labelled stops the bit co-occurs with — which makes this the evidence supply
for P1 rather than a feature of its own.
**If it's wrong.** Display only.
**For.** Pairs directly with the flagship: the alarm word is the best evidence
for a stop's reason, and a third of it is currently unreadable.
**Against.** A bit's meaning is in a manual on a shelf, not in the data. This
infers a name from correlation, which is a plausible guess and not a fact, and
naming it on a screen makes it look like documentation.
**Verdict.** Do it as an input to P1, not as a label on a screen.

### P7 · Which tag is a machine's primary process value
**Class: configuration.**
**Today.** `services/analysis.py` falls back to "whatever this machine
published most recently that is not on a hand-maintained list of eight
structural names". `kernel/tags.py` records that the list lagged and the screen
charted `ReadyBit`.
**The judgment.** A `Choice` over the machine's published tag names, using the
manifest's kind, unit, min, max and nominal, plus per-tag sample statistics —
with a confidence floor below which it asks an engineer instead.
**If it's wrong.** Which trend a screen draws.
**For.** The hand-maintained exclusion list is house rule 4 inverted:
plant-specific knowledge living in the product's source. A judgment, or a tag
map entry, both move it out.
**Against.** The tag map is the right home for this and already exists. The
model's job is to *draft* the entry at commissioning, not to answer at request
time — which turns this into P9's shape.
**Verdict.** Merge into P9. Nothing should be inferred per request that a
commissioning engineer could have declared once.

### P8 · Drafting the mapping from an incumbent's export
**Class: production**, but at commissioning time and reviewed before use.
**Today.** `integrations/erp/incumbent.py` needs a human to write four
dictionaries translating another system's column headings and codes into this
MES's, and `integrations/inbound/folder.py` the same for stream mappings.
**The judgment.** A `Choice` per column — which canonical field is this
heading — and per code, over this MES's own order and equipment codes, given
the header row, a sample of values and the documented meaning of each
canonical column.
**If it's wrong.** Nothing at runtime. The output is a config file a person
edits and `fsmes pack check` validates before it touches a plant.
**For. This is the best risk-adjusted candidate in the product half.** It is
the only one whose output is reviewed by a human before it can affect anything,
it runs once per plant rather than continuously, and it attacks the work that
actually makes a first plant slow — sitting with somebody else's CSV export
working out what `QTY_CONF` means. Shadow mode is not even engaged: this
happens before the shadow starts.
**Against.** It sends a sample of a plant's real export, which is order codes
and quantities, so it is `production` class and needs the plant's consent —
obtained once, at commissioning, in a conversation that is happening anyway.
And a mapping that is 90 % right may be worse than a blank sheet if the
reviewer trusts it.
**Verdict.** Build this second, after P2. Present it as a draft with the
distribution shown per column, so the reviewer's attention goes to the
uncertain ones.

### P9 · Drafting a tag map for a PLC nobody documented
**Class: configuration**, at commissioning, reviewed before use.
**Today.** `integrations/opc/tag_map.py` raises on a state code it was not
given, because "silently calling it 'idle' would quietly corrupt every
availability figure".
**The judgment.** A `Choice` over `EquipmentStateName` per unmapped code, and
a `Choice` per tag name for its role in the map — proposed into a draft tag
map, never applied.
**If it's wrong.** Nothing, if it stays a draft. Everything, if it is applied:
a wrong state mapping corrupts availability silently, which is the failure the
raise exists to prevent.
**For.** Commissioning a tag map is the longest pole in standing a plant up,
and the raise — correct as it is — is the thing a controls engineer hits at
17:30 on day one. A draft map with a distribution per entry is a better
starting point than a blank file and a `KeyError`.
**Against.** The gap between "draft" and "applied" is one commit, and this is
the one place where a mistake corrupts every number downstream without
announcing itself. It needs the draft to be structurally incapable of being
loaded — a separate file, a different extension, a refusal to read it — not a
comment asking people to check.
**Verdict.** Worth it, with the draft made unloadable by construction. Absorbs
P7.

### P10 · Which of this MES's gauges the supplier meant
**Class: production.**
**Today.** `services/inbound.py` records that a reading came from a gauge this
MES does not know and says it "is untraceable here", then stops. Nothing
attempts a match.
**The judgment.** A `Choice` over the registered gauges — code, name, kind,
location — for the supplier's unknown code, with a `none of these` option.
**If it's wrong.** It sets `QualityCheck.gauge_id`, which is what
`services/gauges.py` walks on a failed calibration. A wrong match widens or
narrows a recall.
**For.** An untraceable reading is a real gap, and instrument-code matching
across two systems is exactly the entity-alignment problem the primitive
handles well.
**Against.** Recall scope is not a place for a probability. The right answer is
a gauge alias table in the plant pack — house rule 4 again — and a proposal
that drafts *that* table, reviewed once, rather than a match made per reading.
**Verdict.** Reshape as a draft alias table at commissioning. Refuse at
runtime.

### P11 · Maintenance findings, which are typed and never read
**Class: production** (free text a named person wrote).
**Today.** `services/maintenance.py` stores what a fitter typed and surfaces it
in an `ILIKE` substring search. Nothing derives a failure mode, a recurrence
or a plan change from it.
**The judgment.** A `Choice` over a failure-mode vocabulary; a `Noul` for
"this repeats the last job on this machine"; a `Score` for how strongly the
finding argues the plan interval is wrong.
**If it's wrong.** Display and planning advice, unless it retunes an interval,
which must stay a proposal.
**For.** It is the largest body of free text in the product and it is
write-only. Recurrence detection in particular is something a substring search
cannot do and a person cannot do across a year.
**Against.** It is one person's words about their own work, going to a US
service with no published retention period, and "we send what the fitters type
to a third party" is a sentence a works manager will react to. The vocabulary
also has to come from somewhere, and inventing one in the product is house
rule 4 inverted.
**Verdict.** Genuinely valuable, genuinely the most sensitive. Park until the
retention question has a written answer.

### P12 · A disposition recommendation, and an audit of the reason given
**Class: production.**
**Today.** `services/quality.py` requires a person to pick a disposition and
type a reason; `disposition_reason` is stored and never read by any code.
Decision 0024 is what gives a non-conformance its life.
**The judgment.** A `Choice` over `NcDisposition` as a recommendation a
supervisor confirms. Separately — and more interestingly — a `Noul` over the
typed reason: does this reason justify the disposition chosen. That is an
audit signal for `use_as_is` concessions, which are exactly what somebody has
to defend a year later.
**If it's wrong.** The recommendation touches scrap-versus-rework, a material
action. The audit signal touches a compliance report.
**For.** The second half is the better idea and nobody has proposed it: a
field that is mandatory, stored, and unread is a control that does not exist.
**Against.** A model reviewing a supervisor's justification is a surveillance
feature wearing an audit badge, and how it lands depends entirely on who sees
the number. It needs a decision about that before it needs an implementation.
**Verdict.** The recommendation: no, decision 0024 put a person there on
purpose. The reason audit: worth a discussion that is about people, not
architecture.

### P13 · The SPC verdict, which is a cliff at Cpk 1.33
**Class: production.**
**Today.** `services/spc.py` branches at `cpk >= 1.33` and `cpk >= 1.0`, so
1.329 and 1.331 read as different worlds.
**The judgment.** A `Score` on the ordered levels the code already writes out
— capable, marginal, stable but not capable, out of control — weighted by the
distribution, with confidence reflecting `n`.
**If it's wrong.** It is the sentence a quality engineer quotes, and it reaches
the pallet certificate.
**For.** A step function over a noisy estimate is a known bad instrument, and
the levels are already described in the source.
**Against.** Cpk 1.33 is not an arbitrary constant — it is the number the
customer's own quality agreement names. Softening an industry threshold into a
probability is not honesty, it is a different claim, and a quality engineer
will say so. The honest fix is a confidence interval on Cpk from `n`, which is
arithmetic.
**Verdict.** Refuse. Compute the interval.

### P14 · Whether the process followed a write
**Class: observation.**
**Today.** `services/adjustments.py` decides followed-or-not with
`FOLLOWED_FRACTION = 0.5` — travelled over distance — justified by a
first-order-lag rule of thumb.
**The judgment.** A `Noul`: did the process move because of this write, given
the full trace across the verification window, the tag's documented lag, and
whether the machine changed state mid-window.
**If it's wrong.** Sets VERIFIED versus FAILED and writes an audit row. A
false FAILED raises a finding against a good write.
**For.** The confidence *is* the useful output here, and a boolean from one
ratio discards the trace it was computed from.
**Against.** This sits on the only path in the product that reaches a PLC.
Decision 0031 permits it — verification is not the write — but a probability in
the audit trail of a setpoint change invites the question of what the number
meant, and the answer has to be a stored distribution, not a verdict.
**Verdict.** Defensible, and last on the product list. Nothing near the PLC
path should be the place a new dependency is proven.

### P15 · How much to believe a promised finish date
**Class: production.**
**Today.** `services/scheduling.py` falls back to
`DEFAULT_CYCLE_SECONDS = 3.0`, sequences by `priority, code`, and flags
`uses_default_cycle` as a crude binary — after a `getattr` typo once sized
every slot in every schedule at the fallback.
**The judgment.** A `Score` for how confident the promised finish is, given the
routing's setup and run seconds, the machine's rating, staging shortages, open
maintenance orders and the schedule's own history.
**If it's wrong.** Advisory by design — the module's own docstring says an MES
that refuses production because it disagrees with a plan is one people route
around.
**For.** A promise with a stated confidence is more useful than a promise, and
the inputs are all already computed.
**Against.** Finite-capacity scheduling has sixty years of literature and the
answer is arithmetic over known quantities, not semantic judgment. There is
nothing here a model knows that the data does not say.
**Verdict.** Refuse. Fix the binary flag into a computed band.

### P16 · The rest, briefly
| Candidate | Class | Judgment | Verdict |
|---|---|---|---|
| Work-instruction draft pre-approval — `services/drafting.py` writes prose a person must approve | production | `Noul`: does this draft use only the supplied facts and invent no tolerance | **Yes, eventually.** It is the one guardrail on text a model wrote that reaches a procedure, and "invents no tolerance" is not checkable in code |
| Maintenance *due soon* — `services/maintenance.py` uses `0.8 <= fraction < 1.0` | observation | `Score` on described urgency, using runtime, alarms and prior findings | Worth it only after P11 gives it findings to read |
| Tag staleness — `services/tags.py`, `STALE_AFTER_SECONDS = 60.0` against a change-driven set | configuration | `Noul`: has this tag actually stopped arriving | Refuse. Change-driven versus periodic is declarable in the tag map |
| Inbound booleans — `integrations/inbound/folder.py` has literal true/false word sets | production | `Noul`: does this cell mean pass | Refuse at runtime; fold into P8's draft mapping |
| Order hold reason — `services/workorders.py` requires free text nothing reads | production | `Choice` over hold categories | Low value; the categories belong in the pack |
| Uncharted inspection attributes — `services/serialization.py` names them and drops them | production | `Noul`: is this attribute worth a specification, from the pass rate conditioned on its values | Yes, **on aggregates only** — see the refusals |

---

# Part three — the ones to refuse, and why the refusal matters

These are the uses that would save the most work. Writing down why each is
refused is the point of this section, so that taking one later requires new
facts rather than new enthusiasm.

**Which order made these units.** `src/fsmes/services/execution.py` takes the
first non-DONE operation on a machine by `priority, id`, and
`services/assistant.py` admits in prose that with two orders queued the
default is a guess. A `Choice` over the candidate operations would be better
than the sort — and it would be a booked number computed from a probability,
which is decision [0004](../decisions/0004-never-invent-production.md)
reversed and decision 0031 broken. The honest fix is the one the tag map
already supports: a `publishes_order` tag, so the machine says which order it
is running. Where the PLC cannot say, the answer is
`record_unassigned`, which is what it does now.

**Assigning orphan production.** `unassigned_production` in the same module
lists rows with no order and has no assignment path in the runtime; they wait
for a person forever. A ranked proposal is admissible under 0031 and a bulk
accept is not — and at any real volume a proposal with an accept-all button
*is* a bulk accept. If this is built, it is one row at a time, the
distribution is on the screen, and the person's name goes in the audit spine.
Anything else is booking production from a model and calling it a
confirmation.

**Which order a supplier's measurement belongs to.** `services/inbound.py`
refuses when nothing is open on a station: it "is unknown and is not guessed".
A `Choice` would turn the refusal into a proposal — but the consequence is a
row on `quality_checks` that can open a non-conformance and trip SPC, which is
a plant action by a chain of three steps. Refuse for the same reason as the
first, and note that the supplier naming the order in its own payload is the
fix and is a boundary-format question.

**A judgment as a gate on a setpoint write.** `services/adjustments.py` has
three guards and a person is the second. A judgment *beside* the human is P14
and is admissible. A judgment that raises the ceiling — that lets an
adjustment through on confidence — is the one thing in this document that could
hurt somebody, and no accuracy number should be accepted as an argument for
it.

**Anything per unit.** `services/serialization.py` handles a plant serialising
ten million pieces a day. A judgment per unit is about $210 a day at the
published price, a cloud round trip in the line's critical path, and a US
service holding a piece-level record of another company's output. Judgments
over serialised production are aggregate-only: over a shift's distribution, a
pallet, a `fail_mask` pattern — never over a piece.

---

# What would have to be true before any of this ships

The project's own standard is the one to apply, and it is higher than the
industry's: **nothing on a page in this repository is a number from a real
plant unless it says so.** A judgment model arrives with somebody else's
benchmarks, and none of them are about manufacturing.

1. **An accuracy number produced here.** The simulated plants script their own
   breakdowns, changeovers, counter resets and micro-stops as named regression
   tests, which means for P1 the true reason of every stop is already known.
   That is an evaluation set most people integrating this model will not have.
   Build the labelled set from the scripted hours first, and publish the
   confusion matrix in this repository. **Done, 2026-09-17.** The set is
   built and the matrix is published on
   [the calibration page](JEV-CALIBRATION.md#numbers): 305 windows from six
   recorded runs, asked of `jev-1.13.0`, **right on 137 of 305 (0.449)**
   where the state word naming the reason is withheld and **281 of 305
   (0.921)** where it is left in. The reports are checked in beside the page.
   The item is met; what it produced does not support a threshold, and
   precondition 2 says why.
2. **A calibration plot, not a threshold.** TypeSafe's documentation says to
   plot confidence against accuracy on your own data and pick thresholds from
   it. Until that plot exists for a given question, that question has no
   threshold, and "0.8 seemed reasonable" is not one. **Answered in writing to
   the maintainer, 2026-09-17:** how `confidence` is computed is not
   published, and the number will drift across model versions — TypeSafe
   describes it as a probability distribution rather than a stable formula.
   That settles this item rather than softening it. A number whose
   computation is unpublished and whose behaviour moves with the version can
   only be calibrated against this project's own labelled data, which is what
   this item already asked for; no threshold may be taken from the vendor at
   any confidence. [D1](JUDGMENT-IN-THE-BUILD-LOOP.md) stores the probability
   with every answer for exactly this purpose, so the plot has its inputs from
   the first run. **The plot exists, 2026-09-17:**
   [stated confidence against measured accuracy](JEV-CALIBRATION.md#calibration-stated-confidence-against-measured-accuracy),
   ten bins, counts printed, empty bins left empty. Its answer is a refusal:
   expected calibration error 0.196 by probability and 0.159 by stated
   confidence on the default view, and the tool's own sentence is that no bin
   has ten or more samples at or above it and is right 90% of the time
   throughout, so no threshold is defensible anywhere on that scale. This
   item is met and it is met with a no.
3. **The model pinned, not `-latest`.** A judgment stored against
   `jev-latest` cannot be reproduced. Pin the served version, store it with
   every answer, and treat a version change as a re-validation. **Answered in
   writing to the maintainer, 2026-09-17:** a version can be pinned, and
   there is no fixed forced-retirement window. So the mechanism this item
   needs exists — [D1](JUDGMENT-IN-THE-BUILD-LOOP.md) pins `jev-1.12` and
   stores the version the API says it served with each answer — and the
   absence of a retirement window cuts both
   ways: nothing forces a re-validation on a date, and nothing promises a
   pinned version will still be served next quarter. A pinned version that
   stops answering is a case the code has to survive, not a case to be warned
   about in advance.
4. **A written answer on retention.** **Answered in writing to the
   maintainer, 2026-09-17,** and the answer has a price on it. Zero data
   retention exists, on the enterprise tier only; no retention period for any
   other tier was given. Rate limits are 250,000 tokens per second and 1,200
   requests per minute, with no availability commitment beyond them. The
   answers are dated on
   [the compatibility table](../operate/compatibility.md), which is where a
   plant will look for them.

    What it changes: every class above `catalogue` in decision
    [0032](../decisions/0032-a-hosted-judgment-and-the-shadow.md) —
    `configuration`, `observation`, `production` — is now gated on the
    enterprise tier or on that plant's written acceptance that its request
    bodies are retained for a period nobody has stated. That is a commercial
    gate on a technical decision, and it is a real one: it means the cheapest
    way to try a `production`-class question is not available at any price
    short of the top tier. `catalogue` is unaffected, because text that ships
    in the wheel is identical at every plant and its retention costs nothing.

    The deployment answer is the harder half. Hosted API only, no
    on-premises, VPC or edge option now or planned, and no public EU region —
    United States only. Air-gapped lines and sites running in shadow beside
    an incumbent cannot use this at all, at any tier, which is what the
    survey predicted and is now a fact with a date rather than a guess. Those
    are the plants with the strongest case for it.
5. **The deterministic fallback tested as the normal case.** Every consumer
   keeps what it does today, and the test suite exercises the no-key,
   no-network, refused-by-class path as the default rather than as an edge.
6. **`fsmes shadow` prints it.** A plant should be able to ask what would
   leave and get a list, per class, from the software rather than from a
   person.

# A phasing that commits nothing

Each step is independently abandonable, and each produces evidence rather than
a feature.

**Step 0 — no code.** Send TypeSafe the retention, rate-limit and SLA
questions. Settle the P1 cold-start vocabulary question, which is a plant pack
question and does not involve the model at all. Decide whether the assistant's
question text can honestly be `catalogue` class.

**Step 1 — the build loop only.** D1 run-log triage, beside the existing qwen
pass, with both recorded. D3 agent-eval scoring. No plant data, no shadow
question, no product surface. If Jev is unimpressive here, stop: this is the
cheapest possible place to find that out.

**Step 2 — the labelled set.** Build the P1 evaluation set out of the scripted
hours and measure. Publish the number whatever it is. This is the step that
decides the product half, and it needs no plant and no customer.

**Step 3 — the demonstration case in the product.** P2, the three
explanations. `observation` class, display only, a distribution presented as a
weighting. It exercises the whole mechanism — the module, the extra, the
register entry, the state class, the stored provenance, the fallback — against
the lowest-stakes consequence in the product.

**Step 4 — the two that pay.** P8, the incumbent mapping draft, and P1, the
downtime proposal, in that order, because P8's output is reviewed by a person
before it can matter and P1's needs step 2's number to justify itself.

**Not scheduled.** Everything in part three, and every `production`-class
question until step 0 has written answers.

# Questions to put to TypeSafe in writing

**Answered.** All six were put to TypeSafe and answered in writing to the
maintainer on 2026-09-17. The answers are recorded under each question below,
and the operational half of them is a row on
[the compatibility table](../operate/compatibility.md). Nothing here was
measured by this project; these are the vendor's own statements, dated.

1. What is the retention period for request bodies, and is there a
   zero-retention or no-log option?

    **Answer, in writing to the maintainer, 2026-09-17.** Zero data retention
    is available, and only on the enterprise tier. No retention period for the
    other tiers was given. So the option exists and it is priced: see
    precondition 4 above for what that gates.

2. What are the rate limits, and is there an availability commitment?

    **Answer, in writing to the maintainer, 2026-09-17.** 250,000 tokens per
    second and 1,200 requests per minute. No availability commitment beyond
    those limits was given, so there is no SLA to hold to and every consumer
    keeps its deterministic path.

3. Is there any on-premises, VPC or edge deployment now or planned? Without
   one, the plants with the strongest case for this — air-gapped lines, sites
   running in shadow beside an incumbent — cannot use it at all.

    **Answer, in writing to the maintainer, 2026-09-17.** Hosted API only.
    No on-premises, VPC or edge option now or planned. The consequence is the
    one the question predicted and it is unchanged by anything else here.

4. Is a version such as `jev-1.12` pinnable and for how long is it served?

    **Answer, in writing to the maintainer, 2026-09-17.** Yes, a version can
    be pinned, with no fixed forced-retirement window. No commitment on how
    long a pinned version is served follows from that, so a version
    disappearing stays a case the code has to survive.

5. How is `confidence` computed, and is the computation stable across model
   versions?

    **Answer, in writing to the maintainer, 2026-09-17.** The computation is
    not published, and the number will drift across model versions.
    TypeSafe's own framing is that it is a probability distribution rather
    than a stable formula. That is the answer this page assumed: see
    precondition 2 above.

6. Is there an EU region, and what is the current subprocessor list?

    **Answer, in writing to the maintainer, 2026-09-17.** No public EU region;
    hosted in the United States only. No subprocessor list was given, so that
    half of the question is still open and a plant that needs one has to ask
    for it itself.

# If the answer turns out to be no

It is a defensible outcome and worth stating plainly, because half of this
document argues for arithmetic over inference. Several seams that look like
judgments are measurements wearing a costume — the SPC cliff wants a
confidence interval, the scheduling promise wants a computed band, the lab's
significance bands want the facts they already discard, the night-shift bound
wants a path filter. Finding that out is worth the survey on its own.

What would remain true even then: this codebase enumerates its refusals in
source, in English, more than any other project of its size, and those
enumerations are a design asset. Whether they are eventually resolved by a
hosted model, by something that runs on the box, or by a plant's own
accumulated labels, the work of writing down *what the options are and what
evidence would decide between them* is the part that does not go stale. That
work is done now, and it is this page.
