"""A button a scene names by its words is brought out from under the caption bar before it is pressed.

Before a click, fill or choose the recorder moves the thing out from under
the caption bar (class 1.3's "Add operation"). Scenes name buttons by their
words, `#order-actions button:has-text("Release")`, which only Playwright's
own locator reads: the first take of class 1.4 stopped on its Release button
because the move looked the selector up with `document.querySelector`. This
drives the runner's own stage in Chromium against a page of that shape; no
plant is needed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"

VIEWPORT = {"width": 800, "height": 600}
# The Release button at the very foot of a page too short to scroll it higher.
PAGE = (
    "<!doctype html><body style='margin:0'><div style='height:560px'>the order</div>"
    "<div id='order-actions'><button>Hold…</button><button>Release</button></div>"
)


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("record_scene_clear_of_caption", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_button_named_with_has_text_at_the_foot_of_a_page_ends_up_above_the_caption_bar(runner, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_content(PAGE)
        button = page.locator('#order-actions button:has-text("Release")')
        clear = VIEWPORT["height"] - runner.CAPTION_HEIGHT * 2
        assert button.bounding_box()["y"] + button.bounding_box()["height"] > clear

        runner.BrowserStage(page, tmp_path, 0.0).clear_of_caption('#order-actions button:has-text("Release")')

        box = button.bounding_box()
        assert box["y"] + box["height"] <= clear
        browser.close()


def test_a_selector_that_matches_nothing_yet_is_left_for_the_step_itself_to_wait_on(runner, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_content(PAGE)

        runner.BrowserStage(page, tmp_path, 0.0).clear_of_caption('#order-actions button:has-text("Close")')

        assert page.evaluate("document.getElementById('fs-scene-room')") is None
        browser.close()
