"""The module registry: what a plant can switch off, and what switching it off takes with it.

Decision [0002](../../docs/decisions/0002-kernel-and-modules.md) said a small
kernel is always present and everything else is a module. Until this file
existed that was a sentence, not a mechanism: `api/app.py` mounted all
twenty-three routers unconditionally and `mcp_server.py` registered all ten
tool files at import, so turning a capability off was a refactor rather than
a setting.

This is the one place that says which modules exist. For each: the API routers
it mounts, the dashboard pages it serves, the MCP tool file it registers, the
settings it owns, and the tables its rows live in. The app and the MCP server
read this list and nothing else; `MES_MODULES` filters it.

**What "off" means.** Off means *not served*. A module that is off mounts no
routers (its paths answer 404), serves no pages, and registers no agent tools.
It does **not** mean *not stored*: the schema is one migration chain for every
plant (decisions 0021 and 0022), so a disabled module's tables are still
created and its existing rows are still there, untouched, and come back when
the module is switched on again. Nothing here drops a table.

**What this file may hold.** Data only. No imports of the routers, the tool
files or the settings — the registry has to be readable by a test that is
checking the layering, and by `fsmes info`, without dragging the application
in behind it. Routers and tool files are imported by name, lazily, at the
moment they are mounted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The setting that filters this registry. Its value is a comma-separated
#: list read left to right, each item one of:
#:
#:   ``all``     every module in this file
#:   ``<name>``  switch that module on
#:   ``-<name>`` switch that module off
#:
#: so ``all`` (the default) is every module on, ``all,-quality`` is everything
#: except quality, and ``quality,maintenance`` is those two and nothing else
#: optional. Kernel modules are not filtered and naming one is refused rather
#: than quietly ignored, because a plant that thinks it turned off its audit
#: trail and did not should hear about it.
SETTING = "MES_MODULES"

DEFAULT = "all"


@dataclass(frozen=True)
class Mount:
    """One API router, and how the app mounts it."""

    module: str
    """The dotted module holding the router, e.g. ``fsmes.api.routers.quality``."""

    prefix: str
    tags: tuple[str, ...]

    public: bool = False
    """True for the handful of routes that answer before anybody signs in:
    health, metrics and the login endpoint itself."""


@dataclass(frozen=True)
class Page:
    """One dashboard page: a static file served at a path, with the sentence
    that says what a person goes there for."""

    path: str
    file: str
    about: str


@dataclass(frozen=True)
class ConfigDomain:
    """One area of the plant that has things in it a person configures.

    A domain is a workspace in the navigation, not a module: *Engineering*
    holds the machines, the tags, the triggers and the downtime vocabulary,
    which come from four different modules. Scott, 2026-09-21, looking at a
    nav bar that had grown a chip for the downtime vocabulary: he does not
    want a top-level entry per configurable thing, he wants **one
    Configuration entry per domain** with that domain's configurable sections
    inside it, because there will eventually be hundreds of sections and a
    chip each is exactly the clutter this work exists to remove.

    A domain listed here is served a page at ``/dashboard/config/<slug>``.
    Domains appear as their first section does; naming one with nothing in it
    would serve a page that says nothing.
    """

    slug: str
    """The last segment of the page's path, and what a section names."""

    title: str
    """What the navigation calls this workspace - `FS.NAV`'s group name."""

    about: str
    """The sentence a person reads at the top of the page."""


@dataclass(frozen=True)
class ConfigSection:
    """One configurable thing, on one domain's Configuration page.

    This is the seam that keeps the navigation from growing. A module that
    adds something configurable adds a `ConfigSection` to its own entry in
    this registry and nothing else: the row appears on its domain's
    Configuration page, and the nav bar does not change. A section whose
    module is switched off is not listed, and the page says how many are not.

    It carries no behaviour. `href` points at the screen that already owns the
    thing, which keeps every bookmark alive and leaves each section's own
    capability gates exactly where its author put them - this registry decides
    where the door is, never who may walk through it.
    """

    domain: str
    """The `ConfigDomain` slug this section is listed under."""

    key: str
    """A stable name for the section, for tests and for anything that links
    to a row rather than to the page."""

    label: str
    """What the row is called."""

    about: str
    """One sentence: what a person changes here."""

    href: str
    """The screen that does the configuring."""

    define: str | None = None
    """The capability that drafts a change here, if there is one. Shown so a
    person can see who this section belongs to; the gate itself lives on the
    section's own screen and its own endpoints."""

    approve: str | None = None
    """The capability that puts a change in force, if this section has an
    approval step. `None` means it takes effect when it is saved."""

    pack_keys: tuple[str, ...] = ()
    """The `plant.toml` keys this section is, where it is keys rather than a
    list somebody edits on a screen - `("[quality] hold_rules",)`.

    Named because a pack key is the one name for a setting that every part of
    this product already agrees on: the pack schema declares it, `fsmes pack
    check` validates it by it, and the Configuration page prints it.

    A tuple rather than one name because some judgments are one decision
    written as two numbers - where a process stops being capable and where it
    stops being marginal, the gauge rule of ten and its floor - and splitting
    those into two rows would make the list longer without making it clearer,
    while naming only one of them would be a half-truth on the screen a person
    reads to find out what their plant is set to.
    """

    edit_here: bool = False
    """Whether this section's `pack_keys` are **edited on the Configuration
    page itself**, rather than only read there.

    This is the one flag a plant-scope section sets to become live. True means
    the database owns these keys - seeded from the pack when the plant was
    built, written afterwards by somebody holding `define`, in force at once -
    so the page renders an input and `PATCH /dashboard/config/{domain}/settings/{key}`
    accepts a new value. False means the keys are read at start-up and nothing
    on a screen can change them, which is what every one of these was until
    2026-09-24 and is still the honest answer for a setting that genuinely
    cannot move while a plant is running.

    `docs/design/config-assistance.md` §11 is the whole of what a future
    section has to do: one flag here, and `define` naming the capability that
    may write it. No table of its own, no endpoint of its own, no `KINDS`
    entry - rule three of decision 0035 is explicit that a number taking
    effect when it is saved has no pending state to review.
    """


