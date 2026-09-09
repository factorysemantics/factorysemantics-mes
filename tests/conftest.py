"""Shared fixtures: a fresh in-memory database per test, seeded with the demo
plant, and API clients signed in at each role."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import fsmes.domain  # noqa: F401  (register all tables)
from fsmes.api.app import create_app
from fsmes.api.deps import get_db
from fsmes.db import Base
from fsmes.seed import seed_demo_plant
from fsmes.services import auth


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        seed_demo_plant(s)
        # Gates resolve capabilities from the roles table, so it has to exist
        # before any signed-in request in a test.
        auth.ensure_builtin_roles(s)
        s.commit()
        yield s
    engine.dispose()


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
