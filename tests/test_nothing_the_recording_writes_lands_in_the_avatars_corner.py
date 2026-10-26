"""Nothing the recording writes on the frame lands in the avatar's corner.

The bottom-right 240 by 200 pixels of every lesson's video are the narrator's:
studio sets a round avatar there afterwards, and a caption, the pre-alpha badge
or a word of a card underneath it would be cut off for the viewer. So the badge
sits at the top right, the caption bar stops 260 px short of the right-hand
edge, and a card keeps its writing in a box well left of the corner.

These tests look at pixels, not at the stylesheet. A stand-in plant serves a
page of one plain colour; a take with a long caption, a card, a scroll and a
click is filmed against it with the real `record()`; and every frame of the
video it made is cropped to the corner and must be one plain colour too — any
letter, border or bar there makes it not. The same page with the bar
stretched to the edge must fail the same check, or the check is not looking.
The recorder's own check, which asks the browser where the boxes are while
the take runs, must refuse that take as well.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
WIDTH, HEIGHT = 1280, 720

#: A caption long enough to wrap to two lines, which is when a bar too wide would show it.
LONG_LINE = (
    "The operator takes the hold under review, on the record, and the chart keeps drawing every reading the "
    "filler sends while somebody decides what happens to the bottles already filled."
)
PAGE = (
    "<!doctype html><body style='margin:0;background:#101418;color:#e8e8e8;font:20px sans-serif'>"
    "<p style='position:absolute;left:40px;top:120px'>a plant page, with nothing of its own in the corner</p>"
    "<button id='go' style='position:absolute;left:200px;top:300px;width:120px;height:40px'>go</button>"
    "<p id='row' style='position:absolute;left:40px;top:1500px'>row 1500</p>"
    "<div style='height:2400px'></div>"
)
SCENE = """
name = "corner"
budget_seconds = 45
base = "{base}"
part = "hook"

[[step]]
do = "goto"
url = "/dashboard/page"
settle = 0.3

[[step]]
do = "say"
line = "{line}"
hold = 0.6

[[step]]
do = "slide"
part = "idea"
name = "the word"
title = "Statistical process control"
lines = ["A chart of every reading, and rules that fire when the line drifts.",
         "The limits come from the line itself, not from a specification someone typed.",
         "When a rule fires, the plant raises a hold; a person decides."]
hold = 0.6

[[step]]
do = "goto"
part = "screen"
url = "/dashboard/page"
settle = 0.3

[[step]]
do = "click"
selector = "#go"

[[step]]
do = "mark"
name = "row"
scroll_to = "#row"
scroll_block = "start"
"""


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("record_scene_corner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def plant():
    """A stand-in for a plant: health, a version, a sign-in, and one plain page."""

    class Plant(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def _send(self, body: str, kind: str = "text/html") -> None:
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/health":
                self._send(json.dumps({"plant": "stand-in"}), "application/json")
            elif path == "/openapi.json":
                self._send(json.dumps({"info": {"version": "0.0.0"}}), "application/json")
            elif path.startswith("/dashboard"):
                self._send(PAGE)
            else:
                self.send_error(404)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._send("{}", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Plant)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def a_scene(runner, tmp_path: Path, base: str) -> dict:
    path = tmp_path / "corner.toml"
    path.write_text(SCENE.format(base=base, line=LONG_LINE), encoding="utf-8")
    return runner.load_scene(path)


def corner_frames(runner, film: Path, every: int = 1) -> list[bytes]:
    """The avatar's corner of every `every`th frame of a video or a picture, as raw RGB."""
    w, h = runner.AVATAR_WIDTH, runner.AVATAR_HEIGHT
    corner = f"crop={w}:{h}:{WIDTH - w}:{HEIGHT - h}"
    pick = f"select='not(mod(n\\,{every}))'," if every > 1 else ""
    done = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(film), "-vf", f"{pick}{corner}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    )
    size = runner.AVATAR_WIDTH * runner.AVATAR_HEIGHT * 3
    return [done.stdout[at:at + size] for at in range(0, len(done.stdout) - size + 1, size)]


