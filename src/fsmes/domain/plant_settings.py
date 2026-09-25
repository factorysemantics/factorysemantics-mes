"""The settings this plant owns, rather than the ones it is handed.

Decision [0035](../../docs/design/config-assistance.md) §3 settled the shape
for tier one — *what a plant is, in its own words and its own numbers* — and
it is two sentences long: **seeded by the pack, owned by the database.**
`shifts` has worked like that since the calendar existed. The `[quality]`
numbers that arrived on 2026-09-22 did not: they were compiled from
`plant.toml` into environment variables at `fsmes pack apply` and read from
there, which made every one of them a row on Quality's Configuration page
saying *nobody — it changes when the pack is applied and the plant restarts.*
Scott clicked one, found no control behind it, and asked the obvious
question.

This table is the missing half. One row is one setting a plant has taken
ownership of: the pack seeds it at build time, somebody holding the section's
own `define` capability edits it afterwards from the Configuration page, and
the change is audited and in force at once.

**Three layers, and the order matters.** A value is read from this table
first, from the compiled pack setting (the environment) second, and from the
literal the product ships third. So a plant with no rows here behaves exactly
as it did before this table existed — which is the test that keeps this
change honest — and a key whose row exists is the plant's own answer, whatever
the file it was built from has come to say since.

**Text, not one column per type.** A setting *is* text by the time a plant
reads one: `fsmes.pack.format.settings` compiles every kind — a float, a
whole number, a list of rule numbers — into an environment variable's string,
and the product parses each back where it reads it. Storing the same string
here means the database and the environment hold a value in the same shape,
so the fallback above is a fallback rather than a conversion, and a reading
that came from a row cannot differ from the same reading out of a pack.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fsmes.db import Base, utcnow


class PlantSetting(Base):
    """One setting this plant owns, as `[section] key = value`.

    Keyed by the pack's own names rather than by the environment variable or
    by the Configuration page's section, because the pack key is the one name
    for this setting that every part of the product already agrees on:
    `fsmes.pack.format.SCHEMA` declares it, `fsmes pack check` validates it
    by it, and the page prints it. A second naming here would be a second
    thing to keep in step.
    """

    __tablename__ = "plant_settings"
    __table_args__ = (
        UniqueConstraint("section", "key", name="uq_plant_setting"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The `plant.toml` table this key lives in, without its brackets —
    #: `quality` for `[quality] spc_min_points`.
    section: Mapped[str] = mapped_column(String(40))
    #: The key's own name inside that table.
    key: Mapped[str] = mapped_column(String(60))
    #: The value, as text, in exactly the shape the compiled pack setting
    #: would have carried it. Empty string is a real value and not an absence:
    #: `hold_rules = []` means *draw every rule and hold on none of them*.
    value: Mapped[str] = mapped_column(Text, default="")

    #: Who wrote the value that is here now. `pack-apply` when the pack seeded
    #: it and nobody has touched it since, which is the same word the
    #: masterdata seeder writes on a vocabulary it puts in force.
    set_by: Mapped[str] = mapped_column(String(40), default="pack-apply")
    set_at: Mapped[datetime] = mapped_column(default=utcnow)
