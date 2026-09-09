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
