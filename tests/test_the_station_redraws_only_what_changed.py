"""The station screen, left alone, in a real browser.

Scott, on the rebuilt lab plant on 2026-09-19 and again on 2026-09-20: the
quality section on /dashboard/station kept disappearing and coming back, and
the card changed height while it did. Nothing behind it was changing. The
screen polls every three seconds and every poll cleared the recent-results
list and rebuilt the same rows, so an operator watching a machine saw the
readings blink out twenty times a minute.

A list that redraws for nothing is not a cosmetic complaint on a screen that
hangs on a wall for a whole shift: a reading that blinks is a reading somebody
looks at twice, and the flicker is indistinguishable, at arm's length, from
data arriving.

So this pins both halves of the promise, and both have to hold or the fix is
worth nothing:

* Left alone, across several refreshes, nothing on the screen is rebuilt. Not
  the quality results, not the queue, not the state buttons.
* When a reading is actually recorded, the quality list redraws and shows it.
  A panel that never updates would pass the first test perfectly.

Same shape as `test_ui_nav.py` and `test_ui_oee_above_rated.py`, and marked
slow for the same reason: it seeds a database, serves it on a loopback port
the operating system picks, and drives Chromium. It touches no plant anyone
else is running. Run it with
`pytest -m slow tests/test_the_station_redraws_only_what_changed.py`.
"""

import socket
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.slow

MACHINE = "MIX01"           # the demo line's mixer: operation 10 of RT-COLA
MATERIAL = "FG-COLA"
CHARACTERISTIC = "brix"     # the one specification the demo plant seeds
#: Readings already on the board when the screen opens. Enough rows that a
#: rebuild would be obvious, fewer than the six the panel shows.
READINGS = [10.1, 10.4, 9.9]
#: Two refresh ticks of the station screen (REFRESH_MS = 3000), and room to
#: spare, so a pass cannot mean "the timer had not fired yet".
WATCH_MS = 7500


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A demo plant with work on the mixer and a few readings already taken,
    served over HTTP on a port nobody chose in advance."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine, utcnow
    from fsmes.domain import Equipment, EquipmentState, EquipmentStateName
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth, quality, workorders

    path = tmp_path_factory.mktemp("flicker") / "plant.db"
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
            workorders.create(session, code="WO-FLICKER", material_code=MATERIAL,
                              quantity=100)
            workorders.release(session, "WO-FLICKER")
            for value in READINGS:
                quality.record_check(session, material_code=MATERIAL,
                                     characteristic=CHARACTERISTIC, value=value,
                                     work_order_code="WO-FLICKER",
                                     equipment_code=MACHINE, actor="SCOTT")
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
    import json

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


@pytest.fixture
def station(browser, plant):
    """The station screen on the mixer, with its quality results drawn."""
    page = browser.new_page()
    page.goto(f"{plant}/dashboard/station?m={MACHINE}", wait_until="load", timeout=30000)
    page.wait_for_selector("#q-recent li .mono", timeout=15000)
    page.wait_for_selector("#queue li .mono", timeout=15000)
    try:
        yield page
    finally:
        page.close()


#: Counts the times a list's children are added or removed, from the moment it
#: is installed. A redraw of any of these panels is one or more childList
#: records on the list itself; leaving the DOM alone is none at all.
WATCH = """
(selectors) => {
  window.__redraws = {};
  window.__observers = [];
  for (const selector of selectors) {
    const node = document.querySelector(selector);
    window.__redraws[selector] = 0;
    const observer = new MutationObserver((records) => {
      window.__redraws[selector] += records.length;
    });
    observer.observe(node, { childList: true, subtree: true, characterData: true });
    window.__observers.push(observer);
  }
}
"""

PANELS = ["#q-recent", "#q-count", "#queue", "#state-buttons", "#book-order"]


def test_nothing_on_the_station_is_rebuilt_while_the_plant_stands_still(station):
    """The complaint itself: with no change behind them, the panels are not
    touched across two full refresh intervals. The recent-results list is what
    Scott watched blink, so its rows are also checked by identity - the same
    <li> elements, not equal ones."""
    station.evaluate(WATCH, PANELS)
    station.evaluate("""() => {
        window.__pinned = [...document.querySelectorAll('#q-recent li')];
    }""")

    station.wait_for_timeout(WATCH_MS)

    redraws = station.evaluate("() => window.__redraws")
    assert redraws == dict.fromkeys(PANELS, 0), (
        f"the screen redrew itself with nothing to redraw: {redraws}")

    same, pinned = station.evaluate("""() => {
        const now = [...document.querySelectorAll('#q-recent li')];
        return [window.__pinned.length === now.length
                && window.__pinned.every((li, i) => li === now[i]),
                window.__pinned.length];
    }""")
    assert pinned == len(READINGS), (
        f"the panel drew {pinned} readings, not the {len(READINGS)} recorded")
    assert same, "the quality results were rebuilt: different elements, same readings"


def test_the_quality_panel_still_shows_a_reading_that_was_just_recorded(station, plant):
    """The other half. A panel that never redraws would pass the test above
    and be useless on a floor, so the same screen is left alone and then given
    something to say."""
    import json

    station.evaluate(WATCH, PANELS)
    before = station.locator("#q-recent li").count()

    reply = station.context.request.post(
        f"{plant}/quality/checks",
        data=json.dumps({"material": MATERIAL, "characteristic": CHARACTERISTIC,
                         "value": 10.2}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"recording a reading failed: {reply.status}"

    station.wait_for_selector(
        f"#q-recent li:nth-child({before + 1})", timeout=15000)
    assert station.evaluate("() => window.__redraws['#q-recent']") > 0, (
        "a new reading did not redraw the list")
    assert f"of {len(READINGS) + 1}" in station.locator("#q-count").text_content(), (
        "the panel did not say what its slice is a slice of")
