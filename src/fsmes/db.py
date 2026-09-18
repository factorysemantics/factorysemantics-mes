"""Database plumbing: one engine, one session factory, one unit-of-work helper.

The same code runs on SQLite (default) and PostgreSQL — the URL decides.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fsmes.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for every MES-TWIN table."""


#: Execution option that marks a connection as a read-only unit of work. Set by
#: `read_only_session` and read by the SQLite transaction handler below; it is
#: never a hint, because the same call that sets it also has the database
#: refuse writes on that connection.
READ_ONLY = "fsmes_read_only"


def utcnow() -> datetime:
    """Naive UTC — the single timestamp convention used across the system."""
    return datetime.now(UTC).replace(tzinfo=None)


def _sqlite_transactions(engine: Engine) -> None:
    """SQLite only: take the write lock when a transaction begins.

    SQLite has one writer. A transaction that is already open when it first
    writes has to upgrade to the write lock, and SQLite refuses that upgrade
    outright — `database is locked`, immediately — rather than waiting, because
    waiting for it could only deadlock. `busy_timeout` covers waiting for a
    lock; it does not cover a refusal, which is why setting it did not stop the
    OPC agent's booking from failing. `BEGIN IMMEDIATE` takes the write lock at
    the start instead, turning the refusal into a wait that `busy_timeout`
    does cover.

    Booking a batch of plant readings is exactly that shape: it opens a
    savepoint per state change, which opens the transaction, and writes inside
    it.

    Every transaction takes the lock unless the caller has said, in so many
    words, that this one only reads — `read_only_session`, below. Guessing was
    never on: assuming a transaction would not write is exactly what was wrong
    before this handler existed, which is why the read-only path does not ask
    the caller to promise and then trust them. It makes the database refuse
    the write.

    WHAT IT COST TO LEARN THAT. Two lab plants ran six hours at replay speed
    10 on 2026-09-18 and one of them logged **18,564** `database is locked`,
    825 HTTP 500s and 754 failed shop-floor steps, and could not book its own
    production. `/dashboard/summary` was spending ten seconds computing a KPI
    inside its request session, and for those ten seconds nothing in the plant
    could write anything — not the agent's readings, not the floor's
    inspections, not a sign-in. A read holding the write lock is how a plant
    stops being able to record what it made. Both halves of that night are
    fixed here: PR #80 took the reads off the lock, and PR #81 took the plant's
    writers, its model endpoints and its log off the consequences.

    The cost of taking the lock is that SQLite serialises transactions rather
    than only writes; SQLite has one writer either way, and a deployment that
    needs more than that is what PostgreSQL is for. So nothing may hold a
    transaction open across a network call, either kind: a write transaction
    blocks every other writer, and a read transaction blocks WAL checkpoints
    for as long as it lives. See `_adjustment_loop` in the OPC agent, which
    reads first and closes before it goes to the PLC, and the two tests in
    `test_sqlite_write_locks.py` that hold every module and every route to it.

    PostgreSQL needs none of this and gets none of it — this runs only when the
    URL is SQLite.
    """

    busy_timeout_ms = get_settings().sqlite_busy_timeout_ms

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # busy_timeout first: the pragmas after it, the WAL switch included,
        # can themselves meet a lock another process is holding.
        dbapi_conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
        # WAL lets readers and the one writer work at the same time.
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        # In WAL, NORMAL fsyncs at a checkpoint rather than at every commit.
        # What it costs, said plainly: a power cut or a kernel panic can lose
        # the last commits that had not reached a checkpoint. It cannot
        # corrupt the database, and a process crash loses nothing. A plant
        # booking ten times a second pays for FULL on every one of those
        # commits, with the write lock held while it waits for the disk.
        dbapi_conn.execute("PRAGMA synchronous=NORMAL")
        # pysqlite starts transactions on its own, never as IMMEDIATE, and
        # not at all before a plain SELECT or a SAVEPOINT. Hand transaction
        # control to SQLAlchemy so the handler below is the only thing that
        # begins one and can choose how.
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def _sqlite_begin(conn):
        if conn.get_execution_options().get(READ_ONLY):
            # A unit of work that cannot write has no reason to hold the one
            # writer up, and every reason not to: a screen refresh that takes
            # a second of database work used to make every sign-in behind it
            # wait that second out, and time out at five.
            #
            # `query_only` is what makes the deferred BEGIN safe. A deferred
            # transaction that writes after all is the refusal this handler
            # exists to prevent, so the database is told to refuse the write
            # rather than this module hoping nobody tries. It is set outside
            # the transaction, and cleared when the connection goes back to
            # the pool.
            conn.exec_driver_sql("PRAGMA query_only=ON")
            conn.exec_driver_sql("BEGIN")
            return
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    @event.listens_for(engine, "checkin")
    def _clear_query_only(dbapi_conn, _record):
        # Connections are pooled, so a read-only unit of work must not hand
        # the next caller a connection that silently refuses to write.
        dbapi_conn.execute("PRAGMA query_only=OFF")


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        _sqlite_transactions(engine)
    return engine


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def read_only_session() -> Iterator[Session]:
    """One unit of work that reads, and that the database will not let write.

    WHY THIS EXISTS. On SQLite every transaction takes the single write lock
    (see `_sqlite_transactions`), reads included. That was fine while reads
    were milliseconds. On 2026-09-18 a six-machine lab plant with eight hours
    of history answered `/dashboard/summary` in ten seconds, and for those ten
    seconds nothing else on that plant could open a transaction: twenty
    sign-ins in a row timed out at five seconds, and `/equipment/{code}/oee`
    and `/analysis/oee` returned 500 with `database is locked` raised by
    `BEGIN IMMEDIATE` itself. A person who cannot sign in reads that as "the
    MES is down", and says so.

    A read has no business holding the writer up. This gives the reads that
    never write a transaction that never takes the lock — and rather than
    trust the caller's word for "never writes", it tells the database to
    refuse: `PRAGMA query_only` on SQLite, `SET TRANSACTION READ ONLY` on
    PostgreSQL. A write attempted inside one of these raises where it is
    written rather than corrupting the promise for everybody else.

    Nothing is committed. A unit of work that cannot write has nothing to
    commit, and the rollback at the end is bookkeeping, not a failure.
    """
    engine = get_engine()
    connection = engine.connect().execution_options(**{READ_ONLY: True})
    session = Session(bind=connection, expire_on_commit=False, autoflush=False)
    try:
        if engine.dialect.name == "postgresql":
            # PostgreSQL readers never block a writer, so this buys no speed
            # there. It buys the same guarantee: a test that proves a screen
            # cannot write proves it on the database a plant actually runs.
            session.execute(text("SET TRANSACTION READ ONLY"))
        yield session
    finally:
        session.rollback()
        session.close()
        connection.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """One unit of work: commit on success, roll back on any error.

    On SQLite it holds the write lock for its whole life, so keep it short and
    never wrap a network call in one. If it only reads, say so with
    `read_only_session` instead and it will hold no write lock at all.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
