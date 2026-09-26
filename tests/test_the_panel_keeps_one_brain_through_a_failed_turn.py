"""The assistant panel, in a browser, across a proposal and a failed turn.

Scott, 2026-09-26, bottling, ADMIN. He asked for `nc_code_prefix` to change,
got a proposal card, typed his next message over it — and from that moment
every reply was *"The cloud brain did not answer (BadRequestError)"*. The
panel read one `unavailable` as "the agent is off", set `agent.available =
false`, and sent the remaining fifteen messages to `/assist/ask`: the local
facts brain, which has no tools, cannot change anything, was never introduced,
and answered *"consult the quality manager"* to a request to change a setting.

Three things have to hold and none of them can be checked in Python, because
the switch lives in `render()` in `assist.js`:

1. A message typed over an open proposal is answered by the agent.
2. A turn the model failed says so and leaves the agent on.
3. The panel only ever hands over to the local model when the reply says the
   agent is genuinely off — and when it does, it says which brain is talking.

Same shape as `test_a_walk_waits_for_a_control_the_page_is_still_drawing.py`:
a seeded plant on a loopback port of the operating system's choosing, driven
by Chromium, touching nothing anyone else is running. The model is scripted in
this process — the server runs on a thread of it — so no key is used and
nothing leaves the box.

LOOPBACK ONLY, deliberately. Nothing here is timing-shaped: the assertions are
about which endpoint answered, and a slow network changes nothing about that.
"""

import json
import socket
import threading
import time

import pytest
from sqlalchemy.orm import Session

from fsmes.services import agent

pytestmark = [pytest.mark.slow, pytest.mark.browser]

#: The setting Scott was trying to change, and the workspace it lives in.
SETTING = "nc_code_prefix"
DOMAIN = "quality"

#: What the scripted model says, so a reply can be told apart from anything
#: the local qwen would produce — it is not running in this test at all.
ITS_OWN_WORDS = "That card is still the one on screen; nothing has changed yet."
AFTER_THE_ERROR = "Nc code prefix is CR at the moment."
THE_ERROR_LINE = ("The assistant hit an error on that one. Say it again and I "
                  "will try afresh.")
THE_LOCAL_BRAIN_LINE = "The plant's agent is off"


class Refused(Exception):
    """What the Messages API raises at a history it will not take."""

    status_code = 400


def _text(text):
    from types import SimpleNamespace
    return SimpleNamespace(type="text", text=text)


def _tool(id_, name, **args):
    from types import SimpleNamespace
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


def _response(*blocks, stop="end_turn"):
    from types import SimpleNamespace
    usage = SimpleNamespace(input_tokens=900, output_tokens=40,
                            cache_read_input_tokens=700, cache_creation_input_tokens=0)
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage)


def _preview(name, args, *, plant, on_behalf_of=None, dry_run=None, client_ref=None):
    """Every tool call this file makes is one setting's dry run."""
    would = f"set {args.get('key')} to {args.get('value')} in the quality configuration"
    return {"dry_run": True, "would": would,
            "request": {"method": "PATCH",
                        "path": f"/dashboard/config/{args.get('domain')}/settings/"
                                f"{args.get('key')}",
                        "body": {"value": args.get("value")}}}


#: The model's answers for the test that is running, in order. A string "raise"
#: is one failed call.
SCRIPT: list = []


