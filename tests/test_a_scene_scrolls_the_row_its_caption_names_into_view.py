"""`scroll_text` puts the row a caption is about on screen, even inside a box that scrolls.

An order's list of issued lots is a box of its own with a fixed height, and a
finished order fills it with a hundred rows. The trace clip's caption is about
one of them — the cap lot — so the runner finds the row by its words and
scrolls it into view, box and page both. This drives the runner's own stage in
Chromium against a page of that shape; no plant is needed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"

PAGE = """<!doctype html><html><body style="margin:0">
<div style="height:900px">the order's route, above the fold</div>
<ul id="consumed" style="max-height:200px; overflow-y:auto; margin:0">
""" + "".join(
    f"<li style='height:30px'>LOT-{'CAP' if n == 80 else 'WATER'}-001 — row {n}</li>" for n in range(100)
) + """
</ul><div style="height:900px"></div></body></html>"""


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("record_scene", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _on_screen(page, text: str) -> bool:
    return page.evaluate(
        """(text) => {
             const row = [...document.querySelectorAll('#consumed li')].find((n) => n.innerText.includes(text));
             const box = document.querySelector('#consumed').getBoundingClientRect();
             const r = row.getBoundingClientRect();
             return r.top >= Math.max(0, box.top) && r.bottom <= Math.min(innerHeight, box.bottom);
           }""",
        text,
    )


def test_the_row_named_by_its_words_ends_up_on_screen_inside_its_scrolling_box(runner, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.set_content(PAGE)
        assert not _on_screen(page, "LOT-CAP-001"), "the row starts out of sight, below the page and inside the box"

        stage = runner.BrowserStage(page, tmp_path, 0.0)
        stage.scroll_to("#consumed li", "nearest", 0, "LOT-CAP-001")

        assert _on_screen(page, "LOT-CAP-001")
        browser.close()


def test_a_row_with_those_words_that_is_not_there_stops_the_take(runner, tmp_path):
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content(PAGE)
        stage = runner.BrowserStage(page, tmp_path, 0.0)

        with pytest.raises(Error, match="nothing to scroll to: #consumed li saying LOT-LABEL-001"):
            stage.scroll_to("#consumed li", "nearest", 0, "LOT-LABEL-001")
        browser.close()
