"""During a failover the question is not "is it up" — it is "is anybody
reading the machines". The dashboard header answers it, in a browser.

A plant that runs on two nodes serves the same dashboard from both, so the
address a person dialled no longer says which box answered, and "the plant is
up" no longer says whether production is being booked. `/health` carries the
`node` block that says both (`api/routers/system.py`), and this file is the
proof that a person *sees* it: Chromium, the real header, the real page
script.

**The health response is held back for longer than this page takes to draw.**
A check on loopback renders in milliseconds and would pass whether the header
waited for its answer or not; the 2026-09-25 lesson is that Scott's phone does
not render in milliseconds. So `/health` is delayed by a route intercept and
every assertion waits on the *text* the page is about to write, never on the
element that will hold it (#112, 2026-09-26).

The payload is the plant's own `/health`, fetched through the route and then
given a `node` block, rather than a body written here by hand: if the shape of
that response changes, this test changes with it instead of passing against a
response the product no longer sends.

What this file does not do is run two nodes — that is
`test_a_plant_on_two_nodes_survives_one_of_them_dying.py`, which needs a
PostgreSQL. This one pins what the header says about whatever `/health` says,
including on a plant with one node, where it says nothing at all.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for, and a Playwright file marked only `slow` is a test nothing runs.
"""

import json
import socket
import threading

import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

#: How long `/health` is held back for. Comfortably longer than this page takes
#: to draw on loopback, so a passing assertion means the header waited for its
#: answer rather than that the answer was already there.
HELD_MS = 1200

#: What the two nodes of the pretend plant are called.
HERE = "node-b"
READER = "node-a"


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """One plant on SQLite, serving the dashboard. Nothing redundant about it.

    `[redundancy]` needs PostgreSQL and is refused on a file database, which
    is exactly why the two-node *header* is tested by answering `/health`
    differently rather than by starting a second node here.
    """
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("twonodeheader") / "plant.db"
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
    import time
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
def browser(plant):
    """Chromium, signed in the way a person's browser is."""
    sync_playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="the [dev] extra is not installed").sync_playwright

    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            chromium = pw.chromium.launch()
            context = chromium.new_context(viewport={"width": 1400, "height": 900})
            reply = context.request.post(
                f"{plant}/auth/login",
                data=json.dumps({"code": "ADMIN", "password": "admin"}),
                headers={"Content-Type": "application/json"})
            assert reply.ok, f"sign-in failed: {reply.status}"
            yield context
            chromium.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


#: Waits for the header's own words, which is the thing being asserted.
HEADER_SAYS = """
(wanted) => {
  const slot = document.querySelector("#plant-name");
  return !!slot && slot.textContent.includes(wanted);
}
"""


def _open_with_node(browser, base, node, path="/dashboard"):
    """The dashboard, with `/health` held back and answered with this node block.

    `node=None` leaves the plant's own answer alone, delay and all, which is
    the single-node case.
    """
    page = browser.new_page()

    def held(route):
        answer = route.fetch()
        body = answer.json()
        if node is not None:
            body["node"] = node
        page.wait_for_timeout(HELD_MS)
        route.fulfill(status=answer.status, json=body)

    page.route("**/health", held)
    page.goto(f"{base}{path}", wait_until="load", timeout=30000)
    return page


def _close(page):
    """Take the delay off before closing, or a held fetch still in flight
    raises into a route callback whose page has gone away."""
    page.unroute_all(behavior="ignoreErrors")
    page.close()


def test_the_header_says_which_node_answered_and_which_one_reads_the_machines(browser, plant):
    """Two nodes, and this is the one that is not reading. A person watching a
    failover learns both facts from one line."""
    page = _open_with_node(browser, plant, {
        "name": HERE, "named": True, "redundancy": True, "lease_seconds": 10.0,
        "roles": ["floor", "housekeeping"],
        "holders": {"reader": READER, "floor": HERE, "housekeeping": HERE},
    })
    try:
        page.wait_for_function(HEADER_SAYS, arg=f"served by {HERE}", timeout=15000)
        said = page.locator("#plant-name").text_content()
        assert f"machines read by {READER}" in said, said
    finally:
        _close(page)


def test_a_role_nobody_holds_is_nobody_and_not_this_node(browser, plant):
    """The seconds after a failover, and the state of a plant whose database
    no node can reach. Saying "read by" and then this node's own name would be
    the one lie this line must never tell."""
    page = _open_with_node(browser, plant, {
        "name": HERE, "named": True, "redundancy": True, "lease_seconds": 10.0,
        "roles": [], "holders": {"reader": None, "floor": None, "housekeeping": None},
    })
    try:
        page.wait_for_function(HEADER_SAYS, arg="machines read by nobody yet", timeout=15000)
        said = page.locator("#plant-name").text_content()
        assert HERE in said, said
    finally:
        _close(page)


def test_a_plant_on_one_node_says_nothing_about_nodes(browser, plant):
    """Every plant that exists today is this one. A line about which box
    answered, on a plant that has one box, is noise on the screen a person
    looks at all day."""
    page = _open_with_node(browser, plant, None)
    try:
        # The plant's own name arrives in the same response, and it is the
        # thing the header writes first - so waiting for it proves the header
        # has read the whole answer and chosen to say nothing about nodes.
        page.wait_for_function(
            "() => { const s = document.querySelector('#plant-name strong');"
            " return !!s && s.textContent.trim().length > 0; }", timeout=15000)
        said = page.locator("#plant-name").text_content()
        assert "served by" not in said, said
        assert "machines read by" not in said, said
    finally:
        _close(page)
