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
class Module:
    name: str
    title: str

    kernel: bool = False
    """Always present. `MES_MODULES` may not switch it off - the kernel is
    master data, routings, orders, dispatch, execution, audit and auth, and a
    plant without them is not an MES."""

    routers: tuple[Mount, ...] = ()
    pages: tuple[Page, ...] = ()

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
        routers=(Mount("fsmes.api.routers.equipment", "/equipment", ("equipment",)),),
        pages=(
            Page("/dashboard/station", "station.html",
                 "One machine, arm's length: the line-side operator's screen."),
            Page("/dashboard/machines", "machines.html",
                 "Engineering: the plant as a tree, with cost centers and alarms."),
            Page("/dashboard/tags", "tags.html",
                 "Engineering: every tag on every machine, and whether each machine "
                 "is still talking."),
            Page("/dashboard/machine/{code}", "machine.html",
                 "One machine: every tag it publishes, trends, timeline, OEE, "
                 "maintenance, and what is queued on it. The script reads the code "
                 "from the URL; the page is the same file for every machine."),
        ),
        tools=("fsmes.mcp.equipment",),
        tables=("equipment", "equipment_states", "tag_values", "uns_publications"),
    ),
    Module(
        name="dashboard",
        title="The plant floor front door",
        kernel=True,
        routers=(Mount("fsmes.api.routers.dashboard", "/dashboard", ("dashboard",)),),
        pages=(Page("/dashboard", "index.html", "The plant at a glance."),),
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
        routers=(Mount("fsmes.api.routers.quality", "/quality", ("quality",)),),
        pages=(
            Page("/dashboard/quality", "quality.html",
                 "Inspection history against the specification that judged it."),
            Page("/dashboard/spc", "spc.html",
                 "The individuals control chart: is the process stable, is it "
                 "capable, and which are two different questions."),
            Page("/dashboard/gauges", "gauges.html",
                 "The gauge register, calibration, and what a failed one invalidated."),
        ),
        tools=("fsmes.mcp.quality",),
        tables=("quality_specs", "quality_checks", "non_conformances",
                "gauges", "calibrations"),
    ),
    Module(
        name="kpis",
        title="OEE and order KPIs",
        routers=(Mount("fsmes.api.routers.kpis", "/kpis", ("kpis",)),),
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
    ),
    Module(
        name="erp",
        title="The ERP connector",
        routers=(Mount("fsmes.api.routers.erp", "/erp", ("erp",)),),
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
