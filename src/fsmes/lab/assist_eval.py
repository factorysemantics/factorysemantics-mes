"""The two ways the assistant's request suite is actually run.

The suite and the scoring are rules and live in `services.assist_eval`. What is
here is the harness each mode needs, which is edge work either way: a scripted
run builds the product's own API in this process and points the tool layer at it;
a live run talks to a plant over HTTP. A service may not reach up to an app or a
seed script, and this is where the reach belongs.

Nothing here starts a plant on anybody's machine. The scripted plant is an
in-memory database and an in-process app: it listens on no port, writes no file,
and is gone when the command is. Standing order four, kept the way the rest of
this package keeps it - a person types `fsmes assist eval`.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from types import SimpleNamespace

from fsmes.lab.assist_fixtures import (
    ARRANGES,
    Plantview,
    Unarrangeable,
    arrange,
    missing,
    why_not,
)
from fsmes.services.assist_eval import (
    SCRIPTED_PLANT,
    Case,
    HistoryRefused,
    Outcome,
    Turn,
    capabilities_of,
    plan_for,
    score,
    scripted_model,
    turn_from_reply,
)

#: What one live run may spend before it stops and says so. A number, not a
#: hope: `docs/ai/BUDGET.md` is a monthly cap and an eval is one line on it.
DEFAULT_MAX_USD = 1.00

KEY = "ANTHROPIC_API_KEY"


# The fixtures themselves, and the rule about which of them a run may put on a
# plant, live in `assist_fixtures` and are re-exported here: what is in this
# module is the two ways of reaching a plant to do it - in-process for a
# scripted run, over its own HTTP API for a live one.
__all__ = ["ARRANGES", "DEFAULT_MAX_USD", "KEY", "LiveRefused", "Plant", "Plantview",
           "Unarrangeable", "arrange", "arrange_live", "missing", "run_live",
           "run_scripted", "scripted_plant", "why_not"]


# ---------------------------------------------------------- a plant to run on

@contextlib.contextmanager
def scripted_plant():
    """A seeded plant, in memory, reachable by the real tools.

    The MCP tools work only through the API as a signed-in account - that is
    the write discipline and it is not bypassed here. So the app is built, the
    plant seeded, an AGENT account created, and the tool layer's client cache
    pointed at the in-process app. Everything a tool does in scripted mode is a
    real HTTP request through the real gates; nothing leaves this process and
    nothing touches a file.
    """
    from contextlib import contextmanager as _cm

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session as SASession
    from sqlalchemy.pool import StaticPool

    import fsmes.domain  # noqa: F401  (register every table)
    from fsmes import mcp_server
    from fsmes.api import deps, idempotency
    from fsmes.api.app import create_app
    from fsmes.api.deps import get_db, get_read_db
    from fsmes.db import Base
    from fsmes.domain import IdempotencyKey
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # The idempotency middleware writes off the event loop, so it gets a
    # database of its own rather than sharing this one session across threads.
    keys = create_engine("sqlite://", connect_args={"check_same_thread": False},
                         poolclass=StaticPool)
    IdempotencyKey.__table__.create(keys)

    @_cm
    def keys_scope():
        with SASession(keys) as s:
            yield s
            s.commit()

    session = SASession(engine, expire_on_commit=False)
    seed_demo_plant(session)
    auth.ensure_builtin_roles(session)
    auth.create_user(session, code=mcp_server.AGENT_USER, name="Plant Agent",
                     password=mcp_server.AGENT_PASSWORD, role="agent")
    # One account per role the suite asks as, so `on_behalf_of` names somebody
    # this plant has actually heard of. A dry run does not check the identity it
    # is given, and a suite that leaned on that would be proving less than it
    # looks like it is proving.
    from fsmes.domain import Person

    have = {person.code for person in session.scalars(select(Person))}
    for role in ("operator", "supervisor", "admin", "agent"):
        if role.upper() not in have:
            auth.create_user(session, code=role.upper(), name=f"Suite {role}",
                             password="a-long-enough-password", role=role)
    session.commit()

    def _same_session():
        yield session
        session.flush()

    @_cm
    def _same_session_read():
        yield session

    app = create_app()
    app.dependency_overrides[get_db] = _same_session
    app.dependency_overrides[get_read_db] = _same_session
    saved = (deps.short_read, idempotency.session_scope, idempotency.read_only_session,
             dict(mcp_server._clients), mcp_server._registry)
    deps.short_read = _same_session_read
    idempotency.session_scope = keys_scope
    idempotency.read_only_session = keys_scope
    client = TestClient(app)
    client.__enter__()
    mcp_server._clients = {SCRIPTED_PLANT: client}
    mcp_server._registry = lambda: {SCRIPTED_PLANT: {"api_port": 0, "label": "Assist eval"}}
    try:
        yield SimpleNamespace(plant=SCRIPTED_PLANT, session=session, client=client)
    finally:
        client.__exit__(None, None, None)
        (deps.short_read, idempotency.session_scope, idempotency.read_only_session,
         mcp_server._clients, mcp_server._registry) = saved
        session.close()
        engine.dispose()
        keys.dispose()


@contextlib.contextmanager
def _a_brain_that_is_on():
    """The loop refuses to start without a key and a budget. Scripted mode has
    neither and needs no model, so it says so for the length of the run."""
    from fsmes.services import agent

    before = {k: os.environ.get(k) for k in
              ("ANTHROPIC_API_KEY", "MES_AGENT_BRAIN", "MES_AGENT_USAGE_FILE")}
    saved_sdk, saved_usage = agent.sdk_installed, agent.USAGE_FILE
    os.environ["ANTHROPIC_API_KEY"] = "scripted: no model is called"
    os.environ["MES_AGENT_BRAIN"] = "claude"
    agent.sdk_installed = lambda: True
    agent.USAGE_FILE = Path(os.devnull)
    try:
        yield
    finally:
        agent.sdk_installed, agent.USAGE_FILE = saved_sdk, saved_usage
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_scripted_case(case: Case, plant: str) -> Turn:
    """One case against the real plumbing, with the model standing in.

    The order of operations is the endpoint's own (`/assist/agent`), and since
    #109 that order is: **the agent answers**. The guide router in front of it
    runs only when the cloud brain is off, so it does not run here - a regex
    deciding "show me" before the model was asked is the 2026-09-26 failure
    this suite exists to catch, and running it in scripted mode would have
    baked the failure into the measurement.

    The walks this person may be shown are handed to the conversation the way
    the endpoint hands them over, signing walks included, so `guides()` and
    `show_guide` answer here exactly as they do on the plant.
    """
    from fsmes.services import agent, assistant

    capabilities = capabilities_of(case.role)
    guides = assistant.listed_guides(capabilities)
    tools = agent.catalogue(capabilities)
    offered = frozenset(t["name"] for t in tools)
    writes = frozenset(t["name"] for t in tools if t["write"])

    with _a_brain_that_is_on():
        session = agent.open_session(case.role.upper(), plant, capabilities,
                                     guides=guides)
        if case.over_proposal:
            agent._call_model = scripted_model(tuple(case.before))
            agent.message(session, case.before_request or "(the turn before)",
                          name=case.role.title(), role=case.role)
        agent._call_model = scripted_model(plan_for(case))
        try:
            reply = agent.message(session, case.request,
                                  name=case.role.title(), role=case.role)
        except HistoryRefused as exc:
            return Turn(kind="unavailable", offered=offered,
                        say=f"the hosted API would refuse this conversation ({exc})")
        turn = turn_from_reply(reply, session, offered=offered, writes=writes)
        agent.forget(session.id)
        return turn


def run_scripted(cases: tuple[Case, ...]) -> tuple[Outcome, ...]:
    """The whole suite against the real plumbing. Deterministic, no network,
    no model, no money.

    The plant is arranged first, through the tools, exactly as a live run
    arranges a real one - so the arrangement is exercised on every pull request
    rather than only when somebody spends money.
    """
    from fsmes.services import agent

    saved_call = agent._call_model
    out: list[Outcome] = []
    try:
        with scripted_plant() as plant:
            # Deliberately not `agent.serve_locally`: that would have the tool
            # layer dial a base URL, and the point here is that it dials the
            # in-process app instead. `scripted_plant` has already pointed the
            # client cache and the registry at it.
            arrange(plant.plant)
            view = Plantview(plant.plant)
            unmet = missing(cases, plant.plant, view=view)
            for case in cases:
                out.append(score(case, run_scripted_case(case, plant.plant),
                                 unmet.get(case.id, ())))
    finally:
        agent._call_model = saved_call
    return tuple(out)


# ------------------------------------------------------------ the live run

class LiveRefused(Exception):
    """A live run that cannot honestly start."""


KEY = "ANTHROPIC_API_KEY"

#: What one live run may spend before it stops and says so. A number, not a
#: hope: `docs/ai/BUDGET.md` is a monthly cap and an eval is one line on it.
DEFAULT_MAX_USD = 1.00


class Plant:
    """A running plant, over its own HTTP API, as the person in the chair.

    Everything the suite needs is already served: `/assist/agent` is the same
    endpoint the panel posts to, so a live run measures what a person gets and
    not a private path into the model. A proposal is always **declined** -
    scoring a plant must not change one.
    """

    def __init__(self, base_url: str, *, client=None, timeout: float = 120.0):
        import httpx

        self.base_url = base_url.rstrip("/")
        self.http = client if client is not None else httpx.Client(
            base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.http.close()

    def _json(self, method: str, path: str, **kwargs) -> dict:
        response = self.http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise LiveRefused(f"{method} {path} -> {response.status_code} {response.text[:300]}")
        return response.json()

    def sign_in(self, code: str, password: str) -> None:
        out = self._json("POST", "/auth/login", json={"code": code, "password": password})
        self.http.headers["Authorization"] = f"Bearer {out['token']}"

    def identity(self) -> dict:
        """What the plant says about itself. It does not report the commit it is
        running - that is the one fact a result file has to be told."""
        return self._json("GET", "/health")

    def brain(self) -> dict:
        return self._json("GET", "/assist/agent/status")

    def ask(self, message: str, *, screen: str | None = None,
            session: str | None = None) -> dict:
        return self._json("POST", "/assist/agent",
                          json={"message": message, "screen": screen, "session": session})

    def decline(self, session: str, proposal: str) -> dict:
        return self._json("POST", "/assist/agent/decline",
                          json={"session": session, "proposal": proposal})


def _tokens_of(brain: dict) -> dict:
    return dict(brain.get("tokens_this_month") or {})


def _spent(brain: dict) -> float:
    return float(brain.get("spend_usd") or 0.0)


def _token_delta(before: dict, after: dict) -> dict:
    return {k: max(0, int(after.get(k, 0)) - int(before.get(k, 0)))
            for k in set(before) | set(after)}


#: The name the tool layer dials a live plant by during an arrangement. It is
#: a key into this process's own client cache and nothing else - the plant is
#: reached at the base URL the run was given, with no registry lookup, the way
#: a plant operating itself does.
LIVE_PLANT = "assist-eval-live"


def arrange_live(base_url: str, *, on_behalf_of: str = "ADMIN") -> dict:
    """Put the suite's fixtures on a running plant, over its own HTTP API.

    The same `arrange` a scripted run uses, reaching the plant the way the
    assistant's own tools reach it: as the AGENT account, naming the person the
    run signed in as, through the product's API. So the fixture is built by the
    same write path the suite is about, it lands in the audit trail with a name
    against it, and a fixture that stops working is a tool that stopped working.

    Writes to somebody's plant. `docs/ai/ASSIST-EVAL.md` lists exactly what it
    leaves there; nothing is removed afterwards, because an MES does not delete
    an audited record.
    """
    from fsmes import mcp_server

    mcp_server.serve_locally(LIVE_PLANT, base_url)
    try:
        # One read first, on purpose. `arrange` reports a fixture it could not
        # make rather than raising, so without this a plant whose AGENT account
        # cannot sign in would come back as eight separate refusals instead of
        # the one sentence that is actually true of it.
        mcp_server.materials(LIVE_PLANT)
    except RuntimeError as exc:
        raise LiveRefused(str(exc)) from exc
    return arrange(LIVE_PLANT, on_behalf_of=on_behalf_of)


def live_view(base_url: str) -> Plantview:
    """A reader for the same plant, so `missing` asks it over HTTP too."""
    from fsmes import mcp_server

    mcp_server.serve_locally(LIVE_PLANT, base_url)
    return Plantview(LIVE_PLANT)


def run_live(cases: tuple[Case, ...], plant: Plant, *,
             max_usd: float = DEFAULT_MAX_USD,
             on_case=None, arranging: bool = True,
             on_behalf_of: str = "ADMIN") -> tuple[tuple[Outcome, ...], dict]:
    """The suite against the real model, on a running plant.

    The plant is arranged first unless `arranging` is off, and every case the
    plant still has not got what for is reported *not arranged* - not asked, not
    scored, not paid for. Stops when the run has cost `max_usd`, and says which
    cases were not run rather than reporting a short suite as a whole one. Every
    proposal is declined on the way out, so a scored plant is an unchanged plant.
    """
    started = plant.brain()
    if not started.get("available"):
        raise LiveRefused(f"this plant's cloud brain is not available: "
                          f"{started.get('reason')}")
    put_there: dict = {}
    if arranging:
        put_there = arrange_live(plant.base_url, on_behalf_of=on_behalf_of)
    view = live_view(plant.base_url)
    unmet = missing(cases, view.plant, view=view)
    spend_at_start = _spent(started)
    tokens_at_start = _tokens_of(started)
    outcomes: list[Outcome] = []
    not_run: list[Case] = []
    spent = 0.0
    for case in cases:
        if case.id in unmet:
            short = ", ".join(unmet[case.id])
            outcomes.append(score(case, Turn(kind="not_asked", from_model=True,
                                             say=f"not asked: this plant has not got "
                                                 f"{short}"),
                                  unmet[case.id]))
            if on_case is not None:
                on_case(outcomes[-1])
            continue
        if spent >= max_usd:
            not_run.append(case)
            continue
        session_id: str | None = None
        try:
            if case.over_proposal and case.before_request:
                first = plant.ask(case.before_request, screen=case.screen)
                session_id = first.get("session")
            reply = plant.ask(case.request, screen=case.screen, session=session_id)
            session_id = reply.get("session") or session_id
            turn = turn_from_reply(reply, from_model=True)
            for proposal in reply.get("proposals") or []:
                if session_id:
                    with contextlib.suppress(LiveRefused):
                        plant.decline(session_id, proposal["id"])
        except LiveRefused as exc:
            turn = Turn(kind="unavailable", say=str(exc), from_model=True)
        after = plant.brain()
        turn.usd = round(_spent(after) - spend_at_start - spent, 6)
        spent = round(_spent(after) - spend_at_start, 6)
        turn.tokens = _token_delta(tokens_at_start, _tokens_of(after))
        outcome = score(case, turn)
        outcomes.append(outcome)
        if on_case is not None:
            on_case(outcome)
    finished = plant.brain()
    run = {"model": finished.get("model"), "usd": round(_spent(finished) - spend_at_start, 6),
           "tokens": _token_delta(tokens_at_start, _tokens_of(finished)),
           "not_run": tuple(not_run), "max_usd": max_usd,
           "cap_usd": finished.get("cap_usd"), "month_usd": _spent(finished),
           "made": put_there.get("made", []), "already": put_there.get("already", []),
           "refused": put_there.get("refused", {}),
           "why_not": {r: why_not(r) for reqs in unmet.values() for r in reqs}}
    return tuple(outcomes), run
