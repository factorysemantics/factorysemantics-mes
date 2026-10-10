"""Maintenance work goes to the right trade by itself, by rules a supervisor
wrote down.

Three hundred skilled trades on three shifts and two thousand orders a week is
not a list anybody works through by hand, and it is not a list anybody should
have to. So when an order comes due the plant hands it to a free person with
the skill in the same second - and the supervisor can read, afterwards, exactly
why it went where it went.

HOW ONE ORDER IS DISPATCHED. Rules are tried in `sequence` order. The first
rule that matches the order *and* finds somebody wins. Matching is three
filters, any of which a rule may leave out: the machine (that node or anything
under it), the skill, and a priority floor. Finding somebody is the roster for
the shift this instant falls in, filtered to people who are available, hold the
skill well enough to work unwatched, and are not on another job at the time -
then narrowed to one by the rule's own strategy.

WHY RULES AND NOT A SOLVER. A supervisor who cannot read why the work went
where it went will not trust the dispatcher, and a dispatcher nobody trusts is
a dispatcher that gets switched off. Every decision here is a sentence:
`explain` prints it. The day a supervisor needs to say something a rule cannot
say is the day to reach for arithmetic, and not before.

WHAT IT NEVER DOES. It never touches an order a person assigned by hand - a
supervisor who reaches in and is overruled by the machine stops reaching in -
and it never *starts* work. Handing somebody a job and the job beginning are
two different facts, and a plant that conflated them would report downtime
that never happened.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from fsmes.db import utcnow
from fsmes.domain import (
    DEFAULT_PRIORITY,
    DISPATCHABLE_LEVEL,
    DispatchRule,
    DispatchStrategy,
    Equipment,
    EquipmentState,
    EquipmentStateName,
    MaintenanceOrder,
    MaintenancePlan,
    MaintenanceStatus,
    Person,
    PersonnelSkill,
    RosterEntry,
    Skill,
    UnassignedReason,
)
from fsmes.services import Conflict, Invalid, NotFound, audit, calendar, masterdata
from fsmes.services import maintenance as _maintenance

#: The rule a plant with **no rules at all** gets: any due order goes to a free
#: person with the skill on this shift, least loaded first. It is not a row - a
#: plant that has configured nothing has configured nothing, and a row nobody
#: added is a row a supervisor would not expect to be able to edit.
#:
#: It applies only while the table is empty. A plant that has written even one
#: rule has said what it wants, and an order none of its rules covers is
#: genuinely `no_rule` rather than quietly swept up by a default the supervisor
#: never wrote - which is the difference between a dispatcher a supervisor can
#: reason about and one that surprises them.
DEFAULT_RULE_CODE = "DEFAULT"
DEFAULT_RULE_NAME = "any due order to a free person with the skill on this shift"

#: How each strategy reads in the sentence. Hoisted out of `says` because a
#: screen offering the blanks has to offer these exact words: a page with its
#: own copy is a page whose dropdown and whose preview drift apart, and the
#: whole point of the sentence is that everything says it the same way.
STRATEGY_SAID = {
    DispatchStrategy.LEAST_LOADED: "whoever has least on",
    DispatchStrategy.NEAREST: "whoever is nearest the machine",
    DispatchStrategy.ROUND_ROBIN: "taking turns",
}

# --------------------------------------------------------------------- rules


def rules(session: Session) -> list[DispatchRule]:
    """The active rules in the order they are tried.

    A plant with none gets the house default, as one rule, so an unconfigured
    plant still dispatches. A plant with an inactive rule and nothing else gets
    the default too: a switched-off rule is a rule the supervisor is not using.
    """
    found = list(session.scalars(
        select(DispatchRule)
        .where(DispatchRule.active.is_(True))
        .order_by(DispatchRule.sequence, DispatchRule.code)))
    return found or [default_rule()]


def default_rule() -> DispatchRule:
    """The house default, as a rule object so everything downstream - matching,
    the strategy, the audit row, the explanation - has exactly one shape to
    read. Not added to the session: it is not this plant's row."""
    return DispatchRule(
        code=DEFAULT_RULE_CODE, name=DEFAULT_RULE_NAME, supervisor_code=None,
        equipment_code=None, skill_code=None, priority_at_least=None,
        strategy=DispatchStrategy.LEAST_LOADED, active=True, sequence=10_000)


def create_rule(session: Session, *, code: str, name: str,
                supervisor_code: str | None = None, equipment_code: str | None = None,
                skill_code: str | None = None, priority_at_least: int | None = None,
                strategy: str = DispatchStrategy.LEAST_LOADED.value,
                sequence: int = 100, active: bool = True,
                actor: str = "system") -> DispatchRule:
    """Write one of the supervisor's sentences down."""
    if code == DEFAULT_RULE_CODE:
        raise Invalid(
            f"{DEFAULT_RULE_CODE!r} is the house rule every plant falls back to "
            "and is not a row. Give yours a code of its own.")
    if session.scalar(select(DispatchRule).where(DispatchRule.code == code)):
        raise Conflict(f"dispatch rule {code} already exists")
    try:
        how = DispatchStrategy(strategy)
    except ValueError as exc:
        raise Invalid(
            f"unknown strategy {strategy!r}. Expected one of "
            f"{', '.join(s.value for s in DispatchStrategy)}") from exc
    if equipment_code:
        masterdata.get_equipment(session, equipment_code)      # 404 rather than a dead rule
    if skill_code:
        get_skill(session, skill_code)
    rule = DispatchRule(
        code=code, name=name, supervisor_code=supervisor_code,
        equipment_code=equipment_code, skill_code=skill_code,
        priority_at_least=priority_at_least, strategy=how,
        sequence=sequence, active=active)
    session.add(rule)
    session.flush()
    audit.record(session, actor=actor, action="maintenance.rule_written",
                 entity_type="dispatch_rule", entity_id=code,
                 after=rule_as_json(rule))
    return rule


def rule_as_json(rule: DispatchRule) -> dict:
    """One rule as the sentence it is, with the parts beside it."""
    return {
        "code": rule.code, "name": rule.name, "sequence": rule.sequence,
        "supervisor": rule.supervisor_code, "equipment": rule.equipment_code,
        "skill": rule.skill_code, "priority_at_least": rule.priority_at_least,
        "strategy": rule.strategy.value, "active": rule.active,
        "says": says(rule),
    }


def says(rule: DispatchRule) -> str:
    """The rule as a supervisor would say it out loud."""
    how = STRATEGY_SAID[rule.strategy]
    said = f"Work on {rule.equipment_code}" if rule.equipment_code else "Work anywhere in the plant"
    # A trade reads as part of the work - "work on the filler needing ELEC".
    # Everything else is an aside, and asides are closed on both sides, or the
    # sentence turns into "work on SIMLINE whatever the skill, goes to ...".
    asides = []
    if rule.skill_code:
        said += f" needing {rule.skill_code}"
    else:
        asides.append("whatever the skill")
    if rule.priority_at_least is not None:
        asides.append(f"priority {rule.priority_at_least} or worse")
    if asides:
        said += ", " + ", ".join(asides) + ","
    return f"{said} goes to somebody on this shift, {how}."


# ------------------------------------------- the sentence, written and edited
#
# A supervisor does not name a rule. They say a sentence with four blanks in
# it - the machine, the trade, the priority, how to choose - and the code, the
# name and the row are made from what they said. Everything in this section
# exists so that the words on the screen, the words in `says`, the words the
# CLI prints and the words in the audit row are one set of words.


