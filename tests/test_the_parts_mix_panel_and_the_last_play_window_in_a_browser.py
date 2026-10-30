"""The Parts mix panel and the last-play window on Shift analysis, in a browser.

A plant with no rate order and no parts readings - every plant that is not a
game today - has no Parts mix panel and no "last played" choice: an empty
panel would say "0 a minute", which nobody measured. Once a game reader has
said when it was watching and the plant has a rate order, the analysis opens
on the last time somebody played, named with its times, and the panel lists
every item of the order with its target, shortest first.

The analysis's answer is held back on purpose (a route that waits before it
answers), so the test proves the page waits for the panel's state and does
not read it before it is drawn.

Same shape as `test_the_energy_panel_is_drawn_only_on_a_plant_that_reports_power.py`.
"""

import json
import socket
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

MACHINE = "MIX01"


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("mix") / "plant.db"
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
            yield base, engine
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
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    from playwright.sync_api import Error as PlaywrightError

    base, _ = plant
    try:
        with sync_playwright() as pw:
            chromium = pw.chromium.launch()
            context = chromium.new_context(viewport={"width": 1400, "height": 900})
            reply = context.request.post(
                f"{base}/auth/login",
                data=json.dumps({"code": "ADMIN", "password": "admin"}),
                headers={"Content-Type": "application/json"})
            assert reply.ok, f"sign-in failed: {reply.status}"
            page = context.new_page()
            yield page
            page.close()
            chromium.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


def _open(page, base: str, query: str = "") -> None:
    page.goto(f"{base}/dashboard/analysis{query}", wait_until="load", timeout=30000)
    page.wait_for_selector(".oee-row", timeout=15000)


def test_a_plant_with_no_rate_order_and_no_parts_readings_has_no_panel_and_no_last_play(
        plant, browser):
    base, _ = plant
    _open(browser, base)
    assert browser.locator("#mix-panel").count() == 1
    assert not browser.locator("#mix-panel").is_visible(), "a parts mix on a plant with none"
    assert browser.locator('#shift option[value="last_play"]').count() == 0


def test_with_a_game_feed_and_a_rate_order_it_opens_on_the_last_play_and_draws_the_mix(
        plant, browser):
    from fsmes.db import utcnow
    from fsmes.domain.masterdata import Equipment
    from fsmes.domain.timeseries import TagValue
    from fsmes.services import last_play, parts_mix

    base, engine = plant
    now = utcnow().replace(microsecond=0)
    with Session(engine) as session:
        mixer = session.scalar(select(Equipment).where(Equipment.code == MACHINE))
        session.add_all([
            TagValue(equipment_id=None, tag=last_play.STRETCH, ts=now - timedelta(minutes=m),
                     value_num=v) for m, v in ((20, 0.0), (10, 1.0), (2, 1.0))
        ] + [TagValue(equipment_id=mixer.id, tag=f"{MACHINE}.{parts_mix.ITEM}",
                      ts=now - timedelta(minutes=30), value_text="Iron Plate")])
        session.commit()
    reply = browser.context.request.put(
        f"{base}/rate-order", data=json.dumps({"code": "MIX", "name": "Iron parts", "items": [
            {"item": "Iron Plate", "per_minute": 40}, {"item": "Iron Rod", "per_minute": 20}]}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, reply.text()

    def held(route):
        import time
        time.sleep(1.5)
        route.continue_()

    browser.route("**/analysis/oee*", held)
    try:
        _open(browser, base)
        browser.wait_for_function(
            "!document.querySelector('#mix-panel').classList.contains('hidden')"
            " && document.querySelectorAll('#mix-table tbody tr[data-item]').length === 2",
            timeout=20000)
    finally:
        browser.unroute("**/analysis/oee*")

    # The picker opened on the last time somebody played, named with its times.
    picked = browser.eval_on_selector("#shift", "s => s.options[s.selectedIndex].text")
    assert picked.startswith("last played ")
    assert browser.inner_text("#window-note").startswith("last played ")
    # The hours choices stay.
    assert browser.locator('#shift option[value=""]').count() == 1

    rows = browser.locator("#mix-table tbody tr[data-item]")
    items = [rows.nth(i).get_attribute("data-item") for i in range(rows.count())]
    assert sorted(items) == ["Iron Plate", "Iron Rod"]
    rod = browser.locator('#mix-table tbody tr[data-item="Iron Rod"]').inner_text()
    assert "20.0" in rod and "33 %" in rod
    assert "Rate order Iron parts: 60.0 units/min over 2 items" in browser.inner_text("#mix-facts")
    assert "2 items, shortest first." in browser.inner_text("#mix-note")


def test_asking_for_hours_keeps_hours_on_a_plant_a_game_feeds(plant, browser):
    base, _ = plant
    _open(browser, base, "?hours=8")
    browser.wait_for_function("document.querySelector('#shift').options.length > 1")
    assert browser.eval_on_selector("#shift", "s => s.value") == ""
