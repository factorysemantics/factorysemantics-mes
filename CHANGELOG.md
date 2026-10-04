# Changelog

All notable changes to FactorySemantics MES. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
SemVer, where `0.x` means the API may change with a note here.

Sections: **Added**, **Changed**, **Fixed**, and **Honesty** — anything that
changed what a number *means* (a KPI formula, a state mapping, a counter rule)
goes under Honesty with a migration line, so plant people can find it.

## [Unreleased]

### Added

- **`plant-from-your-mes`: a skills folder a customer's own assistant takes in,
  that turns their SQL MES into one safe, readable plant shape.** It ships as
  `plant-from-your-mes.zip` on the release page (`fsmes skills-zip`). A plant
  unpacks it next to their own AI assistant, asks *"how do I put my plant into
  FactorySemantics without giving away anything proprietary?"*, and the
  assistant runs read-only queries against their own database in widening
  steps and writes **one file, `plant-shape.toml`**, which they read before it
  leaves the building.

  Sixteen numbered queries in each of two dialects — SQL Server first, SQLite
  for our own test — every one of them a single bounded `SELECT`. Three scripts,
  standard library only, no driver and nothing to install: `profile.py` writes
  the file (and proposes a draft mapping of their schema from the catalogue
  alone), `leakcheck.py` **refuses to write it** if any identifying string from
  the source appears in it, and `explain.py` reads it back in plain words.

  What the file carries: stations as `ST-01`… with their rates and scrap
  shares, every tag as `AN-`/`CT-`/`DS-` with its base, spread, sampling
  interval and whether it only moves while the machine runs, the share of time
  in each state with stop duration distributions, stop reasons as categories
  with generic wording written to the measured length, the quality loop,
  order sizes, the shift pattern, their numbering grammar as patterns
  (`AAA-AA-###`), ERP traffic as path shapes — and `[[also_tracked]]`: the
  tables their MES holds that FactorySemantics does not model, described by
  shape, as a feature list.

  What it never carries: an order, work order, lot, pallet, serial, person,
  customer, item, price, address, free-text note, or one of their table or
  column names.

  **Proven blind.** `tests/unknown_mes.py` builds a deliberately foreign SQL
  MES — its own table names, a state column of three-letter words, tag history
  in one tall table — out of the bottling line's own generated hour, and the
  skill is run against it with no hint about that schema. It proposes the
  mapping from the catalogue, and the plant it describes is bottling: six
  stations in line order, every rate within a tenth of nameplate, every scrap
  share within a third of a percentage point, all twenty analogs recovered with
  the right base and spread, counters and ready bits told apart from
  measurements by shape alone — and not one order code, lot, pallet, operator
  code, non-conformance number or technician's note in the output.
  `docs/operate/plant-from-your-mes.md` says what that proves and, just as
  plainly, what it does not: **no SQL Server has parsed the `queries/mssql/`
  set**, because there is none here or on the runner. That check is structural.

- **Explore on the AI tab: an exploration that draws what it read.** A person
  holding `audit.read` opens **Explore** on `/dashboard/ai`, types a question,
  and the analysis agent answers beside its own charts — the trace graph of who
  is asking what, and then whatever the thread leads to on the floor. It is the
  design page's §1 worked example, made real: *"what is the biggest problem for
  our operators?"* answered with the totals, the coverage and the named
  silences.

  **The agent never computes a series.** It names a tool call it already made —
  the id of its own `tool_use` block — and the server attaches the envelope out
  of what the plant actually returned; the browser draws that with
  `FS.kit.chart`, which writes the total and the coverage before any shape draws
  anything. The `draw` tool has three string fields and nowhere to put a number,
  and a spec that finds a way, or that names no tool result, is **refused with a
  plain sentence** rather than drawn. So a chart here cannot state a figure this
  MES did not measure, and there is no code path by which it could.

  **Interactive means two things.** The person asks the next question in words
  and the agent re-draws; and clicking a node on the graph becomes a *question
  put to the agent* — never this page reaching around it into an endpoint — and
  the re-drawn picture re-states its own total. Every exploration chart carries
  the SVG and PNG export from `FS.kit.export`, footer and all.

  **Every reply says what it cost**: that answer, this exploration against what
  one exploration may spend, and the month. At the cap the agent stops and says
  what the month has left. With the analysis brain off — a plant with no key, a
  spent budget, shadow mode — Explore is still there and says which and why: a
  state with a reason is not a missing screen.

  What the trace keeps of a picture is a **summary** — shape, tool, total,
  coverage word, title — as the `draw` call's own line among the turn's tool
  calls. Never the envelope: a second copy of the plant's rows in `ai_turns`
  would be a way around the capabilities those rows are behind.

- **`My agent`: everything the analysis could say about you, shown to you.**
  A tab on `/dashboard/ai` behind `plant.read` — which every role holds — and
  scoped to the signed-in account by the route itself, with no way to ask it
  about anybody else. Your own conversations, the questions you asked grouped
  the way the analysis groups them, and **every time an analysis named you**:
  the `analysis.person_named` audit rows, with when and by whom. That is
  decision 0039 clause 4, reciprocity, and the test it sets for any analysis
  this product ever runs — *if the plant would not show it to the person it is
  about, it should not be run.* An operator who may not read the trace sees this
  tab and no other; a supervisor sees it beside the rest, and it is still theirs
  and not the plant's.

- **The audit trail says which shift a row fell in.** `shift_code` and
  `shift_day`, which D1 stamped and nothing read. It is what makes *"compare OEE
  and scrap during somebody's shift"* a question this plant can answer honestly:
  the shift that person's rows fell in, beside what that shift made. Two facts
  side by side and never a third — a booking carries no actor at all, so
  "this person's OEE" is an attribution nothing in this product records.

- **A fifth chart shape — the network graph — and charts a reader can touch.**
  `FS.kit.chart("graph", envelope)` draws the network of the deep-analysis
  design page §7: nodes by kind, edges by kind with their weight, and the
  holes as things with a number on them. The layout is ninety hand-written
  lines of force maths, deterministic from the first frame. **Nothing new is
  vendored and there is still no build step** — the reason the graph is a
  *shape* rather than a charting library is that the six rules of the chart
  contract are structural in `kit.js`: the frame writes `data-total`,
  `data-coverage`, the `<title>`, the `<desc>` and the footer for every shape
  before the shape draws anything, so a new shape inherits all of it and a new
  engine would inherit none.

  Four rules the graph keeps on top of the six. **Every edge is a recorded
  fact** — the kit draws the edges the envelope carries and never one between
  two nodes that ended up near each other. **The `unattributed` and
  `unlabelled` nodes carry their degree** rather than a weight, because what a
  hole is, is what it touches. **A node kind this plant records nothing of is
  drawn empty** with its zero and its name, never omitted — a graph that quietly
  left `screen` out would read as a complete picture of a plant where the
  questions came from nowhere. And **coverage is `absent`**, because a graph of
  records is not a rate over a watched window; there is no centrality, because
  a centrality over an edge set that is whatever happens to be recorded is the
  most convincing wrong number this product could show.

  **And what the reader narrows a chart to now re-states its total.** Hovering
  a mark says that mark's own number with the coverage sentence under it; the
  legend switches a kind out of the picture; the graph's threshold moves; the
  time axis brushes on the line, the timeline and the histogram. Every one of
  those rewrites `data-total` **and** the footer, on all five shapes, because a
  filtered chart that kept the old total is a list that reads complete. The
  shift analysis screen's downtime pareto and state timeline get this for free.

- **`FS.kit.export(chart, "svg" | "png")` takes a chart off the page with its
  footer on.** The SVG is the chart node's own markup with the theme's resolved
  colours inlined and the panel colour behind it; the PNG is that SVG through a
  canvas in the browser. No server round trip and no second renderer — a second
  renderer is the one that drifts, because nobody checks an export in four
  themes. The rule it is built to keep: **a chart is presentation-ready when its
  footer survives being pasted into a slide**, so the total and the coverage
  sentence go into the file and a test says they are still there.
- **Five nullable columns, and the plant stops dropping facts it already had.**
  One Alembic revision, additive and nullable, nothing else:
  `ai_turns.screen` (the browser has posted it since the assistant panel
  existed and the API declared it; nothing stored it), `shift_code`/`shift_day`
  on `ai_turns` and `audit_log` (the same pair every floor table carries,
  resolved at write time from the plant calendar as it stood — decision 0028),
  `production_logs.booked_by` (the account the booking's own audit row already
  named), and `personnel.home_equipment_id` (a nullable work centre or
  station, edited on the personnel screen, by `add_person` and by a new
  `set_home_equipment`).

  **What it makes answerable.** *Which work centre asks which questions* and
  *OEE and scrap in a given person's shift* were unanswerable in principle
  rather than for want of a better model. `/analysis/trace/rollup` now filters
  on a real `screen=` instead of matching nothing, groups by `workcenter` off
  the line above a person's home equipment, and reads the shift a turn was
  stamped with rather than re-resolving it against today's roster. The graph
  draws `asked_from` — question group to screen — and the `screen`,
  `workcenter` and `shift` node kinds fill. `/dashboard/ai`'s conversation list
  shows the screen, and says how many other screens a conversation moved
  through rather than presenting the first one as the whole of it.

  **Where a person works grants nothing.** `home_equipment_id` is not a role
  and no capability reads it; it says where to *group* somebody. Setting or
  clearing it is `users.manage` and writes `person.home_equipment_set` to the
  audit trail, so the person can see that somebody said it.

- **Three reads that answer a management question from the records: `trace_rollup`,
  `trace_graph` and `maintenance_mttr`.** *"What is the biggest problem for our
  operators?"* is now answerable out of the plant's own AI trace, its stops and
  its repairs, with no new engine and no new dependency. The arithmetic is the
  plant's, in `services/trace_analysis.py`; the routes are `/analysis/trace/rollup`,
  `/analysis/trace/graph` and `/analysis/maintenance/mttr`, so a screen and an
  agent read the same envelope; the tools hand over what came back, unchanged.

  **Counts before names (decision 0039).** A rollup over people comes back grouped
  by role, by workcenter or by shift and **never by account** — the default is in
  the plant's code, not in a prompt. Naming a person needs a new capability,
  `people.analyse`, which **no shipped role holds**: not the administrator, and
  not the analyst whose job this analysis is. A plant grants it deliberately, and
  every answer that names somebody writes `analysis.person_named` to the audit
  trail against `personnel`/their code, so the person can find out. What the
  grouping could not attribute is counted out loud rather than dropped.

  **The graph draws only what a record stands behind, and says what it cannot
  draw.** `screen` and `workcenter` are node kinds that are empty on every plant
  today and are declared empty with the reason; `asked_from` and `visited` are
  edge kinds with no source at all and say so. **Nothing draws an edge from a
  question to a stop** — nothing in this product links the two, and putting them
  side by side because the times are close would be inventing the link. The holes
  are nodes with a degree on them: turns with no person, stops nobody named.

  **Betweenness, PageRank and eigenvector centrality are refused**, by name, with
  the reason in the payload. On a graph whose edge set is whatever a plant happens
  to have recorded, a centrality score is the most convincing wrong number this
  product could offer — the graph's version of the recomputed rate decisions 0031
  and 0033 exist to prevent. Degree and weight are what it reports.

  **Every MTTR prints how many repairs it could not time.** Each timed order says
  which record timed it — `started_at`→`completed_at`, or `downtime_minutes` — and
  a preventive plan's `expected_minutes` is reported beside the actual, never
  inside it. A window that reached past `[admin] ai_trace_days` says so instead of
  quietly shortening.

- **A second agent: the analysis agent, which holds every read tool and no
  write tool.** An agent kind is now an account, a role, a tool set, a prompt
  and a budget (decision 0038), and there are two of them: the `floor`
  assistant that proposes changes for the person signed in, and `analysis`,
  which explores and explains and can change nothing at all. Every place that
  used to mean "the agent" now says which — the catalogue, the prompt, the
  availability sentence, the per-conversation budget, the account its tool
  calls sign in as, and the `brain` on its row in the AI trace.

  **"No write tool" is checked, not asserted.** The analysis catalogue is built
  by exclusion: every tool carrying `dry_run` — which is what makes a tool a
  write in this product — and every tool named in the list of what each write
  is gated on is dropped, so a write tool written tomorrow is outside it on the
  day it is written rather than when somebody remembers. A test asserts it
  against the tool registry rather than against the catalogue: 53 of the 101
  tools, every one a read.

  **Two gates, not one.** The second is the account. The tools reach a plant
  over its own HTTP API, and an analysis conversation signs in as `ANALYST`,
  whose built-in `analyst` role holds `plant.read` and `audit.read` and nothing
  that writes — so the plant refuses a write to it even if a catalogue ever
  offered one. Each account keeps its own HTTP client, because a shared one
  carries whichever of them signed in last.

  It is not offered the two walkthrough tools either: a walk ends on a form
  with a button somebody presses, and an agent that may not propose a change
  has no business leading anybody there. Asked for a change, it says that it
  only reads and that the assistant in the panel can propose it to whoever
  signs it — never that the person's own role is the obstacle, because it is
  not.

  **What one conversation may spend** is `MES_ANALYSIS_CONVERSATION_USD`,
  $0.25 by default against the month's shared $10; the floor assistant stays
  uncapped per conversation, as it has always been. A conversation that reaches
  its cap says so and says what the month has left. **In shadow mode it is off
  and says which agent and why** — there is no local analysis agent, and
  answering worse was the option that was turned down.

  `POST /assist/agent` takes a `kind`; `GET /assist/agent/status` answers for
  one kind and carries every kind beside it. The Assistant panel has one
  button, *Ask the analyst*, which starts a fresh conversation with the other
  agent — a conversation belongs to one agent for its whole life. Where an
  exploration is drawn is the next handoff's; until then it answers in words
  and numbers and promises no picture. `tests/assist_suite/analyst.toml` asks
  it eleven things, three of them changes it must refuse: `fsmes assist eval
  --scripted` is 122 of 122, every role at 100%.
