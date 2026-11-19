"""The recorder draws a cursor that clicks, a box round what a line is about, and readable typing.

Scott, 2026-11-15, on the first five classes: "Just the screen doesn't explain
anything." These tests film the demo plant's dashboard (the shared
`live_server`, read only) and check what a viewer would see: a drawn cursor on
the page from the first `goto`, sitting on the control after a click; a box
round the thing a `say` names, written to marks.json in frame pixels; a take
refused, with the step's number, when that thing is not on the page; a `say`
without `at` marked exactly as before; and typing no faster than twelve keys
a second.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"

#: The keys a plain `say` mark has always had; studio reads them.
PLAIN_SAY_KEYS = {"at", "what", "kind", "part", "line", "hold"}

SCENE = """
name = "pointer"
base = "{base}"

[[step]]
do = "goto"
url = "/dashboard"
settle = 0.5

[[step]]
do = "wait_for"
selector = "#m-count"
js = "/[0-9]/.test(document.querySelector('#m-count').textContent)"

[[step]]
do = "say"
line = "Plant OEE is over the machines that have one."
hold = 0.6
at = "#kpi-oee"
label = "Plant OEE"

[[step]]
do = "say"
line = "A line that points at nothing."
hold = 0.3

[[step]]
do = "fill"
selector = "#m-q"
text = "FILLER"

[[step]]
do = "choose"
selector = "#m-state"
value = "running"

[[step]]
do = "click"
selector = "#m-clear"

[[step]]
do = "say"
line = "The rest of the screen is dimmed."
hold = 0.4
at = "#m-q"
style = "spotlight"
"""


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("record_scene_cursor", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def take(runner, live_server, tmp_path_factory):
    """One take of the scene above, filmed with Chromium alone; its scene and its marks."""
    out = tmp_path_factory.mktemp("pointer")
    scene_file = out / "pointer.toml"
    scene_file.write_text(SCENE.format(base=live_server.base), encoding="utf-8")
    scene = runner.load_scene(scene_file)
    made = runner.record(scene, out / "take")
    return scene, made["marks"]


@pytest.fixture()
def stage(runner, live_server, tmp_path):
    """A stage on a page of the demo plant with the overlay on it, for asking the page itself."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        live_server.sign_in(context)
        context.add_init_script(runner.overlay_script("badge"))
        page = context.new_page()
        yield runner.BrowserStage(page, tmp_path, time.monotonic()), live_server.base
        context.close()
        browser.close()


def cursor_tip(page) -> tuple[float, float] | None:
    return page.evaluate("""() => {
        const c = document.getElementById('fs-scene-cursor');
        if (!c) return null;
        const m = new DOMMatrixReadOnly(getComputedStyle(c).transform);
        return [m.m41, m.m42];
    }""")


def test_a_say_with_at_writes_the_box_it_drew_in_frame_pixels(take):
    _scene, marks = take
    pointed = [mark for mark in marks if mark["kind"] == "say" and "box" in mark]

    assert len(pointed) == 2, f"{len(pointed)} of the scene's 2 pointed says carry a box"
    for mark in pointed:
        box = mark["box"]
        assert set(box) == {"x", "y", "w", "h"} and all(isinstance(v, int) for v in box.values()), box
        assert box["w"] > 0 and box["h"] > 0, box
        assert box["x"] >= 0 and box["x"] + box["w"] <= 1280, box
        assert box["y"] >= 0 and box["y"] + box["h"] <= 720, box
    first, second = pointed
    assert (first["selector"], first["label"], first["style"]) == ("#kpi-oee", "Plant OEE", "box")
    assert (second["selector"], second["label"], second["style"]) == ("#m-q", "", "spotlight")


def test_a_say_without_at_is_marked_with_exactly_the_keys_it_always_had(take):
    _scene, marks = take
    plain = [mark for mark in marks if mark["kind"] == "say" and mark["line"] == "A line that points at nothing."]

    assert len(plain) == 1
    assert set(plain[0]) == PLAIN_SAY_KEYS, set(plain[0]) ^ PLAIN_SAY_KEYS


