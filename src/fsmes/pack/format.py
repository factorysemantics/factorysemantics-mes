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
    """`str`, `int`, `float`, `bool`, `path`, `paths`, `ints`, `strs`.

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
    discovered by a plant whose ERP has a status called `Hold, pending QA`."""
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
    if key.kind == "ints":
        # An empty list is a real answer for every key of this kind the
        # product has - `hold_rules = []` is *draw every rule and hold on
        # none of them* - so empty text parses to the empty list rather than
        # refusing.
        out: list[int] = []
        for part in written.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError:
                raise ValueError(
                    f"{part!r} is not a whole number. This key is a list of them, "
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
            elif key.kind == "ints":
                # A setting is text. The list is written as a comma list
                # rather than as Python's own repr, so the value a person
                # reads in `fsmes info` is the value they wrote in the pack.
                if not isinstance(value, list):
                    continue  # already refused by the checker; never guessed at here
                value = ",".join(str(int(item)) for item in value)
            elif key.kind == "strs":
                if not isinstance(value, list):
                    continue  # already refused by the checker; never guessed at here
                value = ",".join(str(item).strip() for item in value)
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