- **A design page for analysis that reasons as deeply as the question, and
  decision 0039 *accepted* 2026-09-29.** `docs/design/deep-analysis.md` works one
  management question — *"what is the biggest problem for our operators?"* —
  end to end over the records this plant actually keeps, and says at every step
  what it can and cannot support. Its findings: the question is answerable to
  about half its depth today, and what stops the rest is the records rather
  than the engine — the screen a question was asked from reaches the API and is
  dropped, a person has no workcenter and no shift, a booking has no actor,
  nothing times a request, and nothing records a page visit at all. It argues
  the engine fork (a catalogue the agent composes, against agent-written code in
  a sandbox), the renderer fork with measured bytes (`kit.js` at 52,536 against
  plotly 3.1.1 at 4,830,889, which has no network layout in it either), the
  network model over the trace and the plant, the standard-chart gallery and how
  a saved view is stored, what one answer costs, and eleven milestones replacing
  `analysis-in-the-ai-tab`.

  [0039](docs/decisions/0039-an-analysis-is-recorded-code-that-can-only-read.md)
  carries the two things that need a record: a deep analysis is a computation the
  plant records and which can only read — never a rate the plant computes — and
  it counts people by role, workcenter or shift before it names one, with naming
  behind a capability no shipped role holds and every named-person answer writing
  an audit row the person can see. Nothing is built; the page and the record are
  the maintainer's to accept or send back.

- **Charts that say what they do not know.** `kit.js` — the one set of chart
  shapes every screen draws through — grows `FS.kit.chart(kind, envelope,
  options)` and four shapes an analysis needs: a line series, bars (a pareto
  when the cumulative line is on), the state timeline, and a histogram. They
  take the API's own payload, with its coverage, its ledger and its totals
  still on it, and draw it under a **chart contract** now written into
  `docs/design/STYLE.md`: a chart computes nothing and every plotted number
  appears verbatim in the markup; every figure carries its coverage, and a
  station below this plant's coverage floor is drawn withheld at full width
  with its ledger rather than as a shorter bar; unknown is hatched and breaks
  the line rather than being drawn through; a y-axis that does not start at
  zero says so, a truncated window says what it truncated, a rate carries its
  denominator and a bin width is stated; every chart states its total,
  including the rows nobody drew; and every mark takes its colour from the
  palette, in all four themes. Each chart carries a text description built
  from the same sentences as its visible footer, so a screen reader and the
  screen cannot disagree.

  No new dependency, nothing vendored, no build step: hand-drawn SVG, extended
  in `kit.js` rather than duplicated, because `kit.js` exists precisely so that
  two screens can never state coverage differently. Pinned by 52 browser tests
  across the four shapes and the four themes.

- **The four shift analyses are agent tools, and answer with the payload the
  screen gets.** `oee_breakdown`, `state_timeline`, `downtime_pareto` and
  `tag_trend` are read tools on the `analysis` module. Each sends one `GET` to
  `/analysis/...` and hands back what came back, whole — so an agent reading
  them inherits the honesty of decisions 0030, 0031 and 0033 instead of being
  told about it in a prompt. Until now OEE and the Gantt had no tool at all, and
  an agent asked about them would have had to rebuild them out of the state
  history; a second arithmetic reachable only through a model is what those
  decisions exist to prevent.

  **What "the payload the screen gets" amounts to differs by analysis, and the
  tools say so per tool rather than implying all four carry the same:**
  `oee_breakdown` carries `coverage`, `coverage_floor`, the `coverage_note` that
  withholds a figure and the whole `ledger`, per station and for the line;
  `downtime_pareto` carries `unknown_seconds` and `unknown_share` — how blind
  the window was — and no ratio and no ledger; `state_timeline` and `tag_trend`
  carry no coverage figure at all, because a Gantt and a trend are what the MES
  recorded and neither says what share of the window it was watching. Nothing
  supplies a figure its route does not serve, and a test pins which payload
  carries what — the same fact the chart kit draws as `data-coverage=absent`.
  `labelled_by`, `requested_hours` and every total come through untouched.

  No write tool, no new capability: `plant.read` is the whole gate, and a test
  asserts no tool on this module takes `dry_run`. An answer too wide for one
  tool result bounds itself and says so — a list holds its tail back with the
  call that reaches the rest, a Gantt asks the plant for the machines that fit,
  and a trend asks for fewer, wider buckets rather than dropping every nth point
  and losing the excursions `min` and `max` exist to keep. Four cases in the
  request suite, one per analysis.

- **A live faithfulness run asks each role as an account that holds it.**
  `fsmes assist eval --live` signed in once and asked every role's questions as
  that one person, which made three of the four per-role numbers meaningless: an
  operator's refusal cases cannot refuse while somebody holding every capability
  is typing, so they passed for the wrong reason. `--account operator=SCOTT`
  (repeatable) says who asks a role's cases, with each account's password in
  `MES_ASSIST_EVAL_PASSWORD_<ROLE>` — or in `--password` when the code is the
  same as `--user`, so no password goes in two variables. A role with no account
  is reported **no account**: not asked, not scored, not paid for, and named in
  the result file, which also says which account answered for each role. `--user`
  stays the account the arrangement is written for and is never a stand-in for a
  role nobody was named for. Nothing creates an account; a plant to be scored as
  a supervisor needs one in its pack. `docs/ai/ASSIST-EVAL.md` has the command.

- **A live faithfulness run can put the demo plant's master data on the plant it
  scores — as the person, never as the agent.** `fsmes assist eval --live
  --seed-masterdata` creates `LINE1`, `MIX01`, `PACK01`, `RAW-SUGAR`,
  `RAW-FLAVOR`, `FG-COLA`, the routing `RT-COLA` (Mix 10 on `MIX01`, Pack 20 on
  `PACK01`), the brix specification on `FG-COLA` (9.5–11.5 °Bx) and the lots
  `LOT-SUGAR-001` (500 kg) and `LOT-FLAVOR-001` (100 l) — over the plant's own
  API, on the session the run signed in with.

  The arrangement added above stops at master data on purpose: an agent
  deployment does not define materials, equipment, routings, lots or
  specifications (decision 0035) and the AGENT account holds no
  `masterdata.write`. A **person** may, and pointed at a plant that was not built
  from the demo pack the suite had nothing to ask about. This is the ten `POST`s
  somebody would otherwise type by hand, which a plant rebuilt on a fresh build
  wipes every time.

  Off unless asked for. It **creates and never updates**: a code that is already
  there is reported *already there* and left exactly as the plant has it, so a
  plant whose `FG-COLA` is a different product keeps its own, and a second run
  writes nothing. It is refused in one sentence, before anything is written, when
  the account signed in may not define master data; the two lots are guarded by
  `production.consume` rather than `masterdata.write`, and an account holding one
  and not the other is told which capability they wanted. The codes and the
  numbers are read back out of `seed_demo_plant`, not copied into the seeding, so
  the plant that lands is the demo pack's by construction. `LINE1` arrives
  parentless — a run has no business inventing four levels of somebody else's
  hierarchy. The result file lists every code that arrived, beside what the agent
  arranged and apart from it, so a scored plant's operator can see which account
  wrote what.

  Honestly stated in [ASSIST-EVAL.md](docs/ai/ASSIST-EVAL.md): **this API cannot
  remove any of it.** There is no `DELETE` or `PATCH` for equipment, materials,
  routings, specifications or lots, and nothing in the product sets a lot to
  `blocked`. A seeded plant keeps it until somebody reaches the database, or until
  the plant is rebuilt or restored from a backup taken first. Seed a plant you are
  willing to rebuild.

