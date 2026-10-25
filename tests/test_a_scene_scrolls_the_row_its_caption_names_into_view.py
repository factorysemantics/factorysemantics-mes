"""`scroll_text` puts the row a caption is about on screen, even inside a box that scrolls.

An order's list of issued lots is a box of its own with a fixed height, and a
finished order fills it with a hundred rows. The trace clip's caption is about
one of them — the cap lot — so the runner finds the row by its words, puts it
at the top of its box, and puts the box where the scene says on the page —
clear of the caption bar along the bottom, even when the box is at the foot of
a page too short to scroll it any higher. This drives the runner's own stage
in Chromium against pages of that shape; no plant is needed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"

ROWS = "".join(
    f"<li style='height:30px'>LOT-{'CAP' if n == 80 else 'WATER'}-001 — row {n}</li>" for n in range(100)
)
BOX = f"<ul id='consumed' style='max-height:200px; overflow-y:auto; margin:0; padding:0'>{ROWS}</ul>"
# The orders page as it is: the Genealogy box is the last thing on it.
SHORT_PAGE = f"<!doctype html><body style='margin:0'><div style='height:900px'>the route</div>{BOX}"
PAGE = SHORT_PAGE + "<div style='height:900px'></div>"
VIEWPORT = {"width": 800, "height": 600}
CAPTION_BAR = 110


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("record_scene", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(page, text: str) -> dict:
    return page.evaluate(
        """(text) => {
             const row = [...document.querySelectorAll('#consumed li')].find((n) => n.innerText.includes(text));
             const box = document.querySelector('#consumed').getBoundingClientRect();
             const r = row.getBoundingClientRect();
             return {top: r.top, bottom: r.bottom, inBox: r.top >= box.top && r.bottom <= box.bottom};
           }""",
        text,
    )


def test_the_row_named_by_its_words_lands_where_the_scene_puts_it(runner, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_content(PAGE)
        assert not _row(page, "LOT-CAP-001")["inBox"], "the row starts out of sight inside its box"

        runner.BrowserStage(page, tmp_path, 0.0).scroll_to("#consumed li", "center", 250, "LOT-CAP-001")

        row = _row(page, "LOT-CAP-001")
        assert row["inBox"]
        assert row["top"] == pytest.approx(250, abs=2)
        browser.close()


def test_a_row_at_the_foot_of_a_short_page_still_ends_up_clear_of_the_caption_bar(runner, tmp_path):
    """The first takes left the cap lot's row under the caption: the page ends, so it could not move up."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_content(SHORT_PAGE)

        runner.BrowserStage(page, tmp_path, 0.0).scroll_to("#consumed li", "center", 250, "LOT-CAP-001")

        row = _row(page, "LOT-CAP-001")
        assert row["inBox"]
        assert row["bottom"] <= VIEWPORT["height"] - CAPTION_BAR
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
