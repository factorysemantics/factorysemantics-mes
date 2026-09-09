"""Sign-in endpoints. Login sets both a bearer token (for API clients) and an
HttpOnly cookie (for the dashboard), so one mechanism serves both."""

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import select

from fsmes.api.deps import SESSION_COOKIE, ActorDep, DbDep, UserDep, require
from fsmes.config import get_settings
from fsmes.domain import Person
from fsmes.services import auth

router = APIRouter()


class LoginIn(BaseModel):
    code: str
    password: str


class TokenOut(BaseModel):
    token: str
    code: str
    name: str
    role: str


@router.post("/login")
def login(body: LoginIn, response: Response, db: DbDep) -> TokenOut:
    settings = get_settings()
    person = auth.authenticate(db, body.code, body.password)
    token = auth.issue_token(person, settings.secret_key, settings.token_ttl_seconds)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=settings.token_ttl_seconds
    )
    return TokenOut(token=token, code=person.code, name=person.name, role=person.role)


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE)
    return {"signed_out": True}


@router.get("/me")
def me(user: UserDep, db: DbDep) -> dict:
    """Who is signed in, and what they may do.

    The screens hide what a person cannot use, and they need the capability
    list to do it honestly - showing a button that will 403 is worse than not
    showing it. The role is read live rather than from the token, so a
    reassignment reaches the UI on its next poll.
    """
    person = db.scalar(select(Person).where(Person.code == user["sub"]))
    role = auth.current_role(db, user) or user["role"]
    return {
        "code": user["sub"],
        "role": role,
        "name": person.name if person else user["sub"],
        "capabilities": sorted(auth.capabilities_for(db, role)),
    }


class UserIn(BaseModel):
    code: str
    name: str
    password: str
    role: str = "operator"


@router.post("/users", status_code=201, dependencies=[require("users.manage")])
def create_user(body: UserIn, db: DbDep, actor: ActorDep) -> dict:
    person = auth.create_user(
        db, code=body.code, name=body.name, password=body.password, role=body.role, actor=actor
    )
    return {"code": person.code, "name": person.name, "role": person.role}


class PasswordIn(BaseModel):
    password: str


@router.post("/users/{code}/password", dependencies=[require("users.manage")])
def set_password(code: str, body: PasswordIn, db: DbDep, actor: ActorDep) -> dict:
    person = auth.set_password(db, code=code, password=body.password, actor=actor)
    return {"code": person.code, "password_changed": True}