- **A design page for the MES as an agentic harness, and an accepted decision
  behind it.** Not built: `docs/design/agentic-harness.md` states what exists in
  the AI layer today, maps the shape of the maintainer's own development crew
  onto plant-side concepts (and says which parts have no plant analogue and why),
  and says what an agent kind is — an account with a role, a tool set, a cadence,
  a monthly budget, a declared data class, and one place its work lands. Decision
  [0038](docs/decisions/0038-an-agent-is-an-account-with-a-role-a-budget-and-a-cadence.md)
  is **accepted (2026-09-28) with two amendments**, both of them the maintainer's
  own answers, quoted in full in the page's *Decided* section.

  **Amendment A — the harness changes the product's code.** *"I was thinking a
  person should literally be able to change the code to improve the UI. Move
  cards, change graphs, etc."* So the automated tier this page first drew —
  fifteen `[screens]` numbers a plant's screens run at — is a floor, and the
  signature boundary is now **three tiers, each with its test and its
  signature**. Tier (a) is those numbers, behind a five-clause test, warn-only
  for a release before anything applies itself. **Tier (b) is the product's own
  UI code**: the harness proposes a diff to `web/` as a branch and a pull
  request, proves it with the repository's existing gates (`test`, `postgres`,
  `browser`, `lab`, `lockfile`, `wheel-demo` and the DCO sign-off) and a test
  that fails before the change, and **a person's merge is the signature** —
  which is how decision 0035's rule that an agent never approves is kept. If
  that tier ever automates it is only inside a declared file set, and the page
  names the exclusions rather than implying them: `common.js`, `assist.js`,
  `assist-record.js`, `kit.js`, `styles.css`, `themes.css`, `themes.js`,
  `web/vendor/` and `web/line/`, plus any diff that removes a `data-assist`
  anchor, moves a computation into the browser, or fetches anything from outside
  the box. One question is named and not answered: `fsmes ui-check --accept`
  makes the current look the accepted look, so an agent that may run it can erase
  the check that would have caught its own change. **Tier (c) is everything else
  and never automates at all.**

  **Amendment B — the harness is the engine of all product improvement.** *"This
  should become the bottle neck for driving all other improvements on that list.
  For example, if I wanted to integrate with SAP or fix any ERPNext connection,
  then it should be this harness that allows be to fix or build it."* So the
  crew → plant map stops being an analogy, and the page says what runs where,
  honestly: **the plant box has the wheel, not the repository.** There is no
  checkout, no compiler and no test runner on a plant, deliberately (decision
  0005 is "no build step"), so a code-editing harness needs a checkout somewhere
  else, the CI the project already has, and **a release as the only path back to
  a running plant**. No code is pushed into a running plant. A plant with no
  internet can still produce the finding and receive the fix as a release, and
  the maintainer's own crew keeps the public repository's governance.

  **A four-milestone build plan**, in the order the answers imply, each milestone
  sized as handoffs: **M1** the analysis agent with graphing (the four analyses
  as read tools returning the screens' own envelope; a chart contract of six
  rules — a chart draws what the API measured, every figure carries its coverage,
  unknown is drawn as unknown, honest axes, every chart states its total, the
  palette and all four themes; hand-drawn SVG through `kit.js`, no build step);
  **M2** the `[ai]` Configuration domain, the `my agent` view, a durable
  transcript and a short remembered-facts list the person can read and delete,
  per-agent budgets; **M3** the improvement crew, warn-only, on a no-model delta;
  **M4** the harness as the engine — the checkout and CI story, UI code
  proposals, and the crew's roles inside the product.

  The page keeps its own case against being built at all — the project's strategy
  pages rank this fifth of five and nobody outside this machine has asked for it
  — and answers it in the maintainer's words rather than withdrawing it. **Two of
  the three objections survive intact**, and the answer is written so it can be
  shown wrong: after the warn-only release, three numbers say whether it was —
  how many proposals a person signed, what a merged proposal cost against doing
  it by hand, and whether anybody but the maintainer ever merged one.

  It also records what it found while reading the code. **An unattended agent
  signing in as `AGENT` could put twenty-four settings in force with nobody in
  the loop** — twenty-two Configuration sections take effect when saved and are
  gated on `process.define` or `quality.define`, both of which the `agent` role
  holds, and they include `[quality] hold_rules`, the two Cpk bars and
  `[oee] min_observed_seconds`. Nothing exploits it today because the floor
  assistant never writes unattended; a scheduled agent would, which is why the
  first field of an agent kind is its own account. **`ai_turns` is a record and
  not a replayable history**, so "each person's agent remembers" needs a store
  that does not exist. Two further findings have been fixed since the page was
  written and the page says so with the numbers moved: the nine ungated write
  tools (#119, so `NEEDS ∪ PER_CALL_NEEDS` is now 47 of the 47 writes and the
  catalogue on `plant.read` alone is 51 read tools) and the AI tab's Status
  blanking without a local model (#122).

- **A live faithfulness run arranges the plant it scores, and says what it
  could not.** `fsmes assist eval --live` used to ask a plant questions about
  things that were not on it. Nine of the nineteen failures in the first live
  run, on 2026-09-26, were the assistant *correctly* refusing to invent — *"there
  is no material coded FG-COLA in this plant"*, *"there is no draft on
  'changeover' — it is already at revision 1, approved and in force"* — scored as
  though the model had got them wrong.

  Each case in `tests/assist_suite/` now declares what the plant has to have for
  its sentence to mean anything (`requires = ["material:FG-COLA",
  "order:WO-EVAL-1", "no reason:changeover"]`), and a live run puts there what it
  may: a released work order, the non-conformance a failed brix check opens, a
  corrective maintenance order, one setting written so the audit trail has a row
  in it, and four **drafts** — an instruction, a trigger, a downtime reason and a
  severity, none of which changes anybody's screen until somebody signs. Written
  by the AGENT account, naming the person the run signed in as, through the
  product's own API, so the fixture is built by the write path the suite is
  about. Idempotent: a second run on an arranged plant creates nothing twice.

  **Master data is never arranged.** Materials, equipment, routings, lots and
  specifications are the plant's own and an agent deployment does not define them
  (decision 0035). A case the plant has not got what for is reported **not
  arranged** — not asked, not paid for, not scored, counted in its own column and
  listed in the result file with the reason. `--no-arrange` leaves the plant
  alone and reports the same way. Nothing is removed afterwards and there is no
  `--clean`: an MES does not delete an audited record.
  `docs/ai/ASSIST-EVAL.md` lists every row a run leaves and what a person does
  about each.

- **AI is a workspace in the navigation bar, beside Setup, and it shows every
  conversation this plant's AI has had.** Three tabs behind `audit.read`:
  **Conversations** — one row per conversation with who, when, how many turns,
  what was proposed and what became of each, how many turns failed and what it
  cost, and under it the turns themselves in order (the person's words, the
  assistant's, every tool call with the one sentence the panel showed, every
  proposal with its outcome, the walkthrough that went on their screen, the
  class of any error); **Status** — which brains are on, which are off and
  why, the GPU, and the cloud brain's spend against its cap; **Settings** —
  how long the trace is kept, and links to the Setup rows that gate the
  assistant. A confirmed proposal names the audit row it wrote, so the trace
  and the audit trail can be read side by side.

  The record is the plant's own: an `ai_turns` table in the plant's database,
  written by the assistant as it answers and by the design chat, pruned to
  `[admin] ai_trace_days` (90 by default; 0 keeps everything) as new rows are
  written. It holds what the person was shown and one sentence per tool call —
  never the system prompt, never the key, never a tool's raw payload. Without
  a browser: `fsmes ai conversations`, `fsmes ai show <session>`,
  `fsmes ai status`. `docs/operate/ai.md` says what is recorded, who may read
  it, how long it is kept and how to read a failure out of it.

  It exists because twice in one week the only record of an assistant failure
  was a screenshot somebody pasted into a chat window, and the error that
  broke a session for good was written down nowhere at all. Scott, 2026-09-26:
  *"really show that this is putting AI and Agents into this system as first
  class citizens."*

- **Every change the assistant can make for you, it can show you how to make
  yourself.** "Show me" sat on ten of the thirty-eight write tools the
  assistant may propose; it is now on all thirty-eight. Press it on any
  proposal card and the walk opens the real screen, opens the tab the control
  is behind, types the proposed values into the real boxes and stops with your
  finger over the real button — for a material, a machine, a specification, a
  bill of materials, a routing, a sign-in, a role, a maintenance plan, a
  trigger, a setpoint proposal, a shift, a shutdown day, a serialised unit, a
  non-conformance decision, and the rest. A test keyed on the tool catalogue
  fails if a write tool is ever added without one, so it stays true.

  Where a tool takes something the screen has no box for — a serial the plant
  mints, a shift that applies to one machine, a start time for a plan — the
  step says so, rather than filling in three fields and quietly dropping the
  fourth. One walk deliberately fills nothing: putting somebody in a role is a
  dropdown that saves the instant it changes, so setting it for you would be
  making the change rather than showing you where it is made.

- **Asked to approve something, the assistant walks you to the signature.**
  It approves nothing itself and never will (decision 0035) — but *"approve
  the draft severity"* used to come back as "no tool named approve is
  available to this person", which is true and useless. There are now five
  walks, one per approvable kind: a downtime reason, a non-conformance
  severity, a work instruction, a trigger, a setpoint adjustment. Each ends on
  the control that actually signs it — the floor screen's review panel for the
  two vocabularies, their own screens for the other three. Ask for one you are
  not allowed to sign and it names the capability and what that capability is
  for, so you know who to ask instead of being told no.

- **The assistant finds a walkthrough from the words people actually type.**
  Without a local model the fallback matched whole words, so *"how do I book
  production?"* found nothing — the guide's own line says *booking*. Words now
  agree on their first four characters, which is the difference between an
  assistant and a shrug on a plant with no model running.

- **"Could you show me where?" over an open proposal now shows you where.**
  The walk behind a card's "Show me" is a walkthrough the model can hand over
  by name, so a question typed under a proposal is answered with that
  proposal's own walk instead of "there isn't a walkthrough for that". The
  decline a typed message produces also stopped saying "the person moved on
  without confirming", which was not what happened.

- **The four newest tools are four of those thirty-eight, and one of them
  needed a screen before it could be shown at all.** Starting and finishing a
  step of an order, booking a lot in, revising a work instruction: starting
  and finishing are on the station's own queue, one row per step, which is
  where the person who did the work is standing.

- **The floor screen can book a lot in.** A delivery on the dock, or a
  quantity somebody counted, had no form anywhere: lots only appeared by
  production booking one or the ERP link sending one, so the assistant could
  record stock arriving and a person could not. There is now a fourth form
  beside Book output, Issue material and Quality check — lot code, material,
  quantity — gated on `production.consume`, the same capability as issuing,
  because both are the same person's job at the same bench. The lot code is
  the label on the pallet and is never generated, and an unknown material is
  refused by name.

- **The assistant can put a walk on your screen because it decided to, not
  because a regex did.** Two new tools when the cloud brain is on: `guides()`
  lists the walkthroughs this person is allowed to follow, and
  `show_guide(id)` starts one — the same walk, on the same real controls, that
  "Show me" on a proposal card starts. The model chooses it with the whole
  conversation in view, so *"I want you to show me how to do it"* typed over
  an open proposal reaches the model that holds that proposal. One sentence in
  the system prompt says that being asked to be shown is answered with a walk
  and never with a description of the screen.

- **Every turn of every conversation is written down.**
  `~/.local/share/fsmes/agent-turns.jsonl`, one JSON line per turn beside the
  bill in `agent-usage.jsonl`: plant, session, person, what kind of answer they
  got (reply, proposals, guide, unavailable, error), which tools ran, which
  proposals were opened and how each ended, the tokens, the dollars, and the
  exception class when a turn failed. Nothing in it that is not already in the
  transcript the panel shows the person. It exists because the only question
  worth asking about the conversation of 2026-09-26 — *how many of those
  thirty turns reached the model?* — could not be answered from anything on
  the machine. (It was five.) `docs/ai/OBSERVABILITY.md` carries it.

- **A written suite of requests that says whether the floor assistant does what
  people ask — 93 cases, scored two ways.** `tests/assist_suite/` holds one TOML
  file per role in the words people actually type, each with one expectation:
  propose *this* tool with *these* arguments, walk them to *that* control, answer
  from the plant, look before answering, or refuse and say who can. Scoring is
  strict on identity — the tool name, the argument keys, and the values the
  sentence named, so `1.33` is `"1.33"` and `55` is not `56` — and loose on
  prose. `fsmes assist eval --scripted` runs the whole suite against the real
  guide router, the real per-role tool catalogue, the real tools on a real
  seeded plant and the real surfaces, with the model replaced by a stand-in; it
  is deterministic, needs no key, takes about two seconds, and
  `tests/test_assist_suite_scripted.py` puts it on every pull request.
  `fsmes assist eval --live --plant <url> --user <code>` asks the other half —
  whether the real model chooses right — through the same `POST /assist/agent`
  the assistant panel uses, declining every proposal so a scored plant is an
  unchanged plant, stopping at `--max-usd` (default $1.00) and writing
  `docs/ai/assist-eval/<date>.md` the way a calibration run is written. Every
  one of the 33 write tools the agent may propose has a case; so does every
  approval a person signs, which an agent must never perform. Scott's own two
  conversations are in the suite word for word, typing included. Nothing in the
  repository would have caught either of the two failures he found this week;
  `docs/ai/ASSIST-EVAL.md` says how to read a result and why a bug becomes a
  case before it becomes a fix.

- **`GET /assist/agent/status` reports tokens as well as dollars.**
  `tokens_this_month` carries the month's input, output, cache-read and
  cache-write counts. The dollars are an estimate against a price list kept in
  this repository; the tokens are the bill's own unit, and a faithfulness run
  that reported only dollars would show a price change as a change in how much
  the assistant does.

- **Every action a person can take on a screen, the assistant can take for
  them — or the code says why not.** A ratchet, a report and four missing
  tools. `tests/test_every_write_route_has_a_tool_or_a_reason.py` walks every
  route that changes the plant (POST, PUT, PATCH, DELETE — sixty-eight of them)
  and fails unless an MCP write tool sends it or a line says why none does; the
  list of reasons may only shrink. It is the sibling of `test_route_coverage`,
  which has ratcheted screens against routes since 2026-09-02, and it
  **replaces `tests/test_mcp_parity.py`**, which made the same claim from
  2026-09-08 without being able to keep it: that one matched the tool source
  with the method thrown away, so the read tool `lots()` counted as covering
  `POST /execution/lots`, and three of its reasons were plan phases rather than
  rules ("no operation start/complete tools yet"). A reason containing *not
  yet*, *later* or *phase* now fails a test of its own — that is how a list
  which may only shrink starts growing, and it is why the person who found the
  assistant's two gaps this week was the maintainer, twice.
  `fsmes assist coverage [--role operator|supervisor|admin|agent]` prints the
  table — route, the screen that calls it, the tool or the reason, the
  capability the route gates on, whether the tool has a "Show me" walk, and
  whether each role holds it — with a summary line per role, and
  `docs/operate/assistant-coverage.md` is that command's output, checked for
  staleness by a test.

  Four write routes had neither a tool nor a reason, three of them the most
  basic thing an operator does. New tools, each reading the plant first so its
  preview sentence carries the from and the to: **`start_operation`** and
  **`complete_operation`** (`order_action` released, held, resumed, closed and
  cancelled a whole order and stopped there — the step in front of somebody had
  no tool at all), **`create_lot`** (booking a delivery in, in the material's
  own unit), and **`revise_document`** (the next revision of an instruction, as
  a draft, saying whether it is editing an open draft or copying the revision
  in force). An admin can now propose 51 of the 66 screen actions rather than
  47; an operator 16 rather than 13; the `agent` role 23 rather than 19.

  Nothing was given an approval tool, and a test now asserts that nothing
  reaches a route gated on a `*.approve` capability: decision 0035 read from
  the other end — an approval is the person's signature, and "Do it" runs as
  the AGENT account on their behalf, so a tool that approved would sign their
  name. The same for passwords and for signing somebody out. `DELETE
  /admin/roles/{code}` is a judgement call and the table says so: defining what
  a role grants is reversible from the screen that did it, and deleting a role
  is not.

- **The assistant can find a setting by what a person calls it, and read what
  has been written to one.** `plant_settings(plant, find="reporting window")`
  searches every Configuration workspace's names, labels and descriptions in
  one call and says which workspace each answer is in; `key=` returns one
  setting in full with the paragraph that describes it; calling it with
  nothing lists the workspaces. `setting_changes(plant, key=, domain=)` reads
  the audit trail for settings — who set what, from what, to what, when — so
  *"no change is recorded"* is a sentence that can only be said after looking.
  `GET /dashboard/config` indexes the workspaces, which until now could only
  be learned by naming one that does not exist and reading the 404.

- **CI runs the browser tests.** Every Chromium-driven test in the suite is
  marked `browser` as well as `slow`, and a new `browser` job runs
  `pytest -m browser` on Ubuntu and one Python on every pull request — twenty-six
  tests, about twenty seconds of testing after Chromium is installed. They
  existed before and nothing ran them: they are `slow`, the default selection
  is `-m 'not slow'`, and every CI job used the default, so the whole tier was
  opt-in — and opt-in tests rot. The proof is in *Fixed* below. The rest of the
  slow tier runs in the same job (`slow and not browser and not erpnext_live`),
  which is four tests and four seconds; the ERPNext round trip keeps its own
  workflow, because it needs a real ERPNext. `tests/test_every_browser_test_is_in_the_browser_tier.py`
  fails if a test file that reaches Playwright is missing the marker, so the
  next browser test cannot be added outside the tier by accident. **House rule
  6 in CONTRIBUTING** now says the other half of it: a look on `127.0.0.1`
  proves the logic and not the experience, and anything timing-shaped is
  verified against an artificial delay or a real remote client, or is described
  as "looked at on loopback only".

- **One tool for every domain's live settings, not one per domain.** Two agent
  tools: `plant_settings(plant, domain)` reads a whole Configuration workspace
  — every setting a plant owns, what it is set to, whether that is the
  product's default or this plant's own choice, what kind of value it is, and
  the capability a write is gated on, with both totals (thirteen keys across
  eleven sections in Quality, because two of those sections are one judgment
  written as two numbers). `write_plant_setting(plant, domain, key, value)`
  puts one value in force through the same
  `PATCH /dashboard/config/{domain}/settings/{key}` a person's Save uses.
  Neither knows a domain's name: both read the `ConfigSection` registry the
  Configuration page reads, so **a section that becomes live is reachable
  through the assistant with no tool written for it** — Quality's thirteen keys
  today, the next domain's the moment its handoff lands. `dry_run` previews the
  exact `PATCH` and sends nothing; `on_behalf_of` puts the person's name beside
  the agent's in the audit trail; `client_ref` makes a double click one write.
  Nothing is validated twice — a value the pack checker refuses (a marginal Cpk
  bar at or above the capable one, judged against the bar this plant is
  actually running on) and a caller whose role does not grant the owning
  section's `define` both come back as the sentence the API answered with.
  In the floor assistant it is `propose_adjustment`'s card, not
  `draft_nc_severity`'s: there is no draft and nobody signs it, so it waits for
  a click. **Show me** opens the owning workspace's Configuration page with the
  box filled in and stops in front of Save; **Do it** writes it and walks back
  to the same box to show the value in force. There is no approve capability
  anywhere near either tool, and nothing for one to do — a number that takes
  effect when it is saved has no pending state (decision 0035, rule three).

- **Supply chain gets its Configuration page: what this plant asks of its ERP
  link.** Ten `[erp]` keys, six sections, and a capability that had been named
  since 2026-09-21 and used by nothing — `erp.define`. How many times a
  confirmation is offered before a person has to look at it (8) and how long
  this plant waits between attempts (5 s, doubling, capped at an hour); which
  statuses its ERP puts an order in while it waits to be made (`Not Started`,
  `In Process`); when a number the ERP hands back is the number that was sent
  (a thousandth, or a hundredth of a unit); how long to wait on one request
  (30 s for ERPNext, 10 s for plain REST); the priority an order arriving with
  none inherits (50); and the slack `fsmes erp validate` allows between
  `machine_seconds` and the time a step was open (1 s). **Every default is the
  literal that was in the source, so a plant that writes none of these keys
  behaves exactly as it did.** Nine of the ten are boxes you type in on
  **Orders › Configuration**, gated on `erp.define`, audited, in force with no
  restart. `fsmes pack check` reads them offline and refuses the same values
  in the same sentences — a confirmation offered no times at all, an empty
  list of open statuses, a first wait past the ceiling on a wait.

  The nav entry sits in **Orders** rather than in a Supply chain group of its
  own, because this product has no ERP screen: an order from the ERP lands in
  the order book and the outbox is a panel on Ops, so a group holding one
  Configuration chip and nothing else would have been a group invented for a
  settings page.

  Four of these are applied by the *connector* rather than by a service, and a
  connector is handed no database session on purpose — the sync worker never
  lets a transaction span an HTTP call. So the worker reads this plant's
  policy once per cycle, in a transaction closed before anything is sent, and
  hands it over. A change is in force at the start of the next cycle: five
  seconds by default, with nothing restarted. A connector published on its own
  and written against the older port takes no policy and behaves exactly as it
  did.

  One of the ten has no box, deliberately: `confirmation_seconds_tolerance` is
  read by `fsmes erp validate`, which reads files and no database by design —
  a plant's ERP team runs it on a shadow-mode outbox, often on a laptop that
  has never had an MES database on it. Its row says what is true instead:
  *nobody — it changes when the pack is applied and the plant restarts.* It is
  the first section in this product to answer that way, and the page has had
  the words for it since the day the live ones arrived.

  The pack format grew a `strs` kind beside `ints` — a TOML array of words,
  compiled to a comma-separated setting — because a list of the ERP's own
  status names is a list in the same sense a list of rule numbers is. A status
  name with a comma inside it is the one thing it cannot carry, and
  `fsmes pack check` says so rather than splitting it into two.

  No migration: the table these rows live in has been there since
  2026-09-24.

- **This plant's own engineering numbers — seventeen settings, live on the same
  seam.** Every plant-scope item the configuration audit found in process and
  controls engineering becomes a box you type in on **Engineering ›
  Configuration**: how far through a maintenance plan counts as coming due
  (0.8), what one unit costs at a station with no rating (3 s), how long a job
  with no plan takes (60 min), a new plan's duration (30 min), how long a window
  is when nobody says (8 h — the source called it *a shift*), the five windows
  every time picker offers, how many machines one Gantt draws (12), how far back
  *the previous shift* reaches (14 days), the default working week (`1111100`),
  the floor below which no rate is reported (10 s), the schedule board's horizon
  (24 h) — and for controls, the OPC booking retry (4 × 0.5 s), the namespace
  retry policy (8 attempts, 5 s to an hour), how fast an approved trigger
  reaches the agent (30 s), a new trigger's cooldown (300 s), how densely tag
  history is sampled (10× publish, floor 1000 ms), and the agent's two cadences
  (2 s and 5 s). **Every default is the literal that was there, so a plant that
  writes none of these keys behaves exactly as it did.** Seeded from `[process]`,
  `[controls]` and `[oee]` in the pack, owned by the database after that, in
  force when saved with no restart, audited as `plant_setting.set`. Two new pack
  tables, a new `floats` key kind for the window list, and `fsmes pack check`
  refuses from the page exactly what it refuses in a file, in the same
  sentences. Reference: [this plant's own engineering numbers](docs/operate/engineering-numbers.md).

- **`signals.define`, the capability controls engineering did not have.** Named
  in decision 0035 §2 in September and added now that there is something to gate
  on it: the six controls rows above. On the **admin** role; deliberately not on
  the **agent** role, because an agent may draft a vocabulary for a person to
  approve and retuning how hard the OPC agent retries a booking is not drafting.
  Its pair `signals.approve` is still absent on purpose — nothing here has an
  approval step, and a capability that gates nothing is a role saying something
  untrue about itself.

### Changed

- **`[admin] agent_result_limit` is 12,000 characters, up from 6,000.** Raised
  on two measurements rather than a feeling. The trace graph's *frame* alone —
  the window, the declared-and-empty node kinds with their sentences, the
  refused measures — took most of a 6,000-character answer: a two-machine test
  plant came back **3 nodes of 15 and 1 edge of 14**, and at 12,000 it is 15 of
  15 and 14 of 14. Administration's settings list went from **12 rows of 40** to
  **26 of 40**. An exploration that reads a graph and then follows the thread
  cannot do either on three nodes. **The honest paging is unchanged**: every list
  still states its total and names the call that reaches the rest, and this
  raises the budget rather than removing the honesty. A plant that wants the old
  number sets the key.

- **The shift analysis screen's downtime pareto and state timeline are drawn by
  the chart kit.** The pareto now prints what its payload always knew and the
  picture never said: the total downtime, how much of it was unlabelled, the
  window it had to truncate, and the minutes in it that nobody was watching.
  Unlabelled stops are hatched rather than shaded, because they are not a
  reason. On every timeline — analysis, the machine page, the line view and the
  schedule board — a stretch where the MES had lost sight of a machine is now
  hatched instead of being a solid grey block that looks like a measurement,
  and the chart says how many machines it drew out of how many the line has.

- **`downtime` and `tag_trend` no longer carry a default window of their own.**
  Both were declared in `mcp_server.py` with `hours=8.0` and `hours=1.0`. The
  routes have no default on purpose: `hours` left out is this plant's
  `[process] default_report_hours`, read at the moment of the request, which is
  what makes it editable on Engineering's Configuration page with no restart —
  so a tool defaulting to eight told a twelve-hour plant its own default was
  somebody else's, in the one place nobody would look. They are now
  `downtime_pareto` and `tag_trend` on the `analysis` module, where the registry
  always said they belonged, and both take `line` as well. An integration that
  called `downtime` by name calls `downtime_pareto`.

- **The request suite scores what a reply means, not the words the database
  stores it under.** The dated live run of 2026-09-27 found four places where the
  suite, not the model, was the thing being measured, and all four are in the
  suite rather than in the product:

  - A `read` case no longer makes a setting's storage key the only wording that
    passes. The one admin miss in forty was this: asked *"I just changed it to
    10.0 hrs. Could you change it back to 8 hrs?"*, the assistant read both
    records and answered *"the default reporting window is already back at 8.0
    hours — the audit trail shows it was changed from 10.0 to 8.0 at 16:14
    today"* — right, read from the trail, in the words off the screen — and the
    case wanted the literal `default_report_hours`. The key is now one rendering
    in a `contains_any` group beside the label a person reads, in all six cases
    that demanded one, and a test keyed on the product's own settings registry
    fails on the next one anybody writes.
  - The one request only `produce_batch` can answer names a pallet. It named two
    loose serials, and the model answered with two `produce_units` calls that
    book exactly the same two units — a judgement, not a wrong act. What a
    palletizer actually sends is the stack with the stack's own serial, and
    `produce_units` has no `container` argument, so there is one right answer
    again; a test goes red if another tool grows one.
  - Asked to requeue a dead ERP message on a plant whose outbox holds nothing
    dead, *"there is nothing to retry"* is a pass — and only when the reply also
    names who could if there were. The refusal case asks for `orders.close` and a
    role that holds it, read off `capabilities.py`; the literal `can` it replaced
    was never that test, being inside both "cancel" (this capability's own
    description) and "cannot".
  - The same live reply put a tool's name in front of an operator — *"requeuing a
    dead ERP message is done via `erp_retry`"* — which the scripted stand-in
    cannot do and so never scored. `tests/live_replies.py` keeps a live reply
    verbatim, and the rule from #116 is scored against that reply from here on.

- **A case a plant could not arrange says what would make it arrangeable, not
  only why not.** `admin-approves-an-adjustment` came back *not arranged* on a
  plant whose tag manifest declares no writable setpoint, and the result file said
  only why. From the reason alone a reader cannot tell a plant that is short of
  one line in a file from a suite asking for something no plant could give it, and
  those are different problems with different owners. Every *Not arranged* row now
  carries both halves — for this one, the tag to put under
  `tables.<the machine's object>.tags` in the plant's `tags.json`.

- **The assistant reads before it says a thing does not exist or cannot be
  changed.** Asked on 2026-09-27 to give a non-conformance the prefix `CR`, it
  made no tool call and answered *"That's not something I can do here — NC codes
  aren't configurable in this system; they're assigned automatically with a fixed
  'NC' prefix."* `nc_code_prefix` is a Quality setting this product ships, and the
  same request four hours earlier had found it in one `plant_settings(find=…)`.
  Asked then to be shown where, it said *"there's no walkthrough for this because
  it isn't a real control"* — one unread assertion costing two answers, because
  having decided the control was not real there was nothing left to walk anybody
  to. The prompt now says it: *"it isn't configurable"*, *"it doesn't exist"*,
  *"the code generates it"* and *"it isn't a real control"* are denials, and a
  denial is said only after looking — `plant_settings(find=…)` for anything that
  sounds like a setting, the domain's own read otherwise. It is the sibling of the
  *read before saying what is recorded* rule: one is about what changed, this one
  is about what exists.

  Held by a test over **every reply the request suite produces**, all four roles:
  a denial never appears in a turn with no read of the plant behind it. Listing
  the walkthroughs does not count as a read of the plant — a list of walks cannot
  tell you whether a setting exists, which is exactly what went wrong the second
  time.

- **An optional argument is not a question.** *"MIX01 is down, mark it down"* was
  answered *"I can set MIX01 to down, but I need a reason so it's tracked
  properly… What's causing it?"* with nothing on the screen to press — a machine
  still running in the plant's own record while somebody types. The machine and
  the state are all `set_machine_state` requires; a reason is optional, the
  station screen puts its picker beside the state control, and the card's own
  "Show me" lands there. So when the request names everything the tool requires,
  the assistant proposes, and anything optional the person did not give is left
  for the card and the walk rather than asked for first. A test holds every
  `propose` case in the suite to ending in a card and to never asking for an
  argument the tool would have run without — and holds every one of those cases to
  naming what its tool requires, which is what makes the rule fair.

- **`add_calendar_exception` says which kinds it takes.** Its docstring
  described "a shutdown day or an overtime day" and named neither of the two
  words the API actually accepts, so on 2026-09-26 the live model sent
  `kind="shutdown"` for Christmas Day while the suite expected `kind="holiday"` —
  both of which the plant would have refused. There are two kinds:
  `non_working` (a holiday or a shutdown; the plant is dark) and `working` (an
  overtime day that is normally dark). A tool description is the whole of what a
  model knows about an argument, so both are named, with what each is for, and a
  test keyed on the enum fails if a third is ever added to one and not the other.

- **Three expectations in the assistant's request suite were stricter than the
  truth, and are not any more.** "Which spc rules are on hold" accepts the answer
  read out of `spc_chart` as well as out of `plant_settings`, and accepts "all
  four" and "1, 2, 3 and 4" as the same four rules the settings page writes
  `1,2,3,4` — the live model answered it correctly and was marked wrong on both
  counts. "Take me to scrap" accepts the `book-production` walk: there is no
  scrap screen, and the form where scrap is booked is where scrap is. What is
  still scored is unchanged — an answer that never looked, or that sends somebody
  to a procedure document, still fails.

- **"Which spc rules are on hold" scores the fact rather than the label, and a
  request to produce has one right answer.** Two more expectations the third live
  run found stricter than the truth. The rules case asked for the words
  `hold_rules` or "hold rules" and was answered *"rules 1, 2, 3 and 4 are all set
  to hold (open an NC) when they fire"* — the right four rules and the right thing
  happening to them; what it asks for now is the four numbers and the word *hold*,
  which is the fact. And *"produce 2 units of FG-COLA on MIX01 for WO-EVAL-1"* was
  answered by starting step 10, which on a released order whose first step had not
  begun is a defensible first move — so a run now starts step 10 as part of
  arranging the plant, which leaves booking production the direct act. Three cases
  moved with it and say so in their own notes: starting a step is asked of step 20,
  the one still waiting; completing a step and asking how far through the order is
  both need the started one; and "is it on the floor?" accepts *running* as well as
  *released*, because both mean it is.

- **When your role does not let you do something, the assistant says which
  capability it needs and who holds it.** Asked to close a non-conformance, an
  operator used to be told *"no tool named 'close_nonconformance' is available
  to this person"* — a true fact about the tool catalogue, and no use to
  somebody standing in front of the non-conformance. The answer now names the
  capability, the product's own plain words for it, and the roles that hold it:
  *"quality.close_nc is what this needs, and you do not hold it. Supervisor and
  Administrator can. That capability is: Close non-conformances."* The roles are
  **your plant's own**, by the names an admin gave them, so a plant that put
  `quality.close_nc` on a role called Shift Lead is told Shift Lead; where no
  role at the plant holds it at all, it says so and says that an administrator
  has to grant it, rather than sending you to somebody who cannot help either.

  It says the same thing in both places it can: at the moment the tool is
  refused, and up front in the assistant's own briefing, where every action
  this person cannot take is listed with the capability and who holds it —
  because the tool catalogue is filtered per role, so without that list there
  is nothing in view to name. A plant setting is answered with the capability
  of the section its key is listed under, read from the same registry the API
  reads it from. The five signing walks already said half of this and now share
  the sentence, so they name the signer's role too. **The whole faithfulness
  suite is required for the first time: 97 of 97 required cases pass, and the
  last 6 `not_yet` marks are off.**

- **The faithfulness suite scores ten more requests, and stopped measuring an
  order of operations the product no longer has.** Scripted mode ran the guide
  router before the agent — the very pre-emption #109 removed — so the suite
  was baking the 2026-09-26 failure into its own measurement. It now opens the
  conversation the way the endpoint does. Ten cases came off `not_yet`: the
  five approvals, the two requests to be shown a task, the two refusals to
  sign somebody else's draft, and Scott's own *"could you show me where?"*.
  **77 of 77 required cases before, 87 of 87 after; `not_yet` 16 → 6**, and
  all six that remain belong to another handoff.

### Fixed

- **A trace graph said each machine had been watched for the whole window, on a
  plant the coverage ledger had barely seen.** The first picture this product
  ever drew for a person carried the number decision 0033 exists to prevent. A
  168-hour `trace_graph` came back with `watched_seconds: 604800` on both
  machines' `stopped_with` edges and `unknown_seconds: 0`, while
  `oee_breakdown` over the same request clamped its window to 10.36 h and
  reported **coverage 0.0617 — 37,303 observed seconds of 604,800**. The agent
  read the graph and told the reader the plant was *"fully watched … no blind
  time."*

  The cause: the graph took the window's own length less the recorded
  disconnections. A plant with no disconnection row therefore looked completely
  watched — but a hole in the state history that nothing recorded a
  disconnection for is time nobody watched either, which is the whole reason the
  coverage ledger exists and is the fix `oee_breakdown` already had. Every
  seconds-weighted edge now carries that machine's **`observed_seconds` out of
  the ledger**, and the graph's `unknown_seconds` is the ledger's unobserved
  time rather than zero by default. A test pins the edge against
  `oee_breakdown`'s own figure for the same window, so the two cannot drift
  apart again.

  The graph also says which window those seconds came from. A new `watched`
  block carries `hours`, `requested_hours` and `clamped` the way the OEE
  envelope does, with the machine-seconds watched, the machine-seconds nobody
  watched, and the coverage between them. The graph's own window is **not**
  clamped to the ledger — a question is recorded whether or not a machine was
  being watched, and shortening the picture would silently drop turns — so the
  two halves cover different lengths of time and the block says so.

- **An exploration described the shape it had read instead of drawing it.**
  Asked *"what is the biggest problem for our operators?"* on a real plant, the
  analysis agent read the rollup, the graph and the pareto, answered honestly —
  and drew nothing. Asked the same question with the word *"draw"* in it, it
  drew both. A reader handed the sentence and not the picture has to take the
  shape on trust, so the prompt now says in one line that a graph or a pareto it
  has read is **drawn, not described**, in the same turn it read one and without
  being asked. The request suite scores it: a case may name the reads whose
  answers have to be drawn, and the §1 case does, with no *"draw"* anywhere in
  the sentence a person types.

- **`draw` refused a guessed id where a tool name would do.** The same live run
  spent two rounds on ids it had invented (`downtime_pareto_1`, `trace_graph_1`)
  before it used the real `toolu_…` ones — the ids are in the conversation, but a
  model that has not looked at them has no way to be sure, and the person waits
  through every round trip. `draw(from=…)` now takes **a tool's name** as well as
  a `tool_use` id, meaning that tool's most recent answer in this conversation.
  Nothing is guessed: `downtime_pareto_1` is neither, and is still refused — with
  a sentence that now names both ways of saying it, so the next call is right.
  A spec carrying numbers of its own is refused exactly as before.
- **The downtime pareto said "0 % of this window nobody was watching" over a
  window the plant had seen 6 % of.** Its `unknown_seconds` was the sum of the
  **recorded disconnections**, which is nought on any plant that has never
  recorded one — so on a plant whose coverage ledger started ten hours into a
  168-hour window, a pareto reported that nothing went unwatched, and the chart
  kit printed that under the bars. Measured here on a seeded plant on
  2026-09-29: `downtime_pareto(hours=168)` answered `unknown_seconds: 0`,
  `unknown_share: 0.0`, while `oee_breakdown` over the same window answered
  `not_observed_seconds: 1,204,200` and `coverage: 0.0045`. The second chart in
  the first picture this product drew for anybody carried the wrong sentence.

  The pareto's unwatched seconds now come from the **coverage ledger**, over the
  window that was asked for — the same `coverage.totals_many` arithmetic
  `oee_breakdown` reports and PR #136 gave the trace graph. The envelope keeps
  every key it had and gains `watched_seconds` and `coverage`, so the chart kit
  prints *"Watched 0.4 % of the window"* the way it does for every other figure
  instead of falling through to a share of the disconnections. A test pins the
  pareto's three figures against `oee_breakdown`'s own answer for the same
  window rather than against a number typed into a test, so the two arithmetics
  a screen shows side by side cannot drift apart again. Decision 0033, and the
  chart contract's rule 2, applied to the pareto.

  A disconnection is still never a bucket: it is not downtime and nobody named
  it. The other three analyses were read for the same fault and do not carry it
  — `state_timeline` and `tag_trend` and `production_trend` state no unwatched
  figure at all, which is what their docstrings and the MCP tools say. What
  `state_timeline` *does* still do is draw only the holes a disconnection was
  recorded for; a hole nothing recorded is white space on the Gantt. That is
  the same understatement in a different shape and it needs the ledger's
  intervals rather than a one-line change, so it is named here and not fixed.


- **The AI tab's Status showed nothing at all on a plant with no local model —
  including what its assistant costs.** The tab was built from one payload,
  `GET /ai`, which is the *local* AI layer on the box: Ollama, the GPU, and the
  jobs the local model has. The cloud brain the assistant actually runs on was
  a **note inside one of those local rows**, so `MES_LOCAL_AI=0` returned
  `{"enabled": false}` and took the spend-against-cap with it — the one number
  a plant administrator most needs from that tab, gone with a setting about a
  different model. `GET /assist/agent/status` had been serving it properly all
  along and the page never called it.

  Status now reads both, side by side. The cloud brain's model, whether it is
  on, its spend this month against this plant's cap and when it was last used
  are shown whatever `MES_LOCAL_AI` says; the local layer's rows appear when it
  is on, and when it is off it is **one row saying `off` and why** rather than
  an empty tab. Neither call failing takes the other with it, and one that does
  not answer reports *unknown* — not zero, and not a blank that reads as
  nothing happening. Off says why in `available()`'s own words (*"no
  ANTHROPIC_API_KEY in this plant's environment"* is something somebody can act
  on).

  `fsmes ai-status` had the same hole and prints the cloud brain first now,
  before the local layer's early return. The Ops screen's panel is titled
  *Local AI* and stays exactly that — the cloud brain is read on the AI tab.

  One rendering bug came out with it: `agent.status()`'s `last_used` carries a
  `+00:00` offset, and the dashboard's date helper appended a `Z` to every
  stamp it was given, making `…+00:00Z` — so the cell read **"Invalid Date"**
  where the date of the assistant's last turn belonged. The helper now
  recognises a stamp that already says its own offset.

- **The assistant walks an administrator to the signature instead of refusing
  it.** Asked to approve a draft downtime reason by somebody holding
  `process.approve`, it answered *"a signature I can't put my own name to — that
  needs process.approve from a person on the Engineering screen"*. The first half
  is true and the second is not: the five walks to the five signing controls exist
  precisely so that request has an answer, and an administrator is shown all five.
  One sentence in the prompt now says that never approving is not refusing — when
  a draft's approve walk is theirs to follow, that walk goes on their screen — and
  the wording that names the capability is for the person who may *not* sign.

- **The faithfulness suite no longer asks for what its own arrangement already
  did.** Five of the six failures in the 2026-09-27 live run were the suite
  tripping over its fixtures, each one the model answering correctly: the
  reporting window was set to 10.0 and then asked to be changed to 10, a
  `cosmetic` severity was asked for on a plant carrying the run's own
  `eval_cosmetic`, the Cpk bar was asked for at its own default, nothing arranged
  the setpoint change an approval case names, and one answer was rejected for
  writing "the Cpk capable bar" rather than `cpk_capable`. The requests stay word
  for word; the arrangement moved. A run takes the reporting window to 10.0 and
  puts it back to the product's 8.0, the drafts it puts up have their own words
  (`eval_awaiting_parts`, `eval_scuff`) rather than a case's word with a prefix,
  it recommends a setpoint change where the plant declares a writable one, and
  two tests fail if any fixture ever reads like the draft a case asks for.
- **Nine write tools were offered to anyone who could sign in; each now names
  the capability its route demands.** `add_person`, `register_gauge`,
  `calibrate_gauge`, `issue_certificate`, `issue_pallet_certificate`,
  `produce_batch`, `pack_unit`, `set_unit_status` and `erp_retry` were in
  neither `agent.NEEDS` nor `agent.PER_CALL_NEEDS`, and the tool catalogue
  filters on those two and nothing else — so the assistant offered all nine to
  anybody holding `plant.read`: **seven of them to an operator**, two to a
  supervisor, seven to the AGENT account itself.

  Nothing was written. Every one of those routes refused the call with its own
  `require(...)`, which is the second of the two gates decision 0035 asks for.
  What a person got was worse than a refusal: their own assistant offered to
  issue a certificate of analysis and then the plant said no. And
  `docs/operate/assistant-coverage.md` — the page somebody reads instead of
  trying it — said an operator may not issue one, because the page reads the
  route. The page was right about the API and wrong about the assistant.

  Each tool now names the capability **its own route demands**, read from the
  route rather than chosen: `users.manage`, `masterdata.write`,
  `quality.close_nc` (four of them), `production.book` (two) and `orders.close`.
  An operator asking for any of the seven now gets the refusal that names the
  capability and who holds it, rather than a card with "Do it" on it.

  `tests/test_every_write_tool_names_the_capability_its_route_demands.py` is the
  ratchet, keyed on the registry: a write tool in neither dictionary fails, a
  named capability its route does not demand fails, and the catalogue and the
  coverage page are made to agree about every role — the two readers that
  disagreed. Nine cases went into the assistant's request suite (**107
  scripted, up from 98**), and the "Show me" ratchet can see the nine for the
  first time: what it sees is nine cards with a "Do it" and no walk, which is
  what they have always been, recorded in `assistant.WITHOUT_A_WALK` with where
  each control is. That list can only shrink.

