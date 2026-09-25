"""The plant pack format: what `plant.toml` may say, and what it compiles to.

A pack is one directory of declarative data that completely answers *which
plant is this?*. Decision
[0022](../../../docs/decisions/0022-what-a-plant-pack-may-contain.md) fixes
what it may hold; this module is that decision as a schema.

    packs/<name>/
      plant.toml        identity, profile, modules, clock, words   (required)
      tag_map.json      which machines exist and what their tags mean
      masterdata/       equipment, materials, routings - data, not a script
      mappings/         inbound columns, inbound SQL
      line_layout.json  optional geometry for the line view
      README.md         what this plant is, for the next engineer

**Every key is enumerated below.** That is the whole difference between a
pack and the registry it replaces: the registry was read into a plain dict by
`tomllib`, so nothing could tell a key from a typo, and the file and its own
documentation drifted apart in both directions inside two weeks. Here an
unknown key is a refusal with a sentence.

**Three kinds of thing never appear.** No code - a pack names no script and
carries no expression, because a pack that can run code is a pack nobody can
review before it touches a plant. No secret - a pack *names* the environment
variable or the file a password lives in and never the password, so a pack is
safe to attach to a support thread. And nothing that changes what a number
means: `[words]` renames a label on a screen, and `check` refuses it the
moment it tries to rename a state, a capability, a role, an event kind or a
KPI.

The two open tables are `[modules]`, whose keys are module names the registry
in `fsmes.modules` knows, and `[words]`, whose keys are the product's own
display terms. Every other table is closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from fsmes import identity
from fsmes import modules as module_registry

#: The pack format this version of the product speaks. A pack states its own
#: in `[pack] format`; one from the future is refused rather than
#: half-understood, and one from the past is what `fsmes pack migrate` moves
#: forward.
FORMAT = 1

#: Format 0 is not a file shape at all: it is a plant's table in the registry
#: (`labs/multiplant/plants.toml`), which is what a plant was before a pack
#: existed. `fsmes pack migrate` reads one and writes format 1, which is how
#: every pack in this repository was first written.
REGISTRY_FORMAT = 0

PLANT_FILE = "plant.toml"


class PackError(Exception):
    """A pack cannot be read as written, and the message says why in one
    sentence. Raised only for what stops the file being parsed at all;
    everything else is a `Problem` from `fsmes.pack.check`, because a person
    fixing a pack wants the whole list, not the first line of it."""


@dataclass(frozen=True)
class Key:
    """One key a pack may carry."""

    name: str
    kind: str
    """`str`, `int`, `float`, `bool`, `path`, `paths`, `ints`, `strs`, `floats`.

    `ints` is a TOML array of whole numbers - `hold_rules = [1, 2]`. It
    compiles to a comma-separated string, because a setting is an environment
    variable and an environment variable is text; the product parses it back
    where it reads it, and states what it parsed.

    `strs` is the same shape for words - `open_statuses = ["Not Started", "In
    Process"]`. It exists because a list of the ERP's own status names is a
    list in the same sense a list of rule numbers is, and writing one as a
    string with commas inside it would have been a second way of saying list
    in one file. Commas separate, and a value that needs a comma inside it
    is the one thing this kind cannot carry - said out loud here rather than
    discovered by a plant whose ERP has a status called `Hold, pending QA`.

    `floats` is the same thing for measurements - `report_windows =
    [0.25, 1, 8]`, a list of window lengths in hours, where a quarter of an
    hour is a real answer and a whole number is not enough to say it."""
    about: str
    becomes: str | None = None
    """The `MES_*` setting this compiles to, or None when the key is read by
    the fleet rather than by the plant's own processes (`label`, `simulate`)."""

    inside: bool = True
    """Whether a `path` must lie inside the pack directory. True for
    everything a person writes, because a pack is one directory somebody can
    hand over. False for generated line data, which is often shared between
    plants replaying the same line and is often enormous."""


@dataclass(frozen=True)
class Section:
    name: str
    about: str
    keys: tuple[Key, ...] = ()
    open_keys: str = ""
    """Non-empty when the table's keys are not fixed: the sentence saying what
    they are instead. `[modules]` and `[words]` are the only two."""
    repeated: bool = False
    """True for an array of tables (`[[accounts]]`)."""


