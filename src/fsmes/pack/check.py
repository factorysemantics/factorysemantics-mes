"""`fsmes pack check`: refuse a pack before it ever touches a plant.

No database, no network, no plant. It reads the directory and says, one
sentence per problem, everything wrong with it - and it lists every problem
rather than the first, because a person fixing a pack on a plant PC should
need one round trip, not six.

What it cannot prove without a plant - that the OPC endpoint answers, that
the database is reachable - it reports as **unknown**, never as passing. That
is house rule 2 at the configuration boundary, and the same discipline
`fsmes erp check` follows.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from fsmes import __version__, identity
from fsmes import modules as module_registry
from fsmes.pack import format as fmt

#: Display terms a plant may rename. Short and enumerated on purpose: a
#: `[words]` key that is not here is a typo, and a typo that silently renames
#: nothing is exactly what the registry did for two weeks. Grow it when a
#: plant asks for a word, one line per word.
#:
#: Two obvious candidates are deliberately absent. `equipment` is the name of
#: a field in every inbound event and every namespace payload, and `operator`
#: is the name of a built-in role; a word that is both a label on a screen and
#: an identifier a number is keyed on is exactly the ambiguity decision 0022
#: clause 3 exists to stop. A plant that wants another word for a machine
#: renames `machine`, which is a label and nothing else.
RENAMEABLE: dict[str, str] = {
    "work order": "the order a plant runs - job, batch ticket, works order",
    "operation": "one step of a routing - step, task",
    "routing": "the sequence of operations - process plan, method",
    "material": "a thing with a code and a unit - part, item, SKU",
    "lot": "a quantity of one material with an identity - batch, heat",
    "serial": "one unit with an identity of its own - unit, tag number",
    "machine": "a piece of equipment - asset, resource, station",
    "work center": "the line or cell a machine belongs to - line, cell",
    "shift": "the working period the calendar is drawn in",
    "non-conformance": "a recorded failure against a specification - defect, reject",
    "gauge": "a measuring instrument under calibration - instrument",
    "maintenance order": "a job on a machine rather than on a product",
    "work instruction": "the controlled document at the station - SOP, procedure",
}


@dataclass(frozen=True)
class Problem:
    """One thing wrong, in one sentence, with where it is written."""

    where: str
    says: str

    def __str__(self) -> str:
        return f"{self.where}: {self.says}"


@dataclass(frozen=True)
class Unknown:
    """One thing this check could not prove without a plant. Never counted as
    passing: `fsmes pack check` says how many there are, every time."""

    what: str
    why: str

    def __str__(self) -> str:
        return f"{self.what} - {self.why}"


@dataclass(frozen=True)
class Report:
    directory: Path
    problems: tuple[Problem, ...]
    unknowns: tuple[Unknown, ...]
    checked: int
    """How many files the check actually read, so a report that has quietly
    stopped reading is visible rather than reassuring."""

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self) -> list[str]:
        lines = []
        for problem in self.problems:
            lines.append(f"  NOT OK  {problem}")
        for unknown in self.unknowns:
            lines.append(f"  unknown {unknown}")
        return lines


# ------------------------------------------------------------ protected words


def protected_terms() -> dict[str, str]:
    """Every term a pack may not rename, read from the product rather than
    typed here, mapped to what it is.

    Decision 0022 clause 3: `[words]` renames a label on a screen. It may not
    rename a state, a KPI, a capability, a role, an audit action, an MCP tool,
    an event `kind`, or any field an API response or an MQTT topic is built
    from - because two plants whose events mean different things are two
    plants a fleet view is comparing as though they meant the same, and that
    is house rule 1 applied to a company rather than to a machine.

    Built by import, so a state or a capability added next month is protected
    the day it lands. `tests/test_pack_format.py` holds it to that.
    """
    from fsmes.domain.equipment import EquipmentStateName
    from fsmes.domain.masterdata import EquipmentLevel, MaterialType
    from fsmes.domain.workorders import OrderStatus
    from fsmes.integrations import events
    from fsmes.integrations.inbound.contract import EVENT_TYPES
    from fsmes.services.capabilities import BUILTIN_ROLES, CAPABILITIES

    terms: dict[str, str] = {}

    def add(kind: str, *values) -> None:
        for value in values:
            terms.setdefault(str(value), kind)

    add("an equipment state", *EquipmentStateName)
    add("an order status", *OrderStatus)
    add("an equipment level", *EquipmentLevel)
    add("a material type", *MaterialType)
    add("a capability", *CAPABILITIES)
    add("a role", *BUILTIN_ROLES)
    add("an inbound event kind", *EVENT_TYPES)
    add("a domain event kind", *[
        model.model_fields["kind"].default for model in
        (events.EquipmentStateChange, events.OrderHold, events.OrderResume)])
    add("a module name", *module_registry.ALL_NAMES)
    add("a KPI", "oee", "availability", "performance", "quality_rate", "scrap")
    add("a field every reader is built on", "plant", "shadow", "profile", "timezone",
        "kind", "state", "status", "code", "actor", "equipment", "quantity")
    return terms


# ------------------------------------------------------------------- version


_COMPARISON = re.compile(r"\A(>=|<=|==|!=|>|<)\s*([0-9]+(?:\.[0-9]+)*)\Z")


def _release(text: str) -> tuple[int, ...]:
    """The numeric part of a version, as a tuple. `0.1.2.dev3` is `(0, 1, 2)`:
    a pre-release of a version satisfies what that version satisfies, which is
    what a person running a checkout of the next release expects."""
    digits = re.match(r"\A[0-9]+(?:\.[0-9]+)*", text.strip())
    return tuple(int(p) for p in digits.group(0).split(".")) if digits else ()


def _pad(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[tuple, tuple]:
    width = max(len(left), len(right))
    return (left + (0,) * (width - len(left)), right + (0,) * (width - len(right)))


def satisfies(version: str, requires: str) -> bool | None:
    """Does `version` satisfy a `requires` string like `>=0.1.2,<0.3`?

    None when the requirement cannot be read at all, which the caller reports
    as a problem with the requirement rather than as a version mismatch.
    """
    here = _release(version)
    if not here:
        return None
    for clause in requires.split(","):
        clause = clause.strip()
        if not clause:
            continue
        match = _COMPARISON.match(clause)
        if not match:
            return None
        operator, wanted = match.group(1), _release(match.group(2))
        mine, theirs = _pad(here, wanted)
        ok = {">=": mine >= theirs, "<=": mine <= theirs, "==": mine == theirs,
              "!=": mine != theirs, ">": mine > theirs, "<": mine < theirs}[operator]
        if not ok:
            return False
    return True


# --------------------------------------------------------------------- check


def check(directory: Path, *, version: str = __version__) -> Report:
    """Everything wrong with the pack in this directory, as sentences.

    `version` is the product version the pack's `requires` is held against.
    It is a parameter so a test can ask what a different release would say
    about the same pack, which is the only honest way to pin a `requires`
    refusal.
    """
    pack = fmt.read(directory)
    problems: list[Problem] = []
    unknowns: list[Unknown] = []

    problems += _pack_section(pack, version)
    problems += _sections(pack)
    problems += _identity(pack)
    problems += _modules(pack)
    problems += _words(pack)
    files, file_problems, file_unknowns = _files(pack)
    problems += file_problems
    unknowns += file_unknowns
    problems += _accounts(pack)
    unknowns += _cannot_be_known(pack)

    return Report(directory=pack.directory, problems=tuple(problems),
                  unknowns=tuple(unknowns), checked=files)


def _pack_section(pack: fmt.Pack, version: str) -> list[Problem]:
    table = pack.table("pack")
    out: list[Problem] = []
    if "format" not in table:
        out.append(Problem("[pack] format", (
            f"missing. A pack states the format it is written in; this product speaks "
            f"format {fmt.FORMAT}.")))
    elif not isinstance(table["format"], int):
        out.append(Problem("[pack] format", "should be a whole number."))
    elif table["format"] > fmt.FORMAT:
        out.append(Problem("[pack] format", (
            f"is {table['format']}, and this product speaks format {fmt.FORMAT}. A pack "
            "from a later release is refused rather than half-understood; upgrade the "
            "product.")))
    elif table["format"] < fmt.FORMAT:
        out.append(Problem("[pack] format", (
            f"is {table['format']} and this product speaks {fmt.FORMAT}. Run "
            f"`fsmes pack migrate {pack.directory}` to bring it forward.")))

    requires = table.get("requires")
    if requires is None:
        out.append(Problem("[pack] requires", (
            "missing. A pack says which product versions it is written for, so a "
            "version that cannot honour it refuses instead of guessing.")))
    elif not isinstance(requires, str):
        out.append(Problem("[pack] requires", "should be a string such as `>=0.1.2`."))
    else:
        verdict = satisfies(version, requires)
        if verdict is None:
            out.append(Problem("[pack] requires", (
                f"cannot be read: {requires!r}. It is a comma-separated list of "
                "comparisons, each an operator and a version: `>=0.1.2`, `>=0.2,<0.3`.")))
        elif verdict is False:
            out.append(Problem("[pack] requires", (
                f"is {requires!r}, and this product is {version}. This pack is not "
                "written for this release.")))
    return out


def _sections(pack: fmt.Pack) -> list[Problem]:
    """Every table and key held against the schema. The refusal the registry
    could never make: an unknown key is an error, not a default nobody saw."""
    out: list[Problem] = []
    for name, value in pack.raw.items():
        section = fmt.BY_SECTION.get(name)
        if section is None:
            out.append(Problem(f"[{name}]", (
                f"is not a table this format has. The tables are "
                f"{', '.join(s.name for s in fmt.SCHEMA)}.")))
            continue
        if section.repeated:
            if not isinstance(value, list):
                out.append(Problem(f"[{name}]", "is an array of tables, written `[[accounts]]`."))
            continue
        if not isinstance(value, dict):
            out.append(Problem(f"[{name}]", "is a table."))
            continue
        if section.open_keys:
            continue
        known = {k.name: k for k in section.keys}
        for key, written in value.items():
            if key in known:
                out += _typed(f"[{name}] {key}", known[key], written)
                continue
            if key in fmt.REFUSED:
                out.append(Problem(f"[{name}] {key}", fmt.REFUSED[key]))
                continue
            out.append(Problem(f"[{name}] {key}", (
                f"is not a key this format has. `[{name}]` takes "
                f"{', '.join(sorted(known))}.")))
    for key in pack.raw:
        if key in fmt.REFUSED and key not in fmt.BY_SECTION:
            out.append(Problem(key, fmt.REFUSED[key]))
    return out


_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool, "path": str}


def _typed(where: str, key: fmt.Key, value) -> list[Problem]:
    wanted = _TYPES.get(key.kind, object)
    # bool is an int in Python and a plant that wrote `api_port = true` should
    # hear about it, so the bool case is excluded explicitly.
    if key.kind != "bool" and isinstance(value, bool):
        return [Problem(where, f"is a true/false value; {key.about[0].lower() + key.about[1:]}")]
    if not isinstance(value, wanted):
        return [Problem(where, f"should be a {key.kind}. {key.about}")]
    return []


def _identity(pack: fmt.Pack) -> list[Problem]:
    """Name, profile and clock, refused by exactly the code the plant itself
    refuses them with - so a pack that checks clean cannot fail at start-up."""
    table = pack.table("plant")
    out: list[Problem] = []
    name = table.get("name")
    if not isinstance(name, str) or not name:
        out.append(Problem("[plant] name", (
            "missing. A pack says which plant it is; a reader of this plant's health, "
            "metrics, backups or namespace events cannot tell otherwise.")))
        return out
    problem = identity.check(
        plant_name=name,
        plant_profile=str(table.get("profile") or "laptop"),
        plant_timezone=str(table.get("timezone") or ""))
    if problem:
        out.append(Problem("[plant]", problem))
    if not table.get("timezone"):
        out.append(Problem("[plant] timezone", (
            "missing. A pack states the zone its plant works in; leaving it out makes "
            "every shift boundary and every screen read in whatever zone the machine "
            "running the MES happens to be set to.")))
    return out


def _modules(pack: fmt.Pack) -> list[Problem]:
    out: list[Problem] = []
    for name, value in pack.table("modules").items():
        if not isinstance(value, bool):
            out.append(Problem(f"[modules] {name}", "is true or false."))
    try:
        module_registry.resolve(fmt.module_spec(pack))
    except module_registry.UnknownModule as exc:
        out.append(Problem("[modules]", str(exc).replace(
            f"{module_registry.SETTING} names", "names")))
    return out


def _words(pack: fmt.Pack) -> list[Problem]:
    protected = protected_terms()
    out: list[Problem] = []
    for term, value in pack.table("words").items():
        key = str(term)
        if key in protected:
            out.append(Problem(f"[words] {key!r}", (
                f"renames {protected[key]}, which a number depends on. `[words]` renames "
                "a label on a screen and in a report; renaming this would make two "
                "plants' events mean different things while still saying the same word, "
                "and a fleet reading both would be comparing dialects.")))
            continue
        if key not in RENAMEABLE:
            out.append(Problem(f"[words] {key!r}", (
                f"is not a display term this product knows. The terms a plant may rename "
                f"are: {', '.join(sorted(RENAMEABLE))}.")))
            continue
        if not isinstance(value, str) or not value.strip():
            out.append(Problem(f"[words] {key!r}", "is the word this plant uses instead."))
    return out


def _files(pack: fmt.Pack) -> tuple[int, list[Problem], list[Unknown]]:
    """Every file the pack names: it exists, and it passes its own validator.

    Its *own* validator, deliberately - the reader that consumes the file in
    production, not a second opinion written here that could come to disagree
    with it. A tag map that `fsmes pack check` accepts and the OPC agent then
    refuses would be worse than no check at all.
    """
    problems: list[Problem] = []
    unknowns: list[Unknown] = []
    read = 0
    inside = _must_stay_inside()
    for where, path in pack.referenced().items():
        if where in inside and not _within(pack.directory, path):
            problems.append(Problem(where, (
                f"points outside the pack, at {path}. Everything a person writes in a "
                "pack is inside it, by relative path - a pack that reaches out of its "
                "own directory is not one directory anybody can hand over.")))
            continue
        if not path.exists():
            if where in inside:
                problems.append(Problem(where, (
                    f"names {path.name}, which is not in this pack. Every file a pack names "
                    "is inside the pack, by relative path.")))
            else:
                # Generated line data. A plant that has not run its generator
                # yet is normal; a check that called that a failure would
                # refuse every fresh checkout.
                unknowns.append(Unknown(
                    f"{where} at {path}",
                    "it is not there yet. Line data is generated - `fsmes sim-generate` "
                    "writes it - and a pack names where it will be"))
            continue
        read += 1
        problems += _validate(where, path)

    masterdata = pack.table("files").get("masterdata")
    if isinstance(masterdata, str) and masterdata and pack.path(masterdata).is_dir():
        from fsmes.pack import masterdata as data

        for problem in data.problems(pack.path(masterdata)):
            problems.append(Problem("[files] masterdata", problem))
        read += len(list(pack.path(masterdata).glob("*.json")))
    elif not masterdata:
        unknowns.append(Unknown(
            "master data",
            "this pack carries none, so `fsmes pack apply` seeds nothing. A plant "
            "whose master data is generated or comes from an ERP runs that itself"))
    return read, problems, unknowns


def _must_stay_inside() -> set[str]:
    """The path keys that may not leave the pack directory."""
    return {f"[{section.name}] {key.name}"
            for section in fmt.SCHEMA for key in section.keys
            if key.kind == "path" and key.inside}


def _within(directory: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def _validate(where: str, path: Path) -> list[Problem]:
    if path.is_dir():
        return []
    try:
        if path.name.endswith("tag_map.json") or where.endswith("tag_map"):
            from fsmes.integrations.opc.tag_map import load_tag_map

            load_tag_map(path)
        elif where == "[inbound] mapping":
            from fsmes.integrations.inbound.folder import load_mapping

            load_mapping(path)
        elif where == "[inbound] sql":
            from fsmes.integrations.inbound.sql import load_streams

            load_streams(path)
        elif path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [Problem(where, f"{path.name} is not usable: {exc}")]
    return []


def _accounts(pack: fmt.Pack) -> list[Problem]:
    from fsmes.services.capabilities import BUILTIN_ROLES

    out: list[Problem] = []
    wanted = {k.name for k in fmt.BY_SECTION["accounts"].keys}
    for index, account in enumerate(pack.accounts(), start=1):
        where = f"[[accounts]] #{index}"
        for key in account:
            if key in fmt.REFUSED:
                out.append(Problem(f"{where} {key}", fmt.REFUSED[key]))
            elif key not in wanted:
                out.append(Problem(f"{where} {key}", (
                    f"is not a key an account has. An account takes {', '.join(sorted(wanted))}.")))
        for key in sorted(wanted):
            if not account.get(key):
                out.append(Problem(f"{where} {key}", "is missing."))
        role = account.get("role")
        if role and role not in BUILTIN_ROLES:
            out.append(Problem(f"{where} role", (
                f"is {role!r}. The built-in roles are {', '.join(sorted(BUILTIN_ROLES))}; a "
                "role this plant defined itself is created by an administrator, not by a "
                "pack.")))
    return out


def _cannot_be_known(pack: fmt.Pack) -> list[Unknown]:
    """What a check with no plant behind it cannot answer. Said out loud every
    time, because a report that lists only what it proved reads as a report
    that proved everything."""
    out = []
    endpoint = pack.table("serve").get("opc_endpoint")
    if endpoint:
        out.append(Unknown(f"the OPC endpoint {endpoint}",
                           "nothing here connects to it; `fsmes opc-verify` does"))
    url = pack.table("storage").get("database_url")
    if url:
        out.append(Unknown("the database",
                           "nothing here connects to it; `fsmes db-status` does"))
    secret_env = pack.table("serve").get("secret_key_env")
    if secret_env:
        out.append(Unknown(f"the environment variable {secret_env}",
                           "whether it is set where this plant runs is a fact about that "
                           "machine, not about this pack"))
    for account in pack.accounts():
        if account.get("password_env"):
            out.append(Unknown(f"the environment variable {account['password_env']}",
                               f"account {account.get('code')} refuses to be created "
                               "without it, where this plant runs"))
    return out
