"""Master data: the ISA-95 equipment hierarchy, materials/BOM, routings, personnel."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fsmes.db import Base
from fsmes.domain.common import str_enum


class EquipmentLevel(enum.StrEnum):
    ENTERPRISE = "enterprise"
    SITE = "site"
    AREA = "area"
    WORK_CENTER = "work_center"
    WORK_UNIT = "work_unit"


class Equipment(Base):
    """ISA-95 equipment hierarchy (enterprise→site→area→work center→work unit)
    as one self-referencing table. Work units are the machines that run
    operations and carry OPC tags.

    The tree may be any depth: a plant with six lines, each holding several
    cells, each holding machines, is `site → area* → work_center → cell →
    work_unit`. Nothing here assumes a fixed number of rungs, and nothing
    that walks it should either — use `services.masterdata.descendants` and
    `work_units_under`, not a single `parent_id ==` hop.
    """

    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    level: Mapped[EquipmentLevel] = mapped_column(str_enum(EquipmentLevel))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"))
    ideal_cycle_seconds: Mapped[float | None]  # rated seconds per unit; feeds OEE performance
    # Who pays for what this machine does. Inherited down the tree by
    # `masterdata.cost_center`, so a plant sets it once per line and only
    # overrides where a cell is accounted for separately.
    #
    # Deliberately a code and not an amount: the MES reports quantities and
    # time against a cost center, and the ERP owns what they are worth. An
    # MES that computes money will disagree with Finance, and Finance wins.
    cost_center: Mapped[str | None] = mapped_column(String(40), index=True)
    # Taken off the floor: when, and the sentence why. A retired machine keeps
    # every row of its history - states, production, connections - and drops
    # out of the lists that mean "the machines this plant has now": the
    # Floor, `watching`, `work_units`. Null is on the floor. Clearing both
    # puts it back. First written by the Satisfactory reader, for the
    # buildings of a save that is no longer the one being played.
    #
    #
    # Kept out of every statement that does not ask for them: deferred, so a
    # SELECT of equipment does not name them; `server_default` NULL, so an
    # INSERT leaves them out unless they are set; and not fetched back by
    # RETURNING (`eager_defaults` off, below). A plant seeded by this code at
    # a revision older than these columns - the in-place upgrade tests do
    # exactly that - still accepts it. Lists filter on them in SQL.
    retired_at: Mapped[datetime | None] = mapped_column(
        server_default=text("NULL"), deferred=True, deferred_group="retired")
    retired_reason: Mapped[str | None] = mapped_column(
        String(200), server_default=text("NULL"), deferred=True, deferred_group="retired")

    # Where the machine stands on the floor, as a source reports it: x and y
    # across the floor and z its height, in metres, and its heading in
    # degrees. All four null is "nobody has said" - the line view then
    # places the machine from a drawn layout or from the routing, and never
    # at a made-up spot. A drawn `line_layout.json` entry still wins over a
    # held position; this is the fallback below a human, not above one.
    # First written by the Satisfactory reader, from the game's own map
    # coordinates. Deferred and NULL by default for the same reason as
    # `retired_at` below.
    pos_x: Mapped[float | None] = mapped_column(
        server_default=text("NULL"), deferred=True, deferred_group="position")
    pos_y: Mapped[float | None] = mapped_column(
        server_default=text("NULL"), deferred=True, deferred_group="position")
    pos_z: Mapped[float | None] = mapped_column(
        server_default=text("NULL"), deferred=True, deferred_group="position")
    heading: Mapped[float | None] = mapped_column(
        server_default=text("NULL"), deferred=True, deferred_group="position")

    parent: Mapped[Equipment | None] = relationship(remote_side=[id], backref="children")

    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": False}


class MaterialType(enum.StrEnum):
    RAW = "raw"
    INTERMEDIATE = "intermediate"
    FINISHED = "finished"


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    unit: Mapped[str] = mapped_column(String(20), default="ea")
    type: Mapped[MaterialType] = mapped_column(str_enum(MaterialType), default=MaterialType.RAW)
    # Whether a pallet certificate counts this material in pieces and prints
    # a capability block for it.
    #
    # A fact about the material, recorded on it. Until this column existed,
    # `services/coa.py` decided it by testing whether the code started `UT-`,
    # which is one plant's numbering convention living in product code: any
    # plant not numbering its pieces that way got an empty capability block
    # on every certificate and no error anywhere. The migration sets this
    # true for exactly the materials that prefix chose, so nothing about an
    # existing plant's certificates changes - it is the same answer, given
    # honestly by a flag instead of guessed from a name.
    counted_in_pieces: Mapped[bool] = mapped_column(default=False)


class BomItem(Base):
    """One component line: parent needs `quantity` of component per unit.

    `operation_seq` is where on the routing it goes in. A line consumes
    different components at different stations - the preform at the loader,
    the cap at the filler, the carton at the palletiser - and a BOM that
    cannot say which is a BOM for a job shop, not for a line.

    None means "somewhere on this order", which is the honest answer for a
    consumable nobody tracks to a station.
    """

    __tablename__ = "bom_items"
    __table_args__ = (
        UniqueConstraint("parent_id", "component_id", "operation_seq",
                         name="uq_bom_parent_component_operation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    component_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    quantity: Mapped[float]
    operation_seq: Mapped[int | None] = mapped_column(default=None)

    parent: Mapped[Material] = relationship(foreign_keys=[parent_id], backref="bom_items")
    component: Mapped[Material] = relationship(foreign_keys=[component_id])


class Routing(Base):
    """How a material is made: an ordered list of operations."""

    __tablename__ = "routings"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))

    material: Mapped[Material] = relationship(backref="routings")
    operations: Mapped[list[RoutingOperation]] = relationship(
        back_populates="routing", order_by="RoutingOperation.seq", cascade="all, delete-orphan"
    )


class RoutingOperation(Base):
    """One step of a routing: what is done, where, and how long it takes.

    **Where** is deliberately two questions. `equipment_id` names the machine
    the step runs on. `work_center_id` names a group of interchangeable
    machines - a cell - and means "any machine under here", which is how a
    plant with cells actually plans: the operation belongs to the cell and
    dispatch picks the member that is free. One of the two must be set; a
    step that names neither has nowhere to happen.

    **How long** is three numbers because a plant is billed for three
    different things: the setup that happens once per order, the machine time
    that scales with quantity, and the labour time that may not (one operator
    can mind four machines). All optional - a plant that has not time-studied
    a route still has to be able to run it, and the scheduler falls back to
    the machine's rated cycle. Seconds throughout, because minutes invite
    somebody to store 1.5 and mean ninety seconds.

    No rates and no money here: these feed the ERP, which owns valuation.
    """

    __tablename__ = "routing_operations"
    __table_args__ = (UniqueConstraint("routing_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    routing_id: Mapped[int] = mapped_column(ForeignKey("routings.id"))
    seq: Mapped[int]
    name: Mapped[str] = mapped_column(String(120))
    # The machine, or the group any machine of which will do. Nullable since
    # 2026-09-02: an operation may name a cell instead.
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"))
    work_center_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"))

    # Seconds. Setup happens once per order; run and labour scale with
    # quantity. Absent means "not time-studied", not zero - the scheduler
    # falls back to the machine's rated cycle and says which it used.
    setup_seconds: Mapped[float | None]
    run_seconds_per_unit: Mapped[float | None]
    labour_seconds_per_unit: Mapped[float | None]

    routing: Mapped[Routing] = relationship(back_populates="operations")
    equipment: Mapped[Equipment] = relationship(foreign_keys=[equipment_id])
    work_center: Mapped[Equipment | None] = relationship(foreign_keys=[work_center_id])


class Role(Base):
    """A named bundle of capabilities.

    Roles are data, not code, so a plant can define "Quality Inspector" or
    "Line Lead" without a deployment. `builtin` marks the five the product
    ships; they can be edited but the admin role cannot be deleted, because an
    MES with no administrator is a plant nobody can administer.
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(400))
    # A JSON array of capability names. Stored as text so this works
    # identically on SQLite and PostgreSQL.
    capabilities: Mapped[str] = mapped_column(Text, default="[]")
    builtin: Mapped[bool] = mapped_column(default=False)

    def granted(self) -> list[str]:
        import json

        try:
            return list(json.loads(self.capabilities or "[]"))
        except ValueError:
            return []


class Person(Base):
    """Personnel and system users are the same record: the code that signs in
    is the code that appears in the audit trail."""

    __tablename__ = "personnel"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(40), default="operator")
    password_hash: Mapped[str | None] = mapped_column(String(200))  # None = cannot sign in
    # Where this person normally works - a work center or a station. The one
    # link between a person and the plant's equipment tree, and the reason
    # "which work centre asks which questions" can be answered at all.
    #
    # **It grants nothing.** It is not a role, not a restriction, and no
    # capability reads it: somebody with a home station may work anywhere, and
    # somebody with none may work everywhere. It says where to *group* them,
    # and null means the plant has not said - which every rollup that uses it
    # counts out loud rather than folding into a group.
    home_equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"))

    home_equipment: Mapped[Equipment | None] = relationship()
