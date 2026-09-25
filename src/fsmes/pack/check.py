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
    problems += _coverage_floor(pack)
    problems += _quality_numbers(pack)
    problems += _erp_numbers(pack)
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


_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool, "path": str,
          "ints": list, "strs": list}


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


def _coverage_floor(pack: fmt.Pack) -> list[Problem]:
    """`[oee] coverage_floor` is a share, and the ends of the range are not
    both allowed.

    Zero is refused rather than read as "no floor": a pack that means no floor
    leaves the key out, and a plant that typed zero meant something and should
    be told the key does not do it. One is allowed and is a real answer - a
    plant that will not report a KPI unless it watched the whole window - but
    anything above one asks for more of a window than a window has.
    """
    value = pack.table("oee").get("coverage_floor")
    if value is None or isinstance(value, bool):
        return []  # absent, or already refused by the type check
    if not isinstance(value, int | float):
        return []
    if not 0 < float(value) <= 1:
        return [Problem("[oee] coverage_floor", (
            f"is {value}, and it is a share of a window: greater than 0 and at most 1. "
            "Leave the key out to withhold nothing - that is what no floor means, and "
            "coverage is printed beside every figure either way."))]
    return []


def _rule_numbers(where: str, value: list) -> list[Problem]:
    """A list of Western Electric rule numbers, checked. Two keys hold one -
    which rules raise a hold, and which of those are major - and they are the
    same list of four either way.

    The rule *numbers* are the product's and always will be: a plant that
    renumbered them would publish `SpcSignal.rule = 3` meaning something
    nobody else means by rule 3. So either key may only ever narrow the set
    {1, 2, 3, 4}, and a number outside it is a typo rather than a preference.

    An empty list is allowed and is a real answer - *record and draw every
    rule, raise a hold on none of them* - because a plant running SPC as an
    observation is a plant, not a mistake. It is never silent: the chart says
    which rules raise a hold on this plant whatever the list holds.
    """
    out: list[Problem] = []
    seen: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            out.append(Problem(where, (
                f"holds {item!r}. It is a list of Western Electric rule numbers, "
                "each a whole number from 1 to 4.")))
            continue
        if item not in (1, 2, 3, 4):
            out.append(Problem(where, (
                f"names rule {item}, and this product has four: 1 (a point beyond "
                "three sigma), 2 (two of three beyond two sigma), 3 (four of five "
                "beyond one sigma) and 4 (eight in a row on one side of centre). "
                "The numbering is the product's, because a plant that renumbered "
                "the rules would publish a rule number meaning something nobody "
                "else means by it.")))
        elif item in seen:
            out.append(Problem(where, (
                f"names rule {item} twice. Once is what it means either way, and a "
                "list a person has to read twice is a list worth tidying.")))
        seen.add(item)
    return out


#: `[quality]` keys that are a count or a measurement, and the range each has
#: to be in for the thing it decides to mean anything. `(low, high, why)`;
#: `high` is None where the only wrong answer is a number at or below the
#: floor. Every default the product ships is inside its own range, and a test
#: holds it to that - a range that refused the shipped value would be a range
#: that refused a plant for behaving as the product does.
QUALITY_RANGES: dict[str, tuple[float, float | None, str]] = {
    "cpk_capable": (0, None, "a Cpk bar is a positive number"),
    "cpk_marginal": (0, None, "a Cpk bar is a positive number"),
    "spc_min_points": (2, None,
                       "control limits come from a mean moving range, which "
                       "needs at least two readings to have a range at all"),
    "spc_history": (1, None, "a chart reads at least one reading"),
    "gauge_ratio_adequate": (0, None, "a resolution ratio is a positive number"),
    "gauge_ratio_floor": (0, None, "a resolution ratio is a positive number"),
    "gauge_default_interval_days": (0, None,
                                    "a calibration interval is a positive "
                                    "number of days"),
    "coa_serials_listed": (0, None,
                           "a certificate lists at least one serial, or the "
                           "key is doing nothing a reader can see"),
    "serial_digits": (1, 12, "a serial number carries between one and twelve digits"),
    "containment_max_depth": (1, 12,
                              "it is also the guard that stops a containment "
                              "walk running away, so the product keeps a hard "
                              "ceiling of twelve"),
}