#: Every configuration domain, in nav order. One today. A second is one entry
#: here plus at least one `ConfigSection` naming it, and `test_web.py` refuses
#: a domain with a page and no sections, or sections and no nav entry.
CONFIG_DOMAINS: tuple[ConfigDomain, ...] = (
    ConfigDomain(
        "engineering", "Engineering",
        "What the plant's own screens are configured with: the words its "
        "records are written in, and the settings behind them. Each section "
        "keeps its own rules about who may draft and who may sign."),
    ConfigDomain(
        "quality", "Quality",
        "What this plant's quality records are written in, and the numbers "
        "its charts and certificates are judged against. The words are the "
        "plant's; the arithmetic behind them is the product's."),
    ConfigDomain(
        "supply_chain", "Supply chain",
        "What this plant asks of the link between itself and an ERP it does "
        "not own: how long it keeps trying to deliver a confirmation, which "
        "order statuses it will take an order in, how long it waits, and "
        "when a number read back is the number sent. The contract itself is "
        "the product's; what this plant asks of the link is the plant's."),
)

DOMAIN_BY_SLUG: dict[str, ConfigDomain] = {d.slug: d for d in CONFIG_DOMAINS}


@dataclass(frozen=True)
class Module:
    name: str
    title: str

    kernel: bool = False
    """Always present. `MES_MODULES` may not switch it off - the kernel is
    master data, routings, orders, dispatch, execution, audit and auth, and a
    plant without them is not an MES."""

    routers: tuple[Mount, ...] = ()
    pages: tuple[Page, ...] = ()

    config_sections: tuple[ConfigSection, ...] = ()
    """What this module puts on a domain's Configuration page. Empty for all
    but one module today; this is where the next configurable thing goes so
    that it needs no new navigation entry."""

    tools: tuple[str, ...] = ()
    """Dotted MCP tool modules, e.g. ``fsmes.mcp.quality``. Each exposes
    ``register(mcp, call, write, identify)`` and hands its tools back."""

    settings: tuple[str, ...] = ()
    """`MES_*` setting prefixes this module owns, without the `MES_`. Most
    modules own none: the settings in `config.py` are overwhelmingly about the
    plant's edges (OPC, inbound, UNS) rather than about a module."""

    tables: tuple[str, ...] = field(default_factory=tuple)
    """The tables this module's rows live in. Recorded, never acted on:
    switching the module off leaves every one of them in place. Listing them
    is how the docs' claim that off means not-served rather than not-stored
    stays checkable."""


