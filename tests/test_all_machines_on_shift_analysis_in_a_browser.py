"""All machines on Shift analysis, in a browser.

The line picker's first entry is the whole plant. Picked, the OEE list ranks
every machine on every line worst first and says which line each one is on,
in a Line column that one line at a time does not draw.

Same shape as `test_the_energy_panel_is_drawn_only_on_a_plant_that_reports_power.py`:
a seeded demo plant on a loopback port of the operating system's choosing,
driven by Chromium.
"""

import json
import socket
import threading
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _second_line(session):
    """A second line beside the demo's: one extruder, running the last hour."""
    from fsmes.db import utcnow
    from fsmes.domain import Equipment, EquipmentLevel, EquipmentState, EquipmentStateName

    line = Equipment(code="LINE2", name="Second line", level=EquipmentLevel.WORK_CENTER)
    session.add(line)
    session.flush()
    extruder = Equipment(code="EXT01", name="Extruder", level=EquipmentLevel.WORK_UNIT,
                         parent_id=line.id, ideal_cycle_seconds=1.0)
    session.add(extruder)
    session.flush()
    now = utcnow()
    session.add(EquipmentState(equipment_id=extruder.id, state=EquipmentStateName.RUNNING,
                               started_at=now - timedelta(hours=1), ended_at=None))
    session.flush()


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("allmachines") / "plant.db"
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
            _second_line(session)
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


def _rows(page) -> list[tuple[str, str]]:
    return page.eval_on_selector_all(
        "#oee .oee-row:not(.oee-head)",
        "rows => rows.map(r => [r.querySelector('.who .code').textContent,"
        " (r.querySelector('.line .code') || {}).textContent || ''])")


def test_picking_all_machines_ranks_both_lines_and_names_each_ones_line(plant, browser):
    base, _ = plant
    browser.goto(f"{base}/dashboard/analysis?hours=8", wait_until="load", timeout=30000)
    browser.wait_for_function(
        "document.querySelectorAll('#oee .oee-row').length > 0"
        " && document.querySelector('#line').options.length > 1")

    first = browser.eval_on_selector("#line", "s => [s.options[0].value, s.options[0].text]")
    assert first[0] == "*" and first[1].startswith("All machines"), first
    assert browser.input_value("#line") != "*", "one line is still what the screen opens on"
    assert browser.locator("#oee .oee-row .line").count() == 0, "no Line column for one line"

    browser.select_option("#line", "*")
    browser.wait_for_function(
        "document.querySelector('#oee').classList.contains('plant-wide')"
        " && [...document.querySelectorAll('#oee .oee-row .line .code')]"
        ".some(c => c.textContent === 'LINE2')")

    assert browser.text_content("#oee .oee-head .line") == "Line"
    rows = _rows(browser)
    by_machine = dict(rows)
    assert by_machine["EXT01"] == "LINE2"
    assert by_machine["MIX01"] == "LINE1"
    assert all(line for _, line in rows), "every row says which line it is on"
    total = browser.inner_text("#oee .oee-total")
    assert total.startswith(f"{len(rows)} machines across every line, worst OEE first"), total
    assert "line=*" in browser.url or "line=%2A" in browser.url, "the pick is kept in the address"

    # And back to one line: the Line column goes with it.
    browser.select_option("#line", "LINE2")
    browser.wait_for_function("!document.querySelector('#oee').classList.contains('plant-wide')"
                              " && document.querySelectorAll('#oee .oee-row').length === 1")
    assert browser.locator("#oee .oee-row .line").count() == 0