@pytest.fixture()
def script():
    SCRIPT.clear()
    yield SCRIPT
    SCRIPT.clear()


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A seeded demo plant with the cloud brain switched on and scripted.

    The server runs on a thread of this process, so patching the model here
    patches the one the endpoint calls. No key is read and no request leaves
    the machine: `_call_model` never runs.
    """
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("one-brain") / "plant.db"
    url = f"sqlite:///{path}"

    def scripted_model(sess):
        step = SCRIPT.pop(0)
        if step == "raise":
            raise Refused("messages.3: unexpected `tool_use` without `tool_result`")
        return step

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        env.setenv("ANTHROPIC_API_KEY", "not-a-key-the-model-is-scripted")
        env.setenv("MES_AGENT_BRAIN", "claude")
        env.setattr(agent, "sdk_installed", lambda: True)
        env.setattr(agent, "_call_model", scripted_model)
        env.setattr(agent, "execute", _preview)
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
        thread = threading.Thread(
            target=lambda: server.run(sockets=[sock]), daemon=True)
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
def admin(playwright, plant):
    _pw, chromium = playwright
    context = chromium.new_context(viewport={"width": 1400, "height": 900})
    reply = context.request.post(
        f"{plant}/auth/login",
        data=json.dumps({"code": "ADMIN", "password": "admin"}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in as ADMIN failed: {reply.status}"
    yield context
    context.close()


def _panel(context, base):
    """A page with the assistant open, the way a person opens it."""
    page = context.new_page()
    page.goto(f"{base}/dashboard/config/{DOMAIN}", wait_until="load", timeout=30000)
    page.wait_for_selector(".assist-launch", state="visible", timeout=15000)
    # The panel is built from the agent's status, which is fetched at boot.
    page.wait_for_function(
        "() => document.querySelector('.assist-launch')", timeout=15000)
    page.locator(".assist-launch").click()
    page.wait_for_selector("#assist-input", state="visible", timeout=15000)
    return page


def _say(page, words):
    page.fill("#assist-input", words)
    page.press("#assist-input", "Enter")


def _bot_lines(page):
    return page.locator("#assist-log .assist-msg.bot").all_inner_texts()


def _wait_for_bot_line(page, words, timeout=20000):
    page.wait_for_function(
        """needle => [...document.querySelectorAll('#assist-log .assist-msg.bot')]
               .some(line => line.textContent.includes(needle))""",
        arg=words, timeout=timeout)


def _brain_chip(page):
    chip = page.locator("#assist-brain")
    return chip.get_attribute("class"), chip.inner_text()


# ------------------------------------------- a message typed over a proposal

def test_the_message_typed_over_a_proposal_is_answered_by_the_agent(admin, plant, script):
    """The first half of Scott's afternoon, in a browser. Card on screen, next
    message typed anyway — and the reply is the agent's, because the decline
    reaches the history before his words do."""
    script += [
        _response(_text("I will set it to CR."),
                  _tool("p1", "write_plant_setting", domain=DOMAIN, key=SETTING, value="CR"),
                  stop="tool_use"),
        _response(_text(ITS_OWN_WORDS)),
        _response(_text(AFTER_THE_ERROR)),
    ]
    page = _panel(admin, plant)
    try:
        _say(page, f"Change {SETTING} to CR")
        page.wait_for_selector(".assist-card", state="visible", timeout=20000)

        _say(page, "set it to 4")
        _wait_for_bot_line(page, ITS_OWN_WORDS)

        # A third turn, on the same session, still the agent's.
        _say(page, f"what is {SETTING} set to?")
        _wait_for_bot_line(page, AFTER_THE_ERROR)

        classes, _label = _brain_chip(page)
        assert "on" in classes, f"the panel switched brains: {classes}"
        assert not any(THE_LOCAL_BRAIN_LINE in line for line in _bot_lines(page))
        assert not SCRIPT, "a turn never reached the model"
    finally:
        page.close()


# ------------------------------------------------ a failed turn is one turn

def test_one_failed_turn_does_not_hand_the_rest_of_the_page_to_the_local_model(
        admin, plant, script):
    """The mechanism itself. The model fails on the second message; the panel
    says so in a person's words, leaves the agent on, and the third message is
    answered by the agent — not by `/assist/ask`.

    Under the old `render()` this is the point where `agent.available` went
    false and stayed false, and the person was never told."""
    script += [
        _response(_text("Bottling is running; nothing is down.")),
        "raise",
        _response(_text(AFTER_THE_ERROR)),
    ]
    page = _panel(admin, plant)
    try:
        _say(page, "what is running?")
        _wait_for_bot_line(page, "Bottling is running")

        _say(page, "set it to 4")
        _wait_for_bot_line(page, THE_ERROR_LINE)
        classes, label = _brain_chip(page)
        assert "on" in classes, f"one error switched the brain: {classes}"
        assert label, "the brain chip lost its model name"

        _say(page, f"what is {SETTING} set to?")
        _wait_for_bot_line(page, AFTER_THE_ERROR)
        assert not any(THE_LOCAL_BRAIN_LINE in line for line in _bot_lines(page))
        assert not SCRIPT, "the third message never reached the model"
    finally:
        page.close()


# --------------------------------------- and when it really is off, it says so

def test_a_plant_whose_agent_is_off_says_which_brain_is_answering(admin, plant, script):
    """The other side of the same rule. With no key the panel is on the local
    model from the first paint, and the first thing it says is which brain the
    person is talking to and what it cannot do — fifteen improvised answers to
    requests for changes is what silence bought last time."""
    with pytest.MonkeyPatch.context() as off:
        off.delenv("ANTHROPIC_API_KEY", raising=False)
        page = _panel(admin, plant)
        try:
            classes, label = _brain_chip(page)
            assert "on" not in classes and label == "local"
            _say(page, "which spc rules are on hold")
            _wait_for_bot_line(page, THE_LOCAL_BRAIN_LINE)
            notice = [line for line in _bot_lines(page) if THE_LOCAL_BRAIN_LINE in line]
            assert notice and "cannot change anything" in notice[0], _bot_lines(page)
            # Said once, not before every answer.
            _say(page, "and the cpk bar?")
            page.wait_for_timeout(500)
            assert sum(THE_LOCAL_BRAIN_LINE in line for line in _bot_lines(page)) == 1
        finally:
            page.close()
