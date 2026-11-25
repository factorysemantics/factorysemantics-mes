"""The trace page, asked about a lot no serialised unit carries, lists the orders that drew on it.

A bottling line books bottles by the lot. Asked "where did this cap lot go",
the trace page used to say "nothing serialised carries LOT-CAP-001" and show
an empty table. It now shows, under its own heading, each order that drew on
the lot - where it went in, how much, and the finished lot that order made -
with the serialised packages below it, as before.

The where-used answer is held back half a second by a route intercept, so the
assertions wait for the drawn state and not for the empty table that is
there before the answer arrives (house rule 6).

A seeded plant served by `serve_plant` from `tests/conftest.py` on a loopback
port of the operating system's choosing, driven by Chromium.
"""

import time

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _two_orders_on_the_sugar_lot(session) -> None:
    """One finished order that drew on LOT-SUGAR-001 at the mixer and made
    WO-TRACE-1-FG, and one order still drawing on it."""
    from fsmes.services import execution, workorders

    first = workorders.create(session, code="WO-TRACE-1", material_code="FG-COLA", quantity=40)
    workorders.release(session, "WO-TRACE-1")
    execution.consume(session, order_code="WO-TRACE-1", lot_code="LOT-SUGAR-001", quantity=20, seq=10)
    execution.create_lot(session, code="WO-TRACE-1-FG", material_code="FG-COLA", quantity=40,
                         produced_by_order=first)
    workorders.create(session, code="WO-TRACE-2", material_code="FG-COLA", quantity=40)
    workorders.release(session, "WO-TRACE-2")
    execution.consume(session, order_code="WO-TRACE-2", lot_code="LOT-SUGAR-001", quantity=7, seq=10)


@pytest.fixture(scope="module")
def plant(serve_plant):
    """The demo plant with those two orders, served by `tests/conftest.py`.
    A plant of this file's own: the shared one may hold other tests' draws
    on the sugar lot, and this file counts every order that drew on it."""
    with serve_plant(_two_orders_on_the_sugar_lot, name="trace-lot") as served:
        yield served


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
            plant.sign_in(context)
            open_page = context.new_page()
            open_page.route("**/trace/where-used/**", slowly)
            open_page.goto(f"{plant.base}/dashboard/trace?lot=LOT-SUGAR-001", wait_until="load", timeout=30000)
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
