"""The Floor screen's Book output form says what a booking moved, in a browser.

On 2026-11-08 the pretend operator booked 120 good and 3 scrap at FILL01,
saw a toast that said "Output booked." and nothing else, watched the Work
Orders table for a jump of 120 that never came - the order's Good is the
last step's output, and the filler is not the last step - and could not tell
whether its booking had gone in. It also typed its machine into the machine
search first, because both boxes answered to "Machine".

Now the form keeps a line under it after the toast has gone: what was
booked, the step's new count, and the order's, and - when the step is not
the last - that the order's Good counts the last step and will rise when
that step books. The booking's reply is held back half a second by a route
intercept, so the assertions wait for the drawn sentence and not for the
empty line that is there before it (house rule 6).

Same shape as `test_the_trace_page_lists_the_orders_that_drew_on_a_lot.py`.
"""

import json
import socket
import threading
import time

import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A demo plant with WO-BOOK running on both its steps (MIX01 op 10,
    PACK01 op 20), each already carrying counts of its own."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.domain.execution import ProductionSource
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth, execution, workorders

    path = tmp_path_factory.mktemp("book-output") / "plant.db"
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
            workorders.create(session, code="WO-BOOK", material_code="FG-COLA", quantity=400)
            workorders.release(session, "WO-BOOK")
            for seq in (10, 20):
                workorders.start_operation(session, "WO-BOOK", seq)
            execution.report(session, order_code="WO-BOOK", seq=10, good=102, scrap=2, source=ProductionSource.OPC)
            execution.report(session, order_code="WO-BOOK", seq=20, good=95, scrap=1, source=ProductionSource.OPC)
            session.commit()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(create_app(), log_level="warning", lifespan="on"))
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
def page(plant):
    """The Floor screen, signed in, with the booking's reply held back."""
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    from playwright.sync_api import Error as PlaywrightError

    def slowly(route):
        time.sleep(0.5)
        route.continue_()

    try:
        with sync_playwright() as pw:
            chromium = pw.chromium.launch()
            context = chromium.new_context(viewport={"width": 1280, "height": 900})
            reply = context.request.post(
                f"{plant}/auth/login", data=json.dumps({"code": "ADMIN", "password": "admin"}),
                headers={"Content-Type": "application/json"})
            assert reply.ok, f"sign-in failed: {reply.status}"
            open_page = context.new_page()
            open_page.route("**/execution/report", slowly)
            open_page.goto(f"{plant}/dashboard", wait_until="load", timeout=30000)
            open_page.wait_for_function(
                "!document.querySelector('#app').classList.contains('hidden')", timeout=15000)
            yield open_page
            open_page.close()
            chromium.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


def test_the_booking_machine_box_and_the_machine_search_answer_to_different_names(page):
    booking = page.get_by_label("Machine to book on", exact=True)
    search = page.get_by_label("Machine code or name", exact=True)
    assert booking.count() == 1 and search.count() == 1
    assert booking.get_attribute("name") == "equipment"
    assert search.get_attribute("id") == "m-q"
    assert page.get_by_label("Machine", exact=True).count() == 0, (
        "a box on the Floor screen still answers to plain 'Machine'")


def test_booking_at_an_earlier_step_says_the_step_rose_and_the_orders_good_waits_for_the_last_step(page):
    page.get_by_label("Machine to book on", exact=True).fill("MIX01")
    page.get_by_label("Machine to book on", exact=True).dispatch_event("change")
    page.wait_for_function(
        "[...document.querySelectorAll('#form-report [name=order] option')].some(o => o.value === 'WO-BOOK')",
        timeout=15000)
    page.fill("#form-report [name=good]", "120")
    page.fill("#form-report [name=scrap]", "3")
    page.click("#form-report button")

    page.wait_for_function(
        "document.querySelector('#report-result').textContent.includes('Output booked')", timeout=15000)
    said = page.locator("#report-result").text_content()
    assert "120 good, 3 scrap on WO-BOOK op 10" in said
    assert "That step now has 222 good and 5 scrap." in said
    assert "The order's Good stays 95" in said
    assert "op 20" in said and "the last step" in said
    assert "Scrap, counted at every step, is now 6" in said
