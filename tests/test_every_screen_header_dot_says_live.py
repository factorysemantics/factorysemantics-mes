"""Every screen's header dot says what that screen's own calls said.

Scott, 2026-10-25: "when I'm on 'ai' it always says connecting". The header
is built once for every screen by `common.js`, with the dot's words set to
"connecting…"; each screen's script is meant to say "live" once its first
read comes back and "reconnecting…" when one fails. The AI tab never did, so
its dot was not reporting anything - it was the page's birth text, yellow on
the screen the assistant research calls the most demo-ready. A dot that says
"connecting" on a working page teaches a first-time user to distrust the dot
everywhere.

The convention now lives in one place, `FS.live`, and this walks every
dashboard screen that carries the header - read from the module registry, so
a new screen is walked the day it is added - and holds each to it: signed in
as the test admin, within the first refresh interval the dot says "live".

The same file had the same kind of slip in its capability gate: the gate
turned `hidden` off every element the person may use, and the orders page's
New-order popover is one of those, so for anybody who may create an order the
page opened with the form already showing (found while filming clip 04,
#199). The gate decides whether a control is available, never whether a
popover is open.

Seeds a demo plant into a temporary SQLite file, serves it on a loopback port
the operating system picks, drives Chromium. It touches no plant anyone else
is running. Run it with
`pytest -m browser tests/test_every_screen_header_dot_says_live.py`.
"""

import json
import socket
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

MACHINE = "MIX01"
WEB = Path(__file__).resolve().parent.parent / "src" / "fsmes" / "web"
#: Longer than the slowest screen's refresh interval (the floor's and the
#: station's are a few seconds; most screens read once), and well inside the
#: ~10 s Scott would look for on the lab plant.
WITHIN_MS = 15000


def _screens() -> list[str]:
    """Every dashboard path whose page opts into the shared header."""
    from fsmes import modules

    paths = []
    for module in modules.enabled():
        for page in module.pages:
            if "data-nav" in (WEB / page.file).read_text(encoding="utf-8"):
                paths.append(page.path.replace("{code}", MACHINE))
    return paths


SCREENS = _screens()


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
def plant(tmp_path_factory):
    """The demo plant, with the mixer reporting a state so the station and
    machine screens have a machine to open."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine, utcnow
    from fsmes.domain import Equipment, EquipmentState, EquipmentStateName
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("live-dot") / "plant.db"
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
            mixer = session.scalar(select(Equipment).where(Equipment.code == MACHINE))
            session.add(EquipmentState(
                equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                started_at=utcnow() - timedelta(hours=1)))
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


@pytest.fixture(scope="module")
def browser(plant):
    """Chromium, signed in as the test admin."""
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


DOT_SAYS = """
(words) => {
  const text = document.getElementById("live-text");
  const dot = document.getElementById("live-dot");
  return text && dot && text.textContent === words
    && dot.classList.contains(words === "live" ? "ok" : "bad");
}
"""


def test_the_walk_covers_the_ai_tab_and_every_other_screen_with_the_header():
    # A registry that came back empty would make every test below pass by
    # walking nothing.
    assert "/dashboard/ai" in SCREENS
    assert len(SCREENS) >= 25, SCREENS


@pytest.mark.parametrize("path", SCREENS)
def test_within_the_first_refresh_the_header_dot_on_this_screen_says_live(browser, plant, path):
    page = browser.new_page()
    try:
        page.goto(f"{plant}{path}", wait_until="load", timeout=30000)
        try:
            page.wait_for_function(DOT_SAYS, arg="live", timeout=WITHIN_MS)
        except Exception:
            said = page.evaluate(
                "() => [document.getElementById('live-text')?.textContent,"
                " document.getElementById('live-dot')?.className]")
            raise AssertionError(f"{path}: the header dot said {said!r}, not live") from None
    finally:
        page.close()


def test_the_ai_tab_says_connecting_until_its_list_comes_back_and_live_after(browser, plant):
    """Timing-shaped, so proved against a held-back response: the dot on the
    AI tab is driven by the conversation list arriving, not by the page having
    loaded. On loopback both happen in the same few milliseconds."""
    parked = []
    page = browser.new_page()
    try:
        page.route("**/ai/conversations*", lambda route: parked.append(route))
        page.goto(f"{plant}/dashboard/ai", wait_until="load", timeout=30000)
        # The list has been asked for and is being held: the page is drawn,
        # its tabs are up, and nothing has answered yet.
        page.wait_for_function("() => document.querySelector('#ai-tabs .tab')")
        deadline = WITHIN_MS / 1000
        start = time.monotonic()
        while not parked and time.monotonic() - start < deadline:
            page.wait_for_timeout(50)
        assert parked, "the AI tab never asked for its conversation list"
        assert page.text_content("#live-text") == "connecting…"
        for route in parked:
            route.continue_()
        page.wait_for_function(DOT_SAYS, arg="live", timeout=WITHIN_MS)
    finally:
        page.close()


def test_the_ai_tab_says_reconnecting_when_its_list_fails(browser, plant):
    page = browser.new_page()
    try:
        page.route("**/ai/conversations*", lambda route: route.fulfill(
            status=503, content_type="application/json",
            body=json.dumps({"detail": "the plant is not answering"})))
        page.goto(f"{plant}/dashboard/ai", wait_until="load", timeout=30000)
        page.wait_for_function(DOT_SAYS, arg="reconnecting…", timeout=WITHIN_MS)
    finally:
        page.close()


def test_the_orders_page_opens_with_the_new_order_form_closed_for_an_admin(browser, plant):
    page = browser.new_page()
    try:
        page.goto(f"{plant}/dashboard/orders", wait_until="load", timeout=30000)
        # The gate has run once the button it governs is showing; the form is
        # read after that, or "closed" could only mean "not gated yet".
        page.wait_for_function(
            "() => { const b = document.querySelector('button[data-needs-cap=\"orders.create\"]');"
            " return b && !b.classList.contains('hidden'); }", timeout=WITHIN_MS)
        page.wait_for_function(DOT_SAYS, arg="live", timeout=WITHIN_MS)
        assert page.eval_on_selector("#new-order-form", "n => n.classList.contains('hidden')")
        assert not page.is_visible("#new-order-form")
    finally:
        page.close()


def test_the_new_order_button_still_opens_the_form_the_gate_left_closed(browser, plant):
    page = browser.new_page()
    try:
        page.goto(f"{plant}/dashboard/orders", wait_until="load", timeout=30000)
        button = "button[data-needs-cap=\"orders.create\"]"
        page.wait_for_function(
            f"() => {{ const b = document.querySelector('{button}');"
            " return b && !b.classList.contains('hidden'); }", timeout=WITHIN_MS)
        page.click(button)
        page.wait_for_function(
            "() => !document.getElementById('new-order-form').classList.contains('hidden')",
            timeout=WITHIN_MS)
    finally:
        page.close()
