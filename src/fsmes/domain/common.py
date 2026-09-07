"""Shared column helpers for the domain model."""

import enum

from sqlalchemy import Enum as SAEnum


def str_enum(enum_cls: type[enum.StrEnum]) -> SAEnum:
    """Store StrEnum *values* as plain VARCHAR — portable across SQLite and Postgres."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=30,
        values_callable=lambda e: [member.value for member in e],
    )