def test_a_press_is_marked_with_where_the_cursor_came_from_and_when_the_ripple_began(take):
    _scene, marks = take
    pressed = [mark for mark in marks if mark["kind"] in ("click", "fill", "choose")]

    assert [mark["kind"] for mark in pressed] == ["fill", "choose", "click"]
    for mark in pressed:
        assert set(mark["cursor_from"]) == {"x", "y"}, mark
        assert isinstance(mark["x"], int) and isinstance(mark["y"], int), mark
        assert mark["ripple_at"] <= mark["at"], f"the ripple starts as the press is sent, not after: {mark}"


def test_typing_takes_at_least_a_twelfth_of_a_second_a_key(take):
    _scene, marks = take
    (fill,) = [mark for mark in marks if mark["kind"] == "fill"]

    assert fill["typed_seconds"] >= len("FILLER") / 12, fill


def test_the_cursor_is_on_the_page_from_the_first_goto_and_on_the_control_after_a_click(stage):
    stage, base = stage
    stage.goto(f"{base}/dashboard", 0.3)
    assert cursor_tip(stage.page) is not None, "no cursor drawn on the page"

    stage.click("#m-clear")
    mark = stage.marks[-1]

    tip = cursor_tip(stage.page)
    assert tip is not None
    assert abs(tip[0] - mark["x"]) <= 1 and abs(tip[1] - mark["y"]) <= 1, (tip, mark)
    box = stage.page.locator("#m-clear").bounding_box()
    assert box["x"] <= tip[0] <= box["x"] + box["width"] and box["y"] <= tip[1] <= box["y"] + box["height"], (
        f"the cursor's tip {tip} is not on the button {box}")


def test_a_page_shown_inside_a_page_gets_no_second_cursor_caption_or_badge(stage):
    # The Line page's 3D tab is a page of its own inside the Line page, and the
    # overlay script runs in every document; it draws only in the top one.
    stage, base = stage
    stage.goto(f"{base}/dashboard", 0.3)
    stage.page.evaluate("""() => new Promise((done) => {
        const frame = document.createElement('iframe');
        frame.id = 'inner'; frame.src = '/dashboard'; frame.onload = () => done();
        document.body.appendChild(frame);
    })""")
    inner = stage.page.frame_locator("#inner")

    drawn_inside = [part for part in ("fs-scene-badge", "fs-scene-cap", "fs-scene-cursor")
                    if inner.locator(f"#{part}").count()]
    assert drawn_inside == [], f"drawn inside the inner page as well: {drawn_inside}"
    assert stage.page.locator("#fs-scene-badge").count() == 1


def test_the_cursor_is_never_drawn_over_the_caption_bar_or_the_badge(stage):
    stage, base = stage
    stage.goto(f"{base}/dashboard", 0.1)
    layers = stage.page.evaluate("""() => Object.fromEntries(
        ['fs-scene-cursor', 'fs-scene-cap', 'fs-scene-badge'].map(
          (id) => [id, Number(getComputedStyle(document.getElementById(id)).zIndex)]))""")

    assert layers["fs-scene-cursor"] < layers["fs-scene-cap"] < layers["fs-scene-badge"], layers


def test_typing_a_field_takes_at_least_a_twelfth_of_a_second_a_key(stage):
    stage, base = stage
    stage.goto(f"{base}/dashboard", 0.1)
    text = "SPOON-MOULD"
    begun = time.monotonic()
    stage.fill("#m-q", text)

    assert time.monotonic() - begun >= len(text) / 12
    assert stage.page.input_value("#m-q") == text


def test_a_say_pointing_at_something_not_on_the_page_refuses_the_take_with_its_step_number(stage):
    stage, base = stage
    for _ in range(3):
        stage.begin({"part": None})
    stage.goto(f"{base}/dashboard", 0.1)

    with pytest.raises(SystemExit, match=r"step 3: the `say` points `at = '#no-such-thing'`"):
        stage.say("This is the thing.", 0.1, at="#no-such-thing")
    assert stage.page.locator("#fs-scene-callout").count() == 0, "a box was drawn for nothing"


