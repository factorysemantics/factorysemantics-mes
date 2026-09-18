"""SQLite has one writer, and a transaction that will write has to say so
when it begins.

Found in CI: one run of the demo logged eight
``sqlite3.OperationalError: database is locked`` from the OPC agent while
booking plant readings. ``db.py`` set WAL and ``busy_timeout=5000`` and its
comment said that prevented this. It did not. A transaction that is already
open when it first writes has to upgrade to the write lock, and SQLite
refuses that upgrade outright rather than waiting for it — waiting could
only deadlock. ``busy_timeout`` covers waiting for a lock; it does not cover
a refusal.

The agent's booking is exactly that shape: ``_book`` opens a savepoint per
state change, which opens the transaction, and writes inside it. These tests
use a file-backed database on purpose — the race does not exist in an
in-memory one, which is why the rest of the suite never saw it.
"""

import pathlib
import threading

import pytest
from sqlalchemy import func, select

import fsmes.domain  # registers all tables, and locates the source tree
from fsmes.db import Base, make_engine
from fsmes.domain import IdempotencyKey

WRITERS = 2
ROUNDS = 40


@pytest.fixture()
def file_database(tmp_path, monkeypatch):
    """Point the real engine at a file on disk, then put it back."""
    from fsmes import config
    from fsmes import db as db_module

    cached = (config.get_settings, db_module.get_engine, db_module.get_sessionmaker)
    monkeypatch.setenv("MES_DATABASE_URL", f"sqlite:///{(tmp_path / 'locks.db').as_posix()}")
    for c in cached:
        c.cache_clear()
    Base.metadata.create_all(db_module.get_engine())
    yield
    db_module.get_engine().dispose()
    for c in cached:
        c.cache_clear()


def _book_a_batch(writer: int, errors: list[str], barrier: threading.Barrier) -> None:
    """One unit of work per round, shaped like the OPC agent's booking:
    a savepoint, a read inside it, then a write.

    Every round starts on the barrier so the writers overlap on purpose
    rather than by luck. A round that fails is recorded and the writer
    carries on, so the barrier stays balanced and every failure is counted
    rather than only the first.
    """
    from fsmes.db import session_scope

    for round_no in range(ROUNDS):
        try:
            barrier.wait(timeout=60)
        except threading.BrokenBarrierError:
            return
        try:
            with session_scope() as db, db.begin_nested():
                seen = db.scalar(select(func.count()).select_from(IdempotencyKey))
                db.add(IdempotencyKey(
                    actor=f"writer-{writer}", key=f"{writer}-{round_no}-{seen}",
                    method="POST", path="/execution/consume",
                    status_code=201, body=None))
        except Exception as exc:
            errors.append(f"writer {writer}, round {round_no}: {type(exc).__name__}: {exc}")


def test_two_batches_booked_at_the_same_time_do_not_lock_each_other_out(file_database):
    errors: list[str] = []
    barrier = threading.Barrier(WRITERS)
    threads = [threading.Thread(target=_book_a_batch, args=(i, errors, barrier))
               for i in range(WRITERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)

    assert not any(t.is_alive() for t in threads), "a writer never finished"
    assert errors == [], (
        f"{len(errors)} of {WRITERS * ROUNDS} units of work failed; first: {errors[0]}")

    from fsmes.db import session_scope
    with session_scope() as db:
        written = db.scalar(select(func.count()).select_from(IdempotencyKey))
    assert written == WRITERS * ROUNDS, f"{written} rows booked, expected {WRITERS * ROUNDS}"


def test_a_transaction_is_not_held_open_across_a_network_call():
    """Every transaction takes SQLite's one write lock now, reads included, so
    a session held open across a network call stops every other writer for as
    long as the network takes. The OPC agent's adjustment loop was the one
    place doing it; this pins that no module starts doing it again."""
    import ast

    scopes = {"session_scope", "scope", "_scope"}

    def opens_a_scope(item) -> bool:
        call = item.context_expr
        if not isinstance(call, ast.Call):
            return False
        fn = call.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        return name in scopes

    offenders = []
    source_root = pathlib.Path(fsmes.domain.__file__).parent.parent
    files = sorted(source_root.rglob("*.py"))
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.With | ast.AsyncWith):
                continue
            if any(opens_a_scope(i) for i in node.items) and any(
                    isinstance(n, ast.Await) for n in ast.walk(node)):
                offenders.append(f"{path.relative_to(source_root)}:{node.lineno}")

    assert files, "no source files found to check"
    assert offenders == [], (
        f"{len(offenders)} of {len(files)} source files hold a session open "
        f"across an await: {offenders}")


