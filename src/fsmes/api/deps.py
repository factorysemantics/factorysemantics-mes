"""FastAPI dependencies: a DB unit-of-work per request, and the signed-in user.

The session token arrives either as `Authorization: Bearer <token>` (API
clients) or as the `mes_session` cookie (the dashboard). Whoever it identifies
becomes the audit actor, so the audit trail records real people rather than a
self-declared header.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from fsmes.config import get_settings
from fsmes.db import read_only_session, session_scope
from fsmes.services import auth

SESSION_COOKIE = "mes_session"

# Methods that only answer a question. A request with one of these gets a
# session that cannot write even when the route asked for `DbDep`, so on SQLite
# it never takes the single write lock while it computes. `ReadDbDep` below is
# the same thing said explicitly by a route; this is the floor under every
# route that has not said it, because a GET that takes the write lock is a GET
# that can stop the plant booking, and there are more routes than anyone will
# remember to move.
#
# The method decides, rather than a per-route declaration, because HTTP already
# says this and a second place to say it is a second place to get it wrong. A
# GET that writes is then not a slow request but an outright failure — `attempt
# to write a readonly database` — which is the right answer for a GET that
# writes.
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_db(request: Request) -> Iterator[Session]:
    if request.method in READ_METHODS:
        with read_only_session() as session:
            yield session
    else:
        with session_scope() as session:
            yield session


@contextmanager
def short_read() -> Iterator[Session]:
    """A read that opens and closes inside the call that makes it.

    Deliberately not a FastAPI dependency. A dependency's session is held
    until the response has been sent, which is the whole problem this
    avoids: `/design/chat` spends up to four network calls on a model, and
    while a request session is open on SQLite, nothing anywhere in the
    plant can write. The capability gate and the endpoints that call a
    model read through this instead, so they hold nothing while they wait.

    Callers reach it as `deps.short_read()`, through the module, so the
    test suite can point it at the session a test is working in — the same
    reason, and the same place, as its override of `get_db`.
    """
    with read_only_session() as session:
        yield session


def get_read_db() -> Iterator[Session]:
    """A unit of work for an endpoint that only reads.

    On SQLite this is the difference between a screen refresh and a plant
    that cannot be signed into while one is computing — see
    `fsmes.db.read_only_session`. The database refuses writes on it, so an
    endpoint that starts to write one day fails loudly here rather than
    quietly taking the write lock back.
    """
    with read_only_session() as session:
        yield session


DbDep = Annotated[Session, Depends(get_db)]
#: For endpoints that read and never write. Same session type; a transaction
#: that cannot write and does not queue behind the one that can.
ReadDbDep = Annotated[Session, Depends(get_read_db)]


def _token_from(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header.removeprefix("Bearer ").strip()
    return request.cookies.get(SESSION_COOKIE)


def current_user(request: Request) -> dict:
    """The signed-in user's claims: {"sub": code, "role": role}."""
    token = _token_from(request)
    claims = auth.read_token(token, get_settings().secret_key) if token else None
    if claims is None:
        raise HTTPException(401, "sign in required", headers={"WWW-Authenticate": "Bearer"})
    return claims


UserDep = Annotated[dict, Depends(current_user)]


def require(capability: str):
    """Dependency factory: require one capability.

    Gates ask what a person may *do*, not how senior they are. The role a
    token carries is resolved to its capabilities from the database on each
    request, so revoking a power takes effect at once rather than at the
    user's next sign-in.

    The gate reads in a session of its own, closed before the endpoint runs,
    rather than borrowing the request's. Two reasons, both learned the hard
    way. It is a read, and a read should take no write lock. And it runs
    first, so on a write endpoint it used to open the request's transaction
    before the handler had done anything — which on `/design/chat` meant the
    plant's one write lock was taken and then held across four calls to a
    model.
    """

    def _check(user: UserDep) -> dict:
        with short_read() as db:
            role = auth.current_role(db, user)
            if role is None:
                raise HTTPException(401, "this account can no longer sign in",
                                    headers={"WWW-Authenticate": "Bearer"})
            allowed = auth.can(db, role, capability)
        if not allowed:
            raise HTTPException(
                403,
                f"this action needs the {capability!r} capability, "
                f"which the {role!r} role does not grant",
            )
        # Hand on the live role, so an endpoint reading user["role"] sees what
        # the person holds rather than what their token remembers.
        return {**user, "role": role}

    return Depends(_check)


def require_role(role: str):
    """The old rank check, kept for anything genuinely about seniority."""

    def _check(user: UserDep) -> dict:
        if not auth.has_role(user["role"], role):
            raise HTTPException(403, f"this action requires the {role} role or higher")
        return user

    return Depends(_check)


ON_BEHALF_HEADER = "X-On-Behalf-Of"


class Actor(str):
    """The audit actor: an account code that, for an agent, also carries the
    person it acts for. A str, so every service that stores or logs an actor
    keeps working; `audit.record` reads the extra attribute."""

    on_behalf_of: str | None = None


def get_actor(user: UserDep, request: Request, db: DbDep) -> str:
    """Who this request is audited as - and, for an agent, who it acts for.

    An account in the `agent` role may name the person it is acting for in
    the X-On-Behalf-Of header; the name must be a real account, and it lands
    on every audit row the request writes. Anyone else sending the header is
    ignored: a person cannot claim to be acting for someone by asserting it.
    """
    from sqlalchemy import select

    from fsmes.domain import Person

    actor = Actor(user["sub"])
    claimed = (request.headers.get(ON_BEHALF_HEADER) or "").strip().upper() or None
    if claimed and auth.current_role(db, user) == "agent":
        if db.scalar(select(Person).where(Person.code == claimed)) is None:
            raise HTTPException(400, f"{ON_BEHALF_HEADER} names no account: {claimed!r}")
        actor.on_behalf_of = claimed
    return actor


ActorDep = Annotated[str, Depends(get_actor)]