def test_the_box_stays_through_a_mark_after_its_line_and_is_gone_before_the_next_step(stage):
    stage, base = stage
    stage.goto(f"{base}/dashboard", 0.1)
    stage.say("Plant OEE.", 0.1, at="#kpi-oee", label="Plant OEE")

    stage.begin({"do": "mark"})
    assert stage.page.locator("#fs-scene-callout").count() == 1, "a still taken after the line has no box"

    stage.begin({"do": "click"})
    assert stage.page.locator("#fs-scene-callout").count() == 0
    assert stage.page.locator("#fs-scene-callout-label").count() == 0


#: Is the point at the middle of the element's top band its own, or does
#: something drawn over it (the sticky header) answer instead?
SEEN = """(sel) => {
    const el = document.querySelector(sel);
    const r = el.getBoundingClientRect();
    const x = r.left + r.width / 2;
    return [r.top + 2, r.top + r.height / 2].map((y) => el.contains(document.elementFromPoint(x, y)));
}"""


def test_a_say_at_a_thing_under_the_sticky_header_scrolls_it_out_and_its_label_clear(stage):
    stage, base = stage
    stage.goto(f"{base}/dashboard", 0.1)
    page = stage.page
    page.evaluate("""() => {
        const r = document.querySelector('#m-q').getBoundingClientRect();
        window.scrollBy({top: r.top - 20, behavior: 'instant'});
    }""")
    assert page.evaluate(SEEN, "#m-q") == [False, False], "the field is not under the header to begin with"

    stage.say("Type part of a machine's name.", 0.1, at="#m-q", label="Filter")

    assert page.evaluate(SEEN, "#m-q") == [True, True], "the boxed field is still under the header"
    header = page.evaluate("() => document.querySelector('header').getBoundingClientRect().bottom")
    tab = page.evaluate("() => document.getElementById('fs-scene-callout-label').getBoundingClientRect().top")
    assert tab >= header, f"the label's top {tab} is on the header, which ends at {header}"
    assert stage.marks[-1]["box"]["y"] >= header, stage.marks[-1]


def test_the_label_of_a_box_that_fills_the_frame_is_drawn_inside_it_where_it_can_be_read(stage):
    stage, base = stage
    stage.goto(f"{base}/dashboard", 0.1)
    tab = stage.page.evaluate("""() => {
        window.__sceneCallout(document.body, 'The whole page', 'box');
        const r = document.getElementById('fs-scene-callout-label').getBoundingClientRect();
        return {top: r.top, bottom: r.bottom, left: r.left, high: innerHeight};
    }""")

    assert tab["top"] >= 0 and tab["bottom"] <= tab["high"] - 1, f"the label is off the frame: {tab}"


def test_a_thing_the_page_redraws_while_it_is_being_brought_into_view_is_found_again_and_boxed(stage):
    """The floor redraws its cards on a timer; class 0.2's watched box was drawn round a card it had just replaced.

    Here the plant OEE tile replaces itself with a fresh copy the second time
    anything asks where it is: once the recorder holds it and is bringing it
    out from under the header. The box must be round the copy on the page,
    not a zero-sized box at the foot of the document.
    """
    stage, base = stage
    stage.goto(f"{base}/dashboard", 0.1)
    stage.page.evaluate("""() => {
        const tile = document.getElementById('kpi-oee');
        const own = tile.getBoundingClientRect.bind(tile);
        let asked = 0;
        tile.getBoundingClientRect = () => {
            asked += 1;
            if (asked === 2) tile.replaceWith(tile.cloneNode(true));
            return own();
        };
    }""")

    stage.say("Plant OEE.", 0.1, at="#kpi-oee")

    box = stage.marks[-1]["box"]
    tile = stage.page.evaluate("() => document.getElementById('kpi-oee').getBoundingClientRect().toJSON()")
    assert box["w"] > 0 and box["h"] > 0, box
    assert abs(box["x"] - tile["left"]) <= 12 and abs(box["y"] - tile["top"]) <= 12, (box, tile)
