"""The Idempotency-Key middleware.

A write that carries `Idempotency-Key` is performed once per (account, key).
The second time the same account sends the same key, the stored answer comes
back with `Idempotent-Replay: true` and nothing runs. Only successful answers
are stored: a refused or failed write is not an answer worth repeating.
"""

from __future__ import annotations

import json

from fastapi import Request, Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from fsmes.config import get_settings
from fsmes.db import session_scope
from fsmes.domain import IdempotencyKey
from fsmes.services import auth

HEADER = "Idempotency-Key"
WRITES = {"POST", "PUT", "PATCH", "DELETE"}


def _actor(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header.removeprefix("Bearer ").strip()
    else:
        token = request.cookies.get("mes_session")
    claims = auth.read_token(token, get_settings().secret_key) if token else None
    return claims["sub"] if claims else None


async def idempotency(request: Request, call_next):
    key = (request.headers.get(HEADER) or "").strip()
    if not key or request.method not in WRITES:
        return await call_next(request)
    actor = _actor(request)
    if actor is None:
        return await call_next(request)  # the route will answer 401 itself

    # Database work runs off the event loop. The route's own session commits
    # in its dependency teardown, which is scheduled after the response is
    # handed back; a synchronous write here would block the loop while
    # waiting for a lock that teardown cannot release until the loop is free.
    # Found live: "database is locked" after five seconds, on every keyed write.
    seen = await run_in_threadpool(_lookup, actor, key)
    if seen is not None:
        status, body = seen
        return Response(content=json.dumps(body), status_code=status,
                        media_type="application/json", headers={"Idempotent-Replay": "true"})

    response = await call_next(request)
    chunks = [chunk async for chunk in response.body_iterator]
    raw = b"".join(chunks)
    if 200 <= response.status_code < 300:
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        await run_in_threadpool(_store, actor, key, request.method, request.url.path, response.status_code, body)
    return Response(content=raw, status_code=response.status_code,
                    headers=dict(response.headers), media_type=response.media_type)


def _lookup(actor: str, key: str) -> tuple[int, object] | None:
    with session_scope() as db:
        seen = db.scalar(select(IdempotencyKey).where(IdempotencyKey.actor == actor, IdempotencyKey.key == key))
        return (seen.status_code, seen.body) if seen is not None else None


def _store(actor: str, key: str, method: str, path: str, status: int, body) -> None:
    with session_scope() as db:
        db.add(IdempotencyKey(actor=actor, key=key, method=method, path=path,
                              status_code=status, body=body))