#: Every module, in the order the app mounts them. Nine are the kernel and
#: fourteen are optional; the totals are asserted below so this comment cannot
#: drift away from the list.
REGISTRY: tuple[Module, ...] = (
    # ----------------------------------------------------------- the kernel
    Module(
        name="system",
        title="Health, metrics and version",
        kernel=True,
        routers=(Mount("fsmes.api.routers.system", "", ("system",), public=True),),
    ),
    Module(
        name="auth",
        title="Sign-in and sessions",
        kernel=True,
        routers=(Mount("fsmes.api.routers.auth", "/auth", ("auth",), public=True),),
    ),
    Module(
        name="admin",
        title="People, roles and capabilities",
        kernel=True,
        routers=(Mount("fsmes.api.routers.admin", "/admin", ("administration",)),),
        pages=(Page("/dashboard/admin", "admin.html",
                    "People, roles and routings. The screen gates itself on the "
                    "users.manage capability, as the API does."),),
        tables=("roles", "personnel"),
    ),
    Module(
        name="ops",
        title="What is running, and the audit trail",
        kernel=True,
        routers=(Mount("fsmes.api.routers.ops", "/ops", ("operations",)),),
        pages=(Page("/dashboard/ops", "ops.html",
                    "What is running, what it has been saying, and who did what."),),
        tables=("audit_log", "idempotency_keys"),
    ),
    Module(
        name="masterdata",
        title="Equipment, materials, bills of material and routings",
        kernel=True,
        routers=(Mount("fsmes.api.routers.masterdata", "/masterdata", ("master data",)),),
        pages=(Page("/dashboard/masterdata", "masterdata.html",
                    "Equipment with cost centers, materials and bills of material, "
                    "specifications, people."),),
        tools=("fsmes.mcp.masterdata",),
        tables=("materials", "bom_items", "routings", "routing_operations"),
    ),
    Module(
        name="workorders",
        title="Work orders",
        kernel=True,
        routers=(Mount("fsmes.api.routers.workorders", "/workorders", ("work orders",)),),
        pages=(Page("/dashboard/orders", "orders.html",
                    "Where an order is, what each step yielded, and what went into it."),),
        tables=("work_orders", "work_order_operations"),
    ),
    Module(
        name="execution",
        title="Execution, material lots and genealogy",
        kernel=True,
        routers=(Mount("fsmes.api.routers.execution", "/execution", ("execution",)),),
        tables=("material_lots", "lot_consumptions", "production_logs",
                "inbound_events", "inbound_watermarks"),
    ),
    Module(
        name="equipment",
        title="Machines, their states and their tags",
        kernel=True,
        # Order matters. `routers.equipment` ends with `/{code}`, which matches
        # any single segment, so the vocabulary's routes are mounted first or
        # `/equipment/downtime-reasons` is read as a machine by that name.
        routers=(Mount("fsmes.api.routers.reasons", "/equipment", ("equipment",)),
                 Mount("fsmes.api.routers.equipment", "/equipment", ("equipment",))),
        pages=(
            Page("/dashboard/station", "station.html",
                 "One machine, arm's length: the line-side operator's screen."),
            Page("/dashboard/machines", "machines.html",
                 "Engineering: the plant as a tree, with cost centers and alarms."),
            Page("/dashboard/reasons", "reasons.html",
                 "Engineering: the plant's downtime vocabulary - what is on the "
                 "list, what is drafted, what was retired, and the form that "
                 "drafts the next word."),
            Page("/dashboard/tags", "tags.html",
                 "Engineering: every tag on every machine, and whether each machine "
                 "is still talking."),
            Page("/dashboard/machine/{code}", "machine.html",
                 "One machine: every tag it publishes, trends, timeline, OEE, "
                 "maintenance, and what is queued on it. The script reads the code "
                 "from the URL; the page is the same file for every machine."),
        ),
        config_sections=(
            ConfigSection(
                domain="engineering",
                key="downtime_reasons",
                label="Downtime reasons",
                about="The words an operator picks from when a machine stops, and "
                      "that the pareto groups on. Drafted here, put in force on the "
                      "Floor screen's Waiting for you panel.",
                href="/dashboard/reasons",
                define="process.define",
                approve="process.approve"),
            ConfigSection(
                domain="engineering",
                key="opc_book_retry",
                label="How hard the agent tries to book what a machine said",
                about="How many times the OPC agent retries booking a batch of "
                      "readings, and the first wait between attempts, doubling "
                      "each time. Readings a machine sent are not dropped "
                      "without trying: house rule one, read the other way round. "
                      "Its sibling `sqlite_busy_timeout_ms` has been a setting "
                      "for months; this was the other half of the same argument.",
                href="/dashboard/tags",
                define="signals.define",
                edit_here=True,
                pack_keys=("[controls] opc_book_attempts",
                           "[controls] opc_book_backoff_s")),
            ConfigSection(
                domain="engineering",
                key="uns_retry_policy",
                label="How long the namespace keeps trying",
                about="How many attempts a publication gets before it is "
                      "recorded dead, the first wait, and the ceiling the "
                      "doubling stops at. A broker restarted nightly for twenty "
                      "minutes kills every queued event at eight attempts. Dead "
                      "is never deleted, so what this changes is how long the "
                      "plant keeps trying before a person has to decide. The ERP "
                      "outbox holds the same three numbers and they are "
                      "deliberately separate: one plant's broker and one plant's "
                      "ERP have different maintenance windows.",
                href="/dashboard/ops",
                define="signals.define",
                edit_here=True,
                pack_keys=("[controls] uns_max_attempts",
                           "[controls] uns_base_backoff_s",
                           "[controls] uns_max_backoff_s")),
            ConfigSection(
                domain="engineering",
                key="opc_history_sampling",
                label="How densely tag history is sampled",
                about="How much slower the rest of a machine's tags are sampled "
                      "than the ones the MES reasons about, as a multiple of the "
                      "publish interval, and the floor under that. A ratio, so "
                      "it already scales with each plant's publish rate; what is "
                      "left to answer is how much history to store. These two "
                      "take effect when the agent next subscribes, which is what "
                      "a subscription interval a server holds can honestly be.",
                href="/dashboard/tags",
                define="signals.define",
                edit_here=True,
                pack_keys=("[controls] opc_history_ratio",
                           "[controls] opc_min_history_ms")),
            ConfigSection(
                domain="engineering",
                key="opc_agent_cadences",
                label="How often the agent looks for work",
                about="How often the agent checks whether a machine's order code "
                      "has changed, and how often it looks for approved setpoint "
                      "adjustments to write. Two hundred machines on one endpoint "
                      "is a hundred database reads a second to discover nothing "
                      "changed. Approving an adjustment promises a person that "
                      "the agent writes within seconds, and the second of these "
                      "is the number that promise rests on.",
                href="/dashboard/adjustments",
                define="signals.define",
                edit_here=True,
                pack_keys=("[controls] opc_order_sync_seconds",
                           "[controls] opc_adjustment_poll_seconds")),
        ),
        # Two tool files, for the same reason there are two routers: the
        # vocabulary is its own thing, read and drafted on its own screen.
        tools=("fsmes.mcp.equipment", "fsmes.mcp.reasons"),
        tables=("equipment", "equipment_connections", "equipment_states", "tag_values",
                "uns_publications", "downtime_reasons"),
    ),
    Module(
        name="dashboard",
        title="The plant floor front door",
        kernel=True,
        routers=(Mount("fsmes.api.routers.dashboard", "/dashboard", ("dashboard",)),),
        # One file serves every domain's Configuration page and reads the
        # domain out of its own URL, the way machine.html reads a machine
        # code. The paths are written out rather than templated so that a
        # crawl, a link check and this registry all see real addresses.
        pages=(Page("/dashboard", "index.html", "The plant at a glance."),
               *(Page(f"/dashboard/config/{domain.slug}", "config.html",
                      f"{domain.title}: everything configurable in this workspace, "
                      "in one place, so a new setting needs no new nav entry.")
                 for domain in CONFIG_DOMAINS)),
        # The settings a plant owns, for every domain at once. It belongs here
        # rather than to Quality because the table is the Configuration page's
        # own, keyed by pack section and key, and the next domain's live
        # section writes rows into this same table. Here also means always
        # present: a plant that switches Quality off keeps the numbers it had
        # chosen, and they are what it reads again when Quality comes back.
        tables=("plant_settings",),
        # Two tools for every domain's settings, for the same reason the table
        # is here: one endpoint writes them all and reads the owning section
        # per key, so a domain that becomes live needs no tool of its own.
        tools=("fsmes.mcp.settings",),
    ),

    # -------------------------------------------------------- the modules
    Module(
        name="assist",
        title="The in-screen assistant",
        routers=(Mount("fsmes.api.routers.assist", "/assist", ("assistant",)),),
    ),
    Module(
        name="documents",
        title="Controlled work instructions",
        routers=(Mount("fsmes.api.routers.documents", "/documents", ("work instructions",)),),
        pages=(Page("/dashboard/instructions", "instructions.html",
                    "Controlled work instructions, and the revision in force."),),
        tables=("documents",),
    ),
    Module(
        name="design",
        title="The design partner",
        routers=(Mount("fsmes.api.routers.design", "/design", ("design partner",)),),
    ),
    Module(
        name="maintenance",
        title="Maintenance plans and work",
        routers=(Mount("fsmes.api.routers.maintenance", "/maintenance", ("maintenance",)),),
        pages=(Page("/dashboard/maintenance", "maintenance.html",
                    "What has come due on use, what is open and what clearing it costs, "
                    "the plans, and the work that was done."),),
        config_sections=(
            ConfigSection(
                domain="engineering",
                key="maintenance_due_soon",
                label="When a plan is coming due",
                about="How far through a plan's own interval counts as a warning "
                      "rather than a surprise. A fraction, so it scales from a "
                      "weekly filter change to an annual overhaul; what differs "
                      "between plants is how long a spare takes to arrive.",
                href="/dashboard/maintenance",
                define="process.define",
                edit_here=True,
                pack_keys=("[process] maintenance_due_soon_fraction",)),
            ConfigSection(
                domain="engineering",
                key="default_job_minutes",
                label="How long a job with no plan is assumed to take",
                about="Every corrective job. It sizes the backlog's downtime "
                      "figure and the block the scheduler reserves, so a "
                      "supervisor deciding whether tonight is the night is "
                      "reading it.",
                href="/dashboard/maintenance",
                define="process.define",
                edit_here=True,
                pack_keys=("[process] default_job_minutes",)),
            ConfigSection(
                domain="engineering",
                key="maintenance_plan_default_minutes",
                label="A new plan's expected duration",
                about="The house default a new plan inherits. Each plan's own "
                      "figure is the engineer's and is unaffected.",
                href="/dashboard/maintenance",
                define="process.define",
                edit_here=True,
                pack_keys=("[process] maintenance_plan_default_minutes",)),
        ),
        tools=("fsmes.mcp.maintenance",),
        tables=("maintenance_plans", "maintenance_orders"),
    ),
    Module(
        name="scheduling",
        title="The schedule and the working calendar",
        routers=(Mount("fsmes.api.routers.scheduling", "/scheduling", ("scheduling",)),),
        pages=(Page("/dashboard/schedule", "schedule.html",
                    "The board, what the plan promises each order, and the calendar "
                    "every promise rests on."),),
        config_sections=(
            ConfigSection(
                domain="engineering",
                key="default_cycle_seconds",
                label="Cycle time for a machine with no rating",
                about="What one unit is assumed to cost at a station that has "
                      "not been commissioned. A machine's own rated cycle time "
                      "always wins, and a schedule built on this fallback says "
                      "so rather than hiding it. A filling line and a CNC cell "
                      "want different guesses.",
                href="/dashboard/schedule",
                define="process.define",
                edit_here=True,
                pack_keys=("[process] default_cycle_seconds",)),
            ConfigSection(
                domain="engineering",
                key="schedule_default_horizon_hours",
                label="How far ahead the board looks",
                about="A job shop planning a fortnight and a line planning a "
                      "shift want different boards. This is what the board "
                      "opens on; a person can still pick another window on it.",
                href="/dashboard/schedule",
                define="process.define",
                edit_here=True,
                pack_keys=("[process] schedule_default_horizon_hours",)),
            ConfigSection(
                domain="engineering",
                key="previous_shift_horizon_days",
                label="How far back the previous shift may reach",
                about="Two weeks covers a plant that ran nothing over a "
                      "shutdown. A seasonal plant with a six-week one answers "
                      "differently, and the refusal a screen shows quotes "
                      "whatever this says.",
                href="/dashboard/schedule",
                define="process.define",
                edit_here=True,
                pack_keys=("[process] previous_shift_horizon_days",)),
            ConfigSection(
                domain="engineering",
                key="working_week_mask",
                label="The default working week",
                about="The days a shift pattern works when it names none: seven "
                      "flags, Monday first. The format is the product's; which "
                      "mask is the default is this plant's, because Sunday to "
                      "Thursday is a real working week. Every pattern that names "
                      "its own days is unaffected.",
                href="/dashboard/schedule",
                define="process.define",
                edit_here=True,
                pack_keys=("[process] working_week_mask",)),
        ),
        tools=("fsmes.mcp.scheduling",),
        tables=("scheduled_slots", "shift_patterns", "calendar_exceptions"),
    ),
    Module(
        name="serialization",
        title="Serialised traceability",
        routers=(Mount("fsmes.api.routers.serialization", "/trace", ("traceability",)),),
        pages=(Page("/dashboard/trace", "trace.html",
                    "One serial (what is inside it, what went into it) or one lot "
                    "(where it went, as packages a warehouse can pull)."),),
        tools=("fsmes.mcp.serialization",),
        tables=("serial_units", "unit_inspections", "serial_sequences", "unit_components"),
    ),
    Module(
        name="quality",
        title="Specifications, checks, non-conformances and gauges",
        # The severities are mounted first, for the reason the downtime
        # vocabulary is mounted before `routers.equipment`: a router that ends
        # with `/{code}` reads any single segment as one of its own. This one
        # has no such route today, and the ordering is here so that the day it
        # grows one, nothing has to be found out the hard way.
        routers=(Mount("fsmes.api.routers.severities", "/quality", ("quality",)),
                 Mount("fsmes.api.routers.quality", "/quality", ("quality",))),
        pages=(
            Page("/dashboard/quality", "quality.html",
                 "Inspection history against the specification that judged it."),
            Page("/dashboard/spc", "spc.html",
                 "The individuals control chart: is the process stable, is it "
                 "capable, and which are two different questions."),
            Page("/dashboard/gauges", "gauges.html",
                 "The gauge register, calibration, and what a failed one invalidated."),
            Page("/dashboard/severities", "severities.html",
                 "Quality: the plant's own words for how bad a finding is - what "
                 "is on the list, what is drafted, what was retired, and the form "
                 "that drafts the next word."),
        ),
        config_sections=(
            ConfigSection(
                domain="quality",
                key="nc_severities",
                label="Non-conformance severities",
                about="The words a non-conformance is graded with, on the record "
                      "and on the certificate. Drafted here, put in force on the "
                      "Floor screen's Waiting for you panel.",
                href="/dashboard/severities",
                define="quality.define",
                approve="quality.approve"),
            ConfigSection(
                domain="quality",
                key="spc_hold_rules",
                label="Which SPC rules raise a hold",
                about="The chart draws and records all four Western Electric "
                      "rules on every plant. Which of them open a non-conformance "
                      "is this plant's, and defaults to all four (decision 0036).",
                href="/dashboard/spc",
                define="quality.define",
                edit_here=True,
                pack_keys=("[quality] hold_rules",)),
            ConfigSection(
                domain="quality",
                key="spc_major_rules",
                label="Which rules are a major finding",
                about="Which of those rules open a major non-conformance rather "
                      "than a minor one. Rule 1 alone by default. The two words "
                      "come from this plant's own severity list.",
                href="/dashboard/spc",
                define="quality.define",
                edit_here=True,
                pack_keys=("[quality] major_rules",)),
            ConfigSection(
                domain="quality",
                key="cpk_bars",
                label="Where a process is called capable",
                about="The Cpk at or above which this plant says capable, and the "
                      "one below it for marginal. Only the English word moves - "
                      "the Cpk itself is arithmetic and means the same everywhere.",
                href="/dashboard/spc",
                define="quality.define",
                edit_here=True,
                pack_keys=("[quality] cpk_capable", "[quality] cpk_marginal")),
            ConfigSection(
                domain="quality",
                key="spc_min_points",
                label="Fewest readings behind a control limit",
                about="Below this the limits move so much with each new reading "
                      "that they mislead more than they inform. Twelve by default, "
                      "and the pallet certificate prints whatever this says.",
                href="/dashboard/spc",
                define="quality.define",
                edit_here=True,
                pack_keys=("[quality] spc_min_points",)),
            ConfigSection(
                domain="quality",
                key="spc_history",
                label="How far back a chart looks",
                about="A plant inspecting every fifteen minutes and one inspecting "
                      "hourly want different histories behind one chart.",
                href="/dashboard/spc",
                define="quality.define",
                edit_here=True,
                pack_keys=("[quality] spc_history",)),
            ConfigSection(
                domain="quality",
                key="gauge_ratio",
                label="When a gauge can judge a tolerance",
                about="The rule of ten and its floor of four. AIAG says 10:1 and "
                      "ANSI Z540 says 4:1; a plant follows one standard for every "
                      "gauge it owns.",
                href="/dashboard/gauges",
                define="quality.define",
                edit_here=True,
                pack_keys=("[quality] gauge_ratio_adequate", "[quality] gauge_ratio_floor")),
            ConfigSection(
                domain="quality",
                key="gauge_default_interval",
                label="A new gauge's calibration interval",
                about="The house default for a gauge nobody gives one. Each "
                      "gauge's own interval is the engineer's and is unaffected, "
                      "as is how much warning each gauge wants.",
                href="/dashboard/gauges",
                define="quality.define",
                edit_here=True,
                pack_keys=("[quality] gauge_default_interval_days",)),
            ConfigSection(
                domain="quality",
                key="coa_serials_listed",
                label="Serials printed on a certificate",
                about="How many serial numbers a pallet certificate lists before "
                      "it says how many more there are. The plant's agreement "
                      "with whoever reads the certificate.",
                href="/dashboard/coa",
                define="quality.define",
                edit_here=True,
                pack_keys=("[quality] coa_serials_listed",)),
            ConfigSection(
                domain="quality",
                key="serial_digits",
                label="How a serial number is numbered",
                about="How many digits a generated serial carries after its "
                      "prefix. The hyphen between them stays the product's: the "
                      "scan that recovers a counter reads PREFIX-digits.",
                href="/dashboard/trace",
                define="quality.define",
                edit_here=True,
                pack_keys=("[quality] serial_digits",)),
            ConfigSection(
                domain="quality",
                key="nc_code_prefix",
                label="What a non-conformance is called",
                about="`NC-00017` by default. A plant that calls them NCRs calls "
                      "all of them NCRs; the number's width stays the product's.",
                href="/dashboard/quality",
                define="quality.define",
                edit_here=True,
                pack_keys=("[quality] nc_code_prefix",)),
            ConfigSection(
                domain="quality",
                key="containment_max_depth",
                label="How deep the packaging goes",
                about="Piece, stack, pack, pallet, truck is five. It is also the "
                      "guard that stops a containment walk running away, so the "
                      "product keeps a hard ceiling of twelve above it.",
                href="/dashboard/trace",
                define="quality.define",
                edit_here=True,
                pack_keys=("[quality] containment_max_depth",)),
        ),
        tools=("fsmes.mcp.quality", "fsmes.mcp.severities"),
        tables=("quality_specs", "quality_checks", "non_conformances",
                "spc_signals", "gauges", "calibrations", "nc_severities"),
    ),
    Module(
        name="kpis",
        title="OEE and order KPIs",
        routers=(Mount("fsmes.api.routers.kpis", "/kpis", ("kpis",)),),
        config_sections=(
            ConfigSection(
                domain="engineering",
                key="min_observed_seconds",
                label="The floor below which no rate is reported",
                about="Below this much observed time, nothing is divided by it: "
                      "the answer is unknown with the ledger saying why, rather "
                      "than a figure measured over four seconds. Its sibling "
                      "`[oee] coverage_floor` has been a plant's to set for "
                      "months, and by the same argument so is this. It is an "
                      "`[oee]` key listed here because a Configuration workspace "
                      "is a place on a screen and a pack section is a table in a "
                      "file, and those two do not have to share a name.",
                href="/dashboard",
                define="process.define",
                edit_here=True,
                pack_keys=("[oee] min_observed_seconds",)),
        ),
    ),
    Module(
        name="line",
        title="The line view",
        routers=(Mount("fsmes.api.routers.line", "/line", ("line view",)),),
        pages=(
            Page("/dashboard/line", "line.html",
                 "One line: machine health, work in progress between stations, the "
                 "state timeline, and the 3D view as a tab."),
            Page("/dashboard/line/3d", "line3d.html",
                 "The 3D line view. Its own page, so nothing else pays for a WebGL "
                 "scene it does not draw; the Line page embeds it as a tab."),
        ),
    ),
    Module(
        name="analysis",
        title="Shift analysis",
        routers=(Mount("fsmes.api.routers.analysis", "/analysis", ("analysis",)),),
        pages=(Page("/dashboard/analysis", "analysis.html",
                    "Shift analysis: OEE losses, the state timeline, downtime pareto and "
                    "tag trends. Its own page because these are questions you sit down with, "
                    "not things you watch."),),
        config_sections=(
            ConfigSection(
                domain="engineering",
                key="default_report_hours",
                label="How long a window is when nobody says",
                about="The source called eight hours *a shift*. A plant working "
                      "twelve-hour shifts answers 12, and every payload still "
                      "states the hours it was actually given, so nothing "
                      "downstream reads this as a fact about a window.",
                href="/dashboard/analysis",
                define="process.define",
                edit_here=True,
                pack_keys=("[process] default_report_hours",)),
            ConfigSection(
                domain="engineering",
                key="report_windows",
                label="The windows every screen offers",
                about="The list behind every time picker in the product, in "
                      "hours. A plant whose people work twelve-hour shifts and "
                      "think in weeks offers a different five. A viewer who has "
                      "chosen a window of their own keeps it whatever this says.",
                href="/dashboard/analysis",
                define="process.define",
                edit_here=True,
                pack_keys=("[process] report_windows",)),
            ConfigSection(
                domain="engineering",
                key="gantt_screenful",
                label="How many machines a Gantt draws",
                about="A six-station cell and a 108-station plant want different "
                      "screenfuls. The payload states how many machines it drew "
                      "beside how many the plant has, so moving this hides "
                      "nothing.",
                href="/dashboard/analysis",
                define="process.define",
                edit_here=True,
                pack_keys=("[process] gantt_screenful",)),
        ),
    ),
    Module(
        name="erp",
        title="The ERP connector",
        routers=(Mount("fsmes.api.routers.erp", "/erp", ("erp",)),),
        # Supply chain's Configuration page, and the first domain built from
        # nothing since #92 made a domain possible. Every section here is a
        # `[erp]` pack key, and five of the six are live: `edit_here` plus
        # `erp.define` is the whole opt-in, per config-assistance.md §11.
        #
        # There is no ERP screen in this product, so `href` points at the
        # screen where each value's *effect* is visible - the outbox on Ops,
        # the order book on Orders - which is what `href` has always meant.
        config_sections=(
            ConfigSection(
                domain="supply_chain",
                key="erp_retries",
                label="ERP delivery retries",
                about="How many times a confirmation is offered to the ERP "
                      "before it is dead and a person decides, and how long "
                      "this plant waits between attempts. An ERP with a "
                      "four-hour maintenance window is why the ceiling moves.",
                href="/dashboard/ops",
                define="erp.define",
                edit_here=True,
                pack_keys=("[erp] max_attempts", "[erp] base_backoff_s",
                           "[erp] max_backoff_s")),
            ConfigSection(
                domain="supply_chain",
                key="erp_open_statuses",
                label="Which ERP statuses are open",
                about="The statuses this plant's ERP puts an order in while "
                      "it is waiting to be made. The words are the ERP's and "
                      "sites customise them; reading them as an order-release "
                      "policy is what makes the list this plant's.",
                href="/dashboard/orders",
                define="erp.define",
                edit_here=True,
                pack_keys=("[erp] open_statuses",)),
            ConfigSection(
                domain="supply_chain",
                key="erp_readback",
                label="When a number read back is the number sent",
                about="Two numbers, because it is one judgment: how far a "
                      "value the ERP hands back may differ as a fraction, and "
                      "how far it may differ outright. A site rounding to two "
                      "decimals and one rounding to four disagree about this.",
                href="/dashboard/ops",
                define="erp.define",
                edit_here=True,
                pack_keys=("[erp] float_rel_tol", "[erp] float_abs_tol")),
            ConfigSection(
                domain="supply_chain",
                key="erp_timeouts",
                label="How long this plant waits on its ERP",
                about="One request to a system this plant does not own, in "
                      "seconds - the ERPNext connector and the plain REST "
                      "one. A bench across a VPN posting a large bill of "
                      "material exceeds thirty seconds routinely, and every "
                      "one of those burns an attempt.",
                href="/dashboard/ops",
                define="erp.define",
                edit_here=True,
                pack_keys=("[erp] http_timeout", "[erp] rest_timeout")),
            ConfigSection(
                domain="supply_chain",
                key="erp_default_priority",
                label="Priority an ERP order inherits",
                about="What an order arriving with no priority is given - "
                      "lower is more urgent. ERPNext Work Order has no "
                      "priority field at all, so on this plant it is always "
                      "this number.",
                href="/dashboard/orders",
                define="erp.define",
                edit_here=True,
                pack_keys=("[erp] default_order_priority",)),
            # The one that is not live, and the first section in this product
            # to answer that way. `fsmes erp validate` reads a folder of files
            # and no database - that is its contract, and a plant's ERP team
            # runs it on a laptop that has never had an MES database on it -
            # so there is no session to read a live row through and a box
            # saying *in force when you save it* would have been false.
            ConfigSection(
                domain="supply_chain",
                key="erp_confirmation_tolerance",
                label="Confirmation time agreement",
                about="How far machine_seconds in a confirmation file may "
                      "differ from the time the step was open before the "
                      "fsmes erp validate command calls the document wrong. "
                      "Read at start-up, because that command reads a folder "
                      "of files and no database.",
                href="/dashboard/ops",
                pack_keys=("[erp] confirmation_seconds_tolerance",)),
        ),
        tools=("fsmes.mcp.erp",),
        settings=("erp_", "erpnext_"),
        tables=("erp_messages",),
    ),
    Module(
        name="triggers",
        title="Triggers: what the plant does when a signal crosses a line",
        routers=(Mount("fsmes.api.routers.triggers", "/triggers", ("triggers",)),),
        pages=(Page("/dashboard/triggers", "triggers.html",
                    "Engineering: what the plant does when a signal crosses a line - "
                    "drafted, approved, withdrawn, and every firing."),),
        config_sections=(
            ConfigSection(
                domain="engineering",
                key="trigger_reload_seconds",
                label="How fast an approval reaches the agent",
                about="A trigger approved on screen reaches the running agent "
                      "without a restart; this is how fast. A plant that stops a "
                      "line on an SPC signal wants five seconds, and one with "
                      "two hundred machines on one endpoint may not.",
                href="/dashboard/triggers",
                define="signals.define",
                edit_here=True,
                pack_keys=("[controls] trigger_reload_seconds",)),
            ConfigSection(
                domain="engineering",
                key="trigger_default_cooldown_seconds",
                label="A new trigger's inherited cooldown",
                about="How long a newly drafted trigger stays quiet after firing "
                      "when nobody says otherwise. Five minutes of silence is "
                      "right on a continuous line and wrong on a station with "
                      "forty-second cycles. Each trigger's own cooldown is the "
                      "engineer's and is unaffected.",
                href="/dashboard/triggers",
                define="signals.define",
                edit_here=True,
                pack_keys=("[controls] trigger_default_cooldown_seconds",)),
        ),
        tools=("fsmes.mcp.triggers",),
        tables=("triggers", "trigger_firings"),
    ),
    Module(
        name="adjustments",
        title="The recommendation queue",
        routers=(Mount("fsmes.api.routers.adjustments", "/adjustments", ("adjustments",)),),
        pages=(Page("/dashboard/adjustments", "adjustments.html",
                    "Engineering: the recommendation queue - the only path to a PLC."),),
        tools=("fsmes.mcp.adjustments",),
        tables=("recommended_adjustments",),
    ),
    Module(
        name="coa",
        title="Certificates of analysis",
        routers=(Mount("fsmes.api.routers.coa", "/coa", ("certificates",)),),
        pages=(Page("/dashboard/coa", "coa.html",
                    "Certificates of analysis: readable, printable, immutable."),),
        tools=("fsmes.mcp.coa",),
    ),
)

