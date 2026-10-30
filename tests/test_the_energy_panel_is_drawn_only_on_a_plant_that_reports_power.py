"""The Energy panel on Shift analysis, in a browser.

A plant with no power readings - every plant that is not a game today - has
no Energy panel at all: an empty one would say "0 MW", which nobody
measured. Once a reader has written a circuit's figures and a machine's
draw, the panel is there: the bar of MW drawn against capacity with the
peak marked, the MWh, and the machine's row with every figure in its unit.

Same shape as `test_ui_oee_above_rated.py`: a seeded demo plant on a
loopback port of the operating system's choosing, driven by Chromium.
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

    path = tmp_path_factory.mktemp("energy") / "plant.db"
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


def _open(page, base: str) -> None:
    page.goto(f"{base}/dashboard/analysis", wait_until="load", timeout=30000)
    # The OEE rows are drawn by the same pass that decides the energy panel.
    page.wait_for_selector(".oee-row", timeout=15000)


def test_a_plant_with_no_power_readings_has_no_energy_panel(plant, browser):
    base, _ = plant
    _open(browser, base)
    panel = browser.locator("#energy-panel")
    assert panel.count() == 1
    assert not panel.is_visible(), "an energy panel on a plant that reports no power"


def test_once_a_circuit_and_a_machine_report_power_the_panel_draws_them_with_units(
        plant, browser):
    from fsmes.db import utcnow
    from fsmes.domain.masterdata import Equipment
    from fsmes.domain.timeseries import TagValue

    base, engine = plant
    now = utcnow()
    with Session(engine) as session:
        mixer = session.scalar(select(Equipment).where(Equipment.code == MACHINE))
        session.add_all([
            TagValue(equipment_id=None, tag=f"POWER.CIRCUIT-0.{name}", ts=now - timedelta(minutes=1),
                     value_num=value)
            for name, value in (("CapacityMW", 100.0), ("ConsumedMW", 48.5),
                                ("PeakDemandMW", 141.0), ("FuseTripped", 1.0))
        ] + [
            TagValue(equipment_id=mixer.id, tag=f"{MACHINE}.PowerMW",
                     ts=now - timedelta(minutes=1), value_num=4.0),
            TagValue(equipment_id=mixer.id, tag=f"{MACHINE}.PowerMaxMW",
                     ts=now - timedelta(minutes=1), value_num=4.0),
            TagValue(equipment_id=mixer.id, tag=f"{MACHINE}.EnergyMWh",
                     ts=now - timedelta(minutes=1), value_num=0.0667),
        ])
        session.commit()

    _open(browser, base)
    browser.wait_for_function(
        "!document.querySelector('#energy-panel').classList.contains('hidden')"
        " && document.querySelectorAll('#energy-table tbody tr').length >= 1")
    facts = browser.inner_text("#energy-facts")
    assert "0.067 MWh used in this window" in facts
    # Peak demand over capacity: headroom is negative, and said as a shortfall.
    assert "41.0 MW short at peak demand" in facts
    assert "1 circuit" in facts
    trip = browser.locator("#energy-trip")
    assert trip.is_visible()
    assert "demand 141 MW over capacity 100 MW" in trip.inner_text()
    # The bar: drawing now, the overload beyond capacity, and both marks.
    for mark in (".used", ".over", ".cap", ".peak"):
        assert browser.locator(f"#energy-bar .bar > i{mark}").count() == 1, mark
    row = browser.locator(f'#energy-table tbody tr:has(.code:text-is("{MACHINE}"))')
    text = row.inner_text()
    assert "4.0 MW" in text and "0.067 MWh" in text
    # Nothing was made in the window, so there is no figure a part - a dash,
    # never "0 kWh".
    assert "0.00 kWh" not in text