- **Asked for a change, the assistant proposes it — the card is the question.**
  *"I want a non-conformance to have a prefix CR instead of NC. Could you make
  that change?"* came back as *"I can change it to 'CR' — want me to go
  ahead?"*, with nothing on the screen to press; *"…which spc rules raise a
  hold to 1,2 only"* came back as a paragraph about what the change would mean.
  The same two requests had produced cards four hours earlier — the model
  varied, and the prompt let it, because *a proposal is not a change* did not
  also say *a request for a change is a proposal, not a question*. It says so
  now. Asked to be shown a change nobody has proposed yet, it proposes first:
  the card's own "Show me" is the walk being asked for, standing on the very
  field, and a walkthrough cannot go on the screen in the same turn as a card
  because the card is what the person is then looking at. And no tool's name
  reaches the person any more — *"show_guide("proposal") will put the real form
  on your screen"* is a sentence for a developer, not for somebody at a
  machine. Three cases of the nineteen the assistant's request suite found on
  its first live run; the suite now pins all three, over every reply it
  produces and for every role.

- **A walk's card could be caught showing one step's number over another
  step's words.** It said `STEP 5 OF 6` while the body was still step 4's —
  on a slow machine, never on loopback. The card is painted a quarter of a
  second after the control is scrolled into view, so the ring lands where the
  control ends up, and that late paint took its words from the step that asked
  for it and its *number* from wherever the walk had got to by then. Press
  Next inside that quarter second and the two disagree. A step's number now
  travels with the step, and a paint a later step has overtaken says nothing.

