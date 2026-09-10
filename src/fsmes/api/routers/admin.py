"""Administration: who can sign in, and what each role is allowed to do.

Roles are data here, not code. An admin can build "Quality Inspector" - a
bundle that records inspections and nothing else - without a deployment, which
is exactly what the old role ladder made impossible.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from fsmes.api import paging
from fsmes.api.deps import ActorDep, DbDep, require
from fsmes.domain import Person, Role
from fsmes.services import Conflict, Invalid, NotFound, audit, auth
from fsmes.services import capabilities as caps

router = APIRouter()


class RoleIn(BaseModel):
    code: str
    name: str
    description: str | None = None
    capabilities: list[str] = []


class UserRoleIn(BaseModel):
    role: str


def _role_out(role: Role) -> dict:
    return {
        "code": role.code,
        "name": role.name,
        "description": role.description,
        "capabilities": role.granted(),
        "builtin": role.builtin,
        "protected": role.code in caps.PROTECTED,
    }


@router.get("/capabilities")
def list_capabilities() -> dict:
    """The vocabulary a role is built from, each with what it means.

    Readable by anyone signed in: a screen has to explain what it is offering,
    and the list is not a secret - what a person *has* is.
    """
    return {"capabilities": [{"name": n, "description": d}
                             for n, d in sorted(caps.CAPABILITIES.items())]}


@router.get("/roles")
def list_roles(db: DbDep) -> list[dict]:
    auth.ensure_builtin_roles(db)
    return [_role_out(r) for r in db.scalars(select(Role).order_by(Role.code))]


@router.post("/roles", status_code=201, dependencies=[require("users.manage")])
def create_role(body: RoleIn, db: DbDep, actor: ActorDep) -> dict:
    if db.scalar(select(Role).where(Role.code == body.code)):
        raise Conflict(f"role {body.code} already exists")
    # A misspelt capability grants nothing and says nothing, so refuse it here
    # rather than hand back a role that quietly does less than it claims.
    bad = caps.unknown(body.capabilities)
    if bad:
        raise Invalid(f"unknown capabilities: {', '.join(bad)}")

    role = Role(code=body.code, name=body.name, description=body.description,
                capabilities=json.dumps(body.capabilities), builtin=False)
    db.add(role)
    db.flush()
    audit.record(db, actor=actor, action="role.created", entity_type="role",
                 entity_id=body.code, after={"capabilities": body.capabilities})
    return _role_out(role)


@router.put("/roles/{code}", dependencies=[require("users.manage")])
def update_role(code: str, body: RoleIn, db: DbDep, actor: ActorDep) -> dict:
    role = db.scalar(select(Role).where(Role.code == code))
    if role is None:
        raise NotFound(f"no role {code}")
    bad = caps.unknown(body.capabilities)
    if bad:
        raise Invalid(f"unknown capabilities: {', '.join(bad)}")

    before = role.granted()
    role.name = body.name
    role.description = body.description
    role.capabilities = json.dumps(body.capabilities)
    db.flush()
    audit.record(db, actor=actor, action="role.updated", entity_type="role",
                 entity_id=code, before={"capabilities": before},
                 after={"capabilities": body.capabilities})
    return _role_out(role)


@router.delete("/roles/{code}", dependencies=[require("users.manage")])
def delete_role(code: str, db: DbDep, actor: ActorDep) -> dict:
    role = db.scalar(select(Role).where(Role.code == code))
    if role is None:
        raise NotFound(f"no role {code}")
    if code in caps.PROTECTED:
        # An MES with no admin role is a plant nobody can administer.
        raise Invalid(f"{code} is protected and cannot be deleted")

    holders = db.scalars(select(Person).where(Person.role == code)).all()
    if holders:
        raise Invalid(
            f"{len(holders)} account(s) still hold {code}: "
            f"{', '.join(p.code for p in holders)}. Reassign them first."
        )
    db.delete(role)
    audit.record(db, actor=actor, action="role.deleted", entity_type="role",
                 entity_id=code, before={"capabilities": role.granted()})
    return {"deleted": code}


@router.get("/users", dependencies=[require("users.manage")])
def list_users(
    db: DbDep,
    role: str | None = None,
    q: str | None = Query(None, description="Match a code or a name."),
    limit: int = paging.LimitQuery,
    offset: int = paging.OffsetQuery,
) -> dict:
    """People, one page at a time, filterable by role or name.

    Three hundred employees is an ordinary plant, and scrolling is not how
    anybody finds one of them.
    """
    query = select(Person).order_by(Person.code)
    if role:
        query = query.where(Person.role == role)
    if q:
        like = f"%{q}%"
        query = query.where(Person.code.ilike(like) | Person.name.ilike(like))

    people, total = paging.paginate(db, query, limit, offset)
    grants = {r.code: r.granted() for r in db.scalars(select(Role))}
    return paging.page(
        [
            {
                "code": p.code,
                "name": p.name,
                "role": p.role,
                "can_sign_in": p.password_hash is not None,
                "capabilities": grants.get(p.role, []),
            }
            for p in people
        ],
        total, limit, offset,
    )


@router.put("/users/{code}/role", dependencies=[require("users.manage")])
def set_user_role(code: str, body: UserRoleIn, db: DbDep, actor: ActorDep) -> dict:
    person = db.scalar(select(Person).where(Person.code == code))
    if person is None:
        raise NotFound(f"no user {code}")
    if db.scalar(select(Role).where(Role.code == body.role)) is None:
        raise Invalid(f"unknown role {body.role}")

    before = person.role
    person.role = body.role
    db.flush()
    audit.record(db, actor=actor, action="user.role_changed", entity_type="user",
                 entity_id=code, before={"role": before}, after={"role": body.role})
    # Capabilities resolve per request, so this takes effect on the user's very
    # next call rather than at their next sign-in.
    return {"code": code, "role": body.role, "effective": "immediately"}
