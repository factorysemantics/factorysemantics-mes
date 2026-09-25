"""The seventy-five candidates a person kept, and where each one's answer lives.

`config_audit.py` scans. This file remembers. The scan finds literals; a
person read all 419 of them on 2026-09-21 and kept 75, and those carry two
judgments a scanner cannot make: which **domain** owns the setting, and — added
here on 2026-09-22 — what its **scope** is.

**Seventy-six rows, where the design page lists seventy-five.** `Q0` — the Cpk
bar at which a process is called capable — is the audit page's own calibration
example, worked through in its section 2 and never given a row number in its
section 4. It is a candidate like any other, so it is carried here with a
scope, and both totals are said out loud rather than one of them quietly
becoming the other.

**Scope answers "whose answer is it?", and it decides who is asked.**

- `general` — one default across every plant. A product-level decision; the
  maintainer weighs in on it, and a plant that wants something else is asking
  for the product to change its mind.
- `plant` — the plant's own answer. It lives in `plant.toml` or the plant's
  masterdata, under its domain's Configuration tab. **The shipped default is
  today's literal, unchanged, and nobody is asked anything.**
- `object` — a property of one tag, machine, material, gauge or order. It
  lives *on the object*, beside `state_map` and `cycle_seconds` in the tag
  map or the equivalent row, and the engineer looking at that object sets it.
  **Nobody is asked anything here either.**

**The rule for deciding, applied to every row below:**

> Would two honest engineers **at the same plant** answer differently for two
> different objects? → `object`.
> Would two honest **plants** answer differently? → `plant`.
> Otherwise → `general`.

The order matters: the object question is asked first, because an answer that
differs between two tags on one line cannot be a plant-wide setting without
being wrong for one of them.

**Where a row is unsure, it says so** in `unsure` rather than picking quietly.
Seven of the seventy-five carry that line.

## How this survives a rerun

The scan is true on the day it runs; a curated judgment has to outlive a
refactor. So a row is anchored on `needle` — a distinctive fragment of the
line it lives on — and not on the line number. `line` is where it sat when the
list was written; `config_audit` searches the file for `needle` and reports
where it is *now*, so a row that moved is reported as moved rather than going
quietly stale, and a row whose needle has gone is reported as **stale**, which
is a person's cue to read it again. Nothing here is generated: this is a table
somebody argued their way to, written down where it can be argued with again,
exactly the way `DOMAIN_RULES` is.

A row is not required to be one the scanner can see. Thirty of these were
found by a person reading the files - a judgment written as a `for` loop, a
prose house style, a `startswith` test - and `config_audit` reports which of
the two found each row, so the scanner's own blind spots stay visible.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The three answers to "whose answer is it?", with what each one means for
#: who gets asked. Ordered the way the report prints them.
SCOPE_TITLES: dict[str, str] = {
    "general": "General - one default across every plant; a product decision",
    "plant": "Plant - the plant's own answer, under its domain's Configuration tab",
    "object": "Object - a property of one tag, machine, material, gauge or order",
}

#: The rule, in one sentence, so the report can print the test beside the
#: answer rather than asking a reader to go and find it.
SCOPE_RULE = (
    "Would two honest engineers at the same plant answer differently for two "
    "different objects? -> object. Would two honest plants answer "
    "differently? -> plant. Otherwise -> general.")


@dataclass(frozen=True)
class Curated:
    """One candidate a person kept, with the scope they argued it into.

    `line` is where it sat on 2026-09-21; `needle` is what actually anchors
    it. When the two disagree, the needle wins and the report says the row
    moved.
    """

    id: str
    domain: str
    scope: str
    what: str
    path: str
    """Relative to `src/fsmes`, the way the design page cites it."""
    line: int
    needle: str
    why: str
    """Why this scope, in one or two sentences. The argument, not the label."""
    unsure: str | None = None
    """The other answer, and what would settle it. None when the row is settled."""

    settled: str | None = None
    """What this candidate became, and when, once it stopped being a literal.

    A row does not leave this list when it is built. The list is what a
    person read on 2026-09-21 and the argument they made; deleting a row the
    day somebody acted on it would leave the page claiming a smaller tree
    than it read, and would lose the argument with it. So the row stays, its
    `needle` moves to where the judgment lives now - the key's own default -
    and this sentence says what happened.
    """


#: The sentence a row gets when the literal it named became a key, said the
#: same way every time so a reader can see at a glance which of these are
#: answered and which are still questions.
DONE = "Built 2026-09-22: it is `[quality] %s`, shipping the literal that was here."

#: The same sentence for the six supply-chain rows, built three days later
#: against the pattern the quality ones established.
DONE_ERP = "Built 2026-09-25: it is `[erp] %s`, shipping the literal that was here."

#: The same sentence for the seventeen Engineering rows built on 2026-09-25, on
#: the mechanism §11 of the design page wrote down after Quality's. Its shape is
#: deliberately identical: a reader scanning this file should be able to see
#: which rows are answered without reading two different wordings for it.
LIVE = ("Built 2026-09-25: it is `[%s] %s`, shipping the literal that was here, "
        "and it is edited on Engineering's Configuration page - seeded by the "
        "pack, owned by the database, in force when it is saved.")


CURATED: tuple[Curated, ...] = (

    # ------------------------------------------------------ quality engineering

    Curated(
        "Q0", "quality", "plant",
        "The Cpk bar at which a process is called capable",
        "config.py", 246, "quality_cpk_capable: float = 1.33",
        why="1.33 is a widely used industry convention and not a law of "
            "physics. A plant with looser tolerances or a stricter quality "
            "culture can set a different bar and still be telling the truth "
            "about itself; the Cpk number keeps its meaning either way, and "
            "only the English word beside it moves.",
        unsure="It could be object. A plant that grades characteristics as "
               "critical, major and minor may want a higher bar on a "
               "critical one; what would settle it is whether any plant "
               "charts two characteristics it would judge capable at "
               "different numbers. It is section 2's calibration example and "
               "has no row in section 4 of the design page, so the curated "
               "list is 76 where that page's list is 75.",
        settled="Built 2026-09-22: it is `[quality] cpk_capable` and "
                "`cpk_marginal`, shipping the 1.33 and the 1.0 that were "
                "here. The Cpk itself never moved - only the word beside it - "
                "and the browser's own copy of the bar went with it."),
    Curated(
        "Q1", "quality", "plant",
        "Fewest readings before control limits are drawn at all",
        "config.py", 45, "quality_spc_min_points: int = 12",
        why="One plant draws limits at twelve readings and one insists on "
            "twenty-five, because it is a statement about how much data that "
            "plant's quality system is willing to trust. Inside a plant the "
            "answer is the same for every characteristic.",
        settled=DONE % "spc_min_points" + " The pallet certificate prints the "
                "key's value rather than the word twelve."),
    Curated(
        "Q2", "quality", "plant",
        "Which SPC rule counts as a major finding",
        "config.py", 370, 'quality_major_rules: str = "1"',
        why="Which rule is major is one triage policy for the whole plant. "
            "Two characteristics do not want different answers; two plants "
            "with different escalation paths do.",
        settled=DONE % "major_rules" + " The two words themselves come from "
                "the plant's severity vocabulary."),
    Curated(
        "Q3", "quality", "plant",
        "The non-conformance severity vocabulary itself",
        "domain/severities.py", 99, 'class NcSeverity(Base):',
        why="A vocabulary is the plant's own words, used the same way "
            "everywhere in it. critical/major/minor/observation at one plant "
            "and a 1-to-5 scale at another are both honest.",
        settled="Built 2026-09-22: `nc_severities`, the second draft-then-"
                "approve vocabulary in this product, with `quality.define` "
                "and `quality.approve`, a pack kind and a screen. It ships "
                "the two words the source writes and no third."),
    Curated(
        "Q4", "quality", "plant",
        "How far back a chart and the rules look",
        "config.py", 49, "quality_spc_history: int = 200",
        why="A plant inspecting every fifteen minutes and one inspecting "
            "hourly want different histories behind one chart.",
        unsure="It could be object. A plant that samples one characteristic "
               "every unit and another once a shift might want the history "
               "per characteristic; what would settle it is whether any real "
               "plant asks for two different windows on one chart screen.",
        settled=DONE % "spc_history"),
    Curated(
        "Q5", "quality", "general",
        "Which Western Electric rules are in force",
        "services/spc.py", 84, '"a point beyond three sigma"',
        why="Whether a plant may switch a rule off at all was a question "
            "decision 0035 had not answered - section 2 of the audit argues "
            "the chart is the product's, and a chart drawn with rule 4 off "
            "and still called an SPC chart does not say what it appears to. "
            "That is a product decision, which is why it is on the short "
            "list. Decision 0036 made it on 2026-09-22 and it is built: every "
            "rule is drawn and recorded on every plant, and `[quality] "
            "hold_rules` chooses which of them raise a hold. All four is the "
            "shipped default, so a plant that configures nothing is "
            "unchanged.",
        settled="Decided and built 2026-09-22 - decision 0036, `[quality] "
                "hold_rules`. The chart draws and records every rule on every "
                "plant; only who gets called changed."),
    Curated(
        "Q6", "quality", "plant",
        "The gauge rule of ten, and its floor of four",
        "config.py", 179, "quality_gauge_ratio_adequate: float = 10.0",
        why="AIAG says 10:1 and ANSI Z540 says 4:1. A plant follows one "
            "standard, for every gauge it owns.",
        settled=DONE % "gauge_ratio_adequate" + " Its floor is "
                "`gauge_ratio_floor`, and the two are checked against each "
                "other so neither can make the other unreachable."),
    Curated(
        "Q7", "quality", "object",
        'A gauge is "due soon" thirty days out',
        "domain/gauges.py", 31, "warn_days: Mapped[int] = mapped_column(default=30)",
        why="A quarterly calibration wants fourteen days of warning and an "
            "annual one wants sixty, and both gauges hang in the same "
            "calibration room. It belongs beside the gauge's own interval, "
            "set by the person who calibrates it.",
        settled="Built 2026-09-22: a `warn_days` column on the gauge, thirty "
                "by default - the number the browser had invented for itself "
                "at three places on one screen. The server answers `due_soon` "
                "now, so the floor's definition of the phrase left JavaScript."),
    Curated(
        "Q8", "quality", "plant",
        "Default calibration interval for a new gauge",
        "config.py", 59, "quality_gauge_default_interval_days: int = 365",
        why="The per-gauge interval is already the engineer's. What is "
            "hard-coded is the plant's house default for a gauge nobody has "
            "set one on yet.",
        settled=DONE % "gauge_default_interval_days" + " The three copies - "
                "the service, the API schema and the browser's form - became "
                "one."),
    Curated(
        "Q9", "quality", "object",
        'Which materials are "pieces" on a pallet certificate',
        "domain/masterdata.py", 277, "counted_in_pieces: Mapped[bool]",
        why="Whether a material is counted in pieces on a certificate is a "
            "fact about that material. Two materials in one plant answer "
            "differently, which is exactly why a prefix test is standing in "
            "for the flag today.",
        settled="Built 2026-09-22: `materials.counted_in_pieces`, migrated "
                "true for exactly the materials the `UT-` prefix chose. This "
                "also closes the first of the three defects section 6 names - "
                "a plant numbering its pieces any other way got an empty "
                "capability block and no error."),
    Curated(
        "Q10", "quality", "plant",
        "How many serials a certificate lists before truncating",
        "config.py", 175, "quality_coa_serials_listed: int = 200",
        why="How long a certificate may be is the plant's agreement with the "
            "people who read it, and the plant prints them all the same way.",
        unsure="It could be object. If one customer demands every unit listed "
               "and another does not, the answer sits on the customer or the "
               "material; what would settle it is whether any plant runs two "
               "certificate agreements at once.",
        settled=DONE % "coa_serials_listed"),
    Curated(
        "Q11", "quality", "plant",
        "The serial number format",
        "config.py", 87, "quality_serial_digits: int = 6",
        why="One label scheme per plant, read by its own scanners.",
        settled="Built 2026-09-22 as `[quality] serial_digits`, the width "
                "only. The separator stays the product's and the row says why: "
                "`next_serial`'s recovery scan reads `PREFIX-digits` to find "
                "where a plant's numbering had got to, so a plant that changed "
                "the hyphen would start again at one over labels already on "
                "pallets."),
    Curated(
        "Q12", "quality", "plant",
        "The non-conformance code format",
        "config.py", 158, 'quality_nc_code_prefix: str = "NC"',
        why="A plant that calls them NCRs calls all of them NCRs.",
        settled=DONE % "nc_code_prefix" + " The width of the number after it "
                "stays the product's."),
    Curated(
        "Q13", "quality", "plant",
        "How deep a containment tree may go",
        "services/serialization.py", 48, "MAX_DEPTH = 6",
        why="How deep the plant's own packaging goes - piece, stack, pack, "
            "case, pallet, truck - is one answer per plant, not per shipment. "
            "It doubles as a loop guard, so the plant's key needs a ceiling "
            "over it.",
        settled=DONE % "containment_max_depth" + " The ceiling is "
                "`serialization.DEPTH_CEILING`, twelve, and a plant may "
                "choose how deep its packaging goes without switching off the "
                "guard."),

    # --------------------------------------- manufacturing / process engineering

    Curated(
        "P1", "process", "plant",
        'The maintenance "coming due" warning at 80% of an interval',
        "config.py", 430, "process_maintenance_due_soon_fraction: float = 0.8",
        why="Eighty per cent is a fraction of each plan's own interval, so it "
            "already scales: a weekly greasing and a yearly overhaul do not "
            "need different fractions. What differs between plants is how "
            "much warning their spares lead time needs.",
        settled=LIVE % ("process", "maintenance_due_soon_fraction")
                + " The browser had its own copy in web/maintenance.js, colouring "
                + "the bar amber at eight tenths; it reads the server's due_soon "
                + "now, so the bar and the sentence beside it cannot disagree."),
    Curated(
        "P2", "process", "plant",
        "Fallback cycle time when a machine has no rating",
        "config.py", 436, "process_default_cycle_seconds: float = 3.0",
        why="The per-object answer already exists - it is the machine's own "
            "cycle rating. What is hard-coded is the guess a plant makes for "
            "a machine nobody has rated yet, and a filling line and a "
            "machine shop guess differently.",
        settled=LIVE % ("process", "default_cycle_seconds")),
    Curated(
        "P3", "process", "plant",
        "How long a maintenance job with no plan is assumed to take",
        "config.py", 437, "process_default_job_minutes: float = 60.0",
        why="Same shape as P2: the per-job estimate is the planner's, and the "
            "hour is the plant's fallback when there is none.",
        settled=LIVE % ("process", "default_job_minutes")
                + " Both call sites read the one key - the backlog's cost line and "
                + "the block the scheduler reserves."),
    Curated(
        "P4", "process", "plant",
        "Default expected duration of a new maintenance plan",
        "config.py", 441, "process_maintenance_plan_default_minutes: float = 30.0",
        why="The per-plan column exists; only the house default is in code.",
        settled=LIVE % ("process", "maintenance_plan_default_minutes")
                + " All three copies went: the service resolves it, the API field "
                + "and the browser form send nothing, and a pack that omits it gets "
                + "the plant's own number rather than the product's thirty."),
    Curated(
        "P5", "process", "plant",
        'The default reporting window - eight hours, "a shift"',
        "config.py", 448, "process_default_report_hours: float = 8.0",
        why="Shift length. The plant already states it, in shift_patterns; "
            "this is the same fact written a second time in code.",
        settled=LIVE % ("process", "default_report_hours")
                + " The accessor lives in services/calendar rather than "
                + "services/analysis, because this page's own argument for the key "
                + "is that a plant already states its shift length in "
                + "shift_patterns - and because the OEE path in services/equipment "
                + "reads the same number, which analysis cannot lend it without "
                + "importing its own caller."),
    Curated(
        "P6", "process", "plant",
        "The fixed list of windows every screen offers",
        "config.py", 449, 'process_report_windows: str = "0.25,1,8,24,168"',
        why="The same judgment as P5 one level up: which windows a plant's "
            "people work in. Its own shifts are the honest source.",
        settled=LIVE % ("process", "report_windows")
                + " The list was in the browser, so the browser reads it from GET "
                + "/dashboard/screens and builds each label from the number itself. "
                + "A viewer who has chosen a window of their own keeps it whatever "
                + "the plant says. Same item as A8, built once."),
    Curated(
        "P7", "process", "plant",
        "How many machines a Gantt draws",
        "config.py", 453, "process_gantt_screenful: int = 12",
        why="A six-station cell and a 108-station plant want different "
            "screenfuls, and one plant wants one answer for all its screens.",
        settled=LIVE % ("process", "gantt_screenful")),
    Curated(
        "P8", "process", "plant",
        'How far back "the previous shift" may reach',
        "config.py", 459, "process_previous_shift_horizon_days: int = 14",
        why="A seasonal plant with a six-week shutdown answers differently "
            "from a continuous one. Inside a plant it is one answer, and the "
            "refusal sentence it produces is the plant's own.",
        settled=LIVE % ("process", "previous_shift_horizon_days")
                + " The refusal sentence quotes the plant's own number now rather "
                + "than a fortnight nobody chose."),
    Curated(
        "P9", "process", "plant",
        "The default working week",
        "config.py", 464, 'process_working_week_mask: str = "1111100"',
        why="The mask format is a product fact; which mask a plant starts "
            "from is the plant's calendar.",
        settled=LIVE % ("process", "working_week_mask")
                + " A pack that omits a shift's days gets this plant's week rather "
                + "than being refused, which is the question this row asked: "
                + "seeding five days was a guess about somebody else's plant and is "
                + "now the plant's own stated answer, so there is nothing left to "
                + "refuse. The column default in domain/calendar.py stays the "
                + "product's - a column default cannot read a plant's settings - "
                + "and says so. Same item as A25, built once."),
    Curated(
        "P10", "process", "plant",
        "The floor below which no rate is reported at all",
        "config.py", 335, "oee_min_observed_seconds: float = 10.0",
        why="Its sibling suppression threshold is already a pack key - "
            "[oee] coverage_floor - which is a plant-level answer, argued "
            "that way when it was made one.",
        settled=LIVE % ("oee", "min_observed_seconds")
                + " In [oee] beside coverage_floor, which is where this row said it "
                + "belonged. Carried on coverage.Ledger and coverage.Totals as a "
                + "field rather than read inside availability, because a frozen "
                + "account of one window must not answer the same question two "
                + "ways."),
    Curated(
        "P11", "process", "plant",
        "The schedule board's default horizon",
        "config.py", 467, "process_schedule_default_horizon_hours: float = 24.0",
        why="A job shop planning a fortnight and a line planning a shift want "
            "different boards; each plant wants one board.",
        settled=LIVE % ("process", "schedule_default_horizon_hours")
                + " The board states the window it drew, and the picker opens on the "
                + "plant's horizon - adding it to the four offered when it is none "
                + "of them, rather than rounding it to one of them."),
    Curated(
        "P12", "process", "object",
        "What counts as a good yield",
        "web/orders.js", 43, "value >= 0.98",
        why="A difficult casting and a simple assembly do not have the same "
            "good yield, and one plant can make both. The target belongs on "
            "the material or the line, set by the engineer who owns it.",
        unsure="A plant making one product family would call this plant-wide "
               "and be right. What would settle it is whether any plant's "
               "materials disagree by more than the warning band; until an "
               "object carries a target the shipped pair stays plant-wide."),

    # ----------------------------------------------------- controls engineering

    Curated(
        "C1", "controls", "object",
        'Tag staleness - "not live after a minute"',
        "services/tags.py", 45, "STALE_AFTER_SECONDS = 60.0",
        why="How long a value stays live is a property of the tag's own "
            "update rate. A counter published every 200 ms and a historian "
            "value polled every five minutes go stale at different ages, on "
            "one line, in one plant. It belongs beside cycle_seconds."),
    Curated(
        "C2", "controls", "object",
        "OPC reconnect wait - a flat three seconds, forever",
        "integrations/opc/agent.py", 1016, "await asyncio.sleep(3)",
        why="A flaky wireless gateway and a wired backbone are two endpoints "
            "in the same plant, and hammering the first one every three "
            "seconds is what the engineer who owns it would change. It "
            "belongs on the connection."),
    Curated(
        "C3", "controls", "plant",
        "Booking retry count and backoff before readings are given up",
        "config.py", 480, "controls_opc_book_attempts: int = 4",
        why="This is the agent arguing with its own database, not with a "
            "machine: the same contention policy applies to every tag it "
            "books. Its sibling sqlite_busy_timeout_ms is already a plant "
            "setting.",
        settled=LIVE % ("controls", "opc_book_attempts")
                + " Its pair opc_book_backoff_s goes with it, so the half of this "
                + "argument that was already configurable - sqlite_busy_timeout_ms "
                + "- is no longer the only half. Read off self rather than the "
                + "class, so a test or a subclass that sets one still has it "
                + "honoured."),
    Curated(
        "C4", "controls", "object",
        "Inspection-group grace and timeout",
        "integrations/opc/agent.py", 118, "GROUP_GRACE_S = 0.25",
        why="A station behind a gateway that fans its tags out over half a "
            "second and a station that publishes them together want "
            "different grace, and both are in one plant.",
        unsure="It could be plant, or per connection rather than per station: "
               "the agent applies one number to every group today. What "
               "would settle it is whether the partial readings show up on "
               "particular stations or across a whole endpoint."),
    Curated(
        "C5", "controls", "object",
        "How long a station's current order is trusted",
        "integrations/opc/agent.py", 123, "ORDER_CACHE_S = 10.0",
        why="A job-shop station changing over every ninety seconds and a "
            "continuous line trust a cached order for very different "
            "lengths of time, and one plant has both. Ten seconds of "
            "mis-booked units at every changeover is the cost."),
    Curated(
        "C6", "controls", "object",
        'Setpoint "did the process follow?" fraction',
        "services/adjustments.py", 32, "FOLLOWED_FRACTION = 0.5",
        why="A fast electric heater and a two-tonne oil bath follow a "
            "setpoint differently, and the same 0.5 marks one plant's every "
            "adjustment verified and its neighbouring line's every "
            "adjustment failed. It belongs on the loop, beside its lag."),
    Curated(
        "C7", "controls", "object",
        "Default verification wait: three time constants, a 40 s assumed lag, "
        "a 30 s floor",
        "services/adjustments.py", 80, 'meta.get("lag_s", 40.0)',
        why="lag_s is already a per-recommendation field, which is the "
            "product admitting this is a fact about one process. The forty "
            "seconds and the thirty-second floor are the same per-object "
            "judgment, left in code."),
    Curated(
        "C8", "controls", "plant",
        "Unified-namespace delivery retry policy",
        "config.py", 492, "controls_uns_max_attempts: int = 8",
        why="One broker per plant, one retry policy. uns_qos, uns_batch, "
            "uns_inflight and uns_poll_seconds are already plant settings; "
            "this is the piece left behind.",
        settled=LIVE % ("controls", "uns_max_attempts")
                + " All three numbers, as uns_max_attempts, uns_base_backoff_s and "
                + "uns_max_backoff_s. The identical three in services/erp.py are "
                + "deliberately not merged with them: one plant's broker and one "
                + "plant's ERP have different maintenance windows, so one policy "
                + "would make one of the two wrong."),
    Curated(
        "C9", "controls", "plant",
        "How often an approved trigger reaches the running agent",
        "config.py", 499, "controls_trigger_reload_seconds: float = 30.0",
        why="One agent, one reload cadence. A plant that stops a line on an "
            "SPC signal wants five seconds for all its triggers, not for one.",
        settled=LIVE % ("controls", "trigger_reload_seconds")
                + " Read inside the session the evaluator's reload already opens, so "
                + "the docstring's promise that an approval reaches the agent "
                + "without a restart now holds for the cadence itself."),
    Curated(
        "C10", "controls", "plant",
        "Default cooldown on a new trigger",
        "config.py", 500, "controls_trigger_default_cooldown_seconds: float = 300.0",
        why="The per-trigger cooldown is already the engineer's. Only the "
            "default a new trigger inherits is hard-coded, and that is the "
            "plant's house answer.",
        settled=LIVE % ("controls", "trigger_default_cooldown_seconds")
                + " All three copies went. web/triggers.js sent `|| 0` for an empty "
                + "box, which meant a blank field asked for fire on every reading - "
                + "the one thing nobody typing nothing intends; it sends null now."),
    Curated(
        "C11", "controls", "plant",
        "Process-value history sampling ratio and floor",
        "config.py", 507, "controls_opc_history_ratio: int = 10",
        why="It is a ratio against each tag's own publish rate, so it already "
            "scales per tag. What is left is how much history the plant is "
            "willing to store, which is one answer beside tag_retention_days.",
        settled=LIVE % ("controls", "opc_history_ratio")
                + " With its floor opc_min_history_ms. Both are read when the agent "
                + "subscribes rather than on every pass, which is the honest answer "
                + "for a sampling interval an OPC server holds for the life of a "
                + "subscription - and the page and the pack key both say so."),
    Curated(
        "C12", "controls", "plant",
        "Order-code write cadence and adjustment dispatch cadence",
        "config.py", 513, "controls_opc_order_sync_seconds: float = 2.0",
        why="Agent-wide cadences against one database. Two hundred machines "
            "on one endpoint is a plant-sized problem, not a tag's.",
        settled=LIVE % ("controls", "opc_adjustment_poll_seconds")
                + " With opc_order_sync_seconds, which is the one named here. The "
                + "user-facing promise at api/routers/adjustments.py now names the "
                + "key it rests on, because a plant raising it past a few seconds "
                + "is changing what it has told its own operators."),
    Curated(
        "C13", "controls", "object",
        "Container member retry count",
        "integrations/opc/agent.py", 107, "MEMBER_RETRIES = 3",
        why="Three is a guess about how one line publishes a container and "
            "the pieces inside it; two lines in one plant can publish in "
            "different orders.",
        unsure="It could be plant: the agent applies one number to every "
               "container today, and nobody has yet seen two lines in one "
               "plant disagree. What would settle it is a plant with two "
               "packing lines from different builders."),
    Curated(
        "C14", "controls", "object",
        'Counter-reset detection - a counter that "fell below half" is read as '
        "a PLC reset",
        "integrations/opc/agent.py", 703, "value < last // 2",
        why="A counter that wraps at 65535 and a counter zeroed once a shift "
            "are two tags, not two plants, and they disagree about what "
            "'near zero' means. The controls engineer looking at that "
            "counter on that machine is the only person who knows how it "
            "behaves, so the threshold belongs on the tag, beside state_map "
            "and cycle_seconds. This is also the first concrete case for "
            "bulk editing: 'set all production counters to a small "
            "threshold' is one draft across many tags, not one click each."),

    # -------------------------------------------------------- supply chain / ERP

    Curated(
        "S1", "supply-chain", "plant",
        "ERP delivery retry policy",
        "config.py", 199, "erp_max_attempts: int = 8",
        why="One ERP per plant, one maintenance window, one retry policy.",
        settled=DONE_ERP % "max_attempts" + " With `[erp] base_backoff_s` and "
                "`[erp] max_backoff_s` beside it, as one section: they are one "
                "policy written as three numbers. `services/uns.py` keeps its "
                "own three (C8), deliberately - two systems, two outages."),
    Curated(
        "S2", "supply-chain", "plant",
        "Which ERP order statuses the MES will take",
        "config.py", 209, 'erp_open_statuses: str = "Not Started,In Process"',
        why="The site's own ERPNext customisation. It belongs beside "
            "erpnext_company, which is already the plant's.",
        settled=DONE_ERP % "open_statuses" + " A list, which needed the pack "
                "format's `ints` kind to grow a `strs` twin: a list of the "
                "ERP's own words is a list in the same sense a list of rule "
                "numbers is."),
    Curated(
        "S3", "supply-chain", "plant",
        "ERP read-back agreement tolerance",
        "config.py", 214, "erp_float_rel_tol: float = 1e-3",
        why="The comment already contains the two-plants test: float "
            "precision is 'two decimals on some sites'. One site, one "
            "precision.",
        settled=DONE_ERP % "float_rel_tol" + " With `[erp] float_abs_tol` "
                "beside it: one agreement written as two numbers. The sync "
                "worker hands both to the connector once a cycle, because a "
                "transport is given no database session on purpose."),
    Curated(
        "S4", "supply-chain", "plant",
        "ERP HTTP timeouts",
        "config.py", 218, "erp_http_timeout: float = 30.0",
        why="One link between this plant and its ERP. A bench across a VPN "
            "and one on the same switch answer differently.",
        settled=DONE_ERP % "http_timeout" + " With `[erp] rest_timeout` "
                "beside it for the plain REST connector. Applied per request "
                "rather than when the client is built, so it moves while the "
                "sync worker is running."),
    Curated(
        "S5", "supply-chain", "object",
        "Material shortage threshold - zero buffer",
        "services/staging.py", 76, "needed - already - stock",
        why="A screw with a week of lead time and a casting with three months "
            "want different buffers, in the same plant, on the same order. "
            "The buffer belongs on the material, set by the planner who buys "
            "it."),
    Curated(
        "S6", "supply-chain", "plant",
        "Default priority for an ERP order that carries none",
        "config.py", 226, "erp_default_order_priority: int = 50",
        why="Priority is already per order. Only the number an order without "
            "one inherits is hard-coded, and a plant on a 1-to-9 scale wants "
            "a different one.",
        settled=DONE_ERP % "default_order_priority" + " The contract now "
                "reports silence as silence - `ProductionRequest.priority` is "
                "null when the ERP sent none - and the MES applies this key "
                "when it imports the order, which is where the decision was "
                "always the plant's."),
    Curated(
        "S7", "supply-chain", "plant",
        "Confirmation time-agreement tolerance",
        "integrations/erp/validate.py", 50, "SECONDS_TOLERANCE = 1.0",
        why="A fact about one plant's incumbent system. incumbent.py already "
            "reads a plant-supplied tolerances object; this constant is the "
            "one that was not routed through it.",
        unsure="The 'not routed through tolerances' reading turned out to be "
               "wrong when somebody read both. `incumbent.Mapping.tolerances` "
               "is the slack between this MES and an incumbent MES's export, "
               "and `scorecard.py` already reads it; this constant is the "
               "slack between two numbers inside one document the MES itself "
               "wrote, and `fsmes erp validate` is handed no mapping. They "
               "are two comparisons, not one that was missed.",
        settled="Built 2026-09-25: it is `[erp] confirmation_seconds_"
                "tolerance`, shipping the literal that was here - and it is "
                "the one `[erp]` key with no box on the Configuration page. "
                "`fsmes erp validate` reads files and no database by design, "
                "so there is no session to read a live row through and the "
                "page says what is true: it changes when the pack is applied "
                "and the plant restarts."),

    # ------------------------------------------------------ plant administration

    Curated(
        "A1", "administration", "plant",
        "Default role a new account gets",
        "services/auth.py", 170, 'role: str = "operator"',
        why="One plant onboards everyone as viewer and grants up; another "
            "starts them on the floor. The role codes stay the product's."),
    Curated(
        "A2", "administration", "general",
        "PBKDF2 iteration count - the product's entire password policy",
        "services/auth.py", 110, "_ITERATIONS = 240_000",
        why="A password policy is the product's floor, not a plant's "
            "preference: a plant that turns it down is weaker without "
            "knowing it. One shipped number, raised by the maintainer as "
            "hardware gets faster - and worth saying out loud that it is the "
            "only password-policy number in the product."),
    Curated(
        "A3", "administration", "plant",
        "Default page size and hard ceiling for every list endpoint",
        "api/paging.py", 28, "DEFAULT_LIMIT = 50",
        why="A plant with three thousand characteristics and one with fifty "
            "want different ceilings. The envelope shape stays the "
            "product's; the number in it does not."),
    Curated(
        "A4", "administration", "plant",
        "Pending-approvals panel page size - its own number, different from A3",
        "api/routers/dashboard.py", 378, "limit: int = Query(20",
        why="How many waiting drafts fit on one plant's screen. The panel has "
            "no pager, so item 21 is simply not there."),
    Curated(
        "A5", "administration", "plant",
        "Admin screen page sizes",
        "web/admin.js", 23, "const userPageSize = 25",
        why="Three hundred employees is one plant's ordinary; twenty-five "
            "rows is one plant's answer to it."),
    Curated(
        "A6", "administration", "plant",
        "Floor dashboard page sizes and refresh clocks",
        "web/app.js", 20, "const REFRESH_MS = 2000",
        why="A plant on a thin WAN link wants a ten-second poll where a plant "
            "on a switched floor network wants two. The file's own header "
            "says it was tuned against one plant."),
    Curated(
        "A7", "administration", "plant",
        "Admin screen refresh - a third, inconsistent clock",
        "web/admin.js", 400, "setInterval(refresh, 8000)",
        why="Same answer as A6, given a third time by a different file. The "
            "plant should say it once."),
    Curated(
        "A8", "administration", "plant",
        "The fixed list of time windows, and its default",
        "config.py", 449, 'process_report_windows: str = "0.25,1,8,24,168"',
        why="The same item as P6 seen from administration: the plant's own "
            "shifts, with a literal in front of them.",
        settled=LIVE % ("process", "report_windows")
                + " The same item as P6 and built once, in Engineering's domain, by "
                + "agreement between the two executors that were working on "
                + "Administration and Engineering at the same time. "
                + "Administration's Configuration page adds no key of its own for "
                + "it; the browser reads the one key from GET /dashboard/screens."),
    Curated(
        "A9", "administration", "plant",
        "FS.allPages ceiling",
        "web/common.js", 297, "cap = 2000",
        why="Where the ceiling sits is the plant's size. It returns "
            "complete: false, so nothing lies today - but a plant with three "
            "thousand characteristics meets it every day."),
    Curated(
        "A10", "administration", "plant",
        "Maximum steps in a recorded walkthrough, and four silent truncations",
        "services/walkthroughs.py", 50, "MAX_STEPS = 60",
        why="A plant with a ninety-step changeover procedure is refused for a "
            "reason nothing outside that plant cares about, and the four "
            "truncations quietly shorten the plant's own words."),
    Curated(
        "A11", "administration", "plant",
        "Default capability a recorded walkthrough requires",
        "services/walkthroughs.py", 105, 'needs or "plant.read"',
        why="A plant that wants every recorded walkthrough gated to at least "
            "production.book says so once. Capability names stay the "
            "product's."),
    Curated(
        "A12", "administration", "plant",
        "Floor-agent round budget, session lifetime and result cap",
        "services/agent.py", 45, "MAX_ROUNDS = 12",
        why="SESSION_TTL is a session-lifetime policy exactly like "
            "token_ttl_seconds, which is already the plant's. One plant, one "
            "budget."),
    Curated(
        "A13", "administration", "general",
        '"At most four sentences" - the assistant\'s house style, stated twice',
        "services/agent.py", 109, "in at most four sentences",
        why="The assistant's voice is the product's. One plant hearing four "
            "sentences while another hears eight is the product speaking two "
            "ways to the same question, and the duplicate copy in "
            "assistant.py is the evidence that nobody owns it yet."),
    Curated(
        "A14", "administration", "general",
        "Suggestion and guide chip counts",
        "services/assistant.py", 692, "return out[:4]",
        why="How many things-to-try a screen offers is the shape of the "
            "product's own screen, and there is no plant-side reason to "
            "answer it differently. The honest fix is one number instead of "
            "the two independent copies there are now."),
    Curated(
        "A15", "administration", "general",
        "Lexical guide-match threshold, the fallback when the model is down",
        "services/assistant.py", 782, "score >= 2",
        why="A crude word-overlap heuristic inside the product. Two honest "
            "plants have no way to answer it differently, because nobody "
            "outside this repository can see what the score counts."),
    Curated(
        "A16", "administration", "plant",
        "Context truncation before the model is asked",
        "services/assistant.py", 836, "default=str)[:3000]",
        why="How much of the plant's own facts reach the answer, on the "
            "plant's own hardware. A plant running a larger local model can "
            "afford more."),
    Curated(
        "A17", "administration", "plant",
        "Local-model timeouts - six of them, all different, none shared",
        "services/assistant.py", 730, "timeout: float = 60.0",
        why="A plant on a slower GPU needs longer everywhere, and there is "
            "one GPU per plant to say it about."),
    Curated(
        "A18", "administration", "plant",
        "The work-instruction house style",
        "services/drafting.py", 30, "HOUSE_STYLE = ",
        why="A plant whose QMS mandates Scope / Hazards / Steps / Records "
            "gets the wrong shape today, and that shape is the plant's "
            "document standard. One clause is not configurable and must not "
            "become so: an operator must never be told to adjust a reading "
            "toward the middle - that is a product invariant."),
    Curated(
        "A19", "administration", "plant",
        "Rollup-staleness threshold on the AI panel",
        "services/ai_status.py", 56, "ROLLUP_STALE = timedelta(hours=40)",
        why="The comment names the single machine it was chosen for - one "
            "that is encrypted and regularly off overnight. A plant's server "
            "that never sleeps answers differently."),
    Curated(
        "A20", "administration", "general",
        "The standing GPU priority order",
        "services/ai_status.py", 46, "BUDGET = [",
        why="A fixed list describing one development machine, displayed "
            "rather than enforced. What the product shows here is the "
            "product's call, and the honest answer may be to stop showing a "
            "plant somebody else's priorities at all."),
    Curated(
        "A21", "administration", "plant",
        "Assistant log ring size and fill-retry budget",
        "web/assist.js", 59, "entries.slice(-60)",
        why="How long a walkthrough waits for a control to appear is the "
            "plant's slowest PC, which is the plant's own answer.",
        unsure="The row holds two judgments. The 60-entry log ring beside it "
               "is a product shape and would be general on its own; if this "
               "is ever built, it should be split in two."),
    Curated(
        "A22", "administration", "plant",
        "Toast durations and typing debounces, inconsistent across screens",
        "web/common.js", 235, 'node.classList.add("hidden"), 3500',
        why="How long a confirmation lingers is an accessibility answer a "
            "plant gives for its own people.",
        unsure="Before it is a plant's, it is the product's: 3500 in one file "
               "and 4000 in another is two files disagreeing, and settling "
               "on one number is a general decision that should come first."),
    Curated(
        "A23", "administration", "general",
        "Line-list cache lifetime on the floor screen",
        "web/app.js", 172, "linesLoadedAt < 60000",
        why="A browser cache lifetime for a list that changes when somebody "
            "edits masterdata. No plant has a way to know what to set it to, "
            "and nothing outside the screen reads it."),
    Curated(
        "A24", "administration", "general",
        "Downtime-code length cap",
        "services/reasons.py", 40, "[a-z0-9_]{1,39}",
        why="Forty characters or sixty-four changes nothing anybody can "
            "observe - no payload, no screen, no export is keyed on it. One "
            "shipped number is the whole answer. The character class beside "
            "it stays a protocol fact and is not a candidate."),
    Curated(
        "A25", "administration", "plant",
        "Default shift day-mask when a pack omits one",
        "config.py", 464, 'process_working_week_mask: str = "1111100"',
        why="The same answer as P9 from the pack's side: the plant's own "
            "calendar. The honest fix here may be to refuse rather than "
            "default, so a seven-day plant is never silently seeded five.",
        settled=LIVE % ("process", "working_week_mask")
                + " The same item as P9 and built once, in Engineering's domain, by "
                + "the same agreement. pack/masterdata.py fills a missing days "
                + "field in from it rather than refusing, and the reason is on P9."),

    # -------------------------------------------------------------------- IT

    Curated(
        "I1", "it", "plant",
        "Fleet health-probe timeout",
        "fleet/observe.py", 32, "TIMEOUT = 3.0",
        why="Two fleets with different link quality answer differently, and "
            "getting it wrong reports a healthy plant as unreachable. It is "
            "one answer per fleet, not per plant probed."),
    Curated(
        "I2", "it", "plant",
        "Rotating log size and backup count",
        "logging.py", 95, "maxBytes=5_000_000",
        why="Twenty-five megabytes of history per component is a retention "
            "policy, one file away from tag_retention_days, which is already "
            "the plant's."),
    Curated(
        "I3", "it", "general",
        "Retention prune cadence",
        "api/app.py", 142, "await asyncio.sleep(3600)",
        why="Hourly pruning is defensible in every plant: the window it "
            "prunes to is already the plant's answer, and how often the "
            "product sweeps to honour it is the product's business."),
    Curated(
        "I4", "it", "plant",
        "Which local model answers",
        "services/assistant.py", 33, 'MODEL = "qwen3:8b"',
        why="Which model drafts a plant's work instructions is the plant's "
            "hardware and its choice, and it is already recorded on the "
            "document as drafted_by_model, so the field's meaning does not "
            "change."),
)


def by_id(candidate_id: str) -> Curated | None:
    """The curated row with this id, or None."""
    for row in CURATED:
        if row.id == candidate_id:
            return row
    return None


def totals_by_scope() -> dict[str, int]:
    """How many curated candidates sit in each scope. Every scope is listed,
    including one with nothing in it, because a scope missing from a total is
    a scope nobody can argue with."""
    out = {scope: 0 for scope in SCOPE_TITLES}
    for row in CURATED:
        out[row.scope] += 1
    return out