- **A walkthrough could never have pointed at the trigger form's Save
  button.** `triggers.html` carried `data-assist="trigger-submit"` on the
  *closing* `</button>` tag, where the HTML parser throws it away — so the
  anchor greps as present and has never existed in a rendered page.

- **A regex answered the person three times while the model was never asked.**
  `POST /assist/agent` ran the guide router — `wants_showing()`, a fixed
  `show me|how do i|…` pattern — in front of the agent for everybody. On
  2026-09-26, mid-conversation about `nc_code_prefix`, *"could you show me
  where?"* returned a walkthrough for finding a work instruction, and *"no
  there should be a tool for you to show me how to do it on the quality
  configuration tool"* returned one for recording a quality inspection,
  twice. The agent — which held the proposal, the surface and a two-step walk
  onto that very field — saw none of them. The router now runs **only when
  the agent is off**, which is the case it was written for: a plant with no
  key, a local model with no tools, and one message to judge on. When the
  agent is on it gets every message and answers a request to be shown with
  `show_guide`. `wants_showing`'s docstring says which brain it is the gate
  for.

- **A message typed over an open proposal broke the conversation for good.**
  The decline was recorded but never committed to the history, which left an
  assistant turn holding a `tool_use` beside the person's next sentence — a
  shape the Messages API refuses. Every later message in that session came
  back *"The cloud brain did not answer (BadRequestError)"*, in a third of a
  second, with no model call and nothing in any log. The decline now reaches
  the history before the person's words do, and **a session already in that
  state repairs itself**: before every call, a `tool_use` nothing answered
  gets its `tool_result` ("the person moved on"), so the next thing they type
  works instead of failing until the conversation times out.

- **A failed turn was a shrug, and it stuck.** `except Exception` returned
  the class name to the person and dropped everything else — no log line, no
  API message, nothing to find afterwards. A failure is now logged at WARNING
  with the exception class, the API's own message, the session, and the shape
  of the history (roles and block types — never the key, the prompt, or
  anything about the plant); the person is told *"The assistant hit an error
  on that one. Say it again and I will try afresh."*; the session is left
  callable; and a transient failure (a dropped connection, a rate limit, a
  5xx) is tried once more before any of that. A 400 is not retried: the same
  wrong question costs the same money twice.

