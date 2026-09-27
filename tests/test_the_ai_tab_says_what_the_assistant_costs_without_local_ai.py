"""The AI tab's Status, in a browser, on a plant with and without a local model.

Found on `f9705990`: the tab was built from `GET /ai`, which is the *local* AI
layer on the box, and the cloud brain's spend was a note inside one of those
local rows. A plant with `MES_LOCAL_AI=0` got `{"enabled": false}` and no
number at all — the one figure a plant administrator most needs from that tab,
gone with a setting about a different model.

So the same screen is read three times against the same plant, with only the
environment differing: the local layer off, the local layer on, and the cloud
brain with no key. What has to be true every time is that the cloud brain's
model, its state, its spend against this plant's cap and when it was last used
are on the screen, and that nothing ever simply vanishes — off is a row that
says off and why.

The plant is served in this process, so the environment is set in the test and
the server thread reads it on the next request; `ai_status.enabled()` and
`agent.available()` both read the environment per call rather than at import,
which is what makes that honest.

No model is ever called here. The key in the environment is not a key, the SDK
check is stubbed, and the only thing under test is what the two status
endpoints say and how the tab draws them.

**Looked at on loopback only, and that is the honest answer for this file.**
Nothing here is timing-shaped: no walk lands on a control another script is
drawing, and no redirect follows a save. But the tab now waits on *two* calls
before it is drawn, so every wait below is on the text being asserted rather
than on the element that will hold it — reading the table the instant it exists
is the mistake #112 made.
"""

import json
import socket
import threading
import time

import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

#: What the usage file says this month has cost. A real number rather than
#: zero, because "$0.00 of $10" would also pass on a screen where the spend
#: never arrived at all.
SPENT_USD = 1.2345

#: This plant's own cap, and a number the product's default is not, so the
#: assertion cannot pass on a cap read from somewhere else. The half dollar is
#: deliberate: a cap rounded to the nearest dollar for tidiness would overstate
#: somebody's budget, so the screen must say the fifty cents.
CAP_USD = "25.5"

#: What the screen must say it is — to the cent, not "$26".
SPEND_LINE = f"${SPENT_USD:.2f} of $25.50"