#: The blanks, as the vocabulary a screen fills its `<select>`s from. Published
#: by `GET /maintenance/rules` so the page offers exactly the words this module
#: understands and no screen has to keep a second copy of them.
def vocabulary(session: Session) -> dict:
    """The words the blanks may be filled with, from the plant that has them."""
    return {
        "equipment": [{"code": e.code, "name": e.name}
                      for e in session.scalars(select(Equipment).order_by(Equipment.code))],
        "skills": [{"code": s.code, "name": s.name} for s in
                   session.scalars(select(Skill).order_by(Skill.code))],
        # 1 safety, 2 production-critical, 3 routine. Said as the words a
        # supervisor uses, because "priority 2 or worse" is the sentence and
        # "2" on its own is a number off a form.
        "priorities": [{"value": 1, "name": "safety"},
                       {"value": 2, "name": "production-critical"},
                       {"value": 3, "name": "routine"}],
        "strategies": [{"value": s.value, "says": STRATEGY_SAID[s]}
                       for s in DispatchStrategy],
    }


#: The JSON names of the blanks, against the column each one sets. A PATCH body
#: is checked against this rather than against a second list of strings, so a
#: blank nobody can edit cannot be edited by naming its column.
RULE_FIELDS = {
    "equipment": "equipment_code",
    "skill": "skill_code",
    "priority_at_least": "priority_at_least",
    "strategy": "strategy",
    "sequence": "sequence",
    "active": "active",
    "name": "name",
    "supervisor": "supervisor_code",
}


def get_rule(session: Session, code: str) -> DispatchRule:
    """One written rule, or a 404 that says what this plant does have."""
    rule = session.scalar(select(DispatchRule).where(DispatchRule.code == code))
    if rule is None:
        known = [r.code for r in session.scalars(select(DispatchRule).order_by(DispatchRule.code))]
        if code == DEFAULT_RULE_CODE:
            raise NotFound(
                f"{DEFAULT_RULE_CODE!r} is the house rule a plant with no rules "
                "falls back to, not a row, so there is nothing to edit or remove.")
        raise NotFound(
            f"no dispatch rule {code!r} on this plant. It has {len(known)}: "
            f"{', '.join(known) or 'none at all'}.")
    return rule


def next_sequence(session: Session) -> int:
    """Where a rule nobody has placed goes: last, out of the way of the ones
    already working. Tens, so there is always room to put one between two."""
    last = session.scalar(select(func.max(DispatchRule.sequence)))
    return int(last or 0) + 10


def code_for(session: Session, *, equipment_code: str | None, skill_code: str | None,
             priority_at_least: int | None, strategy: DispatchStrategy) -> str:
    """A code made out of the sentence, so there is nothing to name.

    Readable in a list and in an audit row - `FILL01-ELEC-NEAR` is the rule
    about electrical work on the filler going to whoever is nearest - and
    unique, with a number on the end when a plant writes the same sentence
    twice. The column is forty characters, and the stem is cut to leave room.
    """
    short = {DispatchStrategy.LEAST_LOADED: "LEAST",
             DispatchStrategy.NEAREST: "NEAR",
             DispatchStrategy.ROUND_ROBIN: "TURNS"}[strategy]
    parts = [equipment_code or "ANY", skill_code or "ANY"]
    if priority_at_least is not None:
        parts.append(f"P{priority_at_least}")
    parts.append(short)
    stem = "-".join(parts)[:36].rstrip("-")
    taken = {c for c in session.scalars(select(DispatchRule.code))}
    if stem not in taken and stem != DEFAULT_RULE_CODE:
        return stem
    for n in range(2, 100):
        candidate = f"{stem}-{n}"
        if candidate not in taken:
            return candidate
    raise Conflict(f"a hundred rules already read {stem}; give this one a code of its own")


def write_rule(session: Session, *, equipment_code: str | None = None,
               skill_code: str | None = None, priority_at_least: int | None = None,
               strategy: str = DispatchStrategy.LEAST_LOADED.value,
               code: str | None = None, name: str | None = None,
               supervisor_code: str | None = None, sequence: int | None = None,
               active: bool = True, actor: str = "system",
               dry_run: bool = False) -> dict:
    """The blanks in, a rule out - or, with `dry_run`, only the sentence.

    `dry_run` is how a screen shows the finished sentence while a supervisor is
    still choosing: the preview is the server's own `says` of the rule it would
    write, not a second rendering kept in a page script that could drift from
    this one word by word.
    """
    try:
        how = DispatchStrategy(strategy)
    except ValueError as exc:
        raise Invalid(
            f"unknown strategy {strategy!r}. Expected one of "
            f"{', '.join(s.value for s in DispatchStrategy)}") from exc
    if priority_at_least is not None and not 1 <= int(priority_at_least) <= 3:
        raise Invalid("priority is 1 (safety), 2 (production-critical) or 3 (routine)")
    if equipment_code:
        masterdata.get_equipment(session, equipment_code)
    if skill_code:
        get_skill(session, skill_code)

    sequence = sequence if sequence is not None else next_sequence(session)
    code = code or code_for(session, equipment_code=equipment_code,
                            skill_code=skill_code,
                            priority_at_least=priority_at_least, strategy=how)
    # Built before anything is written, because the name *is* the sentence and
    # `create_rule` needs it at the moment it audits the row. A rule nobody
    # named is not nameless: it is called what it does, cut to the column, so a
    # supervisor reading a list of names is reading a list of sentences.
    draft = DispatchRule(
        code=code, name="", supervisor_code=supervisor_code,
        equipment_code=equipment_code, skill_code=skill_code,
        priority_at_least=priority_at_least, strategy=how,
        active=active, sequence=sequence)
    draft.name = name or says(draft)[:160]

    if dry_run:
        # Nothing is written and no code is reserved, so this is the rule this
        # sentence would become if it were saved this second. Said as such.
        out = rule_as_json(draft)
        out["dry_run"] = True
        return out

    rule = create_rule(
        session, code=code, name=draft.name, supervisor_code=supervisor_code,
        equipment_code=equipment_code, skill_code=skill_code,
        priority_at_least=priority_at_least, strategy=how.value,
        sequence=sequence, active=active, actor=actor)
    return rule_as_json(rule)


def update_rule(session: Session, code: str, changes: dict, *,
                actor: str = "system") -> dict:
    """Change a blank, switch a rule off, or move it in the order.

    Only the blanks named in `RULE_FIELDS`, and a blank set to null is cleared -
    which is how "needing ELEC" becomes "whatever the skill" without deleting
    and rewriting the rule and losing what it has already handed out.

    The audit row carries the sentence before and the sentence after, because
    that is what a supervisor would want to read six weeks later: not that
    `skill_code` went from `ELEC` to null, but that the rule stopped being
    about electricians.
    """
    rule = get_rule(session, code)
    before = rule_as_json(rule)
    unknown = sorted(set(changes) - set(RULE_FIELDS))
    if unknown:
        raise Invalid(
            f"nothing on a rule is called {', '.join(unknown)}. The blanks are "
            f"{', '.join(sorted(RULE_FIELDS))}.")
    if not changes:
        raise Invalid(
            "nothing to change. Name one of "
            f"{', '.join(sorted(RULE_FIELDS))} - a blank sent as null is "
            "cleared - or `move` to reorder it.")

    if "strategy" in changes:
        try:
            changes["strategy"] = DispatchStrategy(changes["strategy"])
        except ValueError as exc:
            raise Invalid(
                f"unknown strategy {changes['strategy']!r}. Expected one of "
                f"{', '.join(s.value for s in DispatchStrategy)}") from exc
    if (changes.get("priority_at_least") is not None
            and not 1 <= int(changes["priority_at_least"]) <= 3):
        raise Invalid("priority is 1 (safety), 2 (production-critical) or 3 (routine)")
    if changes.get("equipment"):
        masterdata.get_equipment(session, changes["equipment"])
    if changes.get("skill"):
        get_skill(session, changes["skill"])
    if "active" in changes and changes["active"] is None:
        raise Invalid("a rule is on or off; `active` cannot be nothing")
    if "name" in changes and not changes["name"]:
        raise Invalid("a rule reads as its sentence; clear a blank, not the name")

    named = "name" in changes
    for field_name, column in RULE_FIELDS.items():
        if field_name in changes:
            setattr(rule, column, changes[field_name])
    if not named:
        # The name follows the sentence unless somebody wrote their own, so a
        # rule edited through the blanks never reads as the rule it used to be.
        rule.name = says(rule)[:160]
    session.flush()
    audit.record(session, actor=actor, action="maintenance.rule_changed",
                 entity_type="dispatch_rule", entity_id=rule.code,
                 before=before, after=rule_as_json(rule))
    return rule_as_json(rule)


