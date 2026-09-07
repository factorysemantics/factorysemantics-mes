"""Shared column helpers for the domain model.

Ported from MES-TWIN (``domain/common.py``).
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


def str_enum(enum_cls: type[enum.StrEnum]) -> SAEnum:
    """Store StrEnum *values* as plain VARCHAR — portable across SQLite and Postgres.

    ``values_callable`` is the load-bearing part. Without it SQLAlchemy stores
    the member *name*, so an enum whose name and value differ round-trips fine
    through the ORM while writing something else entirely into the database —
    which is exactly the kind of difference that only shows up when somebody
    queries the table with SQL.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=30,
        values_callable=lambda e: [member.value for member in e],
    )
