"""A plant's master data as data, not as a script.

Until packs existed, a plant's equipment, materials and routings were a
Python file the registry named and `fsmes plant <name> init` ran as a
subprocess with the plant's environment. It worked, and it is the one thing
decision 0022 refuses outright: arbitrary code nobody can review before it
touches a plant, wearing a configuration file's name.

This is the replacement. One directory, one JSON file per kind, read and
written by `fsmes pack apply`:

    masterdata/
      equipment.json           the ISA-95 tree, parents first
      materials.json           what this plant makes and consumes
      bom.json                 which components a material takes, and where
      routings.json            the operations, in order, on named equipment
      quality_specs.json       what a characteristic must measure
      gauges.json              the instruments that take the measurements
      lots.json                material on hand at the start
      work_orders.json         the order book: what to make, in what order, what is released
      maintenance_plans.json   the recurring jobs, and what makes each due
      shifts.json              the patterns this plant works
      downtime_reasons.json    the reasons an operator picks from when it stops
      nc_severities.json       the plant's own words for how bad a finding is
      personnel.json           the people on the books, and where each is based
      skills.json              the trades this plant recognises
      personnel_skills.json    who holds which trade, and how well
      roster.json              who is on which shift, and who is away
      dispatch_rules.json      how maintenance work is handed out, in order

The last five arrived on 2026-10-09, with the maintenance crew. A plant that
knows its machines and not its electricians cannot hand a filler's electrical
fault to an electrician, and which trades a plant employs, who holds them and
what the supervisor's rules are is exactly the kind of thing that must be
config and not code (house rule 4) - a plant that separates pipefitters from
mechanics adds a row and changes nothing else.

Three before them arrived on 2026-09-14, when the bottling lab plant's line
moved into a pack. It had been seeded by `fsmes seed-kepsim`, which builds a
bill of materials, six maintenance plans and two shift patterns as well as
the equipment and the routing - and a format that could not carry them would
have made "the same line, seeded the same way" a quieter plant than the one
it replaced. Extending the format was the honest half of that trade; the
alternative was a plant that silently lost its BOM and its calendar.

**Rated cycle times are not repeated here.** An equipment entry may leave
`ideal_cycle_seconds` out, and it is read from the pack's own tag map - which
is what the lab seeds already did by hand, and for the reason that matters:
OEE performance is ideal cycle x count / runtime, so a rate that disagrees
with the line that generated the data produces a number that means nothing.

**Applying is idempotent.** Anything already there by code is left exactly as
it is, and the receipt says how many of each kind were made and how many were
already present. It never updates: a pack that changed a routing an order has
already run against would rewrite history, and a pack has no business doing
that. `fsmes pack status` reports the drift instead.

**A key beginning with an underscore is a note, not a field.** `_why_needs_stop`
beside the column it explains, on the row it explains, in the plant's own file -
checked by nothing and applied by nothing. `line.json` and `floor.json` have
carried their reasons this way since they existed; master data is where the
reasons are most wanted and were hardest to put, because the alternative was
the sentence living in a different file from the value it is about.

**One named exception: a column that has never held a value.** A maintenance
plan that was written before 2026-10-09 has no trade on it and no priority,
because there was nowhere to put them - and the same is true of what the job
needs of the line, `needs_stop` and `window`, which arrived a day later. The
pack fills each pair in - only when *both* columns of that pair are empty,
only from a pack that states them, and reported separately in the receipt as
"classified" and "scheduled" so nobody has to guess what moved.
And one named table the pack only ever starts a plant off with: the
supervisor's dispatch rules. They are the one piece of master data a person
edits on a screen, so a pack applied to a plant that has any rule of its own
writes none - reported as "left_alone" - rather than putting back, code by
code, a rule somebody had removed on purpose.

That is not the pack overruling the plant; it is the plant answering a
question it was never asked. The alternative is every plant that existed
before the dispatcher upgrading into a dispatcher that knows no trades, and a
plant has no other way to classify a plan it already has.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

#: The files this reads, and what each holds. A file in the directory that is
#: not one of these is a problem: master data nobody reads is master data
#: somebody thinks is loaded.
KINDS: dict[str, str] = {
    "equipment": "the ISA-95 tree: enterprise, site, area, work centre, work unit",
    "materials": "what this plant makes and consumes",
    "bom": "which components a material takes, and at which operation",
    "routings": "the operations, in order, each on a named machine",
    "quality_specs": "what a characteristic must measure for a material",
    "gauges": "the instruments that take the measurements, and how often each is calibrated",
    "lots": "material on hand when the plant starts",
    "work_orders": "the order book: what to make, in what order, and what is released",
    "maintenance_plans": "the recurring jobs on a machine, and what makes each due",
    "shifts": "the shift patterns this plant works",
    "downtime_reasons": "the reasons an operator may choose from when a machine stops",
    "nc_severities": "the severities a non-conformance may be raised at",
    "personnel": "the people on this plant's books, and where each is based",
    "skills": "the trades this plant recognises",
    "personnel_skills": "who holds which trade, and how well",
    "roster": "who is on which shift, and who is away",
    "dispatch_rules": "how maintenance work is handed out, tried in order",
}

REQUIRED: dict[str, tuple[str, ...]] = {
    "equipment": ("code", "name", "level"),
    "materials": ("code", "name"),
    "bom": ("parent", "component", "quantity"),
    "routings": ("code", "name", "material", "operations"),
    "quality_specs": ("material", "characteristic"),
    "gauges": ("code", "name"),
    "lots": ("code", "material", "quantity"),
    "work_orders": ("code", "material", "quantity"),
    "maintenance_plans": ("code", "name", "equipment", "trigger", "interval"),
    "shifts": ("code", "name", "starts", "ends"),
    "downtime_reasons": ("code", "name"),
    "nc_severities": ("code", "name"),
    "personnel": ("code", "name"),
    "skills": ("code", "name"),
    "personnel_skills": ("person", "skill"),
    "roster": ("person", "shift"),
    "dispatch_rules": ("code", "name"),
}

OPTIONAL: dict[str, tuple[str, ...]] = {
    "equipment": ("parent", "ideal_cycle_seconds"),
    "materials": ("unit", "type", "counted_in_pieces"),
    "bom": ("operation_seq",),
    "routings": (),
    # `sample_size` is the sampling plan: how many pieces this
    # characteristic is inspected at a time. Left out, or 1, is one piece at
    # a time, which is what every pack written before this field existed
    # says. Decision 0040.
    "quality_specs": ("unit", "min", "max", "sample_size"),
    "gauges": ("kind", "location", "interval_days", "warn_days", "resolution",
               "calibrated_days_ago"),
    "lots": (),
    "work_orders": ("priority", "release", "due_in_hours"),
    # `skill` and `priority` arrived with the maintenance crew. A plan that
    # says neither is work anybody on shift can take, at routine - which is
    # the honest reading of a plan nobody has classified, and why every pack
    # written before the crew existed still applies unchanged.
    # `needs_stop` and `window` arrived with the crew that does the work. A
    # plan that says neither can be done while the machine runs, whenever -
    # which is what every plan written before these two fields existed says,
    # and why every pack written before them still applies unchanged.
    "maintenance_plans": ("instructions", "document_code", "expected_minutes",
                          "skill", "priority", "needs_stop", "window"),
    "shifts": ("days", "equipment"),
    "downtime_reasons": ("description",),
    "nc_severities": ("description",),
    "personnel": ("role", "home_equipment"),
    "skills": ("description",),
    "personnel_skills": ("level",),
    "roster": ("day", "available", "reason"),
    "dispatch_rules": ("supervisor", "equipment", "skill", "priority_at_least",
                       "strategy", "sequence", "active"),
}

#: What `[[maintenance_plans]] trigger` may say, and what each counts. Spelt
#: out here rather than deferred to the enum's own error, because a pack is
#: checked offline and the person fixing it is reading this file's sentences.
TRIGGERS: dict[str, str] = {
    "runtime_hours": "hours the machine actually ran",
    "calendar_days": "elapsed days, use or no use",
    "produced_qty": "units it has made",
}

#: What `[[maintenance_plans]] window` may say, and what each means on the
#: floor. Spelt out here for the same reason as the triggers: a pack is checked
#: offline and the person fixing it is reading this file's sentences.
WINDOWS: dict[str, str] = {
    "anytime": "whenever somebody is free; the machine can keep running",
    "between_orders": "only when the line is between orders",
    "end_of_shift": "only in the last stretch of the shift",
}


@dataclass(frozen=True)
class Data:
    directory: Path
    kinds: dict[str, list[dict]] = field(default_factory=dict)

    def rows(self, kind: str) -> list[dict]:
        return self.kinds.get(kind, [])

    @property
    def total(self) -> int:
        return sum(len(rows) for rows in self.kinds.values())


def read(directory: Path) -> Data:
    """Every master-data file in this directory. Missing files are missing,
    not empty: a pack that carries three of the six kinds is normal."""
    directory = Path(directory)
    kinds: dict[str, list[dict]] = {}
    for kind in KINDS:
        path = directory / f"{kind}.json"
        if not path.is_file():
            continue
        loaded = json.loads(path.read_text(encoding="utf-8"))
        kinds[kind] = loaded if isinstance(loaded, list) else []
    return Data(directory=directory, kinds=kinds)


def problems(directory: Path) -> list[str]:
    """Everything wrong with this directory, offline, as sentences.

    Structure only. Whether `RT-BRACKET` names a routing that makes sense for
    this plant is the plant's question; whether it names a material this pack
    also declares is this function's.
    """
    directory = Path(directory)
    out: list[str] = []
    for path in sorted(directory.glob("*.json")):
        if path.stem not in KINDS:
            out.append(f"{path.name} is not a kind of master data this product reads. "
                       f"The kinds are {', '.join(sorted(KINDS))}.")
    try:
        data = read(directory)
    except json.JSONDecodeError as exc:
        return [*out, f"{directory.name} holds JSON that will not parse: {exc}"]

    for kind, rows in data.kinds.items():
        if not isinstance(rows, list):
            out.append(f"{kind}.json is a list of entries.")
            continue
        allowed = set(REQUIRED[kind]) | set(OPTIONAL[kind])
        for index, row in enumerate(rows, start=1):
            where = f"{kind}.json #{index}"
            if not isinstance(row, dict):
                out.append(f"{where} is an entry with named fields.")
                continue
            for missing in (k for k in REQUIRED[kind] if row.get(k) in (None, "")):
                out.append(f"{where} has no {missing}.")
            # A key beginning with an underscore is a note to whoever reads
            # the pack next, not a field: `_why_needs_stop` beside the column
            # it explains. `line.json` and `floor.json` have carried their
            # reasons this way since they existed, and master data is where
            # the reasons are most wanted and were hardest to put - a plant's
            # own file is the only place a sentence about *this* plan can
            # live, and the alternative was the reason sitting in a different
            # file from the value, where nobody editing the value sees it.
            # `apply` reads named fields only, so a note can never change a plant.
            for unknown in sorted(k for k in set(row) - allowed
                                  if not k.startswith("_")):
                out.append(f"{where} carries {unknown!r}, which is not a field "
                           f"{kind} has. It takes {', '.join(sorted(allowed))}.")

    known_materials = {row.get("code") for row in data.rows("materials")}
    known_equipment = {row.get("code") for row in data.rows("equipment")}
    for kind, fields in (("routings", ("material",)), ("quality_specs", ("material",)),
                         ("lots", ("material",)), ("work_orders", ("material",)),
                         ("bom", ("parent", "component"))):
        for index, row in enumerate(data.rows(kind), start=1):
            for name in fields:
                value = row.get(name)
                if value and value not in known_materials:
                    out.append(f"{kind}.json #{index} names material {value!r}, which "
                               "materials.json does not declare.")
    # A gauge's numbers, offline, because every one of them is a claim about
    # whether a measurement can be believed. A resolution of zero would make
    # the rule-of-ten check divide by nothing, and a calibration interval of
    # zero days is a gauge that is overdue the instant it is registered.
    for index, row in enumerate(data.rows("gauges"), start=1):
        where = f"gauges.json #{index}"
        for name in ("interval_days", "warn_days", "resolution", "calibrated_days_ago"):
            value = row.get(name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                out.append(f"{where} has {name} {value!r}; that is a number.")
            elif name == "warn_days" and value < 0:
                out.append(f"{where} wants {value} days of warning; `warn_days` counts "
                           "the days before a gauge falls due, so it is never negative.")
            elif name == "calibrated_days_ago" and value < 0:
                out.append(f"{where} was last calibrated {value} days ago, which is in "
                           "the future. `calibrated_days_ago` is relative because a pack "
                           "is seeded whenever somebody builds the plant, and an absolute "
                           "date in one ages.")
            elif name in ("interval_days", "resolution") and value <= 0:
                out.append(f"{where} has {name} {value!r}; that is a positive number.")

    # The sampling plan, offline. A size this product has no constants for
    # would seed a specification whose chart cannot be drawn, and the plant
    # would find out when somebody opened the screen. `services.spc.SUBGROUP`
    # is the table; two to ten, or one piece at a time. Decision 0040.
    from fsmes.services.spc import MAX_SAMPLE_SIZE

    for index, row in enumerate(data.rows("quality_specs"), start=1):
        size = row.get("sample_size")
        if size is None:
            continue
        where = f"quality_specs.json #{index}"
        if isinstance(size, bool) or not isinstance(size, int):
            out.append(f"{where} has sample_size {size!r}; that is a whole number of "
                       f"pieces inspected at a time.")
        elif not 1 <= size <= MAX_SAMPLE_SIZE:
            out.append(f"{where} wants samples of {size}, which this product cannot "
                       f"chart. Leave it out (or write 1) for one piece at a time, or "
                       f"write 2 to {MAX_SAMPLE_SIZE} for a sample of that many.")

    for index, row in enumerate(data.rows("work_orders"), start=1):
        due = row.get("due_in_hours")
        if due is None:
            continue
        if isinstance(due, bool) or not isinstance(due, (int, float)) or due <= 0:
            out.append(f"work_orders.json #{index} is due in {due!r} hours; `due_in_hours` "
                       "is a positive number of hours after the pack is applied. A pack is "
                       "seeded whenever somebody builds the plant, so a due date in one is "
                       "relative or it is wrong the week after it was written.")
    for index, row in enumerate(data.rows("routings"), start=1):
        for step in row.get("operations") or []:
            if not isinstance(step, dict) or not step.get("equipment"):
                out.append(f"routings.json #{index} has an operation with no equipment.")
            elif step["equipment"] not in known_equipment:
                out.append(f"routings.json #{index} runs on {step['equipment']!r}, which "
                           "equipment.json does not declare.")
    for index, row in enumerate(data.rows("equipment"), start=1):
        parent = row.get("parent")
        if parent and parent not in known_equipment:
            out.append(f"equipment.json #{index} hangs under {parent!r}, which "
                       "equipment.json does not declare.")
    for index, row in enumerate(data.rows("maintenance_plans"), start=1):
        machine = row.get("equipment")
        if machine and machine not in known_equipment:
            out.append(f"maintenance_plans.json #{index} is a job on {machine!r}, which "
                       "equipment.json does not declare.")
        trigger = row.get("trigger")
        if trigger and trigger not in TRIGGERS:
            out.append(f"maintenance_plans.json #{index} comes due on {trigger!r}, which "
                       f"is not something this product counts. It counts "
                       f"{', '.join(sorted(TRIGGERS))}.")
    for index, row in enumerate(data.rows("shifts"), start=1):
        machine = row.get("equipment")
        if machine and machine not in known_equipment:
            out.append(f"shifts.json #{index} belongs to {machine!r}, which "
                       "equipment.json does not declare.")
        for field_name in ("starts", "ends"):
            if not _is_clock(row.get(field_name)):
                out.append(f"shifts.json #{index} has a {field_name} of "
                           f"{row.get(field_name)!r}; a shift starts and ends at a time "
                           "of day written HH:MM.")
        days = row.get("days")
        if days is not None and (not isinstance(days, str) or len(days) != 7
                                 or set(days) - {"0", "1"}):
            out.append(f"shifts.json #{index} has days {days!r}; that is a seven "
                       'character mask of 0 and 1, Monday first - "1111100" is weekdays.')
    # A downtime code is checked here, offline, by the same two rules the
    # product enforces when a person drafts one: the shape, because the code
    # is grouped on and published rather than read as a sentence, and the
    # protected words, because a reason that spells a capability, a state or
    # a KPI means two things at once. A pack seeds its vocabulary straight
    # into the plant, so a pack that skipped these would be the one way round
    # them.
    if data.rows("downtime_reasons"):
        from fsmes.pack.check import protected_terms
        from fsmes.services.reasons import CODE

        owned = protected_terms()
        for index, row in enumerate(data.rows("downtime_reasons"), start=1):
            code = row.get("code")
            if code and not CODE.match(str(code)):
                out.append(f"downtime_reasons.json #{index} has code {code!r}; a code is "
                           "two to forty characters, starts with a lowercase letter, and "
                           "holds lowercase letters, digits and underscores.")
            elif code and code in owned:
                out.append(f"downtime_reasons.json #{index} names {code!r}, which is "
                           f"already {owned[code]} in this product.")

    # The same two rules for the severity vocabulary, plus one only it has: a
    # pack that seeds a list must seed the words the product's own code paths
    # write. `services/quality.py` opens a minor non-conformance when a check
    # falls out of specification and `services/spc.py` opens a major one for
    # rule 1, so a plant whose list lacked either would find out at the moment
    # a machine raised a hold. Checked here, offline, where a person is
    # reading and can fix it.
    if data.rows("nc_severities"):
        from fsmes.pack.check import protected_terms
        from fsmes.services.severities import CODE as SEVERITY_CODE
        from fsmes.services.severities import PRODUCT_WRITES

        owned = protected_terms()
        seeded = set()
        for index, row in enumerate(data.rows("nc_severities"), start=1):
            code = row.get("code")
            if code:
                seeded.add(str(code))
            if code and not SEVERITY_CODE.match(str(code)):
                out.append(f"nc_severities.json #{index} has code {code!r}; a code is "
                           "two to twenty characters, starts with a lowercase letter, "
                           "and holds lowercase letters, digits and underscores.")
            elif code and code in owned:
                out.append(f"nc_severities.json #{index} names {code!r}, which is "
                           f"already {owned[code]} in this product.")
        for code, written in PRODUCT_WRITES:
            if code not in seeded:
                out.append(f"nc_severities.json has no {code!r}, which this product "
                           f"writes itself: {written}. A plant seeded with a list "
                           "missing it would refuse at the moment a machine raised a "
                           "hold. Seed it - the name and the sentence beside it are "
                           "yours to write.")

    # The crew, offline. Every one of these is a claim about a person, and a
    # roster row naming somebody the pack never declares would seed a shift
    # with a hole in it that nothing later tells anybody about.
    from fsmes.domain import LEVEL_EXPERT, LEVEL_TRAINEE, DispatchStrategy

    known_people = {row.get("code") for row in data.rows("personnel")}
    known_skills = {row.get("code") for row in data.rows("skills")}
    known_shifts = {row.get("code") for row in data.rows("shifts")}

    for index, row in enumerate(data.rows("personnel"), start=1):
        home = row.get("home_equipment")
        if home and home not in known_equipment:
            out.append(f"personnel.json #{index} is based at {home!r}, which "
                       "equipment.json does not declare.")
    for index, row in enumerate(data.rows("personnel_skills"), start=1):
        where = f"personnel_skills.json #{index}"
        person, skill = row.get("person"), row.get("skill")
        if person and person not in known_people:
            out.append(f"{where} gives a trade to {person!r}, which personnel.json "
                       "does not declare.")
        if skill and skill not in known_skills:
            out.append(f"{where} names trade {skill!r}, which skills.json does not "
                       "declare.")
        level = row.get("level")
        if level is not None and (isinstance(level, bool) or not isinstance(level, int)
                                  or not LEVEL_TRAINEE <= level <= LEVEL_EXPERT):
            out.append(f"{where} has level {level!r}; a level is "
                       f"{LEVEL_TRAINEE} (trainee, works watched), 2 (competent, works "
                       f"alone - the default, and what dispatch sends) or "
                       f"{LEVEL_EXPERT} (expert, signs it off).")
    for index, row in enumerate(data.rows("roster"), start=1):
        where = f"roster.json #{index}"
        person, shift = row.get("person"), row.get("shift")
        if person and person not in known_people:
            out.append(f"{where} rosters {person!r}, which personnel.json does not "
                       "declare.")
        if shift and shift not in known_shifts:
            out.append(f"{where} puts somebody on shift {shift!r}, which shifts.json "
                       "does not declare.")
        day = row.get("day")
        if day is not None and not _is_day(day):
            out.append(f"{where} is for day {day!r}; that is a date written "
                       "YYYY-MM-DD. Leave it out for a standing assignment - on this "
                       "shift whenever it runs - which is what a pack usually means, "
                       "because a pack is applied whenever somebody builds the plant "
                       "and a date written into one is in the past the week after.")
        if row.get("available") is not None and not isinstance(row["available"], bool):
            out.append(f"{where} has available {row['available']!r}; that is true or "
                       "false.")
    for index, row in enumerate(data.rows("dispatch_rules"), start=1):
        where = f"dispatch_rules.json #{index}"
        node = row.get("equipment")
        if node and node not in known_equipment:
            out.append(f"{where} is about {node!r}, which equipment.json does not "
                       "declare.")
        skill = row.get("skill")
        if skill and skill not in known_skills:
            out.append(f"{where} wants trade {skill!r}, which skills.json does not "
                       "declare.")
        strategy = row.get("strategy")
        if strategy and strategy not in {s.value for s in DispatchStrategy}:
            out.append(f"{where} chooses by {strategy!r}, which is not a way this "
                       "product chooses. It chooses by "
                       f"{', '.join(sorted(s.value for s in DispatchStrategy))}.")
        least = row.get("priority_at_least")
        if least is not None and (isinstance(least, bool) or not isinstance(least, int)
                                  or not 1 <= least <= 3):
            out.append(f"{where} catches priority {least!r} or worse; a priority is "
                       "1 (safety), 2 (production-critical) or 3 (routine).")
        sequence = row.get("sequence")
        if sequence is not None and (isinstance(sequence, bool)
                                     or not isinstance(sequence, int)):
            out.append(f"{where} has sequence {sequence!r}; that is a whole number, "
                       "and the rules are tried lowest first.")
        if row.get("active") is not None and not isinstance(row["active"], bool):
            out.append(f"{where} has active {row['active']!r}; that is true or false.")
    for index, row in enumerate(data.rows("maintenance_plans"), start=1):
        where = f"maintenance_plans.json #{index}"
        skill = row.get("skill")
        if skill and skill not in known_skills:
            out.append(f"{where} needs trade {skill!r}, which skills.json does not "
                       "declare.")
        priority = row.get("priority")
        if priority is not None and (isinstance(priority, bool)
                                     or not isinstance(priority, int)
                                     or not 1 <= priority <= 3):
            out.append(f"{where} is priority {priority!r}; a priority is 1 (safety), "
                       "2 (production-critical) or 3 (routine). Leave it out for work "
                       "nobody has classified, which is read as routine.")
        stops = row.get("needs_stop")
        if stops is not None and not isinstance(stops, bool):
            out.append(f"{where} has needs_stop {stops!r}; that is true or false - "
                       "true when the machine has to be stopped for the job.")
        window = row.get("window")
        if window is not None and window not in WINDOWS:
            out.append(f"{where} may be done {window!r}, which is not a window this "
                       f"product knows. It knows "
                       f"{', '.join(f'{k} ({v})' for k, v in WINDOWS.items())}. "
                       "Leave it out when nobody has said when.")

    # A component that is its own parent is a bill of materials that never
    # terminates, and the explosion would recurse until something gave way.
    for index, row in enumerate(data.rows("bom"), start=1):
        if row.get("parent") and row.get("parent") == row.get("component"):
            out.append(f"bom.json #{index} makes {row['parent']!r} a component of itself.")
    return out


def _is_day(value) -> bool:
    """`YYYY-MM-DD`, and nothing else. A roster row for "next Tuesday" is a
    row this product cannot place on a calendar."""
    from datetime import date as _date

    if not isinstance(value, str):
        return False
    try:
        _date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_clock(value) -> bool:
    """`HH:MM` or `HH:MM:SS`, and nothing else. A shift that starts at "6am"
    parses in no library this product uses."""
    if not isinstance(value, str):
        return False
    try:
        time.fromisoformat(value)
    except ValueError:
        return False
    return True


# ----------------------------------------------------------------- applying


def seed(session, directory: Path, cycles: dict[str, float] | None = None) -> dict[str, dict]:
    """Write this pack's master data, and say what was made and what was there.

    Idempotent by code: an entry whose code already exists is counted as
    present and left alone, never updated.
    """
    from datetime import date, timedelta

    from sqlalchemy import select

    from fsmes import identity
    from fsmes.db import utcnow
    from fsmes.domain import (
        LEVEL_COMPETENT,
        BomItem,
        DispatchRule,
        DispatchStrategy,
        DowntimeReason,
        DowntimeReasonStatus,
        Equipment,
        EquipmentLevel,
        Gauge,
        MaintenancePlan,
        MaintenanceWindow,
        Material,
        MaterialLot,
        MaterialType,
        NcSeverity,
        Person,
        PersonnelSkill,
        QualitySpec,
        RosterEntry,
        Routing,
        RoutingOperation,
        ShiftPattern,
        Skill,
        TriggerKind,
        WorkOrder,
    )
    from fsmes.domain.common import VocabularyStatus
    from fsmes.services import calendar as calendar_service
    from fsmes.services import maintenance, masterdata, workorders

    data = read(directory)
    cycles = cycles or {}
    receipt: dict[str, dict] = {kind: {"made": 0, "present": 0} for kind in data.kinds}
    # Only the one kind that has a third outcome carries a third counter, so
    # every other kind's receipt reads exactly as it always has.
    if "maintenance_plans" in receipt:
        receipt["maintenance_plans"]["classified"] = 0
        receipt["maintenance_plans"]["scheduled"] = 0
    if "dispatch_rules" in receipt:
        # A rule the pack did not write because this plant writes its own.
        # Not "present": it is not there, and saying it was would be the
        # receipt telling a reader their rule had arrived.
        receipt["dispatch_rules"]["left_alone"] = 0

    def count(kind: str, made: bool) -> None:
        receipt[kind]["made" if made else "present"] += 1

    equipment: dict[str, Equipment] = {}
    for row in data.rows("equipment"):
        code = row["code"]
        existing = session.scalar(select(Equipment).where(Equipment.code == code))
        if existing is not None:
            equipment[code] = existing
            count("equipment", made=False)
            continue
        made = Equipment(
            code=code,
            name=row["name"],
            level=EquipmentLevel(row["level"]),
            parent=equipment.get(row.get("parent")),
            ideal_cycle_seconds=row.get("ideal_cycle_seconds", cycles.get(code)),
        )
        session.add(made)
        equipment[code] = made
        count("equipment", made=True)

    # The people, before anything that gives them a trade or a shift. A
    # tradesperson seeded from a pack gets no password, which is deliberate:
    # `Person.password_hash` null means "cannot sign in", and an electrician
    # who never opens the MES should be on its books without an account
    # somebody has to manage. Accounts are `[[accounts]]` in plant.toml and
    # want a password from the environment; this is the other half.
    people: dict[str, object] = {}
    for row in data.rows("personnel"):
        code = row["code"]
        existing = session.scalar(select(Person).where(Person.code == code))
        if existing is not None:
            people[code] = existing
            count("personnel", made=False)
            continue
        people[code] = masterdata.create_person(
            session, code=code, name=row["name"], role=row.get("role", "operator"),
            home_equipment=row.get("home_equipment"), actor="pack-apply")
        count("personnel", made=True)

    for row in data.rows("skills"):
        code = row["code"]
        if session.scalar(select(Skill.id).where(Skill.code == code)):
            count("skills", made=False)
            continue
        session.add(Skill(code=code, name=row["name"],
                          description=row.get("description")))
        count("skills", made=True)

    materials: dict[str, Material] = {}
    for row in data.rows("materials"):
        code = row["code"]
        existing = session.scalar(select(Material).where(Material.code == code))
        if existing is not None:
            materials[code] = existing
            count("materials", made=False)
            continue
        made = Material(code=code, name=row["name"], unit=row.get("unit", "ea"),
                        type=MaterialType(row.get("type", "raw")),
                        # Whether a pallet certificate counts this material in
                        # pieces. The pack says so; the product used to guess
                        # it from the shape of the code.
                        counted_in_pieces=bool(row.get("counted_in_pieces", False)))
        session.add(made)
        materials[code] = made
        count("materials", made=True)

    for row in data.rows("bom"):
        parent, component = materials[row["parent"]], materials[row["component"]]
        seq = row.get("operation_seq")
        # The unique key is (parent, component, operation) - the same
        # component may legitimately be consumed at two stations - so the
        # "already there" question has to be asked with all three.
        session.flush()
        existing = session.scalar(select(BomItem.id).where(
            BomItem.parent_id == parent.id, BomItem.component_id == component.id,
            BomItem.operation_seq == seq))
        if existing:
            count("bom", made=False)
            continue
        session.add(BomItem(parent=parent, component=component,
                            quantity=row["quantity"], operation_seq=seq))
        count("bom", made=True)

    for row in data.rows("routings"):
        code = row["code"]
        if session.scalar(select(Routing.id).where(Routing.code == code)):
            count("routings", made=False)
            continue
        routing = Routing(code=code, name=row["name"], material=materials[row["material"]])
        routing.operations = [
            RoutingOperation(seq=step.get("seq", (i + 1) * 10), name=step["name"],
                             equipment=equipment[step["equipment"]])
            for i, step in enumerate(row["operations"])
        ]
        session.add(routing)
        count("routings", made=True)

    for row in data.rows("quality_specs"):
        material = materials[row["material"]]
        existing = session.scalar(select(QualitySpec.id).where(
            QualitySpec.material_id == material.id,
            QualitySpec.characteristic == row["characteristic"]))
        if existing:
            count("quality_specs", made=False)
            continue
        size = row.get("sample_size")
        session.add(QualitySpec(material=material, characteristic=row["characteristic"],
                                unit=row.get("unit", ""), min_value=row.get("min"),
                                max_value=row.get("max"),
                                sample_size=None if size is None else int(size)))
        count("quality_specs", made=True)

    # The instruments. A gauge the plant says was calibrated on a date keeps
    # that date; no `Calibration` row is invented behind it, because a pack
    # states what the plant knows and nobody here performed a calibration. A
    # register migrated into a new MES looks exactly like this: a last-done
    # date, and the certificate wherever the paperwork actually is.
    for row in data.rows("gauges"):
        code = row["code"]
        if session.scalar(select(Gauge.id).where(Gauge.code == code)):
            count("gauges", made=False)
            continue
        since = row.get("calibrated_days_ago")
        gauge = Gauge(
            code=code, name=row["name"], kind=row.get("kind", "general"),
            location=row.get("location"), resolution=row.get("resolution"),
            last_calibrated=(None if since is None
                             else identity.today() - timedelta(days=int(since))),
            **({} if row.get("interval_days") is None
               else {"interval_days": int(row["interval_days"])}),
            **({} if row.get("warn_days") is None
               else {"warn_days": int(row["warn_days"])}))
        session.add(gauge)
        count("gauges", made=True)

    for row in data.rows("lots"):
        code = row["code"]
        if session.scalar(select(MaterialLot.id).where(MaterialLot.code == code)):
            count("lots", made=False)
            continue
        session.add(MaterialLot(code=code, material=materials[row["material"]],
                                quantity=row["quantity"], original_quantity=row["quantity"]))
        count("lots", made=True)

    if data.rows("work_orders"):
        # The services below look orders up by code, so the rows above have to
        # be in the session's own view of the database before the first one.
        session.flush()
    applied_at = utcnow()
    for row in data.rows("work_orders"):
        code = row["code"]
        if session.scalar(select(WorkOrder.id).where(WorkOrder.code == code)):
            count("work_orders", made=False)
            continue
        # A due date in a pack has to be relative. A pack is seeded whenever
        # somebody builds the plant, and an absolute date written into a file
        # is in the past the week after it was written - which is a book
        # every order of which is late before the line has run a minute.
        due = row.get("due_in_hours")
        workorders.create(session, code=code, material_code=row["material"],
                          quantity=row["quantity"], priority=row.get("priority", 10),
                          due_date=(applied_at + timedelta(hours=float(due))
                                    if due is not None else None),
                          actor="pack-apply")
        if row.get("release", True):
            workorders.release(session, code, actor="pack-apply")
        count("work_orders", made=True)

    for row in data.rows("maintenance_plans"):
        code = row["code"]
        standing = session.scalar(
            select(MaintenancePlan).where(MaintenancePlan.code == code))
        if standing is not None:
            count("maintenance_plans", made=False)
            # The one place a pack writes to a row it did not make, and only
            # into two columns that have never held anything: a plan written
            # before the trades existed says nothing about which trade it
            # needs, and a plant has no other way to answer that about a plan
            # it already has. If either column has a value, the plant has had
            # its say and the pack keeps out of it.
            if (standing.skill_code is None and standing.priority is None
                    and (row.get("skill") is not None
                         or row.get("priority") is not None)):
                standing.skill_code = row.get("skill")
                standing.priority = row.get("priority")
                receipt["maintenance_plans"]["classified"] += 1
            # The same exception, the same rule, for what the job needs of the
            # line. `window` is the column that can say "nobody has answered",
            # because it is the nullable one, and `needs_stop` false is read
            # alongside it as not-yet-answered rather than as an answer - a
            # known limit of a boolean, and the reason both have to be untouched
            # before the pack says anything. A plant that has answered either
            # question on the screen keeps its answer.
            if (standing.window is None and standing.needs_stop is False
                    and (row.get("window") is not None
                         or row.get("needs_stop") is not None)):
                standing.needs_stop = bool(row.get("needs_stop", False))
                standing.window = (MaintenanceWindow(row["window"])
                                   if row.get("window") else None)
                receipt["maintenance_plans"]["scheduled"] += 1
            continue
        # A plan that says nothing about how long it takes gets this plant's
        # own house default rather than the product's thirty - the same
        # `[process] maintenance_plan_default_minutes` a person editing a plan
        # on the screen gets, because a pack and a screen creating the same
        # plan should not produce two different plans.
        stated = row.get("expected_minutes")
        session.add(MaintenancePlan(
            code=code, name=row["name"], equipment=equipment[row["equipment"]],
            trigger=TriggerKind(row["trigger"]), interval=float(row["interval"]),
            instructions=row.get("instructions"), document_code=row.get("document_code"),
            expected_minutes=(float(stated) if stated is not None
                              else maintenance.plan_default_minutes(session)),
            # Both left null when the pack says nothing. A plan with no trade
            # on it is work anybody on shift can take and a plan with no
            # priority is read as routine, which is the honest reading of a
            # plan nobody has classified - an invented priority is worse than
            # none, because it sorts.
            skill_code=row.get("skill"), priority=row.get("priority"),
            # And what it needs of the line. False and null when the pack says
            # nothing: a job that has not said it needs the machine stopped
            # does not get to stop it, and a window nobody has stated is read
            # as open rather than invented.
            needs_stop=bool(row.get("needs_stop", False)),
            window=MaintenanceWindow(row["window"]) if row.get("window") else None))
        count("maintenance_plans", made=True)

    # The vocabulary a plant starts with. It arrives **in force**, not as a
    # draft: applying a pack is a deliberate act by a person, and a plant
    # whose station screen offered nothing until somebody went and approved
    # six seeded rows would be a plant that shipped with a text box after all.
    # Every later change goes through draft → approve like everything else,
    # and the plant owns the list from its first edit.
    for row in data.rows("downtime_reasons"):
        code = row["code"]
        if session.scalar(select(DowntimeReason.id).where(DowntimeReason.code == code)):
            count("downtime_reasons", made=False)
            continue
        session.add(DowntimeReason(
            code=code, revision=1, name=row["name"], description=row.get("description", ""),
            status=DowntimeReasonStatus.APPROVED, created_by="pack-apply",
            approved_by="pack-apply", approved_at=utcnow()))
        count("downtime_reasons", made=True)

    # The severity vocabulary, on the same terms and for the same reason: a
    # plant whose quality screen graded nothing until somebody approved two
    # seeded rows would be a plant that shipped with a free-text column after
    # all. Every later change goes through draft -> approve.
    for row in data.rows("nc_severities"):
        code = row["code"]
        if session.scalar(select(NcSeverity.id).where(NcSeverity.code == code)):
            count("nc_severities", made=False)
            continue
        session.add(NcSeverity(
            code=code, revision=1, name=row["name"], description=row.get("description", ""),
            status=VocabularyStatus.APPROVED, created_by="pack-apply",
            approved_by="pack-apply", approved_at=utcnow()))
        count("nc_severities", made=True)

    for row in data.rows("shifts"):
        code = row["code"]
        if session.scalar(select(ShiftPattern.id).where(ShiftPattern.code == code)):
            count("shifts", made=False)
            continue
        # A pack that says nothing about which days a shift runs gets **this
        # plant's own working week**, not the product's Monday-to-Friday.
        #
        # The configuration audit of 2026-09-21 read this line and said the
        # honest fix might be to refuse rather than to seed silently, because
        # seeding five days was a guess about somebody else's plant. Since
        # 2026-09-25 it is not a guess: `[process] working_week_mask` is the
        # plant's own stated answer, and filling a missing field in from what
        # the plant has said is what every other default in this seeder does.
        # So it is not refused - refusing would break every pack that leaves
        # `days` out to mean *the usual week here* and would buy nothing the
        # key does not already buy.
        session.add(ShiftPattern(
            code=code, name=row["name"], starts=time.fromisoformat(row["starts"]),
            ends=time.fromisoformat(row["ends"]),
            days=row.get("days") or calendar_service.working_week_mask(session),
            # A shift with no equipment belongs to the whole site, which is
            # the column's own meaning for null - not a missing value.
            equipment=equipment.get(row["equipment"]) if row.get("equipment") else None))
        count("shifts", made=True)
        # A session that has already asked which shifts exist must not keep
        # the answer it got before this pack was loaded.
        session.info.pop(calendar_service._PATTERN_CACHE, None)

    # Who holds which trade, who is on which shift, and the supervisor's
    # rules - last, because each of the three points at a person, a trade or
    # a shift the lines above have just made.
    session.flush()
    for row in data.rows("personnel_skills"):
        person = people.get(row["person"]) or masterdata.get_person(session, row["person"])
        held = session.scalar(select(PersonnelSkill.id).where(
            PersonnelSkill.personnel_id == person.id,
            PersonnelSkill.skill_code == row["skill"]))
        if held:
            count("personnel_skills", made=False)
            continue
        session.add(PersonnelSkill(personnel_id=person.id, skill_code=row["skill"],
                                   level=int(row.get("level", LEVEL_COMPETENT))))
        count("personnel_skills", made=True)

    for row in data.rows("roster"):
        person = people.get(row["person"]) or masterdata.get_person(session, row["person"])
        # A row with no day is a **standing** assignment: on this shift
        # whenever it runs. That is what a pack almost always means - a pack
        # is applied whenever somebody builds the plant, so a dated row in one
        # is about a day in the past the week after it was written. A dated
        # row still works, and overrides the standing one for that day, which
        # is how an absence is written down.
        day = date.fromisoformat(row["day"]) if row.get("day") else None
        on = session.scalar(select(RosterEntry.id).where(
            RosterEntry.personnel_id == person.id,
            RosterEntry.shift_code == row["shift"],
            RosterEntry.shift_day == day))
        if on:
            count("roster", made=False)
            continue
        session.add(RosterEntry(personnel_id=person.id, shift_code=row["shift"],
                                shift_day=day, available=row.get("available", True),
                                reason=row.get("reason")))
        count("roster", made=True)

    # A rule is the supervisor's own sentence about where work goes, and the
    # one table here a person edits on a screen rather than in a file. So a
    # pack starts a plant off and then keeps out of it: if this plant has any
    # rule at all, the pack's rules are counted as present and nothing is
    # written. Matching code by code would quietly put back a rule a
    # supervisor had switched off and removed, the next time anybody applied
    # the pack - which is a plant's own decision overruled by a file.
    has_its_own = session.scalar(select(DispatchRule.id).limit(1)) is not None
    for row in data.rows("dispatch_rules"):
        code = row["code"]
        if session.scalar(select(DispatchRule.id).where(DispatchRule.code == code)):
            count("dispatch_rules", made=False)
            continue
        if has_its_own:
            receipt["dispatch_rules"]["left_alone"] += 1
            continue
        session.add(DispatchRule(
            code=code, name=row["name"], supervisor_code=row.get("supervisor"),
            equipment_code=row.get("equipment"), skill_code=row.get("skill"),
            priority_at_least=row.get("priority_at_least"),
            strategy=DispatchStrategy(row.get("strategy", DispatchStrategy.LEAST_LOADED)),
            active=row.get("active", True), sequence=int(row.get("sequence", 100))))
        count("dispatch_rules", made=True)
    return receipt
