"""The station screen, on a plant whose order book has run out.

Scott, on the lab bottling plant on 2026-10-07 at 09:00: the **Characteristic**
dropdown on `/dashboard/station?m=QI01` is empty. It was not a broken dropdown.
The plant had finished `WO-ACME-4720` at 04:21 UTC - the last order in its pack
- and from then on had no released or running order at all, so there was no
material in front of the station, no characteristics to list, and nothing to
inspect. What the screen said was *Nothing queued on this machine*, which is
true and sends a person to the wrong place: to this line's schedule, when the
fact is that nobody has planned any work for the whole plant.

So the queue line and the quality card say which of the two it is. Both halves
have to hold:

* No released or running order anywhere on the plant: *No order is open on
  this plant.*
* An order open somewhere but nothing owing on this machine: *Nothing queued
  on this machine.* - the sentence that was always right for that case.

And the third, which is why `/workorders/summary` is read the way it is: a
count that does not arrive is **unknown, not zero**. A station screen whose
summary request is held back must still draw the machine, and must fall back
to the sentence about the machine rather than announce an empty plant it has
no evidence for. That one is driven with an artificial delay - a route
intercept holding the response - because on loopback the request answers in
about two milliseconds and the fallback would never be reached.

Same shape as `test_the_station_redraws_only_what_changed.py`: it seeds a
database, serves it on a loopback port the operating system picks, and drives
Chromium. It touches no plant anyone else is running. Run it with
`pytest -m browser tests/test_the_station_says_when_no_order_is_open_on_the_plant.py`.
"""

import json
import socket
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

MACHINE = "MIX01"           # the demo line's mixer: operation 10 of RT-COLA
MATERIAL = "FG-COLA"
ORDER = "WO-EMPTY-BOOK"
NO_ORDER = "No order is open on this plant."
NOT_HERE = "Nothing queued on this machine."
#: Longer than one refresh tick (REFRESH_MS = 3000) and longer than the
#: summary's own patience (SUMMARY_MS = 2000), so the held-back request is
#: still outstanding when the queue has to be drawn without it.
HELD_MS = 9000


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A demo plant with a machine reporting a state and an empty order book.

    No order at all, which is where a lab plant ends up once its pack's book
    is finished: the mixer is running, the line is making something, and
    nothing in the plant says what.
    """
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine, utcnow
    from fsmes.domain import Equipment, EquipmentState, EquipmentStateName
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("emptybook") / "plant.db"
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
            # The machine picker lists what is reporting a state, so a plant
            # with no open state rows has no station to open.
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


#: Waits for the queue's own text, which is the thing being asserted - never
#: for the element that will hold it. `wait_for_selector("#queue li")` is
#: satisfied by the line this page drew a tick ago.
QUEUE_SAYS = """
(wanted) => {
  const line = document.querySelector("#queue li");
  return !!line && line.textContent.trim() === wanted;
}
"""


def queue_line(page) -> str:
    return page.locator("#queue li").first.text_content().strip()


def test_a_station_on_a_plant_with_no_open_order_says_so(browser, plant):
    """The whole plant's book is empty, and the screen says that, not this line's."""
    page = browser.new_page()
    try:
        page.goto(f"{plant}/dashboard/station?m={MACHINE}", wait_until="load", timeout=30000)
        page.wait_for_function(QUEUE_SAYS, arg=NO_ORDER, timeout=15000)
        assert queue_line(page) == NO_ORDER

        # The quality card is the thing Scott was actually looking at: it has
        # no material, so no characteristics, and it has to say the same why.
        card = page.locator("#q-spec").text_content().strip()
        assert card == "No order is open on this plant, so there is nothing to inspect.", card
        assert page.locator("#q-char option").count() == 0, (
            "characteristics were listed for a plant with no order open")
    finally:
        page.close()


def test_a_station_with_nothing_owing_on_it_still_says_so_when_the_plant_is_busy(browser, plant):
    """An order open somewhere else is a different fact, and keeps its own words.

    The order is released, the mixer's own operation is completed, and the
    order stays open at the packer - which is a line with work on it and
    nothing owing at this station.
    """
    page = browser.new_page()
    try:
        page.goto(f"{plant}/dashboard/station?m={MACHINE}", wait_until="load", timeout=30000)
        page.wait_for_function(QUEUE_SAYS, arg=NO_ORDER, timeout=15000)

        made = browser.request.post(
            f"{plant}/workorders",
            data=json.dumps({"code": ORDER, "material": MATERIAL, "quantity": 100}),
            headers={"Content-Type": "application/json"})
        assert made.ok, f"the order was refused: {made.status} {made.text()}"
        assert browser.request.post(f"{plant}/workorders/{ORDER}/release").ok

        # The queue fills: the sentence is chosen from the plant's state, not
        # printed once. A test that only ever saw an empty queue would pass
        # with the words hard-coded.
        page.wait_for_function(
            """() => !!document.querySelector("#queue li .mono")""", timeout=15000)
        assert ORDER in page.locator("#queue li .mono").first.text_content()

        for action in ("start", "complete"):
            assert browser.request.post(
                f"{plant}/workorders/{ORDER}/operations/10/{action}").ok, action

        page.wait_for_function(QUEUE_SAYS, arg=NOT_HERE, timeout=15000)
        assert queue_line(page) == NOT_HERE
        assert page.evaluate("() => window.__fsmesPageData.anyOpenOrder") is True
    finally:
        page.close()
        # Put the plant back the way the module found it, so the order of the
        # tests in this file is not part of what they prove.
        browser.request.post(f"{plant}/workorders/{ORDER}/cancel")


def test_a_count_that_does_not_arrive_leaves_the_station_drawing_and_says_nothing_about_the_plant(
        browser, plant):
    """Unknown is not zero, and a slow count must not cost the screen the machine.

    `/workorders/summary` is held back for longer than the page's own patience,
    with a route intercept: on loopback it answers in about two milliseconds
    and this path would never be reached. The queue must still draw, it must
    fall back to the sentence about this machine, and the page must not sit on
    *reconnecting…* waiting for a count it does not need.
    """
    import time

    page = browser.new_page()
    try:
        page.route("**/workorders/summary",
                   lambda route: (page.wait_for_timeout(HELD_MS), route.abort())[-1])
        started = time.monotonic()
        page.goto(f"{plant}/dashboard/station?m={MACHINE}", wait_until="load", timeout=30000)

        page.wait_for_function(QUEUE_SAYS, arg=NOT_HERE, timeout=15000)
        # Drawn while the count is still outstanding, not after it gave up:
        # a station that waited for the summary would get here at nine
        # seconds, and on a wall that is a screen somebody reports as frozen.
        waited = time.monotonic() - started
        assert waited < HELD_MS / 1000 / 2, (
            f"the queue took {waited:.1f}s to draw; it waited for the count")
        assert queue_line(page) == NOT_HERE, (
            "the screen named an empty plant from a count that never arrived")
        assert page.evaluate("() => window.__fsmesPageData.anyOpenOrder") is None
        # The rest of the screen is drawn, which is the point: the state
        # pill knows what the mixer is doing.
        assert page.locator("#machine-state").text_content().strip() == "running"
    finally:
        page.close()
