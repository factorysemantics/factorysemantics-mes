"""An order's Work instructions link opens the procedure for what it makes.

The operator walk on 2026-11-10 found the running order and no way from it to
its instructions; with a plain link it reached the whole catalogue and had to
guess which document was the order's. Here the link carries the order's
material, the Instructions page lists only the documents anchored to it (and
says so, with the way back to all of them), and the route's own instruction
opens first - although a specification's instruction for the same material
sorts ahead of it. The order's detail and the catalogue are each held back by
an artificial delay, so the link is proved to follow the order once it has
answered, not on loopback's six milliseconds.
"""

import time

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

DELAY_SECONDS = 0.8


def _seed(session) -> None:
    from fsmes.services import documents, workorders

    workorders.create(session, code="WO-WI-1", material_code="FG-COLA", quantity=10)
    # Codes chosen so the specification's instruction sorts first: the page
    # must pick the route's on purpose, not by being first in the list.
    for code, title, anchors in (
        ("WI-A-COLA-BRIX", "Measuring brix on FG-COLA", {"material": "FG-COLA", "characteristic": "brix"}),
        ("WI-B-COLA-ROUTE", "Making Cola Syrup", {"material": "FG-COLA"}),
        ("WI-C-SUGAR", "Receiving sugar", {"material": "RAW-SUGAR"}),
    ):
        documents.create(session, code=code, title=title, body="1. Do the step.", anchors=anchors)
        documents.approve(session, code, 1)
    session.flush()


@pytest.fixture(scope="module")
def plant(serve_plant):
    """The demo plant with the order and its three instructions, served by
    `tests/conftest.py`. A plant of this file's own: the page counts every
    document in the plant, and the shared one may hold other tests'."""
    with serve_plant(_seed, name="order-instructions") as served:
        yield served


@pytest.fixture(scope="module")
def browser():
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            chromium = pw.chromium.launch()
            yield chromium
            chromium.close()
    except PlaywrightError as err:
        pytest.skip(f"chromium is not installed for playwright: {err}")


def test_the_link_on_an_order_opens_the_instructions_for_its_material_route_first(browser, plant):
    context = browser.new_context(viewport={"width": 1400, "height": 1000})
    try:
        plant.sign_in(context)
        page = context.new_page()
        page.route("**/workorders/WO-*", lambda route: (time.sleep(DELAY_SECONDS), route.continue_()))
        page.route("**/documents", lambda route: (time.sleep(DELAY_SECONDS), route.continue_()))

        page.goto(f"{plant.base}/dashboard/orders", wait_until="load", timeout=30000)
        page.wait_for_function(
            "() => (document.querySelector('#d-instructions').getAttribute('href') || '')"
            ".includes('material=')", timeout=20000)
        material = page.text_content("#d-material").strip()
        assert material == "FG-COLA"
        assert page.get_attribute("#d-instructions", "href") == "/dashboard/instructions?material=FG-COLA"
        assert page.is_visible("#d-instructions")

        # The check runs on whichever page is loaded; until the Instructions
        # page is, there is no #doc-title, so wait for its address first and
        # read the title null-safe.
        page.click("#d-instructions")
        page.wait_for_url("**/dashboard/instructions?material=FG-COLA", timeout=20000)
        page.wait_for_function(
            "() => (document.querySelector('#doc-title')?.textContent || '').trim() === 'Making Cola Syrup'",
            timeout=20000)
        listed = page.eval_on_selector_all(
            "#catalogue tbody tr td.mono", "cells => cells.map((c) => c.textContent.trim())")
        assert listed == ["WI-A-COLA-BRIX", "WI-B-COLA-ROUTE"]
        assert page.is_visible("#doc-for")
        assert page.text_content("#doc-for-material") == "FG-COLA"
        # The list states its total: two shown, of the plant's three.
        assert page.text_content("#catalogue-count").strip() == "— 2 of 2 matching, 3 in the plant"

        page.click("#doc-for-clear")
        page.wait_for_function(
            "() => document.querySelectorAll('#catalogue tbody tr td.mono').length === 3", timeout=20000)
        assert not page.is_visible("#doc-for")
    finally:
        context.close()