#: How far a pixel of a plain corner may stray from the corner's own colour:
#: a video codec smears a flat colour by a few levels; a letter is a hundred off.
PLAIN_WITHIN = 24


def marks_in_the_corner(frame: bytes) -> int:
    """How many pixels of one corner differ from the corner's commonest colour."""
    pixels = [frame[at:at + 3] for at in range(0, len(frame), 3)]
    usual = max(set(pixels[:: 97]), key=pixels[:: 97].count)
    return sum(1 for pixel in pixels if max(abs(a - b) for a, b in zip(pixel, usual, strict=True)) > PLAIN_WITHIN)


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg on this machine")


@needs_ffmpeg
def test_no_frame_of_a_take_has_a_caption_a_badge_or_a_card_in_the_avatars_corner(runner, plant, tmp_path):
    scene = a_scene(runner, tmp_path, plant)
    made = runner.record(scene, tmp_path / "take")

    frames = corner_frames(runner, made["video"])
    assert len(frames) > 20, "the take is long enough to have frames worth looking at"
    dirty = {index: count for index, frame in enumerate(frames) if (count := marks_in_the_corner(frame))}
    assert dirty == {}, f"{len(dirty)} of {len(frames)} frames have marks in the avatar's corner"

    # Every mark says what kind it is and which part of the lesson it is in.
    marks = made["marks"]
    assert all("kind" in mark and "part" in mark for mark in marks)
    kinds = [mark["kind"] for mark in marks]
    assert kinds == ["goto", "say", "slide", "goto", "click", "scroll", "mark", "end"]
    assert [mark["part"] for mark in marks] == ["hook", "hook", "idea", "screen", "screen", "screen", "screen", None]
    say = marks[1]
    assert (say["line"], say["hold"]) == (LONG_LINE, 0.6)
    slide = marks[2]
    assert slide["image"] == "slides/01-the-word.png"
    assert (tmp_path / "take" / slide["image"]).stat().st_size > 0
    assert made["slides"] == ["slides/01-the-word.png"]
    scroll = marks[5]
    assert scroll["from_y"] < scroll["to_y"]
    assert scroll["took"] >= 0
    # The card as kept for studio has nothing in the corner either, and no badge.
    assert marks_in_the_corner(corner_frames(runner, tmp_path / "take" / slide["image"])[0]) == 0


def test_a_caption_bar_stretched_into_the_corner_fails_the_take(runner, plant, tmp_path, monkeypatch):
    """The recorder's own check, run as the take is filmed, is what stops a bad take being kept."""
    monkeypatch.setattr(runner, "CAPTION_RIGHT_INSET", 0)
    scene = a_scene(runner, tmp_path, plant)

    with pytest.raises(SystemExit, match="avatar's corner"):
        runner.record(scene, tmp_path / "take")


@needs_ffmpeg
@pytest.mark.parametrize("inset", ["as shipped", 0])
def test_the_pixel_check_sees_a_bar_in_the_corner_and_none_when_it_stops_short(runner, plant, tmp_path,
                                                                                 monkeypatch, inset):
    """The check on the frames is worth only what it can see: the bar stretched to the edge must show."""
    from playwright.sync_api import sync_playwright

    if inset == 0:
        monkeypatch.setattr(runner, "CAPTION_RIGHT_INSET", 0)
    picture = tmp_path / "frame.png"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.add_init_script(runner.overlay_script("pre-alpha · simulated plant · v0.0.0"))
        page.goto(f"{plant}/dashboard/page", wait_until="load")
        page.evaluate("text => window.__sceneSay(text)", LONG_LINE)
        page.wait_for_function("t => document.getElementById('fs-scene-cap').textContent === t", arg=LONG_LINE)
        page.screenshot(path=str(picture))
        browser.close()

    found = marks_in_the_corner(corner_frames(runner, picture)[0])
    if inset == 0:
        assert found > 1000, "a caption bar across the corner is thousands of pixels"
    else:
        assert found == 0