BY_NAME: dict[str, Module] = {m.name: m for m in REGISTRY}

#: Every module name, every kernel name, every name a plant may switch.
ALL_NAMES: tuple[str, ...] = tuple(m.name for m in REGISTRY)
KERNEL_NAMES: frozenset[str] = frozenset(m.name for m in REGISTRY if m.kernel)
OPTIONAL_NAMES: tuple[str, ...] = tuple(m.name for m in REGISTRY if not m.kernel)


class UnknownModule(ValueError):
    """`MES_MODULES` named something this version does not have.

    Refused rather than ignored: a typo that silently changes nothing is how a
    plant comes to believe it disabled a module it is still serving.
    """


def resolve(spec: str | None = None) -> frozenset[str]:
    """The set of modules that are on, from a `MES_MODULES` value.

    Read left to right so later items win: `all,-quality` is everything but
    quality, and `all,-quality,quality` is everything. An empty or missing
    value means the default, which is everything - nothing changes for a plant
    that has never heard of this setting.
    """
    text = (spec if spec is not None else DEFAULT).strip()
    if not text:
        text = DEFAULT

    on: set[str] = set(KERNEL_NAMES)
    for raw in text.split(","):
        item = raw.strip()
        if not item:
            continue
        if item == "all":
            on |= set(ALL_NAMES)
            continue
        off = item.startswith("-")
        name = item[1:].strip() if off else item
        if name not in BY_NAME:
            raise UnknownModule(
                f"{SETTING} names {name!r}, which is not a module in this version. "
                f"The {len(OPTIONAL_NAMES)} modules a plant may switch are: "
                f"{', '.join(OPTIONAL_NAMES)}.")
        if name in KERNEL_NAMES:
            raise UnknownModule(
                f"{SETTING} names {name!r}, which is part of the kernel and is always "
                "present. The kernel is master data, routings, orders, dispatch, "
                f"execution, audit and auth: {', '.join(sorted(KERNEL_NAMES))}.")
        if off:
            on.discard(name)
        else:
            on.add(name)
    return frozenset(on)