def test_postgresql_keeps_its_own_transaction_handling():
    """The immediate begin is SQLite's problem and SQLite's alone.

    Skipped where no PostgreSQL driver is installed, which includes CI's test
    job — it installs `dev,mcp,agent`, not `postgres`.
    """
    pytest.importorskip("psycopg", reason="no PostgreSQL driver installed")

    def handlers(engine) -> set[str]:
        """What `_sqlite_transactions` attached, by name. SQLAlchemy's own
        SQLite dialect attaches connect handlers of its own, so name them
        rather than counting them."""
        return {fn.__name__ for fn in engine.dispatch.begin} | {
            fn.__name__ for fn in engine.pool.dispatch.connect}

    assert {"_sqlite_begin", "_sqlite_pragmas"} <= handlers(make_engine("sqlite://"))

    postgres = make_engine("postgresql+psycopg://mes:mes@127.0.0.1:5432/mes")
    assert not {"_sqlite_begin", "_sqlite_pragmas"} & handlers(postgres), (
        "PostgreSQL was given SQLite's transaction handling")


def _functions_that_reach_a_model(module_path) -> set[str]:
    """The names in one service module that end up calling a model.

    Worked out rather than listed: a function reaches a model if it opens a
    URL itself, or asks an SDK client to create something, or calls another
    function in the same module that does. A list would be a second place
    to keep up to date, and the next model call added would not be on it.
    """
    import ast

    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    calls: dict[str, set[str]] = {}
    direct: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        named = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Name):
                named.add(f.id)
            elif isinstance(f, ast.Attribute):
                named.add(f.attr)
        calls[fn.name] = named
        if {"urlopen"} & named or ("create" in named and "messages" in ast.dump(fn)):
            direct.add(fn.name)

    reaching = set(direct)
    changed = True
    while changed:
        changed = False
        for name, named in calls.items():
            if name not in reaching and named & reaching:
                reaching.add(name)
                changed = True
    return reaching


def test_no_route_holds_a_request_session_while_it_waits_on_a_model():
    """The same rule as the test above, for the shape that slipped past it.

    That one looks for an `await` inside a literal ``with session_scope()``.
    A FastAPI request session is neither: it arrives as a dependency and
    lives until the response has been sent, and the calls made inside it
    are plain blocking ones. `POST /design/chat` had both - a `DbDep` and,
    inside it, two on-device compressions at 90 s each, a classification at
    45 s and a frontier model with no timeout of its own. On SQLite that is
    the plant's single write lock held for minutes, and the symptom was
    `Could not reach the design surface: 500` on a plant that had stopped
    being able to book its own production.

    Three service modules talk to a model. A route that takes a request
    session may not call the functions in them that reach one; it reads what
    it needs through `deps.short_read`, which closes before the call goes
    out. Routes that call the *other* functions in those modules - the ones
    that only read a catalogue - are fine and stay fine.
    """
    import ast

    SESSION_DEPS = {"DbDep", "ActorDep"}
    source_root = pathlib.Path(fsmes.domain.__file__).parent.parent
    services = source_root / "services"
    waits: dict[str, set[str]] = {
        name: _functions_that_reach_a_model(services / f"{name}.py")
        for name in ("design", "assistant", "agent")
    }
    assert all(waits.values()), f"no model calls found at all in {sorted(waits)}; the guard is blind"

    def takes_a_request_session(fn) -> bool:
        args = fn.args
        return any(getattr(a.annotation, "id", None) in SESSION_DEPS
                   for a in [*args.args, *args.posonlyargs, *args.kwonlyargs])

    offenders = []
    routers = source_root / "api" / "routers"
    files = sorted(routers.rglob("*.py"))
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        known = {
            alias.asname or alias.name: alias.name
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name in waits and (node.module or "").startswith("fsmes.services")
        }
        if not known:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not takes_a_request_session(fn):
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)):
                    continue
                module = known.get(node.func.value.id)
                if module and node.func.attr in waits[module]:
                    offenders.append(
                        f"{path.name}:{fn.lineno} {fn.name} -> {module}.{node.func.attr}")

    assert files, "no routers found to check"
    assert offenders == [], (
        "a route holds the plant's database session while it waits on a model: "
        + "; ".join(offenders))
