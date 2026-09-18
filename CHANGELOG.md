# Changelog

All notable changes to FactorySemantics MES. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
SemVer, where `0.x` means the API may change with a note here.

Sections: **Added**, **Changed**, **Fixed**, and **Honesty** — anything that
changed what a number *means* (a KPI formula, a state mapping, a counter rule)
goes under Honesty with a migration line, so plant people can find it.

## [Unreleased]

### Added

- **Step 2's numbers, published as they came out.** The labelled set was
  asked of the judgment model on 2026-09-17 — six recorded runs, 311 windows,
  305 asked and 305 answered, `jev-1.13.0` asked for and served — and the
  result is on `docs/ai/JEV-CALIBRATION.md` with both reports checked in
  beside it under `docs/ai/calibration/2026-09-17/`. **Right on 137 of 305
  (0.449)** where the machine's state word, which names the reason, is
  withheld, and **281 of 305 (0.921)** where it is left in; the gap between
  the two is the leak, measured. On the default view `changeover` was 0 of 15
  and every one of them was called a breakdown, `blocked` and `micro_stop`
  are confused with each other and with `starved`, and proposing `blocked`
  every time — no model, no state — would have been right 0.462 of the time.
  Expected calibration error 0.196 by probability and 0.159 by stated
  confidence, and the tool's own sentence is that no threshold is defensible
  anywhere on that scale. D1's two decidable conditions do not separate truth
  from not on 14 labelled pairs, and the highest
  `component_stopped_reporting` of the seven run records is on a run where
  nothing was scripted to go silent. **No threshold, no gate and no
  automatic label follows from any of it**; decisions 0031 and 0032 are
  unchanged and still *proposed*. Preconditions 1 and 2 in `docs/ai/JEV.md`
  now point at the evidence: both are met, and the second is met with a no.

- **A labelled set and the two figures a threshold would need.** The
  simulated plants script their own breakdowns, changeovers, counter resets
  and micro-stops, so the true reason for every stop in a recorded run is
  already written down. `fsmes jev labelled-set` turns recorded runs into
  one record per window — the window, the machine, the reason it actually
  had, the state the MES could see around it and what the run's own
  scorecard said — including control windows the machine ran straight
  through, so a reason being invented is visible. `fsmes jev ask` asks one
  typed **choice** over that vocabulary, once per window, printing what the
  pass will cost before it asks anything and refusing above 300 calls
  without `--yes`. `fsmes jev calibrate` draws the confusion matrix, the
  calibration bins, the expected calibration error and a reliability plot,
  and pairs the run-log battery's six conditions with what the scripted hour
  says — marking the four it cannot decide as unlabelled rather than
  guessing. **No threshold is chosen anywhere**: where a bin earns it, the
  report names the bin with its count and leaves the decision to a person
  (decision 0031). The window withholds the machine's raw state word by
  default, because the label is read off it and a question whose answer is
  printed in its own evidence measures nothing; `--state-view full` keeps
  it, so the difference is itself measurable. Seconds the MES had no
  connection for are cut out of the window and said to be missing rather
  than filled in (decision 0030). `docs/ai/JEV-CALIBRATION.md` is the page,
  and its *Numbers* section now carries the first pass (below).
  **With no key — the normal case — nothing is asked and the file says so.**
  No test opens a network connection.

- **A typed judgment beside the agent evals' own check.** Every agent-eval
  answer is now read twice and both readings are kept on the same row: the
  deterministic check, which is still the only thing the pass rate is
  drawn from, and one typed question to a hosted judgment model — does this
  reply name exactly the expected codes and no distractor — recorded with its
  probability, the model version as served, the request id and the time. It is
  the second caller of the `[jev]` client added for the run-log triage, on the
  same `MES_JEV_API_KEY`, the same pinned `MES_JEV_MODEL`, the same outbound
  register entry (`llm.jev`, refused in shadow mode). It gates nothing
  (decision 0031), and it has no threshold: `fsmes agent-eval --summary` prints
  the mean probability where the check passed and where it failed rather than
  an agreement rate, because a threshold needs a calibration plot this project
  does not have yet. **With no key — the normal case — every eval behaves
  exactly as it did**, and the row says `not asked (no MES_JEV_API_KEY in this
  environment)` rather than nothing. No test opens a network connection.

- **`fsmes jev models`** lists the model versions the judgment service serves,
  and with `--resolve` spends one deliberate call learning which concrete
  version a moving alias answers as, so a pin can be moved on purpose. It is a
  build-loop command: nothing in a plant runs it, and shadow mode refuses it.

### Fixed

- **The calibration figures no longer cut their own titles off.** Every line
  of text in a plot from `fsmes jev calibrate` is now wrapped to the canvas
  before the canvas is sized, so a long title makes the drawing taller
  instead of running past the right-hand edge; a word too long to break
  shrinks instead. The four figures checked in under
  `docs/ai/calibration/2026-09-17/` are regenerated from the same stored
  answers, and every number in both reports is unchanged.

- **`fsmes jev ask` no longer understates what a pass will cost.** The
  estimate printed before anything is asked used 3.8 characters to a token,
  measured from round 5's run-log usage, and the first real pass showed it
  wrong by about four: 454,339 input tokens printed, 1,725,959 billed. A run
  log is prose; a window of tag history is digits, commas and short column
  names. The estimate is now **measured** from the answers of an earlier pass
  of the same state view where there are any — the `--out` file if it exists,
  or an answers file named with the new `--measure-from` — using the
  characters of state and the input tokens each stored call carries, so it
  comes in under the bill only if the next pass is dearer per character than
  the last one was. Where there is nothing of that view to measure it uses a
  stated one character to a token and prints, in as many words, that the
  figure is not a measurement. A call the service billed no usage for is left
  out of the rate rather than counted as nought tokens. Both passes of
  2026-09-17 are now estimated to within their own bill.

- **No failure of the judgment model can cost a run its scoring.** The first
  real call to the service, on 2026-09-17, raised `AttributeError: module
  'typesafe_sdk' has no attribute 'TypeSafe'` — the transport had been written
  from the survey's description of the SDK rather than from the SDK — and that
  error killed the triage *and the scoring* of a simulated run that had already
  finished. Two fixes:
  - The transport is written against `typesafe-sdk` as installed:
    `TypeSafeClient`, `system_one`, `Noul` and `Score` questions keyed by name,
    and a response carrying the served model version, the token usage and one
    answer per question. A test drives that real client through a mock HTTP
    transport — no network, no key — so a change in its shape fails a test
    instead of a run. That test skips where the optional `[jev]` extra is not
    installed, and says so.
  - **Every** exception raised while building or using the client is recorded
    as `not asked (<class>: <what it said>)` on the run and goes no further:
    not the list of failures somebody thought of, all of them. A judgment
    gates nothing and may cost nothing (decision 0031). A key the service
    refuses says so and names the setting, never the key itself.
- **`MES_JEV_MODEL` now defaults to a version that is served.** `jev-1.12` was
  the survey's example and is not served; asked for, it answered nothing. The
  default is `jev-1.13.0`, which the service answered as on 2026-09-17 and
  accepts as a pin by name. Its own model list offers only moving aliases,
  which this MES still refuses as a pin, so the way to learn the name of the
  version to pin is to ask — which is what `fsmes jev models --resolve` does.
  A score answer now also keeps the expected score the service returns, which
  can fall between two levels, beside the level carrying the most probability.

### Honesty

