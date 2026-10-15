"""The lease: which node may do a job that must only be done once.

A plant is its database (decision 0021), so the answer to "am I the reader?"
belongs in that database and nowhere else. One row per role in
`plant_leases`, and one statement decides it:

    UPDATE plant_leases
       SET holder = me, renewed_at = <db now>, expires_at = <db now> + lease
     WHERE role = ? AND (holder = me OR expires_at <= <db now>)

Either the row was free - nobody has renewed it inside the lease - or it is
already mine, or the statement changes nothing and I am a standby. Two nodes
racing on PostgreSQL both reach the row, and the second one re-reads it after
the first commits and finds the condition no longer true, so exactly one wins.
No advisory lock, no `SELECT FOR UPDATE`, no new dependency.

**Every time in this module comes from the database.** `now()` is read back
from the server inside the same transaction that writes the row, so the only
clock involved belongs to the thing both nodes already agree on. Two boxes
whose clocks are a minute apart still hand the role over correctly; a box
whose clock jumps cannot steal a live lease or keep a dead one.

**Losing a lease is not an error.** A holder that fails to renew - its
database connection broke, or it was paused long enough for the lease to
expire and another node to take it - stops working at once and goes back to
being a standby. It does not exit: `fsmes.plant.run` takes the whole plant
down when any child exits, and a node whose reader exited would also stop
answering on its dashboard, which is the opposite of what redundancy is for.

What is **not** leased, and is named here so nobody has to grep for it: ERP
sync, the UNS publisher and the inbound watchers are separate commands that a
redundant plant still runs on one node by hand - the same lease, later. The
PLC write path runs under the reader's lease and is single by design.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from datetime import datetime, timedelta

import structlog
from sqlalchemy import case, or_, select, update
from sqlalchemy.exc import IntegrityError

from fsmes.db import session_scope
from fsmes.domain.leases import ROLE_FLOOR, ROLE_HOUSEKEEPING, ROLE_READER, ROLES, PlantLease

log = structlog.get_logger("leases")

__all__ = [
    "ROLES",
    "ROLE_FLOOR",
    "ROLE_HOUSEKEEPING",
    "ROLE_READER",
    "Holder",
    "claim",
    "db_now",
    "for_role",
    "held_by",
    "holders",
    "node_name",
    "release",
    "run_while_held",
]

# How much of a lease may pass before the holder renews it. A third, so a
# holder has two more tries before anybody else may take the role: one slow
# database query must not hand the plant over.
RENEW_FRACTION = 3.0
# How often a standby asks whether the role has come free. Shorter than the
# lease, or a failover would wait for the poll rather than for the lease.
STANDBY_POLL_SECONDS = 1.0


def node_name(settings) -> str:
    """What this node calls itself.

    `--node a` (or `MES_NODE_ID`) when somebody named it, because that is the
    name they will read in `fsmes plant status`, on the dashboard and in the
    lease row. Otherwise this box and the port it serves on, which is unique
    among the nodes of one plant and is a fact rather than a guess: two nodes
    of one plant on one box differ by port, and on two boxes by host name.
    """
    if settings.node_id:
        return settings.node_id[:80]
    return f"{socket.gethostname()}:{settings.api_port}"[:80]


def db_now(session) -> datetime:
    """The database's own clock, as naive UTC - the only clock this module
    compares anything against."""
    from sqlalchemy import func

    value = session.execute(select(func.now())).scalar_one()
    if not isinstance(value, datetime):
        # SQLite answers CURRENT_TIMESTAMP as text when nothing told the
        # driver what it was looking at.
        value = datetime.fromisoformat(str(value))
    return value.replace(tzinfo=None) if value.tzinfo else value


def claim(session, *, role: str, holder: str, seconds: float) -> bool:
    """Take or renew `role` for `holder`. True means this node may work.

    The row is created on first use rather than by the migration, so a plant
    that never runs redundantly never has one and `fsmes plant status` says
    "nothing holds anything" instead of naming three roles nobody wants.
    """
    now = db_now(session)
    expires = now + timedelta(seconds=seconds)
    done = session.execute(
        update(PlantLease)
        .where(PlantLease.role == role,
               or_(PlantLease.holder == holder, PlantLease.expires_at <= now))
        .values(holder=holder, renewed_at=now, expires_at=expires,
                # Kept across my own renewals, reset when the role changes
                # hands: "since when" is a different fact from "last seen".
                acquired_at=case((PlantLease.holder == holder, PlantLease.acquired_at),
                                 else_=now))
        .execution_options(synchronize_session=False))
    if done.rowcount:
        return True
    if session.execute(select(PlantLease.role).where(PlantLease.role == role)).first():
        return False
    try:
        with session.begin_nested():
            session.add(PlantLease(role=role, holder=holder, acquired_at=now,
                                   renewed_at=now, expires_at=expires))
        return True
    except IntegrityError:
        # Another node inserted the row in the moment between the two
        # statements above. It holds the role; this one asks again next poll.
        return False


def release(session, *, role: str, holder: str) -> None:
    """Give up a role on a clean shutdown, so the other node takes over in a
    poll instead of waiting out the lease. Scoped to this holder: a node that
    has already lost the role must not delete the row of the node that has it."""
    session.execute(
        PlantLease.__table__.delete().where(PlantLease.role == role,
                                            PlantLease.holder == holder))


def holders(session) -> list[dict]:
    """Every lease row, with whether it is still live by the database's clock.

    A list of everything the table holds, including rows whose holder has
    stopped renewing - an expired row is how a plant records that nobody is
    doing that job right now, and hiding it would make a stopped reader look
    like a plant that never had one.
    """
    now = db_now(session)
    rows = session.execute(select(PlantLease).order_by(PlantLease.role)).scalars().all()
    return [{"role": row.role, "holder": row.holder, "live": row.expires_at > now,
             "since": row.acquired_at.isoformat(), "renewed": row.renewed_at.isoformat(),
             "expires": row.expires_at.isoformat()}
            for row in rows]


def held_by(session, holder: str) -> list[str]:
    """The roles this node holds right now, by the database's clock."""
    now = db_now(session)
    rows = session.execute(
        select(PlantLease.role)
        .where(PlantLease.holder == holder, PlantLease.expires_at > now)
        .order_by(PlantLease.role)).scalars().all()
    return list(rows)