def move_rule(session: Session, code: str, direction: str, *,
              actor: str = "system") -> dict:
    """Up or down one place in the order the rules are tried.

    One call, not two sequence writes, because the order is the thing the
    supervisor is changing and a page that sent two numbers could leave two
    rules on the same one. Every rule is renumbered in tens afterwards, so the
    arrows keep working however the sequences started.
    """
    if direction not in ("up", "down"):
        raise Invalid("a rule moves `up` or `down`")
    rule = get_rule(session, code)
    order = list(session.scalars(select(DispatchRule).order_by(
        DispatchRule.sequence, DispatchRule.code)))
    was = [r.code for r in order]
    at = was.index(rule.code)
    to = at - 1 if direction == "up" else at + 1
    if not 0 <= to < len(order):
        edge = "first" if direction == "up" else "last"
        raise Conflict(f"{rule.code} is already tried {edge}")
    order[at], order[to] = order[to], order[at]
    for place, row in enumerate(order, start=1):
        row.sequence = place * 10
    session.flush()
    now = [r.code for r in order]
    audit.record(session, actor=actor, action="maintenance.rules_reordered",
                 entity_type="dispatch_rule", entity_id=rule.code,
                 before={"tried_in_order": was},
                 after={"tried_in_order": now, "moved": rule.code,
                        "says": says(rule), "sequence": rule.sequence})
    return rule_as_json(rule)


def handed_out_by(session: Session, code: str) -> int:
    """How many orders this rule has handed out, ever."""
    return int(session.scalar(
        select(func.count()).select_from(MaintenanceOrder)
        .where(MaintenanceOrder.assigned_by == code)) or 0)


def delete_rule(session: Session, code: str, *, actor: str = "system") -> dict:
    """Remove a rule that never did anything.

    A rule that has handed work out is refused, with the count and the way to
    do what was meant: switch it off. Every one of those orders says
    `assigned_by = <this code>`, and an audit trail that points at a rule
    nobody can look up is an audit trail that has stopped being one.
    """
    rule = get_rule(session, code)
    handed = handed_out_by(session, code)
    if handed:
        raise Conflict(
            f"{code} has handed out {handed} order"
            f"{'' if handed == 1 else 's'}, and each of them still names it as "
            "what sent it. Switch the rule off instead - it stops being tried "
            "and the trail keeps pointing somewhere.")
    before = rule_as_json(rule)
    session.delete(rule)
    session.flush()
    audit.record(session, actor=actor, action="maintenance.rule_removed",
                 entity_type="dispatch_rule", entity_id=code, before=before)
    return {"removed": code, "was": before}

# -------------------------------------------------------------------- skills


def get_skill(session: Session, code: str) -> Skill:
    skill = session.scalar(select(Skill).where(Skill.code == code))
    if skill is None:
        known = [s.code for s in session.scalars(select(Skill).order_by(Skill.code))]
        raise NotFound(
            f"no skill {code!r} on this plant. It has {len(known)}: "
            f"{', '.join(known) or 'none at all'}.")
    return skill


def create_skill(session: Session, *, code: str, name: str,
                 description: str | None = None, actor: str = "system") -> Skill:
    """Add a trade this plant employs. Config, not code: a plant that keeps
    pipefitters separate from mechanics adds the row and changes nothing else."""
    if session.scalar(select(Skill).where(Skill.code == code)):
        raise Conflict(f"skill {code} already exists")
    skill = Skill(code=code, name=name, description=description)
    session.add(skill)
    session.flush()
    audit.record(session, actor=actor, action="maintenance.skill_added",
                 entity_type="skill", entity_id=code,
                 after={"name": name, "description": description})
    return skill


def skills(session: Session) -> list[dict]:
    """Every trade this plant recognises, and how many people hold it."""
    held = dict(session.execute(
        select(PersonnelSkill.skill_code, func.count())
        .group_by(PersonnelSkill.skill_code)).all())
    rows = list(session.scalars(select(Skill).order_by(Skill.code)))
    return [{"code": s.code, "name": s.name, "description": s.description,
             "people": int(held.get(s.code, 0))} for s in rows]


def grant_skill(session: Session, *, person_code: str, skill_code: str,
                level: int = DISPATCHABLE_LEVEL, actor: str = "system") -> PersonnelSkill:
    """Say that somebody can do something, and how well."""
    person = masterdata.get_person(session, person_code)
    get_skill(session, skill_code)
    if not 1 <= int(level) <= 3:
        raise Invalid("level is 1 (trainee), 2 (competent) or 3 (expert)")
    row = session.scalar(select(PersonnelSkill).where(
        PersonnelSkill.personnel_id == person.id,
        PersonnelSkill.skill_code == skill_code))
    before = {"level": row.level} if row else None
    if row is None:
        row = PersonnelSkill(personnel_id=person.id, skill_code=skill_code, level=int(level))
        session.add(row)
    else:
        row.level = int(level)
    session.flush()
    audit.record(session, actor=actor, action="maintenance.skill_granted",
                 entity_type="personnel", entity_id=person_code, before=before,
                 after={"skill": skill_code, "level": int(level)})
    return row


# -------------------------------------------------------------------- roster


def set_roster(session: Session, *, person_code: str, shift_code: str,
               day=None, available: bool = True, reason: str | None = None,
               actor: str = "system") -> RosterEntry:
    """Put somebody on a shift - standing, or for one day.

    `day` left out is a standing assignment: this person is on this shift
    whenever it runs. A day given is that day only, and wins over the standing
    row - which is how an absence or a cover shift is written down without
    rewriting the roster.
    """
    person = masterdata.get_person(session, person_code)
    row = session.scalar(select(RosterEntry).where(
        RosterEntry.personnel_id == person.id,
        RosterEntry.shift_code == shift_code,
        RosterEntry.shift_day.is_(None) if day is None else RosterEntry.shift_day == day))
    before = {"available": row.available, "reason": row.reason} if row else None
    if row is None:
        row = RosterEntry(personnel_id=person.id, shift_code=shift_code, shift_day=day,
                          available=available, reason=reason)
        session.add(row)
    else:
        row.available, row.reason = available, reason
    session.flush()
    audit.record(session, actor=actor, action="maintenance.rostered",
                 entity_type="personnel", entity_id=person_code, before=before,
                 after={"shift": shift_code, "day": day.isoformat() if day else None,
                        "available": available, "reason": reason})
    return row


# ------------------------------------------------------- one dispatcher's pass
#
# Everything below runs over a whole backlog at once, so it reads the plant
# four times rather than four times per order: the rules, the roster, who holds
# which skill, and what everybody already has on. Three hundred people and two
# thousand orders is a few thousand rows, which is a handful of queries and a
# dictionary - and the alternative, a query per order per rule, is what makes a
# dispatcher that works on a demo unusable on a plant.


