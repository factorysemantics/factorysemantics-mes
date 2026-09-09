"""Database plumbing: one engine, one session factory, one unit-of-work helper.

The same code runs on SQLite (default) and PostgreSQL — the URL decides.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fsmes.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for every MES-TWIN table."""


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

    Every transaction takes the lock, reads included. Telling the two apart
    would need this module to know, before the first statement, what the
    caller is going to do — and assuming a transaction would not write is
    exactly what was wrong before. The cost is that SQLite serialises
    transactions rather than only writes; SQLite has one writer either way,
    and a deployment that needs more than that is what PostgreSQL is for. So
    nothing may hold a transaction open across a network call: see
    `_adjustment_loop` in the OPC agent, which reads first and closes before
    it goes to the PLC.

    PostgreSQL needs none of this and gets none of it — this runs only when the
    URL is SQLite.
    """

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # busy_timeout first: the pragmas after it, the WAL switch included,
        # can themselves meet a lock another process is holding.
        dbapi_conn.execute("PRAGMA busy_timeout=5000")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
        # WAL lets readers and the one writer work at the same time.
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        # pysqlite starts transactions on its own, never as IMMEDIATE, and
        # not at all before a plain SELECT or a SAVEPOINT. Hand transaction
        # control to SQLAlchemy so the handler below is the only thing that
        # begins one and can choose how.
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def _sqlite_begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")


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
def session_scope() -> Iterator[Session]:
    """One unit of work: commit on success, roll back on any error.

    On SQLite it holds the write lock for its whole life, so keep it short and
    never wrap a network call in one.
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
