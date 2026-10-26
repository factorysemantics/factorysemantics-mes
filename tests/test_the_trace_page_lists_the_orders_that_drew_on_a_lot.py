"""The trace page, asked about a lot no serialised unit carries, lists the orders that drew on it.

A bottling line books bottles by the lot. Asked "where did this cap lot go",
the trace page used to say "nothing serialised carries LOT-CAP-001" and show
an empty table. It now shows, under its own heading, each order that drew on
the lot - where it went in, how much, and the finished lot that order made -
with the serialised packages below it, as before.

The where-used answer is held back half a second by a route intercept, so the
assertions wait for the drawn state and not for the empty table that is
there before the answer arrives (house rule 6).

Same shape as `test_ui_oee_above_rated.py`: a seeded plant on a loopback port
of the operating system's choosing, driven by Chromium.
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
    """A demo plant with one finished order that drew on LOT-SUGAR-001 at the
    mixer and made WO-TRACE-1-FG, and one order still drawing on it."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth, execution, workorders

    path = tmp_path_factory.mktemp("trace-lot") / "plant.db"
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
            first = workorders.create(session, code="WO-TRACE-1", material_code="FG-COLA", quantity=40)
            workorders.release(session, "WO-TRACE-1")
            execution.consume(session, order_code="WO-TRACE-1", lot_code="LOT-SUGAR-001", quantity=20, seq=10)
            execution.create_lot(session, code="WO-TRACE-1-FG", material_code="FG-COLA", quantity=40,
                                 produced_by_order=first)
            workorders.create(session, code="WO-TRACE-2", material_code="FG-COLA", quantity=40)
            workorders.release(session, "WO-TRACE-2")
            execution.consume(session, order_code="WO-TRACE-2", lot_code="LOT-SUGAR-001", quantity=7, seq=10)
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
    """The trace page asked about LOT-SUGAR-001, with the answer held back."""
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
            open_page.route("**/trace/where-used/**", slowly)
            open_page.goto(f"{plant}/dashboard/trace?lot=LOT-SUGAR-001", wait_until="load", timeout=30000)
            open_page.wait_for_function(
                "document.querySelector('#l-orders-count').textContent.includes('2 order(s)')", timeout=15000)
            yield open_page
            open_page.close()
            chromium.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


def test_the_orders_that_drew_on_the_lot_are_listed_with_their_count(page):
    rows = page.locator("#l-orders tbody tr")
    assert rows.count() == 2
    assert [rows.nth(i).locator("td").first.inner_text() for i in range(2)] == ["WO-TRACE-1", "WO-TRACE-2"]
    assert page.locator("#l-orders-total").inner_text() == "2"


def test_each_order_names_the_station_the_lot_went_in_at_and_the_lot_it_made(page):
    finished = page.locator("#l-orders tbody tr").nth(0)
    assert "MIX01" in finished.inner_text()
    assert finished.locator('a[href="/dashboard/trace?lot=WO-TRACE-1-FG"]').count() == 1
    assert finished.locator('a[href="/dashboard/orders?q=WO-TRACE-1"]').count() == 1
    still_running = page.locator("#l-orders tbody tr").nth(1)
    assert "none yet" in still_running.inner_text(), "an order still drawing has made nothing yet, and says so"


def test_the_empty_serial_table_says_why_and_the_verdict_still_names_the_lot(page):
    assert "carries LOT-SUGAR-001" in page.locator("#l-verdict").text_content()
    assert "2 order(s) drew on it" in page.locator("#l-verdict").text_content()
    assert "books by the lot" in page.locator("#l-table tbody").inner_text()
