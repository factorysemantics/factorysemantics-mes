"""Authentication and roles.

Passwords are PBKDF2-hashed; sessions are HMAC-signed tokens carrying the
user's code, role, and expiry. Standard library only — no crypto dependency
to audit, and nothing to run alongside the MES.

A role is a named bundle of capabilities, stored in the database so a plant can
define its own without a deployment. The product ships five - the original four
rungs expressed as bundles, so no existing account changes, plus Quality
Inspector, which the old ladder could not express. See services/capabilities.py
for the vocabulary.

Capabilities are resolved from the database on each request rather than baked
into the token, so revoking a power takes effect immediately instead of at the
user's next sign-in.
"""

import base64
import hashlib
import hmac
import json
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import Person
from fsmes.services import Conflict, Invalid, NotFound, audit

# The legacy ladder. Kept only so has_role() still answers for anything that
# asks in those terms; the gates themselves ask about capabilities now.
ROLES = ("viewer", "operator", "supervisor", "admin")
_LEVEL = {role: i for i, role in enumerate(ROLES)}


def ensure_builtin_roles(session: Session) -> int:
    """Create any missing built-in role. Idempotent, safe on every start.

    New capabilities added to a shipped role reach existing databases here -
    a plant that installed before a capability existed should still have it
    on the built-in roles, without an admin re-creating them by hand.
    """
    import json

    from fsmes.domain import Role
    from fsmes.services import capabilities as caps

    made = 0
    for code, spec in caps.BUILTIN_ROLES.items():
        role = session.scalar(select(Role).where(Role.code == code))
        if role is None:
            session.add(Role(code=code, name=spec["name"],
                             description=spec["description"],
                             capabilities=json.dumps(spec["capabilities"]),
                             builtin=True))
            made += 1
        elif role.builtin and set(role.granted()) != set(spec["capabilities"]):
            role.capabilities = json.dumps(spec["capabilities"])
    session.flush()
    return made


def capabilities_for(session: Session, role_code: str) -> set[str]:
    """Everything a role grants.

    An unknown role grants nothing. That is deliberate: a typo in a role name
    should lock someone out loudly rather than quietly hand them a default.
    """
    from fsmes.domain import Role
    from fsmes.services import capabilities as caps

    role = session.scalar(select(Role).where(Role.code == role_code))
    if role is not None:
        return set(role.granted())
    # Before the roles table is seeded (a fresh in-memory database in a test,
    # or the first request after an upgrade), fall back to what the product
    # ships so the system is never unusable.
    spec = caps.BUILTIN_ROLES.get(role_code)
    return set(spec["capabilities"]) if spec else set()


def current_role(session: Session, claims: dict) -> str | None:
    """The role the signed-in person holds right now.

    Deliberately not the role inside the token. A token is issued once and
    lives for its TTL, so trusting its role claim means a revoked power stays
    usable until the person happens to sign in again - which for a plant
    account could be weeks. Reading the person on each request costs one
    indexed lookup and makes revocation immediate.

    Returns None if the account no longer exists or can no longer sign in, so
    deleting someone locks them out at once too.
    """
    person = session.scalar(select(Person).where(Person.code == claims.get("sub")))
    if person is None or person.password_hash is None:
        return None
    return person.role


def can(session: Session, role_code: str, capability: str) -> bool:
    return capability in capabilities_for(session, role_code)

_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        _, iterations, salt_hex, digest_hex = encoded.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _sign(payload: bytes, secret: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(secret.encode(), payload, hashlib.sha256).digest()).decode().rstrip("=")


def issue_token(person: Person, secret: str, ttl_seconds: int) -> str:
    payload = json.dumps(
        {"sub": person.code, "role": person.role, "exp": int(utcnow().timestamp()) + ttl_seconds},
        separators=(",", ":"),
    ).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{body}.{_sign(payload, secret)}"


def read_token(token: str, secret: str) -> dict | None:
    """Return the token's claims, or None if it is malformed, forged, or expired."""
    try:
        body, signature = token.split(".")
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        return None
    claims = json.loads(payload)
    if claims.get("exp", 0) < utcnow().timestamp():
        return None
    return claims


def authenticate(session: Session, code: str, password: str) -> Person:
    person = session.scalar(select(Person).where(Person.code == code.upper()))
    if person is None or not verify_password(password, person.password_hash):
        raise Invalid("invalid credentials")  # never reveal which half was wrong
    return person


def has_role(role: str, required: str) -> bool:
    return _LEVEL.get(role, -1) >= _LEVEL[required]


def create_user(
    session: Session, *, code: str, name: str, password: str, role: str = "operator", actor: str = "system"
) -> Person:
    from fsmes.domain import Role as RoleModel

    known = {r.code for r in session.scalars(select(RoleModel))} or set(ROLES)
    if role not in known:
        raise Invalid(f"unknown role {role!r} (expected one of {', '.join(sorted(known))})")
    code = code.upper()
    if session.scalar(select(Person).where(Person.code == code)):
        raise Conflict(f"user {code!r} already exists")
    person = Person(code=code, name=name, role=role, password_hash=hash_password(password))
    session.add(person)
    session.flush()
    audit.record(
        session,
        actor=actor,
        action="user.created",
        entity_type="person",
        entity_id=code,
        after={"name": name, "role": role},
    )
    return person


def set_password(session: Session, *, code: str, password: str, actor: str = "system") -> Person:
    person = session.scalar(select(Person).where(Person.code == code.upper()))
    if person is None:
        raise NotFound(f"user {code!r} not found")
    person.password_hash = hash_password(password)
    audit.record(
        session, actor=actor, action="user.password_changed", entity_type="person", entity_id=person.code
    )
    return person
