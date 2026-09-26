"""A walkthrough that lands on a page still fetching its own rows, in a browser.

Scott, 2026-09-25, signed in as ADMIN on a lab plant over Tailscale: he asked
the assistant to change `default_job_minutes`, pressed "Show me", arrived on
Engineering's Configuration page and was told something he read as "I don't
have permission". He held every capability the step needed — he then found the
box himself and saved 55.

Nothing about permissions was wrong. Two things were:

1. The walk looked for its control **once**, 400 ms after the page load, and
   the Configuration page does not have one yet at 400 ms over a link with
   latency in it: it reads /auth/me, then its own sections, then renders
   eighteen sections before the one input a walk is pointed at exists. On
   loopback that whole chain is single-digit milliseconds, which is why every
   test of this path passed and the one person on a laptop saw it fail.
2. When the single look missed, the card said *"That control is not on this
   screen for your role."* — a cause the code cannot know, and the one reading
   it as a refusal.

So this file pins both halves against a page that is deliberately slow, and it
has to be a real browser: the race is between a `setTimeout` and two `fetch`
calls, and nothing about it exists in Python.

Same shape as `test_ui_nav.py`: a seeded plant on a loopback port of the
operating system's choosing, driven by Chromium, touching nothing anyone else
is running. Marked slow; run it with
`pytest -m slow tests/test_a_walk_waits_for_a_control_the_page_is_still_drawing.py`.
"""

import json
import socket
import threading
import time

import pytest
from sqlalchemy.orm import Session

pytestmark = pytest.mark.slow

#: The setting Scott was trying to change, and the section it is listed under.
SETTING = "default_job_minutes"
DOMAIN = "engineering"
PAGE = f"/dashboard/config/{DOMAIN}?setting={SETTING}"
#: What the agent would have typed into the box for him.
PROPOSED = "55"

#: How long the sections request is held back. Comfortably past the 400 ms the
#: resume waits, and comfortably inside the three seconds the walk now spends
#: looking — so a pass means the waiting worked, not that the delay was small.
DELAY_SECONDS = 0.8

#: `[screens] assistant_fill_attempts` x `assistant_fill_wait_ms`: 20 x 150 ms.
BUDGET_SECONDS = 3


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A seeded demo plant, served over HTTP on a port nobody chose in advance.
    A file database rather than the suite's in-memory one, because the server
    answers on its own threads and must open the same rows."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("walk-wait") / "plant.db"
    url = f"sqlite:///{path}"

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
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


