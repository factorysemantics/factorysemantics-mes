"""A PostgreSQL plant that was upgraded in place accepts every word the code writes.

The suite's own PostgreSQL run builds its schema with `create_all`, from the
domain model, so it never sees what the migrations made - and the migrations
are the only way a real plant ever gets its schema. Early ones created nine
status and kind columns as native PostgreSQL enums; the code declares them as
plain text (`domain.common.str_enum`). When `assigned` joined
`MaintenanceStatus` on 2026-10-09, every PostgreSQL plant built by the
migrations - old and upgraded, or new - refused the word, and the lab's Maintenance screen answered 500 for a fortnight
while every test here was green.

So these tests run the shipped chain itself, into a schema of their own inside
the suite's database, and look at what it made. They run only when the suite
is pointed at a PostgreSQL (`MES_TEST_DATABASE_URL`, as CI's `postgres` job
does); on SQLite there is no native enum to fall behind.
"""

import enum
import uuid
from datetime import datetime, timedelta

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Enum as SAEnum
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session

import fsmes.domain  # noqa: F401  (register all tables)
from conftest import TEST_DATABASE_URL, use_the_test_session
from fsmes.api.app import create_app
from fsmes.db import Base
from fsmes.domain import MaintenanceKind, MaintenanceOrder
from fsmes.schema import alembic_config, head_revision
from fsmes.seed import seed_demo_plant
from fsmes.services import auth, masterdata

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason="native enums exist only on PostgreSQL; set MES_TEST_DATABASE_URL to one",
)

#: The revision the lab's bottling plant was at on 2026-10-25, when its
#: Maintenance screen was found saying "reconnecting..." for good.
LAB_REVISION = "b6f0c2a71d58"


@pytest.fixture()
def private_schema():
    """A URL whose connections live in a schema nobody else uses, dropped after.

    The suite's database is shared with every other test (`conftest.py`
    empties its tables between them), so the chain is run somewhere of its
    own rather than over the top of that.
    """
    schema = f"chain_{uuid.uuid4().hex[:12]}"
    admin = create_engine(TEST_DATABASE_URL)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(TEST_DATABASE_URL).update_query_dict(
        {"options": f"-csearch_path={schema}"})
    try:
        yield schema, url.render_as_string(hide_password=False)
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _migrate(url: str, revision: str) -> None:
    # Alembic's configuration interpolates `%`, and a rendered URL escapes `=`.
    command.upgrade(alembic_config(url.replace("%", "%%")), revision)


def _enum_columns():
    """Every column the domain model stores a `StrEnum` in, with that enum."""
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if isinstance(column.type, SAEnum) and column.type.enum_class is not None:
                yield table.name, column.name, column.type.enum_class


def _words_the_database_refuses(conn, schema: str, table: str, column: str,
                                enum_class: type[enum.StrEnum]) -> list[str]:
    found = conn.execute(text(
        "SELECT data_type, udt_name, character_maximum_length "
        "FROM information_schema.columns "
        "WHERE table_schema = :s AND table_name = :t AND column_name = :c"),
        {"s": schema, "t": table, "c": column}).first()
    assert found is not None, f"{table}.{column} is in the code but not in the migrated database"
    data_type, udt_name, max_length = found
    wanted = [member.value for member in enum_class]
    if data_type == "USER-DEFINED":
        labels = set(conn.execute(text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE t.typname = :u AND n.nspname = :s"),
            {"u": udt_name, "s": schema}).scalars())
        return [word for word in wanted if word not in labels]
    if max_length is not None:
        return [word for word in wanted if len(word) > max_length]
    return []


def test_every_status_column_a_migrated_postgresql_plant_has_accepts_every_value_the_code_can_write(
        private_schema):
    """A new member of a `StrEnum` with no migration behind it fails here, not on a plant."""
    schema, url = private_schema
    _migrate(url, "head")

    engine = create_engine(url)
    checked, refused = 0, []
    with engine.connect() as conn:
        for table, column, enum_class in _enum_columns():
            checked += 1
            for word in _words_the_database_refuses(conn, schema, table, column, enum_class):
                refused.append(f"{table}.{column} ({enum_class.__name__}) refuses {word!r}")
    engine.dispose()

    assert checked > 0, "found no enum columns in the domain model to check"
    assert refused == [], f"{len(refused)} of the code's words refused:\n" + "\n".join(refused)


def test_a_postgresql_plant_made_at_the_labs_revision_and_upgraded_can_assign_maintenance_work(
        private_schema):
    """The lab's case end to end: an existing plant, with an order, upgraded in place."""
    _schema, url = private_schema
    _migrate(url, LAB_REVISION)

    engine = create_engine(url)
    # The plant as it stood before the upgrade: its people, its machines and
    # one open corrective job.
    with Session(engine, expire_on_commit=False) as s:
        seed_demo_plant(s)
        auth.ensure_builtin_roles(s)
        masterdata.create_person(s, code="FIXER", name="Fay Ixer", role="operator")
        equipment = masterdata.get_equipment(s, "MIX01")
        s.add(MaintenanceOrder(
            code="CM-LAB-1", equipment_id=equipment.id, kind=MaintenanceKind.CORRECTIVE,
            summary="a bearing is loud", raised_at=datetime(2026, 10, 25, 11, 0)
            - timedelta(hours=1)))
        s.commit()

    _migrate(url, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == head_revision()

    with Session(engine, expire_on_commit=False) as s:
        app = create_app()
        mp = pytest.MonkeyPatch()
        try:
            use_the_test_session(app, s, mp)
            with TestClient(app) as client:
                login = client.post("/auth/login", json={"code": "ADMIN", "password": "admin"})
                assert login.status_code == 200, login.text
                client.headers["Authorization"] = f"Bearer {login.json()['token']}"

                due = client.get("/maintenance/due")
                assert due.status_code == 200, due.text

                assigned = client.post("/maintenance/orders/CM-LAB-1/assign",
                                       json={"person": "FIXER"})
                assert assigned.status_code == 200, assigned.text
            s.commit()
        finally:
            mp.undo()

    with engine.connect() as conn:
        stored = conn.execute(text(
            "SELECT status, assigned_to FROM maintenance_orders WHERE code = 'CM-LAB-1'")).one()
    engine.dispose()
    assert tuple(stored) == ("assigned", "FIXER")
