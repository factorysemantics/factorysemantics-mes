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
import time

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


@pytest.fixture(scope="module")
def plant(serve_plant):
    """A demo plant with WO-BOOK running on both its steps (MIX01 op 10,
    PACK01 op 20), each already carrying counts of its own."""

    def _seed(session):
        from fsmes.domain.execution import ProductionSource
        from fsmes.services import execution, workorders
        workorders.create(session, code="WO-BOOK", material_code="FG-COLA", quantity=400)
        workorders.release(session, "WO-BOOK")
        for seq in (10, 20):
            workorders.start_operation(session, "WO-BOOK", seq)
        execution.report(session, order_code="WO-BOOK", seq=10, good=102, scrap=2, source=ProductionSource.OPC)
        execution.report(session, order_code="WO-BOOK", seq=20, good=95, scrap=1, source=ProductionSource.OPC)

    with serve_plant(_seed, name="book-output") as served:
        yield served.base


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


def test_typing_a_machine_fills_its_orders_without_leaving_the_box(page):
    """Card 2026-11-19-book-output: "the order list stayed empty until I
    clicked away from the machine field". A datalist input fires `change`
    only when it loses focus, so the orders now come on `input`, as soon as
    the box holds a machine the plant has. No `change` is sent here, and the
    Floor's two-second refresh, which also refills the list, is held still so
    it cannot do the filling for the form."""
    page.wait_for_function("timer !== null", timeout=15000)   # the page shows before its clock starts
    page.evaluate("clearInterval(timer); timer = null")
    box = page.get_by_label("Machine to book on", exact=True)
    box.fill("")
    page.evaluate("document.querySelector('#form-report [name=order]').textContent = ''")
    box.focus()
    box.press_sequentially("PACK01")
    page.wait_for_function(
        "[...document.querySelectorAll('#form-report [name=order] option')].some(o => o.value === 'WO-BOOK')",
        timeout=5000)
    assert page.evaluate("document.activeElement.name") == "equipment", "the box lost focus"
    page.evaluate("timer = setInterval(refresh, 2000)")

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