- **The agent evals stopped counting English words as machine codes.** Scoring
  an agent's answer read the reply with
  `re.findall(r"[A-Z][A-Z0-9_-]{1,}", answer.upper())` — the answer was
  upper-cased before an upper-case character class was matched against it, so
  the class was inert and every word of two letters or more became a candidate
  machine code. An agent that named the right machine in a sentence that
  mentions a distractor as a plain word ("DRW01; the drawing area itself is
  fine") was scored zero for a machine it had not named, and an answer that
  only used a code-shaped word ("the drawing looked fine") passed for a machine
  it had not named either. Scoring now asks, of each code the scenario already
  cares about, whether the reply names it as a whole token: case is ignored for
  a code carrying a digit, a hyphen or an underscore, and a code made only of
  letters counts only where the reply writes it in capitals. `NONE`, the answer
  vocabulary rather than a code, is read in any case. **Migration:** the pass
  rate in `~/.local/share/fsmes/agent-evals.jsonl` means something different
  before and after this change, in both directions; results kept before it are
  not comparable with results kept after it. Nothing outside the eval store
  reads these numbers.

- **An order no longer finishes itself when the count reaches its quantity.**
  Reaching the ordered quantity and being finished were treated as the same
  fact; on a floor they are not. An operation now stays open until somebody
  completes it — a person on `POST /workorders/{code}/operations/{seq}/complete`
  or the orders screen, or the ERP — and every unit the machine counts until
  then is booked to the order that made it. **Migration:** a plant that relied
  on an order closing itself now needs someone to close it, because the ERP
  order completion, the finished-goods lot and the certificate of analysis are
  all issued on completion. Where two orders are released on one routing,
  completing the first is also what moves that machine onto the second. The
  certificate now records the person who finished the order as its approver,
  where it used to say `system`. Decision record [0029](docs/decisions/0029-an-order-does-not-finish-itself.md).

- **An SPC signal now does something, and a station's inspection now reaches
  the chart.** Two halves of one gap. `GET /quality/spc/{material}/{characteristic}`
  had run four Western Electric rules since the chart was written and returned
  them as `signals` — and nothing read them: no hold, no non-conformance, no
  trigger. The rules fired for whoever happened to have the screen open, and at
  two in the morning that is nobody. Underneath it the readings mostly were not
  there to fire on: the station inspection path (`InspSeq`/`InspPass`/`Insp_*`)
  wrote a `UnitInspection` per unit and never a `QualityCheck`, which is the
  only table the chart reads, so a scrap burst produced a wall of scrapped units
  and a perfectly flat control chart. Now: the rules are evaluated when a check
  is recorded, a firing is recorded as an `SpcSignal` with the chart as it stood
  (centre, sigma, the ±3σ limits, the readings of its window), and the first
  signal of an excursion raises a non-conformance carrying that evidence. It
  raises nothing else — it does not hold a lot, stop a line or write to a
  machine. The chart endpoint is unchanged and now says, per signal, which hold
  it raised. **Migration:** `fsmes.services.quality.record_check` returns three
  things, not two — the check, the non-conformance and the signals; `a9c4e17b3d60`
  adds `spc_signals`, `non_conformances.evidence` and `quality_checks.equipment_id`
  and changes no data. Decision record
  [0027](docs/decisions/0027-an-spc-signal-raises-a-hold.md).

- **Every production booking, state interval, check and non-conformance now
  says which shift it fell in, and analysis can be asked for one.** Until now
  the calendar could say whether the plant was *meant to be working* and
  nothing more: no row carried a shift and `/analysis/*` was windowed only in
  hours, so the one question a supervisor asks — how did my shift go — could
  only be answered by picking a number of hours and hoping it lined up with
  the boundary. Three rules decide it, each with a test and each capable of
  moving a number ([decision 0028](docs/decisions/0028-which-shift-a-minute-belongs-to.md)):
  a shift is half-open, so a unit counted at 22:00:00 belongs to the shift
  that began and to only one shift; a shift that crosses midnight belongs to
  the plant-local day it *started*, so Friday night is Friday's at two on
  Saturday morning; and an instant no pattern covers is **not attributed**
  rather than filed under the nearest shift. A state interval that runs past a
  boundary keeps the shift it began in — it is one thing the machine did — and
  the *reporting* splits instead, clipping it to each shift's own two ends. A
  shift still running is clipped to now, never to the hour it is rostered to
  end, because the rest of it has not happened. **Migration:** two nullable
  columns (`shift_code`, `shift_day`) on `production_logs`,
  `equipment_states`, `quality_checks` and `non_conformances`, backfilled from
  the shift patterns **as they stand at upgrade time** — this product keeps no
  history of shift patterns, so a plant that moved a boundary will see older
  rows attributed to the current pattern, and rows no pattern covers stay
  null. `docs/plant/shifts.md` says what to do about that.

- **A lost connection to a machine reads as *disconnected*, and the gap it
  leaves is unknown time — never as a stop and never as the last state.**
  The OPC agent used to catch a dropped connection, log it and sleep. No
  interval was closed, so the machine's open state stayed open: a machine that
  was `running` when the cable was pulled read as running for as long as the
  link was down, that invented run time went into availability's numerator,
  and `maintenance.runtime_hours` accrued against a machine nobody could see.
  A connection is now a dimension of its own (decision 0030), recorded in the
  new `equipment_connections` table with the same interval shape the state
  history has. Recording a disconnection **closes the machine's open state
  interval** at the last moment there was evidence and opens nothing in its
  place, so for the length of an outage the machine has no state and every
  screen answers *unknown*. A machine with no row has no connection fact at
  all — nothing is watching it over a connection — and reads as `unknown`,
  never as connected.
  **Migration:** availability is now run time ÷ *observed* time, where
  observed time is the window less the seconds that machine was disconnected
  inside it. On a plant that has never lost a connection nothing changes. On
  one that has, availability rises, because time nobody watched is no longer
  counted as time the machine spent not running. Both OEE answers gained
  `unknown_seconds` and `unknown_share` (and `observed_seconds` /
  `observed_hours`); `loss.availability_seconds` is now measured against
  observed time, so an outage is no longer priced in units as though the
  machine had caused it. The downtime pareto gained `unknown_seconds` and
  `unknown_share` — a disconnection is never a bucket in it. The reporting
  window now clamps to when the MES started *watching* a machine rather than
  to its first recorded state, so a machine whose agent has never reached its
  server reports a window that is wholly unknown instead of an empty one.

- **Known and named, not fixed here: units a machine counted during an outage
  are not booked.** The agent books production from the *increase* of a
  cumulative counter and its memory of the last value lives with the
  connection, so the first reading after a reconnect starts a fresh baseline
  and whatever the counter climbed while nobody was watching books nothing.
  Not new — it has been true of every reconnect since the agent was written —
  but the lab can see it now: with two scripted outages totalling seven minutes
  of line time, the line counted about 520 units per station inside them and the
  MES booked 306 to 337 fewer than the line made, on every station. Nothing is
  invented and nothing is double-counted; the shortfall is real and silent.
  Settling it is a second decision — those units happened, so decision 0019
  says book them, and nothing can say when inside the gap they were made or
  which order they belong to — so it is written down in decision 0030 under
  what that record does not solve, and in
  `docs/operate/opc-disconnections.md`, rather than quietly chosen.

- **A breakdown the lab scripted inside a scripted outage scores unknown, not
  missed.** The MES was blind for those minutes by the script's own doing, and
  scoring it as recall 0 is the same false accusation the scorer already
  refuses to make about a stop shorter than its own sample. Fault checks gained
  `unseen_sim_seconds` and `inside_a_disconnect`.

- **Every number a lab run stores about the MES's OEE says which clock it is
  on.** A run at 20x stored the MES's performance as `19.77` under the plain
  name `performance` while the difference printed beside it had been computed
  from the same figure put back on the line's clock. One object, two answers,
  and nothing in the file to say which of them the difference came from. The
  MES's block in `scores.json` now has no plain `performance`, `oee`,
  `runtime_seconds` or `downtime_seconds`: it has `performance_as_reported`
  beside `performance_line_clock`, `oee_as_reported` beside `oee_line_clock`,
  and `runtime_wall_seconds` beside `runtime_line_seconds` (and the same for
  downtime). Availability and quality keep their plain names, because they are
  the two the replay speed cancels out of. `performance_line_seconds` is gone
  as a name — it was a ratio, not seconds. **Migration:** anything reading
  `plants[].measurements.oee.stations[].mes.performance` from a run directory
  wants `performance_line_clock` if it is comparing and
  `performance_as_reported` if it is quoting the MES.

### Added

- **TypeSafe answered the Jev survey's six questions, and the answers are
  written down with their date.** The six questions in
  [`docs/ai/JEV.md`](docs/ai/JEV.md) were put to TypeSafe and answered in
  writing to the maintainer on 2026-09-17; the answers now sit under each
  question, and the operational half of them is a row on
  [the compatibility table](docs/operate/compatibility.md) in that table's own
  voice — hosted API only, United States only, a version is pinnable with no
  fixed retirement window, 250,000 tokens per second and 1,200 requests per
  minute with no availability commitment, and zero data retention on the
  enterprise tier only. The survey's preconditions carry what that costs:
  every state class above `catalogue` in proposed decision
  [0032](docs/decisions/0032-a-hosted-judgment-and-the-shadow.md) is gated on
  the enterprise tier or on a plant accepting retention with no stated period,
  and hosted-only means an air-gapped line or a site running in shadow beside
  an incumbent cannot use it at any tier. These are the vendor's statements,
  dated; nothing here was measured by this project and nothing is switched on.
  Documentation only.

- **A second, typed pass over every scored run's log, recorded beside the
  first and never in its place.** The pass that reads simulated run logs
  hands 12 KB of log tail to a local model and scrapes a JSON array out of
  the reply, so an unparseable reply and a clean run have been
  indistinguishable: both record *no findings*. A hosted judgment model is
  now asked a fixed battery of six typed questions about the same log —
  five conditions and one severity — where there is nothing to parse. Both
  passes run, both are recorded, and a comparison line per run says where
  they agreed. The typed pass decides nothing: `worst`, the columns the
  results store trends and everything the nightly loop reads still come from
  the open pass alone, and no question carries a threshold. Development only
  — no plant path asks it anything, the client is an optional extra
  (`factorysemantics-mes[jev]`), the outbound register carries it as
  `llm.jev`, **refused** in shadow mode, and with no `MES_JEV_API_KEY` set
  (the normal case) the run is triaged exactly as it was before and the
  record says it was not asked. Step 1 of the phasing in
  [`docs/ai/JEV.md`](docs/ai/JEV.md), under decisions
  [0031](docs/decisions/0031-a-judgment-is-a-proposal.md) and
  [0032](docs/decisions/0032-a-hosted-judgment-and-the-shadow.md);
  the page is
  [`docs/ai/JUDGMENT-IN-THE-BUILD-LOOP.md`](docs/ai/JUDGMENT-IN-THE-BUILD-LOOP.md).

- **The Jev survey, and two proposed decisions about what a judgment may
  touch.** [`docs/ai/JEV.md`](docs/ai/JEV.md) is a survey of where a typed
  judgment model could sit in this project — twelve candidates in the build
  loop, sixteen in the product, and the ones to refuse — written so each can
  be taken or refused on its own. Decision records
  [0031](docs/decisions/0031-a-judgment-is-a-proposal.md) (a judgment is a
  proposal: it never touches a graded number or a gate) and
  [0032](docs/decisions/0032-a-hosted-judgment-and-the-shadow.md) (a hosted
  judgment is an outbound path, classified by the state it sends) are both
  **proposed**, not accepted. Documentation only: nothing here is built and
  nothing is switched on.

- **`shift=` on every analysis endpoint, and a shift selector on the Analysis
  screen.** `current`, `previous`, or a day and a code such as
  `2026-09-14/NIGHT`, read on the plant's own clock — beside the existing
  `hours=`, never mixed with it. A shift that names nothing (the plant is
  between shifts, the code is not a pattern, the pattern does not run that
  day) is a `400` with one sentence rather than a quiet fall-back to eight
  hours. `GET /analysis/shifts` lists what a picker can offer, which shift is
  running, the total, and which clock the boundaries are drawn on and whether
  anybody chose it. The screen keeps line, window, shift and machine in the
  address bar. The MCP tools `downtime` and `tag_trend` take the same `shift`
  argument and a new `shifts` tool lists them.

- **Every screen that shows a machine's state says whether the MES can still
  see it.** The floor tiles, the machine page, the line view and the fleet
  console carry the connection beside the state; a disconnected tile takes the
  grey the product already uses for unknown, with a dashed edge and a strip
  saying since when and why. `GET /equipment/connections` lists every machine
  with its connection and states its total; `GET /health` gained a `watching`
  block — machines, connected, disconnected, and how many have no connection
  fact at all — so a monitor that knows a plant is up also knows it has been
  blind to nine machines since Tuesday. The state timeline draws the outage as
  its own interval rather than as white space. `equipment_connection_change`
  joins the domain events, so a namespace subscriber knows when the last state
  it heard stopped meaning anything.

- **The OPC agent notices a lost connection rather than waiting to be told.**
  A watchdog asks the server whether the session is alive every
  `opc_health_periods` publish intervals (three by default, never faster than
  once a second — config, not code). It is a positive check: OPC UA publishes
  on change, so a machine standing idle correctly says nothing for an hour, and
  inferring an outage from silence invents one. Without it a read-only source —
  a replay, a historian, a server this MES may not write to — held a dead
  subscription for ever.

- **The lab can script a lost connection.** `disconnect` joins the generator's
  closed event vocabulary: the replay closes its OPC endpoint outright for the
  window and reopens it afterwards, while the line runs on and the tables say
  exactly what they always said. It names no station, because asking which
  machine a network outage happened to has no answer. A new `connection`
  measurement, read from what the run saw *while the hour played*, asks whether
  the machines read as disconnected, whether any single look disagreed with
  itself, and whether the window came back as unknown time; an outage shorter
  than a couple of the agent's own health checks is reported as unknown rather
  than as missed. `labs/experiments/lost-connection.toml` is the starter, with
  a real breakdown scripted inside the first outage.

- **A lab run can ask what `fsmes fleet console` made of its plants.** The
  console's promise is decision 0023's — a plant that did not answer is
  *unknown*, never healthy and never down — and nothing tested it against
  plants that really start and really stop, because a console needs several
  plants and a lab run has always had exactly one at a time. Which turns out to
  be the fixture: the lab runs its plants one after another, so during a
  several-plant experiment exactly one is answering and the rest are not. A run
  that asks for `console` starts a real one on a claimed port, over its own
  empty fleet file so it can never pick up plants somebody already has running,
  tells it each plant's address the moment that plant comes up, and asks
  `/fleet.json` at each phase. Two numbers come out: how many phases its count
  of answering plants matched the truth, and how many stopped plants it read as
  anything other than unknown — a defect at any value above zero, and the same
  fault as calling a changeover downtime, at fleet scale.
  `labs/experiments/two-plants-two-zones.toml` now asks for it, so CI proves
  it. On this machine, twelve phases, the count right at all twelve, and no
  stopped plant ever called anything but unknown.

- **A plant can say what an SPC signal should set off, without that being a code
  change.** The trigger catalogue gains `spc.signal`: a tag no PLC publishes,
  raised by the MES on the station whose reading tripped a rule, carrying the
  rule number as its value. A plant that wants rule 1 to set a moulder down, or
  to raise corrective maintenance, drafts a trigger on it and approves it the
  usual way. What the *product* does on a signal — raise the hold — is not
  configurable; what happens next is nothing but the plant's business. A signal
  on a reading with no station recorded reaches no trigger and says so, because
  firing a trigger on a machine the MES is guessing at is worse than not firing
  one.

- **An inspected characteristic the plant has written a specification for
  becomes a quality check.** A station event's `Insp_<Name>` reading is judged
  in or out of spec by that specification and recorded, so the chart and the
  station card see it. One with no specification stays an inspection and is
  counted and named in the ingest's `uncharted` list rather than dropped in
  silence. Which characteristics are charted is therefore the plant's
  configuration — which is also what keeps this from putting a row in
  `quality_checks` for every attribute of every unit at line rate. Such a
  reading opens no non-conformance of its own: the station already judged the
  unit, and what raises the hold is the signal. A reading a *person* records
  still opens one when it is out of spec, as it always has.

- **A quality check can say which station took it.** `quality_checks.equipment_id`,
  null for a reading a person took with a gauge — *not recorded*, not "no
  machine". Nothing derives it from the order's route, which would name a
  machine nobody stood at.

- **A lab run names a station whose own counts and own run time do not agree.**
  Northgate's Deburr on 2026-09-14 reported 826 units and 1,878 line seconds of
  run time for a machine the MES itself rates at 2.4 s a unit — 1,982 seconds
  of work inside 1,878 seconds of run time — while the script, pricing the
  machine at the same 2.4 s, fitted its units inside its running seconds. That
  is not the MES against the script; it is two of the MES's own numbers against
  each other, at a rating both sides agree on, so neither the replay speed nor
  the master data explains it. It gets its own line in the report above the
  ordinary differences, and it is the one finding on the page that survives any
  argument about the truth. What the report still will not do is say which of
  the two numbers is the wrong one.

- **A scored run can be watched while it plays, and the lab measures how long
  the screens took.** `fsmes.sim.runner.scored_run` grew an `observe` hook,
  called with the base URL, a token and **the line second the run is at** every
  interval of wall clock while the scripted hour plays. The line second is
  passed in because only the runner knows when the replay's first tick was. The
  post-run `collect` hook is unchanged, and a run with no observer is the same
  sleep it always was. An observer that raises does not end the run: losing the
  hour because one HTTP call came back badly would be the harness throwing away
  the evidence it exists to collect.

  On top of it, a new measurement: **`latency`** — for every scripted event,
  how long after the line did each screen say it. The operations feed and the
  line view are asked separately, because two screens showing one machine two
  different states at one instant is a finding nothing else would catch.
  `/health` is asked too and answers nothing about any machine, which the
  report says rather than leaving the route out. A lag smaller than the polling
  interval prints as *within resolution*; an event no look caught is *unknown*
  with which of the reasons it was, never a zero or a maximum standing in for
  silence. A plan whose interval is too coarse for the speed it asks for is
  refused with the arithmetic. Beside the events, how far behind the line's own
  count the line view ran — the machine the line ends at, in units, because
  turning a backlog into seconds needs a rate and a line that is starved,
  blocked or down has not got one. The raw looks are kept in
  `watched/<plant>.json` whatever the measurement made of them.
  `labs/experiments/starved-and-blocked.toml` now asks for it.

### Added

- **A tag map can say where the line publishes the order it is running.** Many
  line-control PLCs publish the current order on a line-level register rather
  than on any one machine. Nothing in this MES read it, so which order a unit
  belonged to was always the MES's own inference from what it had released - a
  reasonable guess, and still a guess. A tag map may now carry one `line`
  block: the object the line's own tags sit on, the tag carrying the order it
  is running, and `order_code`, the rule that turns what the line published
  into the code this MES holds (`"WO-ACME-{value}"`, because a PLC publishes
  `4711` in a register and the MES holds `WO-ACME-4711`). It is **read, never
  written** - the opposite of a machine's `order_tag` - so a historian, a
  replayed CSV or a server you have no write rights on can still answer it, and
  the read-only promise in
  [docs/operate/opc-readonly.md](docs/operate/opc-readonly.md) is unchanged. A
  map with no such block behaves exactly as before. The three maps this
  repository ships for its own lab lines now carry one.

- **A lab run compares each order the line published with the order the MES
  holds, and says how far past it the line ran.** For five runs every report in
  the lab carried the same sentence under *what the runs could not answer* -
  seven times - about a line that had been publishing its order number the
  whole time. With the `line` block read, the booking measurement gains a row
  per order: what the line made under it, what the MES booked against it, what
  the order was for, and what each side says the line made past it. The ordered
  quantity is the MES's own, because an order is for what the plant was told it
  was for and a second copy of that number would be a second place for it to be
  wrong. The truth side is a range (the replay loops); the over-run the MES
  reports about itself owes the band nothing, which makes it the sharp reading
  for the sixteen-against-fifteen class of fault. The run also asks the MES what
  it counted with **no order open to book it against**, so a gap between what
  the line made and what an order was booked for says whether the rest was
  dropped or kept - different faults with different fixes. An order the line
  published that the MES never held is *unknown* with the reason; an order the
  MES holds that the line never published is listed and is not a difference.
  New starter `labs/experiments/over-run.toml`: one order, one uninterrupted
  hour, and about two thousand units more than the order asked for.

### Fixed

- **The production trend reports the window it drew, not the window it was
  asked for.** It shares the OEE panel's axis, and the two saying different
  numbers of hours about one axis is how a reader is misled about what they
  are comparing. The state Gantt's merge floor now scales with the window as
  drawn, for the same reason.

- **The README and the docs front page said 85 MCP tools; the product
  registers 89.** Counted from the two servers' own registries. The rest of
  the sentence still holds: ten of the twenty-three modules ship tools, nine
  of the twenty-three are the kernel, and a pack that switches a module off
  takes its tools with it.
- **An over-run is reported as the distance the line actually ran past the
  order.** `labs/experiments/over-run.toml` ran the bottling line for an
  uninterrupted hour against an order for 4,000, with the line publishing that
  order the whole time. The line made 6,104 under it. The MES booked 4,003,
  called the over-run **3**, and kept **15,351 counts** across the six stations
  as production with no order open — while the order was open throughout.
  Nothing was lost and nothing was invented, but the number a plant reads was
  wrong by three orders of magnitude. The cause was that the booking which
  brought an operation to the ordered quantity also finished it, after which
  the machine had no open operation. Booking now continues past the quantity to
  the order that made the units, so `over_qty` is the whole over-run — on
  `GET /workorders/{code}`, on the orders screen, in the ERP order completion
  and in the B2MML confirmation. Units genuinely counted with no order open are
  still unassigned production, listed with their total (decision 0019), and the
  orders screen now shows that total beside the orders instead of leaving it on
  the machine page.

- **Two ephemeral runs on one machine no longer choose the same port.** A
  scored run probed for a free port by binding one and closing the socket
  again, which says a port *was* free a moment ago - a different claim from
  "this port is mine". Two runs started seconds apart both chose 8100, and one
  of them then read *connection refused* in the middle of its own hour (seen on
  2026-09-14 when a two-plant experiment ran beside other ephemeral plants;
  alone, the same plan was clean). A port is now claimed for the length of the
  run in a lock file the other runs can see, using the same `O_EXCL` primitive
  and staleness rule as `fsmes.core.oplock` - so a killed run's port comes back
  rather than leaving the range quietly smaller. The bind probe stays, for
  everything on the machine that is not one of these runs, and an exhausted
  range now says how many ports are held by runs and how many are in use by
  something else.

### Added

- **A scenario can starve or block a station, and the run asks what the MES
  called it.** Two new scripted events — `starve` (nothing arrives) and `block`
  (nowhere to put it) — join the line vocabulary, which is now written down in
  one place and **closed**: a plan naming anything else is refused before a
  directory is made, with the list printed, instead of exiting the process from
  inside the generator several steps later. They script the cause rather than
  the symptom, so the rest of the line starves in turn on its own as the
  buffers drain. Every scripted window is scored against what the MES recorded
  *for that machine* — a neighbour that really broke in the same minutes is not
  counted against it — and a window the MES was not watching is *unknown*,
  never a pass. New starter experiment `labs/experiments/starved-and-blocked.toml`.

### Honesty

- **A performance figure over 100 % no longer blames the master data.** Taking
  the `min(1.0, …)` cap off performance earlier the same day (see *0.2.0 →
  Honesty*, decision
  [0025](https://factorysemantics.github.io/factorysemantics-mes/decisions/0025-performance-is-measured-not-capped/))
  was right; the sentence printed beside the uncapped figure was not. It read
  *the rating is slower than the machine*, and the lab caught it out within
  hours: Northgate's Deburr reported **826 units and 1,878 line-seconds of run
  time** at a rated 2.4 s a unit — 1,982 seconds of work inside 1,878 seconds
  of running — while the script that made the data rated it at the same 2.4 s
  and fitted **831 units inside 1,996 running seconds**. The rating was right
  to a tenth of a percent. The MES's run time was short, because the machine
  changed state every 2.7 seconds and the agent saw it about every 15 — and no
  timestamp recovers that, which 0026 records having tried and measured.

  `performance > 1.0` is the same inequality as *the counted work will not fit
  inside the run time*, and it has at least three causes the MES cannot tell
  apart. So the note now states the disagreement and both of its numbers, and
  says the MES does not know which of them is wrong. The value is unchanged
  and still uncapped. Decision
  [0026](https://factorysemantics.github.io/factorysemantics-mes/decisions/0026-counts-that-outrun-the-run-time/).

  **Migration.** Anything matching on the old sentence — *"rating is slower
  than the machine"* — will stop matching; `performance_note` is prose for a
  person, and the value to test is `performance > 1`. On the analysis screen
  the row's mark reads *counted work outruns the run time*, and its CSS class
  is `.counts-outrun` where it was `.rated-slow`.

- **The MES says how much of its production it counted outside run time.** New
  on every OEE answer, per machine: `counted_outside_run_time`, the units —
  good and scrap together — booked at an instant the MES's own state history
  did not have that machine running. It is the one candidate cause of the
  disagreement above that the MES holds evidence for, and it is what a counter
  catching up after a stop looks like. **Nothing is moved and nothing is
  dropped because of it**: those units stay in `good_qty`, `scrap_qty`,
  quality and the performance numerator (house rule 1). They are named, and
  that is all.

- **A detection lag smaller than the sampling interval is printed as *within
  resolution*, not as a signed number.** A buffer sweep reported a breakdown
  detected one second *before* it was scripted, at a speed whose sampling
  interval was thirty line seconds. That is quantisation, not prescience, and a
  bare `-1 s` invites somebody to trend it. Every lag in a lab report now
  carries the run's resolution beside it, and a lag inside it says so.

- **A scripted stop is printed in the line's own seconds.** The scorer measures
  in wall seconds and the script is written in line seconds; the lab's downtime
  section was printing the first under a heading that said the second, so a
  180-second stop replayed at 20× read as "9 s scripted". Both are now given,
  each named for the clock it belongs to. No change to any recall or
  misclassification figure — those are ratios and were never affected.
- **`fsmes fleet create` builds a plant a person can sign in to.** It applied
  the pack and stopped, and it applied it to *this process's* database rather
  than to the plant's: `fsmes pack apply` named a database only when the pack
  named one of its own, so a pack that named none was applied to the product
  default `./fsmes.db`. What `fleet create` produced was a stray file beside
  the working directory and an ownership entry for a plant that had never
  been created — which then started, answered `/health` with `ok`, was
  counted *answered* by the console, and returned **500 to every sign-in**.
  `pack.apply.database_for` is now the one place that answers which database
  a pack means: the pack's `[storage] database_url`, else `MES_DATABASE_URL`
  when the invoker set it, else `<data dir>/<plant>.db` — the file its fleet
  gives it, which is the file the plant is started against. Never the process
  default. `apply` names that database on a line of its own before it changes
  anything, as `fsmes db-status` already does, and `create` goes on to make
  the accounts `fsmes plant <name> init` makes.
- **`fsmes pack apply` refuses to seed master data into a database that is
  not at head**, rather than writing rows through the ORM into a
  half-migrated file. A database in that state has some of this product's
  tables and no Alembic stamp, and the migrator disowns it outright — which
  on 2026-09-14 left a plant no command could take forward.
- **`fsmes fleet stop --force`** stops a plant this installation created that
  has stopped answering. Without the flag the refusal now says whether the
  pid file still names live processes, and names them. Forcing gives up
  liveness and nothing else: ownership is still corroborated by the instance
  id in the plant's own data directory, and a plant this installation did not
  create is refused either way. Before this, a plant that was running and
  could no longer be talked to could only be escaped with `kill`.
- **The fleet console's default port is 8090.** It was 8100, which is the
  first port `fsmes score` and `fsmes sweep` hand an ephemeral plant, so a
  scored run started in the same minute took the console's port and served a
  plant's sign-in page on its address. The console's port and the simulator's
  range are named constants now, and a test fails if they ever overlap.
- **`fsmes score bottling` and `fsmes sweep bottling` have a line to read.**
  The bottling pack carried no master data — its six stations are the
  product's own reference line — and the lab script that seeded them stopped
  being named by the registry on 2026-09-13, so the ephemeral plant a scored
  run builds had no machines and its first read answered 404. The line is in
  `labs/multiplant/bottling/masterdata/` now, generated from `seed_kepsim`
  itself and pinned against it by a test that seeds one database each way and
  compares them. `labs/experiments/*.toml` no longer name a seeding script for
  bottling, because the pack alone is now enough to build the plant.

### Added

- **A note left at a screen during an experiment lands in that run's report,
  beside the numbers for the screen it is about.** The on-screen design panel
  is on for every plant `fsmes lab run` starts (on the on-device model unless
  the plan's `[feedback] claude` says otherwise), and every conversation is
  tagged with the run, the plant and the screen. The run id comes from the
  plant's own environment rather than from the browser — a body claiming a
  different run is filed against no run at all — and the **moment** is
  resolved by the run, which is the only thing that knows when the replay's
  first tick was: each note carries the line second it was made at and the
  scripted events live at that second, or says it was made before the first
  tick or after the script ran out rather than being rounded to either end.
  At the end of the run the conversations are **copied** into
  `feedback/conversations.jsonl` — the design store is never moved, emptied or
  written into a plant's database — and rendered verbatim in `report.html`
  under the section for their screen. `fsmes lab note <run> "…" --screen
  --plant` writes one from the terminal for a run watched without a browser,
  and `MES_DESIGN_STORE` points the store somewhere else for a scripted run.

- **`fsmes lab review [runs…] --out findings.md`** — several runs read
  together. It clusters every note left at a screen, every row where the MES
  and the script differed and every question a run could not answer, by the
  screen and the measurement they belong to; each cluster names its runs,
  plants, stations and numbers and quotes the notes verbatim. It never says
  which side is right — a test forbids the words — and it works with no model
  at all, so CI runs it: the clustering is by screen and measurement, which
  are facts in the files, and the local model is asked for one thing only, a
  short heading, which is dropped if it comes back as a verdict.

- **`fsmes lab` — an experiment is a plan, a command and a directory somebody
  else can read.** `fsmes lab run <experiment.toml>` builds each plant in the
  plan from its pack, generates the line data for this run from the line
  description (so the seed in the plan is the seed that ran), replays the
  scripted hour through an ephemeral plant on loopback, reads the MES back
  through its own HTTP API and writes one directory: the plan, the script each
  plant played, the data generated from it, the pack each plant was built from,
  what each plant recorded view by view, the truth read out of the replay's own
  input, the scores, a self-contained `report.html` and a `notes.md` for what a
  person saw that no number caught. `fsmes lab list` and `fsmes lab open <run>`
  — which re-renders the report, so notes written after the run reach the page.
  Three measurements, each with the truth beside it: **booking** (units booked
  against units made, per station and in total), **downtime** (the scorer's own
  two questions plus seconds down on the line's clock) and **oee**
  (availability, performance and quality per station against the script).
  `latency`, `console`, `quality` and `agent-eval` are designed and not built,
  and a plan asking for one is refused by name rather than quietly given a
  report that says less than it asked for. Two starter experiments in
  `labs/experiments/`, both run in CI on every pull request. The page is
  `docs/develop/experiments.md`.

- **Every crumb on the machine page goes somewhere, and one renderer draws
  them all.** Scott, on `/dashboard/machine/ASSEM1_Kit`: *"why can't i click
  along the tree Mega-Factory › Mega-Factory Works › Assembly › ASSEM1LINE ›
  ASSEM1_Kit? ... I can only click the ASSEM1LINE, not any of the others."*
  The machine page had already been fixed; the Machines tree drew the same
  trail by its own rule and sent a line to the tree rather than to the Line
  view, so which level was clickable, and where it went, depended on which
  screen you were standing on. `FS.crumbs()` in `common.js` is now the one
  place that knows: a work center opens the Line view, every level above it
  opens the Machines tree scoped to that node, and the node you are on is
  text carrying `aria-current="page"` rather than a link to the page you are
  already looking at.

- **The header menu is audited rather than assumed.** Scott, on
  `/dashboard/line`: *"I can't get back to the main site from here" ... "all
  pages"*. Every screen already carried the shared header and nothing
  watched that it kept doing so. Three checks now do.
  `test_no_screen_can_quietly_appear_without_the_header` reads
  `src/fsmes/web/*.html` instead of a hand-typed list, so a new screen is
  audited the day the file exists; a page that is not a plant screen is
  excused by name with its reason, and `fleet.html` — the fleet console, a
  separate server with no plant session — is the only one.
  `fsmes ui-check` records, for every route in every theme, whether the
  header is present, visible, has a link home and lists any screens at all,
  and files a `no-header` finding when it does not. And a browser test opens
  six screens and a machine page, checks the header on each, and clicks
  every crumb to prove it lands somewhere that renders. The crawl of the
  demo plant on 2026-09-14: 22 routes × 4 themes, no finding.

- **Quality at the station.** `/dashboard/station` now has a quality card for
  whatever the machine in front of you is running: the characteristics that
  have a specification for that material, the last few results with the spec
  that judged them and how many results there are in all, and one field to
  record another. An out-of-spec reading raises the non-conformance as it
  always did, and the card now names it, by code, where the operator is
  looking. The card offers only characteristics with a specification — a
  measurement with nothing to judge it against cannot pass or fail, and
  offering one would invite a reading the MES then has no verdict for.
  `GET /workorders/dispatch` gained `material` so the screen can find them.

- **Three more tools for a non-conformance**: `review_nonconformance`,
  `disposition_nonconformance` and a read-only `nonconformance` that returns
  one record with its whole history. All three are proposals like every other
  write; nothing an agent does to a quality record happens without a person.

- **The floor and the Quality screen page and filter on the server.** Four
  lists that used to be drawn out of a copy of the whole plant now ask for a
  page and are told the total:
  - `GET /dashboard/summary` takes `machine_q`, `machine_state`,
    `machine_limit` and `machine_offset`, and answers with `machines_page`
    alongside `machines` — `total`, `scope_total`, `limit`, `offset`,
    `has_more`. Left out, `machine_limit` still returns every machine in
    scope, so the `machines` agent tool is unchanged. `line` stays a *scope*
    (the tiles follow it, as the Line screen needs); `machine_q` and
    `machine_state` filter the grid alone. Each machine now says which line
    it is on, so a screen no longer downloads the equipment tree to label
    twenty-four cards.
  - `GET /quality/specs` takes `characteristic`, `limit` and `offset`.
  - `GET /quality/checks` takes `order`, `since` and `until`, and each row
    names the work order it was taken against. There is deliberately **no**
    station filter: a measurement records the material, the characteristic,
    the inspector, the gauge and the order — not the machine it was taken
    at. Deriving one from the order's route would name a station nobody
    stood at.
  - `GET /workorders`'s `q` matches a material code as well as an order
    code, which is what the floor screen's box has always said it did.

- **A plant's first morning is no longer one OEE query per machine.** OEE
  clamps each machine's window to when the MES first saw it, and asked for
  that machine's production on its own whenever it was first seen inside the
  window. On a plant that has been running longer than the window that branch
  never fires; on the day a plant stands up it fires for every machine, on
  every refresh of every screen. Machines first seen at the same instant —
  which is what commissioning a plant looks like — now share one query. Same
  numbers, found by the thousand-machine test.

- **`GET /quality/specs/facets`** — the distinct materials and
  characteristics that specifications exist for, each with a count, plus the
  totals. A filter dropdown is built from this instead of from a fetch of
  every specification in the plant.

- **Every filter on the Quality screen is in the address bar.** A supervisor
  who has narrowed the inspection history to last night's failures on one
  characteristic can send that screen to whoever has to answer for it. The
  measurements card gains a characteristic search that narrows the tab strip
  on the server, and the history gains a date range.

- **A third state on `fsmes fleet list`, `fsmes fleet status` and the
  console: *answered, but empty*.** A plant with a schema at head, an account
  that signs in and no equipment at all looks healthy from every other angle,
  and the person it matters to is the one who has just built the fleet.
  `GET /pack` carries `line` — how many machines this plant has, or that it
  could not count them — and the third state is read from that and from
  nothing else: a plant that does not answer `/pack`, or whose database did
  not answer, stays `answered`, because unasked is not empty.
- **Three more kinds of pack master data: `bom`, `maintenance_plans` and
  `shifts`.** Moving the bottling line into its pack needed them —
  `fsmes seed-kepsim` builds a bill of materials, five maintenance plans and
  two shift patterns as well as the equipment and the routing — and a format
  that could not carry them would have made "the same line, seeded the same
  way" a quieter plant than the one it replaced. `fsmes pack check` validates
  all three offline: an unknown maintenance trigger, a shift that does not
  start at a time of day, a seven-day mask that is not seven days, a BOM line
  that makes a material a component of itself.
- **`fsmes fleet plan`** — what a deployment script needs to know about every
  plant in a fleet: where each pack is, where each database is and what kind
  it is, whether the plant simulates a line worth regenerating, and where to
  ask it whether it came back. `--json` for a script, and the totals on the
  envelope: how many packs the fleet lists, and how many of those the
  deployment tooling could back up before migrating. Reads only, touches no
  plant, and prints no password unless `--with-password` asks for one.

### Honesty

- **OEE performance is a measurement against the rated cycle, not a floor at
  100 %.** Both places that computed it wrote `min(1.0, ...)`. On 2026-09-14
  the lab measured what that cost: across two experiments, **nine stations in
  two plants, every one reported performance of exactly 1.0** while the script
  that generated their data said 0.9433 to 0.9994. Availability and quality
  tracked the truth; performance could not track anything, because every raw
  value was above 1.0 and every reported value was therefore 1.0. The rule now
  lives in one place — `fsmes.services.oee.performance`, called by both the
  per-machine KPI and the plant-wide breakdown — and there is no cap. Below
  1.0 the machine ran slower than its rating; above 1.0 it ran faster, and
  that is **reported as above 1.0** with the note that says what it means: the
  rating is slower than the machine, which is a master-data finding rather
  than a score above physics. No rated cycle time is *unknown* with its
  reason, never 1.0 and never 0. The performance loss in units is signed for
  the same reason — flooring it at zero hid the same fact one column along.
  *Migration:* `performance` and `oee`, on `GET /analysis/oee`,
  `GET /equipment/{code}/oee`, `GET /kpis/oee/{code}` and the dashboard
  summary, may now exceed 1.0; each carries a new `performance_note` that is
  the sentence to print beside the number, or null when the number speaks for
  itself. A station whose OEE steps up on upgrade has a rated cycle time that
  is wrong — the note names it, and fixing the master data brings the figure
  back. Decision record 0025; the page is `docs/plant/reading-oee.md`.
- **The lab compares performance on the line's clock, and neither side is
  capped.** Availability and quality are each a ratio of two wall-clock
  numbers, so a replay's speed cancels out of both. Performance does not
  cancel: its numerator is priced in the line's own seconds (the rated cycle)
  and its denominator is run time measured on the wall clock, so a plant
  replaying an hour at 20x reports twenty times the line's performance. With
  the cap gone that would have read as a plant beating its rating twenty-fold.
  The report's **P MES** column is now the MES's own performance put back on
  the line's clock — its rating, its counts, its run time multiplied by the
  replay speed — and each station carries its own band, because both sides
  divide by run time and the MES drains past the end of the script. A
  difference inside that band is not called a finding. At speed 1 nothing
  changes.
- **A replayed run's booking comparison states a band, and says which
  direction is sharp.** A CSV replay loops: when the file runs out the counters
  wrap to zero and the hour starts again, and a run is always left playing a
  little past the end because the agent needs time to book what it has already
  read. Units made in that overlap the MES is right to book. So `fsmes lab`
  prints an **expected range** rather than a single number, states the overlap
  in line seconds, and says what follows from it — under-booking is a real
  finding at any speed, because the overlap can only ever add; over-booking is
  blurred by a band that grows with replay speed, and the sharp reading for
  that class of fault is the over-run the MES reports about itself.
- **Counters are summed as deltas on both sides of every comparison.** A
  scripted counter reset zeroes the column the replay publishes, so the last
  row of a station's data holds what it made *since* the reset and not what it
  made in the hour. The lab's first run read it as the hour's total and
  accused the MES of booking four thousand units it had not — the truth was
  wrong, not the plant. Both sides now count the same way the MES books: a
  counter that goes backwards has been re-baselined, and the step across the
  reset is dropped rather than counted.

- **A non-conformance is now worked, and cannot be closed without a
  disposition.** It was `open` or `closed`, with one action: close it.
  "Closed" never said what happened to the material, which is the one question
  a non-conformance exists to answer — rework, scrap and return are three
  different things that happened to three different piles of stock, and *use
  as is* is a concession somebody put their name to.

  The states are now **open → under review → dispositioned → closed**. Review
  is a step, not a gate: a supervisor who already knows the answer may
  disposition an open record directly. The disposition is one of `use_as_is`,
  `rework`, `scrap` or `return` and **requires a reason**. Who took each step
  and when is on the record, and `GET /quality/nonconformances` returns that
  as a `history` beside each row, along with `next_steps`. Its `status` filter
  is now repeatable, because "still open" is three states rather than one.

  **Breaking:** `POST /quality/nonconformances/{code}/close` now returns 409
  on a record nobody has dispositioned. Anything that closed one in a single
  call takes two. The Quality screen's "Open non-conformances" tile is now
  "Non-conformances not closed" and counts all three unclosed states — a tile
  counting only the untouched ones would have read lower every time somebody
  started work.

  **Migration:** `b1f4c73a9e08` adds eight nullable columns to
  `non_conformances` and changes no data. Existing rows keep their status and
  carry null in the new columns, which is the truth: this MES did not record
  who reviewed them, because it did not ask. The screen shows such a step as
  "not recorded", never as `system`. Decision record
  [0024](docs/decisions/0024-a-nonconformance-has-a-life.md).

  A disposition records what was **decided**, not what was booked: it scraps
  no stock and raises no rework order. Deciding and doing are different
  events, and conflating them would be inventing production.


### Changed

- **`GET /quality/specs` answers with the standard list envelope** —
  `{items, total, limit, offset, has_more}` — where it used to answer with a
  bare array, and returns fifty at a time unless asked for more (500 max),
  like every other list in this API. **If you read this endpoint, read
  `items`.** It was the last list that handed over the whole table, which was
  fine at the 127 characteristics of the lab plant and is not a habit that
  survives a catalogue ten times the size. The `quality` agent tool reports
  `total`, `shown` and `has_more` rather than presenting the first two
  hundred as the plant.

- **The GitHub Actions this repository runs are on their current majors, and
  the welcome message survived the move.** `actions/checkout` v4→v7,
  `actions/setup-python` v5→v7, `github/codeql-action/upload-sarif` v3→v4 and
  `actions/first-interaction` v1→v3, in `ci.yml`, `dco.yml`, `docs.yml`,
  `scorecard.yml`, `erpnext-live.yml` and `welcome.yml`. For every action but
  one the majors are a Node 20 → Node 24 runtime change with no input this
  repository passes removed. The exception is `actions/first-interaction`,
  whose v2 rewrite renamed all three inputs from hyphens to underscores
  (`repo-token` → `repo_token`, and the two messages likewise), and which
  reads all three as required before it asks whether anyone is a first-time
  contributor — so the bump on its own would have failed the welcome job on
  every issue and pull request opened. Dependabot's own bump was green with
  the hyphens still in it, because `pull_request_target` runs the copy of the
  workflow on the base branch. `welcome.yml` now passes the underscore names
  and carries the citation, and the next pull request opened after this merges
  is what proves it. `release.yml` keeps its pinned versions in
  this change; it runs only on a tag, so its bumps go separately, behind a
  dry run that can be exercised without one.
  ([#44](https://github.com/factorysemantics/factorysemantics-mes/pull/44))
- **The README's front page tells the truth of 0.2.0.** It was dated
  2026-09-07: it listed M7 and M8 under *not yet*, sent visitors to a demo
  hostname that turns them away, and said `fsmes demo` runs a six-station
  line when the demo plant has two. The status blockquote is now dated
  2026-09-14 and says what is new since 0.1.2 and what is still not done;
  the north star table's ERP mark matches the roadmap, with the roadmap's
  caveats; the public demo points at the page that emails a link; and the
  install snippet cites the `wheel-demo` job that proves it. Docs only —
  no behaviour changed.
- **`release.yml` can be rehearsed without cutting a release.**
  `workflow_dispatch` runs the same jobs a tag runs — the full CI matrix, the
  build, the tag/citation/config-file checks, the demo from the exact wheel
  that would be published, and a two-platform image build through the real
  `docker/Dockerfile` — and skips every step that makes something public: the
  `pypi` job, the image push, the cosign signature, the SBOM and the GitHub
  Release. A dispatch cannot publish; there is no input that lets it. Until
  now this file was provable only by tagging, which meant a bumped action, a
  changed Dockerfile or a broken artifact hand-off was found on release night.
  The version the run is about now comes from the wheel that was just built:
  on a tag the tag is asserted against it, exactly as before, and the
  `CITATION.cff` check — the one version a human still types — becomes
  runnable before the tag exists.

- **`release.yml`'s actions are on their current majors**, the other half of
  the split in [#44](https://github.com/factorysemantics/factorysemantics-mes/pull/44):
  `actions/checkout` 4→7, `actions/setup-python` 5→7,
  `actions/upload-artifact` 4→7, `actions/download-artifact` 4→8,
  `docker/setup-qemu-action` 3→4, `docker/setup-buildx-action` 3→4,
  `docker/login-action` 3→4, `docker/metadata-action` 5→6,
  `docker/build-push-action` 6→7 and `softprops/action-gh-release` 2→3. The
  artifact pair moves together and stays on the same generation: the format
  boundary is v3 against v4, and v4 through v8 share one backend, so upload 7
  with download 8 is a supported pair and either one left behind would not be.
  `download-artifact` v8 now *fails* on a digest mismatch where v4 warned.
  ([#45](https://github.com/factorysemantics/factorysemantics-mes/pull/45))

### Fixed

- **An edit to one of the roles the product ships now stays edited.** The
  admin screen has had an Edit button on every role and the API has had
  `PUT /admin/roles/{code}` since 0.1.0, but every listing of the roles calls
  `ensure_builtin_roles`, which tops a shipped role back up to the
  capabilities this version ships. So redefining `supervisor` or
  `quality_inspector` saved, said "redefined — effective immediately", and was
  silently undone by the screen's own eight-second refresh — with nothing
  anywhere to say it had happened. Redefining what a shipped role grants now
  clears its `builtin` mark: the role becomes this plant's own, the card stops
  calling it built-in, and it stops picking up capabilities that later
  versions add. A shipped role nobody has changed still gets the top-up, which
  is what it is for; saving one back without touching its capabilities (a
  better description, say) leaves it in the product's hands. Pinned by a test
  that redefines a role and then asks the screen again.
- **The `admin` role can no longer have `users.manage` taken off it.**
  Deleting the admin role was already refused, because an MES with no
  administrator is a plant nobody can administer; emptying it reached the same
  place by another door, and the screen that could grant the capability back is
  the one you would have locked yourself out of. `PUT /admin/roles/admin`
  without `users.manage` is now a 400 that says so.
- **The role edit form can be left.** Pressing Edit fills the "Define a role"
  form in place; there was no way back out, so the panel stayed stuck
  redefining that one role until the page was reloaded — and the next Create
  overwrote the role instead of adding one. The form now names which role it
  is editing and carries a Cancel.
- **`fsmes sweep` scores every variant against its own data.** A sweep
  generated a fresh hour of line data per variant and then started each plant
  from the compiled configuration with `replay_dir` swapped — but the replay
  reads `MES_REPLAY_DIR`, which is compiled out of the pack's `plant.toml` and
  did not move. Every variant replayed the pack's original hour, so
  `fsmes sweep machining -k buffer_capacity=2,6,20` printed three readings of
  one run and any spread it showed was noise. Each variant is now built from a
  copy of its pack rewritten to point at that variant's data and recompiled, so
  the setting and the intention agree by construction; the rule and now the code
  are shared with `fsmes lab`, which had solved it the same way. The comparison
  table, the JSON it writes and each run's log name the directory a variant
  replayed, so two rows about one hour can be told from two rows about two.

- **The container image no longer publishes before the release is approved.**
  `release.yml` gated PyPI and the GitHub Release behind the reviewer-approved
  `pypi` environment, but the `image` job ran beside that gate rather than
  behind it. On 2026-09-14 the v0.2.0 run waited for approval while
  `ghcr.io/factorysemantics/fsmes:0.2.0` was already served and `latest`
  pointed at it, with PyPI still on 0.1.2 and no GitHub Release: for the
  length of the wait, anyone pulling `latest` got a version nothing else in
  the project acknowledged. The push, the cosign signature and the SBOM move
  into a new `image-push` job that `needs: pypi`, so they cannot start until
  that approval lands. The build stays where it was and now pushes nothing on
  either path, and `pypi` needs it — so a Dockerfile that no longer builds
  stops the run before the reviewer is asked, rather than after PyPI has
  published. A `workflow_dispatch` rehearsal still builds both platforms and
  publishes nothing. What is published has not changed, only when.

- **`deploy/promote.sh` can promote a fleet, and can undo one.** It defaulted
  `FSMES_PLANT_REGISTRY` to `fleet.toml` and then read `plants` — the table a
  fleet file stopped having when a plant became a pack — so at 0.2.0 it could
  not promote a fleet at all. It now asks `fsmes fleet plan --json` instead of
  reading the file a second time in bash. Four more things it got wrong: it
  fetched `origin` rather than the remote the release is on (now
  `FSMES_PROMOTE_REMOTE`, default `public`, and a tag that is not on it, or a
  local tag of that name pointing elsewhere, is refused before anything
  stops); its backups covered only file plants, so a PostgreSQL plant had
  none (now `pg_dump -Fc`, with `pg_restore --clean --if-exists
  --single-transaction` to put it back, and a plant whose database it cannot
  copy refuses the whole promote); it backed up while the plants were still
  running; and its health check believed a CLI in its own shell rather than
  the plant. Every PostgreSQL call now carries `PGOPTIONS='-c
  statement_timeout=0'`, and the health check reads `/health` and `/pack`
  from each plant — the plant must call itself by the name the fleet knows it
  by and say its schema is at head. `tests/test_promote_script.py` runs the
  whole script against two fake plants with recorders in place of
  `systemctl`, `uv`, `pg_dump` and `pg_restore`.

- **`fsmes db-status` and `fsmes pack status` report on the database they
  were pointed at, and say which one.** Both read whatever database the
  *process* was configured for, so on a machine where that is the default
  SQLite file they answered about a file nobody had asked about. On
  2026-09-14 a promote ran `fsmes pack apply` — which migrated the plant's
  PostgreSQL to head and said so — and then `fsmes db-status` in the same
  shell, which said *"There is no database yet"*; the script read that as
  failure and rolled a healthy plant back. `fsmes pack status` on the same
  migrated plant said the schema had *never been migrated* while `GET /pack`
  on that plant said it was at head.

  - `fsmes db-status` and `fsmes init-db` take **`--pack <dir>`** or
    **`--plant <name>`** (a plant in the fleet file), and print **which
    database they are looking at on the first line, every time** — including
    with neither, where the line says the database is only the process
    default. A stray database created beside a real one, and a status read
    off the wrong one, both start with that line being absent.
  - `fsmes pack status` reads the pack's own `[storage] database_url`, with
    the password put back from the file `database_password_file` names, and
    prints that database above the schema line. A pack that names no
    database says so rather than answering about a default.
  - `fsmes pack status`, `GET /pack`, `fsmes fleet status` and
    `fsmes db-status` take the schema answer from **one function**, so they
    cannot disagree about a database they were all pointed at.
  - `fsmes pack apply` honours `database_password_file` for the first time;
    the password merge is now one function shared with the fleet and with
    the plant a fleet starts.

### Honesty

- **"Active orders" counted the twenty-five orders listed under it.** The
  floor's tile summed the order card's own page, so a plant with sixty
  released orders was told it had twenty-five, and one with two hundred was
  told the same. It is a count over the whole table now, and agrees with
  `GET /workorders/summary`. Plant OEE had the same shape of error the
  moment the machine list became a page, and is computed for the whole
  scope; so is "machines running". No migration: nothing stored changes,
  only what the three tiles report, and they report more than they did.

- **A database that could not be reached is no longer reported as one that
  has never been migrated.** `fsmes pack status` and `GET /pack` caught every
  connection failure and rendered it as *not stamped; this database has never
  been migrated*, and `fsmes fleet status` rendered the resulting null as
  *behind head*. Nobody-answered and never-migrated are different facts about
  a plant, and a deployment script acting on the second when the first is
  true will roll back a plant that is fine — which is what happened. All four
  now say the database did not answer, and `at_head` on `GET /pack` is
  tri-state with an `answered` field beside it saying which null this is.
  **If you gate a deploy on these commands**, note that they now also exit
  non-zero when they cannot reach or cannot identify the database, where
  before some of those cases exited zero or reported an empty database.

## [0.2.0] — 2026-09-14

Thirty pull requests, #8 to #37, since 0.1.2 on 2026-09-08. All thirty were
opened by the maintainer, [@kalwei](https://github.com/kalwei); there are no
outside contributors to thank yet. Within each section the entries run in the
order a plant person would read them: the ERP and its connectors first, then
the pieces a first real plant needs, then plant packs and the fleet, then
packaging and CI. **Read the Honesty section before upgrading a plant anyone
reads numbers off** — it holds every change to what a number, a file name or a
setting means, each with its migration line.

### Added

- **The outbox is a domain event log.** It carried ERP confirmations and
  nothing else, because the ERP sync read every pending row as one. It now
  selects the kinds its contract can parse, which leaves room for the plant
  events no ERP asked for: **equipment state changes** (the state entered,
  the reason, the state left and how long that had been open) and **order
  holds and resumes**, with the reason a hold always carries. Each is
  written in the same transaction as the fact it describes, so an event
  cannot exist without its fact or a fact without its event. The
  unified-namespace publisher picks them up with no change: a machine going
  down reaches
  `umh/v1/<enterprise>/<site>/…/<machine>/_mes/equipment_state_change`, and
  a hold reaches the site. `MES_OUTBOX_DOMAIN_EVENTS=false` keeps the log to
  what the ERP is owed. See [the unified namespace](docs/operate/uns.md). ([#15](https://github.com/factorysemantics/factorysemantics-mes/pull/15))

- **`fsmes erp setup` and `fsmes erp check`.** The ERPNext connector needs
  five custom fields on Work Order (`custom_mes_synced`,
  `custom_mes_good_qty`, `custom_mes_scrap_qty`, `custom_mes_over_qty`,
  `custom_mes_lot`). Until now the only thing that created them was a demo
  seeding script in `labs/`, which is not in the wheel — so nobody who
  installed the package could create them at all. The definitions moved into
  the package, `fsmes erp setup` creates any that are missing and is safe to
  run twice, and `fsmes erp check` says in words whether the URL, the
  credentials, all five fields and the company are each in order, exiting
  non-zero if the connector would not work. `fsmes run-erp-sync` runs the field check when it
  starts and says so on the console; it still starts, because an ERP that is
  briefly unreachable is not a reason to refuse to run. The `labs/` seeder
  now uses the same definitions.
  See [the ERPNext connector](docs/operate/erpnext.md). ([#16](https://github.com/factorysemantics/factorysemantics-mes/pull/16))

- **The ERPNext connector is tested against a real ERPNext.** A new
  `ERPNext (live)` job stands up ERPNext v15.120.0 in containers pinned to
  image digests (`labs/erpnext/`), seeds the plant, and runs the whole round
  trip: a Work Order submitted in ERPNext, pulled by the connector,
  acknowledged, confirmed, and then checked by reading ERPNext's own
  documents back — the custom fields, `produced_qty`, one submitted
  Manufacture stock entry, the comment. A retried confirmation is asserted to
  leave exactly one stock entry. The old live test fetched orders, asserted a
  list, and was deselected by CI's own settings, so it had never run. The job
  is not on every pull request: it takes about three and a half minutes and
  runs only when the connector, its tests or its fixture change. See
  [the ERPNext connector](docs/operate/erpnext.md). ([#17](https://github.com/factorysemantics/factorysemantics-mes/pull/17))

- **A connector contract, so the next ERP is not the first one all over
  again.** The ERP port had three methods — fetch, acknowledge, confirm —
  and they said nothing about the three things that actually bit the
  ERPNext connector: what has to exist on the far side, how a write is
  proved to have landed, and how a person checks it before trusting it.
  Each was fixed in ERPNext-specific code, so Odoo or SAP or Oracle would
  have rediscovered all three. The port now carries **`requirements()`**
  (what this connector needs on the ERP side, as data a person can act on),
  **`setup()`** (create or verify it, idempotently) and **`check()`**
  (connectivity, credentials and requirements in plain language, non-zero
  when something is wrong). All three have defaults, so a transport that
  needs nothing — and a connector written against the older three-method
  port — keeps working. **`fsmes.integrations.erp.conformance`** ships
  inside the package: eight obligations any connector can be run against
  without vendoring this project's tests, each of them something a
  connector got wrong once. Every adapter that ships passes it.
  [Writing an ERP connector](docs/develop/erp-connectors.md) is the page
  for the person who would write the next one, and decision record
  [0020](docs/decisions/0020-what-supported-means-for-an-erp-connector.md)
  says what this project requires before it calls one supported. Odoo, SAP
  and Oracle are still not written; the contract and the suite exist, the
  connectors do not. ([#18](https://github.com/factorysemantics/factorysemantics-mes/pull/18))

- **`fsmes erp requirements`.** What the configured connector needs on the
  ERP side, and which of it the MES can create itself — the list to send
  whoever administers the ERP, who is usually not the person running the
  MES. ([#18](https://github.com/factorysemantics/factorysemantics-mes/pull/18))

- **A unified-namespace publisher.** `fsmes uns publish` relays the MES's
  own event stream — the transactional outbox the ERP connector already
  delivers from — to an MQTT broker as JSON, under an ISA-95 topic tree
  read out of the equipment model (`umh/v1/<enterprise>/<site>/<area>/<line>/<machine>/_mes/<kind>`).
  Broker, credentials, prefix and the enterprise and site names are
  settings; every rung between them comes from the plant's own equipment
  codes, at whatever depth it modelled them. Delivery is at-least-once with
  the outbox's retry, backoff and dead-letter behaviour, and every payload
  carries the event id a consumer dedupes on. The MQTT client is the new
  `[mqtt]` extra, so the core install does not grow a dependency;
  `MES_UNS_MODE=log` prints the whole namespace without a broker.
  `fsmes uns topics` and `fsmes uns queue` show the tree and the backlog.
  Off by default. See [the unified namespace](docs/operate/uns.md). ([#11](https://github.com/factorysemantics/factorysemantics-mes/pull/11))

- **Shadow mode: `MES_SHADOW=true`.** One setting that lets this MES watch a
  real plant and change nothing in it. It reads the OPC UA tags and books
  production exactly as it would in charge; every path by which it could
  reach past its own database is shut. No setpoint reaches a machine (the
  order-code write-back included, which was never approval-gated); no live
  ERP is contacted; nothing is published to a broker; the ERP file adapter
  reads its inbox and moves nothing, because that folder may be the
  incumbent's; the optional cloud model is refused, so the plant's numbers
  stay on the box; `fsmes demo` refuses to run a fake plant beside a real
  one. `MES_ERP_MODE` may only be `off` or `file` and `MES_UNS_MODE` only
  `off` or `log` — anything else refuses to start, in one sentence naming
  the variable. A mode you never set is settled rather than refused.

  Every outbound path in the package is listed in one place,
  `fsmes/shadow.py`, with what shadow mode does to each and why; a test
  walks each closed path and holds the refusal, and a second test scans the
  source for outbound primitives so a new path cannot be added without the
  register hearing about it. It shows on every screen as a bar that cannot
  be dismissed, in `fsmes info`, at `GET /health` and `GET /shadow`, in the
  MCP server's description and per plant in `list_plants()`, and in the
  audit trail as `shadow.on` / `shadow.off` at start-up. There is no runtime
  toggle: leaving shadow mode is a restart.
  See [running beside an existing MES](docs/operate/shadow-mode.md). ([#21](https://github.com/factorysemantics/factorysemantics-mes/pull/21))

- **A second front door: events other systems tell this MES.** The OPC agent
  is how the MES sees a plant; it is not how it learns why a machine stopped,
  what an inspector measured, or how many units somebody counted by hand.
  Those are typed into whatever system the plant already has, and until now
  none of it could reach the MES. `fsmes.integrations.inbound.contract` is
  three typed shapes — **`DowntimeLabel`**, **`QualityResult`**,
  **`ManualCount`** — each carrying who supplied it (`source`, a free name
  such as `replay:incumbent-mes`, plus a `source_kind` category), the
  supplier's own id (`external_key`), and when the *supplier* recorded it
  (`recorded_at`). `fsmes.services.inbound` writes them through the services
  that already own the rules, deduplicating on
  `(source, kind, external_key)`, so the same file delivered twice changes
  nothing. It matters most for a shadow run: technicians label stops in the
  incumbent, and a shadow that never hears those shows an unlabelled stop
  where the incumbent shows a reason code, which reads as a data problem in
  the shadow when it is a plumbing problem. ([#22](https://github.com/factorysemantics/factorysemantics-mes/pull/22))

- **The first inbound driver: files in a folder.** `fsmes inbound watch`
  reads CSV — or the same rows as JSON — from one inbox per event type,
  through a column mapping that is *your* configuration and not code. No
  input file is ever deleted: it moves to `processed/`, or to `rejected/`
  when nothing could be taken from it, and a rejects report beside it states
  the file's totals and the first reason for each row refused. `fsmes
  inbound check` says whether the mapping and the folders are usable before
  any file exists. [Feeding the MES what people typed
  elsewhere](docs/operate/inbound.md) is the page. A SQL poller and an MQTT
  subscriber are the same three shapes over a different transport, and are
  not written. ([#22](https://github.com/factorysemantics/factorysemantics-mes/pull/22))

- **The confirmation handoff: a published schema, worked example files and
  a validator.** A plant running this MES in shadow mode has no live ERP
  link, and its people still have to answer whether what it *would* send
  is correct. That answer used to live in Pydantic models, an XML renderer
  and a symmetry test, none of which an ERP analyst can read. Now the
  contract is published as a **JSON Schema, generated from the same models
  that write the files**, with every field carrying what it means on the
  floor, where the MES gets it, and when it is null and why null is the
  honest value ([the contract](docs/reference/erp-confirmations.md)); six
  **worked example files** — a clean operation, one with scrap and consumed
  lots, an over-run, a completion with a lot, one carrying an over-run, one
  with no lot — generated from a run of the demo plant rather than typed,
  as JSON and B2MML side by side, and pinned by a test that regenerates
  them; and **`fsmes erp validate <path>`**, which checks a file or a whole
  outbox against the contract and the house rules with no ERP, no connector
  and no database, exiting non-zero so it can gate a deployment. Problems
  and notes are kept apart: a negative work in progress, a completion with
  no lot and a missing cost centre are reported and do not fail, because
  every one of them is a fact the MES states on purpose.
  [The confirmation handoff](docs/operate/confirmation-files.md) is the page
  for the ERP team, and it says plainly that the contract is SAP-*shaped*
  and that no SAP has consumed one of these. ([#23](https://github.com/factorysemantics/factorysemantics-mes/pull/23))

- **`fsmes backup` and `fsmes restore`.** A plant that cannot restore has no
  backup, and until now the only copy anything made was the one
  `fsmes plant <name> migrate` takes before a schema change. `fsmes backup`
  writes one timestamped folder holding the database, the tag map, the line
  layout and the **OPC UA client certificate** — the last is the one people
  forget, because minting a new one means asking whoever administers the OPC
  server to trust it again. On SQLite the copy goes through SQLite's own
  online backup, so it is safe while the agent is running and cannot lose a
  transaction still sitting in the write-ahead log, which a plain file copy
  of the `.db` does. It never copies `.env`: that holds the OPC password, the
  ERP credentials and the token signing key, and a backup folder gets mailed
  around. The manifest states its own totals and names everything it did not
  copy and why. On a server database it copies no database at all and says
  so in those words, rather than handing somebody a folder that looks like a
  backup and has no production record in it. `fsmes restore` checks every
  file against its recorded hash before writing anything, refuses to write
  over a database that is already there without `--force`, has a `--dry-run`
  that proves a backup is restorable without touching the plant, and reads
  the row counts back out of the restored file rather than repeating the
  manifest. Files land where the settings of the machine being restored to
  say — a restore onto a new PC is a different `.env`, not a different
  backup. [Backup and restore](docs/operate/backup.md). ([#24](https://github.com/factorysemantics/factorysemantics-mes/pull/24))

- **`fsmes shadow scorecard` — the instrument a shadow run is judged with.**
  Running this MES beside the one in charge only answers anything if
  somebody puts the two records side by side, and until now the only
  comparison in the package was `fsmes score` against a simulated plant,
  whose truth is scripted. A real plant's truth is whatever the incumbent
  booked, and that arrives as a file a person exported, not as an API. The
  command reads that export — CSV or JSON, a documented generic column set,
  with the incumbent's own headings and order and equipment codes mapped in
  a **config file rather than in code** — and this MES's own confirmations,
  either the shadow outbox folder or the outbox in its database. It reports,
  per order and per operation, where the two agree and where they do not on
  good quantity, scrap, start, end and duration, within tolerances the plant
  sets. Output is the terminal, a JSON, and one self-contained HTML page
  with no scripts and nothing to fetch, because a plant network often cannot
  reach the internet and the page gets mailed around.
  **Three things it deliberately will not do:** say which side is right — it
  names the difference with both records' numbers and stops; read a blank
  cell as zero — a field one side never stated is *not compared*, never an
  agreement; or add operations up into an order total, because good units at
  two operations of the same order are usually the same units. Every report
  ends with *what this cannot tell you*: orders only one record holds,
  comparisons that could not be made, and the periods neither record covers.
  See [the shadow scorecard](docs/operate/shadow-scorecard.md). ([#27](https://github.com/factorysemantics/factorysemantics-mes/pull/27))

- **`fsmes inbound subscribe`: the MES listens to the plant's MQTT broker.**
  It has published to a broker since the unified namespace landed and could
  not hear. Two kinds of thing arrive and are kept apart. **Tag values** — a
  counter, a state word, a process value from a gateway — are machine data,
  wired in an `mqtt` section of the tag map beside the OPC machines so one
  document describes one plant, and held to the OPC agent's own discipline:
  a delta is the rise of a monotonic total, an unmapped state word is
  refused rather than guessed at, and a reading for a machine this MES does
  not hold is refused rather than inventing the machine. **Inbound events**
  are the contract already there: a stream in the mapping file gains a
  `topic`, and the mapping, the parsing, the deduplication and the writers
  are the folder driver's, unchanged. Off by default; needs the same
  `[mqtt]` extra as the publisher, and its own client id, because a broker
  disconnects the older session when two clients share one. Nothing here
  publishes, and shadow mode does not gate it — being told things is the
  opposite direction from changing something. No broker has been tested
  against it; the suite drives it with a fake source.
  See [the inbound page](docs/operate/inbound.md#the-broker-mqtt). ([#28](https://github.com/factorysemantics/factorysemantics-mes/pull/28))

- **The second inbound driver: `fsmes inbound poll-sql`.** A CSV export needs
  a person every day; a query needs a person once. Where the system holding
  downtime labels, quality results or counts has a database you can be given
  read-only credentials to, the poller runs **your** query against it on a
  schedule and feeds the same contract. The query is configuration, not code,
  and emphatically so: this repository contains no commercial system's
  schema, table names or SQL, and cannot — the docs describe the *shape* a
  query must return and say nothing about where to find it in any product.
  The shipped example reads a SQLite file the page tells you how to make, and
  names no product. It only reads: the query is inspected before it is ever
  sent (one statement, `SELECT` or `WITH`, no word that could change
  anything — a data-modifying CTE included), the connection is opened
  read-only and given a statement timeout wherever the dialect has a way to
  say so, and where a dialect has neither, `fsmes inbound sql-check` says so
  in those words rather than staying quiet. Every row is fetched and the
  connection closed **before** this MES writes anything, so a slow write here
  can never become a lock in a system somebody else depends on. New setting
  `MES_INBOUND_SQL_FILE`; migration `d9a3f61c48e0` adds `inbound_watermarks`,
  additive and empty for a plant that never runs it. ([#29](https://github.com/factorysemantics/factorysemantics-mes/pull/29))

- **A cursor that will not step over a row it could not read.** The poller
  keeps how far it has read as the supplier's *own* ordering value, handed
  back as the text that column gave — a timestamp re-read into this MES's
  convention would move the boundary by the supplier's UTC offset, and the
  rows in that gap would go missing with nothing to say so. `start_from` is
  required and has no default, because the two values this MES could guess
  are "now", which silently skips that system's backlog, and "the
  beginning", which pulls ten years through a plant network. When a row
  cannot be recorded the cursor **stops at that row**: the rows after it are
  still recorded, but the next pass asks for the bad one again and keeps
  saying which row and why, on every pass, until somebody deals with it.
  Stepping over it is a person's decision, made in words with `fsmes inbound
  sql-watermark --set ... --force`, which prints what will never be read
  before it does it. Correctness does not rest on the cursor: `inbound_events`
  is still keyed on the supplier's own id, so a cursor that is behind costs a
  re-read and changes nothing. ([#29](https://github.com/factorysemantics/factorysemantics-mes/pull/29))

- **M8 designed before it is built — docs only, no product code.**
  [Plant packs and the fleet console](docs/design/m8-packs-and-fleet.md)
  states what a plant is today with file paths, measures the two lab plants
  against the milestone's own *done when*, proposes what a pack may and may
  not contain, scopes the console, and breaks the work into four
  pieces. Decisions [0021](docs/decisions/0021-one-database-per-plant.md),
  [0022](docs/decisions/0022-what-a-plant-pack-may-contain.md) and
  [0023](docs/decisions/0023-the-fleet-console-observes.md) are **proposed**,
  not accepted. ([#31](https://github.com/factorysemantics/factorysemantics-mes/pull/31))

- **Decision 0023 revised: the console manages only the plants it owns.**
  The first draft made the fleet console purely read-only. The maintainer
  read it and said read-only is not the safety property he needs — managing
  a lab fleet of simulated plants one plant at a time is friction with no
  threat model behind it, while pushing to somebody else's plant is a
  remote-execution path into their machinery. The rule is now *the console
  may act only on plants it owns; for every other plant it observes and
  cannot push*. Ownership is defined so a test can check it: this
  installation created the plant from a pack and recorded it with an
  `instance_id`, the plant's own `/health` returns that same id, and the
  operator gave it a path — same host and user, or a credential a person
  typed. Managing means five verbs and no more — create, start, stop, apply
  a pack, show drift — and never writing to a PLC, an ERP or production
  data. The write verbs live in `fsmes fleet`, a local command; the page
  stays read-only and its credential stays the read-only machine role.
  [Design §8 and piece 4](docs/design/m8-packs-and-fleet.md) match, and
  [0023](docs/decisions/0023-the-fleet-console-observes.md) is still
  **proposed**. Docs only, no product code. ([#32](https://github.com/factorysemantics/factorysemantics-mes/pull/32))

- **A module registry, so a plant can switch a module off.**
  [Decision 0002](docs/decisions/0002-kernel-and-modules.md) said a small
  kernel is always present and everything else is a module; until now that
  was a sentence rather than a mechanism, because `api/app.py` mounted all
  twenty-three routers unconditionally and `mcp_server.py` registered all ten
  agent tool files at import. `src/fsmes/modules.py` is now the one place
  that says which modules exist and, for each, the routers it mounts, the
  screens it serves, the agent tools it registers, the settings it owns and
  the tables its rows live in. `MES_MODULES` filters it — `all` (the
  default), `all,-quality`, or `quality,maintenance` — and a name this
  version does not have is refused at start-up rather than ignored. Off means
  **not served**: the routes answer 404, the module is absent from
  `/openapi.json`, its screens are gone and its tools are not registered. Off
  does **not** mean not stored: the schema is one chain for every plant, so a
  disabled module's tables and rows are untouched and come back when it is
  switched on. Nine of the twenty-three modules are the kernel and cannot be
  switched off; fourteen can.
  [How-to — switch a module off](docs/operate/modules.md). This is M8 piece 2
  of [the design](docs/design/m8-packs-and-fleet.md); piece 3 moves the
  setting into a plant pack's `[modules]` table. ([#33](https://github.com/factorysemantics/factorysemantics-mes/pull/33))

- **The two guard tests M8 promised: `core_purity` and `no_tenant_literals`.**
  Named on the roadmap since M8 was planned and, until now, nowhere else in
  the repository. `tests/test_core_purity.py` holds the layering rule that
  lived only as a docstring in `fsmes/kernel/__init__.py`: the kernel imports
  nothing from a module — which is what makes a module switchable at all —
  and no layer imports a layer above it. Six upward imports exist, each
  allowed in one of two tables with a reason that is checked rather than
  asserted. `tests/test_no_tenant_literals.py` holds house rule 4: no lab
  plant's name, equipment code or material code may appear in code under
  `src/`. It builds its forbidden list from `labs/` rather than from a typed
  list, so a new lab plant extends the guard instead of escaping it, and it
  scans code rather than prose — a comment naming the plant a finding came
  from is provenance, and the house rules ask for it. ([#33](https://github.com/factorysemantics/factorysemantics-mes/pull/33))

- **A plant knows its own name, and what clock it keeps** — M8 piece 1 of
  [the design](docs/design/m8-packs-and-fleet.md). `MES_PLANT_NAME` is now a
  real identity: **required** for any deployment that is not a laptop, and
  validated as a code (the characters a namespace topic segment keeps
  unchanged) so `/health` and the broker can never disagree about what this
  plant is called. It reaches every surface a reader has — `/health`,
  `/shadow`, a label on every `/metrics` series, the dashboard header,
  `fsmes info`, the backup manifest, `list_plants()` over MCP — so a console
  can tell two plants apart from what they say about themselves rather than
  from the address it dialled.
  New `MES_PLANT_PROFILE` (`laptop` | `plant` | `fleet`) says what shape the
  deployment is; it is what lets a plant node say *I am a plant, not a
  laptop* in one word. A laptop with nothing set is the demo plant and keeps
  working exactly as before.
  New `MES_PLANT_TIMEZONE` is a real IANA zone, validated at start-up (on
  Windows it needs the `tzdata` package, and the refusal says so). Every
  wall-clock boundary the MES draws is now drawn on it: the shift calendar,
  and "today" on the gauge register. On the screens, every clock, stamp, due
  date and chart axis reads in the plant's zone instead of the browser's.
  Left unset it is the machine's own zone, and **every reader is told it was
  defaulted** — including, honestly, when the machine's zone has no name to
  report. ([#34](https://github.com/factorysemantics/factorysemantics-mes/pull/34))

- **A plant is a pack** — M8 piece 3 of
  [the design](docs/design/m8-packs-and-fleet.md), building
  [decision 0022](docs/decisions/0022-what-a-plant-pack-may-contain.md). One
  directory, `plant.toml` and the files it names, is the complete, versioned,
  checked answer to *which plant is this?*: identity, clock, profile, which
  modules this plant serves, what it calls things, where its data lives, its
  tag map, its master data and its boundary mappings.
  **`fsmes pack check`** refuses one offline — no database, no network, no
  plant — with one sentence per problem and *every* problem rather than the
  first: an unknown key or table, a bad or missing time zone, a `[words]`
  entry that renames a state, a capability, a role, an event kind or a KPI, a
  module this version does not have, a `requires` this release does not
  satisfy, a file that is missing or that its own reader will not accept, and
  a secret or a script refused by name. What it cannot prove without a plant
  it reports as **unknown**, never as passing.
  **`fsmes pack apply`** checks first, adopts the pack's settings, brings the
  schema to head (pack before database), seeds the master data the pack
  carries and records what was applied. **`fsmes pack status`** answers which
  pack a plant runs, whether its files have drifted from the fingerprint that
  was applied, and what schema revision it is at — and says *never applied*
  rather than *no drift*, because those are different facts.
  **`fsmes pack migrate`** writes a pack from a plant registry entry, saying
  what it moved, what it dropped and why, and the one value it will not
  guess: a registry never held a time zone.
  New `MES_WORDS` is what a checked pack's `[words]` table compiles to, and
  the words ride on `/health`, `/shadow` and `fsmes info` beside the plant's
  name and clock.
  [The page](docs/operate/packs.md). ([#35](https://github.com/factorysemantics/factorysemantics-mes/pull/35))

- **`fsmes fleet` — the plants this installation owns** — M8 piece 4 of
  [the design](docs/design/m8-packs-and-fleet.md), under
  [decision 0023](docs/decisions/0023-the-fleet-console-observes.md) as
  revised. Six commands — `create`, `start`, `stop`, `apply`, `status`,
  `list` — that manage a fleet of plants built from
  [packs](docs/operate/packs.md), and that **refuse any plant this
  installation did not create**. The safety property is not read-only; it is
  *cannot touch a plant it does not own*.
  A plant is owned when three things hold: this installation created it and
  recorded that in `ownership.toml`; the plant returns the same random
  **instance id** on `/health` that was written into its data directory; and
  a path to act on it exists — same host and OS user, or a credential a
  person named for another host. Any one missing and the plant is observed
  only. The id is a **continuity check, not an authentication**, and the
  [page](docs/operate/fleet.md) says so plainly.
  Every write path calls one ownership function before it does anything, and
  a ratchet in the suite reads the source to hold it there: a new verb that
  forgets the gate fails a test rather than shipping. Deleting the id from a
  plant's data directory gives ownership back, and two plants claiming one
  id is an error that refuses rather than a coin toss.
  `fsmes fleet` manages **plants, never production**: no PLC write, no ERP
  send, no order, no booking, no master data and no audit row. A plant it
  owns can be stopped; a plant it owns cannot be made to say it built
  something. Nothing starts a plant on its own — there is no reconciler.
  `/health` now carries `instance_id`, which is `null` for every plant no
  fleet tool created — null means *not owned*, never *probably fine*. ([#36](https://github.com/factorysemantics/factorysemantics-mes/pull/36))

- **The fleet console** — M8 piece 4's other half, and what closes the
  milestone. `fsmes fleet console` serves one page that shows every plant in
  the list — the packs this machine runs, the plants this installation
  created, and the plants a person added to watch — each polled on `/health`
  and `/pack`: name, whether it is owned, whether it answered, profile,
  clock, shadow mode, which pack and whether it has drifted, schema revision
  against head, which modules it serves, and when it last answered. At the
  top, the total: *"3 plants, 2 answered, 1 unknown"*.
  **A plant that did not answer is `unknown`** — never healthy, never down —
  and it is not owned while it is silent, because nothing can corroborate
  the instance id. Nothing is aggregated across plants: a fleet OEE is a lie
  unless every plant is the same shape.
  **The page has no write path.** It declares two routes, both GET; it
  imports no fleet verb, so the command is not reachable from the process
  serving the page; its script makes one GET to its own server; and it holds
  no credential, because everything it asks a plant is public. Four tests
  parse the source to keep each of those true.
  New **`GET /pack`** on every plant: which pack it was given, when and by
  which product version, whether the files have drifted since, the schema
  revision against head, and the modules this plant serves — four separate
  facts, never merged into one light, with `drifted: null` for *never
  applied* and every unknown carrying its reason. ([#37](https://github.com/factorysemantics/factorysemantics-mes/pull/37))

- CI builds the wheel and runs `fsmes demo` from it in a fresh virtual
  environment in an empty directory, on every pull request and every push to
  `main`. The same check runs against the exact wheel a tag is about to
  publish, before it reaches PyPI. This is the clean-machine install that
  0.1.0 needed and did not get. ([#9](https://github.com/factorysemantics/factorysemantics-mes/pull/9))

- `tzdata` is now a dependency **on Windows only**. Windows ships no IANA
  time-zone database, so `zoneinfo` there cannot resolve `America/Chicago`
  — or even `UTC` — without it, and the inbound driver reads a plant's
  exports in the plant's own local time. Linux and macOS have a database
  already and gain nothing. ([#22](https://github.com/factorysemantics/factorysemantics-mes/pull/22))

- **Ten decision records, 0009 to 0018.** The choices made in the week the
  repository went public were in chat logs and commit messages and nowhere a
  reader could find them: the organisation account rather than a personal
  one, Discussions and no forum, MkDocs Material with generated reference,
  Contributor Covenant 3.0, one-page governance, `0.1.0` as the first public
  version, the MCP registry name, no trademark registration, one public demo
  that is gated and bounded, and going public on 2026-09-08 with the pre-tag
  check that release night asked for. [The index](docs/decisions/index.md). ([#8](https://github.com/factorysemantics/factorysemantics-mes/pull/8))

### Honesty

- **A replayed run's booking comparison states a band, and says which
  direction is sharp.** A CSV replay loops: when the file runs out the counters
  wrap to zero and the hour starts again, and a run is always left playing a
  little past the end because the agent needs time to book what it has already
  read. Units made in that overlap the MES is right to book. So `fsmes lab`
  prints an **expected range** rather than a single number, states the overlap
  in line seconds, and says what follows from it — under-booking is a real
  finding at any speed, because the overlap can only ever add; over-booking is
  blurred by a band that grows with replay speed, and the sharp reading for
  that class of fault is the over-run the MES reports about itself.
- **Counters are summed as deltas on both sides of every comparison.** A
  scripted counter reset zeroes the column the replay publishes, so the last
  row of a station's data holds what it made *since* the reset and not what it
  made in the hour. The lab's first run read it as the hour's total and
  accused the MES of booking four thousand units it had not — the truth was
  wrong, not the plant. Both sides now count the same way the MES books: a
  counter that goes backwards has been re-baselined, and the step across the
  reset is dropped rather than counted.

### Changed

- **`fsmes erp setup` and `fsmes erp check` no longer know that ERPNext
  exists.** They are the port's `setup()` and `check()`, so they act on
  whatever `MES_ERP_MODE` names — including a connector published on its
  own. They used to refuse every mode but `erpnext`, which was exactly
  backwards. `fsmes run-erp-sync` runs the configured connector's `check()`
  at start-up instead of ERPNext's field check, and still starts, because
  an ERP that is briefly unreachable is not a reason to refuse to run.
  `check` reports what it could not verify as **unknown** rather than
  counting it as working: the REST connector can prove it reached the ERP's
  order list and cannot prove the confirmation endpoint works without
  posting a confirmation, so a green check that skipped something says
  `Ready, as far as anything above was checked.` ([#18](https://github.com/factorysemantics/factorysemantics-mes/pull/18))

- **[Compatibility](docs/operate/compatibility.md) has a rule for
  connectors.** Every connector row states the exact version tested and the
  date it was tested, and a connector with no live test says so in those
  words. Three words are defined there and nothing uses others: supported,
  contributed, experimental. ([#18](https://github.com/factorysemantics/factorysemantics-mes/pull/18))

- **The unified-namespace publisher at plant volume.** It shipped off by
  default and carrying two event kinds; it now carries equipment state
  changes — one event per transition — and the first real plant will turn it
  on. Nothing it publishes changed: same topics, same payloads, at-least-once,
  oldest first. What changed is what a cycle costs. Measured on the fake
  broker, one full batch of 200 confirmations from two machines:
  **1602 statements and 202 commits before, 210 statements and 3 commits
  after**, and 1000 equipment lookups down to 6. ([#30](https://github.com/factorysemantics/factorysemantics-mes/pull/30))
  - A topic depends on the kind and the machine and nothing else, so it is
    worked out once per machine per cycle instead of once per event.
  - The results of a cycle are written in one transaction after the last
    publish, rather than one session, one row read and one commit each. A
    transaction still never spans a publish — there is a test that watches
    for one.
  - QoS 1 waits for the broker to acknowledge every event, so publishes now
    go out in groups the client can hold in flight (`MES_UNS_INFLIGHT`,
    default 10; set it to 1 for strictly one at a time). Events go onto the
    wire oldest first as before; the order the broker acknowledges them in
    was never promised and is not promised now.
  - Enrolment reads the ids it needs rather than hydrating every JSON
    payload in the backlog to find them, and `erp_messages` has an index on
    (direction, id) — the question both readers of the outbox ask.
  - **A backlog now drains at the broker's speed.** A cycle that filled its
    batch without a failure goes straight back for the next one instead of
    sleeping `MES_UNS_POLL_SECONDS`, which had capped catch-up after an
    outage at `MES_UNS_BATCH / MES_UNS_POLL_SECONDS` events a second however
    fast the broker was.

- **The night shift reads the plant registry instead of naming two plants.**
  `fsmes autoloop` hard-coded `bottling` and `machining` with their ports —
  a tenant literal inside the product, found by the new
  `no_tenant_literals` guard. It now runs against every plant in the registry
  this checkout points at, so a third plant joins the night shift by being in
  the registry. `MES_AUTOLOOP_PLANTS` narrows it to a comma-separated subset;
  `MES_AUTOLOOP_BOTTLING` and `MES_AUTOLOOP_MACHINING` are gone. ([#33](https://github.com/factorysemantics/factorysemantics-mes/pull/33))

- `fsmes demo` exits non-zero, with the reason, when its loop does not close:
  the order never completed, the ERP was never told, or no finished lot was
  booked. It used to exit 0 either way, which is why a wheel that booked
  nothing looked like a success. A final line now says which happened. An OEE
  component reported as null is still a closed loop — that is an honest
  answer, not a failure. ([#9](https://github.com/factorysemantics/factorysemantics-mes/pull/9))

- **The roadmap says where this actually stands.** *Where this stands* had
  been written before the repository went public and still read as a plan.
  It now states each numbered item's condition against the code, with file
  paths, and says plainly which parts are done, which are partly done and
  what is open in each. [ROADMAP.md](ROADMAP.md). ([#10](https://github.com/factorysemantics/factorysemantics-mes/pull/10))

### Fixed

- The ERPNext connector never acknowledged an order. Its `fetch_orders`
  returned plain dicts while the sync worker reads `request.code` off each
  one, so every inbound order raised `AttributeError` immediately after
  being imported, `custom_mes_synced` was never set, and the same order was
  re-imported on every poll. It now returns the `ProductionRequest` the
  adapter contract declares, and a test acknowledges what `fetch_orders`
  returned so the two cannot drift apart again. ([#16](https://github.com/factorysemantics/factorysemantics-mes/pull/16))

- `fsmes demo` crashed with `KeyError: 'lot'` at the end of a run. It printed
  whichever ERP confirmation happened to arrive last, and an operation
  confirmation has no finished-goods lot because an operation does not make
  one. It now looks for the order completion for its own order, and says so
  plainly if the completion has not arrived rather than crashing. ([#13](https://github.com/factorysemantics/factorysemantics-mes/pull/13))

- **The B2MML confirmation carries its idempotency key.** `message_key` — the
  only thing that stops one confirmation being posted twice — was never
  written into the XML, so a folder of operation confirmations gave a
  collector no way to tell a re-sent file from a second confirmation. It is
  the first element of both documents now, and a reader rebuilds it for
  files written before today with the same rule that made it. ([#23](https://github.com/factorysemantics/factorysemantics-mes/pull/23))

- **The session cookie is marked HTTPS-only behind TLS.** Writing the
  [TLS page](docs/operate/tls.md) turned this up: the dashboard's session
  cookie was `HttpOnly` and `SameSite=Lax` but never `Secure`, so a plant
  that had put a reverse proxy in front of the API could still have a live
  session sent back in clear over one stray `http://` link. The flag now
  follows the request's own scheme, which behind a proxy is the scheme in
  `X-Forwarded-Proto`. A laptop on `http://127.0.0.1:8000` gets no `Secure`
  flag, because there it is a cookie the browser silently drops. ([#24](https://github.com/factorysemantics/factorysemantics-mes/pull/24))

- **A backup's folder name and its manifest could name different seconds.**
  `fsmes backup` read the clock twice — once to build the timestamped folder
  name and again to write the manifest's `taken` — so when the two reads
  landed either side of a second boundary the folder said `…-113219` while
  the manifest inside it said `…:32:20`. One second, and it is the backup's
  own record of itself being wrong: a restore that trusts `taken` names a
  time the folder does not carry. It reads the clock once now, at the top of
  `back_up`, and uses that instant for both. It had also made the overwrite
  test race — it failed that way once on `windows-latest, 3.13` on a branch
  that does not touch backup at all — and a new test moves a fake clock
  forward on every read, so it can only pass for a backup that reads once.
  `restore` reads no clock and is unchanged. ([#26](https://github.com/factorysemantics/factorysemantics-mes/pull/26))

- `database is locked` under concurrent writes on SQLite. WAL and
  `busy_timeout` were set and their comment claimed that prevented it; they
  do not. A transaction already open when it first writes has to upgrade to
  SQLite's single write lock, and SQLite refuses that upgrade outright
  instead of waiting — `busy_timeout` covers waiting, not a refusal. The OPC
  agent's booking is that shape: it opens a savepoint per state change and
  writes inside it, which is why one CI run of `fsmes demo` lost two batches
  of plant readings to it. Transactions on SQLite now begin with
  `BEGIN IMMEDIATE`, so the refusal becomes a wait that `busy_timeout` does
  cover. `busy_timeout` is also set before the WAL switch rather than after
  it, so a new connection meeting a lock waits rather than failing. SQLite
  now serialises transactions rather than only writes, so no session may be
  held open across a network call; the OPC agent's adjustment loop was the
  one place doing that, and a test now keeps it that way. PostgreSQL is
  untouched — the change is gated on the SQLite dialect. ([#12](https://github.com/factorysemantics/factorysemantics-mes/pull/12))

- **`fsmes --version` told you the wrong version.** A 0.1.2 install answered
  `0.1.0`, because the number was written down twice — in `pyproject.toml`
  and again in `src/fsmes/__init__.py` — and only one copy was bumped for
  either release. It is written down once now: `src/fsmes/__init__.py` holds
  it, and hatchling stamps the wheel, the sdist and the container tag from
  that line, so a release bumps one file. The first question a new user asks
  their install now gets a true answer. A test compares what the CLI prints
  with the installed distribution's metadata, and the wheel check that runs
  on every pull request asks the built artifact the same question two ways
  and fails if the answers differ, so a mismatch cannot reach PyPI.
  `CITATION.cff` still carries a hand-typed version — the citation format has
  no way to read one from the package — so it was corrected from the stale
  `0.1.0` to `0.1.2`, and the release workflow now refuses a tag that
  disagrees with it. ([#14](https://github.com/factorysemantics/factorysemantics-mes/pull/14))

- **A list search ignored case on SQLite and not on PostgreSQL.** Every list
  search in the API — equipment, materials, routings, people, work orders,
  lots, maintenance plans and orders, specifications, non-conformances,
  certificates, the audit trail — is built on `LIKE`. SQLite's `LIKE`
  ignores case for ASCII and PostgreSQL's does not, so a plant on PostgreSQL
  got nothing back for a code typed in lower case where the same search on a
  laptop found it. They all say `ILIKE` now. Case-insensitive is what the
  product already meant: the document catalogue, the trigger list and the
  gauge register filter in Python on `.lower()`, and the SQL-side searches
  only agreed with them by SQLite's accident. Found by the new PostgreSQL
  cell. ([#19](https://github.com/factorysemantics/factorysemantics-mes/pull/19))

- **A PyPI install could not upgrade its own database.** `fsmes init-db`
  ran the Alembic migrations only when there was an `alembic.ini` in the
  working directory, and the wheel shipped neither that file nor the
  migration scripts. Outside a source checkout — which is every plant that
  installs from PyPI, and the way the engineers' guide says to install —
  it fell back to `create_all`: missing tables appeared, no `ALTER` ever
  ran, and it printed "Database schema is up to date" either way. The
  migrations now live inside the package (`src/fsmes/migrations/`), so the
  wheel carries them, and `fsmes.schema` resolves them from the package
  rather than from whatever directory you are standing in. `alembic.ini`
  remains for `python -m alembic` in a checkout; nothing at runtime reads
  it. **`fsmes db-status`** prints the revision a database is at and the
  revision the installed version expects, and exits non-zero when they
  differ. The `wheel-demo` check — already required — now also installs the
  release that is on PyPI, makes a database with it, upgrades that database
  with the wheel under test, and asks the database whether it is at head, so
  the upgrade path a plant takes is proven on the bytes that ship.
  [Upgrading between versions](docs/operate/upgrade.md) loses its
  run-from-a-checkout-at-a-tag workaround. ([#25](https://github.com/factorysemantics/factorysemantics-mes/pull/25))

### Honesty

- **`fsmes erp outbox` counts the ERP's own queue, not the whole log.** The
  same table now holds plant events waiting for a different reader, and
  counting those as pending ERP work would report a backlog that does not
  exist. The status counts cover the confirmation kinds only; the rest are
  stated as `other_outbound` and broken down by kind, so nothing is hidden
  either.
  **Migration:** anything reading `fsmes erp outbox` totals as *all* pending
  outbound work now needs `other_outbound` added to them; the status counts
  cover the confirmation kinds only. ([#15](https://github.com/factorysemantics/factorysemantics-mes/pull/15))

- `MES_ERPNEXT_COMPANY` now does something. It was defined and documented and
  nothing read it, so a shared Frappe bench handed this MES every company's
  work orders. Inbound orders are filtered by it. Its default changed from
  the demo's company (`ACME Beverages`) to empty, which means every company
  on the site — the right answer for a single-company ERPNext, and better
  than a default that silently imports nothing on a stranger's site. A bench
  holding more than one company's books must now set it.
  **Migration:** a Frappe bench holding more than one company's books must
  now set `MES_ERPNEXT_COMPANY`. Its default is empty, which means every
  company on the site; it used to be the demo's company, which on a
  stranger's site silently imported nothing. ([#16](https://github.com/factorysemantics/factorysemantics-mes/pull/16))

- **`docs/operate/erpnext.md` said the connector worked "with nothing
  installed on the ERPNext side". It did not.** It has always needed four
  custom fields on Work Order. The page now names them, says what creates
  them, and says what happens when they are absent. ([#16](https://github.com/factorysemantics/factorysemantics-mes/pull/16))

- **A confirmation to an ERPNext missing a field was recorded as delivered.**
  Frappe answers `200` to a `PUT` naming a field its doctype does not have
  and drops the value, so the MES marked the outbox message sent and the
  plant's counted quantity existed nowhere. Every write to a Work Order is
  now read back and compared with what was sent; a value that did not
  survive raises, so the confirmation stays in the outbox and retries and
  the log names the field. Rounding to the site's float precision is not
  treated as a loss. That Frappe drops the field silently is what its
  document layer does when read, but is **not verified against a live
  ERPNext** — see the connector page. ([#16](https://github.com/factorysemantics/factorysemantics-mes/pull/16))

- **What a missing ERPNext custom field does is now measured, not assumed.**
  Against ERPNext v15.120.0: a `PUT` to a submitted Work Order naming a field
  the doctype does not have returns `200`, and the value is neither stored nor
  returned. A site missing one of the MES fields therefore accepts a
  confirmation and silently loses whichever number that field carried. An
  HTTP success is not proof a value landed. The experiment runs on every live
  job, so a change of behaviour in ERPNext shows up there. ([#17](https://github.com/factorysemantics/factorysemantics-mes/pull/17))

- `docs/operate/compatibility.md` says ERPNext v15.120.0 is tested
  continuously and v16 is untested, in place of "tested against a development
  bench, not yet against a current stable release in a clean container". ([#17](https://github.com/factorysemantics/factorysemantics-mes/pull/17))

- **What ERPNext does with an over-run is now measured, and a refusal is
  never recorded as delivered.** The MES books every unit a machine counted,
  so an order for 400 that ran to 420 is confirmed as 420 good with 20 over.
  ERPNext has an over-production allowance of its own (Manufacturing
  Settings, zero out of the box), and nobody had asked it what it does.
  Measured against v15.120.0: inside the allowance it takes the whole
  quantity — `produced_qty` 420, one Manufacture entry of 420. Beyond it, it
  **refuses the entry whole** with HTTP 417 `For quantity 500.0 should not be
  greater than allowed quantity 440.0`, books nothing, and leaves
  `produced_qty` at 0. The connector no longer lets that pass as a transport
  failure: the confirmation is not delivered, and because a retry sends the
  identical entry and gets the identical answer, the message goes straight to
  `dead` in the outbox with ERPNext's own sentence as its error instead of
  spending eight attempts and an hour on it. A comment on the Work Order says
  what was refused, what the MES counted and what ERPNext said, so the person
  who has to decide can see all of it; `custom_mes_good_qty` and
  `custom_mes_over_qty` still carry what the machines counted, beside a
  `produced_qty` of 0. Nothing partial is posted in place of the refused
  entry. Once somebody raises the allowance or agrees what the ERP should
  hold, `POST /erp/outbox/{id}/retry` sends the same confirmation again. ([#20](https://github.com/factorysemantics/factorysemantics-mes/pull/20))

- **A unit a machine counted is never discarded.** A counter delta can carry
  more than one unit, so a booking can straddle the ordered quantity: the
  release check saw `16/15 good` on an order for fifteen, and the next unit
  the machine counted became a warning line and nothing else. All sixteen
  are booked, as before — the machine made them — and the order now says how
  far past it ran (`over_qty`, in `GET /workorders/{code}` and in the ERP
  order completion). Units counted when no operation is open are recorded as
  **unassigned production**: kept against the machine, with no guess about
  which order they belonged to, and listed with their totals at
  `GET /execution/unassigned` and on the machine page's Operate tab.
  See [decision 0019](docs/decisions/0019-count-everything-the-machine-counted.md).
  Migration: `a3f6c81d09e2` makes `production_logs.work_order_id` nullable.
  Existing rows are untouched. A plant that has been reading
  `SUM(production_logs.good_qty)` as order-attributed production should now
  filter on `work_order_id IS NOT NULL`, or read the wider number knowing
  what it includes. ([#13](https://github.com/factorysemantics/factorysemantics-mes/pull/13))

- **A new production source, `external`, because "we counted it" and "we were
  told" are different facts.** `ProductionSource` had `manual` and `opc`; a
  count that reached the MES from another system had nowhere honest to sit,
  and calling it `manual` would have claimed somebody typed it *here*.
  `external` is now the third value, with **`ProductionLog.source_system`**
  naming the system that supplied it. Null there means this MES counted it
  itself — there is no other system to name, and naming one would be a guess.
  **Migration `b5c1d09e73af`** adds `production_logs.source_system`,
  `equipment_states.reason_source`, `quality_checks.source_system` and
  `quality_checks.supplied_result`, and the `inbound_events` ledger. Every
  column is nullable and nothing existing is rewritten: a row from before the
  migration was observed by this MES, and null is the right answer.
  **What to check after upgrading:** any report that groups production by
  source, or that assumes `ProductionSource` has two values, now has a third. ([#22](https://github.com/factorysemantics/factorysemantics-mes/pull/22))

- **A supplied downtime label goes on a stop this MES observed, and never
  creates one.** The interval stays this MES's own observation; `reason` and
  `reason_source` record who named it. A label for a stop the MES never saw
  is refused and reported, because manufacturing an interval from another
  system's claim would put seconds into availability that nothing here ever
  watched, with no way afterwards to tell them from the real ones. A supplied
  label never overwrites one given here. ([#22](https://github.com/factorysemantics/factorysemantics-mes/pull/22))

- **A quality result keeps both verdicts when they disagree.** The `result`
  on a check is always this MES's own, from this MES's spec.
  `supplied_result` keeps the verdict the other system sent. Two systems
  disagreeing about the same reading is a finding about the two systems, and
  storing only one of them would hide it. A reading for a characteristic this
  MES has no spec for is refused rather than measured against an invented
  spec, and a gauge code this MES does not know is reported as untraceable
  rather than created. ([#22](https://github.com/factorysemantics/factorysemantics-mes/pull/22))

- **The downtime pareto says who named each stop.** Every bucket gains
  `labelled_by`, seconds by labeller: `here` is this MES's own, the rest are
  named by supplier. Supplied labels are counted the same as local ones —
  they are real evidence — but they are not the same claim, and a pareto that
  cannot separate them cannot be audited. ([#22](https://github.com/factorysemantics/factorysemantics-mes/pull/22))

- **A count supplied by another system with no order open here is kept, not
  refused.** It is booked against an open operation when there is one, and
  otherwise recorded as unassigned production against the machine with the
  supplying system named — the same rule the OPC path already had, for the
  same reason: the system that took the count had the order, and a unit the
  plant made may not disappear because this MES had nowhere tidy to put it. A
  count typed *into this MES* with no order open is still an error. ([#22](https://github.com/factorysemantics/factorysemantics-mes/pull/22))

- **A timestamp with no time zone, in a stream whose mapping does not say
  which zone that system writes, is rejected row by row.** Reading a local
  timestamp as UTC would move every stop in a shift by hours, silently. ([#22](https://github.com/factorysemantics/factorysemantics-mes/pull/22))

- **The file connector's outbound folder is deterministic.** Names lead with
  a six-digit sequence number, so sorting the folder by name replays the
  order the confirmations happened; the number is read back from the folder
  at start-up, so a restart continues rather than collides; each document is
  written to a `.part` file and renamed into place, so a collector never
  reads half a document; and anything outside `A-Za-z0-9_-` in an order code
  becomes a dash, so an order code cannot decide where a file lands. A
  collector that globbed `confirmation_<order>_*.xml` must now glob
  `*_<order>_op10.xml` or `*_<order>_completion.xml`.
  **Migration:** a collector that globbed `confirmation_<order>_*.xml` must
  glob `*_<order>_op10.xml` or `*_<order>_completion.xml` instead. ([#23](https://github.com/factorysemantics/factorysemantics-mes/pull/23))

- **A counter over MQTT must be a running total, never an increment.** MQTT
  at QoS 1 is at-least-once, and nothing in a redelivered message tells it
  from the first: a repeated total is not a rise and books nothing, while a
  repeated increment would book units the plant never made. A mapping that
  declares an increment is refused at start-up and told the two ways out. ([#28](https://github.com/factorysemantics/factorysemantics-mes/pull/28))

- **A retained MQTT message sets a counter baseline and nothing else.** The
  broker replays it to every new subscriber as though it had just happened
  and nothing in it says how old it is, so it never becomes a state change
  or a tag-history row. Counted in the run's report, not dropped in silence. ([#28](https://github.com/factorysemantics/factorysemantics-mes/pull/28))

- **A published event has one `published_at`, not two.** The envelope
  stamped the clock when the batch was built and the publication row stamped
  it again when the result was written, so the MES's own record disagreed
  with what it had already told the plant about the same event. One clock
  read per cycle now, and a test compares the two. ([#30](https://github.com/factorysemantics/factorysemantics-mes/pull/30))

- **The module registry ships with everything on.** `MES_MODULES` defaults
  to `all`, so an install that upgrades to 0.2.0 and sets nothing serves
  exactly the routes, screens and agent tools it served before — the wall is
  new, the default is not a change. Switching a module off is opt-in, and
  off means *not served*, never *not stored*: the schema is one chain for
  every plant, so a disabled module's tables and rows are untouched and come
  back when it is switched on. Nine of the twenty-three modules are the
  kernel and cannot be switched off; fourteen can.
  **Migration:** none. A plant that wants a smaller surface sets
  `MES_MODULES` (or a pack's `[modules]` table) and restarts. ([#33](https://github.com/factorysemantics/factorysemantics-mes/pull/33))

- **`/metrics` series now carry a `plant` label.** A Prometheus scraping a
  fleet had two plants' `mes_work_orders{status="running"}` under one name,
  and read their sum as one plant's number. A dashboard or alert written
  against the old series needs the label adding. New `mes_plant_info` gives
  the plant's name as a series of its own.
  **Migration:** add the `plant` label to any dashboard, alert or recording
  rule written against a series this MES exports. `mes_plant_info` gives the
  plant's name as a series of its own. ([#34](https://github.com/factorysemantics/factorysemantics-mes/pull/34))

- **Shift patterns are read on the plant's clock, not the process's.** A
  plant that set no zone sees no change; a plant whose server runs in
  another zone will find its shifts move to where the plant floor always
  said they were. `calendar.describe` now states the zone and whether it was
  defaulted.
  **Migration:** a plant whose server does not run in the plant's own zone
  should set `MES_PLANT_TIMEZONE`, then check the shift boundaries on the
  dashboard: they move to where the plant floor always said they were.
  `calendar.describe` states the zone and whether it was defaulted. ([#34](https://github.com/factorysemantics/factorysemantics-mes/pull/34))

- **The plant registry is a list of packs.** `labs/multiplant/plants.toml`
  becomes `labs/multiplant/fleet.toml`, holding `packs = [...]` and
  `[environment] data_dir` and nothing else; every one of the seventeen keys
  it used to carry per plant moved into that plant's `plant.toml` or went,
  with the reason recorded in `fsmes.pack.migrate` and in
  [the fleet how-to](docs/operate/registry.md). `secret_key` went because a
  pack holds no secret; `init` and `post_boot` went because a pack carries no
  code; `opc_port` became a whole `opc_endpoint`; `agent` went because
  nothing in the product ever read it. `FSMES_PLANT_REGISTRY` keeps its name.
  **If you run plants from a registry**, `fsmes pack migrate <registry>
  --plant <name> --out <dir>` writes the pack, and the fleet loader names the
  command when it meets a file that still describes plants directly. A plant
  whose master data was an `init` script now either carries it as data under
  `[files] masterdata` or keeps its generator as a tool a person runs — the
  two scale labs do the second, and `fsmes pack apply` says it seeded nothing
  rather than implying it seeded something.
  **Migration:** `fsmes pack migrate <registry> --plant <name> --out <dir>`
  writes the pack for a plant that still lives in a registry; the fleet
  loader names the command when it meets a file that still describes plants
  directly. ([#35](https://github.com/factorysemantics/factorysemantics-mes/pull/35))

- **The test suite runs on PostgreSQL in CI.** Every test ran on in-memory
  SQLite; PostgreSQL was documented, configured and shipped in the Compose
  file, and nothing exercised it. A `postgres` cell now runs
  `alembic upgrade head` against an empty PostgreSQL 16.15 — pinned by
  digest — and then the whole suite against the same server, on every pull
  request. `MES_TEST_DATABASE_URL` points the suite at any database;
  unset, the default is still in-memory SQLite and nothing about running
  `python -m pytest` changes. A test in the suite fails if it is not on the
  database that variable names, so a typo cannot leave the cell green.
  See [compatibility](docs/operate/compatibility.md). ([#19](https://github.com/factorysemantics/factorysemantics-mes/pull/19))

- **A database made before the migrations shipped is recognised, not
  guessed at.** A database created by a 0.1.x wheel has tables and no
  Alembic stamp, so nothing in it says which revision its tables correspond
  to. `fsmes init-db` works it out rather than assuming: it rebuilds the
  schema each revision in the chain produces, in a throwaway SQLite
  database, compares table names and column names, and stamps only on an
  exact match — then runs the migrations since. A database made by the 0.1.2
  wheel on PyPI is recognised as `153379d6cf19`, stamped there, and moved
  forward by the migrations that have landed since. If nothing matches — a
  table added by hand, a file from something other than a release — it names
  the nearest revision, lists every difference,
  and **changes nothing**, because a stamp that is not true of a database is
  worse than no stamp: every later migration is then skipped or applied
  twice on the strength of it. `fsmes plant <name> migrate` prints what
  `init-db` recognised instead of discarding it, and reports an unstamped
  database as unstamped rather than as `None`. ([#25](https://github.com/factorysemantics/factorysemantics-mes/pull/25))

## [0.1.2] — 2026-09-08

### Fixed
- `pipx install factorysemantics-mes && fsmes demo` did not run: the wheel
  carried no `config/tag_map.json` or `config/line_layout.json`, so the
  simulator had no line to run and nothing was ever booked. The four
  default config files now ship as package data, and the settings fall
  back to them when the relative path is absent (a path you set yourself is
  never replaced). The release workflow refuses a wheel without them.
- The release workflow's SBOM step asked for an image tag that does not
  exist (#5); the docs workflow serialises pushes to `gh-pages` (#5).

## [0.1.1] — 2026-09-08

### Fixed
- The container image did not build: the Dockerfile copied `pyproject.toml`
  without `LICENSE` and `NOTICE`, and the build backend refuses metadata
  without the licence file. The 0.1.0 release reached PyPI but no image and
  no GitHub Release; this version carries the same code with the image built.
- The Scorecard workflow pinned an action version whose image had moved
  registries (#3).
- The cutlery walkthrough installer failed lint (#3).

## [0.1.0] — first public release

The first public version. The code was developed privately from 2026-08-28
to 2026-09-07 in 34 pull requests; [docs/history/private-era.md](docs/history/private-era.md)
lists them. Everything below is what a stranger gets on day one, and every
number in it comes from a simulated plant.

### Added
- Kernel: master data, routings, work orders, dispatch, execution with lot
  genealogy, an append-only audit trail, users and roles with capabilities.
- OPC UA connectivity: tag maps from an engineering worksheet, `opc-browse`
  and `opc-verify`, a replay server for simulated lines, triggers as data, a
  write-back *recommendation* queue with three guards, and certificates of
  analysis at the end of the line.
- Downtime and OEE with an explicit *unknown* segment; shift calendars.
- Quality: inspections, holds, non-conformances, SPC with capability withheld
  when the data cannot support it, a gauge register with calibration.
- Maintenance-lite, finite-capacity scheduling with promised dates,
  serialisation and genealogy at ten million pieces a day (the cutlery lab).
- ERP: a typed per-operation contract with an outbox, a REST adapter, a
  B2MML-flavoured file adapter, and an ERPNext connector registered as a
  module (`fsmes.modules` entry point `erpnext`).
- The agent surface: an MCP server with 85 tools that go through the HTTP API
  as the `AGENT` role, dry-run on every write, `on_behalf_of` and idempotency
  keys; the floor assistant in the operator UI; recorded walkthroughs; agent
  evals that ask whether an agent given only the tools can answer what the
  plant knows.
- Operations: plants from a registry (`fsmes plant`), scored runs
  (`fsmes score`) against the simulator's scripted truth, sweeps, a Docker
  image and Compose file, systemd units for test and promoted environments.
- Documentation site (MkDocs), decision records, security policy, code of
  conduct, governance.

### Honesty
- Counters book production by delta only; a counter falling toward zero is a
  reset and books nothing. OEE reports *unknown* rather than zero when the
  MES was not watching. Unlabelled downtime is reported as unlabelled. These
  are the house rules and they are tested.

[Unreleased]: https://github.com/factorysemantics/factorysemantics-mes/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/factorysemantics/factorysemantics-mes/releases/tag/v0.1.0