#: What a non-conformance code may start with. Short, because the number's
#: own width comes after it and the whole thing lives in a twenty-character
#: column; upper case and plain, because it is printed on certificates and
#: read back by people.
NC_PREFIX = re.compile(r"\A[A-Z][A-Z0-9]{0,9}\Z")


def _quality_numbers(pack: fmt.Pack) -> list[Problem]:
    """`[quality]`'s counts and measurements, as a pack file carries them."""
    return quality_numbers(pack.table("quality"))


def quality_numbers(table: dict) -> list[Problem]:
    """`[quality]`'s counts and measurements, and the two pairs that have to
    stay in order.

    Ranges rather than opinions: this refuses a number that would make the
    thing it decides meaningless, and it refuses nothing else. A plant that
    wants twenty-five readings behind its limits, or a four-to-one gauge
    floor, is answering its own question and is not being second-guessed here.

    Takes the table rather than the pack so that **one setting edited on the
    Configuration page is judged by exactly the rules a pack file is judged
    by, in the same words** - including the two pairs, which can only be
    checked against the value the plant is already running on for the other
    half. A second copy of these ranges behind an input would be a screen that
    accepted a Cpk pair `fsmes pack check` refuses.
    """
    out: list[Problem] = []

    for name, (low, high, why) in QUALITY_RANGES.items():
        value = table.get(name)
        if value is None or isinstance(value, bool) or not isinstance(value, int | float):
            continue  # absent, or already refused by the type check
        if value <= low or (high is not None and value > high):
            bound = f"above {low}" if high is None else f"between {low} and {high}"
            out.append(Problem(f"[quality] {name}", f"is {value}, and it has to be {bound}: {why}."))

    # The two pairs. Each is one judgment written as two numbers, and the
    # numbers crossing over does not refuse anything - it quietly makes one of
    # them unreachable, which is worse.
    for lower, upper, sentence in (
            ("cpk_marginal", "cpk_capable",
             "a process cannot be marginal at a higher Cpk than it is capable "
             "at; nothing would ever be called marginal"),
            ("gauge_ratio_floor", "gauge_ratio_adequate",
             "a gauge cannot be too coarse at a finer ratio than it is "
             "adequate at; nothing would ever be called usable but marginal")):
        low_value, high_value = table.get(lower), table.get(upper)
        if not all(isinstance(v, int | float) and not isinstance(v, bool)
                   for v in (low_value, high_value)):
            continue
        if low_value >= high_value:
            out.append(Problem(f"[quality] {lower}", (
                f"is {low_value} and `{upper}` is {high_value}. {sentence[0].upper()}"
                f"{sentence[1:]}.")))

    prefix = table.get("nc_code_prefix")
    if isinstance(prefix, str) and not NC_PREFIX.match(prefix):
        out.append(Problem("[quality] nc_code_prefix", (
            f"is {prefix!r}. It is one to ten characters, upper case, starting "
            "with a letter - it is printed on certificates and read back by "
            "people, and the number's own width comes after it in a "
            "twenty-character column.")))

    for name in ("hold_rules", "major_rules"):
        rules = table.get(name)
        if isinstance(rules, list):
            out += _rule_numbers(f"[quality] {name}", rules)
    return out


#: `[erp]` keys that are a count or a measurement, and the range each has to
#: be in for the thing it decides to mean anything. Read exactly as
#: `QUALITY_RANGES` is - `(low, high, why)`, `high` is None where the only
#: wrong answer is a number at or below the floor - and every default the
#: product ships is inside its own range, which a test holds.
ERP_RANGES: dict[str, tuple[float, float | None, str]] = {
    "max_attempts": (0, None,
                     "a confirmation is offered at least once, or nothing is "
                     "ever delivered and nothing is ever called dead"),
    "base_backoff_s": (0, None,
                       "a backoff is a wait, and a wait of nothing is a loop "
                       "against an ERP that has just failed"),
    "max_backoff_s": (0, None, "the ceiling on a wait is itself a wait"),
    "float_rel_tol": (0, 1, "a relative agreement is a fraction of the number "
                            "sent, so between nothing and the whole of it"),
    "float_abs_tol": (0, None, "an absolute agreement is a positive amount"),
    "http_timeout": (0, None, "a timeout is how long this plant waits, and "
                              "waiting for no time is not trying"),
    "rest_timeout": (0, None, "a timeout is how long this plant waits, and "
                              "waiting for no time is not trying"),
    "default_order_priority": (0, None,
                               "priority is a positive number and lower is "
                               "more urgent"),
    "confirmation_seconds_tolerance": (0, None,
                                       "a slack of nothing calls every file "
                                       "wrong that carries whole seconds"),
}