SCHEMA: tuple[Section, ...] = (
    Section("pack", "Which format this file is, and which product versions can read it.", (
        Key("format", "int", "The pack format version. This product speaks 1."),
        Key("requires", "str",
            "The product versions this pack is written for, as a comma-separated "
            "list of comparisons: `>=0.1.2`, or `>=0.2,<0.3`."),
    )),
    Section("plant", "Who this plant is. The identity every reader sees.", (
        Key("name", "str", "The plant's code. One namespace topic segment, so it "
            "never has to be cleaned up downstream.", identity.NAME_SETTING),
        Key("label", "str", "What a person calls this plant, in full."),
        Key("timezone", "str", "The IANA zone this plant works in (Europe/Berlin). "
            "Every wall-clock boundary the MES draws is drawn here.",
            identity.ZONE_SETTING),
        Key("profile", "str", "laptop, plant or fleet - the deployment shape.",
            identity.PROFILE_SETTING),
        Key("enterprise", "str", "The ISA-95 level above the site, as the namespace "
            "publishes it.", "MES_UNS_ENTERPRISE"),
        Key("site", "str", "This plant's site, as the namespace publishes it.",
            "MES_UNS_SITE"),
    )),
    Section("modules", "Which optional modules this plant serves. One boolean per "
            "module; anything not named keeps the product default, which is on.",
            open_keys="a module name from `fsmes.modules` - the kernel cannot be named"),
    Section("words", "This plant's own word for a display label. Display only: a "
            "term a number depends on is refused.",
            open_keys="a display term this plant renames"),
    Section("storage", "Where this plant's data lives.", (
        Key("database_url", "str", "The database. Empty means one SQLite file under "
            "the fleet's data directory.", "MES_DATABASE_URL"),
        Key("database_password_file", "path",
            "The file holding the database password. The pack names the file and "
            "never the password."),
    )),
    Section("serve", "What this plant serves, and where.", (
        Key("api_host", "str", "The interface the dashboard listens on.", "MES_API_HOST"),
        Key("api_port", "int", "The port the dashboard listens on.", "MES_API_PORT"),
        Key("opc_endpoint", "str", "The OPC UA endpoint this plant's machine layer "
            "is at.", "MES_OPC_ENDPOINT"),
        Key("simulate", "bool", "Whether this plant runs its simulated line, or only "
            "serves what its database already holds."),
        Key("speed", "float", "Replay speed. 60 replays a scripted hour in a minute.",
            "MES_SIM_SPEED"),
        Key("secret_key_env", "str",
            "The environment variable holding this plant's token-signing key. The "
            "pack names the variable and never the key."),
    )),
    Section("files", "The rest of the pack, by relative path inside this directory.", (
        Key("tag_map", "path", "Which machines exist and what their tags mean.",
            "MES_TAG_MAP_FILE"),
        Key("replay_dir", "path", "Generated line data for a simulated plant. The one "
            "path a pack may point outside itself: it is produced by a generator, not "
            "written by a person, and two plants replaying one line share it.",
            "MES_REPLAY_DIR", inside=False),
        Key("line_layout", "path", "Optional geometry for the line view.",
            "MES_LINE_LAYOUT_FILE"),
        Key("masterdata", "path",
            "A directory of this plant's equipment, materials, routings and "
            "specifications, as data. `fsmes pack apply` seeds from it."),
    )),
    Section("erp",
            "The ERP boundary: which connector, and what this plant asks of "
            "the link between itself and a system it does not own. Every key "
            "below ships the value that was in the product's source, so a "
            "plant that writes none of them behaves exactly as it does now.", (
        Key("mode", "str", "off, file, rest or erpnext.", "MES_ERP_MODE"),

        # --- how long this plant keeps trying to deliver a confirmation ---
        Key("max_attempts", "int",
            "How many times one confirmation is offered to the ERP before it "
            "is dead and a person decides. An ERP with a four-hour weekly "
            "maintenance window kills a shift of confirmations at eight.",
            "MES_ERP_MAX_ATTEMPTS"),
        Key("base_backoff_s", "int",
            "The wait before the second attempt, in seconds. It doubles after "
            "each failure.", "MES_ERP_BASE_BACKOFF_S"),
        Key("max_backoff_s", "int",
            "The longest this plant waits between two attempts at one "
            "confirmation, in seconds. The doubling stops here.",
            "MES_ERP_MAX_BACKOFF_S"),

        # --- what this plant's ERP means by an order it has not started ---
        Key("open_statuses", "strs",
            "Which ERP order statuses this MES will take an order in, as the "
            "ERP's own words. ERPNext ships `Not Started` and `In Process` and "
            "sites customise the list; this is an order-release policy and it "
            "is the plant's, not the product's.",
            "MES_ERP_OPEN_STATUSES"),

        # --- when a number the ERP read back counts as the number sent ---
        Key("float_rel_tol", "float",
            "How far a number the ERP hands back may differ from the number "
            "sent, as a fraction of it, and still be the same number. Frappe "
            "rounds a Float to the site's own float precision.",
            "MES_ERP_FLOAT_REL_TOL"),
        Key("float_abs_tol", "float",
            "The same agreement as an absolute amount, for numbers near zero. "
            "A site on float precision 4 counting in grams has real "
            "disagreements hidden by a hundredth.",
            "MES_ERP_FLOAT_ABS_TOL"),

        # --- how long this plant waits on a system it does not own ---
        Key("http_timeout", "float",
            "How long the ERPNext connector waits on one request, in seconds. "
            "A bench across a VPN posting a Manufacture entry against a large "
            "bill of material routinely exceeds thirty, and every one of those "
            "burns an attempt.", "MES_ERP_HTTP_TIMEOUT"),
        Key("rest_timeout", "float",
            "The same, for the plain REST connector, in seconds.",
            "MES_ERP_REST_TIMEOUT"),

        # --- what an order that carries no priority inherits ---
        Key("default_order_priority", "int",
            "The priority an order arriving from the ERP is given when the ERP "
            "sends none - lower is more urgent. ERPNext Work Order has no "
            "priority field at all. On a plant that dispatches on a 1-9 scale, "
            "50 sorts every ERP order behind everything a person typed.",
            "MES_ERP_DEFAULT_ORDER_PRIORITY"),

        # --- read by `fsmes erp validate`, which reads no database ---
        Key("confirmation_seconds_tolerance", "float",
            "How far `machine_seconds` in a confirmation file may differ from "
            "the time the step was open, in seconds, before `fsmes erp "
            "validate` calls the document wrong. A file can carry whole "
            "seconds where the MES had more. This one is read at start-up and "
            "not on the Configuration page: the command that reads it is "
            "defined to read files and no database.",
            "MES_ERP_CONFIRMATION_SECONDS_TOLERANCE"),
    )),
    Section("uns", "The unified namespace boundary.", (
        Key("mode", "str", "off, log or mqtt.", "MES_UNS_MODE"),
        Key("topic_prefix", "str", "Everything this plant publishes hangs under this.",
            "MES_UNS_TOPIC_PREFIX"),
    )),
    Section("inbound", "What other systems tell this plant.", (
        Key("mapping", "path", "What this plant's inbound columns are called.",
            "MES_INBOUND_MAPPING_FILE"),
        Key("sql", "path", "This plant's own read-only queries against systems it "
            "already has.", "MES_INBOUND_SQL_FILE"),
        Key("mqtt_mode", "str", "off or mqtt - whether this plant listens to a broker.",
            "MES_INBOUND_MQTT_MODE"),
    )),
    Section("oee", "What this plant asks of a KPI before it will report one.", (
        Key("min_observed_seconds", "float",
            "The least observed time this MES will divide by. Below it no rate, "
            "no OEE and no availability is reported for a window at all, and "
            "the answer is unknown with the ledger saying why rather than a "
            "figure measured over four seconds. Ten seconds by default, which "
            "is the literal this product shipped. `coverage_floor`'s sibling "
            "and beside it on purpose: both are this plant saying how little "
            "evidence is too little, and the argument that made one of them a "
            "key makes the other one too.",
            "MES_OEE_MIN_OBSERVED_SECONDS"),
        Key("coverage_floor", "float",
            "How much of a window this MES must have watched before it reports a "
            "KPI for it, between 0 and 1. Leave it out and every figure prints "
            "with its coverage beside it and nothing is withheld; write 0.8 and a "
            "window this MES saw less than 80% of comes back as unknown, with the "
            "ledger saying where the rest of it went.",
            "MES_OEE_COVERAGE_FLOOR"),
    )),
    Section("quality",
            "What this plant asks of its own quality records. Every key here "
            "ships the value that was in the product's source, so a plant that "
            "writes none of them behaves exactly as it does now.", (
        Key("hold_rules", "ints",
            "Which Western Electric rules raise a quality hold, as a list of "
            "rule numbers from 1 to 4. Leave it out and all four do, which is "
            "what this product has always done. The chart draws and records "
            "every rule whatever this says - a rule a plant switched off the "
            "chart would be a chart that lies (decision 0036); what a plant "
            "chooses here is which of them are worth somebody's morning.",
            "MES_QUALITY_HOLD_RULES"),
        Key("major_rules", "ints",
            "Which of those rules open a *major* non-conformance rather than a "
            "minor one, as a list of rule numbers. Rule 1 alone by default, "
            "which is what the source does. The two words come from this "
            "plant's own severity list.",
            "MES_QUALITY_MAJOR_RULES"),
        Key("cpk_capable", "float",
            "The Cpk at or above which this plant calls a process capable. "
            "1.33 is the usual convention and is the default; a plant with "
            "looser tolerances or a stricter quality culture answers "
            "differently and is still telling the truth about itself. Only the "
            "English word beside the figure moves - the Cpk itself is "
            "arithmetic.",
            "MES_QUALITY_CPK_CAPABLE"),
        Key("cpk_marginal", "float",
            "The Cpk at or above which a process is called marginal rather "
            "than not capable. 1.0 by default, and it must be below "
            "`cpk_capable`.",
            "MES_QUALITY_CPK_MARGINAL"),
        Key("spc_min_points", "int",
            "The fewest readings this plant will draw control limits from. "
            "Twelve by default: below that the limits move so much with each "
            "new reading that they mislead more than they inform. A plant that "
            "insists on twenty-five produces the same payload shape, and the "
            "pallet certificate prints whatever this says.",
            "MES_QUALITY_SPC_MIN_POINTS"),
        Key("spc_history", "int",
            "How many readings back a chart and the rules look. Two hundred by "
            "default. A plant inspecting every fifteen minutes and one "
            "inspecting hourly want different histories behind one chart.",
            "MES_QUALITY_SPC_HISTORY"),
        Key("gauge_ratio_adequate", "float",
            "How many times finer than the tolerance a gauge must resolve "
            "before this plant calls it adequate. Ten by default - AIAG's rule "
            "of ten; ANSI Z540 says four, and both plants are right.",
            "MES_QUALITY_GAUGE_RATIO_ADEQUATE"),
        Key("gauge_ratio_floor", "float",
            "The ratio below which a gauge is too coarse to judge a tolerance "
            "at all. Four by default, and it must not be above "
            "`gauge_ratio_adequate`.",
            "MES_QUALITY_GAUGE_RATIO_FLOOR"),
        Key("gauge_default_interval_days", "int",
            "The calibration interval a newly registered gauge gets when "
            "nobody says otherwise. 365 by default. Each gauge's own interval "
            "is the engineer's and is unaffected.",
            "MES_QUALITY_GAUGE_DEFAULT_INTERVAL_DAYS"),
        Key("coa_serials_listed", "int",
            "How many serial numbers a pallet certificate prints before it says "
            "how many more there are. Two hundred by default. A certificate "
            "that must list every unit and one that must stay printable are "
            "the plant's agreement with whoever reads it.",
            "MES_QUALITY_COA_SERIALS_LISTED"),
        Key("serial_digits", "int",
            "How many digits a generated serial number carries after the "
            "prefix. Six by default. The separator is a hyphen and stays the "
            "product's: the scan that recovers a counter from serials already "
            "issued reads `PREFIX-digits`, and a plant that changed the "
            "separator would restart its own numbering.",
            "MES_QUALITY_SERIAL_DIGITS"),
        Key("nc_code_prefix", "str",
            "What this plant calls a non-conformance on the record itself - "
            "`NC` by default, giving `NC-00017`. A plant that calls them NCRs "
            "calls all of them NCRs. The number's width stays the product's.",
            "MES_QUALITY_NC_CODE_PREFIX"),
        Key("containment_max_depth", "int",
            "How deep this plant's packaging goes: piece, stack, pack, pallet, "
            "truck is five and six is the default. It is also the guard that "
            "stops a containment walk running away, so the product keeps a "
            "hard ceiling of twelve above whatever is written here.",
            "MES_QUALITY_CONTAINMENT_MAX_DEPTH"),
    )),
    Section("process",
            "What this plant assumes when nothing has told it otherwise, and "
            "how much of itself one screen shows. Every key here ships the "
            "value that was in the product's source, so a plant that writes "
            "none of them behaves exactly as it does now.", (
        Key("maintenance_due_soon_fraction", "float",
            "How far through a maintenance plan's own interval counts as "
            "coming due, between 0 and 1. 0.8 by default. It is a fraction of "
            "each plan's interval rather than a number of hours, so it already "
            "scales from a weekly filter change to an annual overhaul; what "
            "differs between plants is how long a spare takes to arrive.",
            "MES_PROCESS_MAINTENANCE_DUE_SOON_FRACTION"),
        Key("default_cycle_seconds", "float",
            "What one unit is assumed to cost at a station with no rated cycle "
            "time, in seconds. 3.0 by default. Real cycle times are seeded onto "
            "the machine from the tag map and always win; this only keeps a "
            "plan possible for a plant that has not commissioned its machines "
            "yet, and a schedule built on it says so - `uses_default_cycle` "
            "reports it rather than hiding it. A filling line and a CNC cell "
            "want different guesses.",
            "MES_PROCESS_DEFAULT_CYCLE_SECONDS"),
        Key("default_job_minutes", "float",
            "How long a maintenance job with no plan behind it is assumed to "
            "take, in minutes. 60.0 by default. It sizes the backlog's downtime "
            "figure and the block the scheduler reserves, so a supervisor "
            "deciding whether tonight is the night is reading it.",
            "MES_PROCESS_DEFAULT_JOB_MINUTES"),
        Key("maintenance_plan_default_minutes", "float",
            "The expected duration a new maintenance plan gets when nobody "
            "says otherwise, in minutes. 30.0 by default. Each plan's own "
            "figure is the engineer's and is unaffected; this is only the "
            "plant's house default.",
            "MES_PROCESS_MAINTENANCE_PLAN_DEFAULT_MINUTES"),
        Key("default_report_hours", "float",
            "How long a window is when a caller asks for none, in hours. 8.0 "
            "by default, which the source called *a shift*. A plant working "
            "twelve-hour shifts answers 12 and every payload still states the "
            "`requested_hours` it was actually given, so nothing downstream "
            "reads the default as a fact.",
            "MES_PROCESS_DEFAULT_REPORT_HOURS"),
        Key("report_windows", "floats",
            "The window lengths every screen with a time picker offers, in "
            "hours, as a list. `[0.25, 1, 8, 24, 168]` by default - the list "
            "the browser held as a literal until 2026-09-25. A plant whose "
            "people work in twelve-hour shifts and think in weeks offers a "
            "different five. `default_report_hours` is which of them a screen "
            "opens on, and a viewer who has chosen a window of their own keeps "
            "it whatever this says.",
            "MES_PROCESS_REPORT_WINDOWS"),
        Key("gantt_screenful", "int",
            "How many machines one Gantt draws before it stops, as a count. "
            "12 by default. A six-station cell and a 108-station plant want "
            "different screenfuls; the payload states `machines_shown` beside "
            "`machines_total` either way, so moving this hides nothing.",
            "MES_PROCESS_GANTT_SCREENFUL"),
        Key("previous_shift_horizon_days", "int",
            "How far back `shift=previous` will look for a shift that has "
            "ended, in days. 14 by default - two weeks covers a plant that ran "
            "nothing over a shutdown. A seasonal plant with a six-week shutdown "
            "answers differently, and the refusal sentence quotes whatever this "
            "says rather than a number nobody chose.",
            "MES_PROCESS_PREVIOUS_SHIFT_HORIZON_DAYS"),
        Key("working_week_mask", "str",
            "The working week a shift pattern gets when it names no days: "
            "seven characters of 0 or 1, Monday first. `1111100` by default. "
            "The *format* is the product's and always will be - Monday first, "
            "seven flags - and which mask is the default is this plant's, "
            "because Sunday to Thursday is a real working week.",
            "MES_PROCESS_WORKING_WEEK_MASK"),
        Key("schedule_default_horizon_hours", "float",
            "How far ahead the schedule board looks when nobody says, in "
            "hours. 24.0 by default. A job shop planning a fortnight and a "
            "line planning a shift want different boards.",
            "MES_PROCESS_SCHEDULE_DEFAULT_HORIZON_HOURS"),
    )),
    Section("controls",
            "How hard this plant's own processes argue with the systems they "
            "talk to, and how densely they sample. Not *where* those systems "
            "are - that is `[serve]`, `[uns]` and `[inbound]`, one line of "
            "plumbing each - but how long to keep trying and how much to "
            "store, which is the controls engineer's tuning. Every key here "
            "ships the value that was in the product's source.", (
        Key("opc_book_attempts", "int",
            "How many times the OPC agent will retry booking a batch of "
            "readings before it gives up on them, as a count. 4 by default. "
            "Its sibling `sqlite_busy_timeout_ms` has been a setting for "
            "months; this was the half of the same argument left in code.",
            "MES_CONTROLS_OPC_BOOK_ATTEMPTS"),
        Key("opc_book_backoff_s", "float",
            "The first wait between those attempts, in seconds, doubling each "
            "time. 0.5 by default.",
            "MES_CONTROLS_OPC_BOOK_BACKOFF_S"),
        Key("uns_max_attempts", "int",
            "How many times a namespace publication is attempted before it is "
            "recorded dead, as a count. 8 by default. A broker restarted "
            "nightly for twenty minutes kills every queued event at eight "
            "attempts; dead is never deleted, so what this changes is how long "
            "the plant keeps trying before a person has to decide.",
            "MES_CONTROLS_UNS_MAX_ATTEMPTS"),
        Key("uns_base_backoff_s", "int",
            "The first wait before a failed publication is retried, in "
            "seconds, doubling each attempt. 5 by default.",
            "MES_CONTROLS_UNS_BASE_BACKOFF_S"),
        Key("uns_max_backoff_s", "int",
            "The ceiling that doubling curve is capped at, in seconds. 3600 by "
            "default - an hour.",
            "MES_CONTROLS_UNS_MAX_BACKOFF_S"),
        Key("trigger_reload_seconds", "float",
            "How often a running agent re-reads the approved triggers, in "
            "seconds, which is how fast an approval on screen reaches the "
            "machines. 30.0 by default. A plant that stops a line on an SPC "
            "signal wants five.",
            "MES_CONTROLS_TRIGGER_RELOAD_SECONDS"),
        Key("trigger_default_cooldown_seconds", "float",
            "How long a newly drafted trigger stays quiet after firing when "
            "nobody says otherwise, in seconds. 300.0 by default. Each "
            "trigger's own cooldown is the engineer's and is unaffected; five "
            "minutes of silence is right on a continuous line and wrong on a "
            "station with forty-second cycles.",
            "MES_CONTROLS_TRIGGER_DEFAULT_COOLDOWN_SECONDS"),
        Key("opc_history_ratio", "int",
            "How many times slower than the semantic tags the rest of a "
            "machine's tags are sampled, as a multiple of `opc_publish_ms`. 10 "
            "by default. A ratio rather than a rate, so it already scales with "
            "each plant's publish interval; what is left for a plant to answer "
            "is how much history it wants to store. It takes effect when the "
            "agent next subscribes, which is the honest answer for a "
            "subscription interval a server holds.",
            "MES_CONTROLS_OPC_HISTORY_RATIO"),
        Key("opc_min_history_ms", "int",
            "The floor under that sampling interval, in milliseconds. 1000 by "
            "default. Like the ratio, it is read when the agent subscribes.",
            "MES_CONTROLS_OPC_MIN_HISTORY_MS"),
        Key("opc_order_sync_seconds", "float",
            "How often the agent checks whether any machine's OrderCode has "
            "changed, in seconds. 2.0 by default. Two hundred machines on one "
            "endpoint is a hundred database reads a second to discover nothing "
            "changed.",
            "MES_CONTROLS_OPC_ORDER_SYNC_SECONDS"),
        Key("opc_adjustment_poll_seconds", "float",
            "How often the agent looks for approved setpoint adjustments to "
            "write, in seconds. 5.0 by default. `POST /adjustments/{code}/"
            "approve` promises a person that *the OPC agent writes within "
            "seconds*, and this is the number that promise rests on - a plant "
            "raising it past a few seconds is changing what it has told its "
            "own operators.",
            "MES_CONTROLS_OPC_ADJUSTMENT_POLL_SECONDS"),
    )),
    Section("admin",
            "What this plant asks of its own administration: the role a new "
            "account starts with, how big an answer is, what one conversation "
            "with a model may spend, and the shape a drafted document takes. "
            "Every key here ships the value that was in the product's source, "
            "so a plant that writes none of them behaves exactly as it does "
            "now.", (
        Key("default_new_account_role", "str",
            "The role an account created without one is given. `operator` by "
            "default. One plant onboards everyone as a viewer and grants "
            "upward; another starts them on the floor. The role codes "
            "themselves stay the product's, and a role this plant does not "
            "have is refused rather than created.",
            "MES_ADMIN_DEFAULT_NEW_ACCOUNT_ROLE"),
        Key("list_default_limit", "int",
            "How many rows a list endpoint returns when the caller asks for "
            "no size. Fifty by default. Read once when the process starts, "
            "because it is published in this plant's own OpenAPI document.",
            "MES_ADMIN_LIST_DEFAULT_LIMIT"),
        Key("list_max_limit", "int",
            "The most rows a caller may ask one list for. Five hundred by "
            "default, and it must not be below `list_default_limit`. Read "
            "once when the process starts, for the same reason: it is the "
            "ceiling this plant's OpenAPI document states, and a ceiling that "
            "moved under a caller holding that document would make it a lie.",
            "MES_ADMIN_LIST_MAX_LIMIT"),
        Key("pending_approvals_page_size", "int",
            "How many waiting items the Floor screen's approvals panel "
            "answers with. Twenty by default. Its own number and not "
            "`list_default_limit`: the panel has no pager, so item twenty-one "
            "is simply not on the screen.",
            "MES_ADMIN_PENDING_APPROVALS_PAGE_SIZE"),
        Key("walkthrough_max_steps", "int",
            "The most steps one recorded walkthrough may have. Sixty by "
            "default. A plant with a ninety-step changeover procedure says so "
            "here rather than being refused for a reason nothing outside it "
            "cares about.",
            "MES_ADMIN_WALKTHROUGH_MAX_STEPS"),
        Key("walkthrough_title_chars", "int",
            "How long one step's title may be before it is shortened. 120 by "
            "default. It is the plant's own words being cut, which is why the "
            "plant says where.",
            "MES_ADMIN_WALKTHROUGH_TITLE_CHARS"),
        Key("walkthrough_body_chars", "int",
            "How long one step's explanation may be before it is shortened. "
            "1000 by default.",
            "MES_ADMIN_WALKTHROUGH_BODY_CHARS"),
        Key("walkthrough_fill_chars", "int",
            "How long a value a step types into a control may be. 200 by "
            "default.",
            "MES_ADMIN_WALKTHROUGH_FILL_CHARS"),
        Key("walkthrough_tab_chars", "int",
            "How long a step's tab or open-this name may be. 60 by default.",
            "MES_ADMIN_WALKTHROUGH_TAB_CHARS"),
        Key("walkthrough_default_capability", "str",
            "The capability a recorded walkthrough asks of a viewer when "
            "nobody says otherwise. `plant.read` by default. A plant that "
            "wants every recording gated to at least `production.book` says "
            "so once, here; the capability names themselves stay the "
            "product's and one this version does not have is refused.",
            "MES_ADMIN_WALKTHROUGH_DEFAULT_CAPABILITY"),
        Key("agent_max_rounds", "int",
            "How many turns the floor agent may take on one message before it "
            "has to stop and say something. Twelve by default.",
            "MES_ADMIN_AGENT_MAX_ROUNDS"),
        Key("agent_session_ttl_seconds", "int",
            "How long a conversation with the floor agent lives without a "
            "message. Half an hour by default - a session lifetime exactly "
            "like `token_ttl_seconds`, which is already the plant's.",
            "MES_ADMIN_AGENT_SESSION_TTL_SECONDS"),
        Key("agent_result_limit", "int",
            "How many characters of one tool result the agent is shown. 6000 "
            "by default; the rest is marked truncated rather than dropped "
            "silently.",
            "MES_ADMIN_AGENT_RESULT_LIMIT"),
        Key("assistant_context_chars", "int",
            "How much of the plant's own facts reach the local model when it "
            "answers a question. 3000 characters by default. A plant running "
            "a larger model on better hardware can afford more.",
            "MES_ADMIN_ASSISTANT_CONTEXT_CHARS"),
        Key("design_compress_budget", "int",
            "How many characters the design chat's on-device summary of a "
            "screen may run to. 2500 by default.",
            "MES_ADMIN_DESIGN_COMPRESS_BUDGET"),
        Key("design_compress_source_chars", "int",
            "How much of a screen's text is handed to that summary in the "
            "first place. 12000 by default.",
            "MES_ADMIN_DESIGN_COMPRESS_SOURCE_CHARS"),
        Key("design_source_budget", "int",
            "How much of the screen's own source the design chat reads before "
            "answering about it. 14000 characters by default.",
            "MES_ADMIN_DESIGN_SOURCE_BUDGET"),
        Key("assistant_timeout_seconds", "float",
            "How long the floor assistant waits for the local model to answer "
            "a question. 60 seconds by default. The six timeouts in this "
            "table are named one per thing waited for, rather than left as "
            "six anonymous numbers in five files: a plant on a slower GPU "
            "raises all six, and can see which one it just raised.",
            "MES_ADMIN_ASSISTANT_TIMEOUT_SECONDS"),
        Key("drafting_timeout_seconds", "float",
            "How long drafting a work instruction waits for the local model. "
            "180 seconds by default - the longest of the six, because it is "
            "the one writing prose.",
            "MES_ADMIN_DRAFTING_TIMEOUT_SECONDS"),
        Key("design_generate_timeout_seconds", "float",
            "How long an ordinary design-chat generation waits. 120 seconds "
            "by default.",
            "MES_ADMIN_DESIGN_GENERATE_TIMEOUT_SECONDS"),
        Key("design_classify_timeout_seconds", "float",
            "How long the one-word question *is this a design question* "
            "waits. 45 seconds by default, because it is the shortest thing "
            "asked of the model anywhere.",
            "MES_ADMIN_DESIGN_CLASSIFY_TIMEOUT_SECONDS"),
        Key("design_compress_timeout_seconds", "float",
            "How long an on-device compression of a screen waits. 90 seconds "
            "by default.",
            "MES_ADMIN_DESIGN_COMPRESS_TIMEOUT_SECONDS"),
        Key("design_chat_timeout_seconds", "float",
            "How long the design chat's own reply waits. 240 seconds by "
            "default: it reads a screen's source and a conversation before it "
            "starts writing.",
            "MES_ADMIN_DESIGN_CHAT_TIMEOUT_SECONDS"),
        Key("document_house_style", "str",
            "The shape a drafted work instruction takes, as the instructions "
            "handed to the model: a one-line Purpose, numbered Steps, a short "
            "*If it fails*, six to ten steps, imperative, no preamble. A "
            "plant whose quality system mandates Scope / Hazards / Steps / "
            "Records writes its own here. **What is not in this key, and "
            "cannot be:** the product always tells the model to use only the "
            "facts given, to invent no tolerance or tool, and that an "
            "operator is never told to adjust a reading toward the middle. "
            "Those are product invariants, they are added to whatever this "
            "says, and a plant cannot edit them away.",
            "MES_ADMIN_DOCUMENT_HOUSE_STYLE"),
        Key("ai_rollup_stale_hours", "int",
            "How old a daily AI artifact gets before the panel calls it late. "
            "Forty hours by default, which was chosen for one encrypted "
            "laptop that is regularly off overnight; a plant's server that "
            "never sleeps answers differently.",
            "MES_ADMIN_AI_ROLLUP_STALE_HOURS"),
    )),
    Section("screens",
            "The cadence and the page sizes this plant's own screens run at. "
            "Everything in this table is read by the browser, which asks for "
            "it once when a page loads, so a number saved here is in force on "
            "the next page load and needs no rebuild and no restart. Every "
            "key ships the literal that was in the JavaScript.", (
        Key("floor_refresh_ms", "int",
            "How often the Floor screen re-reads the plant, in milliseconds. "
            "2000 by default, which was tuned against one plant on a switched "
            "floor network; a plant on a thin WAN link answers differently.",
            "MES_SCREENS_FLOOR_REFRESH_MS"),
        Key("floor_pending_refresh_ms", "int",
            "How often the Floor screen re-reads the approvals panel. 30000 "
            "by default: this answer is per-caller and cannot be shared "
            "between everybody watching, and a queue somebody signs off once "
            "a week does not want a two-second poll.",
            "MES_SCREENS_FLOOR_PENDING_REFRESH_MS"),
        Key("admin_refresh_ms", "int",
            "How often the Admin screen re-reads people and routings. 8000 by "
            "default. A third number rather than a third opinion: it sits "
            "beside the two above so a plant sets its three clocks in one "
            "place, and they stay three because a screen watching machines "
            "and a screen listing employees are not one cadence.",
            "MES_SCREENS_ADMIN_REFRESH_MS"),
        Key("floor_machine_page", "int",
            "How many machine cards one page of the Floor grid shows. 24 by "
            "default. The tiles above the grid still count the whole plant.",
            "MES_SCREENS_FLOOR_MACHINE_PAGE"),
        Key("floor_order_page", "int",
            "How many work orders one page of the Floor card shows. 10 by "
            "default.",
            "MES_SCREENS_FLOOR_ORDER_PAGE"),
        Key("floor_spec_choices", "int",
            "How many characteristics the Floor screen's specification picker "
            "offers before it says how many more there are. 200 by default.",
            "MES_SCREENS_FLOOR_SPEC_CHOICES"),
        Key("admin_user_page_size", "int",
            "How many people one page of the Admin screen lists. 25 by "
            "default. Three hundred employees is one plant's ordinary.",
            "MES_SCREENS_ADMIN_USER_PAGE_SIZE"),
        Key("admin_routing_page_size", "int",
            "How many routings one page of the Admin screen lists. 25 by "
            "default.",
            "MES_SCREENS_ADMIN_ROUTING_PAGE_SIZE"),
        Key("all_pages_limit", "int",
            "How big each page is when a screen reads a whole bounded list "
            "one page at a time. 500 by default.",
            "MES_SCREENS_ALL_PAGES_LIMIT"),
        Key("all_pages_cap", "int",
            "How far such a read will go before it stops and says so. 2000 "
            "rows by default. Nothing lies when it is met - the screen is "
            "told the list is incomplete and says so - but a plant with three "
            "thousand characteristics meets it every day.",
            "MES_SCREENS_ALL_PAGES_CAP"),
        Key("toast_ms", "int",
            "How long a confirmation stays on screen, in milliseconds. 3500 "
            "by default. How long a message lingers is an accessibility "
            "answer a plant gives for its own people.",
            "MES_SCREENS_TOAST_MS"),
        Key("input_debounce_ms", "int",
            "How long a screen waits for typing to settle before it searches. "
            "250 by default.",
            "MES_SCREENS_INPUT_DEBOUNCE_MS"),
        Key("assistant_log_entries", "int",
            "How many lines of the assistant's conversation survive a page "
            "change. 60 by default.",
            "MES_SCREENS_ASSISTANT_LOG_ENTRIES"),
        Key("assistant_fill_attempts", "int",
            "How many times a walkthrough looks again for a control that has "
            "not appeared yet. 20 by default.",
            "MES_SCREENS_ASSISTANT_FILL_ATTEMPTS"),
        Key("assistant_fill_wait_ms", "int",
            "How long it waits between those looks, in milliseconds. 150 by "
            "default. Twenty attempts at 150 ms is three seconds, and the "
            "plant's slowest PC is what the two of them are really about.",
            "MES_SCREENS_ASSISTANT_FILL_WAIT_MS"),
    )),
    Section("system",
            "This plant's own plumbing: how long the fleet console waits, how "
            "much log history is kept, and which local model answers. "
            "Decision 0035 section 2 keeps IT outside the role model, so "
            "these have no capability and no workspace of their own - they "
            "are listed on the Administration page and written by whoever "
            "holds `users.manage`, which is already the only role that can "
            "reach them.", (
        Key("fleet_health_probe_timeout", "float",
            "How long the fleet console waits for one plant to answer a "
            "health check, in seconds. 3.0 by default: long enough for a "
            "plant that is busy, short enough that a console polling a dozen "
            "of them does not hang on the one that is off. Read once when the "
            "console starts, because it is the console's number about every "
            "plant it watches rather than any one plant's about itself.",
            "MES_SYSTEM_FLEET_HEALTH_PROBE_TIMEOUT"),
        Key("log_rotation_max_bytes", "int",
            "How large one component's log file grows before it is rotated. "
            "5,000,000 by default. Read once when the process starts: logging "
            "is configured before this plant's database is open, so a value "
            "saved on the screen is in force at the next restart and the "
            "screen says so.",
            "MES_SYSTEM_LOG_ROTATION_MAX_BYTES"),
        Key("log_rotation_backups", "int",
            "How many rotated log files are kept per component. Five by "
            "default, so the shipped pair is twenty-five megabytes of history "
            "- a retention policy, one file away from `tag_retention_days`, "
            "which is already the plant's.",
            "MES_SYSTEM_LOG_ROTATION_BACKUPS"),
        Key("local_model_name", "str",
            "Which model on this machine answers. `qwen3:8b` by default. "
            "Which model drafts a plant's work instructions is the plant's "
            "hardware and the plant's choice; it is already recorded on each "
            "document as `drafted_by_model`, so nothing a record means "
            "changes when this does.",
            "MES_SYSTEM_LOCAL_MODEL_NAME"),
    )),
    Section("floor", "The shop floor's own cadence, for a simulated plant.", (
        Key("inspect_every", "int", "Seconds between recorded quality checks.",
            "MES_OPS_INSPECT_EVERY"),
        Key("issue_every", "int", "Seconds between material issues.",
            "MES_OPS_ISSUE_EVERY"),
        Key("inspect_all", "bool", "Whether a pass records every specification or one.",
            "MES_OPS_INSPECT_ALL"),
    )),
    Section("accounts", "The accounts this plant creates when it is applied. Each "
            "names the environment variable its password comes from; a variable "
            "that is not set refuses rather than making an account with no password.",
            (Key("code", "str", "The account's code."),
             Key("name", "str", "The person or service, in full."),
             Key("role", "str", "The role it holds."),
             Key("password_env", "str", "The environment variable holding its password.")),
            repeated=True),
)

