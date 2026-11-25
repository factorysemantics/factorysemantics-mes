"""On the Quality screen, Export xlsx downloads the range the history shows.

A supervisor picks From and To in the inspection history's bar - in the bar,
not behind a toggle, because the pretend supervisor read the old "When"
toggle as a sort - presses Export xlsx, and gets a file with the rows the
table counted. The history is fetched with an artificial delay, so the link
is proved to follow the range once the list has answered, not on loopback's
six milliseconds.
"""

import io
import time
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

DELAY_SECONDS = 0.8
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _september_and_either_side(session) -> None:
    """Five checks in September, two outside it on either side."""
    from fsmes.services import quality

    for day in (1, 8, 15, 22, 30):
        check, _nc, _ = quality.record_check(session, material_code="FG-COLA",
                                             characteristic="brix", value=10.2, actor="ines")
        check.ts = datetime(2026, 9, day, 10, 0)
    for when in (datetime(2026, 8, 31, 10, 0), datetime(2026, 10, 1, 10, 0)):
        check, _nc, _ = quality.record_check(session, material_code="FG-COLA",
                                             characteristic="brix", value=10.2, actor="ines")
        check.ts = when


@pytest.fixture(scope="module")
def plant(serve_plant):
    """The demo plant with those seven checks, served by `tests/conftest.py`.
    A plant of this file's own: the shared one may hold brix checks of other
    tests' making, and this file counts every one in its range."""
    with serve_plant(_september_and_either_side, name="export-xlsx") as served:
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


def test_export_downloads_every_check_of_the_chosen_range(browser, plant):
    context = browser.new_context(viewport={"width": 1400, "height": 1000}, accept_downloads=True)
    try:
        plant.sign_in(context)
        page = context.new_page()
        page.route("**/quality/checks?*",
                   lambda route: (time.sleep(DELAY_SECONDS), route.continue_()))
        page.goto(f"{plant.base}/dashboard/quality", wait_until="load", timeout=30000)

        page.fill("#h-from", "2026-09-01")
        page.dispatch_event("#h-from", "change")
        page.fill("#h-to", "2026-09-30")
        page.dispatch_event("#h-to", "change")
        page.wait_for_function(
            """() => document.querySelector('#h-count').textContent.includes('of 5 matching')
                     && document.querySelector('#h-export').getAttribute('href')
                          .includes('since=2026-09-01&until=2026-09-30')""",
            timeout=20000)
        assert page.is_visible("#h-from") and page.is_visible("#h-export")
        assert "5 checks" in page.get_attribute("#h-export", "aria-label")

        with page.expect_download(timeout=20000) as caught:
            page.click("#h-export")
        download = caught.value
        assert download.suggested_filename == "quality-checks-2026-09-01-to-2026-09-30.xlsx"
        body = Path(download.path()).read_bytes()
    finally:
        context.close()

    with zipfile.ZipFile(io.BytesIO(body)) as z:
        sheet = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = sheet.find("m:sheetData", NS)
    assert len(rows) == 1 + 5
