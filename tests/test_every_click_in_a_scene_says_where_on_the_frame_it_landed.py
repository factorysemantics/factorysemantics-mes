"""Every click in a scene's marks.json says which pixel of the frame it landed on.

Whoever lays a click effect over a clip needs to know where to put it, and a
mark that says only `click #no-cancel` at 1.73 s does not say. The runner knows:
the viewport is the video's frame, and the element it clicks has a box. These
tests film a tiny scene — buttons near the top, one below the fold that has to
be scrolled to, one sitting under a fixed header when the click is sent, and a
link that leaves the page — against a stand-in for a
plant served from this test, and read marks.json's content back. The page
reports, by a plain GET, the `clientX`/`clientY` of every click it really
received, so the marks' `press` is checked against where the click landed,
not against the runner's own arithmetic.

A click effect is drawn on the frames, and the frames show where the clicked
thing is once the page has gone still — which is not the press point when the
page moves after the press (the orders page does, when it draws the order a
row opened). So `x`, `y` is that settled place, `press` keeps the press, and
`moved` says when the two differ. One button here scrolls the page 140 px
after it is pressed; one takes itself away; one link leaves the page.

No plant and no ffmpeg are needed: `record()` films with Chromium alone, and
the GIF whose size marks.json reports is written by hand here, because the GIF
size is read from the file's own header.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"

REPORT = "<script>addEventListener('click', (e) => fetch(`/clicked?x=${e.clientX}&y=${e.clientY}`), true)</script>"
BUTTONS = (
    "<!doctype html><body style='margin:0'>" + REPORT
    + "<button id='near' style='position:absolute;left:100px;top:80px;width:120px;height:40px'>near</button>"
    + "<button id='right' style='position:absolute;left:1100px;top:300px;width:61px;height:31px'>right</button>"
    + "<button id='far' style='position:absolute;left:600px;top:1800px;width:200px;height:60px'>far</button>"
    + "<button id='covered' style='position:absolute;left:300px;top:1400px;width:100px;height:30px'>covered</button>"
    + "<button id='jumps' style='position:absolute;left:500px;top:500px;width:100px;height:40px'"
    + " onclick='setTimeout(() => window.scrollBy(0, 140), 80)'>jumps</button>"
    + "<button id='vanish' style='position:absolute;left:800px;top:400px;width:60px;height:20px'"
    + " onclick='this.remove()'>vanish</button>"
    + "<header style='position:fixed;left:0;right:0;top:0;height:60px;background:#222;z-index:5'>header</header>"
    + "<a id='away' href='/dashboard/other' style='position:absolute;left:400px;top:2100px'>away</a>"
    + "<div style='height:2600px'></div>"
)
OTHER = (
    "<!doctype html><body style='margin:0'>" + REPORT
    + "<button id='there' style='position:absolute;left:640px;top:360px;width:80px;height:20px'>there</button>"
)

SCENE = """
name = "clicks"
budget_seconds = 45
base = "{base}"

[[step]]
do = "goto"
url = "/dashboard/buttons"
settle = 0.3

[[step]]
do = "click"
selector = "#near"

[[step]]
do = "click"
selector = "#right"

# A page that moves after the press, as the orders page does when it draws the
# order a row opened: the frames show the button 140 px higher than it was pressed.
[[step]]
do = "click"
selector = "#jumps"

[[step]]
do = "click"
selector = "#vanish"

[[step]]
do = "click"
selector = "#far"

# Put the button under the fixed header, as a sticky header covers a button on
# the orders page: Playwright scrolls again before it clicks, so a point
# measured before the click would be a point on the header.
[[step]]
do = "click"
selector = "#covered"
scroll_to = "#covered"
scroll_block = "start"

[[step]]
do = "click"
selector = "#away"

[[step]]
do = "wait_for"
js = "!!document.querySelector('#there')"