BY_SECTION: dict[str, Section] = {s.name: s for s in SCHEMA}

#: Keys a registry carried that a pack refuses outright, with the sentence
#: saying where the value went instead. Separate from "unknown key" because
#: "secret_key is not a key this format has" is a worse answer than "a pack
#: never holds a secret; name the variable it lives in".
REFUSED: dict[str, str] = {
    "secret_key":
        "a pack never holds a secret. Put the key in an environment variable and "
        "name the variable in `[serve] secret_key_env`.",
    "database_password":
        "a pack never holds a secret. Put the password in a file and name the file "
        "in `[storage] database_password_file`.",
    "password":
        "a pack never holds a secret. An account names the environment variable its "
        "password comes from, in `password_env`.",
    "init":
        "a pack carries no code. Master data is data, in the directory `[files] "
        "masterdata` names; a plant whose master data is generated runs its own "
        "generator and is not seeded by the pack.",
    "post_boot":
        "a pack carries no code. A scenario script belongs to the lab that wrote "
        "it, and is run by the person running the scenario.",
}


@dataclass(frozen=True)
class Pack:
    """One pack, read from disk. Nothing here is validated - that is
    `fsmes.pack.check`, which is what a person runs and what `apply` calls
    first."""

    directory: Path
    raw: dict
    """Exactly what the TOML said, so the checker can talk about what is
    written rather than about what survived parsing."""

    @property
    def name(self) -> str:
        return str(self.table("plant").get("name") or "")

    @property
    def label(self) -> str:
        return str(self.table("plant").get("label") or self.name)

    @property
    def format(self) -> int | None:
        value = self.table("pack").get("format")
        return value if isinstance(value, int) else None

    def table(self, section: str) -> dict:
        value = self.raw.get(section)
        return value if isinstance(value, dict) else {}

    def accounts(self) -> list[dict]:
        value = self.raw.get("accounts")
        return [a for a in value if isinstance(a, dict)] if isinstance(value, list) else []

    def path(self, relative: str) -> Path:
        """A file the pack names, as a path from here. Relative to the pack
        directory always: a pack that reached outside itself would not be one
        directory a plant could hand to anybody."""
        return self.directory / relative

    def referenced(self) -> dict[str, Path]:
        """Every file and directory this pack names, keyed by the key that
        named it, so a complaint can say which line to open."""
        found: dict[str, Path] = {}
        for section in ("files", "inbound"):
            for key in BY_SECTION[section].keys:
                if key.kind != "path":
                    continue
                value = self.table(section).get(key.name)
                if isinstance(value, str) and value:
                    found[f"[{section}] {key.name}"] = self.path(value)
        return found


