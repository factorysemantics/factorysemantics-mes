"""Shared fixtures: a fresh database per test, seeded with the demo plant, and
API clients signed in at each role.

WHICH DATABASE. In-memory SQLite unless `MES_TEST_DATABASE_URL` says otherwise,
so `python -m pytest` needs no setup and stays fast. Point that variable at a
PostgreSQL and the whole suite runs there instead:

    MES_TEST_DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/fsmes

CI does exactly that in the `postgres` job, which is what makes "runs on
PostgreSQL" a gate rather than a claim.

It is deliberately *not* `MES_DATABASE_URL`, the setting a deployment uses. A
developer with that set is pointing it at a database with rows in it, and this
file empties whatever it is given between tests.

HOW EACH TEST GETS A CLEAN DATABASE. On in-memory SQLite, one engine per test:
nothing is shared, so nothing has to be cleaned. On a server database that
would mean creating and dropping sixty-odd tables a thousand times, so the
schema is created once for the run and every table is emptied before each test
with TRUNCATE ... RESTART IDENTITY CASCADE.

Emptying rather than rolling back a wrapping transaction, which is the other
usual answer, for two reasons. A commit in a test is then a real commit, so a
constraint or a deadlock behaves the way it would on a plant instead of the way
it behaves inside a savepoint. And RESTART IDENTITY hands each test the same
first ids SQLite hands it, because a rolled-back transaction does not roll back
a sequence and tests that name a row by its id would drift apart run to run.
"""

import os
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.api.app import create_app
from fsmes.api.deps import get_db
from fsmes.db import Base
from fsmes.seed import seed_demo_plant
from fsmes.services import auth

TEST_DATABASE_URL = os.environ.get("MES_TEST_DATABASE_URL", "").strip()
# The default: a private in-memory database per test.
IN_MEMORY_SQLITE = not TEST_DATABASE_URL


def _empty(engine: Engine) -> None:
    """Remove every row from every table the domain model declares."""
    if engine.dialect.name == "postgresql":
        # One statement, so one set of locks, and CASCADE because the tables
        # reference each other. RESTART IDENTITY resets the sequences.
        names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        return
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture(scope="session")
def _server_engine():
    """One engine and one schema for the whole run, when the suite is pointed
    at a database that outlives a single test."""
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def engine(request):
    if IN_MEMORY_SQLITE:
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(eng)
        yield eng
        eng.dispose()
        return
    eng = request.getfixturevalue("_server_engine")
    _empty(eng)
    yield eng


@pytest.fixture()
def session(engine):
    with Session(engine, expire_on_commit=False) as s:
        seed_demo_plant(s)
        # Gates resolve capabilities from the roles table, so it has to exist
        # before any signed-in request in a test.
        auth.ensure_builtin_roles(s)
        s.commit()
        yield s


@pytest.fixture()
def scope(session):
    """A session_scope stand-in that reuses the test session (no commit/close)."""

    @contextmanager
    def _scope():
        yield session
        session.flush()

    return _scope


@pytest.fixture()
def make_client(session):
    """Build API clients that share the test session. Each call returns its own
    client, so tests can hold several roles at once without them colliding."""
    app = create_app()

    def _same_session():
        yield session
        session.flush()

    app.dependency_overrides[get_db] = _same_session
    clients = []

    def _make() -> TestClient:
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture()
def anon(make_client):
    """An API client with no session — used to prove endpoints are protected."""
    return make_client()


@pytest.fixture()
def sign_in(make_client, session):
    """Sign a fresh client in as any role; seeded users cover operator and admin."""

    def _sign_in(code: str, password: str | None = None, role: str = "supervisor") -> TestClient:
        if password is None:  # roles without a seeded account get one on demand
            password = "test-password"
            auth.create_user(session, code=code, name=code.title(), password=password, role=role)
            session.flush()
        client = make_client()
        response = client.post("/auth/login", json={"code": code, "password": password})
        assert response.status_code == 200, response.text
        client.headers["Authorization"] = f"Bearer {response.json()['token']}"
        return client

    return _sign_in


@pytest.fixture()
def client(sign_in):
    """Signed in as the seeded operator — the default for most API tests."""
    return sign_in("SCOTT", "operator")


@pytest.fixture()
def admin(sign_in):
    return sign_in("ADMIN", "admin")


@pytest.fixture()
def supervisor(sign_in):
    return sign_in("SUPER", role="supervisor")