- **One failed turn handed the rest of the afternoon to a brain that cannot
  act.** The panel read any `unavailable` as "the agent is off", set
  `agent.available = false` and sent every later message to `/assist/ask` —
  the local facts brain, with no tools — without telling anybody. It then
  answered fifteen requests to change things by improvising: *"check the
  quality procedure document"*, *"consult the quality manager"*. Now a model
  error is its own reply kind and leaves the agent on; only the reasons
  `available()` names — no key, budget spent, shadow mode — hand the panel
  over, and when the local brain is the one answering it says so once per
  page (*"The plant's agent is off; I can answer from what is on screen but
  cannot change anything"*) and is told to say it cannot make changes rather
  than point at a document.
  `tests/test_the_panel_keeps_one_brain_through_a_failed_turn.py` drives a
  real Chromium through proposal → message over it → failed turn → next
  message against a scripted model, in the `browser` tier.

- **The assistant could not see a setting that exists, because the list was
  cut in half.** Asked to change "the default reporting window", it called
  `plant_settings` five times and answered that no workspace holds such a
  setting. `[process] default_report_hours` is item 17 of 22 on the
  Engineering page and its section is labelled *The default reporting
  window*. The list carried every key's full description — about 11,100
  characters for that one workspace — and the agent loop cut tool results at
  6,000 with a bare `…(truncated)` glued into the middle of the JSON, so the
  model saw eleven items, could not know the list went on, and reported half
  a list as the whole. Three changes, none of them a bigger limit: a row in a
  list is now ten compact fields **including the section's label** and no
  description; a list returns as many whole rows as fit, states the plant's
  `total`, and names the call that reaches the rest
  (`tests/test_every_settings_list_fits_in_one_tool_result.py` measures every
  workspace of every shipped pack); and `_tool_result` drops whole trailing
  items and says *showing 10 of 22* instead of cutting JSON mid-string.

- **The assistant said "I don't see any change recorded" without having
  looked.** Told that a setting had just been changed on the Configuration
  page, it answered from an earlier, truncated read and made no tool call —
  there was no tool through which it could have read the audit trail. There
  is now (`setting_changes`), and the prompt says to use it: *when the person
  says they changed something, read the plant again before you answer.*

- **"Updating it now" over a proposal that had changed nothing.** A write is
  a card waiting for "Do it", and the assistant described it as already under
  way. The prompt forbids the wording, and the card no longer depends on the
  model getting it right: it opens with **Nothing has changed yet**.

- **The Configuration page's section count is read from the registry.** The
  browser test asserting Engineering's Configuration page lists sections pinned
  the number one; the page has listed eighteen since the Engineering settings
  landed, and the test had been red on `main` ever since, unseen, because
  nothing in CI ran it. It now reads `modules.config_sections("engineering")`
  and asserts the page shows every section the registry holds, in the
  registry's order, with that total in the count line — identity and a live
  source, never a number that was true on one day. Nothing about the page
  changed; the test was the thing that was wrong.

- **The KEPSim replay end-to-end is declared red rather than quietly
  unrun.** Bringing the slow tier into CI found it failing three runs out of
  three on its analog tag-history assertion (`RD01.MotorTemp`): production and
  equipment states arrive from the replayed line, the analog history does not.
  It is marked `xfail` with the reason and the two candidate causes written into
  the test, so it runs on every pull request and reports rather than hides. It
  is not skipped, not strict, and not loosened — and it is not fixed here.

### Changed

- **Nine `hours` and `limit` parameters no longer declare a default of their
  own.** `GET /analysis/{oee,timeline,downtime,production,tag}`,
  `GET /scheduling/board`, `GET /dashboard/summary`, `GET /kpis/oee/{code}` and
  `GET /equipment/{code}/oee` had `8.0`, `24.0` or `12` in their signatures;
  they take no value now and the service reads this plant's own
  `[process] default_report_hours`, `schedule_default_horizon_hours` or
  `gantt_screenful` at the moment of the request. **Answers do not change on a
  plant that configures nothing** — the default is the same number — but the
  OpenAPI schema no longer tells a twelve-hour plant that its own default is
  eight hours. Every payload already stated the window it actually used.
  `expected_minutes` on `POST /maintenance/plans`, `days` on
  `POST /scheduling/calendar/shifts` and `cooldown_seconds` on
  `POST /triggers` are optional the same way and for the same reason: sending
  the product's number would overrule a plant that had chosen another.

- **`fsmes pack apply` seeds `[process]`, `[controls]` and `[oee]` once and
  never updates them**, the rule it already keeps for every masterdata kind and
  for `[quality]`. A pack is how a new plant starts, not how a running one is
  steered; `fsmes pack status` reports the difference. A pack that seeds a shift
  and omits `days` now gets **this plant's** working week rather than the
  product's five days.

- **The browser stopped keeping its own copies of four of these numbers.** The
  shared time picker held the window list and its eight hours as literals, the
  maintenance bar turned amber at eight tenths of its own, the new-plan form
  sent 30, the new-trigger form sent 300 (and `|| 0` for an empty box, which
  asked for *fire on every reading*), and the shift form sent `1111100`. Each
  now reads the plant's answer — from the payload it belongs to where one
  exists, and from `GET /dashboard/screens` for the three controls that are
  built before any payload has been asked for. A plant that warned at 70% used
  to get a bar that disagreed with the sentence beside it.

- **Engineering › Configuration is eighteen sections, so it draws the grouping
  it already claimed.** The page said it was sorted by the module each section
  belongs to and showed nothing to see it by; each row now carries its module
  and the table draws one heading per group, following the search box.

- **This plant's own administration numbers — forty-four keys across three
  tables, and where IT's settings live.** The role a new account starts with,
  how big a list answer is, how often each of the three screens re-reads the
  plant, how many rows the Floor and Admin screens page at, how far a screen
  reads a whole list, how long a recorded walkthrough may be and what it asks
  of a viewer, what one conversation with the floor agent may spend, how much
  of the plant reaches a model, six local-model timeouts named one per thing
  waited for, the shape a drafted work instruction takes, when the AI panel
  calls a rollup late, how long a confirmation stays on screen, how much of
  the assistant survives a page change — each was a literal in the source, and
  **every default is the literal that was there**, so a plant that writes none
  of them behaves exactly as it did. They arrive as `[admin]`, `[screens]` and
  `[system]` keys in `plant.toml`, and are edited on a new **Setup ›
  Configuration** page by somebody holding `users.manage`, in force the moment
  they are saved. `fsmes pack check` reads all three offline. Reference:
  [this plant's own administration numbers](https://docs.factorysemantics.com/operate/administration-numbers/).

  IT's three settings — the fleet probe, log rotation and which local model
  answers — are on that page too, gated on the same capability. IT gets no
  Configuration workspace of its own: decision 0035 §2 keeps it outside the
  role model, so a workspace would need a capability that does not exist, and
  the person who administers a plant's accounts is already the only person who
  can reach these.

  Five of the forty-four are read when a process starts rather than live, and
  the page says so instead of offering an input that would half work: the list
  default and ceiling are published in this plant's own OpenAPI document and a
  ceiling that moved under a caller holding it would make that document a lie;
  log rotation is configured before the plant's database is open; the fleet
  probe is the console's number about every plant it watches.

  **The browser keeps no copy of any of them.** Fifteen were literals in
  `app.js`, `admin.js`, `assist.js` and `common.js`; each page now reads
  `GET /dashboard/ui-settings` once in its own boot, before it draws anything
  or starts a clock, and carries no fallback — a browser default beside a
  server default is how the same number comes to exist twice and drift. That
  settles one such drift by deletion: `admin.js` had its own `toast()` at
  4000 ms against `common.js`'s 3500. The copy is gone and `[screens] toast_ms`
  ships 3500, which is what every other screen already used.

  What a plant may **not** edit: the clauses that keep a drafted work
  instruction honest. `document_house_style` carries the structure — a plant
  whose quality system mandates Scope / Hazards / Steps / Records writes its
  own — and *use only the facts given*, *never invent a tolerance, a tool or a
  machine*, and **an operator is never told to adjust a reading toward the
  middle** are added to whatever it says, from the source, reachable by no
  setting.

- **The assistant can draft the plant's own words, and never sign them.** Two
  agent tools that were missing while the API was already open to them:
  `draft_downtime_reason` and `draft_nc_severity`, with `downtime_reasons` and
  `nc_severities` to read the whole vocabulary first — statuses, revisions, and
  how many recorded intervals or non-conformances each code labels, with its
  total. They are the same shape as every other agent write: through the plant's
  HTTP API as the `AGENT` account, `dry_run` previews and sends nothing,
  `on_behalf_of` puts the person's name beside the agent's in the audit trail,
  `client_ref` makes a repeat return its first answer. In the floor assistant
  each arrives as the proposal card the other write tools already use —
  **Do it** drafts and walks you to your word sitting in the vocabulary as a
  draft, **Show me** fills the real form on Engineering › Downtime reasons or
  Quality › Non-conformance severities and stops in front of Save draft.
  Approving stays a person's: the `agent` role holds neither `process.approve`
  nor `quality.approve`, so there is no tool for it and the route refuses the
  agent by name. Retiring a word stays a screen action too, because the count
  of history a code labels belongs in front of somebody before they drop it.

- **This plant's own quality numbers — thirteen `[quality]` keys, and one
  rule.** Where a process stops being capable (1.33 and 1.0), how many
  readings a control limit needs (12), how far back a chart looks (200), the
  gauge rule of ten and its floor of four, a new gauge's calibration interval
  (365), how many serials a pallet certificate prints (200), how many digits a
  serial carries (6), what a non-conformance is called (`NC-00017`), how deep
  the packaging goes (6), and which SPC rules are a major finding (rule 1).
  Each was a literal in the source, most with a comment beside it arguing for
  the number rather than stating it — which is what a judgment call sounds
  like before anybody calls it configuration. **Every default is the literal
  that was there, so a plant that writes none of these keys behaves exactly as
  it did.** `fsmes pack check` reads them offline and refuses a number that
  would leave the thing it decides unable to decide anything — control limits
  from one reading, a Cpk bar that makes *marginal* unreachable — and refuses
  nothing else. All thirteen are listed on **Quality › Configuration** with
  the value this plant is running on and whether the plant chose it.
  [This plant's own quality numbers](docs/operate/quality-numbers.md).

- **How much warning a gauge wants is the gauge's** — `gauges.warn_days`,
  thirty by default, set when a gauge is registered and left blank for the
  default. A quarterly calibration wants a fortnight and an annual one wants
  two months, and one plant owns both. Until this column existed the gauges
  screen decided *due soon* in JavaScript at three separate places, so the
  shop floor's definition of the phrase lived in the browser and the server
  did not know it; `GET /quality/gauges` now answers `warn_days` and
  `due_soon` per gauge and counts them for the register.

- **The plant's own non-conformance severities — the second vocabulary, and
  the test of whether the first one's loop generalised.**
  `NonConformance.severity` had been twenty characters of free text
  defaulting to `minor` since the table was written: nothing validated it, no
  list stood behind it, and the only two words in the product were the two
  its own code writes (a recorded check outside its specification opens a
  minor one; SPC rule 1 opens a major one). A plant can now name its own:
  `nc_severities` holds one revision of one word per row, somebody holding
  the new **`quality.define`** drafts on **Quality › Configuration ›
  Non-conformance severities** (`/dashboard/severities`), somebody holding
  the new **`quality.approve`** puts it in force on the *Waiting for you*
  panel, and undo is approving the previous revision. `GET
  /quality/severities` answers in the same catalogue shape every server-owned
  list in this product does, and there is a `nc_severities.json` pack kind.
  **A plant that approves nothing behaves exactly as it did** — the column
  takes what it is given; the moment a plant has one word in force, a new
  record may only be raised at a word on the list, and **no existing record
  is read or rewritten.** `minor` and `major` may be renamed and described in
  the plant's own words and may not be retired: the product's own code paths
  write them, so the refusal lands where a person is standing rather than on
  a machine raising a hold at three in the morning, and `fsmes pack check`
  refuses a seeded list that omits either. The four lab packs ship exactly
  those two words — shipping `critical` to be helpful would be inventing a
  plant. [Who names the severities](docs/operate/quality-severities.md).

- **Quality has a Configuration entry**, the second workspace to get one
  (`/dashboard/config/quality`): the severity vocabulary, and which SPC rules
  raise a hold. One entry per domain with its configurable sections inside
  it, not a nav chip per setting.

- **Which SPC rules raise a quality hold is the plant's —
  `[quality] hold_rules`, and decision
  [0036](docs/decisions/0036-the-chart-draws-every-rule-the-plant-chooses-which-hold.md).**
  All four Western Electric rules have always raised a non-conformance, which
  on a characteristic that trips a warning rule several times a shift is a
  queue nobody works. Decision 0035 had placed the four rules with the
  product — *"a rule a plant can switch off is a chart that lies"* — and it
  was answering about the **chart**. It still is: **every rule is evaluated,
  drawn, recorded and counted in the verdict on every plant**, and what a
  plant chooses is which of them are worth somebody's morning. Leave the key
  out and all four hold, which is what this product has always done. An empty
  list is a real answer and is never silent — the chart payload carries
  `rules` and `hold_rules`, and the SPC screen says on every load which rules
  this plant holds on and which are *drawn and recorded and raise no hold*, so
  a firing that opened nothing is explained rather than noticed.

- **`fsmes config-audit` — find the business judgments still hard-coded in
  the source.** Decision 0035 gave one test for whether a setting belongs to
  the plant or to the product — *ask what breaks if two plants answer
  differently* — and it had only ever been applied by hand, to a dozen
  settings, on a design page. The command applies it to the whole tree: every
  number, mapping and fixed list sitting near a word that names a judgment
  (threshold, limit, window, rule, severity, warning, due, capable, and the
  rest), reported with its file, its line, the literal, and the comment beside
  it, grouped by the six configuration domains. Python is read with `ast`, so
  a named mapping is reported once instead of once per number; the browser
  files are read as text. It **decides nothing** — a person applies the test —
  and it states how many files it read and how many it skipped with a reason
  for each, so *we looked at everything* is checkable rather than asserted. A
  file the domain table does not place is reported as unassigned rather than
  folded into the biggest domain. `--strong` narrows to the candidates with a
  hedging comment or a self-describing name without changing any domain's
  stated total; `--json` is for diffing one run against the next, which is the
  point of it being a command and not a report.

- **The downtime vocabulary has a screen: Engineering › Downtime reasons**
  (`/dashboard/reasons`). The lifecycle shipped with a place to *sign* a
  reason and nowhere to *draft* one, so the only person who could use the
  drafting half was somebody holding a token and a terminal. The screen lists
  every word the plant has ever had — on the list, drafted, retired, never
  signed — with who drafted it and when, who signed it and when, and how many
  recorded intervals each code labels; it states the vocabulary's total. A
  person holding `process.define` drafts a new word, edits the open draft of
  one, drafts the next revision of one already in force, or drafts a
  retirement — and **is told how much history a code labels before deciding to
  retire it**, rather than after a refusal. Everyone else reads the list and
  sees no form. Signing is unchanged and still happens on the *Waiting for
  you* panel. `GET /equipment/downtime-reasons/vocabulary` is the list behind
  it; the catalogue an operator chooses from is untouched. A reason may no
  longer be named `drafts` or `vocabulary` — those are the vocabulary's own
  addresses, and a word spelled that way could never have its history opened.

- **A plant's downtime reasons become a list with an author.** The box that
  asked why a machine stopped was a text input and the pareto grouped on
  whatever came back, so *jam*, *Jam*, *jam at infeed* and *infed jam* were
  four bars too small to act on while the real top reason was invisible. A
  plant can now name its own reasons: `downtime_reasons` holds one revision of
  one reason per row, somebody holding the new **`process.define`** drafts,
  somebody holding the new **`process.approve`** puts it in force, and undo is
  approving the previous revision. `GET /equipment/downtime-reasons` answers in
  the shape `/triggers/catalog` already has, and **the station screen offers a
  select where a plant has a vocabulary and the text box where it has none.**
  Once a plant has one, going down takes a code from it — a list nobody has to
  use is a suggestion beside the text box that caused the problem — and the
  shipped vocabulary carries an explicit *not yet determined*, so "required"
  never means "make something up". The page is
  *Who names the reasons* (`docs/operate/who-names-the-reasons.md`); the
  design is decision 0035.

- **Retiring a code changes what may be chosen next, never what was chosen
  before.** An interval labelled `jam_infeed` keeps that label when
  `jam_infeed` leaves the list. A retirement is refused unless the draft says
  how many recorded intervals carry the code — a code leaves the list with
  somebody having looked at what it already labels, or it leaves it blind.

- **`GET /dashboard/pending-approvals`, and a *Waiting for you* panel on the
  plant dashboard.** Three approval lifecycles already ran in this product —
  work instructions, triggers, setpoint adjustments — and **not one of them
  told anybody**: no inbox, no aggregate count, no mail, webhook or push,
  while drafting sits with supervisors and agents and approving sits with
  administrators. The endpoint answers what is waiting that *this caller* may
  act on, from their live capabilities: kind, code, what it is, who drafted it
  and on whose behalf, how long it has waited, and one action that signs it —
  counted, with the whole queue's total rather than the page's. A caller who
  can approve nothing is told about nothing and the panel is absent for them
  rather than empty. A draft nobody acts on waits visibly: it never expires and
  never goes live by itself. One kind behind it today, the downtime
  vocabulary; the other three join it next.

- **`GET /dashboard/pending-approvals/{kind}/{code}/{revision}`, and a review
  the floor assistant walks you through.** The panel above shipped with a row
  and an approve button, which is a blind signature: the row says a draft
  exists and nothing about what it would do to the plant. **Review** on the
  row now opens the substance in one call — the draft against the revision it
  would supersede as a diff in plain words, how many reasons the list holds
  and would hold, how many recorded intervals already carry the code (counted
  now, beside what the drafter said when they wrote it), who drafted it and on
  whose behalf, and the one click that puts the previous revision back. It is
  gated by the same capability that lists the kind: a panel that refuses the
  button while showing the substance leaks the draft. **Walk me through it**
  hands that diff to the assistant's guide mode — one step per change, each
  painting the row it belongs to with the ring that walks an operator through
  a task, the approve control last. The walk is generated deterministically
  from the draft; **no model is called anywhere in it**, and the coach card
  says so. Kinds live in one registry (`services/review.py`) with a reader and
  a reviewer each, so a kind can no longer be listed as waiting without
  something that can show what it would change.

- **`downtime_reasons.json`, the tenth master-data kind a plant pack may
  carry.** Codes are checked offline by the same two rules the product
  enforces when a person drafts one — the shape, and the protected words. A
  packed vocabulary arrives in force, because applying a pack is a deliberate
  act by a person, and **the plant owns the list from its first edit**:
  applying again never rewrites a code that is already there. The three lab
  packs and the cutlery demo ship six words, ISO-22400-shaped.

- **The coverage ledger: every second of an OEE window, accounted for.**
  Per machine, per window, the MES now keeps a ledger of disjoint intervals
  that tile the window **exactly** — every second in one disposition
  (`observed_running`, `observed_stopped_labelled`,
  `observed_stopped_unlabelled`, `not_observed`) and every unwatched second
  with a cause on it: `before_first_sample`, `disconnected`,
  `after_last_sample`, `no_state_recorded`. Availability is *derived from*
  that ledger rather than computed beside it, and every figure carries
  **`coverage`** — how much of the window anybody watched — in the API, on
  the screens and in `/metrics`. An OEE window whose seconds do not add up is
  a bug, not a rounding difference, and a test says so on both code paths.
  Decision 0033; the page is *How much of the window did the MES see*.

- **`fsmes oee explain <machine> <window>`** prints that ledger as a table a
  plant engineer can argue with: every interval, its disposition, its cause,
  the recorded sentence behind a disconnection, the rule that produced it,
  and the totals. The window is a span (`8h`, `90m`) or a shift (`current`,
  `previous`, `2026-09-17/NIGHT`). It exits non-zero rather than print a
  table that does not balance.

- **A pack may set a coverage floor.** `[oee] coverage_floor` — a share in
  (0, 1], validated by `fsmes pack check` — makes this plant report a KPI as
  *unknown*, with the ledger attached, when it watched less of the window
  than that. **There is no default floor**: a plant that writes nothing gets
  every figure with its coverage beside it and nothing withheld. What is
  withheld is the figure, never the evidence. The three lab packs set 0.8;
  the cutlery demo deliberately sets none.

- **`/metrics` exports availability and coverage as a pair**
  (`mes_equipment_availability`, `mes_equipment_coverage`,
  `mes_equipment_not_observed_seconds` by cause, and `mes_coverage_floor`
  where a pack sets one), so a Grafana panel cannot show one without being
  able to show the other. An availability this MES cannot state is **absent**,
  not zero.

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

### Changed

- **An ERP order that carries no priority now says so.**
  `ProductionRequest.priority` is `null` when the ERP sent nothing — which for
  an ERPNext Work Order is always, since that doctype has no priority field —
  rather than 50. Fifty was this MES's own answer written into the border, so
  an order that had never carried a priority and one that carried exactly
  fifty arrived as the same document. The MES applies
  `[erp] default_order_priority` when it imports the order, which ships 50, so
  **nothing about a plant that leaves that key alone changes** — and a plant
  dispatching on a 1–9 scale can finally say what an ERP order should inherit.
  The published JSON Schema and its field notes say this; anything reading
  `priority` off a production request should read a null as *the ERP was
  silent*, not as *no priority*.

- **A setting a plant edits is refused by what the edit breaks, not by what is
  wrong with the table.** A pair rule — `cpk_marginal` against `cpk_capable`,
  `gauge_ratio_floor` against its adequate ratio, and now `all_pages_limit`
  against its cap — reports at whichever of its two keys reads best in the
  sentence. The write path only refused a problem reported *at the key being
  written*, so raising `cpk_marginal` over `cpk_capable` was refused while
  lowering `cpk_capable` under `cpk_marginal` — the same crossing-over, making
  *marginal* just as unreachable — was accepted. Both are refused now. It also
  means a plant already out of range on one key can still save the others,
  rather than having every input on the page refuse until that one is fixed.

- **A drafted work instruction records the model that actually wrote it.**
  `drafted_by_model` carried the model the product ships rather than the one
  that answered, which was the same string until `[system] local_model_name`
  existed and is not any more.

- **Eleven Quality settings move from the pack file to the plant's database,
  and the Configuration page edits them.** Scott, using the page: *"when I
  click on stuff, it doesn't seem to take me to where I can actually make those
  changes. Shouldn't it?"* He was right — each of those rows said *nobody, it
  changes when the pack is applied and the plant restarts*, and the link took
  him to the screen where the value's **effect** was visible with no control to
  change it anywhere. Now the Cpk bars, the two SPC rule lists, the readings
  behind a control limit and how far back a chart looks, the gauge rule of ten
  and its floor, a new gauge's calibration interval, the serials a certificate
  lists, a serial's digit width, what a non-conformance is called and how deep
  the packaging goes are all **typed into a box on Quality › Configuration by
  somebody holding `quality.define`, in force at once, audited, with no
  restart and no pack to re-apply.** No approval step: nothing in this MES
  records the Cpk bar that was in force when it judged something, so there is
  nothing for a revision to protect, and undo is typing the old number back.
  `fsmes pack check`'s own ranges refuse the same values from the page in the
  same sentences, including the two pairs — `cpk_marginal` is judged against
  the `cpk_capable` this plant is running on, not the product's default.
  [Editing a plant's own quality numbers](docs/operate/quality-numbers.md#editing-a-plants-own-quality-numbers).

  **Migration:** additive, and it moves no data. `plant_settings` starts empty
  on every existing plant, and a value is read in three layers — that table,
  then the `MES_QUALITY_*` setting the pack compiled, then the literal the
  product ships — so **a plant that has changed nothing behaves exactly as it
  did, and one already running on its own pack values keeps them.** The next
  `fsmes pack apply` seeds a row per key its pack carries, once: after that the
  database owns it and a later apply leaves it alone, the same rule every other
  kind a pack seeds already keeps. Editing `plant.toml` and re-applying no
  longer moves a number the plant has taken ownership of — change it on the
  screen; `fsmes pack status` reports the difference.

  And the mechanism is the pattern, not Quality's: a future plant-scope section
  becomes editable by setting `edit_here=True` on its `ConfigSection` and naming
  the capability in `define`. No table, endpoint, migration or JavaScript of its
  own. [Decision 0035 §11](docs/design/config-assistance.md).

- **The Configuration page says what each setting is set to.** A row used to
  say where the door was and who may open it. It now also says the value this
  plant is running on and whether that value is the product's default or one
  the plant chose — two answers and not three, because by the time a plant is
  serving, a pack key *is* an environment variable and the file it was
  compiled from is not recorded anywhere the running process can see. Naming a
  pack there would be a guess, and this column exists so nobody has to guess.
  Past twelve sections the page gains a search box and states its sort; below
  twelve it does not, because a search box on a list the eye can read whole is
  furniture.

- **The approvals panel takes a second kind, and one line of it had to
  change.** The panel, the endpoint that answers *what is waiting that I may
  approve* from the caller's live capabilities, the review and the generated
  walk all took the non-conformance severities without being touched — one
  entry in `services/review.py`'s `KINDS` registry, which is what decision
  0035 said a second vocabulary would test. What did not survive was a noun:
  the review panel spelled *recorded interval* in the browser, so a severity
  draft would have told an approver that non-conformances were downtime
  intervals. The server owns that word now, beside the coverage sentence that
  already did — `affected.records` and `affected.of` replace
  `affected.intervals_labelled` on
  `GET /dashboard/pending-approvals/{kind}/{code}/{revision}`.

- **The draft → approve lifecycle is one implementation, read by both
  vocabularies.** Draft, validate, approve, retire and undo were written for
  the downtime reasons over one table; they are now
  `fsmes.services.vocabulary`, with each list naming what is true of it alone
  — what carries its codes, what the product writes itself, what its own
  routes already spell. Two copies of *"retiring changes what may be chosen
  next and never what was chosen before"* would have been two copies that
  drift.

- **`fsmes config-audit` says whose answer each candidate is, not just where
  it lives.** The audit sorted seventy-five hard-coded judgments by domain, by
  size and by the reason each one qualified. It was missing the
  classification that decides who gets asked, so the command now carries a
  fourth one on every curated candidate — its **scope**. `general` means one
  default across every plant and a maintainer weighs in; `plant` means the
  plant's own answer, routed to its domain's *Configuration* tab with today's
  literal as the shipped default; `object` means a property of one tag,
  machine, material, gauge or order, set on that object's own row by the
  engineer looking at it. The rule, applied to all of them: *would two honest
  engineers at the same plant answer differently for two different objects?*
  → `object`; *would two honest plants answer differently?* → `plant`;
  otherwise → `general`. **Only the nine `general` items are open questions:**
  fifty-four `plant` and twelve `object` items are routed to the person who
  can actually answer them, which is the whole point — nobody can pick a
  counter-reset threshold for a counter they have never seen. `--scope
  general|plant|object` filters the list, combines with `--domain`, and
  `--json` carries each scope with the argument for it. The judgments live in
  `src/fsmes/sim/config_audit_curated.py`, a table in source the way the
  domain table is, and each row is anchored on a fragment of its line rather
  than a line number: a refactor moves a row and the tool says it moved, and a
  row whose anchor is gone is reported stale for a person to re-read rather
  than quietly pointing at whatever is on that line now. The audit page
  (`docs/design/config-audit-2026-09-21.md`) carries the same scopes in its
  tables, with a new §8 listing only what anybody is asked, and a §9 sketching
  bulk editing — the counter-reset threshold being the first case where an
  engineer wants to say *"set all production tags to a small threshold"* once
  rather than four hundred times.

- **The navigation gets one *Configuration* entry per workspace, instead of a
  chip per configurable thing.** *Engineering › Downtime reasons* was a
  top-level entry of its own for four days; with hundreds of configurable
  sections ahead of this product, a chip each is a nav bar nobody can read. So
  **Engineering › Configuration** (`/dashboard/config/engineering`) is now the
  door, and the downtime vocabulary is the first row on it — listed with what
  it changes, which capability drafts it and which one signs it, and the
  workspace's total, which is one today. **The screen itself did not move:
  `/dashboard/reasons` is the same page at the same address, so a bookmark
  still opens it; only the nav entry pointing at it directly is gone.**
  Nothing about who may draft or sign anything changed — each section keeps
  its own gates, and the Configuration page gates nothing. Adding the next
  configurable thing is now a `ConfigSection` entry in `fsmes.modules` and no
  navigation change at all; the first section in a new domain adds that
  domain's single Configuration entry, and a test refuses either half without
  the other.

- **Walking through a waiting draft now walks you to where the change takes
  effect.** The guided walkthrough on a downtime-reason review used to stay on
  the review panel and describe each change on a card beside it, which asked an
  approver to picture the effect rather than see it. A step whose change has a
  real control now **leaves the dashboard**: it opens
  `/dashboard/station` and rings the actual dropdown an operator picks a stop
  from, with the reason under review already selected in it. Which machine is
  computed from the plant's own records — the one that has recorded the most
  stops under that code — and the card says which machine and how many have
  recorded it at all; the vocabulary is plant-wide, so only the history can
  answer *which station*. The station screen opens that list **to be read, not
  answered**: no state change is armed and the buttons that would change the
  machine are off the screen while it is open. Three cases have nowhere honest
  to stand and keep the old in-place description — a brand-new reason (nothing
  has chosen it and the dropdown does not contain it), a code on the list that
  no machine has ever chosen, and a code already retired. The last step is
  still **Approve**, back on the review, and any step off the dashboard carries
  a *Back to the review* button; the review reopens when you return, and
  nothing is signed until Approve is pressed. No model is called anywhere in
  it — the destination is read out of the plant's records, exactly as the words
  are read out of the draft's own diff.

### Fixed

- **A walkthrough looked for its control once, 400 ms after the page loaded,
  and called a miss a matter of the person's role.** The Configuration page
  reads `/auth/me`, then its own sections, then renders every section before
  the one input a walk was sent to exists. On loopback that chain finishes in
  single-digit milliseconds and the single look always won; over a link with
  real latency in it — a laptop, a phone, a VPN — it does not, and the walk gave
  up while the page was still drawing the box. A missing control is now waited
  for on the budget the plant already sets for exactly this
  (`[screens] assistant_fill_attempts` x `assistant_fill_wait_ms`, three
  seconds by default), watching the document so the ring is drawn the moment
  the control appears rather than up to one poll later. The 400 ms is still
  there as a head start; it is no longer the only chance a step gets.

- **"That control is not on this screen for your role" named a cause the code
  could not know.** One sentence covered "the page has not drawn it yet", "a
  panel is hidden for this role" and "the thing it pointed at has been closed",
  and to somebody who had just asked to change a setting it read as a refusal
  of permission — reported by an administrator holding every capability the
  step needed. The card now says what actually happened and how long it looked,
  and names the role only where the step says which capability its control is
  drawn for and the person does not hold it; a settings walk now carries that
  capability on each of its steps, because the input and its Save are drawn
  only for somebody who may define the section. The same walk's last step no
  longer says only that saving needs a capability: built for a named person, it
  says whether they hold it — and if they do not, what would change that.

- **A pallet certificate computed capability only for materials numbered
  `UT-*`.** `services/coa.py` decided which materials were counted in pieces
  by testing the shape of their code, which is one plant's numbering
  convention living in product code: **any plant not numbering its pieces that
  way got an empty capability block on every pallet certificate and no error
  anywhere.** Whether a material is counted in pieces is now a fact recorded
  on the material — `materials.counted_in_pieces` — and the migration sets it
  true for exactly the materials that prefix chose, so nothing about an
  existing plant's certificates changes. It is the same answer, given honestly
  by a flag instead of guessed from a name. Found by the configuration audit
  of 2026-09-21 as the first of its three defects.

- **The trigger catalogue advertised a severity nothing in this product has
  ever written.** `open_nc`'s help read *"severity (minor|major|critical)"*,
  so a trigger configured from the catalogue's own words raised records graded
  `critical` — a word that existed on no list, in no enum and nowhere else in
  the product. The sentence now points at the plant's own severity list, and
  the value is checked against it wherever a plant has one.

- **Dependabot's Python pull requests arrive with `uv.lock` already
  regenerated.** `.github/dependabot.yml` asked for Python updates through
  the `pip` ecosystem, which edits the version constraints in
  `pyproject.toml` and has no concept of a lockfile. Since the `lockfile`
  check became required, that combination failed every Python bump on
  arrival — not one flaky run, but 100% of them, and a person had to run
  `uv lock` by hand on each. Python updates now run through Dependabot's
  `uv` ecosystem, which reads `pyproject.toml` and `uv.lock` together and
  writes both in the same commit. The weekly `python` group is unchanged: it
  groups on `patterns`, which `uv` supports.

- **The station screen no longer redraws itself for nothing.** Every panel on
  `/dashboard/station` cleared itself and rebuilt identical rows on each
  three-second refresh, and the quality results cleared *before* the read that
  refilled them — so on a screen that hangs on a wall for a shift, the recent
  readings blinked out twenty times a minute and the card lost 332 px of
  height while they did. Each list now compares what it is about to draw
  against what it is showing and leaves the DOM alone when they are the same;
  a real change still redraws in full.

- **The station stopped reporting itself disconnected from a plant it was
  reading.** Its maintenance panel read a paging envelope as if it were a
  list, so every refresh threw part-way through: the screen said
  *reconnecting…* with nothing wrong with the connection, and never drew a
  maintenance job. It now asks for the open work on the machine the way the
  Maintenance screen does, and says how many are open when it draws fewer.

- **A floor screen no longer stops people signing in, and it no longer takes
  ten seconds.** On a six-machine plant with a shift of one-second history,
  `/dashboard/summary` took **10 s**, `/equipment/{code}/oee` and
  `/analysis/oee` returned **500** with `database is locked`, and sign-ins
  hung and timed out at five seconds. Two causes, both fixed.

  `counted_outside_run_time` asked, for every booking, whether *any* running
  interval contained it — a search that cannot stop early on a booking that
  was not running, so its cost grew with bookings × history. A state history
  does not overlap, so the only interval that can contain an instant is the
  last one starting at or before it; asking for exactly that row gives the
  same numbers, to the unit. Measured on a copy of a real plant (94,000
  bookings, 12,000 intervals): that query **8.2 s → 0.07 s**, the summary
  **8.0 s → 0.20 s**, `/equipment/{code}/oee` **2.0 s → 0.02 s**,
  `/analysis/oee?hours=1` **1.25 s → 0.05 s**. Two indexes came with it —
  `production_logs (equipment_id, ts)` and `tag_values (equipment_id, id)`,
  the second for the machine card's latest reading, which was reading and
  sorting the whole of a machine's tag history to find its newest row.
  **The coverage ledger was not the cost**: it was 14 ms of the ten seconds.

  And on SQLite every transaction took the single write lock, reads included,
  so a slow screen refresh held up everything behind it — including
  `/auth/login`, which only reads. The endpoints that only read now take a
  unit of work that begins deferred and that the database will not let write
  (`PRAGMA query_only`; `SET TRANSACTION READ ONLY` on PostgreSQL), so a read
  never needs the writer's lock and never queues for it. Everything else
  still takes the lock up front, deliberately.

  Both are pinned by tests: a generated six-machine plant with eight hours of
  one-second history that holds `/dashboard/summary` and `/metrics` to a
  stated budget, and a file-backed plant where a sign-in and both OEE
  endpoints have to answer while the plant is being written to.

- **A plant on SQLite can book its own production while somebody is looking
  at it.** The same night as the entry above, from the writers' side. A lab
  plant running six hours at replay speed 10 logged **18,564** `database is
  locked`, 825 HTTP 500s and 754 failed shop-floor steps, because a read held
  the single write lock for as long as it took. The read-only unit of work
  above is what fixes that; this extends it to **every** request that only
  answers a question — GET, HEAD and OPTIONS get one whether or not the route
  asked, and so do the capability gate and the idempotency lookup, because a
  GET that takes the write lock can stop a plant booking and there are more
  routes than anyone will remember to move. Measured on a reproduction that
  runs the agent, three screens and a shop floor against one file database,
  sixty seconds: 55 lock failures → **0**; 2 of 600 agent batches booked →
  **600**; a floor that could not sign in → 545 steps, none refused.
  PostgreSQL is untouched, by design.

- **`MES_SQLITE_BUSY_TIMEOUT_MS`** (default 15,000, was a hard-coded 5,000):
  how long a write waits for the lock before giving up. Five seconds was
  chosen when reads took the lock too, so raising it could only make a doomed
  wait longer. SQLite also runs with `synchronous=NORMAL` in WAL now, which
  fsyncs at a checkpoint rather than on every one of a plant's ten commits a
  second. What that costs, plainly: a power cut or kernel panic can lose the
  last commits that had not reached a checkpoint. It cannot corrupt the
  database, and a process crash loses nothing.

- **Nothing that calls a model holds the plant's write lock while it waits.**
  `POST /design/chat` took a request session — which on SQLite is the plant's
  one write lock, held until the response is sent — and inside it made up to
  four network calls: two on-device compressions at 90 s each, a
  classification at 45 s, and a frontier model with no timeout of its own.
  The symptom was `Could not reach the design surface: 500` on a plant that
  had stopped being able to book anything. `POST /assist/ask` and
  `POST /assist/agent` had the same shape. None of the three takes a request
  session now; each reads what it needs, closes, and only then calls out. A
  guard test holds every route to it.

- **A plant's log is one line per failure, and has a ceiling.** The lab
  plant's console log reached 6,348,536 lines and 1.1 GB in six hours — about
  ten gigabytes a day. Its last 150,000 lines covered two and a half minutes
  and carried 386 actual log events: one `database is locked` was rendering
  as a 282-line rich traceback with locals and source excerpts, ten times a
  second. Failures now say what was being written, for which machine, how
  long they waited and how many units are owed, in one line; the stack goes
  to DEBUG. httpx's per-request INFO lines and uvicorn's access lines for the
  plant calling its own API are silenced — anybody else's request is still
  logged. A machine counting with no order open says so once a minute with
  the units since, rather than once a booking. `logs/<plant>/plant.log` from
  `fsmes fleet start` is capped at 50 MB a file and four files: 200 MB, and
  no setting can raise it. Measured on the reproduction: 17,381 lines and
  2.7 MB a minute → **12 lines and 2.5 kB**. A budget test pins 20,000 lines
  and 4 MB per plant-hour.

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

- **Nothing is backfilled into the five new columns, and null is *not
  attributed*.** Every row written before this release keeps a null in
  `ai_turns.screen`, the two shift columns on `ai_turns` and `audit_log`,
  `production_logs.booked_by` and `personnel.home_equipment_id`, and no
  screen, envelope or tool turns that into a value. A shift could have been
  re-resolved onto old `audit_log` rows the way `c6a4b81f39d7` did for the
  floor tables, and it would have been wrong here: that migration stamped
  rows against the patterns in force at the time, while an audit row from a
  year ago would be stamped against a roster written since. `booked_by`
  could have been copied out of the audit trail, and that turns a join a
  reader can check into a column they cannot.

  So every place that uses one of these columns states what it could not
  attribute: `turns_without_a_screen` on the trace rollup and on every
  conversation row, the unattributed count on a `workcenter` breakdown, and
  an `unattributed` node carrying its degree on the graph. A booking from a
  counter delta or from another system names nobody rather than naming
  `"system"`; a person the plant has not placed reads "—" on the personnel
  screen rather than being filed under the first line on the list; and a
  node kind that comes back empty says why it might be, so *nothing
  happened* and *nothing is recorded* are told apart. **Migration:**
  `c7a2e94b16d3`, additive and nullable; no existing figure changes.

- **The downtime pareto states both totals.** It groups by code where there is
  one and by typed text where there is not, and the two never merge — a code is
  a choice from a list somebody approved and a sentence is not.
  `GET /analysis/downtime` now carries `from_the_list_seconds` and
  `typed_seconds` beside the existing unlabelled share, and the three account
  for every second of downtime in the window. One number alone would let a
  plant with six codes and a thousand typed sentences look like a plant with
  six reasons, and *how much of this window came from the list* is the number
  that says whether the vocabulary is being used at all. A plant with no
  vocabulary reports `vocabulary_total` 0 and nothing from a list, which is the
  truth rather than a plant ignoring its own list. **Migration:** none — no
  figure changes for any plant that has not named its reasons, and
  `unlabelled_share` means exactly what it meant.

- **Production a machine counted is no longer lost when the write that books
  it fails.** The OPC agent measures a counter delta against the last value
  it saw, and moved that baseline *before* the booking committed — so a
  failed write took its units with it permanently, and the retry, measuring
  from the moved baseline, found a delta of zero, committed nothing and
  reported success. A lock failure left no trace in the counts at all.
  Measured: seventy `failed to book production` lines at eight to ten units
  each in the last 150,000 lines of one lab plant's log; in the reproduction,
  twelve units counted and **zero** booked in sixty seconds. The baseline now
  moves only when the transaction that used it has committed, so an attempt
  that fails leaves the counter where it was and the next reading re-measures
  the whole delta: late, never lost. `failed to book decisions` and `failed
  to book production` say how many units are owed. **Migration:** none —
  nothing already recorded changes. A plant that ran under contention before
  this is short by whatever its log's `failed to book production` lines
  named, and those lines are the only record of it. Decision 0034.

- **A performance or OEE figure above 100 % is no longer printed anywhere.**
  Where the counted work will not fit inside the run time — which is the same
  inequality as `performance > 1.0` — the MES now reports **no performance
  figure and no OEE**, and prints the sentence naming the disagreement and
  both its numbers on every surface the figure used to reach: the dashboard
  tile and machine card, the machine page, `/analysis/oee`,
  `/kpis/oee/{code}`. Nothing is capped and nothing is discarded: the ratio
  is published as **`performance_ratio`**, with **`counts_outrun_run_time`**
  beside it, under a name that says what it measures — two of this MES's own
  records disagreeing, not the machine.

  *Migration.* `performance` and `oee` are `null` on exactly the machines
  where they used to read above 1.0; read `performance_ratio` if you were
  consuming the number, and `performance_note` for the sentence. The plant
  OEE tile is a mean over the machines that have a figure and now says how
  many that was out of how many there are; `/analysis/oee` gains
  `stations_rated` and `stations_counts_outrun` for the same reason. Decision
  0026, amended.

- **A plant replaying a recorded line faster than real time computes its rates
  on the line's clock, and says that it is doing so.** Such a plant counts at
  the line's pace and measures every duration on the wall's, so performance —
  the one OEE factor that divides a count by a duration — came out by the
  replay factor: a lab plant at `--speed 10` showed performance 881 % and OEE
  980 %. The API reads the factor from `MES_SIM_SPEED`, which `fsmes fleet
  start --speed` already puts in the environment of every process of that
  plant, and restates the run time before dividing. It is declared rather than
  applied quietly: a **Replay 10×** bar on every screen (the shadow-mode bar's
  shape and argument), `replay` on `/health`, `mes_replay_factor` in
  `/metrics`, a **replay** pill on the fleet console, and `clock` beside the
  figures. Availability and quality are unaffected — they are ratios of things
  measured the same way — and durations stay on the wall clock, with the
  factor beside them.

  *Migration.* Nothing changes for a plant that does not set `MES_SIM_SPEED`,
  which is every real plant: the factor is 1.0 and the conversion is the
  identity. **A plant meant to be looked at runs at 1×**; above 1× it is a
  test harness and the screens now say so. Decision 0026, amended.

- **Availability's denominator changed meaning: it is now the time this MES
  watched, all causes together, not the window less the disconnections it
  recorded.** Seconds in a hole in the state history that no disconnection
  covered — an agent killed outright writes none on its way down — used to
  sit in that denominator priced as time the machine was not running, and now
  leave it as `not_observed`.

  *Migration.* Availability figures go **up** on any window where such a hole
  was large and the machine was running through the watched part of it, and
  the change is nil where the state history is contiguous — which is most
  windows on a healthy plant. Read `coverage` beside every figure from now
  on; a figure without it cannot be read as covering its window. A plant that
  sets `[oee] coverage_floor` will additionally see KPIs it used to get as
  numbers come back as *unknown* with the ledger attached; that is the pack
  asking for it, and removing the key restores the numbers. `window_hours`,
  `unknown_seconds` and `unknown_share` keep their existing meanings.
  Decision 0033.

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

- **The lab packs carry an order book, and the simulated floor works it.**
  `labs/multiplant/bottling`, `machining` and `finewire` each ship a schedule
  rather than a single order: enough released and planned work to cover more
  than twenty-four hours of that line's own rated output — bottling 10 orders
  and 151,600 bottles, machining 9 and 27,600 brackets, finewire 5 and
  26,000 kg — one order released, the rest planned, due dates in sequence.
  Each pack's `masterdata/README.md` shows the arithmetic. `fsmes
  run-operations` gains the shift supervisor's half of the job: when the line
  has made the quantity, `FLOOR-SUP` finishes the order over the API and
  releases the next one in the book, and the audit row carries their name.
  **Decision 0029 is unchanged** — the MES still does not finish an order at
  its quantity; what finishes one is somebody's act, and here that somebody is
  simulated and says so. The floor no longer invents an order when it runs
  out of work: an empty book is announced once, and what the line counts from
  then on is unassigned production with its total, which is the true answer.

  The reason: a bottling lab plant left up for six hours on 2026-09-18
  reported `WO-ACME-4711` at **285,881 good against an order for 4,000**.
  Nothing was wrong with the MES; the plant had one order and nobody to
  release a second.

  **A plant created before this keeps its old book.** `fsmes pack apply` never
  rewrites an order that already exists, because a pack that rewrote history
  would be rewriting production. Rebuild the plant — `fsmes fleet create`, or
  `crew lab-up --fresh` — to get the new one.

- **A pack's `work_orders.json` takes `due_in_hours`.** Hours after the pack
  is applied, validated as a positive number by `fsmes pack check`. Due dates
  in a pack have to be relative: a pack is seeded whenever somebody builds the
  plant, so an absolute date written into one is in the past the week after it
  was written.

- **An experiment plan takes `[floor] finish_orders`.** Default true.
  `labs/experiments/over-run.toml` sets it false, because a line running past
  its order is the whole subject of that hour and a supervisor tidying it away
  would end the experiment forty minutes in.

- **`fsmes fleet status` and the fleet console show the order book** — how
  many orders a plant has planned, released and running, read off `/metrics`,
  which the fleet tooling could already ask without a credential. A plant that
  did not say is `unknown`, never an empty book: a plant that is up with
  nothing left to run and a plant nobody could ask are different facts.

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