@pytest.fixture(scope="module")
def usage_file(tmp_path_factory):
    """One month's bill, written where `agent.spend_this_month()` reads it."""
    from datetime import UTC, datetime

    path = tmp_path_factory.mktemp("agent-usage") / "agent-usage.jsonl"
    when = datetime.now(UTC).isoformat(timespec="seconds")
    path.write_text(json.dumps({
        "ts": when, "plant": "demo", "user": "ADMIN", "model": "claude-sonnet-5",
        "input": 2000, "output": 60, "cache_read": 0, "cache_write": 0,
        "usd": SPENT_USD}) + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def bill(usage_file):
    """Point `agent.spend_this_month()` at this file's own bill, per test.

    The suite's own autouse fixture sends both agent records somewhere
    disposable so a test never books imaginary dollars into a developer's
    month, and it is function-scoped — so a module-scoped patch would be
    overwritten before the first test ran. This is the way that fixture's
    docstring says to point them somewhere of your own.
    """
    from fsmes.services import agent

    with pytest.MonkeyPatch.context() as mine:
        mine.setattr(agent, "USAGE_FILE", usage_file)
        yield


@pytest.fixture(scope="module")
def plant(tmp_path_factory, usage_file):
    """A seeded demo plant with a month's agent bill behind it, over HTTP."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.services import agent, auth

    path = tmp_path_factory.mktemp("ai-status-screen") / "plant.db"
    url = f"sqlite:///{path}"

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        env.setenv("MES_AGENT_MONTHLY_USD", CAP_USD)
        env.setenv("MES_AGENT_BRAIN", "auto")
        # Not a key. `available()` only checks that the variable is set, and
        # nothing in this file sends a message, so no model is ever reached.
        env.setenv("ANTHROPIC_API_KEY", "not-a-key-nothing-here-calls-a-model")
        env.setattr(agent, "sdk_installed", lambda: True)
        env.setattr(agent, "USAGE_FILE", usage_file)
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
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
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
    context = chromium.new_context(viewport={"width": 1400, "height": 1000})
    reply = context.request.post(
        f"{plant}/auth/login",
        data=json.dumps({"code": "ADMIN", "password": "admin"}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in as ADMIN failed: {reply.status}"
    yield context
    context.close()


def _status_tab(context, base):
    """The Status tab, open and drawn.

    Waited for on the cloud brain's own row and on the spend in the head —
    which is to say on the two things every assertion below reads, not on the
    table and the box that will hold them.
    """
    page = context.new_page()
    page.goto(f"{base}/dashboard/ai", wait_until="load", timeout=30000)
    page.locator('.tab[data-tab="status"]').click()
    page.wait_for_function(
        "() => document.querySelector('#status-table tbody')"
        "        .innerText.includes('Floor agent (cloud)')", timeout=15000)
    page.wait_for_function(
        "() => document.querySelector('#status-head').innerText"
        "        .toLowerCase().includes('spend this month')", timeout=15000)
    return page


def _row(page, name):
    """One row of the Status table by the job it names, as the screen says it."""
    rows = page.locator("#status-table tbody tr")
    for index in range(rows.count()):
        if name in rows.nth(index).inner_text():
            return rows.nth(index)
    raise AssertionError(
        f"no row named {name!r} on the Status tab; the rows were "
        f"{[rows.nth(i).inner_text() for i in range(rows.count())]}")


def _state(row):
    """A row's state, out of its own pill rather than out of its prose — the
    word `off` appears in half these sentences."""
    return row.locator(".pill").inner_text().strip().lower()


def test_a_plant_with_no_local_model_still_sees_what_its_assistant_costs(
        admin, plant, monkeypatch):
    """`MES_LOCAL_AI=0` is a statement about the local model. The cloud brain
    is a different model behind a different setting, and its bill is the one
    number a plant administrator most needs from this tab."""
    monkeypatch.setenv("MES_LOCAL_AI", "0")
    page = _status_tab(admin, plant)

    # The label is upper-cased by the stylesheet, so the values are what is
    # compared — the model, and the spend against this plant's own cap.
    head = page.locator("#status-head").inner_text()
    assert "claude-sonnet-5 \u2014 on" in head, head
    assert SPEND_LINE in head, head

    brain = _row(page, "Floor agent (cloud)")
    assert _state(brain) == "ok", brain.inner_text()
    # `last_used` carries a +00:00 offset, and the date helper used to append a
    # Z to it and draw an Invalid Date as an em dash — a missing number that
    # looked like a measured absence. The month's one turn has a date.
    from datetime import UTC, datetime
    year = str(datetime.now(UTC).year)
    assert year in brain.inner_text(), brain.inner_text()
    assert year in page.locator("#status-spend").inner_text()

    # And the local layer says off, and why, rather than the tab going blank.
    local = _row(page, "Local AI layer")
    assert _state(local) == "off", local.inner_text()
    assert "MES_LOCAL_AI=0" in local.inner_text(), local.inner_text()
    assert "The cloud brain above is unaffected" in local.inner_text()
    page.close()


def test_with_the_local_layer_on_both_brains_are_on_the_same_tab(
        admin, plant, monkeypatch):
    """The other half of the claim: turning the local layer back on adds its
    own jobs and takes nothing away from the cloud brain."""
    monkeypatch.setenv("MES_LOCAL_AI", "1")
    page = _status_tab(admin, plant)
    page.wait_for_function(
        "() => document.querySelector('#status-table tbody')"
        "        .innerText.includes('Nightly rollup')", timeout=15000)

    head = page.locator("#status-head").inner_text()
    assert SPEND_LINE in head, head
    # The local layer's own facts are beside the cloud brain's, not instead.
    assert "model server" in head.lower(), head

    body = page.locator("#status-table tbody").inner_text()
    for job in ("Floor agent (cloud)", "Run triage", "Nightly rollup",
                "Design chat", "Floor assistant", "Instruction drafting"):
        assert job in body, (job, body)
    # The local layer is on, so its jobs are listed rather than one row
    # standing in for them.
    assert "Local AI layer" not in body, body
    # And the spend is no longer a note inside a local row: it is said once, in
    # the head, and no row repeats it.
    assert SPEND_LINE not in body, body

    assert "GPU budget, in priority order" in page.locator("#status-budget").inner_text()
    page.close()


def test_a_cloud_brain_that_is_off_says_off_and_why_rather_than_going_quiet(
        admin, plant, monkeypatch):
    """Off is a state with a reason beside it, in `available()`'s own words.
    A plant whose assistant is not answering needs to read why on the screen
    that is about the assistant."""
    monkeypatch.setenv("MES_LOCAL_AI", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    page = _status_tab(admin, plant)

    brain = _row(page, "Floor agent (cloud)")
    assert _state(brain) == "off", brain.inner_text()
    assert "ANTHROPIC_API_KEY" in brain.inner_text(), brain.inner_text()

    # The month is still what the month was: a brain switched off did not spend
    # nothing, it spent what it spent before it was switched off.
    head = page.locator("#status-head").inner_text()
    assert "claude-sonnet-5 \u2014 off" in head, head
    assert SPEND_LINE in head, head
    page.close()