def read(directory: Path) -> Pack:
    """The pack in this directory, parsed. Raises `PackError` if it is not a
    pack at all - no directory, no `plant.toml`, or TOML that will not parse."""
    directory = Path(directory)
    if not directory.is_dir():
        raise PackError(f"{directory} is not a directory, so it is not a plant pack.")
    manifest = directory / PLANT_FILE
    if not manifest.is_file():
        raise PackError(
            f"{directory} holds no {PLANT_FILE}, so it is not a plant pack. A pack is a "
            f"directory whose {PLANT_FILE} says which plant it is.")
    try:
        raw = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PackError(f"{manifest} is not valid TOML: {exc}") from exc
    return Pack(directory=directory, raw=raw)


# ------------------------------------------------------------- what it means


def module_spec(pack: Pack) -> str:
    """`[modules]` as the `MES_MODULES` string it compiles to.

    Written `all` first and then each module the pack switches off or on, so a
    plant that names nothing serves exactly what it served before packs
    existed - and so a module this pack has never heard of, added by a later
    release, arrives switched on rather than silently missing.
    """
    table = pack.table("modules")
    if not table:
        return module_registry.DEFAULT
    parts = [module_registry.DEFAULT]
    for name, on in table.items():
        parts.append(str(name) if on else f"-{name}")
    return ",".join(parts)


