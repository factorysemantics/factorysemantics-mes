"""A row a scene scrolls to at the foot of a short page is lifted clear of the caption bar, and no higher than asked.

A `scroll_to` puts its row `offset` pixels from the top of the window, as near
as the page can. At the foot of a short page it cannot, and the second take of
class 2.1 left the station screen's "Nothing owed on this machine" under the
caption bar for that reason. The recorder now adds room below the page and
lifts the row clear, but only as far as the offset the scene asked for, so a
row that reached its offset, or a panel taller than the window, is where it
always was. This drives the runner's own stage in Chromium; no plant is needed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"

VIEWPORT = {"width": 800, "height": 600}
# A maintenance list that is the last thing on a page a little taller than the window.
SHORT = (
    "<!doctype html><body style='margin:0'><div style='height:700px'>the cards above</div>"
    "<ul id='maint' style='margin:0;height:40px'><li>Nothing owed on this machine.</li></ul>"
)
# A panel taller than the window, with plenty of page below it.
TALL = (
    "<!doctype html><body style='margin:0'><div style='height:900px'>above</div>"
    "<div id='detail-panel' style='height:1200px'>the order</div><div style='height:2000px'></div>"
)


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("record_scene_scroll_room", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_last_row_of_a_short_page_ends_up_above_the_caption_bar(runner, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_content(SHORT)
        clear = VIEWPORT["height"] - runner.CAPTION_HEIGHT * 2

        runner.BrowserStage(page, tmp_path, 0.0).scroll_to("#maint", "start", 320)

        box = page.locator("#maint").bounding_box()
        assert box["y"] + box["height"] <= clear, box
        assert box["y"] >= 320 - 1, box
        browser.close()


def test_a_panel_taller_than_the_window_stays_at_the_offset_it_was_scrolled_to(runner, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_content(TALL)

        runner.BrowserStage(page, tmp_path, 0.0).scroll_to("#detail-panel", "start", 220)

        assert round(page.locator("#detail-panel").bounding_box()["y"]) == 220
        assert page.evaluate("document.getElementById('fs-scene-room')") is None
        browser.close()
