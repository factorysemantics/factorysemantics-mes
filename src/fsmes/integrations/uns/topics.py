"""Where an event hangs in the namespace.

ISA-95 gives the shape — enterprise, site, area, work centre, work unit —
and the MES already holds that tree, so the topic is read out of it rather
than configured machine by machine. Only the top two levels are settings:
which enterprise and which site this MES speaks for is a plant-boundary
decision, and the same database is deployed under different namespace names
in test and in production.

Three rules that come from the house rules rather than from MQTT:

* A level the plant has not modelled is published as `unknown`. It is not
  guessed from the level above and it is not silently left out, because a
  consumer counting topics would then count a machine as belonging to the
  line above it.
* The tree may be any depth. A plant laid out site → area → line → cell →
  machine publishes all of it; nothing here assumes a fixed number of rungs.
* An event that names no machine is published where it is actually true —
  at the site — instead of being hung under an arbitrary one.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.config import Settings
from fsmes.domain import Equipment, EquipmentLevel

UNKNOWN = "unknown"

# MQTT reserves `/` (the separator), `+` and `#` (wildcards), and forbids the
# null character. Anything else outside this set is replaced too, because a
# space or an accent in a topic is legal and still ruins somebody's shell
# script. The replacement is visible on purpose: a mangled segment should
# look mangled.
_ALLOWED = re.compile(r"[^A-Za-z0-9_.:@=-]")


def segment(value: str | None) -> str:
    """One topic level, safe to put between two slashes."""
    if value is None:
        return UNKNOWN
    cleaned = _ALLOWED.sub("_", value.strip())
    return cleaned or UNKNOWN


def _ancestors(equipment: Equipment) -> list[Equipment]:
    """The machine and everything above it, machine last."""
    chain: list[Equipment] = []
    seen: set[int] = set()
    node: Equipment | None = equipment
    while node is not None and node.id not in seen:
        seen.add(node.id)
        chain.append(node)
        node = node.parent
    chain.reverse()
    return chain


def _sole(session: Session, level: EquipmentLevel) -> Equipment | None:
    """The one node at this level, or None if the plant has none or several.

    Several is not an error and not a tie to break: a database holding two
    sites cannot answer "which site is this event about" from the tree, so
    the answer is unknown until settings say otherwise.
    """
    found = list(session.scalars(
        select(Equipment).where(Equipment.level == level).order_by(Equipment.code).limit(2)))
    return found[0] if len(found) == 1 else None


def enterprise_and_site(session: Session, settings: Settings,
                        equipment: Equipment | None = None) -> tuple[str, str]:
    """The top two segments: settings first, then the tree, then `unknown`."""
    chain = _ancestors(equipment) if equipment is not None else []
    by_level = {node.level: node for node in chain}

    def resolve(configured: str, level: EquipmentLevel) -> str:
        if configured:
            return segment(configured)
        node = by_level.get(level) or _sole(session, level)
        return segment(node.code) if node is not None else UNKNOWN

    return (resolve(settings.uns_enterprise, EquipmentLevel.ENTERPRISE),
            resolve(settings.uns_site, EquipmentLevel.SITE))


def equipment_path(equipment: Equipment) -> list[str]:
    """The segments between the site and the machine, top down, machine last.

    Enterprise and site are dropped because they are named separately; every
    other rung the plant modelled is kept, whatever it is called.
    """
    return [segment(node.code) for node in _ancestors(equipment)
            if node.level not in (EquipmentLevel.ENTERPRISE, EquipmentLevel.SITE)]


def topic_for(session: Session, settings: Settings, *, kind: str | None = None,
              equipment_code: str | None = None) -> str:
    """The full topic for one event.

        <prefix>/<enterprise>/<site>[/<area>/<line>/.../<machine>]/<schema>/<kind>

    An `equipment_code` the MES does not hold is still published, as a single
    segment with no ancestors: the event happened, and hiding it because the
    master data is behind would be inventing the plant's shape.

    `kind=None` stops after the schema segment. That is not a topic anything
    publishes on; it is the prefix `fsmes uns topics` prints, so a plant can
    read its tree without a placeholder pretending to be an event name.
    """
    equipment = None
    if equipment_code:
        equipment = session.scalar(select(Equipment).where(Equipment.code == equipment_code))
    enterprise, site = enterprise_and_site(session, settings, equipment)
    if equipment is not None:
        path = equipment_path(equipment)
    elif equipment_code:
        path = [segment(equipment_code)]
    else:
        path = []
    parts = [p for p in settings.uns_topic_prefix.strip("/").split("/") if p]
    parts += [enterprise, site, *path, segment(settings.uns_schema)]
    if kind is not None:
        parts.append(segment(kind))
    return "/".join(parts)


def equipment_topics(session: Session, settings: Settings, kind: str | None = None) -> list[str]:
    """The topic prefix every work unit in the plant publishes under.

    What `fsmes uns topics` prints, so a plant can see the tree before a
    single event has been produced.
    """
    units = session.scalars(
        select(Equipment).where(Equipment.level == EquipmentLevel.WORK_UNIT)
        .order_by(Equipment.code)).all()
    return [topic_for(session, settings, kind=kind, equipment_code=unit.code) for unit in units]


def equipment_code_in(payload: dict | None) -> str | None:
    """Which machine an outbox payload is about, if it says.

    One key, `equipment`, because that is what the ERP contract calls the
    work unit. A payload that does not name one is not a problem to solve
    here — it is an order-level fact, and it publishes at the site.
    """
    if not payload:
        return None
    code = payload.get("equipment")
    return str(code) if code else None