@dataclass
class _Crew:
    """The plant's people, read once, in the shapes the picking needs."""

    by_id: dict[int, Person]
    #: The same people by code, because an assignment names a code and walking
    #: three hundred people to find one, two thousand times, is a walk.
    by_code: dict[str, Person] = field(default_factory=dict)
    skills: dict[int, dict[str, int]] = field(default_factory=dict)
    #: shift code -> standing roster rows; and (shift code, day) -> that day's.
    standing: dict[str, list[RosterEntry]] = field(default_factory=dict)
    dated: dict[tuple[str, object], list[RosterEntry]] = field(default_factory=dict)
    #: How many open orders each person holds, kept up to date as we assign.
    load: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    #: The windows each person is already occupied by: (start, end).
    busy: dict[int, list[tuple[datetime, datetime]]] = field(default_factory=lambda: defaultdict(list))
    #: How many orders each rule has ever given each person, for taking turns.
    turns: dict[tuple[str, int], int] = field(default_factory=lambda: defaultdict(int))
    #: Every machine's chain of ancestors, nearest first, for `nearest`.
    chain: dict[int, list[int]] = field(default_factory=dict)
    #: (shift code, day, skill, window) -> was anybody on that shift holding
    #: the skill, remembered **only when nobody was free**. Negative only, and
    #: that is the whole of the safety argument: inside one pass nobody ever
    #: becomes *less* busy, so "no electrician is free between nine and ten"
    #: cannot stop being true before the pass ends - while "MT-017 is free"
    #: stops being true the second MT-017 is handed the job. A plant whose
    #: electricians are all out would otherwise walk the same hundred people
    #: again for every one of nine hundred waiting orders.
    #:
    #: Used only by a dispatch pass. `explain` walks every time, because the
    #: walk is the entire reason anybody calls `explain`.
    nobody_free: dict[tuple, bool] = field(default_factory=dict)
    #: What each person is holding right now, for a page that has to show a
    #: crew rather than a count: the order they are on, or the next one they
    #: have been given. Only one, because a person works one job at a time and
    #: a supervisor's page asks "what is Mary doing", not "what is on Mary's
    #: list" - the count beside it answers the second question.
    on_now: dict[int, MaintenanceOrder] = field(default_factory=dict)
    #: Each plan's expected minutes, so a page can say how long the job on
    #: somebody's hands was meant to take beside how long it has taken.
    plan_minutes: dict[int | None, float] = field(default_factory=dict)
    #: (shift code, day) -> who is on it, with their person, sorted by code.
    #: Worked out the first time a shift is asked for and kept: one pass asks
    #: for the same three shifts once per order per rule.
    on_shift: dict[tuple[str, object], list[tuple[RosterEntry, Person]]] = field(
        default_factory=dict)


def _comes_first(order: MaintenanceOrder, against: MaintenanceOrder) -> bool:
    """Is `order` more the job somebody is on than `against` is?"""
    rank = {MaintenanceStatus.IN_PROGRESS: 0, MaintenanceStatus.ASSIGNED: 1}
    mine, theirs = rank.get(order.status, 2), rank.get(against.status, 2)
    if mine != theirs:
        return mine < theirs
    # `or utcnow()` rather than None-last sorting: an order with no times on it
    # at all is the newest thing that could have happened to this person, which
    # is the honest reading and keeps the comparison total.
    return (order.started_at or order.assigned_at or utcnow()) < (
        against.started_at or against.assigned_at or utcnow())


def _read_crew(session: Session) -> _Crew:
    # Where each person is based comes back with them: the roster says
    # "nearest the machine" and the screen prints their home line, and asking
    # the equipment table per person is a query per head.
    people = {p.id: p for p in session.scalars(
        select(Person).options(joinedload(Person.home_equipment)))}
    crew = _Crew(by_id=people, by_code={p.code: p for p in people.values()})

    for row in session.scalars(select(PersonnelSkill)):
        crew.skills.setdefault(row.personnel_id, {})[row.skill_code] = row.level

    for row in session.scalars(select(RosterEntry)):
        if row.shift_day is None:
            crew.standing.setdefault(row.shift_code, []).append(row)
        else:
            crew.dated.setdefault((row.shift_code, row.shift_day), []).append(row)

    assumed = _maintenance.default_job_minutes(session)
    # The machine and the plan come back with the order, because the roster
    # says what each person is on - "MT-04 on PM-00031 at M1-04 - change the
    # filter" - and reaching for them row by row is a query per busy person.
    # At a hundred on shift that was forty-eight queries for one roster read
    # (measured 2026-10-09); it is one.
    held = session.scalars(
        select(MaintenanceOrder)
        .options(joinedload(MaintenanceOrder.equipment), joinedload(MaintenanceOrder.plan))
        .where(MaintenanceOrder.status.in_(
            (MaintenanceStatus.ASSIGNED, MaintenanceStatus.IN_PROGRESS)))).unique().all()
    codes = {p.code: p.id for p in people.values()}
    plan_minutes = dict(session.execute(
        select(MaintenancePlan.id, MaintenancePlan.expected_minutes)).all())
    crew.plan_minutes = dict(plan_minutes)
    for order in held:
        person_id = codes.get(order.assigned_to or "")
        if person_id is None:
            continue
        crew.load[person_id] += 1
        # Which of this person's open orders is *the* one they are on. In
        # progress beats assigned, because that is the job in their hands; two
        # at the same status are ordered by when they were given out, because
        # a crew works its list in the order it arrived. One person, one job.
        standing = crew.on_now.get(person_id)
        if standing is None or _comes_first(order, standing):
            crew.on_now[person_id] = order
        minutes = plan_minutes.get(order.plan_id, assumed)
        if order.status is MaintenanceStatus.IN_PROGRESS:
            # On it now, and for as long as the job is expected to take from
            # when they picked it up. An order in progress with no start time
            # recorded is treated as started now, which is the only honest
            # reading of a row that says somebody is on it.
            start = order.started_at or order.assigned_at or utcnow()
        else:
            start = order.scheduled_for or order.assigned_at or utcnow()
        crew.busy[person_id].append((start, start + timedelta(minutes=minutes)))

    for rule_code, person_code, count in session.execute(
            select(MaintenanceOrder.assigned_by, MaintenanceOrder.assigned_to, func.count())
            .where(MaintenanceOrder.assigned_to.is_not(None))
            .group_by(MaintenanceOrder.assigned_by, MaintenanceOrder.assigned_to)).all():
        person_id = codes.get(person_code or "")
        if person_id is not None and rule_code:
            crew.turns[(rule_code, person_id)] += int(count)

    for equipment in session.scalars(select(Equipment)):
        crew.chain[equipment.id] = []
    parents = {e.id: e.parent_id for e in session.scalars(select(Equipment))}
    for equipment_id in list(crew.chain):
        chain, at, guard = [], parents.get(equipment_id), 0
        while at is not None and guard < 50:
            chain.append(at)
            at, guard = parents.get(at), guard + 1
        crew.chain[equipment_id] = chain
    return crew


def _scope_of(session: Session, rule: DispatchRule) -> set[int] | None:
    """The equipment ids a rule covers: that node and everything under it.

    None means the whole plant, which is a rule with no machine on it. A rule
    naming a machine that has since been removed covers nothing, and is said
    out loud rather than silently matching everything.
    """
    if not rule.equipment_code:
        return None
    node = session.scalar(select(Equipment).where(Equipment.code == rule.equipment_code))
    if node is None:
        return set()
    return {node.id} | {e.id for e in masterdata.descendants(session, node)}