def words(pack: Pack) -> dict[str, str]:
    """`[words]` as a plain mapping: the product's term -> this plant's."""
    return {str(k): str(v) for k, v in pack.table("words").items()}


def key_named(section: str, name: str) -> Key | None:
    """One key of the schema, by the two names that identify it. None when
    this version has no such key, which callers say out loud rather than
    guessing at - a pack key nobody can look up is a pack key nobody can
    validate."""
    known = BY_SECTION.get(section)
    if known is None:
        return None
    return next((k for k in known.keys if k.name == name), None)


def parse(key: Key, written: str):
    """Text as the value its key is, or `ValueError` saying why it is not.

    The other direction from `settings` below, and it exists for the same
    reason that one does: a setting is text by the time anything reads one, so
    something has to turn a person's typing back into the whole number, the
    measurement or the list of rule numbers the key declares itself to be -
    once, here, rather than at each screen that offers an input.

    It converts and it does not judge. Whether 25 readings is a sensible
    number of readings, or whether rule 7 exists, is `fsmes.pack.check`'s
    question and is answered there for a pack file and for an edited setting
    alike; this refuses only text that is not the kind of thing at all.
    """
    written = (written or "").strip()
    if key.kind == "int":
        try:
            return int(written)
        except ValueError:
            raise ValueError(f"{written!r} is not a whole number.") from None
    if key.kind == "float":
        try:
            return float(written)
        except ValueError:
            raise ValueError(f"{written!r} is not a number.") from None
    if key.kind == "bool":
        if written.lower() in ("true", "yes", "on", "1"):
            return True
        if written.lower() in ("false", "no", "off", "0"):
            return False
        raise ValueError(f"{written!r} is not true or false.")
    if key.kind in ("ints", "floats"):
        # An empty list is a real answer for `ints` - `hold_rules = []` is
        # *draw every rule and hold on none of them* - so empty text parses to
        # the empty list rather than refusing. Whether an empty list is a
        # sensible answer for a particular key is `fsmes.pack.check`'s
        # question: `report_windows = []` is a screen with no time picker on
        # it, and the checker refuses that in its own sentence.
        whole = key.kind == "ints"
        word = "a whole number" if whole else "a number"
        out: list = []
        for part in written.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(int(part) if whole else float(part))
            except ValueError:
                raise ValueError(
                    f"{part!r} is not {word}. This key is a list of them, "
                    "written with commas between.") from None
        return out
    if key.kind == "strs":
        # Same rule as `ints`, and the same reason for it: an empty list is a
        # real answer, and the text between commas is trimmed because a person
        # writing a list by hand puts a space after the comma.
        return [part.strip() for part in written.split(",") if part.strip()]
    return written