def _erp_numbers(pack: fmt.Pack) -> list[Problem]:
    """`[erp]`'s counts and measurements, as a pack file carries them."""
    return erp_numbers(pack.table("erp"))


def erp_numbers(table: dict) -> list[Problem]:
    """`[erp]`'s counts and measurements, and the one pair that has to stay in
    order.

    Takes the table rather than the pack for the reason `quality_numbers`
    does: **one setting edited on the Configuration page is judged by exactly
    the rules a pack file is judged by, in the same words** - including the
    pair, which can only be checked against the value the plant is already
    running on for the other half.

    Ranges rather than opinions. A plant that waits two minutes on its ERP, or
    gives up after three attempts because somebody watches the outbox, is
    answering its own question and is not second-guessed here.
    """
    out: list[Problem] = []

    for name, (low, high, why) in ERP_RANGES.items():
        value = table.get(name)
        if value is None or isinstance(value, bool) or not isinstance(value, int | float):
            continue  # absent, or already refused by the type check
        if value <= low or (high is not None and value > high):
            bound = f"above {low}" if high is None else f"between {low} and {high}"
            out.append(Problem(f"[erp] {name}", f"is {value}, and it has to be {bound}: {why}."))

    # The one pair. A first wait longer than the ceiling on a wait does not
    # refuse anything - it quietly makes the ceiling the only wait there is,
    # which is worse than being told.
    base, ceiling = table.get("base_backoff_s"), table.get("max_backoff_s")
    if (isinstance(base, int | float) and not isinstance(base, bool)
            and isinstance(ceiling, int | float) and not isinstance(ceiling, bool)
            and base > ceiling):
        # Said under both names, not one. A pack file gets the same sentence
        # twice for one mistake, which is a little noisy; the Configuration
        # page keeps only the problems naming the key somebody just typed, so
        # a pair reported under one name alone would let the other half of it
        # be saved with nothing said.
        said = (f"[erp] base_backoff_s is {base:g} and [erp] max_backoff_s is "
                f"{ceiling:g}, so the first wait is already past the ceiling on a wait "
                "and every attempt is the same distance apart. A backoff that never "
                "backs off is a retry loop with extra words.")
        out.append(Problem("[erp] base_backoff_s", said))
        out.append(Problem("[erp] max_backoff_s", said))

    statuses = table.get("open_statuses")
    if isinstance(statuses, list):
        out += _open_statuses(statuses)
    return out


def _open_statuses(statuses: list) -> list[Problem]:
    """The ERP's own status names, as this plant writes them.

    The words themselves are never judged: they are the ERP's vocabulary and
    this MES does not hold a list of what ERPNext, SAP or Odoo may call a
    status. What is judged is whether each one is a word at all, and whether
    the list says anything - an empty list is *take no order in any status*,
    which is a plant whose order book never fills, and is refused here rather
    than discovered by a supervisor at the start of a shift.
    """
    out: list[Problem] = []
    where = "[erp] open_statuses"
    if not statuses:
        out.append(Problem(where, (
            "is empty, so no order in any status would ever be taken from the ERP and "
            "the order book would never fill. Leave the key out to use what the product "
            "ships (Not Started, In Process).")))
        return out
    seen: set[str] = set()
    for item in statuses:
        if not isinstance(item, str) or not item.strip():
            out.append(Problem(where, (
                f"holds {item!r}. It is a list of the ERP's own status names, each "
                "written as the ERP writes it.")))
            continue
        if "," in item:
            out.append(Problem(where, (
                f"holds {item!r}, which has a comma in it. This list is carried to the "
                "product as a comma-separated setting, so a status name with a comma "
                "inside it cannot survive the trip and would arrive as two.")))
        if item.strip() in seen:
            out.append(Problem(where, (
                f"names {item.strip()!r} twice. Once means the same thing, and a list a "
                "person has to read twice is a list worth tidying.")))
        seen.add(item.strip())
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