def _matches(rule: DispatchRule, order: MaintenanceOrder, scope: set[int] | None) -> str | None:
    """None when the rule matches; otherwise why it did not, in plain words."""
    if scope is not None and order.equipment_id not in scope:
        return f"its machine is not under {rule.equipment_code}"
    if rule.skill_code and (order.skill_code or None) != rule.skill_code:
        return (f"it needs {order.skill_code or 'no particular skill'}, "
                f"not {rule.skill_code}")
    priority = order.priority or DEFAULT_PRIORITY
    if rule.priority_at_least is not None and priority > rule.priority_at_least:
        return (f"it is priority {priority}, below the rule's "
                f"{rule.priority_at_least}")
    return None


def _rostered(crew: _Crew, shift_code: str, day) -> list[tuple[RosterEntry, Person]]:
    """Who is on this shift: the standing roster, with the day's rows winning.

    Each row comes back beside the person it is about, sorted by person code,
    and the answer is kept on the crew. A roster row whose person is not on the
    register is dropped here rather than skipped in the picking, so the picking
    reads as the decision it is.
    """
    key = (shift_code, day)
    found = crew.on_shift.get(key)
    if found is None:
        dated = {row.personnel_id: row for row in crew.dated.get(key, ())}
        rows = [*dated.values(),
                *(row for row in crew.standing.get(shift_code, ())
                  if row.personnel_id not in dated)]
        found = sorted(((row, crew.by_id[row.personnel_id]) for row in rows
                        if row.personnel_id in crew.by_id),
                       key=lambda pair: pair[1].code)
        crew.on_shift[key] = found
    return found


def _free_at(crew: _Crew, person_id: int, window: tuple[datetime, datetime]) -> str | None:
    """None when they are free for that window, otherwise when they are not."""
    start, end = window
    for busy_start, busy_end in crew.busy.get(person_id, ()):
        if busy_start < end and start < busy_end:
            return f"on another job {busy_start:%H:%M}-{busy_end:%H:%M}"
    return None


def _distance(crew: _Crew, person: Person, equipment_id: int) -> int:
    """How far somebody's home station is from a machine, in hops up the tree.

    Zero is the machine itself. A person with no home station is as far away as
    the tree is deep - not excluded, because a home station grants nothing and
    restricts nothing (`Person.home_equipment_id`), just never *nearest*.
    """
    home = person.home_equipment_id
    if home is None:
        return 99
    if home == equipment_id:
        return 0
    up_from_order = [equipment_id, *crew.chain.get(equipment_id, ())]
    up_from_home = [home, *crew.chain.get(home, ())]
    for order_hops, node in enumerate(up_from_order):
        if node in up_from_home:
            return order_hops + up_from_home.index(node)
    return 99


@dataclass
class _Considered:
    person: str
    name: str
    verdict: str
    chosen: bool = False

    def as_json(self) -> dict:
        return {"person": self.person, "name": self.name,
                "verdict": self.verdict, "chosen": self.chosen}


def _choose(crew: _Crew, rule: DispatchRule, order: MaintenanceOrder,
            rostered: list[tuple[RosterEntry, Person]],
            window: tuple[datetime, datetime], *, collect: bool = True,
            ) -> tuple[Person | None, str, list[_Considered], bool]:
    """Who gets it, why, and everybody who was looked at on the way.

    The fourth value answers the question the unassigned reason turns on: was
    there *anybody* on shift who held the skill? Nobody on shift with the
    skill and everybody on shift already busy are two different facts, and a
    supervisor acts on them differently.

    `collect` is off for a dispatch pass and on for `explain`. The walk - a
    sentence per person per rule - is what a supervisor reads afterwards about
    *one* order; building it for two thousand orders against three hundred
    people is half a million sentences nobody asked for, and it is the
    difference between a pass that takes a quarter of a second and one that
    takes four.
    """
    looked: list[_Considered] = []
    eligible: list[Person] = []
    any_with_skill = False
    needed = order.skill_code or None

    for row, person in rostered:
        if not row.available:
            if collect:
                looked.append(_Considered(
                    person.code, person.name,
                    f"not available{f' ({row.reason})' if row.reason else ''}"))
            continue
        if needed:
            level = crew.skills.get(person.id, {}).get(needed)
            if level is None:
                if collect:
                    looked.append(_Considered(person.code, person.name,
                                              f"does not hold {needed}"))
                continue
            if level < DISPATCHABLE_LEVEL:
                if collect:
                    looked.append(_Considered(
                        person.code, person.name,
                        f"holds {needed} at level {level} - a trainee, not sent alone"))
                continue
        any_with_skill = True
        occupied = _free_at(crew, person.id, window)
        if occupied:
            if collect:
                looked.append(_Considered(person.code, person.name, occupied))
            continue
        eligible.append(person)
        if collect:
            looked.append(_Considered(person.code, person.name,
                                      "free, and holds the skill"))

    if not eligible:
        return None, "", looked, any_with_skill

    if rule.strategy is DispatchStrategy.NEAREST:
        best = min(eligible, key=lambda p: (_distance(crew, p, order.equipment_id),
                                            crew.load[p.id], p.code))
        why = (f"nearest the machine - {_distance(crew, best, order.equipment_id)} "
               f"hop(s) from {best.code}'s home station")
    elif rule.strategy is DispatchStrategy.ROUND_ROBIN:
        best = min(eligible, key=lambda p: (crew.turns[(rule.code, p.id)], p.code))
        why = (f"taking turns - this rule has sent {best.code} "
               f"{crew.turns[(rule.code, best.id)]} job(s) before")
    else:
        best = min(eligible, key=lambda p: (crew.load[p.id], p.code))
        why = f"least loaded - {best.code} has {crew.load[best.id]} open order(s)"

    for row in looked:
        if row.person == best.code:
            row.chosen = True
            row.verdict = why
    return best, why, looked, any_with_skill


def _at(now: datetime | None) -> datetime:
    """The instant to decide at, on the plant's own clock.

    Every timestamp this product stores is naive UTC (`fsmes.db.utcnow`), and
    so are the windows read back out of `maintenance_orders` to work out who is
    already out on a job. A caller handing in an *aware* datetime - a route
    parsing an ISO string with a `Z` on the end of it - would get away with it
    on a plant with nothing assigned and then fail on the second pass, when the
    first pass's windows come back from the database naive. That is the worst
    possible day to find out, so it is settled here, once, at the door.
    """
    now = now or utcnow()
    return now.astimezone(UTC).replace(tzinfo=None) if now.tzinfo else now


def _window(session: Session, order: MaintenanceOrder, now: datetime,
            plan_minutes: dict[int | None, float], assumed: float) -> tuple[datetime, datetime]:
    minutes = plan_minutes.get(order.plan_id, assumed)
    return now, now + timedelta(minutes=minutes)


@dataclass
class _Decision:
    """What happened to one order, kept so `explain` and `dispatch` agree."""

    order: str
    skill: str | None
    priority: int
    rules_tried: list[dict] = field(default_factory=list)
    considered: list[_Considered] = field(default_factory=list)
    rule: str | None = None
    rule_says: str | None = None
    assigned_to: str | None = None
    why: str | None = None
    shift: str | None = None
    unassigned_reason: str | None = None

    def as_json(self) -> dict:
        return {
            "order": self.order, "skill": self.skill, "priority": self.priority,
            "shift": self.shift,
            "rules_tried": self.rules_tried,
            "rule": self.rule, "rule_says": self.rule_says,
            "considered": [c.as_json() for c in self.considered],
            "assigned_to": self.assigned_to, "why": self.why,
            "unassigned_reason": self.unassigned_reason,
        }