def as_written(key: Key, value) -> str:
    """One value as the text a compiled setting carries it in.

    The same three coercions `settings` performs, factored out so that a value
    stored in the database and the same value compiled from a pack file reach
    the product as the identical string. `path` is not here: a path is
    resolved against the pack directory it came from, which nothing editing a
    setting afterwards has.
    """
    if key.kind == "bool":
        return "true" if value else "false"
    if key.kind == "ints":
        return ",".join(str(int(item)) for item in value)
    if key.kind == "strs":
        return ",".join(str(item).strip() for item in value)
    if key.kind == "floats":
        return ",".join(str(float(item)) for item in value)
    return str(value)


def settings(pack: Pack) -> dict[str, str]:
    """Every `MES_*` variable this pack states, and only those.

    What it deliberately does not do is fill anything in. A key the pack does
    not carry is absent here, so the product's own default stands and the pack
    is never a second opinion about what the default is.
    """
    out: dict[str, str] = {}
    for section in SCHEMA:
        if section.open_keys or section.repeated:
            continue
        table = pack.table(section.name)
        for key in section.keys:
            if key.becomes is None or key.name not in table:
                continue
            value = table[key.name]
            if value is None or value == "":
                continue
            if key.kind == "path":
                value = pack.path(str(value)).as_posix()
            elif key.kind == "bool":
                value = "true" if value else "false"
            elif key.kind in ("ints", "floats", "strs"):
                # A setting is text. The list is written as a comma list
                # rather than as Python's own repr, so the value a person
                # reads in `fsmes info` is the value they wrote in the pack.
                if not isinstance(value, list):
                    continue  # already refused by the checker; never guessed at here
                if key.kind == "strs":
                    value = ",".join(str(item).strip() for item in value)
                else:
                    cast = int if key.kind == "ints" else float
                    value = ",".join(str(cast(item)) for item in value)
            out[key.becomes] = str(value)
    out["MES_MODULES"] = module_spec(pack)
    if pack.table("words"):
        out["MES_WORDS"] = json.dumps(words(pack), sort_keys=True)
    secret_env = str(pack.table("serve").get("secret_key_env") or "")
    if secret_env and os.environ.get(secret_env):
        out["MES_SECRET_KEY"] = os.environ[secret_env]
    return out


