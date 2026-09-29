"""The panel's switch between the two agents, in a browser.

There are two agents now - the assistant that proposes changes and the analysis
agent that holds every read tool and no write tool - and the panel has one button
for choosing between them. Three things have to hold and none of them can be
checked in Python, because they live in `switchKind()` and `agentSend()` in
`assist.js`:

1. Pressing the button sends the *other* kind. The reply this file scripts says
   which kind the server opened the conversation with, so a button that changed
   a label and nothing else fails here.
2. Switching starts a new conversation. A conversation belongs to one kind for
   its whole life - the tools, the prompt and the budget in it are that kind's -
   so the panel must not carry the old session id across.
3. The chip says which agent is answering. On 2026-09-26 the panel changed
   brains silently and a person asked fifteen questions of a model that could
   not do any of it; the rule out of that morning is that whatever else happens,
   the person is told who they are talking to.

Same shape as `test_the_panel_keeps_one_brain_through_a_failed_turn.py`: a seeded
plant on a loopback port of the operating system's choosing, driven by Chromium,
touching nothing anyone else is running. The model is scripted in this process -
the server runs on a thread of it - so no key is used and nothing leaves the box.

LOOPBACK ONLY, deliberately. Nothing here is timing-shaped: every assertion is
about which agent answered, and each waits on the text or the value it asserts
rather than on the element that will hold it.
"""

import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from fsmes.services import agent

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _response(text):
    usage = SimpleNamespace(input_tokens=900, output_tokens=40,
                            cache_read_input_tokens=700, cache_creation_input_tokens=0)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                           stop_reason="end_turn", usage=usage)


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A seeded demo plant with both agents on, and a model that answers with the
    kind of the conversation it was called in.

    That is the whole trick of this file: the reply is the fact under test, so
    the button cannot pass by looking right.
    """
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("two-agents") / "plant.db"
    url = f"sqlite:///{path}"

    def which_agent_am_i(sess):
        return _response(f"You are talking to kind={sess.kind}.")

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        env.setenv("ANTHROPIC_API_KEY", "not-a-key-the-model-is-scripted")
        env.setenv("MES_AGENT_BRAIN", "claude")
        env.setenv("MES_ANALYSIS_BRAIN", "claude")
        env.setattr(agent, "sdk_installed", lambda: True)
        env.setattr(agent, "_call_model", which_agent_am_i)
        env.setattr(agent, "USAGE_FILE", path.parent / "usage.jsonl")
        env.setattr(agent, "TURN_FILE", path.parent / "turns.jsonl")
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_sessionmaker.cache_clear()

        engine = make_engine(url)
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            seed_demo_plant(session)
            auth.ensure_builtin_roles(session)
            session.commit()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(
            create_app(), log_level="warning", lifespan="on"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            port = sock.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            _wait_until_answering(base)
            yield base
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()


def _wait_until_answering(base: str, seconds: float = 20.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=1) as reply:
                if reply.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    raise AssertionError(f"the plant at {base} never answered /health")


@pytest.fixture(scope="module")
def playwright():
    sync_playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="the [dev] extra is not installed").sync_playwright
    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            chromium = pw.chromium.launch()
            yield pw, chromium
            chromium.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


@pytest.fixture(scope="module")
def supervisor(playwright, plant):
    """Signed in as somebody who reads the plant and closes things - not an
    administrator, because choosing an agent is not an administrative act."""
    _pw, chromium = playwright
    context = chromium.new_context(viewport={"width": 1400, "height": 900})
    reply = context.request.post(
        f"{plant}/auth/login",
        data=json.dumps({"code": "SCOTT", "password": "operator"}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in failed: {reply.status}"
    yield context
    context.close()


def _panel(context, base):
    page = context.new_page()
    page.goto(f"{base}/dashboard", wait_until="load", timeout=30000)
    page.wait_for_selector(".assist-launch", state="visible", timeout=15000)
    page.locator(".assist-launch").click()
    page.wait_for_selector("#assist-input", state="visible", timeout=15000)
    return page


def _say(page, words):
    page.fill("#assist-input", words)
    page.press("#assist-input", "Enter")


def _wait_for_bot_line(page, words, timeout=20000):
    """Wait on the text being asserted, never on the element that will hold it."""
    page.wait_for_function(
        """needle => [...document.querySelectorAll('#assist-log .assist-msg.bot')]
               .some(line => line.textContent.includes(needle))""",
        arg=words, timeout=timeout)


def _session_id(page):
    return page.evaluate("() => sessionStorage.getItem('fsmes-agent-session')")


def test_the_panel_offers_the_other_agent_and_sends_the_kind_it_says_it_will(
        supervisor, plant):
    """One button, and the reply says which agent got the message."""
    page = _panel(supervisor, plant)
    try:
        switch = page.locator("#assist-kind")
        assert switch.count() == 1, "the panel offers no way to reach the other agent"
        assert switch.inner_text() == "Ask the analyst"

        _say(page, "how did the line run?")
        _wait_for_bot_line(page, "kind=floor")
        floor_session = _session_id(page)
        assert floor_session

        switch.click()
        page.wait_for_function(
            """() => document.querySelector('#assist-kind').textContent
                     === 'Back to the assistant'""", timeout=10000)
        # Switching starts a new conversation: the old session id is not carried
        # into the other agent's.
        page.wait_for_function("() => !sessionStorage.getItem('fsmes-agent-session')",
                               timeout=10000)

        _say(page, "where did LINE1 lose its OEE?")
        _wait_for_bot_line(page, "kind=analysis")
        assert _session_id(page) != floor_session

        # And the chip says which agent is answering.
        page.wait_for_function(
            """() => document.querySelector('#assist-brain')
                     .textContent.includes('analysis')""", timeout=10000)

        # Back again, and the assistant that can propose changes is the one
        # answering - one button, both ways.
        switch.click()
        _say(page, "and before that?")
        _wait_for_bot_line(page, "kind=floor")
    finally:
        page.close()


def test_the_browser_remembers_which_agent_it_was_talking_to_across_a_page(
        supervisor, plant):
    """A walk can cross screens and so can a conversation. What must not happen
    is a page change quietly handing somebody back to the agent that can change
    the plant."""
    page = _panel(supervisor, plant)
    try:
        page.locator("#assist-kind").click()
        page.wait_for_function(
            """() => document.querySelector('#assist-kind').textContent
                     === 'Back to the assistant'""", timeout=10000)
        page.goto(f"{plant}/dashboard/analysis", wait_until="load", timeout=30000)
        page.wait_for_selector("#assist-input", state="visible", timeout=15000)
        page.wait_for_function(
            """() => document.querySelector('#assist-kind').textContent
                     === 'Back to the assistant'""", timeout=10000)
        _say(page, "what was each machine doing?")
        _wait_for_bot_line(page, "kind=analysis")
    finally:
        page.close()