def _decide(session: Session, order: MaintenanceOrder, now: datetime, crew: _Crew,
            prepared: list[tuple[DispatchRule, set[int] | None]],
            plan_minutes: dict, assumed: float, *, collect: bool = True,
            shifts: dict[int, object] | None = None) -> _Decision:
    """Walk the rules for one order and say what should happen to it.

    `shifts` is the shift each machine is on at `now`, worked out once and
    shared across the pass: the answer depends on the machine and the instant,
    and a dispatch pass has one instant. `collect` is handed straight to
    `_choose` - see there for why a pass does not build the walk.
    """
    decision = _Decision(order=order.code, skill=order.skill_code,
                         priority=order.priority or DEFAULT_PRIORITY)
    window = _window(session, order, now, plan_minutes, assumed)
    if shifts is None:
        shifts = {}
    if order.equipment_id not in shifts:
        shifts[order.equipment_id] = calendar.shift_for(session, now, order.equipment_id)
    shift = shifts[order.equipment_id]
    decision.shift = shift.key() if shift else None

    nobody_on_shift = True
    for rule, scope in prepared:
        missed = _matches(rule, order, scope)
        if missed is not None:
            decision.rules_tried.append({"rule": rule.code, "matched": False,
                                         "because": missed})
            continue
        if shift is None:
            decision.rules_tried.append({
                "rule": rule.code, "matched": True,
                "because": "no shift pattern covers this moment, so nobody is rostered"})
            continue
        # Who could take it does not depend on which rule is asking - only the
        # shift, the trade and the window - so a pass that has already found
        # nobody free for this exact three does not look again.
        seen = (crew.nobody_free.get((shift.code, shift.day, order.skill_code or None,
                                      window)) if not collect else None)
        if seen is not None:
            if seen:
                nobody_on_shift = False
            decision.rules_tried.append({
                "rule": rule.code, "matched": True,
                "because": ("nobody on shift holds the skill" if not seen
                            else "everybody who holds it is on another job")})
            continue

        rostered = _rostered(crew, shift.code, shift.day)
        chosen, why, looked, any_with_skill = _choose(
            crew, rule, order, rostered, window, collect=collect)
        if any_with_skill:
            nobody_on_shift = False
        if chosen is None:
            crew.nobody_free[(shift.code, shift.day, order.skill_code or None,
                              window)] = any_with_skill
            decision.rules_tried.append({
                "rule": rule.code, "matched": True,
                "because": ("nobody on shift holds the skill" if not any_with_skill
                            else "everybody who holds it is on another job")})
            decision.considered = looked or decision.considered
            continue
        decision.rules_tried.append({"rule": rule.code, "matched": True, "because": None})
        decision.rule, decision.rule_says = rule.code, says(rule)
        decision.considered = looked
        decision.assigned_to, decision.why = chosen.code, why
        return decision

    # The three reasons in the order a supervisor would ask them. Nobody wrote
    # a rule for this work is the first question and the cheapest to fix; then
    # whether the trade was even on shift; then, only then, that they were all
    # out on something else.
    if not any(tried["matched"] for tried in decision.rules_tried):
        decision.unassigned_reason = UnassignedReason.NO_RULE.value
    elif shift is None or nobody_on_shift:
        decision.unassigned_reason = UnassignedReason.NOBODY_ON_SHIFT_WITH_SKILL.value
    else:
        decision.unassigned_reason = UnassignedReason.ALL_BUSY.value
    return decision


def dispatch(session: Session, now: datetime | None = None, actor: str = "rules") -> dict:
    """Hand out every order that is due, and say what was handed out.

    Highest priority first, oldest first within a priority - which is the order
    a supervisor would work down the list in, and the only ordering that keeps a
    safety job from waiting behind a filter change raised an hour earlier.

    Idempotent: it looks only at orders at `due`, so running it twice in the
    same second changes nothing the second time, and an order a person assigned
    by hand is already past `due` and is never touched.
    """
    now = _at(now)
    crew = _read_crew(session)
    prepared = [(rule, _scope_of(session, rule)) for rule in rules(session)]
    assumed = _maintenance.default_job_minutes(session)
    plan_minutes = dict(session.execute(
        select(MaintenancePlan.id, MaintenancePlan.expected_minutes)).all())

    due_orders = list(session.scalars(
        select(MaintenanceOrder)
        .where(MaintenanceOrder.status == MaintenanceStatus.DUE)
        .order_by(func.coalesce(MaintenanceOrder.priority, DEFAULT_PRIORITY),
                  MaintenanceOrder.raised_at, MaintenanceOrder.id)))

    # The shift each machine is on at `now`, read once. `now` is one instant
    # for the whole pass by design - two thousand orders handed out "as of"
    # two thousand slightly different moments is not a pass a supervisor could
    # reason about - so one answer per machine is the whole answer.
    shifts: dict[int, object] = {}

    assigned, unassigned = 0, defaultdict(int)
    for order in due_orders:
        decision = _decide(session, order, now, crew, prepared, plan_minutes, assumed,
                           collect=False, shifts=shifts)
        if decision.assigned_to is None:
            order.unassigned_reason = decision.unassigned_reason
            unassigned[decision.unassigned_reason] += 1
            continue

        person = crew.by_code[decision.assigned_to]
        order.status = MaintenanceStatus.ASSIGNED
        order.assigned_to = person.code
        order.assigned_at = now
        order.assigned_by = decision.rule
        order.scheduled_for = now
        order.unassigned_reason = None
        window = _window(session, order, now, plan_minutes, assumed)
        crew.load[person.id] += 1
        crew.busy[person.id].append(window)
        crew.turns[(decision.rule, person.id)] += 1
        audit.record(session, actor=actor, action="maintenance.assigned",
                     entity_type="equipment", entity_id=order.equipment.code,
                     after={"order": order.code, "to": person.code,
                            "by_rule": decision.rule, "because": decision.why,
                            "shift": decision.shift})
        assigned += 1

    session.flush()
    return {
        "at": now,
        "considered": len(due_orders),
        "assigned": assigned,
        "unassigned": sum(unassigned.values()),
        # Named, not totalled: "six unassigned" is a number a supervisor can do
        # nothing with, and "four nobody on shift with the skill, two all busy"
        # is two different mornings.
        "unassigned_by_reason": dict(unassigned),
        "people": len(crew.by_id),
        "rules": len(prepared),
    }


def explain(session: Session, order_code: str, now: datetime | None = None) -> dict:
    """Why one order went where it went - or why it went nowhere.

    Evaluated against the plant as it stands, and it writes nothing. For an
    order somebody already holds, the recorded assignment is reported beside
    the walk, because what was decided an hour ago and what the same rules
    would decide now are two different answers and conflating them is how a
    supervisor ends up mistrusting both.
    """
    now = _at(now)
    order = _maintenance.get(session, order_code)
    crew = _read_crew(session)
    prepared = [(rule, _scope_of(session, rule)) for rule in rules(session)]
    assumed = _maintenance.default_job_minutes(session)
    plan_minutes = dict(session.execute(
        select(MaintenancePlan.id, MaintenancePlan.expected_minutes)).all())

    decision = _decide(session, order, now, crew, prepared, plan_minutes, assumed)
    out = decision.as_json()
    out["status"] = order.status.value
    out["equipment"] = order.equipment.code
    out["summary"] = order.summary
    out["held_by"] = order.assigned_to
    out["held_by_rule"] = order.assigned_by
    out["held_since"] = order.assigned_at
    out["recorded_unassigned_reason"] = order.unassigned_reason
    # Whether the number beside "priority" is the plant's or this product's
    # reading of a plan nobody classified. A supervisor who is told "priority
    # 3" about an order that says nothing has been told something the plant
    # never said (house rule 3).
    out["priority_is_stated"] = order.priority is not None
    out["would_now"] = (f"go to {decision.assigned_to}" if decision.assigned_to
                        else f"go to nobody - {decision.unassigned_reason}")
    return out