def database_url(pack: Pack) -> tuple[str, str]:
    """The database this pack's plant keeps its data in, and where that came
    from - the sentence a status command prints on its first line.

    The password is put back from the file `[storage] database_password_file`
    names, because a pack names the file and never the secret. A pack that
    names no database at all raises `storage.Unknown`: which file the fleet
    would give it is the fleet's fact, not the pack's, and answering about
    this process's own database instead is exactly the mistake that made
    `fsmes pack status` report "never migrated" about a plant at head.
    """
    from fsmes import storage

    table = pack.table("storage")
    url = str(table.get("database_url") or "").strip()
    if not url:
        raise storage.Unknown(
            f"{pack.directory} names no `[storage] database_url`, so this plant keeps its "
            "data in the file its fleet gives it - and which file that is belongs to the "
            "fleet, not to the pack. Pass --plant <name> to ask about a plant in the "
            "fleet file.")
    named = table.get("database_password_file")
    try:
        resolved = storage.with_password(url, named)
    except OSError as exc:
        raise storage.Unknown(
            f"{pack.directory} names {storage.redacted(url)}, whose password is in "
            f"{named} - and that file could not be read here "
            f"({exc.strerror or exc}). Nothing here can reach that database.") from None
    return resolved, f"from the pack at {pack.directory}"


