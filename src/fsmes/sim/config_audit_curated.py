"""The seventy-five candidates a person kept, and where each one's answer lives.

`config_audit.py` scans. This file remembers. The scan finds literals; a
person read all 419 of them on 2026-09-21 and kept 75, and those 75 carry two
judgments a scanner cannot make: which **domain** owns the setting, and — added
here on 2026-09-22 — what its **scope** is.

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


CURATED: tuple[Curated, ...] = (

    # ------------------------------------------------------ quality engineering

    Curated(
        "Q1", "quality", "plant",
        "Fewest readings before control limits are drawn at all",
        "services/spc.py", 45, "MIN_POINTS = 12",
        why="One plant draws limits at twelve readings and one insists on "
            "twenty-five, because it is a statement about how much data that "
            "plant's quality system is willing to trust. Inside a plant the "
            "answer is the same for every characteristic."),
    Curated(
        "Q2", "quality", "plant",
        "Which SPC rule counts as a major finding",
        "services/spc.py", 370, 'severity="major" if signal["rule"] == 1',
        why="Which rule is major is one triage policy for the whole plant. "
            "Two characteristics do not want different answers; two plants "
            "with different escalation paths do."),
    Curated(
        "Q3", "quality", "plant",
        "The non-conformance severity vocabulary itself",
        "domain/quality.py", 99, 'severity: Mapped[str] = mapped_column(String(20)',
        why="A vocabulary is the plant's own words, used the same way "
            "everywhere in it. critical/major/minor/observation at one plant "
            "and a 1-to-5 scale at another are both honest."),
    Curated(
        "Q4", "quality", "plant",
        "How far back a chart and the rules look",
        "services/spc.py", 49, "limit: int = 200)",
        why="A plant inspecting every fifteen minutes and one inspecting "
            "hourly want different histories behind one chart.",
        unsure="It could be object. A plant that samples one characteristic "
               "every unit and another once a shift might want the history "
               "per characteristic; what would settle it is whether any real "
               "plant asks for two different windows on one chart screen."),
    Curated(
        "Q5", "quality", "general",
        "Which Western Electric rules are in force",
        "services/spc.py", 84, '"a point beyond three sigma"',
        why="Whether a plant may switch a rule off at all is a question "
            "decision 0035 has not answered - section 2 of the audit argues "
            "the chart is the product's, and a chart drawn with rule 4 off "
            "and still called an SPC chart does not say what it appears to. "
            "That is a product decision, which is why it is on the short "
            "list. All four run today and that stays the shipped default."),
    Curated(
        "Q6", "quality", "plant",
        "The gauge rule of ten, and its floor of four",
        "services/gauges.py", 179, '"adequate": ratio >= 10',
        why="AIAG says 10:1 and ANSI Z540 says 4:1. A plant follows one "
            "standard, for every gauge it owns."),
    Curated(
        "Q7", "quality", "object",
        'A gauge is "due soon" thirty days out',
        "web/gauges.js", 31, "g.days_until_due <= 30",
        why="A quarterly calibration wants fourteen days of warning and an "
            "annual one wants sixty, and both gauges hang in the same "
            "calibration room. It belongs beside the gauge's own interval, "
            "set by the person who calibrates it."),
    Curated(
        "Q8", "quality", "plant",
        "Default calibration interval for a new gauge",
        "services/gauges.py", 59, "interval_days: int = 365",
        why="The per-gauge interval is already the engineer's. What is "
            "hard-coded is the plant's house default for a gauge nobody has "
            "set one on yet."),
    Curated(
        "Q9", "quality", "object",
        'Which materials are "pieces" on a pallet certificate',
        "services/coa.py", 277, 'm.startswith("UT-")',
        why="Whether a material is counted in pieces on a certificate is a "
            "fact about that material. Two materials in one plant answer "
            "differently, which is exactly why a prefix test is standing in "
            "for the flag today."),
    Curated(
        "Q10", "quality", "plant",
        "How many serials a certificate lists before truncating",
        "services/coa.py", 175, 'data["serials"][:200]',
        why="How long a certificate may be is the plant's agreement with the "
            "people who read it, and the plant prints them all the same way.",
        unsure="It could be object. If one customer demands every unit listed "
               "and another does not, the answer sits on the customer or the "
               "material; what would settle it is whether any plant runs two "
               "certificate agreements at once."),
    Curated(
        "Q11", "quality", "plant",
        "The serial number format",
        "services/serialization.py", 87, 'f"{prefix}-{number:06d}"',
        why="One label scheme per plant, read by its own scanners."),
    Curated(
        "Q12", "quality", "plant",
        "The non-conformance code format",
        "services/quality.py", 158, 'f"NC-{nc.id:05d}"',
        why="A plant that calls them NCRs calls all of them NCRs."),
    Curated(
        "Q13", "quality", "plant",
        "How deep a containment tree may go",
        "services/serialization.py", 48, "MAX_DEPTH = 6",
        why="How deep the plant's own packaging goes - piece, stack, pack, "
            "case, pallet, truck - is one answer per plant, not per shipment. "
            "It doubles as a loop guard, so the plant's key needs a ceiling "
            "over it."),

    # --------------------------------------- manufacturing / process engineering

    Curated(
        "P1", "process", "plant",
        'The maintenance "coming due" warning at 80% of an interval',
        "services/maintenance.py", 92, '"due_soon": 0.8 <= fraction',
        why="Eighty per cent is a fraction of each plan's own interval, so it "
            "already scales: a weekly greasing and a yearly overhaul do not "
            "need different fractions. What differs between plants is how "
            "much warning their spares lead time needs."),
    Curated(
        "P2", "process", "plant",
        "Fallback cycle time when a machine has no rating",
        "services/scheduling.py", 37, "DEFAULT_CYCLE_SECONDS = 3.0",
        why="The per-object answer already exists - it is the machine's own "
            "cycle rating. What is hard-coded is the guess a plant makes for "
            "a machine nobody has rated yet, and a bottling plant and a "
            "machine shop guess differently."),
    Curated(
        "P3", "process", "plant",
        "How long a maintenance job with no plan is assumed to take",
        "services/maintenance.py", 284, "o.plan.expected_minutes if o.plan else 60.0",
        why="Same shape as P2: the per-job estimate is the planner's, and the "
            "hour is the plant's fallback when there is none."),
    Curated(
        "P4", "process", "plant",
        "Default expected duration of a new maintenance plan",
        "services/maintenance.py", 227, "expected_minutes: float = 30.0",
        why="The per-plan column exists; only the house default is in code."),
    Curated(
        "P5", "process", "plant",
        'The default reporting window - eight hours, "a shift"',
        "services/analysis.py", 196, "hours: float = 8.0",
        why="Shift length. The plant already states it, in shift_patterns; "
            "this is the same fact written a second time in code."),
    Curated(
        "P6", "process", "plant",
        "The fixed list of windows every screen offers",
        "web/common.js", 484, "const WINDOWS = ",
        why="The same judgment as P5 one level up: which windows a plant's "
            "people work in. Its own shifts are the honest source."),
    Curated(
        "P7", "process", "plant",
        "How many machines a Gantt draws",
        "services/analysis.py", 440, "limit: int = 12, shift",
        why="A six-station cell and a 108-station plant want different "
            "screenfuls, and one plant wants one answer for all its screens."),
    Curated(
        "P8", "process", "plant",
        'How far back "the previous shift" may reach',
        "services/calendar.py", 52, "PREVIOUS_HORIZON_DAYS = 14",
        why="A seasonal plant with a six-week shutdown answers differently "
            "from a continuous one. Inside a plant it is one answer, and the "
            "refusal sentence it produces is the plant's own."),
    Curated(
        "P9", "process", "plant",
        "The default working week",
        "services/calendar.py", 469, 'days: str = "1111100"',
        why="The mask format is a product fact; which mask a plant starts "
            "from is the plant's calendar."),
    Curated(
        "P10", "process", "plant",
        "The floor below which no rate is reported at all",
        "services/coverage.py", 285, "MIN_OBSERVED_SECONDS = 10.0",
        why="Its sibling suppression threshold is already a pack key - "
            "[oee] coverage_floor - which is a plant-level answer, argued "
            "that way when it was made one."),
    Curated(
        "P11", "process", "plant",
        "The schedule board's default horizon",
        "services/scheduling.py", 210, "hours: float = 24.0)",
        why="A job shop planning a fortnight and a line planning a shift want "
            "different boards; each plant wants one board."),
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
        "integrations/opc/agent.py", 519, "BOOK_ATTEMPTS = 4",
        why="This is the agent arguing with its own database, not with a "
            "machine: the same contention policy applies to every tag it "
            "books. Its sibling sqlite_busy_timeout_ms is already a plant "
            "setting."),
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
        "services/uns.py", 30, "MAX_ATTEMPTS = 8",
        why="One broker per plant, one retry policy. uns_qos, uns_batch, "
            "uns_inflight and uns_poll_seconds are already plant settings; "
            "this is the piece left behind."),
    Curated(
        "C9", "controls", "plant",
        "How often an approved trigger reaches the running agent",
        "services/triggers.py", 260, "reload_seconds: float = 30.0",
        why="One agent, one reload cadence. A plant that stops a line on an "
            "SPC signal wants five seconds for all its triggers, not for one."),
    Curated(
        "C10", "controls", "plant",
        "Default cooldown on a new trigger",
        "services/triggers.py", 131, "cooldown_seconds: float = 300.0",
        why="The per-trigger cooldown is already the engineer's. Only the "
            "default a new trigger inherits is hard-coded, and that is the "
            "plant's house answer."),
    Curated(
        "C11", "controls", "plant",
        "Process-value history sampling ratio and floor",
        "integrations/opc/agent.py", 76, "HISTORY_RATIO = 10",
        why="It is a ratio against each tag's own publish rate, so it already "
            "scales per tag. What is left is how much history the plant is "
            "willing to store, which is one answer beside tag_retention_days."),
    Curated(
        "C12", "controls", "plant",
        "Order-code write cadence and adjustment dispatch cadence",
        "integrations/opc/agent.py", 67, "_ORDER_SYNC_SECONDS = 2.0",
        why="Agent-wide cadences against one database. Two hundred machines "
            "on one endpoint is a plant-sized problem, not a tag's."),
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
        "services/erp.py", 41, "MAX_ATTEMPTS = 8",
        why="One ERP per plant, one maintenance window, one retry policy."),
    Curated(
        "S2", "supply-chain", "plant",
        "Which ERP order statuses the MES will take",
        "integrations/erp/erpnext_adapter.py", 44, "_OPEN_STATUSES = (",
        why="The site's own ERPNext customisation. It belongs beside "
            "erpnext_company, which is already the plant's."),
    Curated(
        "S3", "supply-chain", "plant",
        "ERP read-back agreement tolerance",
        "integrations/erp/erpnext_adapter.py", 204, "rel_tol=1e-3, abs_tol=0.01",
        why="The comment already contains the two-plants test: float "
            "precision is 'two decimals on some sites'. One site, one "
            "precision."),
    Curated(
        "S4", "supply-chain", "plant",
        "ERP HTTP timeouts",
        "integrations/erp/erpnext_adapter.py", 97, "timeout: float = 30.0",
        why="One link between this plant and its ERP. A bench across a VPN "
            "and one on the same switch answer differently."),
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
        "integrations/erp/contract.py", 29, "priority: int = 50",
        why="Priority is already per order. Only the number an order without "
            "one inherits is hard-coded, and a plant on a 1-to-9 scale wants "
            "a different one."),
    Curated(
        "S7", "supply-chain", "plant",
        "Confirmation time-agreement tolerance",
        "integrations/erp/validate.py", 50, "SECONDS_TOLERANCE = 1.0",
        why="A fact about one plant's incumbent system. incumbent.py already "
            "reads a plant-supplied tolerances object; this constant is the "
            "one that was not routed through it."),

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
        "web/common.js", 493, "saved > 0 ? saved : 8",
        why="The same item as P6 seen from administration: the plant's own "
            "shifts, with a literal in front of them."),
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
        "pack/masterdata.py", 451, 'row.get("days", "1111100")',
        why="The same answer as P9 from the pack's side: the plant's own "
            "calendar. The honest fix here may be to refuse rather than "
            "default, so a seven-day plant is never silently seeded five."),

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
