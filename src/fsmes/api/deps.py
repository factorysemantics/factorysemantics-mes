"""FastAPI dependencies: a DB unit-of-work per request, and the signed-in user.

The session token arrives either as `Authorization: Bearer <token>` (API
clients) or as the `mes_session` cookie (the dashboard). Whoever it identifies
becomes the audit actor, so the audit trail records real people rather than a
self-declared header.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from fsmes.config import get_settings
from fsmes.db import session_scope
from fsmes.services import auth

SESSION_COOKIE = "mes_session"


def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


DbDep = Annotated[Session, Depends(get_db)]


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
    """

    def _check(user: UserDep, db: DbDep) -> dict:
        role = auth.current_role(db, user)
        if role is None:
            raise HTTPException(401, "this account can no longer sign in",
                                headers={"WWW-Authenticate": "Bearer"})
        if not auth.can(db, role, capability):
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