def enabled(spec: str | None = None) -> tuple[Module, ...]:
    """The modules that are on, in registry order."""
    names = resolve(spec)
    return tuple(m for m in REGISTRY if m.name in names)


def disabled(spec: str | None = None) -> tuple[Module, ...]:
    """The modules that are off, in registry order. The other half of the
    answer, so anything reporting the state can state its total."""
    names = resolve(spec)
    return tuple(m for m in REGISTRY if m.name not in names)


def config_sections(domain: str, served: tuple[Module, ...] | None = None
                    ) -> tuple[ConfigSection, ...]:
    """The sections of one domain, in registry order.

    `served` is the modules a plant actually serves; the default is every
    module this version has. A section belonging to a module that is switched
    off is not listed - the screen behind it answers 404, and a row that opens
    onto a 404 is worse than no row.
    """
    modules = REGISTRY if served is None else served
    return tuple(section
                 for module in modules
                 for section in module.config_sections
                 if section.domain == domain)


def module_of_section(section: ConfigSection) -> Module | None:
    """Which module put one section on a Configuration page.

    The section itself does not carry its module's name - it is reached
    through the module's own registry entry, so a name on it would be a second
    copy of a fact the structure already holds. This reads it back for the one
    caller that needs it: the page, which groups eighteen rows by the module
    each belongs to and has to be able to print the heading it claims to be
    sorted by.
    """
    return next((module for module in REGISTRY
                 if section in module.config_sections), None)


def config_domains_with_sections() -> tuple[ConfigDomain, ...]:
    """The domains this version has at least one configurable section in.

    What the navigation should carry a Configuration entry for. Read by the
    test that keeps `FS.NAV` and this registry from drifting apart.
    """
    return tuple(d for d in CONFIG_DOMAINS if config_sections(d.slug))