class Holder:
    """One node's claim on one role: take it, keep it, notice losing it.

    `held` is what the work asks before it writes anything. It is False
    before the role is taken and the moment a renewal fails, so a gate on it
    cannot write after this node has stopped being the holder - which matters
    because cancelling a task is not instant and a booking retry can be
    asleep in a backoff when the lease goes.
    """

    def __init__(self, role: str, node: str, seconds: float) -> None:
        self.role = role
        self.node = node
        self.seconds = float(seconds)
        self._held = False
        self._said_no_table = False

    @property
    def held(self) -> bool:
        return self._held

    def holds(self) -> bool:
        """The same answer as a callable, for gates that are handed a
        predicate and default to `lambda: True` when nothing is leased."""
        return self._held

    # ---------------------------------------------------------------- sync
    def take(self) -> bool:
        try:
            with session_scope() as session:
                got = claim(session, role=self.role, holder=self.node, seconds=self.seconds)
        except Exception as exc:
            # A database this node cannot reach is a database it cannot
            # prove it holds a lease in. Standby is the safe answer.
            self._say_why_not(exc)
            self._held = False
            return False
        self._held = got
        return got

    def _say_why_not(self, exc: Exception) -> None:
        """Say why this node could not ask for the lease, once per reason.

        A missing lease table is not a database that went away: it is a
        plant whose schema was never created, and the fix is a command. It
        is said once, as an error, because a renewal comes every third of a
        lease and the message never changes - a node standing by for ten
        minutes printed the same stack 450 times before this.
        """
        detail = f"{type(exc).__name__}: {exc}"
        no_table = "plant_leases" in detail and (
            "does not exist" in detail or "no such table" in detail)
        if not no_table:
            log.warning("could not ask for the lease", role=self.role, node=self.node,
                        error=detail[:160])
            return
        if self._said_no_table:
            return
        self._said_no_table = True
        log.error("this plant's database has no lease table, so no node can hold a "
                  "role: create this plant's schema once - `fsmes plant <name> init`, "
                  "or `fsmes init-db` against this database - and start the nodes "
                  "again", role=self.role, node=self.node)

    def drop(self) -> None:
        self._held = False
        try:
            with session_scope() as session:
                release(session, role=self.role, holder=self.node)
        except Exception as exc:
            log.warning("could not give up the lease", role=self.role, node=self.node,
                        error=f"{type(exc).__name__}: {exc}"[:160])

    # --------------------------------------------------------------- async
    async def wait_to_hold(self) -> None:
        """Ask for the role until it is ours. Says so once, not every poll."""
        said = False
        while not await asyncio.to_thread(self.take):
            if not said:
                log.info("standby: another node holds this role",
                         role=self.role, node=self.node, lease_s=self.seconds)
                said = True
            await asyncio.sleep(min(STANDBY_POLL_SECONDS, self.seconds / RENEW_FRACTION))
        log.info("holding this role", role=self.role, node=self.node, lease_s=self.seconds)

    async def renew_until_lost(self) -> str:
        """Renew for as long as this node keeps the role. Returns the reason
        it stopped holding it, which is the line a person reads in the log."""
        every = self.seconds / RENEW_FRACTION
        while True:
            await asyncio.sleep(every)
            if not await asyncio.to_thread(self.take):
                return "another node holds this role now"


def for_role(role: str, settings) -> Holder | None:
    """A holder for this role, or None when this plant is one box.

    None is the whole of the "redundancy off" story: every caller runs its
    work directly, every gate keeps its `lambda: True` default, and a plant
    that is not redundant does not read or write the lease table at all.
    """
    if not getattr(settings, "redundancy", False):
        return None
    return Holder(role, node_name(settings), settings.lease_seconds)


async def run_while_held(holder: Holder, factory) -> None:
    """Run `factory()` only while this node holds the role, for ever.

    Standby, then holder, then - if the lease is lost - standby again, with
    the work torn down in between. It returns only when the work itself
    returns while the lease is still held, which for the reader and the floor
    means never.
    """
    while True:
        await holder.wait_to_hold()
        work = asyncio.create_task(factory())
        renew = asyncio.create_task(holder.renew_until_lost())
        try:
            done, _ = await asyncio.wait({work, renew}, return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                holder.drop()
                return work.result()
            why = renew.result()
            log.warning("stopping this role's work", role=holder.role, node=holder.node, why=why)
        finally:
            for task in (work, renew):
                task.cancel()
            for task in (work, renew):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
