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
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fsmes.config import get_settings
from fsmes.db import read_only_session, session_scope
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


class Idempotency:
    """Plain ASGI rather than `@app.middleware("http")`, and on purpose.

    The key is stored after the route has finished and before the client is
    answered, so a retry that arrives the moment the answer does is a replay
    and not a second write. "After the route has finished" has to include its
    database session's commit, which FastAPI runs in the route's teardown,
    after the route has sent its last body message.

    Starlette 1.7 made `call_next` hold that last message until the outer
    response has gone out. A store made inside `call_next`'s middleware then
    waits on SQLite's write lock while the route's session holds it, and the
    session cannot commit until the store has finished: "database is locked"
    once `busy_timeout` runs out, on every keyed write, the assistant's
    "Do it" included. Here the route's messages are held in a list instead,
    so the route runs to the end of its teardown, then the key is stored,
    then the held messages are sent.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in WRITES:
            return await self.app(scope, receive, send)
        request = Request(scope)
        key = (request.headers.get(HEADER) or "").strip()
        actor = _actor(request) if key else None
        if actor is None:
            # No key, or no one signed in: the route will answer 401 itself.
            return await self.app(scope, receive, send)

        # Database work runs off the event loop: a synchronous wait for
        # SQLite's lock here would stop every other request while it waits.
        seen = await run_in_threadpool(_lookup, actor, key)
        if seen is not None:
            status, body = seen
            replay = Response(content=json.dumps(body), status_code=status,
                              media_type="application/json", headers={"Idempotent-Replay": "true"})
            return await replay(scope, receive, send)

        held: list[Message] = []

        async def hold(message: Message) -> None:
            held.append(message)

        await self.app(scope, receive, hold)
        start = next((m for m in held if m["type"] == "http.response.start"), None)
        status = start["status"] if start else 500
        if 200 <= status < 300:
            raw = b"".join(m.get("body", b"") for m in held if m["type"] == "http.response.body")
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                body = None
            await run_in_threadpool(_store, actor, key, scope["method"], request.url.path, status, body)
        for message in held:
            await send(message)


def _lookup(actor: str, key: str) -> tuple[int, object] | None:
    # A read, and said so: it runs before the route's own session and used
    # to take SQLite's write lock ahead of it for the length of a SELECT.
    with read_only_session() as db:
        seen = db.scalar(select(IdempotencyKey).where(IdempotencyKey.actor == actor, IdempotencyKey.key == key))
        return (seen.status_code, seen.body) if seen is not None else None


def _store(actor: str, key: str, method: str, path: str, status: int, body) -> None:
    with session_scope() as db:
        db.add(IdempotencyKey(actor=actor, key=key, method=method, path=path,
                              status_code=status, body=body))
