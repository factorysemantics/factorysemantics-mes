# Measuring the judgment model on this project's own runs

**What this page is about: the development build loop, and nothing else.**
No plant sends anything anywhere because of what is described here. No
screen, no API route, no agent tool and no plant data path asks a judgment
model anything, and shadow mode refuses the call outright.

This is step 2 of the phasing in [JEV.md](JEV.md): *build the P1 evaluation
set out of the scripted hours and measure; publish the number whatever it
is.* Step 1 — the questions asked beside the deterministic answers — is
[the build loop page](JUDGMENT-IN-THE-BUILD-LOOP.md). This page is what
decides whether any of those probabilities may ever be compared with a line.

The two decisions underneath stay as they are:
[0031](../decisions/0031-a-judgment-is-a-proposal.md) — a judgment is a
proposal — and [0032](../decisions/0032-a-hosted-judgment-and-the-shadow.md)
— a hosted judgment is an outbound path, classified by the state it sends.
Both are still *proposed*. **Nothing on this page chooses a threshold, and
the tooling it describes cannot choose one.**

## Why this project can measure this at all

Most people wiring a judgment model into an MES have no way of knowing
whether its answers are any good, because nobody wrote down why each machine
stopped. This project does: the simulated plants script their own
breakdowns, changeovers, counter resets, micro-stops and idling as named
regression tests, in the `line/<plant>.json` the generator obeys. That file
is not documentation — it is the input — so **the true reason for every stop
in a recorded run is already known**.

That makes an evaluation set available here that most integrators of this
model will never have.

## The labelled set

`fsmes jev labelled-set` reads one or more lab results directories and emits
one record per window: the window, the machine, the reason it actually had,
the state the MES could see around it, and what the run's own scorecard said
about it.

### Where a label comes from

The generator writes one state word per machine per simulated second, and
its precedence is fixed and readable in `fsmes.sim.generate.simulate`:
changeover, then breakdown, then a pre-rolled micro-stop, then scripted
blocking, then scripted starving, then the blocking and starving the line's
own buffers produce. So the state word **is** the truth about why a machine
stopped, and the label is read off it rather than inferred from it.

Whether a scripted event *named* the window is recorded separately, in
`scripted`. A knock-on starve is a real stop with a real reason; it is
simply one nobody wrote down in advance, and a reader needs to see how much
of a set is staged and how much is emergent.

### The vocabulary

Seven options, taken from the generator's own event types and state words
rather than invented. A fixed vocabulary's total is the measure of what it
cannot say: a stop whose real reason is not one of these seven can only be
answered wrongly.

| Option | What it means |
|---|---|
| `breakdown` | it was willing to run, something failed |
| `changeover` | a planned stop, and never downtime |
| `micro_stop` | a jam or a misfeed, cleared in seconds |
| `starved` | nothing arrived; the shortage was upstream |
| `blocked` | nowhere to put it; the hold-up was downstream |
| `counter_reset` | the counters went back to zero without a stop |
| `none` | it did not stop in this window; there is no reason to give |

### What the question is allowed to see

The label is read off the machine's state word, so **the window may not
carry it**. A question whose answer is printed in its own evidence measures
nothing at all.

So the default view — `stopped-not-why` — replaces the state word with a
plain `Running` column, 1 while the machine was making something and 0 while
it was not, and keeps every other tag exactly as the machine published it:
the alarm word, the ready bit, the cycle time, the counters, the analogs,
and the counters and run bit of the machine immediately before and after it
on the line. That is what most machine layers publish, and it is what a real
diagnosis reads.

Some machine layers *do* publish a reason code. `--state-view full` keeps
the state word, and both views are built by the same code — so the gap
between the two measurements is the reason code and nothing else, which
makes it a number worth having rather than an argument.

### What the MES could not see stays unseen

A scripted disconnect closes the OPC endpoint: the line runs on and the MES
has no state for those seconds at all
([decision 0030](../decisions/0030-a-lost-connection-is-unknown-time.md)). The tag
history still holds them, because the generator wrote them. Handing them to
a question about what the MES observed would be inventing observation, and
writing blanks or the last state anybody heard would be two other ways of
writing down something that did not happen. So those rows are cut out and
replaced by a line saying how long the gap was, and a stop that happened
entirely inside a disconnect is marked `observable: false` and is not asked
about unless you pass `--include-unobservable`.

### Negatives

A set of nothing but stops cannot show a reason being invented, and
inventing one is exactly what round 5 raised: one condition scored highest
on every run, including the six where nothing of the kind happened. So the
set also carries **control windows** — one per machine by default, spread
across the hour, in which the machine never stopped. Their label is `none`,
and an answer that finds a reason in one is visible in the matrix as a
column under a row with no stops in it.

### How big a question is

Sixty seconds of line either side of the stop, at one row a second, thinned
to about 120 rows for the machine and 60 for each neighbour, with the first
and the last row always kept and the thinning stated in the text.

Built over two of round 5's recorded runs that came to **1,479 to 9,205
characters a window, median 5,432** — roughly 390 to 2,400 input tokens,
median about 1,400. The comparison that matters: one run-level question in
round 5 cost about 9,400 input tokens, because its state was the tail of a
whole run. A stop window is a minute of one machine and its two neighbours,
so it is the smaller question by about six times at the median.

Every size the set produced is written into the set itself, under
`totals.state_characters`, so a pass can be costed from the file rather than
from the bill afterwards.

### The record

