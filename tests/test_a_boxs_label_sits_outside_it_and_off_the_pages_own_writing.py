"""The label on a box sits just outside it, and never over the page's own writing.

The recorder used to put a box's label on the box's top-left edge, above it.
On the station screen the boxed thing sits under its card's title, so the
label covered the title's first words ("Jobs due" over MAINTENANCE, "In
spec" over RECENT RESULTS in class 2.1, 2026-11-15). The label now tries
spots in turn (above, below, beside the right edge, beside the left), passes
over any that sits on text of the page's, under the header, on the caption
bar or in the narrator's corner, and refuses the take when none is clear.
This drives the runner's own stage in Chromium against pages of those
shapes; no plant is needed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"

VIEWPORT = {"width": 1280, "height": 720}
ORIGIN = "http://scene.test/"

# A full-width card: its title at the top left, the boxed reading straight under it.
CARD = (
    "<!doctype html><body style='margin:0;font:16px sans-serif'>"
    "<div style='position:absolute;left:16px;top:200px;width:1248px'>"
    "<h3 id='title' style='margin:0;height:22px;line-height:22px'>MAINTENANCE WINDOW AND JOBS</h3>"
    "<div id='jobs' style='margin-top:4px;height:80px'>3 due</div></div>"
)

# A thing at the very top of the frame: there is no room above it.
AT_TOP = (
    "<!doctype html><body style='margin:0;font:16px sans-serif'>"
    "<div id='top' style='position:absolute;left:300px;top:8px;width:300px;height:40px'>at the top</div>"
)

# A card on the right just above the narrator's corner (which starts at y 520,
# x 1040), its title row written at both ends, as the station's cards are:
# above is on the title, below is in the corner, right is off the frame; only
# the left is clear.
BY_THE_CORNER = (
    "<!doctype html><body style='margin:0;font:16px sans-serif'>"
    "<div style='position:absolute;left:1000px;top:396px;width:270px'>"
    "<h3 id='title' style='margin:0;height:22px;line-height:22px;display:flex;justify-content:space-between'>"
    "<span id='name'>ISSUE MATERIAL</span><span id='count'>2 lots</span></h3>"
    "<div id='issue' style='margin-top:6px;height:84px'>lot</div></div>"
)

# A thing as big as the frame: there is no outside to put a label in.
EVERYTHING = (
    "<!doctype html><body style='margin:0'>"
    "<div id='all' style='position:absolute;left:4px;top:4px;right:4px;bottom:4px'>all of it</div>"
)


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("record_scene_label_outside", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def on_a_page(runner, tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        def open_page(body: str):
            page = browser.new_page(viewport=VIEWPORT)
            page.add_init_script(runner.overlay_script("pre-alpha · simulated plant · vtest"))
            page.route(ORIGIN, lambda route: route.fulfill(status=200, content_type="text/html", body=body))
            page.goto(ORIGIN, wait_until="load")
            stage = runner.BrowserStage(page, tmp_path, 0.0)
            stage.step_number = 7
            return stage

        yield open_page
        browser.close()


def rect(page, selector: str) -> dict:
    return page.evaluate(
        """(sel) => { const r = document.querySelector(sel).getBoundingClientRect();
                      return {x: r.left, y: r.top, w: r.width, h: r.height}; }""",
        selector,
    )


def writing(page, selector: str) -> dict:
    """Where the words of `selector` are drawn, not the element round them."""
    return page.evaluate(
        """(sel) => { const range = document.createRange();
                      range.selectNodeContents(document.querySelector(sel));
                      const r = range.getBoundingClientRect();
                      return {x: r.left, y: r.top, w: r.width, h: r.height}; }""",
        selector,
    )


def the_label(page) -> dict:
    return rect(page, "#fs-scene-callout-label")


def overlap(a: dict, b: dict) -> bool:
    across = a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
    return across and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def test_a_label_on_a_box_under_a_card_title_does_not_cover_the_title(on_a_page):
    stage = on_a_page(CARD)

    stage.say("Three jobs are due.", 0.05, at="#jobs", label="Jobs due")

    label, title = the_label(stage.page), writing(stage.page, "#title")
    assert not overlap(label, title), f"the label {label} covers the card's title {title}"
    assert not overlap(label, rect(stage.page, "#fs-scene-callout")), "the label is on its own box"
    assert stage.marks[-1]["label_box"] == {key: round(label[key]) for key in ("x", "y", "w", "h")}


def test_a_box_at_the_top_of_the_frame_gets_its_label_below_it(on_a_page):
    stage = on_a_page(AT_TOP)

    stage.say("At the top.", 0.05, at="#top", label="Top")

    label, box = the_label(stage.page), rect(stage.page, "#fs-scene-callout")
    assert label["y"] >= box["y"] + box["h"], f"the label {label} is not below the box {box}"
    assert label["y"] + label["h"] <= VIEWPORT["height"] - stage_caption_height(stage)


def stage_caption_height(stage) -> int:
    return stage.page.evaluate("document.getElementById('fs-scene-cap').offsetHeight")


def test_a_box_beside_the_narrators_corner_keeps_its_label_out_of_the_corner_and_off_the_title(runner, on_a_page):
    stage = on_a_page(BY_THE_CORNER)

    stage.say("Issue the lot.", 0.05, at="#issue", label="Issue")

    label = the_label(stage.page)
    corner = {"x": VIEWPORT["width"] - runner.AVATAR_WIDTH, "y": VIEWPORT["height"] - runner.AVATAR_HEIGHT,
              "w": runner.AVATAR_WIDTH, "h": runner.AVATAR_HEIGHT}
    assert not overlap(label, corner), f"the label {label} is in the narrator's corner"
    for words in ("#name", "#count"):
        assert not overlap(label, writing(stage.page, words)), f"the label {label} covers the card's title"
    assert label["x"] >= 0 and label["x"] + label["w"] <= VIEWPORT["width"]


def test_a_label_with_no_clear_spot_refuses_the_take_naming_the_step(on_a_page):
    stage = on_a_page(EVERYTHING)

    with pytest.raises(SystemExit, match=r"^step 7: the label 'Everything'"):
        stage.say("All of it.", 0.05, at="#all", label="Everything")
