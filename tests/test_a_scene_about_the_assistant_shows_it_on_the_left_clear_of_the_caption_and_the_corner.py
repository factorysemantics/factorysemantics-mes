"""A scene about the Assistant shows it, on the left, clear of the caption bar, the badge and the avatar's corner.

A take hides the product's floating Assistant, because it sits at the bottom
right: under the caption bar and in the narrator's corner. Class 6.2 is about
that Assistant, so a scene may say `assistant = true`: the button stays, and
it and its panel move to the left, above the caption bar's two lines and below
the badge's row. This drives the runner's own overlay in Chromium against a
page laid out as the product's `assist.css` lays them out; no plant is needed.
"""

from __future__ import annotations

import pytest

VIEWPORT = {"width": 1280, "height": 720}
ORIGIN = "http://scene.test/"

# The product's two fixed things, as assist.css places them, the panel as tall
# as it can be.
ASSISTANT = (
    "<!doctype html><body style='margin:0'>"
    "<button class='assist-launch' style='position:fixed;right:18px;bottom:18px;padding:10px 16px'>"
    "Assistant</button>"
    "<section class='assist-panel' style='position:fixed;right:18px;bottom:18px;"
    "width:min(380px,calc(100vw - 36px));max-height:min(560px,calc(100vh - 36px));overflow:hidden'>"
    "<div style='height:2000px'>a long conversation</div></section>"
    "<button class='design-launch' style='position:fixed;right:18px;bottom:60px'>Design</button>"
)


def test_without_the_option_the_assistant_stays_hidden_as_before(runner):
    assert runner.load_scene  # the runner is the recorder module
    css = runner.overlay_script("badge").replace(" ", "")
    assert ".assist-launch,.design-launch{display:none!important}" in css


def test_a_scene_says_assistant_true_to_keep_it_and_is_off_by_default(runner, tmp_path):
    plain = tmp_path / "plain.toml"
    plain.write_text('name = "x"\n[[step]]\ndo = "hold"\n', encoding="utf-8")
    assert runner.load_scene(plain)["assistant"] is False

    shown = tmp_path / "shown.toml"
    shown.write_text('name = "x"\nassistant = true\n[[step]]\ndo = "hold"\n', encoding="utf-8")
    assert runner.load_scene(shown)["assistant"] is True


@pytest.mark.slow
@pytest.mark.browser
def test_with_the_option_the_button_and_the_panel_sit_left_above_the_bar_below_the_badge_and_out_of_the_corner(runner):
    from playwright.sync_api import sync_playwright

    corner_left = VIEWPORT["width"] - runner.AVATAR_WIDTH
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.add_init_script(runner.overlay_script("pre-alpha · simulated plant · vtest", assistant=True))
        page.route(ORIGIN, lambda route: route.fulfill(status=200, content_type="text/html", body=ASSISTANT))
        page.goto(ORIGIN, wait_until="load")

        for selector in ("body > .assist-launch", ".assist-panel"):
            box = page.locator(selector).bounding_box()
            assert box is not None, f"{selector} is not drawn"
            assert box["x"] == runner.ASSIST_LEFT
            assert box["x"] + box["width"] < corner_left
            assert box["y"] + box["height"] <= VIEWPORT["height"] - runner.TOAST_LIFT
            assert box["y"] >= runner.BADGE_TOP + runner.BADGE_HEIGHT

        assert not page.locator(".design-launch").is_visible()
        browser.close()