```json
{
  "id": "<results>/<plant>/<equipment>/<start second>",
  "window_sim_s": [100, 160],
  "label": "breakdown",
  "label_says": "the run scripted `down` for Weld from 100s to 160s",
  "scripted": true,
  "observable": true,
  "unseen_seconds": 0,
  "state_view": "stopped-not-why",
  "state_sha256": "…", "state_characters": 5356, "state_rows": 120,
  "state": "…the window as the question is asked over it…",
  "mes_verdict": {"kind": "faults", "observed": true, "…": "…"},
  "mes_verdict_says": "the scorecard's faults[0] covers this window"
}
```

A window the scorecard names nothing in says so in `mes_verdict_says`
rather than carrying nothing: unknown is not zero.

## P1, asked offline

`fsmes jev ask` asks one typed **choice** over that vocabulary, once per
window, through the same `integrations/jev` client the run-log triage uses —
the same `MES_JEV_API_KEY`, the same pinned `MES_JEV_MODEL`, the same
outbound register entry (`llm.jev`, refused in shadow mode). `state_class`
is `observation`, per decision 0032: a window of tag history is
observation-shaped, even though the plant that produced it is simulated.

A choice is the right shape because the options are not ordered — there is
no sense in which `blocked` is more than `starved` — and because **a choice
proposes an option of its own**, which is why a confusion matrix can be
drawn without anybody picking a threshold first.

Every answer is stored with the option chosen, the probability on it, the
whole distribution across the seven options, the stated confidence, the
model version **as served**, the request id and the usage, with the label
beside it. A call that fails is stored as `not asked (…)` and counted apart
from the answers: a judgment may not cost the thing it judges (0031), and a
matrix that counts a failure as a wrong answer is worthless.

**Nothing is asked without a key.** With no key — the normal case for every
installation of this package — `fsmes jev ask` writes a file saying it was
not asked and why, which is a record rather than a failure. Every test in
the repository runs the asked path through a transport a test wrote; none
opens a network connection.

### What a pass costs, before it is spent

`fsmes jev ask` prints the number of calls, the characters of state and a
rough input-token estimate **before it asks anything**, and refuses above
300 calls unless you pass `--yes`. `--dry-run` prints the estimate and
stops. `--limit` asks about fewer.

## D1's battery against the scripted hours

`fsmes jev calibrate` also pairs each scored run's recorded run-log battery
with what the hour was scripted to do. Two of the six questions have a truth
in the script; four do not.

| Question | What the scripted hour says |
|---|---|
| `component_stopped_reporting` | **true** where a `disconnect` was scripted, **false** where none was |
| `counter_went_backwards` | **true** where a `counter_reset` was scripted, **false** where none was |
| `retry_storm` | **unlabelled** — nothing in a scenario scripts how this MES retries |
| `silent_exception` | **unlabelled** — nothing scripts an exception in this MES |
| `deadlock` | **unlabelled** — nothing scripts a deadlock in this MES |
| `worst_problem` | **unlabelled** — a scenario scripts events, not a severity |

The unlabelled four are printed as unlabelled rather than guessed at. A
guessed label measured against a probability produces a number that looks
like evidence and is not.

One caveat is printed with every pairing rather than buried here: the
`component_stopped_reporting` question says "stopped logging **before the
run ended**", and a disconnect that later reconnects only half meets that
wording. The scripted windows are printed beside it so a reader can judge.

## The two figures

`fsmes jev calibrate --results … --set … --answers … --out <dir>` writes:

- `calibration.md` — the confusion matrix (labels × proposals, with every
  row, column and grand total stated), the calibration bins, the expected
  calibration error, and the D1 pairing table. Self-contained: every number
  in it can be checked against the two files it names, without running
  anything.
- `p1-by-probability.svg`, `p1-by-confidence.svg` and one SVG per labelled
  D1 condition — stated confidence against measured accuracy, in ten bins,
  with the perfect-calibration line drawn and the count printed under every
  bin.
- `d1-against-truth.json` — the pairing as data.

A bin with no samples is reported with a count of zero and no accuracy at
all. It is never interpolated, never joined across and never left out: an
empty bin is a thing the set did not measure, and a plot that hides it is a
plot that claims it did.

### And still no threshold

The tool chooses none, and nothing in this MES reads any number it produces.
Where a bin has at least ten samples at or above it and is right at least
nine times in ten throughout, the report **names that bin**, with its count,
and says in as many words that whether it is a threshold is a person's
decision and not the tool's. Where no bin manages that, it says so.

## Running it

```bash
# 1. Build the set. No key, no network, no plant: this reads files.
fsmes jev labelled-set \
  --results ~/lab-results/<a run> \
  --results ~/lab-results/<another run> \
  --out build/jev/set.json

# 2. See what asking would cost, and ask nothing.
fsmes jev ask --set build/jev/set.json --out build/jev/answers.json --dry-run

# 3. Ask. Needs MES_JEV_API_KEY, MES_JEV_MODEL and the [jev] extra.
fsmes jev ask --set build/jev/set.json --out build/jev/answers.json --yes

# 4. Draw both figures, and choose nothing.
fsmes jev calibrate \
  --results ~/lab-results/<a run> \
  --results ~/lab-results/<another run> \
  --set build/jev/set.json \
  --answers build/jev/answers.json \
  --out build/jev/report
```

Steps 1, 2 and 4 need no key. Step 3 is the only one that asks anything, and
without a key it writes a record saying it did not.

## Numbers

**No run has been recorded yet.** The tooling above exists and is tested;
nothing has been asked of the service with it. When a pass is run, the
numbers go here — the confusion matrix, the expected calibration error and
the bin counts — whatever they say.