# ------------------------------------------------- by hand, and the read side


def assign(session: Session, order_code: str, person_code: str, *,
           actor: str = "system", scheduled_for: datetime | None = None) -> MaintenanceOrder:
    """A person gives the work to a person. The rules never undo this.

    A hand assignment is recorded with the assigner's code in `assigned_by`,
    which is how `dispatch` knows to leave it alone - and the reason is not
    arithmetic: a supervisor who reaches in and is overruled by the machine
    stops reaching in, and then the plant has a dispatcher nobody corrects.
    """
    order = _maintenance.get(session, order_code)
    if order.status in (MaintenanceStatus.DONE, MaintenanceStatus.SKIPPED):
        raise Conflict(f"{order_code} is {order.status.value}")
    person = masterdata.get_person(session, person_code)
    if order.skill_code:
        held = session.scalar(select(PersonnelSkill).where(
            PersonnelSkill.personnel_id == person.id,
            PersonnelSkill.skill_code == order.skill_code))
        if held is None:
            # Said, not refused. A supervisor on the floor at two in the
            # morning knows something this table does not, and an MES that
            # blocked them would be an MES they work around. The audit row
            # carries it so the gap in the skills table can be fixed.
            note = f"{person.code} does not hold {order.skill_code} on this plant"
        else:
            note = f"{person.code} holds {order.skill_code} at level {held.level}"
    else:
        note = f"{order_code} names no skill"

    before = {"status": order.status.value, "assigned_to": order.assigned_to,
              "assigned_by": order.assigned_by}
    order.status = MaintenanceStatus.ASSIGNED
    order.assigned_to = person.code
    order.assigned_at = utcnow()
    order.assigned_by = actor
    order.scheduled_for = scheduled_for or utcnow()
    order.unassigned_reason = None
    session.flush()
    audit.record(session, actor=actor, action="maintenance.assigned",
                 entity_type="equipment", entity_id=order.equipment.code, before=before,
                 after={"order": order.code, "to": person.code, "by_hand": True,
                        "note": note})
    return order


def _on_now(crew: _Crew, person_id: int, now: datetime) -> dict | None:
    """The one job this person is on, with how long it has been theirs.

    `minutes` means two different things and the status beside it says which:
    for an order in progress it is how long they have been working on it, and
    for one merely assigned it is how long it has been waiting. Keeping them
    in one field with the status beside it rather than in two is deliberate -
    a page that showed "waiting 190 minutes" in a column headed *worked* would
    be the screen lying, and a reader who has the status cannot be misled.

    `needs_stop` travels with it because it is the answer to the question a
    waiting order provokes. An electrician with a condenser clean assigned at
    06:03 and still waiting at 14:00 is not idle: the job needs the line
    stopped, and the line has been making bottles all shift.
    """
    order = crew.on_now.get(person_id)
    if order is None:
        return None
    since = (order.started_at if order.status is MaintenanceStatus.IN_PROGRESS
             else order.assigned_at) or order.assigned_at or order.raised_at
    return {
        "order": order.code,
        "equipment": order.equipment.code,
        # The plan, because the job is the plan's and a reader who wants to
        # know what the mechanic is actually doing to the machine has to be
        # able to get from the person to it in one step.
        "plan": order.plan.code if order.plan else None,
        "summary": order.summary,
        "status": order.status.value,
        "since": since,
        # `now` arrived through `_at`, and every stored timestamp is naive UTC
        # (`fsmes.db.utcnow`), so the subtraction needs no conversion here.
        "minutes": (round(max(0.0, (now - since).total_seconds() / 60.0), 1)
                    if since else None),
        "expected_minutes": crew.plan_minutes.get(order.plan_id),
        "needs_stop": bool(order.needs_stop),
        "window": order.window.value if order.window else None,
        "priority": order.priority,
        "skill": order.skill_code,
    }


def roster(session: Session, shift: str | None = None, now: datetime | None = None,
           equipment_id: int | None = None) -> dict:
    """Who is on shift, what they can do, and what they already have on.

    `shift` is a shift key (`2026-10-09/DAY`) or left out for the shift this
    moment falls in. The total is always stated, and so is the fact that there
    is no shift at all - a plant that has not told this MES its shift patterns
    has nobody rostered, which is a finding about the calendar and not an empty
    crew (house rule 2).
    """
    now = _at(now)
    found = (calendar.resolve_shift(session, shift, equipment_id) if shift
             else calendar.shift_for(session, now, equipment_id))
    if found is None:
        return {"shift": None, "people": [], "total": 0,
                "why_empty": calendar.nothing_to_window(session, equipment_id)}

    crew = _read_crew(session)
    people = []
    # Already in person-code order: `_rostered` sorts it, and the dispatcher
    # reads the same list in the same order, so the page and the decision
    # cannot disagree about who was looked at first.
    for row, person in _rostered(crew, found.code, found.day):
        held = crew.skills.get(person.id, {})
        people.append({
            "person": person.code, "name": person.name,
            "skills": [{"skill": code, "level": level}
                       for code, level in sorted(held.items())],
            "available": row.available, "reason": row.reason,
            "standing": row.standing,
            "open_orders": crew.load.get(person.id, 0),
            # How loaded they are, in minutes of work they are holding: the
            # expected minutes of every open job, which is exactly what the
            # dispatcher refuses to double-book. A plan's own number, or this
            # plant's default for a job with no plan - never a guess.
            "minutes_loaded": round(sum(
                (end - start).total_seconds() / 60.0
                for start, end in crew.busy.get(person.id, [])), 1),
            "home": person.home_equipment.code if person.home_equipment else None,
            # What they are doing, not just how much of it there is. A
            # supervisor's page needs the job in somebody's hands and how long
            # it has been there; a count of three tells them nothing they can
            # act on.
            "on_now": _on_now(crew, person.id, now),
        })
    return {"shift": found.as_json(), "people": people, "total": len(people),
            "available": sum(1 for p in people if p["available"])}


# ------------------------------------------------------------ one shift, read
#
# The supervisor's screen asks one question - "what happened on my shift, and
# what is stuck" - and this answers it in one read. Four queries, whatever the
# plant's size: the orders, the register of people, every machine's current
# state, and the rules. Never one query per order; the 300-person fixture has
# two thousand of them.


#: Why an order is sitting there, grouped the way a supervisor would ask.
#:
#: The first is not an `UnassignedReason` at all and that is the point: an
#: order given to somebody that needs the machine stopped on a machine that is
#: running has nothing wrong with its dispatch. Nils Berger has the chiller
#: clean; the line has been making bottles all shift. A screen that only read
#: `unassigned_reason` would show that order as handed out and fine, and the
#: supervisor would find out at the end of the shift.
WAITING_REASONS: dict[str, dict[str, str]] = {
    "needs_a_stop": {
        "label": "needs the line stopped",
        "why": "Somebody has it, but the job needs the machine stopped and it "
               "is still running.",
        "clause": "needs a stop and the line has not stopped",
    },
    UnassignedReason.NO_RULE.value: {
        "label": "no rule covers it",
        "why": "None of your rules matched this work, so the dispatcher had "
               "nothing to go on. Write a rule for it.",
        "clause": "no rule covers it",
    },
    UnassignedReason.NOBODY_ON_SHIFT_WITH_SKILL.value: {
        "label": "nobody on shift holds the trade",
        "why": "A rule matched, but nobody rostered on this shift holds the "
               "trade the job needs.",
        "clause": "nobody on shift holds the trade",
    },
    UnassignedReason.ALL_BUSY.value: {
        "label": "everybody with the trade is out on a job",
        "why": "A rule matched and the trade is on shift, but all of them are "
               "already out on something else.",
        "clause": "everybody with the trade is out",
    },
    "not_dispatched_yet": {
        "label": "the dispatcher has not seen it",
        "why": "Raised, and no dispatch pass has run since. Run the "
               "dispatcher, or give it to somebody.",
        "clause": "has not been through the dispatcher",
    },
}