def _signed_in(playwright, base, code, password):
    """A browser context signed in the way a person's browser is: the login
    sets an HttpOnly cookie Playwright shares between its jar and its pages."""
    _pw, chromium = playwright
    context = chromium.new_context(viewport={"width": 1400, "height": 900})
    reply = context.request.post(
        f"{base}/auth/login",
        data=json.dumps({"code": code, "password": password}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in as {code} failed: {reply.status}"
    return context


@pytest.fixture(scope="module")
def admin(playwright, plant):
    context = _signed_in(playwright, plant, "ADMIN", "admin")
    yield context
    context.close()


@pytest.fixture(scope="module")
def operator(playwright, plant):
    """Scott's own demo account, which is an operator and does not hold
    `process.define` — the one case where "your role" is a true answer."""
    context = _signed_in(playwright, plant, "SCOTT", "operator")
    yield context
    context.close()


def _walk_onto(context, base, step, *, delay_sections=False):
    """A page that is mid-walk, the way the resume path leaves it.

    The walk is put in sessionStorage exactly as `saveWalk()` writes it — one
    step, saved whole, the way a generated or agent-proposed walk crosses a
    screen boundary — and then the page the step asks for is opened. Which is
    what "Show me" does, minus the model.
    """
    page = context.new_page()
    # When the anchor the step points at first exists, in milliseconds since
    # this document started. This is the measurement the whole file is about:
    # the old code looked once, at 400 ms, and never again.
    page.add_init_script(
        """
        window.__anchorAt = null;
        const watch = new MutationObserver(() => {
          if (window.__anchorAt === null
              && document.querySelector('[data-assist="setting-in-focus"]')) {
            window.__anchorAt = performance.now();
          }
        });
        watch.observe(document, { childList: true, subtree: true });
        """)
    if delay_sections:
        page.route(
            f"**/dashboard/config/{DOMAIN}/sections",
            lambda route: (time.sleep(DELAY_SECONDS), route.continue_()))

    page.goto(f"{base}/dashboard", wait_until="load", timeout=30000)
    page.evaluate(
        "saved => sessionStorage.setItem('fsmes-guide', saved)",
        json.dumps({"walk": {"title": "Change one of this plant's own settings",
                             "steps": [step]},
                    "index": 0, "exact": True, "moved": 0}))
    page.goto(f"{base}{step['page']}", wait_until="load", timeout=30000)
    return page


def _step(anchor_setting=SETTING, **extra):
    return {"page": f"/dashboard/config/{DOMAIN}?setting={anchor_setting}",
            "anchor": "setting-in-focus",
            "title": "The box this setting is changed in",
            "body": "How long an unplanned job takes, on the engineering "
                    "Configuration page.",
            "fill": {"value": PROPOSED},
            **extra}


def _coach_text(page, timeout=15000):
    page.wait_for_selector(".assist-coach", state="visible", timeout=timeout)
    return page.locator(".assist-coach").inner_text()


# ------------------------------------------------- the control is waited for

def test_the_walk_finds_a_control_the_page_had_not_drawn_at_four_hundred_milliseconds(
        admin, plant):
    """The race itself. Engineering's sections are held back long enough that
    the input does not exist when the resume's 400 ms is up, and the walk still
    ends standing on it with the proposed value typed in."""
    page = _walk_onto(admin, plant, _step(), delay_sections=True)
    try:
        page.wait_for_selector(".assist-ring", state="visible", timeout=15000)

        drawn_at = page.evaluate("window.__anchorAt")
        assert drawn_at is not None, "the input never appeared at all"
        assert drawn_at > 400, (
            f"the input appeared {drawn_at:.0f} ms in, before the single look "
            f"this test exists to outlive — the delay did not take, so a pass "
            f"here would prove nothing")

        box = page.locator('input[data-assist="setting-in-focus"]')
        assert box.count() == 1
        assert box.input_value() == PROPOSED, (
            "the walk found the box but did not type the proposal into it")

        # The ring ends up round that box, not parked in the corner where a
        # card with no target sits. Waited for rather than measured once: the
        # step scrolls its control into view smoothly and the ring follows it,
        # so the two rectangles agree a moment after they first exist.
        page.wait_for_function(
            """() => {
                const ring = document.querySelector('.assist-ring');
                const box = document.querySelector('[data-assist="setting-in-focus"]');
                if (!ring || !box || ring.style.display === 'none') return false;
                const r = ring.getBoundingClientRect();
                const b = box.getBoundingClientRect();
                return r.left <= b.left && r.top <= b.top
                    && r.right >= b.right && r.bottom >= b.bottom;
            }""", timeout=10000)

        said = _coach_text(page)
        assert "How long an unplanned job takes" in said, said
        assert "could not find" not in said, said
    finally:
        page.close()


def test_a_page_that_answers_at_once_is_not_made_to_wait(admin, plant):
    """The fast case still lands the way it did: waiting is what happens when
    the control is missing, not a delay added to every walk."""
    page = _walk_onto(admin, plant, _step())
    try:
        page.wait_for_selector(".assist-ring", state="visible", timeout=15000)
        assert page.locator('input[data-assist="setting-in-focus"]').input_value() \
            == PROPOSED
    finally:
        page.close()


# ------------------------------------------- what it says when it really is absent

def test_a_control_that_is_absent_is_not_blamed_on_the_persons_role(admin, plant):
    """The sentence Scott read as a refusal. This walk points at a setting the
    plant does not have, so nothing will ever render the anchor — and the card
    has to say that without inventing a reason for it. ADMIN holds everything
    here, so "for your role" would be simply false."""
    page = _walk_onto(admin, plant, _step("no_such_setting_on_this_plant"))
    try:
        said = _coach_text(page, timeout=(BUDGET_SECONDS + 12) * 1000)
        assert "for your role" not in said, said
        assert "could not find that control on this screen" in said, said
        # How long it looked, said out loud, so "it didn't even try" is not a
        # reading anybody has to guess at.
        assert f"waited {BUDGET_SECONDS} s" in said, said
        # It offers both possibilities and asserts neither.
        assert "may not have finished loading" in said, said
    finally:
        page.close()


def test_the_role_is_named_only_where_the_step_says_what_it_needs_and_they_lack_it(
        operator, plant):
    """The one case where the role *is* the answer, and is knowable: the input
    on this page is only drawn for somebody holding the section's `define`
    capability, the step says which one that is, and this person is an operator
    who does not hold it. Then, and only then, the card says so — and names
    what would change it."""
    page = _walk_onto(operator, plant, _step(needs="process.define"))
    try:
        said = _coach_text(page, timeout=(BUDGET_SECONDS + 12) * 1000)
        assert "process.define" in said, said
        assert "which you do not hold" in said, said
        assert "plant administrator can change it, or grant it" in said, said
    finally:
        page.close()
