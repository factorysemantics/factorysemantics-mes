"""The equipment form's code and name boxes show the whole of what is in them.

On Engineering > Master data > Equipment the code and name boxes were 90px
wide while the level and parent selects took the rest of the row, so a picked
heater band read "FORK1_M" and "Heater ba" (Scott, via the manager,
2026-11-28: "the cut-off code and name boxes"). The two boxes are `wide` now.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"

CODE = "FORK1_Mold_HeaterZone2"
NAME = "Melt temperature sensor"


def test_the_code_and_name_boxes_are_the_wide_kind():
    page = (WEB / "masterdata.html").read_text(encoding="utf-8")
    assert '<input id="eq-code" class="wide"' in page
    assert '<input id="eq-name" class="wide"' in page
    assert ".row > input.wide { flex: 1 1 26ch; }" in (WEB / "styles.css").read_text(encoding="utf-8")


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


@pytest.mark.slow
@pytest.mark.browser
@pytest.mark.parametrize("width", [1400, 1024])
def test_a_long_code_and_name_fit_their_boxes(browser, live_server, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    try:
        live_server.sign_in(context)
        page = context.new_page()
        page.goto(f"{live_server.base}/dashboard/masterdata", wait_until="load", timeout=30000)
        page.fill("#eq-code", CODE)
        page.fill("#eq-name", NAME)
        for box in ("#eq-code", "#eq-name"):
            scroll, client = page.eval_on_selector(box, "(el) => [el.scrollWidth, el.clientWidth]")
            assert scroll <= client, f"{box} cuts its text off at {width}px: {scroll} > {client}"
    finally:
        context.close()
