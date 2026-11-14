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
from fsmes.api import deps
from fsmes.api.app import create_app
from fsmes.api.deps import get_db, get_read_db
from fsmes.db import Base
from fsmes.seed import seed_demo_plant
from fsmes.services import auth

# WHICH CLOCK THE SUITE KEEPS. The plant's wall clock decides where a shift
# starts and when a gauge falls due, so a suite that let each machine use its
# own zone would pass in CI (UTC) and fail on a laptop in Chicago - which is
# exactly what happened the first time the zone became real. Pinned to UTC
# here so a stored timestamp and the plant's reading of it are the same
# number; the tests that are *about* the zone set their own and say so.
os.environ.setdefault("MES_PLANT_TIMEZONE", "UTC")

TEST_DATABASE_URL = os.environ.get("MES_TEST_DATABASE_URL", "").strip()
# The default: a private in-memory database per test.
IN_MEMORY_SQLITE = not TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def _the_suite_writes_no_agent_records_to_this_machine(tmp_path, monkeypatch):
    """The agent's two append-only records go somewhere disposable.

    Both default to `~/.local/share/fsmes/`, which on a developer's machine is
    a real one: a suite that books a hundred imaginary dollars into the month's
    spend would switch the cloud brain off on that machine for the rest of it.
    Tests that are *about* these files point them somewhere of their own, and
    that still wins - an autouse fixture is applied before the test's own.
    """
    from fsmes.services import agent

    monkeypatch.setattr(agent, "USAGE_FILE", tmp_path / "agent-usage.jsonl")
    monkeypatch.setattr(agent, "TURN_FILE", tmp_path / "agent-turns.jsonl")


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


def use_the_test_session(app, session, monkeypatch) -> None:
    """Point every one of the API's database seams at this test's session.

    Three seams, not one, and each of them exists for a reason a test would
    otherwise trip over.

    `get_db` is the request's own session. `get_read_db` is the unit of work
    a read-only endpoint asks for (`fsmes.db.read_only_session`), which would
    otherwise open a second connection to the deployment's database rather
    than this test's; what the read-only path guarantees is proved where it
    matters, against a real file database, in `test_read_never_waits.py`.
    `deps.short_read` is the one the capability gate and the endpoints that
    call a model read through - deliberately not a FastAPI dependency,
    because a dependency's session is held until the response has been sent
    and those endpoints wait on a network in between. A test that points only
    the first at its own session gets 401s from the gate, which would be
    looking for this test's accounts in a database that has none.
    `deps.short_write` is its sibling, which those same endpoints use to
    record what they did once the model has answered.
    """

    def _same_session():
        yield session
        session.flush()

    @contextmanager
    def _same_session_read():
        yield session

    @contextmanager
    def _same_session_write():
        yield session
        session.flush()

    app.dependency_overrides[get_db] = _same_session
    app.dependency_overrides[get_read_db] = _same_session
    monkeypatch.setattr(deps, "short_read", _same_session_read)
    monkeypatch.setattr(deps, "short_write", _same_session_write)


@pytest.fixture()
def make_client(session, monkeypatch):
    """Build API clients that share the test session. Each call returns its own
    client, so tests can hold several roles at once without them colliding."""
    app = create_app()
    use_the_test_session(app, session, monkeypatch)
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


# ------------------------------------------------------- the shared live plant
#
# A browser test needs a plant answering over HTTP, which the in-memory
# database above cannot give it: the server answers on its own threads and
# must open the same rows. Thirty-five test files each grew their own copy of
# the code below (`tests/test_browser_tests_share_one_server.py` holds the
# list). New browser tests use `live_server`, or `serve_plant` when they need
# rows of their own; neither starts anything a person is running.


class LiveServer:
    """A plant served on a loopback port of the operating system's choosing.

    `base` is its address, `engine` its database (a file under the run's
    temporary directory), and `sign_in(context)` signs a Playwright browser
    context in, as the seeded ADMIN unless told otherwise, the way a person's
    browser is: the login sets an HttpOnly cookie that Playwright shares
    between its request jar and its pages.
    """

    def __init__(self, base: str, engine: Engine) -> None:
        self.base = base
        self.engine = engine

    def sign_in(self, context, code: str = "ADMIN", password: str = "admin") -> None:
        import json

        reply = context.request.post(
            f"{self.base}/auth/login",
            data=json.dumps({"code": code, "password": password}),
            headers={"Content-Type": "application/json"})
        assert reply.ok, f"sign-in as {code} failed: {reply.status}"


def _wait_until_answering(base: str, seconds: float = 20.0) -> None:
    import time
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=1) as reply:
                if reply.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    raise AssertionError(f"the plant at {base} never answered /health")


@contextmanager
def _serve(directory, seed=None):
    """Seed the demo plant (and `seed(session)`, if given) into a file
    database under `directory` and serve it until the block ends."""
    import socket
    import threading

    import uvicorn

    from fsmes import config, db
    from fsmes.db import make_engine

    url = f"sqlite:///{directory / 'plant.db'}"
    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_sessionmaker.cache_clear()

        engine = make_engine(url)
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as s:
            seed_demo_plant(s)
            auth.ensure_builtin_roles(s)
            if seed is not None:
                seed(s)
            s.commit()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(create_app(), log_level="warning", lifespan="on"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
            _wait_until_answering(base)
            yield LiveServer(base, engine)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """The demo plant, served once for the whole run. Shared by every test
    that asks for it, so a test that writes to it writes under codes of its
    own and asserts only on those; a test that needs a plant shaped its own
    way uses `serve_plant` instead. `MES_DATABASE_URL` points at it until the
    run ends, as it did for the length of a module in the copies this
    replaces; a test that needs it unset sets its own."""
    with _serve(tmp_path_factory.mktemp("live-server")) as served:
        yield served


@pytest.fixture(scope="session")
def serve_plant(tmp_path_factory):
    """`with serve_plant(seed) as plant:` - a plant of this test's own: the
    demo plant plus whatever `seed(session)` adds, served until the block
    ends. Call it from a module-scoped fixture."""

    @contextmanager
    def _serve_plant(seed=None, name: str = "plant"):
        with _serve(tmp_path_factory.mktemp(name), seed) as served:
            yield served

    return _serve_plant