[[step]]
do = "click"
selector = "#there"
"""


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("record_scene", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def plant():
    """A stand-in for a plant: health, a version, a sign-in, two pages, and an ear for clicks."""
    clicks: list[tuple[int, int]] = []

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
            path = urllib.parse.urlsplit(self.path)
            if path.path == "/health":
                self._send(json.dumps({"plant": "stand-in"}), "application/json")
            elif path.path == "/openapi.json":
                self._send(json.dumps({"info": {"version": "0.0.0"}}), "application/json")
            elif path.path == "/clicked":
                asked = urllib.parse.parse_qs(path.query)
                clicks.append((round(float(asked["x"][0])), round(float(asked["y"][0]))))
                self._send("", "text/plain")
            elif path.path == "/dashboard/other":
                self._send(OTHER)
            elif path.path.startswith("/dashboard"):
                self._send(BUTTONS)
            else:
                self.send_error(404)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._send("{}", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Plant)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", clicks
    server.shutdown()
    server.server_close()


def _a_gif(path: Path, width: int, height: int) -> Path:
    """The smallest well-formed GIF of a given size: header, screen, one blank image, trailer."""
    path.write_bytes(
        b"GIF89a" + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\x00\x00\x00"
        + b"\x2c" + b"\x00\x00\x00\x00" + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\x00"
        + b"\x02\x02\x44\x01\x00" + b"\x3b"
    )
    return path


def test_every_click_mark_carries_the_pixel_of_the_frame_it_landed_on(runner, plant, tmp_path):
    base, clicks = plant
    scene_file = tmp_path / "clicks.toml"
    scene_file.write_text(SCENE.format(base=base), encoding="utf-8")
    scene = runner.load_scene(scene_file)

    made = runner.record(scene, tmp_path / "take")
    marks = runner.marks_document(scene, made, _a_gif(tmp_path / "clicks.gif", 1000, 563), 1.0)
    (tmp_path / "marks.json").write_text(json.dumps(marks), encoding="utf-8")
    written = json.loads((tmp_path / "marks.json").read_text(encoding="utf-8"))

    assert written["frame"] == {"width": 1280, "height": 720}, "the frame is the viewport the scene filmed in"
    assert written["gif"] == {"width": 1000, "height": 563}, "read off the GIF beside it"
    for key in ("planned_seconds", "took_seconds", "gif_seconds", "marks"):
        assert key in written, f"{key} is read by studio and stays"

    clicked = {mark["what"].removeprefix("click "): mark
               for mark in written["marks"] if mark["what"].startswith("click")}
    order = ["#near", "#right", "#jumps", "#vanish", "#far", "#covered", "#away", "#there"]
    assert list(clicked) == order, f"{len(clicked)} clicks marked of the {len(order)} in the scene"
    for mark in clicked.values():
        assert isinstance(mark["x"], int) and isinstance(mark["y"], int), mark
        assert 0 <= mark["x"] < 1280 and 0 <= mark["y"] < 720, f"outside the frame: {mark}"
        assert isinstance(mark["at"], float), "the time is measured as it always was"

    # Where the page itself says each press landed, in the order they were sent,
    # is the mark's `press`; and where nothing moved afterwards, its `x`, `y` too.
    assert len(clicks) == len(order), f"the page heard {len(clicks)} clicks"
    print("\n".join(f"{name:10s} x,y {mark['x']},{mark['y']}  press {mark['press']}  heard {x},{y}  "
                    f"moved {mark.get('moved')}  {mark.get('settled', '')}"
                    for (name, mark), (x, y) in zip(clicked.items(), clicks, strict=True)))
    for (name, mark), (x, y) in zip(clicked.items(), clicks, strict=True):
        press = mark["press"]
        assert abs(press["x"] - x) <= 1 and abs(press["y"] - y) <= 1, (
            f"{name}: press marked at {press}, landed at {x},{y}"
        )
        if name != "#jumps":
            assert abs(mark["x"] - x) <= 1 and abs(mark["y"] - y) <= 1, (
                f"{name}: marked at {mark['x']},{mark['y']}, landed at {x},{y}"
            )

    # The two that did not need scrolling sit exactly where the page put them, and did not move.
    assert (clicked["#near"]["x"], clicked["#near"]["y"]) == (160, 100)
    assert (clicked["#right"]["x"], clicked["#right"]["y"]) == (1130, 315)
    assert clicked["#near"]["moved"] is False and clicked["#right"]["moved"] is False
    # The one the header covered was clicked below the header, where the button had moved to.
    assert clicked["#covered"]["x"] == 350 and clicked["#covered"]["y"] > 60, clicked["#covered"]
    assert clicked["#covered"]["moved"] is False

    # The page scrolled 140 px after the press: x, y is where the frames show
    # the button once the page is still, and the press is kept beside it.
    jumps = clicked["#jumps"]
    assert jumps["press"] == {"x": 550, "y": 520}, jumps
    assert (jumps["x"], jumps["y"]) == (550, 380), jumps
    assert jumps["moved"] is True
    assert "settled" not in jumps, "it went still, so its place was measured, not given up on"

    # A button that takes itself away, and a link that leaves the page: the press is all there is.
    for name in ("#vanish", "#away"):
        gone = clicked[name]
        assert gone["settled"] == "element gone", gone
        assert (gone["x"], gone["y"]) == (gone["press"]["x"], gone["press"]["y"]), gone
        assert "moved" not in gone, "nothing to compare the press with"


def test_a_file_that_is_not_a_gif_is_not_measured_as_one(runner, tmp_path):
    fake = tmp_path / "not.gif"
    fake.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    with pytest.raises(SystemExit):
        runner.gif_size(fake)


def test_a_click_that_was_neither_heard_nor_measured_is_given_no_place(runner):
    nothing = runner.click_position(None, {"gone": True})
    assert (nothing["x"], nothing["y"], nothing["press"]) == (None, None, None)
    assert nothing["settled"] == "element gone"
    restless = runner.click_position((10, 10), {"x": 10, "y": 300, "still": False})
    assert (restless["x"], restless["y"], restless["moved"]) == (10, 300, True)
    assert restless["settled"].startswith("still moving"), "a place that never settled says so"