# ------------------------------------------------------------- what it hashes

#: Files whose contents are not the pack: editor leftovers, and the data a
#: simulated plant's replay directory holds, which is generated and can be
#: gigabytes. A pack's fingerprint covers what a person wrote.
IGNORED_NAMES = frozenset({".DS_Store", "Thumbs.db", "__pycache__"})


def fingerprint(pack: Pack) -> str:
    """A hash of everything a person wrote in this pack.

    What drift is measured against: `fsmes pack apply` records it, and
    `fsmes pack status` says whether the files on disk still make the same
    one. Sorted by relative POSIX path so a pack hashes the same on Windows
    as it does on Linux, and covering names as well as contents so a renamed
    file is a change.
    """
    digest = hashlib.sha256()
    for path in sorted(_hashable(pack), key=lambda p: p.relative_to(pack.directory).as_posix()):
        digest.update(path.relative_to(pack.directory).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _hashable(pack: Pack) -> list[Path]:
    replay = pack.table("files").get("replay_dir")
    skip = {pack.path(str(replay)).resolve()} if isinstance(replay, str) and replay else set()
    out = []
    for path in pack.directory.rglob("*"):
        if not path.is_file() or path.name in IGNORED_NAMES:
            continue
        if any(part in IGNORED_NAMES for part in path.parts):
            continue
        if any(parent.resolve() in skip for parent in path.parents):
            continue
        out.append(path)
    return out


@dataclass(frozen=True)
class FleetEntry:
    """One pack in a fleet's list, and where it was listed from."""

    directory: Path
    listed_in: Path
    environment: dict = field(default_factory=dict)
