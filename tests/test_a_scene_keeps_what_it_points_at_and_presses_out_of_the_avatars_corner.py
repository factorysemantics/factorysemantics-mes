"""What a scene points at or presses is kept out of the avatar's corner, and a take that cannot keep it out fails.

Studio draws its narrator over the bottom-right of every frame, 240 by 200
pixels. Once the station screen is scrolled, its right-hand cards (the
queue, Issue material) sit in that corner, and a box drawn round them, or a
cursor pressing Issue, would be under the narrator. The recorder already
lifts a thing out from under the caption bar before it boxes or presses it;
it now lifts a thing that reaches into the avatar's columns above the
avatar's corner, and a box or a press that still lands there fails the take,
as a caption in the corner always has. This drives the runner's own stage in
Chromium against pages of that shape; no plant is needed.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


VIEWPORT = {"width": 1280, "height": 720}
ORIGIN = "http://scene.test/"

# Two cards side by side, as the station screen lays them out, the pair from
# y 520 to 560: across the avatar's corner's top edge, above the caption bar's lift.
CARDS = (
    "<!doctype html><body style='margin:0'><div style='height:520px'>the top of the page</div>"
    "<div style='display:flex;gap:16px;padding:0 16px'>"
    "<div id='left' style='flex:1;height:40px'>Book output</div>"
    "<div id='right' style='flex:1;height:40px'>Issue material</div></div>"
    "<div style='height:900px'>the rest of the page</div>"
)

# A button pinned to the bottom-right: nothing can scroll it out of the corner.
PINNED = (
    "<!doctype html><body style='margin:0'>"
    "<button id='pinned' style='position:fixed;right:20px;bottom:20px'>Issue</button>"
)


def a_page(pw, runner, body: str):
    browser = pw.chromium.launch()
    page = browser.new_page(viewport=VIEWPORT)
    page.add_init_script(runner.overlay_script("pre-alpha · simulated plant · vtest"))
    page.route(ORIGIN, lambda route: route.fulfill(status=200, content_type="text/html", body=body))
    page.goto(ORIGIN, wait_until="load")
    return browser, page


def bottom_of(page, selector: str) -> float:
    box = page.locator(selector).bounding_box()
    return box["y"] + box["height"]


def test_a_card_on_the_right_below_the_corners_top_is_lifted_above_it_with_room_for_the_box(runner, tmp_path):
    from playwright.sync_api import sync_playwright

    corner_top = VIEWPORT["height"] - runner.AVATAR_HEIGHT
    with sync_playwright() as pw:
        browser, page = a_page(pw, runner, CARDS)
        assert bottom_of(page, "#right") > corner_top

        runner.BrowserStage(page, tmp_path, 0.0).clear_of_caption("#right")

        assert bottom_of(page, "#right") + runner.CALLOUT_PAD <= corner_top
        browser.close()


def test_a_card_on_the_left_at_the_same_height_is_left_where_it_is(runner, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = a_page(pw, runner, CARDS)
        before = page.locator("#left").bounding_box()

        runner.BrowserStage(page, tmp_path, 0.0).clear_of_caption("#left")

        assert page.locator("#left").bounding_box() == before
        assert page.evaluate("Math.round(window.scrollY)") == 0
        browser.close()


def test_a_press_that_can_only_land_in_the_corner_is_written_down_and_fails_the_take(runner, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = a_page(pw, runner, PINNED)
        stage = runner.BrowserStage(page, tmp_path, 0.0)

        stage.point_at(page.locator("#pinned"))

        assert len(stage.corner_faults) == 1 and "lands at" in stage.corner_faults[0]
        browser.close()


def test_a_box_drawn_into_the_corner_is_found_by_the_corner_check(runner, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = a_page(pw, runner, PINNED)
        stage = runner.BrowserStage(page, tmp_path, 0.0)

        page.evaluate("window.__sceneCallout(document.getElementById('pinned'), '', 'box')")
        stage.check_corner()

        assert [fault for fault in stage.corner_faults if "#fs-scene-callout reaches" in fault]
        browser.close()
