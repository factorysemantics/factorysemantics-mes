"""What `str_enum` guarantees, and why it exists.

The helper looks trivial. It is here because the default SQLAlchemy behaviour
writes the enum member's NAME while the application reads its VALUE, and the
two agree right up until somebody queries the table with SQL.
"""

from __future__ import annotations

import enum

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.schema import CreateTable

from fsmes.kernel.common import str_enum


class _Base(DeclarativeBase):
    pass


class _State(enum.StrEnum):
    """Name and value deliberately differ — that difference is the whole test."""

    RUNNING = "running"
    PLANNED_STOP = "planned_stop"


class _Reading(_Base):
    __tablename__ = "readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[_State] = mapped_column(str_enum(_State))


def _engine():
    engine = create_engine("sqlite://")
    _Base.metadata.create_all(engine)
    return engine


def test_the_database_stores_the_enum_value_not_the_member_name():
    engine = _engine()
    with Session(engine) as session:
        session.add(_Reading(id=1, state=_State.PLANNED_STOP))
        session.commit()

    with engine.connect() as conn:
        stored = conn.execute(text("SELECT state FROM readings WHERE id = 1")).scalar_one()

    assert stored == "planned_stop"
    assert stored != "PLANNED_STOP"


def test_the_value_round_trips_back_into_the_enum_member():
    engine = _engine()
    with Session(engine) as session:
        session.add(_Reading(id=1, state=_State.PLANNED_STOP))
        session.commit()

    with Session(engine) as session:
        assert session.get(_Reading, 1).state is _State.PLANNED_STOP


def test_the_column_is_plain_varchar_so_the_schema_ports_to_postgres():
    # A native enum type would need a CREATE TYPE and an ALTER TYPE to add a
    # member — portability across SQLite and Postgres is the reason this helper
    # exists rather than passing the enum class in directly.
    ddl = str(CreateTable(_Reading.__table__).compile(_engine()))
    assert "VARCHAR(30)" in ddl
    assert "CREATE TYPE" not in ddl