#: The machine states in which a job that needs the line stopped cannot start.
#: `setup` counts: a changeover is the line in somebody else's hands.
_RUNNING = (EquipmentStateName.RUNNING, EquipmentStateName.SETUP)


def _waiting_bucket(order: MaintenanceOrder, machine_state: str | None) -> str | None:
    """Which bucket this order is waiting in, or None if it is not waiting."""
    if order.status is MaintenanceStatus.ASSIGNED and order.needs_stop:
        return "needs_a_stop" if machine_state in {s.value for s in _RUNNING} else None
    if order.status is not MaintenanceStatus.DUE:
        return None
    if order.unassigned_reason in WAITING_REASONS:
        return order.unassigned_reason
    return "not_dispatched_yet"


def _shift_sentence(shift, *, came_due: int, by_rules: int, by_hand: int,
                    waiting: list[dict]) -> str:
    """The one line at the top of the screen, in a supervisor's own words.

    Counts only what this shift raised, because "six came due" and "the rules
    handed out five" have to be about the same six or the line is arithmetic
    nobody can check. What is waiting is counted whenever it is waiting, shift
    or no shift - a job stuck since yesterday is stuck now.
    """
    said = [f"{shift.code}, {shift.day}:"]
    if came_due:
        said.append(f"{came_due} order{'' if came_due == 1 else 's'} came due;")
    else:
        said.append("no orders came due;")
    if by_rules:
        said.append(f"the rules handed out {by_rules}")
    else:
        said.append("the rules handed out none")
    if by_hand:
        said.append(f"and you gave out {by_hand} by hand")
    said[-1] += ";"

    stuck = sum(group["total"] for group in waiting)
    if not stuck:
        said.append("nothing is waiting.")
        return " ".join(said)
    said.append(f"{stuck} {'is' if stuck == 1 else 'are'} waiting")
    if stuck == 1:
        only = waiting[0]["orders"][0]
        clause = WAITING_REASONS[waiting[0]["reason"]]["clause"]
        said.append(f"- the {only['equipment']} {only['summary'][:60].lower()} {clause}.")
    else:
        said.append("- " + ", ".join(
            f"{group['total']} {WAITING_REASONS[group['reason']]['clause']}"
            for group in waiting) + ".")
    return " ".join(said)


def shift_view(session: Session, shift: str | None = None,
               now: datetime | None = None) -> dict:
    """This shift, in one read: the line at the top, the work, and what is stuck.

    `shift` is a shift key (`2026-10-09/DAY`), `current`, `previous`, or left
    out for the shift this moment falls in. A plant with no shift pattern is
    answered with no shift and the reason - not an empty shift, which would
    read as a quiet night (house rule 3).
    """
    now = _at(now)
    found = (calendar.resolve_shift(session, shift) if shift
             else calendar.shift_for(session, now))
    if found is None:
        return {"shift": None, "sentence": None,
                "why_empty": calendar.nothing_to_window(session, None),
                "counts": {}, "orders": [], "total": 0,
                "waiting": [], "waiting_total": 0}

    start, end = found.starts_at, found.ends_at
    # One query, and it has to answer two questions: what this shift raised -
    # which is what the line at the top counts - and what is open right now,
    # which is what is stuck whether this shift raised it or not.
    rows = list(session.scalars(
        select(MaintenanceOrder)
        .options(joinedload(MaintenanceOrder.equipment), joinedload(MaintenanceOrder.plan))
        .where((MaintenanceOrder.raised_at >= start) & (MaintenanceOrder.raised_at < end)
               | MaintenanceOrder.status.in_((MaintenanceStatus.DUE,
                                              MaintenanceStatus.ASSIGNED,
                                              MaintenanceStatus.IN_PROGRESS)))
        .order_by(MaintenanceOrder.raised_at.desc(), MaintenanceOrder.id.desc())))

    # The register, once. A supervisor reads names; the code is what the plant
    # stores. Both travel, and the screen decides which is big.
    names = dict(session.execute(select(Person.code, Person.name)).all())
    state_of = {
        equipment_id: state.value for equipment_id, state in session.execute(
            select(EquipmentState.equipment_id, EquipmentState.state)
            .where(EquipmentState.ended_at.is_(None))).all()}
    rule_says = {r.code: says(r) for r in session.scalars(select(DispatchRule))}
    rule_says.setdefault(DEFAULT_RULE_CODE, says(default_rule()))

    out: list[dict] = []
    waiting_by: dict[str, list[dict]] = defaultdict(list)
    came_due = by_rules = by_hand = done = running = 0
    for order in rows:
        machine_state = state_of.get(order.equipment_id)
        said = {
            "code": order.code,
            "equipment": order.equipment.code,
            "equipment_name": order.equipment.name,
            "machine_state": machine_state,
            "summary": order.summary,
            "reason": order.reason,
            "plan": order.plan.code if order.plan else None,
            "skill": order.skill_code,
            "priority": order.priority,
            "status": order.status.value,
            "needs_stop": bool(order.needs_stop),
            "window": order.window.value if order.window else None,
            "assigned_to": order.assigned_to,
            # Null when nobody has it; the code itself when the plant holds no
            # person by that code, which is a finding and not a blank.
            "assigned_to_name": names.get(order.assigned_to) if order.assigned_to else None,
            "by_rule": order.assigned_by,
            # The sentence the rule says, so a row can be read without
            # looking the rule up. Null when a person gave the work out: a
            # supervisor's name in `assigned_by` is not a rule.
            "by_rule_says": rule_says.get(order.assigned_by or ""),
            "raised_at": order.raised_at,
            "assigned_at": order.assigned_at,
            "started_at": order.started_at,
            "completed_at": order.completed_at,
            "this_shift": bool(start <= order.raised_at < end),
        }
        bucket = _waiting_bucket(order, machine_state)
        said["waiting_for"] = bucket
        out.append(said)
        if said["this_shift"]:
            came_due += 1
            if order.assigned_by and order.assigned_by in rule_says:
                by_rules += 1
            elif order.assigned_by:
                by_hand += 1
            if order.status is MaintenanceStatus.DONE:
                done += 1
            elif order.status is MaintenanceStatus.IN_PROGRESS:
                running += 1
        if bucket:
            waiting_by[bucket].append(said)

    waiting = [{"reason": reason, **{k: v for k, v in WAITING_REASONS[reason].items()
                                     if k != "clause"},
                "total": len(waiting_by[reason]), "orders": waiting_by[reason]}
               for reason in WAITING_REASONS if waiting_by.get(reason)]

    return {
        "shift": found.as_json(),
        "sentence": _shift_sentence(found, came_due=came_due, by_rules=by_rules,
                                    by_hand=by_hand, waiting=waiting),
        "counts": {"came_due": came_due, "by_rules": by_rules, "by_hand": by_hand,
                   "in_progress": running, "done": done,
                   "waiting": sum(g["total"] for g in waiting)},
        "orders": out,
        "total": len(out),
        "waiting": waiting,
        "waiting_total": sum(group["total"] for group in waiting),
    }
