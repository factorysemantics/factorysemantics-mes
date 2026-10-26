"""Film one scene of a running plant from a scene file, and write the folder.

    python scripts/record-scene.py scripts/scenes/02-spc-on-the-line.toml
    python scripts/record-scene.py <scene.toml> --base http://127.0.0.1:8110 --out /tmp/take
    python scripts/record-scene.py --course      # course.json and the tree's README only

A scene file is a list of steps — `goto`, `click`, `wait_for`, `say`, `hold`,
`mark`, `slide` — against a plant that is already running. This file drives a browser
through them, burns the overlay into the picture, and writes, into one folder:

  * `<name>.gif`, the published loop, under the budget the scene states and
    under the 10 MB GitHub will animate
  * `<name>.mp4`, the same take at full length and full rate
  * `stills/`, one screenshot per `mark`
  * `<name>.script.txt`, the overlay lines in the order they were said, one per
    line and nothing else, for whoever narrates
  * `README.md`, naming the plant, its version, the commit this script came
    from, and which two things on screen are this file's rather than the
    product's
  * `marks.json`, the seconds each mark landed at and the planned timings, the
    size of the frame and of the GIF, and for every click the pixel on the
    frame it landed on, so an effect laid over the clip can be put there

A scene that names a `lesson` ("2.1") is filmed into the class's tree instead,
as `scripts/course/course.toml` lays it out and the lesson hand-off contract
describes: `<course>/<NN-module>/<NN-lesson>/` with `lesson.json` beside the
files above, and that lesson's `take`, `status`, `duration_seconds` and
`recorded` brought up to date in the `course.json` at the top — and nothing
else in that file, and nothing outside the lesson's folder. `--course` writes
`course.json` and the tree's README from `course.toml` alone, filming nothing.

Every folder is written whole into a temporary folder beside it and moved
into place last, so a reader finds a lesson complete or not at all.

It is the generalisation of `scripts/record-kill-a-node.py`, which films one
story and brings up its own two-node plant to film it.

By default a scene films a plant somebody else started and **changes nothing**:
any request the page makes that is not a GET (the one sign-in aside) fails the
take rather than being filmed, because the plants most scenes are shot against
are not ours to change, and a take that needed the lab plant restarted would
not be a recording of the lab plant.

Two things a scene may say to get out of that, and only together:

  * `[plant]` — bring up a plant of this take's own from a pack, on the ports
    that pack names, in a temporary home, and stop it and delete it when the
    take ends. `[plant] roll_when_*` holds the camera, off camera, until the
    plant reaches the state the scene opens on — a chart in a given state, or
    `[[plant.roll_when_order]]`, orders that have finished, drawn on a lot, or booked a count —
    because a wait inside a scene is filmed, and a clip has forty-five seconds.
  * `allow_writes` — let the page write. Some stories *are* writes: a hold is
    taken under review by somebody deciding to, and a clip of quality work
    with nothing written in it is a tour. This is honoured only when `base` is
    a loopback address and refused everywhere else, before the browser opens,
    because the lab fleet and the public demo answer on this box too.

Two things on screen are this file's and not the product's — three, when a
scene starts its own plant — and the README of every folder it writes says so:

  1. the caption bar along the bottom — the `say` lines, burnt in — which stops
     short of the bottom-right corner (`captions = false` leaves it out)
  2. the badge at the top right, which names the version the plant itself
     reports and says in as many words that the plant is simulated and the
     software is pre-alpha

The bottom-right corner, 240 by 200 pixels, is kept empty of both: the class's
narrator is drawn there afterwards, and nothing a viewer must read may sit
under it. The take is checked for that as it is filmed. It also hides the
floating Assistant and Design buttons, which are the product's and sit in that
corner.

It is a developer tool. It is not in the wheel, not in CI, and not asked to
run on Windows. It needs `ffmpeg`, and a Python with Playwright and its
Chromium (`pip install -e ".[dev]" && playwright install chromium`).
"""

from __future__ import annotations

import argparse
import datetime
import html
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# The seven words a step can be. An eighth is a newer scene file than this
# script, and guessing what it meant is how a recording comes out missing the
# thing it was made to show.
VERBS = ("goto", "click", "wait_for", "say", "hold", "mark", "slide")

#: What each verb is called in marks.json's `kind`, as the lesson contract names
#: them. A scroll is a step's `scroll_to` rather than a verb, and is `scroll`.
KINDS = {"goto": "goto", "click": "click", "wait_for": "wait", "say": "say",
         "hold": "hold", "mark": "mark", "slide": "slide"}

#: The five parts of a lesson, in the order a lesson runs through them. A step
#: is in the part it names, or else in the part of the step before it.
PARTS = ("hook", "idea", "screen", "refuses", "know")

# The overlay's own geometry, in the viewport's pixels. The badge check reads
# these: it samples a strip of the badge's right-hand padding, where there is
# never any text, in every frame of the finished GIF and mp4.
#
# The badge sits at the top right. It rode at the right-hand end of the
# caption bar until the class reserved the bottom-right corner for its
# narrator's avatar (the lesson contract, 2026-10-26): that corner must be
# empty of anything a viewer must read. Four pixels from the top it clears the
# second line of the replay banner a sped-up plant shows; on a plant without
# that banner it sits over the right-hand end of the product's header.
BADGE_BACKGROUND = (255, 210, 63)  # #ffd23f, the series' yellow
BADGE_TOP = 4
BADGE_RIGHT = 16
BADGE_HEIGHT = 34
BADGE_SAMPLE_INSET = 6  # from the badge's right edge
BADGE_SAMPLE_SIZE = 10  # the square sampled, px
# A flat colour survives GIF quantisation closely but not exactly, and a frame
# is 128 colours chosen from the whole take. 32 per channel is loose enough for
# the palette and far tighter than the gap to any page background.
BADGE_TOLERANCE = 32

# The avatar's corner: bottom right, this wide and this high. The narrator is a
# 190 px round badge inside it, 25 px from the right and 5 px up, laid over the
# finished video by whoever voices it; the box is what the recording keeps
# empty for it.
AVATAR_WIDTH = 240
AVATAR_HEIGHT = 200
# The caption bar along the bottom: this high for one line, and stopping this
# far short of the right-hand edge, so that it ends 20 px before the corner.
CAPTION_HEIGHT = 72
CAPTION_RIGHT_INSET = 260
#: The longest a lesson's teaser GIF may run.
TEASER_SECONDS = 45

# How long a caption stays up when the scene does not say: long enough for the
# narrator to read the line aloud over it. 2.5 words a second plus 0.7 s, and
# never under 2.5 s. Measured against the narrator on a real clip at 2.7 words
# a second, which leaves a little room. Change it here and every lesson is
# re-timed by its next take.
WORDS_PER_SECOND = 2.5
HOLD_PADDING_SECONDS = 0.7
HOLD_FLOOR_SECONDS = 2.5

# The cards of a lesson's "idea" part: text on one colour, painted by the
# recorder. The writing keeps out of the avatar's corner and above the caption
# bar, and a card whose writing does not fit fails the take rather than being
# cut off where nobody sees it was.
SLIDE_BACKGROUND = "#123a5a"
SLIDE_PATH = "/__scene/slide/"

# What the GIF may weigh and how it is made smaller until it does. GitHub
# renders an image up to 10 MB and refuses to animate one over it, and that is
# ten million bytes, not ten mebibytes: two clips of this series came in a hair
# over 10,000,000 under the old 10 * 1024 * 1024 and would have been refused
# by anything that counts in decimal. How long a clip may run is a judgement
# rather than a limit, and it is the scene file's own `budget_seconds`.
GIF_LIMIT_BYTES = 10_000_000
# Width comes down a notch before the frame rate does: a caption a little
# smaller reads the same, and a click effect laid over the clip lands on fewer
# frames as the rate drops.
GIF_SETTINGS = ((10, 1100), (10, 1000), (8, 1000), (6, 1000), (6, 900), (5, 800))

DEFAULTS = {
    "base": "http://127.0.0.1:8010",
    "badge": "pre-alpha · simulated plant · v{version}",
    "budget_seconds": 45.0,
    "gif_skip_seconds": 2.0,
    "width": 1280,
    "height": 720,
    "user": "ADMIN",
    "password": "admin",
    "allow_writes": False,
    "captions": True,
    "part": None,
    "course": "scripts/course/course.toml",
    "take_is": "lesson",
}

# Per-step defaults, in seconds. `settle` is how long a page is left to draw
# and fill itself after a `goto`; `hold` is how long a caption stays up. Both
# are generous: a reader has to find the sentence in a thumbnail, read it, and
# look at what it points to, before it changes.
SETTLE_SECONDS = 3.0
HOLD_SECONDS = 4.0  # a `hold` step's; a `say` without a hold is timed from its words
WAIT_TIMEOUT_SECONDS = 20.0
# A click, a still and a scroll are not instant on a page that is drawing, and
# the planned length has to include them or a scene that fits on paper runs over.
SMALL_STEP_SECONDS = 0.4
SCROLL_SECONDS = 0.5


# --------------------------------------------------------------- the scene


def load_scene(path: Path) -> dict:
    """Read a scene file and refuse anything this script would have to guess at.

    Returns the scene with every default filled in, so nothing downstream has
    to know what the defaults are.
    """
    with path.open("rb") as raw:
        scene = tomllib.load(raw)

    if not scene.get("name"):
        raise SystemExit(f"{path}: a scene needs a `name` — it names every file in the folder")
    steps = scene.get("step") or []
    if not steps:
        raise SystemExit(f"{path}: a scene needs at least one [[step]]")

    for key, value in DEFAULTS.items():
        scene.setdefault(key, value)
    scene["out"] = scene.get("out") or f"recordings/{scene['name']}"
    scene["title"] = scene.get("title") or scene["name"]
    scene["notes"] = list(scene.get("notes") or [])

    if scene["part"] is not None and scene["part"] not in PARTS:
        raise SystemExit(f"{path}: `part = {scene['part']!r}` is not one of {', '.join(PARTS)}")
    if scene.get("lesson") is not None:
        if not re.fullmatch(r"[1-9][0-9]?\.[1-9][0-9]?", str(scene["lesson"])):
            raise SystemExit(f"{path}: `lesson` is module.lesson as the syllabus numbers it, e.g. \"2.1\"")
        scene["lesson"] = str(scene["lesson"])
    if scene["take_is"] not in ("lesson", "teaser"):
        raise SystemExit(f"{path}: `take_is` is \"lesson\" or \"teaser\"")

    part = scene["part"]
    for number, step in enumerate(steps, start=1):
        where = f"{path}: step {number}"
        verb = step.get("do")
        if step.get("part") is not None and step["part"] not in PARTS:
            raise SystemExit(f"{where}: `part = {step['part']!r}` is not one of {', '.join(PARTS)}")
        part = step.get("part") or part
        step["part"] = part
        if verb not in VERBS:
            raise SystemExit(f"{where}: `do = {verb!r}` is not one of {', '.join(VERBS)}")
        if verb == "goto":
            if not step.get("url"):
                raise SystemExit(f"{where}: a `goto` needs a `url`")
            step.setdefault("settle", SETTLE_SECONDS)
        if verb == "click" and not step.get("selector"):
            raise SystemExit(f"{where}: a `click` needs a `selector`")
        if verb == "say":
            if not step.get("line"):
                raise SystemExit(f"{where}: a `say` needs a `line` — it is the sentence on screen")
            step.setdefault("hold", hold_for_words(step["line"]))
        if verb == "slide":
            if not step.get("name") or not step.get("title"):
                raise SystemExit(f"{where}: a `slide` needs a `name` (it names the card's file) and a `title`")
            step["lines"] = [str(line) for line in step.get("lines") or []]
            step.setdefault("hold", hold_for_words(" ".join([step["title"], *step["lines"]])))
        if verb == "hold":
            step.setdefault("seconds", HOLD_SECONDS)
        if verb == "mark" and not step.get("name"):
            raise SystemExit(f"{where}: a `mark` needs a `name` — it names the still")
        if verb == "wait_for":
            if not step.get("selector") and not step.get("js"):
                raise SystemExit(f"{where}: a `wait_for` needs a `selector` or a `js`")
            if step.get("selector") and not any(step.get(k) for k in ("text", "attribute", "js")):
                # House rule 6: a browser step waits for the state it is about
                # to film, never for the element that will hold it. A page on
                # loopback draws in six milliseconds and a page over a network
                # does not, and a caption written over an element that is still
                # empty is the recording telling a lie about the product.
                raise SystemExit(
                    f"{where}: a `wait_for` on a selector needs `text`, or `attribute` with `equals` — "
                    "waiting for the element itself films an empty one"
                )
            if step.get("attribute") and step.get("equals") is None:
                raise SystemExit(f"{where}: `attribute` needs `equals`")
            step.setdefault("timeout", WAIT_TIMEOUT_SECONDS)

    if scene.get("plant"):
        scene["plant"] = plant_defaults(scene["plant"])

    scene["step"] = steps
    return scene


def hold_for_words(line: str) -> float:
    """How long a caption stays up so the narrator can read it: from its words alone."""
    # A dash or a lone symbol is not a word Brian says.
    words = sum(1 for token in line.split() if any(ch.isalnum() for ch in token))
    return max(HOLD_FLOOR_SECONDS, round(words / WORDS_PER_SECOND + HOLD_PADDING_SECONDS, 1))


def script_lines(scene: dict) -> list[str]:
    """The overlay lines, in order. This is the script file's whole content."""
    return [step["line"] for step in scene["step"] if step["do"] == "say"]


def planned_seconds(scene: dict) -> float:
    """How long the take is meant to run, from the scene file alone.

    `wait_for` counts as nothing: on a plant in the state the scene was written
    for, the state is already there. What it actually waited is in marks.json,
    and the finished GIF is measured rather than trusted.
    """
    total = 0.0
    for step in scene["step"]:
        if step.get("scroll_to"):
            total += SCROLL_SECONDS
        if step["do"] == "goto":
            total += float(step["settle"])
        elif step["do"] == "say":
            total += float(step["hold"])
        elif step["do"] == "hold":
            total += float(step["seconds"])
        elif step["do"] == "slide":
            total += float(step["hold"]) + SMALL_STEP_SECONDS
        else:
            total += SMALL_STEP_SECONDS
    return round(total, 2)


def gif_seconds(scene: dict) -> float:
    """The planned length of the published loop: the take, less the head it skips."""
    return round(planned_seconds(scene) - float(scene["gif_skip_seconds"]), 2)


def play(scene: dict, stage) -> None:
    """Walk the steps, handing each to the stage. The one place order lives.

    The stage is anything with these methods, which is what lets the test
    run a real scene file without a browser in front of it. A stage that wants
    to know which step it is in (the browser's, for each mark's `part`) has a
    `begin`.
    """
    for step in scene["step"]:
        if hasattr(stage, "begin"):
            stage.begin(step)
        if step.get("scroll_to"):
            stage.scroll_to(step["scroll_to"], step.get("scroll_block", "center"), int(step.get("scroll_offset", 0)),
                            step.get("scroll_text"))
        verb = step["do"]
        if verb == "goto":
            stage.goto(step["url"], float(step["settle"]))
        elif verb == "click":
            stage.click(step["selector"])
        elif verb == "wait_for":
            stage.wait_for(step, float(step["timeout"]))
        elif verb == "say":
            stage.say(step["line"], float(step["hold"]))
        elif verb == "hold":
            stage.hold(float(step["seconds"]))
        elif verb == "mark":
            stage.mark(step["name"])
        elif verb == "slide":
            stage.slide(step["name"], step["title"], step["lines"], float(step["hold"]))


# --------------------------------------------------------------- the overlay


def overlay_script(badge: str, captions: bool = True) -> str:
    """The caption bar, the badge, and the two buttons that are hidden.

    The bar stops `CAPTION_RIGHT_INSET` short of the right-hand edge and the
    badge sits at the top right, so the avatar's corner holds neither. With
    `captions` false the bar is not drawn at all: the lines still pace the take
    and still go to the script file and marks.json, for whoever sets their own
    type over the video.

    Three things here are the way they are because of what the camera saw.

    It is injected at document start and draws immediately, into
    `<html>` rather than `<body>`, because `<body>` does not exist yet at that
    point and a badge that waits for it misses the new page's first paints. The
    first take of this scene lost the badge for four frames while the quality
    page came up, and the frame check failed the take — which is what it is for.

    The caption is kept in `sessionStorage`, which both dashboards share
    because they are the same origin, so a sentence said before a `goto` is
    still on screen after it rather than blinking out mid-sentence.

    And the product's floating Assistant and Design buttons are hidden, because
    they sit exactly where the caption bar goes.
    """
    css = (
        f"#fs-scene-cap{{position:fixed!important;left:0!important;right:{CAPTION_RIGHT_INSET}px!important;"
        "bottom:0!important;box-sizing:border-box!important;"
        "z-index:2147483646!important;background:rgba(8,11,17,.94)!important;color:#fff!important;"
        "font:600 25px/1.3 system-ui,-apple-system,Segoe UI,sans-serif!important;"
        f"padding:18px 26px!important;min-height:{CAPTION_HEIGHT}px!important;letter-spacing:.1px!important;"
        "border-top:2px solid #ffd23f!important;border-top-right-radius:10px!important;"
        "text-align:left!important}"
        + ("" if captions else "#fs-scene-cap{display:none!important}")
        + f"#fs-scene-badge{{position:fixed!important;top:{BADGE_TOP}px!important;right:{BADGE_RIGHT}px!important;"
        f"height:{BADGE_HEIGHT}px!important;z-index:2147483647!important;background:#ffd23f!important;"
        "color:#111!important;font:700 17px/34px system-ui,-apple-system,Segoe UI,sans-serif!important;"
        "padding:0 18px!important;border-radius:8px!important;white-space:nowrap!important;"
        "box-shadow:0 2px 10px rgba(0,0,0,.5)!important}"
        ".assist-launch,.design-launch{display:none!important}"
    )
    return f"""
(() => {{
  const KEY = 'fsSceneCaption';
  const remembered = () => {{ try {{ return sessionStorage.getItem(KEY) || ''; }} catch (e) {{ return ''; }} }};
  const draw = () => {{
    const root = document.documentElement;
    if (!root || document.getElementById('fs-scene-badge')) return;
    const style = document.createElement('style');
    style.textContent = {json.dumps(css)};
    const badge = document.createElement('div');
    badge.id = 'fs-scene-badge';
    badge.textContent = {json.dumps(badge)};
    const cap = document.createElement('div');
    cap.id = 'fs-scene-cap';
    cap.textContent = remembered();
    root.append(style, badge, cap);
  }};
  window.__sceneSay = (text) => {{
    try {{ sessionStorage.setItem(KEY, text); }} catch (e) {{ /* a caption is not worth a failed take */ }}
    draw();
    const cap = document.getElementById('fs-scene-cap');
    if (cap) cap.textContent = text;
  }};
  // Drawn before a paint rather than after one. At document start there is
  // not always a <html> yet to hang the badge on, and waiting for
  // DOMContentLoaded let the quality page paint once without it — one frame in
  // 346, which the frame check failed the take over, rightly: that frame is
  // the one somebody screenshots. A requestAnimationFrame callback runs before
  // the paint it belongs to, and by then there is always an <html>.
  const ensure = () => {{
    draw();
    if (!document.getElementById('fs-scene-badge')) requestAnimationFrame(ensure);
  }};
  ensure();
  document.addEventListener('DOMContentLoaded', draw);
}})();
"""


class BrowserStage:
    """The page being filmed, and the clock every mark is measured against."""

    def __init__(self, page, out: Path, started: float, presses: list | None = None,
                 base: str = "", slides: dict | None = None, render=None) -> None:
        self.page = page
        #: Where a slide is served from: the plant's own origin, so the caption
        #: carries across in sessionStorage and the overlay draws on the card.
        self.base = base
        #: The cards to serve, by number; `record` routes SLIDE_PATH to these.
        self.slides: dict[str, str] = slides if slides is not None else {}
        #: Paints a card to a PNG without the overlay on it, for whoever redoes it.
        self.render = render
        #: Everything the corner check found in the avatar's corner, as it was filmed.
        self.corner_faults: list[str] = []
        self.part: str | None = None
        #: (x, y) of every press the page reported, in order; see `listen_for_presses`.
        self.presses: list[tuple[int, int]] = presses if presses is not None else []
        self.out = out
        self.started = started
        self.marks: list[dict] = []
        self.said: list[str] = []
        self.caption = ""

    def _at(self) -> float:
        return round(time.monotonic() - self.started, 2)

    def begin(self, step: dict) -> None:
        """The step about to be played: its part goes on every mark it makes."""
        self.part = step.get("part")

    def note(self, what: str, kind: str, at: float | None = None, **rest: object) -> None:
        mark = {"at": self._at() if at is None else at, "what": what, "kind": kind,
                "part": None if kind == "end" else self.part, **rest}
        self.marks.append(mark)
        print(f"{mark['at']:7.2f}  {what}", flush=True)

    def check_corner(self) -> None:
        """Is anything of the overlay's, or any word of a card, in the avatar's corner?

        Asked of the page as it is filmed, after every caption and card, from
        the boxes the browser laid out. What is found fails the take.
        """
        width, height = self.page.viewport_size["width"], self.page.viewport_size["height"]
        found = self.page.evaluate(CORNER_SCRIPT, [width, height, AVATAR_WIDTH, AVATAR_HEIGHT])
        for fault in found:
            what = f"at {self._at():.2f}s: {fault}"
            if what not in self.corner_faults:
                self.corner_faults.append(what)
                print(f"         CORNER: {fault}", flush=True)

    def goto(self, url: str, settle: float) -> None:
        # The caption carries itself across the navigation in sessionStorage,
        # drawn before the new page's first paint; nothing to put back here.
        self.page.goto(url, wait_until="load")
        self.note(f"goto {url}", "goto", url=url)
        self.page.wait_for_timeout(int(settle * 1000))

    def click(self, selector: str) -> None:
        # Where the click lands, on the frame, for whoever lays a click effect
        # over the clip. It is not predicted from the element's box: Playwright
        # scrolls again when something sits over the element — the sticky
        # header over the Release button, in scene 04 — and a point measured
        # before that is a point on the header. The page says where it was
        # pressed (`listen_for_presses`), in the viewport's pixels, which are
        # the mp4's: `record_video_size` is the viewport. The mark's `at` is
        # still the moment the click returned, as it always was.
        #
        # The press point is kept as `press`, but `x`, `y` is where the
        # clicked thing sits once the page has gone still after the click,
        # because that is what the frames show. On the orders page a row
        # pressed at the middle of the window (where Playwright scrolled it)
        # sits 140 px lower by the next frame, once the page has drawn the
        # order it opened; a click effect drawn at the press lands on the row
        # above. When the clicked thing is gone after the click (a button that
        # takes itself away, a link that leaves the page) the press is all
        # there is, and the mark says so.
        heard = len(self.presses)
        # The same element `page.click` will press: the first that matches.
        clicked = self.page.locator(selector).first.element_handle()
        self.page.click(selector)
        self.note(f"click {selector}", "click", x=None, y=None)
        mark = self.marks[-1]
        begun = time.monotonic()
        settled = settled_centre(self.page, clicked, selector)
        # The page's report arrives while Playwright is waiting on anything,
        # and this short wait was here before the report was.
        rest = SMALL_STEP_SECONDS - (time.monotonic() - begun)
        if rest > 0:
            self.page.wait_for_timeout(int(rest * 1000))
        press = self.presses[heard] if len(self.presses) > heard else None
        mark.update(click_position(press, settled))
        if mark["x"] is None:
            mark["what"] += " (where it landed was neither heard from the page nor measured, so it is not given)"
        print(f"         at {mark['x']},{mark['y']}  press {mark.get('press')}"
              + (f"  ({mark['settled']})" if "settled" in mark else ""), flush=True)

    def wait_for(self, step: dict, timeout: float) -> None:
        waited = time.monotonic()
        self.page.wait_for_function(wait_expression(step), timeout=timeout * 1000)
        self.note(f"waited {time.monotonic() - waited:.1f}s for {describe_wait(step)}", "wait")

    def say(self, line: str, hold: float) -> None:
        self.caption = line
        self.page.evaluate("text => window.__sceneSay(text)", line)
        self.said.append(line)
        # `at` is when the caption appears; the narrator starts the line then
        # and finishes it inside `hold`.
        self.note(f"say: {line}", "say", line=line, hold=hold)
        self.check_corner()
        self.page.wait_for_timeout(int(hold * 1000))

    def hold(self, seconds: float) -> None:
        self.note(f"hold {seconds}s", "hold")
        self.page.wait_for_timeout(int(seconds * 1000))

    def slide(self, name: str, title: str, lines: list[str], hold: float) -> None:
        """Put a card on screen: a page this script paints, served on the plant's origin.

        The card is a frame the recorder made, not a page of the product, and
        its mark says so with `image`, the card as painted without the overlay,
        so that whoever voices the lesson can set it again in their own type.
        The request for it never reaches the plant: `record` answers it.
        """
        number = len(self.slides) + 1
        card = slide_html(title, lines)
        self.slides[str(number)] = card
        image = f"slides/{number:02d}-{name.replace(' ', '-')}.png"
        if self.render is not None:
            (self.out / "slides").mkdir(parents=True, exist_ok=True)
            self.render(card, self.out / image)
        self.page.goto(f"{self.base}{SLIDE_PATH}{number}", wait_until="load")
        self.note(f"slide: {title}", "slide", image=image, title=title)
        self.check_corner()
        self.page.wait_for_timeout(int(hold * 1000))

    def mark(self, name: str) -> None:
        stills = self.out / "stills"
        stills.mkdir(parents=True, exist_ok=True)
        shot = stills / f"{len(list(stills.glob('*.png'))) + 1:02d}-{name.replace(' ', '-')}.png"
        self.page.screenshot(path=str(shot))
        self.note(f"mark: {name}", "mark", still=shot.name)

    def scroll_to(self, selector: str, block: str, offset: int, text: str | None = None) -> None:
        # `text` picks, among everything the selector matches, the first whose
        # words contain it: the row about the cap lot in a list of a hundred
        # issues, which sits inside a box that scrolls on its own. That row is
        # put at the top of its box, and then the page is scrolled to put the
        # row `offset` pixels from the top of the window — as near as the page
        # can, since a row near the foot of a short page cannot be lifted past
        # the page's end. (The plain nudge below would push such a row down,
        # under the caption bar, which is where the first takes left it.)
        begun = self._at()
        clock = time.monotonic()
        from_y = self.page.evaluate("() => Math.round(window.scrollY)")
        self.page.evaluate(
            """([selector, block, offset, text]) => {
                 const nodes = [...document.querySelectorAll(selector)];
                 const node = text
                   ? nodes.find((n) => (n.innerText || '').includes(text))
                   : nodes[0];
                 if (!node) throw new Error('nothing to scroll to: ' + selector + (text ? ' saying ' + text : ''));
                 if (!text) {
                   node.scrollIntoView({ block, behavior: 'instant' });
                   if (offset) window.scrollBy(0, -offset);
                   return;
                 }
                 for (let box = node.parentElement; box && box !== document.body; box = box.parentElement) {
                   if (/(auto|scroll)/.test(getComputedStyle(box).overflowY) && box.scrollHeight > box.clientHeight) {
                     box.scrollTop += node.getBoundingClientRect().top - box.getBoundingClientRect().top;
                     break;
                   }
                 }
                 window.scrollBy(0, node.getBoundingClientRect().top - offset);
               }""",
            [selector, block, offset, text],
        )
        self.page.wait_for_timeout(int(SCROLL_SECONDS * 1000))
        to_y = self.page.evaluate("() => Math.round(window.scrollY)")
        # Page pixels, the window's scroll: a box that scrolls inside the page
        # (the issues list on the trace page) moves without this changing.
        self.note(f"scroll to {selector}" + (f" saying {text!r}" if text else ""), "scroll", at=begun,
                  from_y=from_y, to_y=to_y, took=round(time.monotonic() - clock, 2))


#: What the corner check asks the page. The caption bar's box and the badge's,
#: and every line of writing on a card, against the avatar's corner; and a card
#: whose writing ran past its own box, which would be cut off unseen.
CORNER_SCRIPT = """
([width, height, cornerWidth, cornerHeight]) => {
  const corner = { left: width - cornerWidth, top: height - cornerHeight };
  const into = (r) => r.width > 0 && r.height > 0 && r.right > corner.left && r.bottom > corner.top
                      && r.left < width && r.top < height;
  const found = [];
  for (const id of ['fs-scene-cap', 'fs-scene-badge']) {
    const el = document.getElementById(id);
    if (!el || getComputedStyle(el).display === 'none') continue;
    const r = el.getBoundingClientRect();
    if (into(r)) found.push(`#${id} reaches ${Math.round(r.right)},${Math.round(r.bottom)}`);
  }
  const card = document.getElementById('fs-slide-body');
  if (card) {
    if (card.scrollHeight > card.clientHeight + 1 || card.scrollWidth > card.clientWidth + 1) {
      found.push('the card says more than fits on it');
    }
    const walk = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
    for (let node = walk.nextNode(); node; node = walk.nextNode()) {
      if (!node.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (into(r)) { found.push(`card text "${node.textContent.trim().slice(0, 40)}" in the corner`); break; }
      }
    }
  }
  return found;
}
"""


def slide_html(title: str, lines: list[str]) -> str:
    """A card for the "idea" part: a title and a few lines, on one colour.

    The writing is kept in a box that ends well left of the avatar's corner and
    above the caption bar; what does not fit in it is reported by the corner
    check, not cut off quietly.
    """
    items = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    font = "system-ui,-apple-system,Segoe UI,sans-serif"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>"
        f"html,body{{margin:0;height:100%;background:{SLIDE_BACKGROUND};color:#fff;font-family:{font}}}"
        f"#fs-slide{{position:fixed;inset:0;background:{SLIDE_BACKGROUND}}}"
        f"#fs-slide-body{{position:absolute;left:96px;top:96px;right:{AVATAR_WIDTH + 60}px;"
        f"bottom:{AVATAR_HEIGHT + 20}px;overflow:hidden}}"
        "h1{font-size:54px;line-height:1.15;font-weight:700;margin:0 0 30px}"
        "ul{margin:0;padding:0 0 0 1.1em}"
        "li{font-size:32px;line-height:1.35;font-weight:500;margin:0 0 14px}"
        "</style></head><body><div id='fs-slide'><div id='fs-slide-body'>"
        f"<h1>{html.escape(title)}</h1><ul>{items}</ul></div></div></body></html>"
    )


def render_slide(browser, card: str, path: Path, width: int, height: int) -> None:
    """Paint a card to a PNG in a page of its own, with no overlay and no camera."""
    page = browser.new_page(viewport={"width": width, "height": height})
    try:
        page.set_content(card, wait_until="load")
        page.screenshot(path=str(path))
    finally:
        page.close()


#: How long the clicked element must stay put, scroll and box, before it is
#: called still; and how long to wait for that at most. A page that answers a
#: click by fetching and drawing moves within a few frames on loopback; one
#: still moving after the cap is reported as still moving, at its last place.
STILL_FOR_SECONDS = 0.15
STILL_WITHIN_SECONDS = 2.0
#: How long a clicked element that left the page is looked for, drawn again, before it is called gone.
REDRAWN_WITHIN_SECONDS = 0.3
#: A settled point further than this from the press, in either direction, means
#: the page moved between the press and the frames after it.
MOVED_PIXELS = 4

SETTLE_SCRIPT = """
(el, [stillFor, within]) => new Promise((resolve) => {
  const begun = performance.now();
  const look = () => {
    if (!el.isConnected) return { gone: 'detached' };
    const box = el.getBoundingClientRect();
    if (box.width === 0 && box.height === 0) return { gone: 'not shown' };
    return {
      x: Math.floor(box.left + box.width / 2),
      y: Math.floor(box.top + box.height / 2),
      key: [scrollX, scrollY, box.left, box.top, box.width, box.height].join(','),
    };
  };
  let last = null, since = performance.now(), frames = 0;
  const tick = () => {
    const now = look();
    if (now.gone) return resolve(now);
    if (!last || now.key !== last.key) { last = now; since = performance.now(); frames = 0; }
    else frames += 1;
    const t = performance.now();
    if (frames >= 2 && t - since >= stillFor) return resolve({ x: now.x, y: now.y, still: true });
    if (t - begun >= within) return resolve({ x: now.x, y: now.y, still: false });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})
"""


def settled_centre(page, handle, selector: str) -> dict:
    """Where the clicked element's centre sits, in viewport pixels, once the page is still.

    Still means the window's scroll and the element's box have not changed for
    `STILL_FOR_SECONDS` and at least two animation frames. A page that answers
    a click by drawing its list or its buttons again replaces the very node
    that was clicked (the orders page does both), so when the node leaves the
    page the selector is asked again, for the one drawn in its place, for as
    long as `STILL_WITHIN_SECONDS` allows. Returns `{"x", "y", "still"}`, or
    `{"gone": <why>}` when nothing the selector names is on the page any more,
    it is not shown, or the page it was on has been left.
    """
    begun = time.monotonic()
    gone = "element gone"
    while True:
        left = STILL_WITHIN_SECONDS - (time.monotonic() - begun)
        if handle is not None and left > 0:
            try:
                seen = handle.evaluate(SETTLE_SCRIPT, [STILL_FOR_SECONDS * 1000, left * 1000])
            except Exception:  # the page navigated away, or the node was collected
                seen = {"gone": "detached"}
            if seen.get("gone") != "detached":
                return seen if "gone" not in seen else {"gone": f"element {seen['gone']}"}
        if time.monotonic() - begun >= STILL_WITHIN_SECONDS:
            return {"gone": gone}
        # The node was taken away, or not found yet: look for the one drawn in its place.
        try:
            handle = page.query_selector(selector)
        except Exception:  # a navigation in flight
            handle = None
        if handle is None:
            # Nothing there now. A page drawing its list again may have
            # cleared it and not yet filled it; give that a moment.
            if time.monotonic() - begun >= REDRAWN_WITHIN_SECONDS:
                return {"gone": gone}
            page.wait_for_timeout(30)


def click_position(press: tuple[int, int] | None, settled: dict) -> dict:
    """The keys a click mark carries about where it is: `x`, `y`, `press`, `moved`, `settled`.

    `x`, `y` is where the clicked thing sits on the frames after the click.
    `press` is where the page heard the press. `moved` says whether the two
    are more than `MOVED_PIXELS` apart. `settled` is there only when `x`, `y`
    is not a still, measured position, and says why. Nothing is guessed: what
    cannot be measured is `None`.
    """
    keys: dict = {"press": {"x": press[0], "y": press[1]} if press else None}
    if settled.get("gone"):
        keys["x"], keys["y"] = press if press else (None, None)
        keys["settled"] = settled["gone"] if isinstance(settled["gone"], str) else "element gone"
        return keys
    keys["x"], keys["y"] = int(settled["x"]), int(settled["y"])
    if not settled.get("still"):
        keys["settled"] = f"still moving after {STILL_WITHIN_SECONDS:g} s; its last place"
    if press:
        keys["moved"] = max(abs(keys["x"] - press[0]), abs(keys["y"] - press[1])) > MOVED_PIXELS
    return keys


#: The name the page calls to say where it was pressed.
PRESS_BINDING = "__scenePressedAt"


def listen_for_presses(context, presses: list) -> None:
    """Have every page of the take say where each press landed, into `presses`.

    A press is listened for rather than a click, and in the capture phase,
    because a button that takes itself away on the press (or a page that
    navigates on it) still reports the press. The point is `clientX`/`clientY`,
    rounded down: a pixel of the viewport, which is a pixel of the frame.
    """
    context.expose_binding(PRESS_BINDING, lambda _source, x, y: presses.append((int(x), int(y))))
    context.add_init_script(
        "addEventListener('pointerdown', (e) => { try { window."
        + PRESS_BINDING
        + "(Math.floor(e.clientX), Math.floor(e.clientY)); } catch (err) { /* a mark is not worth a take */ } },"
        " true);"
    )


def wait_expression(step: dict) -> str:
    """The JavaScript a `wait_for` waits on — always about state, never existence."""
    if step.get("js"):
        return f"() => {{ {step['js']} }}" if "return" in step["js"] else f"() => ({step['js']})"
    selector = json.dumps(step["selector"])
    if step.get("attribute"):
        attribute, equals = json.dumps(step["attribute"]), json.dumps(str(step["equals"]))
        return (
            f"() => {{ const n = document.querySelector({selector});"
            f" return !!n && n.getAttribute({attribute}) === {equals}; }}"
        )
    text = json.dumps(step["text"].lower())
    return f"() => {{ const n = document.querySelector({selector}); return !!n && (n.innerText||'').toLowerCase().includes({text}); }}"  # noqa: E501


def describe_wait(step: dict) -> str:
    """One line a person can read in the run's output."""
    if step.get("js"):
        return step["js"]
    if step.get("attribute"):
        return f"{step['selector']}[{step['attribute']}] == {step['equals']!r}"
    return f"{step['selector']} to say {step['text']!r}"


# ------------------------------------------------------------- what it wrote


def write_script_file(out: Path, name: str, said: list[str]) -> Path:
    """The overlay lines that were actually said, one per line, nothing else.

    Written from what the stage said and not from the scene file, so the script
    beside a recording is a record of that recording rather than a claim about
    it.
    """
    path = out / f"{name}.script.txt"
    path.write_text("\n".join(said) + "\n", encoding="utf-8")
    return path


def readme(scene: dict, facts: dict) -> str:
    """The folder's own page: what this is, what it was filmed from, what is staged."""
    gif = facts.get("gif_name") or f"{scene['name']}.gif"
    mp4 = facts.get("mp4_name") or f"{scene['name']}.mp4"
    script = facts.get("script_name") or f"{scene['name']}.script.txt"
    lines = [
        f"# {scene['title']}",
        "",
    ]
    if facts.get("lesson_line"):
        lines += [facts["lesson_line"], ""]
    lines += [
        f"`{gif}` — {facts['gif_seconds']:.1f} s, "
        f"{facts['gif_bytes'] / 1024 / 1024:.1f} MB, {facts['gif_setting']}. "
        f"Watch it: `mpv --loop {mp4}`",
        "",
        "## What it shows",
        "",
    ]
    lines += [f"{number}. {line}" for number, line in enumerate(facts["said"], start=1)]
    lines += [
        "",
        "## What it was filmed from",
        "",
        f"- plant **{facts['plant']}** on `{facts['base']}`, reporting **v{facts['version']}**",
        f"- recorded {facts['when']} by `scripts/record-scene.py` from commit `{facts['commit']}`",
        f"- scene file `{facts['scene_file']}` in that commit",
        f"- the command: `{facts['command']}`",
        "",
        "## What is this recording's and not the product's",
        "",
        ("- the caption bar along the bottom: the scene file's `say` lines, burnt in, stopping"
         f" {CAPTION_RIGHT_INSET} px short of the right-hand edge"
         if scene.get("captions", True) else
         "- no caption bar: this take was made with `captions = false`; the lines are in the script file only"),
        f"- the bottom-right {AVATAR_WIDTH} by {AVATAR_HEIGHT} px left empty for the narrator's avatar;"
        " the recording checks that no writing of its own lands there",
        f"- the badge at the top right: `{facts['badge']}`,"
        " in every frame of the GIF and the mp4, which the recording checks before it writes this page",
        "- the product's floating Assistant and Design buttons are hidden, because they sit where"
        " the caption bar goes",
    ]
    plant = scene.get("plant")
    if plant:
        lines += [
            f"- **the plant itself**: built fresh from `{plant['pack']}` into a temporary home, run at"
            f" **{plant['speed']}x** the line's clock, and stopped and deleted when the take ended."
            " Nothing in the checkout and no plant anybody else started was touched.",
        ]
        if plant.get("roll_when_chart"):
            lines += [
                f"- **when the camera started**: it waited, off camera, for"
                f" `{plant['roll_when_chart']}` to have drawn at least"
                f" {plant.get('roll_when_readings', 0)} readings and be in control, with its newest"
                f" reading {plant['roll_when_beyond_sigma']:.0f} sigma or more"
                f"{' below' if plant.get('roll_when_below_centre') else ' from'} centre — the reading"
                " that fires no rule on its own. The rule then fires on camera. The waiting is cut"
                " from the clip; nothing between the event and what follows it is.",
            ]
        for wanted in plant.get("roll_when_order") or []:
            asked = []
            if wanted.get("completed"):
                asked.append("finished and made its lot")
            if wanted.get("consumed"):
                asked.append(f"drawn on `{wanted['consumed']}`")
            if wanted.get("booked"):
                asked.append(f"booked {float(wanted['booked']):,.0f}, short of its quantity")
            source = ("the plant's own genealogy" if wanted.get("completed") or wanted.get("consumed")
                      else "the plant's own order screen")
            lines += [
                f"- **when the camera started**: it waited, off camera, until order `{wanted['code']}` had"
                f" {' and '.join(asked)}, as {source} reported it. The waiting is cut"
                " from the clip.",
            ]
        for key, value in sorted((plant.get("env") or {}).items()):
            lines += [f"- **the simulated floor was told** `{key}={value}` — see *Worth knowing* below"]
    lines += [
        "",
        "Everything else is the plant's own screens, read live, and every figure on them is the"
        " plant's own.",
    ]
    if scene.get("allow_writes"):
        # What was written is the scene's to say: the first scene to write
        # took a hold under review, and every page this script wrote said so,
        # including the pages of scenes that wrote something else.
        written = scene.get("what_is_written") or (
            "which is how the operator takes the hold under review:"
            " that click is a real `POST` to a real plant, and the hold really is under review"
            " afterwards."
        )
        lines += [
            "",
            f"This scene is allowed to write, {written} The recording refuses to run at all unless"
            " the plant is at a loopback address.",
        ]
    # A scene's own footnotes, after everything about how the take was made:
    # some things on screen need a sentence that is nobody's fault and cannot
    # go in a caption. The clip has forty-five seconds; the folder does not.
    if scene.get("notes"):
        lines += ["", "## Worth knowing", ""]
        lines += [f"- {note}" for note in scene["notes"]]
    lines += [
        "",
        "## The files",
        "",
        f"- `{gif}` — the loop",
        f"- `{mp4}` — the same take, full length and full rate",
        f"- `{script}` — the overlay lines in order, for whoever narrates",
        "- `stills/` — one screenshot per `mark`",
        *(["- `slides/` — each card as the recorder painted it, without the caption or the badge"]
          if facts.get("slides") else []),
        *(["- `lesson.json` — the lesson this folder is, what it holds, and what it was filmed from"]
          if facts.get("lesson_line") else []),
        "- `marks.json` — the seconds every step landed at; the frame's size and the GIF's; and, on every"
        " click, `x` and `y`, the pixel of the mp4's frame the click landed on",
        "",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------- the files


def run(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    if done.returncode != 0:
        raise SystemExit(f"{command[0]} failed: {(done.stderr or done.stdout).strip()[-800:]}")


def make_mp4(raw: Path, mp4: Path) -> None:
    run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(raw),
         "-vf", "fps=20", "-c:v", "libx264", "-crf", "26", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(mp4)])


def make_gif(raw: Path, gif: Path, skip: float, length: float | None = None) -> tuple[str, int, int]:
    """The first setting whose GIF fits. Returns how it was made, its bytes, its fps.

    `length`, when given, stops the GIF that many seconds after `skip`: a
    lesson's teaser is its hook and not the whole lesson.
    """
    cut = ["-t", f"{length:.2f}"] if length is not None else []
    for fps, width in GIF_SETTINGS:
        run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", str(skip), *cut, "-i", str(raw),
             "-filter_complex",
             f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];"
             "[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3",
             str(gif)])
        size = gif.stat().st_size
        print(f"   {fps} fps at {width} wide: {size:,} bytes", flush=True)
        if size <= GIF_LIMIT_BYTES:
            return f"{fps} fps, {width} wide", size, fps
    raise SystemExit(f"no setting brought the GIF under {GIF_LIMIT_BYTES:,} bytes; the take is too long")


def gif_size(gif: Path) -> dict:
    """The width and height a GIF says it is, read from its own header.

    Bytes 6 to 9 of every GIF are its logical screen width and height, little
    endian. Read here rather than asked of ffprobe, so the size in marks.json is
    the size of the file beside it and nothing else has to be installed.
    """
    head = gif.read_bytes()[:10]
    if len(head) < 10 or head[:3] != b"GIF":
        raise SystemExit(f"{gif} is not a GIF")
    return {"width": int.from_bytes(head[6:8], "little"), "height": int.from_bytes(head[8:10], "little")}


def marks_document(scene: dict, made: dict, gif: Path, gif_seconds_taken: float) -> dict:
    """What marks.json says: when each step landed, and where on the frame.

    The four keys it has always had stay as they were, because studio reads
    them. `frame` is the mp4's size, which is the viewport's; `gif` is the
    published loop's, so a point placed on the mp4 can be scaled onto the GIF.
    Every mark carries `kind` and `part`; a `say` its `line` and `hold`; a
    `slide` its `image`; a scroll its `from_y`, `to_y` and `took`.
    """
    return {
        "frame": {"width": int(scene["width"]), "height": int(scene["height"])},
        "gif": gif_size(gif),
        "gif_skip_seconds": float(scene["gif_skip_seconds"]),
        "planned_seconds": planned_seconds(scene),
        "took_seconds": made["took"],
        "gif_seconds": round(gif_seconds_taken, 2),
        "marks": made["marks"],
    }


def badge_box(width: int, height: int) -> tuple[float, float, float, float]:
    """Where the badge is sampled, as fractions of the frame: x, y, w, h.

    Fractions and not pixels because the GIF is scaled down from the viewport,
    and a box in pixels would sample the page beside the badge instead.
    """
    right = BADGE_RIGHT + BADGE_SAMPLE_INSET
    x = (width - right - BADGE_SAMPLE_SIZE) / width
    y = (BADGE_TOP + (BADGE_HEIGHT - BADGE_SAMPLE_SIZE) / 2) / height
    return x, y, BADGE_SAMPLE_SIZE / width, BADGE_SAMPLE_SIZE / height


def frame_colours(path: Path, box: tuple[float, float, float, float]) -> list[tuple[int, int, int]]:
    """The average colour of one box, in every frame of a file. One triple per frame."""
    x, y, w, h = box
    crop = f"crop=iw*{w:.6f}:ih*{h:.6f}:iw*{x:.6f}:ih*{y:.6f},scale=1:1"
    done = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-vf", crop,
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"ffmpeg could not read {path}: {done.stderr.decode(errors='replace')[-400:]}")
    raw = done.stdout
    return [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw) - 2, 3)]


def badge_distances(
    colours: list[tuple[int, int, int]],
    want: tuple[int, int, int] = BADGE_BACKGROUND,
) -> list[int]:
    """How far each frame's sample is from the badge's own colour.

    The measure is the largest per-channel difference. A frame the badge has
    gone from shows the page behind it, which is further away than any palette
    could put it.
    """
    return [max(abs(a - b) for a, b in zip(colour, want, strict=True)) for colour in colours]


def frames_without_the_badge(
    colours: list[tuple[int, int, int]],
    want: tuple[int, int, int] = BADGE_BACKGROUND,
    tolerance: int = BADGE_TOLERANCE,
) -> list[int]:
    """Which frames the badge is not in. Every one of them, not just the worst."""
    return [index for index, off in enumerate(badge_distances(colours, want)) if off > tolerance]


def badge_in_every_frame(
    colours: list[tuple[int, int, int]],
    want: tuple[int, int, int] = BADGE_BACKGROUND,
    tolerance: int = BADGE_TOLERANCE,
) -> bool:
    """Is the badge in every frame? A film with no frames is not a film."""
    return bool(colours) and not frames_without_the_badge(colours, want, tolerance)


def teaser_length(marks: list[dict], skip: float) -> float | None:
    """How long a lesson's teaser runs: from `skip` to the end of the hook.

    The hook ends at the first mark in another part. A take that is all hook
    (or names no parts) is its own teaser, and None says so.
    """
    for mark in marks:
        if mark.get("part") not in (None, "hook") and mark["at"] > skip:
            return round(mark["at"] - skip, 2)
    return None


def say_lines_of(marks: list[dict]) -> list[str]:
    """The `say` lines marks.json carries, in order — what the script file must match."""
    return [mark["line"] for mark in marks if mark.get("kind") == "say"]


# ------------------------------------------------------------------ the course
#
# The class is laid out in `scripts/course/course.toml`; the tree it is filmed
# into, and the two JSON files studio, the dashboard and the website read, are
# described in the lesson hand-off contract. A re-record of one lesson changes
# that lesson's folder and four of that lesson's keys in course.json, and
# nothing else: studio keeps its own work beside the tree, never in it, and
# keys some of it on what the crew must not touch.

#: The version of the lesson contract these files are written to.
CONTRACT = 1
#: How far a lesson has come. The crew writes the first two; studio and the
#: person publishing write the others, and nothing ever moves one back.
STATUSES = ("planned", "recorded", "voiced", "published")
#: The keys of a lesson in course.json that a re-record may change. Nothing
#: else in course.json is the recording's to touch — `voiced_take` least of
#: all, which studio sets to the take it voiced.
RECORD_KEYS = ("take", "status", "duration_seconds", "recorded")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def load_course(path: Path) -> dict:
    """Read course.toml and refuse what a reader of course.json could not rely on.

    Returns the course with each lesson's `id` ("2.1") and `path`
    ("02-quality-on-the-line/01-spc-the-chart-notices-first") filled in.
    """
    with path.open("rb") as raw:
        course = tomllib.load(raw)
    head = course.get("course") or {}
    for key in ("slug", "title", "voice", "status_line", "out"):
        if not head.get(key):
            raise SystemExit(f"{path}: [course] needs `{key}`")
    modules = course.get("module") or []
    if not modules:
        raise SystemExit(f"{path}: a course needs at least one [[module]]")
    for m_number, module in enumerate(modules, start=1):
        if module.get("order") != m_number:
            raise SystemExit(f"{path}: module {m_number} says `order = {module.get('order')}`; "
                             "modules are numbered 1, 2, 3 in the order they are written")
        if not SLUG.fullmatch(str(module.get("slug") or "")) or not module.get("title"):
            raise SystemExit(f"{path}: module {m_number} needs a `title` and a lower-case, hyphenated `slug`")
        lessons = module.get("lesson") or []
        if not lessons:
            raise SystemExit(f"{path}: module {m_number} has no [[module.lesson]]")
        for l_number, lesson in enumerate(lessons, start=1):
            where = f"{path}: lesson {m_number}.{l_number}"
            if lesson.get("order") != l_number:
                raise SystemExit(f"{where} says `order = {lesson.get('order')}`")
            if not SLUG.fullmatch(str(lesson.get("slug") or "")) or not lesson.get("title"):
                raise SystemExit(f"{where} needs a `title` and a lower-case, hyphenated `slug`")
            if not lesson.get("one_line"):
                raise SystemExit(f"{where} needs a `one_line`")
            lesson["outcomes"] = list(lesson.get("outcomes") or [])
            lesson["knows_before"] = [str(k) for k in lesson.get("knows_before") or []]
            lesson["id"] = f"{m_number}.{l_number}"
            lesson["path"] = f"{m_number:02d}-{module['slug']}/{l_number:02d}-{lesson['slug']}"
    known = {lesson["id"] for module in modules for lesson in module["lesson"]}
    for module in modules:
        for lesson in module["lesson"]:
            unknown = [k for k in lesson["knows_before"] if k not in known]
            if unknown:
                raise SystemExit(f"{path}: lesson {lesson['id']} assumes {', '.join(unknown)}, not in the course")
    course["course"] = head
    course["module"] = modules
    return course


def find_lesson(course: dict, lesson_id: str) -> tuple[dict, dict]:
    """The module and the lesson a scene's `lesson = "2.1"` names."""
    for module in course["module"]:
        for lesson in module["lesson"]:
            if lesson["id"] == lesson_id:
                return module, lesson
    raise SystemExit(f"lesson {lesson_id} is not in the course")


def refuse_studios_tree(path: Path) -> None:
    """Nothing the recorder writes goes under a `studio` folder: that tree is studio's."""
    if "studio" in path.parts:
        raise SystemExit(f"{path} is inside a `studio` folder; the recorder never writes there")


def now_stamp() -> str:
    """Now, local, to the second, with its offset — as course.json and lesson.json date things."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json_atomically(path: Path, document: dict) -> None:
    """Write beside the file and move it into place, so a reader never opens half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, scratch = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(document, out, indent=1, ensure_ascii=False)
            out.write("\n")
        os.replace(scratch, path)
    finally:
        if os.path.exists(scratch):
            os.remove(scratch)


def lesson_keys(lesson_doc: dict | None) -> dict:
    """The four keys a recorded folder gives its lesson in course.json.

    A folder that holds only the lesson's teaser leaves the lesson `planned`:
    its `take` is the folder's, but there is no lesson video and so no
    duration. A folder with no lesson.json is a lesson nobody has filmed.
    """
    if not lesson_doc:
        return {"take": None, "status": "planned", "duration_seconds": None, "recorded": None}
    has_video = bool((lesson_doc.get("files") or {}).get("video"))
    return {
        "take": lesson_doc["lesson"]["take"],
        "status": "recorded" if has_video else "planned",
        "duration_seconds": lesson_doc["lesson"]["duration_seconds"] if has_video else None,
        "recorded": (lesson_doc.get("filmed_from") or {}).get("recorded"),
    }


def forward(previous: str | None, now: str) -> str:
    """A status only moves forward: studio's `voiced` survives the crew's `recorded`."""
    if previous in STATUSES and STATUSES.index(previous) > STATUSES.index(now):
        return previous
    return now


def course_document(course: dict, root: Path, previous: dict | None) -> dict:
    """The whole course.json, from course.toml and the lesson folders under `root`.

    `previous` is the course.json already there, if any: `voiced_take` and any
    status beyond `recorded` are carried over from it unchanged, because they
    are studio's and the person publishing's, never the crew's.
    """
    before = {}
    for module in (previous or {}).get("modules") or []:
        for lesson in module.get("lessons") or []:
            if lesson.get("path"):
                before[lesson["path"]] = lesson
    versions = []
    modules = []
    for module in course["module"]:
        lessons = []
        for lesson in module["lesson"]:
            folder = read_json(root / lesson["path"] / "lesson.json")
            keys = lesson_keys(folder)
            if folder:
                versions.append(((folder.get("filmed_from") or {}).get("recorded") or "",
                                 (folder.get("filmed_from") or {}).get("product_version")))
            was = before.get(lesson["path"], {})
            lessons.append({
                "order": lesson["order"], "slug": lesson["slug"], "title": lesson["title"],
                "path": lesson["path"],
                "status": forward(was.get("status"), keys["status"]),
                "duration_seconds": keys["duration_seconds"], "take": keys["take"],
                "recorded": keys["recorded"],
                "voiced_take": was.get("voiced_take"),
                "outcomes": lesson["outcomes"],
            })
        modules.append({"order": module["order"], "slug": module["slug"], "title": module["title"],
                        "lessons": lessons})
    head = course["course"]
    newest = max(versions, default=("", None))[1]
    return {
        "contract": CONTRACT,
        "course": {"slug": head["slug"], "title": head["title"], "generated": now_stamp(),
                   # The version the newest filmed folder was filmed from; none filmed, none known.
                   "product_version": newest, "voice": head["voice"], "status_line": head["status_line"]},
        "modules": modules,
    }


def course_readme(course: dict, document: dict) -> str:
    """The tree's page for a person: what this is, how to watch it, where each lesson stands."""
    lessons = [lesson for module in document["modules"] for lesson in module["lessons"]]
    counted = {status: sum(1 for lesson in lessons if lesson["status"] == status) for status in STATUSES}
    lines = [
        f"# {document['course']['title']}",
        "",
        f"*{document['course']['status_line']}.* Generated by `scripts/record-scene.py --course` from"
        " `scripts/course/course.toml`; never edited by hand. `course.json` beside this page is the same"
        " list for machines.",
        "",
        f"{len(document['modules'])} modules, {len(lessons)} lessons: "
        + ", ".join(f"{counted[status]} {status}" for status in STATUSES) + ".",
        "",
        "Each lesson is a folder `NN-module/NN-lesson/`. Watch one with `mpv --loop <folder>/<slug>.mp4`"
        " (or its teaser, `<slug>.teaser.mp4`, where only the teaser is filmed yet). The bottom-right"
        " corner of every frame is left empty for the narrator's avatar. Nothing here is public until"
        " somebody copies it somewhere public.",
        "",
    ]
    by_path = {lesson["path"]: lesson for module in course["module"] for lesson in module["lesson"]}
    for module in document["modules"]:
        lines += [f"## {module['order']}. {module['title']}", "", "| # | Lesson | Status | Take |", "|---|---|---|---|"]
        for lesson in module["lessons"]:
            lines.append(f"| {by_path[lesson['path']]['id']} | [{lesson['title']}]({lesson['path']}/) "
                         f"| {lesson['status']} | {lesson['take'] if lesson['take'] is not None else '—'} |")
        lines.append("")
    return "\n".join(lines)


def write_course(course: dict, root: Path) -> dict:
    """Write course.json and the tree's README from course.toml and the folders under `root`."""
    refuse_studios_tree(root)
    document = course_document(course, root, read_json(root / "course.json"))
    write_json_atomically(root / "course.json", document)
    (root / "README.md").write_text(course_readme(course, document), encoding="utf-8")
    return document


def record_lesson_in_course(course: dict, root: Path, lesson: dict) -> dict:
    """After a lesson's folder is in place: that lesson's four keys in course.json, and nothing else.

    With no course.json yet the whole file is written first, from course.toml.
    Every other byte of an existing course.json stays as it was: the other
    lessons, `voiced_take`, `generated`, keys this script does not know.
    """
    path = root / "course.json"
    document = read_json(path)
    if document is None:
        return write_course(course, root)
    keys = lesson_keys(read_json(root / lesson["path"] / "lesson.json"))
    for module in document.get("modules") or []:
        for entry in module.get("lessons") or []:
            if entry.get("path") == lesson["path"]:
                for key in RECORD_KEYS:
                    entry[key] = forward(entry.get("status"), keys["status"]) if key == "status" else keys[key]
                write_json_atomically(path, document)
                return document
    raise SystemExit(f"{path} has no lesson at {lesson['path']}; run --course to write it from course.toml first")


def next_take(final: Path) -> int:
    """The take this recording will be: one more than the folder's, or 1."""
    before = read_json(final / "lesson.json")
    try:
        return int(before["lesson"]["take"]) + 1
    except (TypeError, KeyError, ValueError):
        return 1


def a_folder_beside(final: Path) -> Path:
    """A temporary folder next to where the take will go, on the same disk, so the move is a rename."""
    refuse_studios_tree(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=final.parent, prefix=f".{final.name}.take-"))


def publish_folder(work: Path, final: Path) -> None:
    """Move a finished folder into place: the old one aside, the new one in, the old one gone.

    Between the two renames the folder is briefly absent, never half-written.
    """
    old = None
    if final.exists():
        old = final.with_name(f".{final.name}.old-{os.getpid()}")
        shutil.rmtree(old, ignore_errors=True)
        final.rename(old)
    work.rename(final)
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)


def lesson_document(course: dict, module: dict, lesson: dict, scene: dict, facts: dict) -> dict:
    """lesson.json: one lesson's title, order, outcomes, files, frame, and what it was filmed from."""
    plant = scene.get("plant")
    if plant:
        filmed = f"{plant['pack']} at {float(plant['speed']):g}x, scratch, loopback"
    else:
        filmed = f"{facts['plant']} on {facts['base']}, a plant somebody else started"
    honesty = ["simulated plant"]
    if plant and float(plant["speed"]) != 1:
        honesty.append(f"sped-up replay at {float(plant['speed']):g}x, the MES told the speed")
    honesty.append("no audio in the file — the voice is added afterwards")
    if scene["take_is"] == "teaser":
        honesty.append("this folder holds the lesson's teaser only; the lesson itself is not filmed yet")
    honesty += list(scene.get("honesty") or [])
    files = facts["files"]
    return {
        "contract": CONTRACT,
        "module": {"order": module["order"], "slug": module["slug"], "title": module["title"]},
        "lesson": {"order": lesson["order"], "slug": lesson["slug"], "title": lesson["title"],
                   "one_line": lesson["one_line"], "outcomes": lesson["outcomes"],
                   "knows_before": lesson["knows_before"],
                   "duration_seconds": facts["duration_seconds"] if files.get("video") else None,
                   "take": facts["take"]},
        "files": files,
        "frame": {"width": int(scene["width"]), "height": int(scene["height"]), "fps": 20},
        "reserved": {"avatar": {"corner": "bottom-right", "width": AVATAR_WIDTH, "height": AVATAR_HEIGHT},
                     "caption_bar": {"bottom": 0, "height": CAPTION_HEIGHT, "right_inset": CAPTION_RIGHT_INSET,
                                     "burnt_in": bool(scene["captions"])},
                     "status_badge": {"corner": "top-right"}},
        "filmed_from": {"product_version": facts["version"], "commit": facts["commit"],
                        "scene": facts["scene_file"], "plant": filmed, "recorded": facts["recorded"]},
        "honesty": honesty,
    }


# ----------------------------------------------------------- the scratch plant

# Ports on this box that belong to something somebody is reading, and which a
# scratch plant must never be given. The lab fleet has been up since 2026-10-22
# and the public demo is the only plant on this box standing orders allow to
# simulate; a take that bound one of these would not fail, it would quietly
# film the wrong plant, or stop one Scott is watching. The pre-flight names
# what usually owns a port so the person reading the refusal knows whether to
# worry.
PORTS_THAT_BELONG_TO_SOMETHING_ELSE = {
    8090: "the lab's fleet console",
    8110: "the lab's bottling plant",
    8111: "the lab's second copy of the bottling plant",
    8120: "the lab's machining plant",
    9030: "the public cutlery demo",
    4941: "the lab's OPC endpoint",
    4942: "the lab's second OPC endpoint",
    4943: "the public demo's OPC endpoint",
    5433: "the public demo's PostgreSQL",
    5434: "the public demo's second PostgreSQL",
}

#: What a scene's `[plant] env` may set: the switches of `fsmes run-operations`,
#: the simulated floor, and nothing of the MES's own.
SCENE_ENV_PREFIX = "MES_OPS_"

#: How long to wait for a plant that has just been started to answer /health.
PLANT_UP_SECONDS = 60.0
#: How often to ask the plant a question while waiting for it.
POLL_SECONDS = 1.0


def plant_defaults(spec: dict) -> dict:
    """Fill in a `[plant]` table and refuse anything this script would guess at."""
    if not spec.get("pack"):
        raise SystemExit("[plant]: a scratch plant needs a `pack` — the directory with plant.toml")
    spec.setdefault("speed", 1.0)
    spec.setdefault("roll_when_chart", None)
    spec.setdefault("roll_when_in_control", True)
    spec.setdefault("roll_when_beyond_sigma", 0.0)
    spec.setdefault("roll_when_timeout_seconds", 300.0)
    spec.setdefault("env", {})
    if spec["roll_when_chart"] and "/" not in str(spec["roll_when_chart"]):
        raise SystemExit("[plant]: `roll_when_chart` is `MATERIAL/characteristic`, e.g. FG-BOTTLE/fill_weight")
    orders = spec.setdefault("roll_when_order", [])
    if not isinstance(orders, list):
        raise SystemExit("[plant]: `roll_when_order` is a list of tables — `[[plant.roll_when_order]]`")
    for wanted in orders:
        if not wanted.get("code"):
            raise SystemExit("[plant]: every `roll_when_order` needs the `code` of the order it waits on")
        if not (wanted.get("completed") or wanted.get("consumed") or wanted.get("booked")):
            # An order with no condition is already "ready" the moment the plant
            # is up, and the take would roll on an order with nothing in it.
            raise SystemExit(
                f"[plant]: `roll_when_order` {wanted['code']} waits for nothing — "
                "say `completed = true`, `consumed = \"LOT-…\"`, `booked = <count>`, or more than one"
            )
    if spec["roll_when_chart"] and spec["roll_when_order"]:
        raise SystemExit("[plant]: a take rolls on a chart or on an order, not on both")
    if not isinstance(spec["env"], dict):
        raise SystemExit("[plant]: `env` is a table of NAME = \"value\"")
    for key, value in spec["env"].items():
        # The simulated floor's own switches and nothing else. A scene may
        # change what the simulated people on the floor do — that the shift
        # supervisor leaves finishing an order to the person on camera, say —
        # and must not reach into the MES itself: a take of a product
        # configured differently from the one anybody installs is not a take
        # of the product.
        if not str(key).startswith(SCENE_ENV_PREFIX):
            raise SystemExit(
                f"[plant]: `env` may set only the simulated floor's switches ({SCENE_ENV_PREFIX}*), "
                f"not {key}"
            )
        if not isinstance(value, str):
            raise SystemExit(f"[plant]: `env` values are strings, as an environment's are — {key} is not")
    return spec


def ports_of_pack(pack: Path) -> dict[int, str]:
    """The ports this pack's own plant.toml says it will listen on.

    Read from the pack rather than restated in the scene file: the plant
    boundary is config, and two places to change a port is one place to forget.
    """
    with (pack / "plant.toml").open("rb") as raw:
        settings = tomllib.load(raw)
    serve = settings.get("serve") or {}
    ports = {}
    if serve.get("api_port"):
        ports[int(serve["api_port"])] = "its API"
    endpoint = serve.get("opc_endpoint") or ""
    opc = urllib.parse.urlsplit(endpoint).port if "//" in endpoint else None
    if opc:
        ports[int(opc)] = "its OPC endpoint"
    return ports


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def refuse_a_port_that_is_not_ours(ports: dict[int, str]) -> None:
    """Every port the scratch plant wants, free and not somebody else's. Before, and after."""
    for port, what in sorted(ports.items()):
        owner = PORTS_THAT_BELONG_TO_SOMETHING_ELSE.get(port)
        if owner:
            raise SystemExit(
                f"this pack wants port {port} for {what}, and {port} is where {owner} answers. "
                "A scratch plant gets a port of its own or none: change the pack's `[serve]`."
            )
        if not port_is_free(port):
            raise SystemExit(
                f"port {port} is busy, and the scratch plant wants it for {what}. Something is already "
                f"listening there — find out what before this take binds to it or films it by mistake "
                f"(`ss -ltnp | grep :{port}`)."
            )


class ScratchPlant:
    """A plant this take built, started, and is responsible for taking away.

    It lives in a temporary directory of its own with its own fleet registry,
    so `fsmes fleet` can only ever see this one plant: `stop` cannot reach a
    plant somebody else created, and nothing is stopped by matching a command
    line, which is how two of Scott's lab plants went down for six minutes on
    2026-09-22.
    """

    def __init__(self, name: str, home: Path, env: dict[str, str], ports: dict[int, str]) -> None:
        self.name = name
        self.home = home
        self.env = env
        self.ports = ports
        self.running = False

    def _fsmes(self, *words: str, root: bool = False) -> subprocess.CompletedProcess:
        """Run one `fsmes` command with this plant's registry and nothing else's.

        `root` adds `--root`, which only the `fleet` verbs take: they look up a
        repository to work from, and `sim-generate` takes a path and refuses
        the option. It is passed where it belongs rather than to everything.
        """
        if root:
            words = (*words, "--root", str(REPO))
        return subprocess.run(
            [sys.executable, "-m", "fsmes", *words],
            cwd=str(REPO), env=self.env, capture_output=True, text=True, encoding="utf-8", check=False,
        )

    def start(self, speed: float) -> None:
        done = self._fsmes("fleet", "start", self.name, "--speed", str(speed), root=True)
        if done.returncode != 0:
            raise SystemExit(f"could not start the scratch plant:\n{done.stdout}\n{done.stderr}")
        self.running = True

    def stop(self) -> None:
        """Stop the plant and take its home away. Safe to call twice."""
        if self.running:
            done = self._fsmes("fleet", "stop", self.name, root=True)
            self.running = False
            if done.returncode != 0:
                print(f"  the scratch plant would not stop cleanly:\n{done.stdout}\n{done.stderr}", flush=True)
        shutil.rmtree(self.home, ignore_errors=True)
        still_busy = [f"{port} ({what})" for port, what in sorted(self.ports.items()) if not port_is_free(port)]
        if still_busy:
            print(f"  these ports are still busy after stopping the scratch plant: {', '.join(still_busy)}",
                  flush=True)
        else:
            print(f"  scratch plant stopped, its home removed, "
                  f"{' and '.join(str(port) for port in sorted(self.ports))} free", flush=True)


def start_a_scratch_plant(spec: dict) -> ScratchPlant:
    """Build a plant from a pack in a temporary home, start it, and wait for it.

    The pack is copied rather than used where it lies, and the replay it needs
    is generated into the copy: a take must not leave anything behind in the
    checkout, and `out/` under a lab pack is a hundred megabytes nobody asked
    for.
    """
    pack = (REPO / spec["pack"]).resolve()
    if not (pack / "plant.toml").is_file():
        raise SystemExit(f"[plant]: {spec['pack']} has no plant.toml, so it is not a pack")

    ports = ports_of_pack(pack)
    refuse_a_port_that_is_not_ours(ports)

    with (pack / "plant.toml").open("rb") as raw:
        name = (tomllib.load(raw).get("plant") or {}).get("name")
    if not name:
        raise SystemExit(f"[plant]: {spec['pack']}/plant.toml has no `[plant] name`")

    home = Path(tempfile.mkdtemp(prefix=f"scene-plant-{name}-"))
    print(f"== bringing up a scratch {name} plant from {spec['pack']} at {spec['speed']}x, in {home}",
          flush=True)
    shutil.copytree(pack, home / "pack")
    (home / "fleet.toml").write_text(
        f'packs = ["pack"]\n\n[environment]\ndata_dir = "{(home / "data").as_posix()}"\n',
        encoding="utf-8",
    )
    # The scene's own floor switches ride in the environment the plant's
    # processes inherit, which is how `fsmes lab` hands an experiment's
    # `finish_orders = false` to the same floor.
    env = {**os.environ, **spec.get("env", {}), "FSMES_PLANT_REGISTRY": str(home / "fleet.toml")}
    if spec.get("env"):
        print("  the simulated floor is told: "
              + ", ".join(f"{key}={value}" for key, value in sorted(spec["env"].items())), flush=True)
    plant = ScratchPlant(name, home, env, ports)

    line = home / "pack" / "line.json"
    if line.is_file():
        print("  generating the replay this pack's line describes", flush=True)
        made = plant._fsmes("sim-generate", str(line))
        if made.returncode != 0:
            plant.stop()
            raise SystemExit(f"could not generate the replay:\n{made.stdout}\n{made.stderr}")

    built = plant._fsmes("fleet", "create", str(home / "pack"), root=True)
    if built.returncode != 0:
        plant.stop()
        raise SystemExit(f"could not build the scratch plant:\n{built.stdout}\n{built.stderr}")

    plant.start(float(spec["speed"]))
    return plant


def wait_for_the_plant(base: str, seconds: float = PLANT_UP_SECONDS) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ask(f"{base}/health"):
            return
        time.sleep(POLL_SECONDS)
    raise SystemExit(f"the scratch plant did not answer {base}/health within {seconds:.0f}s")


def a_signed_in_reader(base: str, user: str, password: str):
    """An opener that has signed in, for the questions asked before the camera rolls."""
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    reader = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    sign_in = urllib.request.Request(
        f"{base}/auth/login",
        data=json.dumps({"code": user, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        reader.open(sign_in, timeout=10)
    except (OSError, urllib.error.URLError) as refused:
        raise SystemExit(f"could not sign in to {base} as {user} to watch the chart: {refused}") from refused
    return reader


def wait_for_the_shot(base: str, spec: dict, user: str, password: str) -> dict:
    """Hold the camera until the chart is in the state the scene opens on.

    This is the waiting a camera operator does, and it is done here rather than
    in the scene because a `wait_for` inside a scene is filmed: a minute of an
    unchanging chart is a minute of the clip, and the clip has forty-five
    seconds. The take rolls on a chart that is in control with a reading
    already out at the edge of it — the point that fires no rule on its own —
    so that the rule fires while the camera is on the chart rather than before
    it arrives.

    Nothing here writes. It asks the plant the same question the chart asks.
    """
    material, _, characteristic = str(spec["roll_when_chart"]).partition("/")
    reader = a_signed_in_reader(base, user, password)
    url = f"{base}/quality/spc/{urllib.parse.quote(material)}/{urllib.parse.quote(characteristic)}"
    want_sigma = float(spec["roll_when_beyond_sigma"])
    want_in_control = bool(spec["roll_when_in_control"])
    want_readings = int(spec.get("roll_when_readings") or 0)
    want_below = bool(spec.get("roll_when_below_centre"))
    deadline = time.monotonic() + float(spec["roll_when_timeout_seconds"])
    started = time.monotonic()
    last = ""

    while time.monotonic() < deadline:
        try:
            with reader.open(url, timeout=10) as answer:
                chart = json.load(answer)
        except (OSError, urllib.error.URLError, ValueError):
            time.sleep(POLL_SECONDS)
            continue
        control = chart.get("control") or {}
        points = chart.get("points") or []
        centre, sigma = control.get("centre"), control.get("sigma")
        newest = points[-1]["value"] if points else None
        out = abs(centre - newest) / sigma if (newest is not None and centre is not None and sigma) else 0.0
        in_control = chart.get("stable")
        enough = centre is not None
        drawn = int(chart.get("n") or 0)
        # Two things the sigma on its own does not say, and both have cost a
        # take. A chart with a dozen readings estimates its limits from a dozen
        # numbers, so an ordinary wobble reads as two and a half sigma: one take
        # rolled on reading eighteen, filmed a wobble, waited the whole of its
        # patience for a rule that was never coming, and stopped. And a reading
        # that far out *above* centre is not the event this scene narrates —
        # the caption says a reading drops, and it has to be a drop.
        settled = drawn >= want_readings
        the_right_way = (newest < centre) if (want_below and newest is not None and centre is not None) else True

        said = (f"  {time.monotonic() - started:5.0f}s  {chart.get('n') or 0:3d} readings, "
                f"{'in control' if in_control else 'out of control' if enough else 'not enough to say'}"
                f"{f', newest {out:.1f} sigma out' if enough else ''}")
        if said != last:
            print(said, flush=True)
            last = said

        if enough and settled and the_right_way and (in_control or not want_in_control) and out >= want_sigma:
            print(f"  rolling: {chart.get('n')} readings, the chart in control, "
                  f"the newest reading {out:.1f} sigma from centre", flush=True)
            return chart
        time.sleep(POLL_SECONDS)

    raise SystemExit(
        f"the chart never reached the state this scene opens on within "
        f"{float(spec['roll_when_timeout_seconds']):.0f}s — at least {want_readings} readings, "
        f"{'in control, ' if want_in_control else ''}the newest {want_sigma:.0f} sigma or more "
        f"{'below' if want_below else 'from'} centre. Nothing was filmed. The replay this pack "
        "describes may have moved on, or the speed may be wrong for it."
    )


def order_is_ready(order: dict | None, genealogy: dict | None, wanted: dict) -> bool:
    """Whether one order is in the state a `[[plant.roll_when_order]]` asks for.

    `completed` means finished *and* with a lot to show for it: an order that
    completes with nothing good made produces no lot, and the scene is about the
    lot. `consumed` means that lot code is among the lots the order's own
    genealogy says went in. `booked` means the order is still open and has
    booked at least that many good — short of its quantity, so a scene about
    the count reaching the number rolls before it does. All are read from the
    plant; nothing is assumed from the pack.
    """
    if not order:
        return False
    if wanted.get("booked") and not (
        order.get("status") in ("released", "running")
        and float(order.get("good_qty") or 0) >= float(wanted["booked"])
    ):
        return False
    if not (wanted.get("completed") or wanted.get("consumed")):
        return True
    if not genealogy:
        return False
    finished = order.get("status") in ("completed", "closed") and bool(genealogy.get("produced"))
    if wanted.get("completed") and not finished:
        return False
    lot = wanted.get("consumed")
    return not lot or lot in {c.get("lot") for c in genealogy.get("consumed") or []}


def wait_for_the_orders(base: str, spec: dict, user: str, password: str) -> None:
    """Hold the camera until every order the scene films is in the state it films.

    The chart's wait, for a scene about orders: a cold plant takes a minute or
    more to finish its first order, and a minute of an order filling up is a
    minute of a forty-five-second clip. The take rolls once each order named
    has finished (if asked) and drawn on the lot named (if asked). Nothing
    here writes; it asks the same two questions the orders page asks.
    """
    reader = a_signed_in_reader(base, user, password)
    wanted = spec["roll_when_order"]
    deadline = time.monotonic() + float(spec["roll_when_timeout_seconds"])
    started = time.monotonic()
    last = ""

    def read(path: str) -> dict | None:
        try:
            with reader.open(f"{base}{path}", timeout=10) as answer:
                return json.load(answer)
        except (OSError, urllib.error.URLError, ValueError):
            return None

    while time.monotonic() < deadline:
        states = []
        for one in wanted:
            code = urllib.parse.quote(one["code"])
            order, genealogy = read(f"/workorders/{code}"), read(f"/execution/genealogy/{code}")
            states.append((one, order, genealogy, order_is_ready(order, genealogy, one)))
        said = f"  {time.monotonic() - started:5.0f}s  " + "; ".join(
            f"{one['code']} {(order or {}).get('status', 'unknown')}, "
            f"{len((genealogy or {}).get('consumed') or [])} issue(s), "
            f"{len((genealogy or {}).get('produced') or [])} lot(s) made"
            + (f", about {float((order or {}).get('good_qty') or 0) // 100 * 100:,.0f} booked"
               if one.get("booked") else "")
            for one, order, genealogy, _ in states
        )
        if said != last:
            print(said, flush=True)
            last = said
        if all(ready for *_, ready in states):
            print("  rolling: " + "; ".join(
                f"{one['code']}" + (" completed" if one.get("completed") else "")
                + (f", drew on {one['consumed']}" if one.get("consumed") else "")
                + (f", booked {float(one['booked']):,.0f} or more" if one.get("booked") else "")
                for one in wanted), flush=True)
            return
        time.sleep(POLL_SECONDS)

    raise SystemExit(
        f"the orders never reached the state this scene opens on within "
        f"{float(spec['roll_when_timeout_seconds']):.0f}s. Nothing was filmed. Last seen:\n{last}"
    )


# --------------------------------------------------------- what may be written


def on_loopback(base: str) -> bool:
    """Whether this base URL names a plant on this box, by its address alone.

    A name is read as an address and nothing more: `localhost` and the
    `127.0.0.0/8` and `::1` literals are on this box, and everything else is
    not. A hostname that happens to resolve to a loopback address today is not
    accepted, because the whole point of this rule is that a person reading a
    scene file can see for themselves which plant a take is allowed to change,
    and a name's answer can be different tomorrow.
    """
    host = urllib.parse.urlsplit(base).hostname
    if host is None:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def refuse_to_write_off_this_box(scene: dict) -> None:
    """Refuse a take that may write unless the plant it films is on this box.

    `allow_writes` exists because an operator doing their job is a write — a
    hold is taken under review by somebody deciding to, and a clip of quality
    work with nothing written in it is a tour rather than a story — and the
    plant a take does that on is a scratch plant the take brought up itself.
    The lab plants and the public demo answer on addresses that are not
    loopback, and somebody is watching them. Between "films a plant" and
    "changes a plant somebody else is reading" there is otherwise one line in a
    scene file and a `--base` nobody re-read, so the address decides.
    """
    if not scene["allow_writes"] or on_loopback(scene["base"]):
        return
    raise SystemExit(
        f"this scene sets `allow_writes = true` and films {scene['base']}, which is not on this box. "
        "A take may only write to a plant at a loopback address — a scratch plant it started itself. "
        "Drop `allow_writes`, or point `base` at 127.0.0.1."
    )


# ------------------------------------------------------------------ the take


def ask(url: str, timeout: float = 5.0) -> dict | None:
    """One GET that answers with JSON, or None. Never raises."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as answer:
            return json.load(answer)
    except (OSError, urllib.error.URLError, ValueError):
        return None


def the_plant(base: str) -> tuple[str, str]:
    """What the plant calls itself and which version it reports. It is asked, not told."""
    health = ask(f"{base}/health")
    if not health:
        raise SystemExit(
            f"{base}/health did not answer. This script films a plant that is already running and "
            "starts nothing: bring the plant up yourself, or point --base at one that is up."
        )
    spec = ask(f"{base}/openapi.json") or {}
    return health.get("plant") or "unknown", (spec.get("info") or {}).get("version") or "unknown"


def commit_of_repo() -> str:
    done = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, encoding="utf-8", check=False)
    return done.stdout.strip() or "unknown"


def record(scene: dict, out: Path) -> dict:
    """Film the scene into `out`: the raw video, the stills, the slides. Returns what was made."""
    # Imported here and not at the top: --plan, and every test, works without a browser installed.
    from playwright.sync_api import sync_playwright

    refuse_to_write_off_this_box(scene)
    base = scene["base"].rstrip("/")
    plant, version = the_plant(base)
    badge = scene["badge"].format(version=version, plant=plant)
    writes: list[str] = []

    shutil.rmtree(out / "raw", ignore_errors=True)
    shutil.rmtree(out / "stills", ignore_errors=True)
    shutil.rmtree(out / "slides", ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    width, height = int(scene["width"]), int(scene["height"])

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": int(scene["width"]), "height": int(scene["height"])},
            record_video_dir=str(out / "raw"),
            record_video_size={"width": int(scene["width"]), "height": int(scene["height"])},
        )
        signed_in = context.request.post(
            f"{base}/auth/login",
            data=json.dumps({"code": scene["user"], "password": scene["password"]}),
            headers={"Content-Type": "application/json"},
        )
        if signed_in.status != 200:
            raise SystemExit(
                f"could not sign in to {base} as {scene['user']} (HTTP {signed_in.status}); "
                "set `user` and `password` in the scene, or MES_SCENE_PASSWORD"
            )
        context.add_init_script(overlay_script(badge, bool(scene["captions"])))
        presses: list[tuple[int, int]] = []
        listen_for_presses(context, presses)
        # Cards are answered here, on the plant's own origin, and never reach the plant.
        slides: dict[str, str] = {}
        context.route(f"{base}{SLIDE_PATH}*", lambda route: (
            route.fulfill(status=200, content_type="text/html; charset=utf-8",
                          body=slides.get(route.request.url.rsplit("/", 1)[-1], "no such card"))
        ))

        page = context.new_page()
        # A scene films a plant that is not this script's to change. Anything
        # the page sends that is not a read is written down and fails the take.
        page.on("request", lambda request: (
            writes.append(f"{request.method} {request.url}")
            if request.method not in ("GET", "HEAD") else None
        ))

        stage = BrowserStage(page, out, time.monotonic(), presses, base=base, slides=slides,
                             render=lambda card, path: render_slide(browser, card, path, width, height))
        page.goto(f"{base}/dashboard", wait_until="load")

        scene_with_base = dict(scene)
        scene_with_base["step"] = [
            {**step, "url": base + step["url"]} if step["do"] == "goto" else step
            for step in scene["step"]
        ]
        play(scene_with_base, stage)
        stage.note("end", "end")

        video = Path(page.video.path())
        context.close()
        browser.close()

    if stage.said != script_lines(scene):
        raise SystemExit("the overlay said something other than the scene's `say` lines; the take is not usable")
    if writes and not scene["allow_writes"]:
        raise SystemExit("the page wrote to the plant during the take, which a scene may not do:\n  "
                         + "\n  ".join(writes))
    if stage.corner_faults:
        raise SystemExit("writing of the recording's own landed in the avatar's corner, which stays empty:\n  "
                         + "\n  ".join(stage.corner_faults))

    return {
        "video": video,
        "plant": plant,
        "version": version,
        "badge": badge,
        "marks": stage.marks,
        "said": stage.said,
        "slides": sorted(path.relative_to(out).as_posix() for path in (out / "slides").glob("*.png")),
        "stills": sorted(path.relative_to(out).as_posix() for path in (out / "stills").glob("*.png")),
        "took": stage.marks[-1]["at"] if stage.marks else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Film one scene of a running plant from a scene file, or write the course's index.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("scene", type=Path, nargs="?",
                        help="the scene file, e.g. scripts/scenes/02-spc-on-the-line.toml")
    parser.add_argument("--base", help="the plant to film (default: the scene's own `base`)")
    parser.add_argument("--out", help="the folder to write (default: the scene's own `out`); for a lesson's"
                                      " scene, or with --course, the course tree's root (default: course.toml's `out`)")
    parser.add_argument("--course", action="store_true",
                        help="write course.json and the README at the top of the course tree from"
                             " scripts/course/course.toml and the lesson folders, and film nothing")
    parser.add_argument("--plan", action="store_true",
                        help="print the steps, the planned length and the script, and film nothing")
    parser.add_argument("--keep-raw", action="store_true", help="keep the raw video beside the mp4 and the GIF")
    args = parser.parse_args()

    if args.course:
        course = load_course(REPO / DEFAULTS["course"])
        root = Path(args.out or course["course"]["out"]).expanduser().resolve()
        document = write_course(course, root)
        lessons = [lesson for module in document["modules"] for lesson in module["lessons"]]
        print(f"== {root / 'course.json'}: {len(document['modules'])} modules, {len(lessons)} lessons")
        for status in STATUSES:
            print(f"  {status:9s} {sum(1 for lesson in lessons if lesson['status'] == status)}")
        return
    if args.scene is None:
        parser.error("name a scene file, or pass --course")

    scene = load_scene(args.scene)
    if args.base:
        scene["base"] = args.base
    if os.environ.get("MES_SCENE_PASSWORD"):
        scene["password"] = os.environ["MES_SCENE_PASSWORD"]

    # A lesson's scene films into its folder in the course tree; any other
    # scene into its own `out`, as it always has.
    course = module = lesson = None
    if scene.get("lesson"):
        course = load_course((REPO / scene["course"]) if not Path(scene["course"]).is_absolute()
                             else Path(scene["course"]))
        module, lesson = find_lesson(course, scene["lesson"])
        root = Path(args.out or course["course"]["out"]).expanduser().resolve()
        final = root / lesson["path"]
        name = lesson["slug"]
    else:
        root = None
        final = Path(args.out or scene["out"]).expanduser().resolve()
        name = scene["name"]
    refuse_studios_tree(final)

    budget = float(scene["budget_seconds"])
    print(f"== {scene['title']} — {len(scene['step'])} steps, "
          f"{planned_seconds(scene):.1f} s filmed, {gif_seconds(scene):.1f} s in the GIF "
          f"against a {budget:.0f} s budget", flush=True)
    if lesson:
        print(f"== lesson {lesson['id']} {lesson['title']}: {final} ({scene['take_is']})", flush=True)
    if planned_seconds(scene) > budget:
        raise SystemExit(f"the scene is planned at {planned_seconds(scene):.1f} s of film, "
                         f"over its own {budget:.0f} s budget")

    if args.plan:
        for number, step in enumerate(scene["step"], start=1):
            what = describe_wait(step) if step["do"] == "wait_for" else (
                step.get("line") or step.get("title") or step.get("url") or step.get("selector")
                or step.get("name") or ""
            )
            print(f"{number:3d}  {step['do']:9s} {(step.get('part') or ''):8s} {what}")
        print("\n" + "\n".join(script_lines(scene)))
        return

    for tool in ("ffmpeg",):
        if not shutil.which(tool):
            raise SystemExit(f"no `{tool}` on PATH; it makes the mp4 and the GIF")

    # A take that writes may only write to a plant on this box, and the answer
    # cannot wait until the browser is open: the refusal comes before anything
    # is started, asked or filmed.
    refuse_to_write_off_this_box(scene)

    # A lesson is filmed into a folder beside its own and moved into place
    # whole when every check has passed: a take that fails leaves the folder
    # that was there as it was, and nobody ever reads half a take.
    work = a_folder_beside(final) if lesson else final
    try:
        take(scene, args, work, final, name, course, module, lesson)
        if lesson:
            publish_folder(work, final)
            record_lesson_in_course(course, root, lesson)
            print(f"  folder {final}")
            print(f"  course {root / 'course.json'} (lesson {lesson['id']}: take, status, duration, recorded)")
    finally:
        if lesson and work.exists():
            shutil.rmtree(work, ignore_errors=True)


def take(scene: dict, args, out: Path, final: Path, name: str, course, module, lesson) -> None:
    """One take into `out`: film, make the mp4 and the GIF, check them, write the folder's files."""
    budget = float(scene["budget_seconds"])
    teaser_only = bool(lesson) and scene["take_is"] == "teaser"
    take_number = next_take(final) if lesson else None

    # A scene with a `[plant]` table brings its own plant up and takes it down
    # again, whatever happens to the take in between — a scratch plant left
    # running holds its ports, and the next run of this script would refuse.
    plant_spec = scene.get("plant")
    scratch = None
    try:
        if plant_spec:
            if not on_loopback(scene["base"]):
                raise SystemExit(
                    f"this scene starts its own plant but films {scene['base']}, which is not on this box. "
                    "A scratch plant is reached at a loopback address."
                )
            scratch = start_a_scratch_plant(plant_spec)
            wait_for_the_plant(scene["base"].rstrip("/"))
            if plant_spec["roll_when_order"]:
                print("== waiting for the orders to reach the state this scene opens on", flush=True)
                wait_for_the_orders(scene["base"].rstrip("/"), plant_spec,
                                    scene["user"], scene["password"])
            if plant_spec["roll_when_chart"]:
                print("== waiting for the chart to reach the state this scene opens on", flush=True)
                wait_for_the_shot(scene["base"].rstrip("/"), plant_spec,
                                  scene["user"], scene["password"])

        made = record(scene, out)
    finally:
        if scratch is not None:
            print("\n== taking the scratch plant away", flush=True)
            scratch.stop()

    print("\n== making the mp4 and the GIF", flush=True)
    mp4 = out / (f"{name}.teaser.mp4" if teaser_only else f"{name}.mp4")
    gif = out / f"{name}.gif"
    skip = float(scene["gif_skip_seconds"])
    make_mp4(made["video"], mp4)
    # A lesson's GIF is its teaser: the hook, cut where the next part begins.
    cut = teaser_length(made["marks"], skip) if lesson and not teaser_only else None
    setting, gif_bytes, fps = make_gif(made["video"], gif, skip, cut)

    box = badge_box(int(scene["width"]), int(scene["height"]))
    colours = frame_colours(gif, box)
    missing = frames_without_the_badge(colours)
    film = frame_colours(mp4, box)
    missing_on_film = frames_without_the_badge(film)
    ok = badge_in_every_frame(colours) and badge_in_every_frame(film)
    worst = max(badge_distances(colours) + badge_distances(film), default=255)
    length = len(colours) / fps
    whole_seconds = round(len(film) / 20, 1)

    script = write_script_file(out, name, made["said"])
    marks = marks_document(scene, made, gif, length)
    if say_lines_of(marks["marks"]) != script.read_text(encoding="utf-8").splitlines():
        raise SystemExit("marks.json's `say` lines and the script file disagree; the take is not usable")
    (out / "marks.json").write_text(json.dumps(marks, indent=1), encoding="utf-8")
    recorded = now_stamp()
    files = None
    if lesson:
        files = {"video": None if teaser_only else mp4.name, "teaser": gif.name,
                 **({"teaser_video": mp4.name} if teaser_only else {}),
                 "script": script.name, "marks": "marks.json",
                 "stills": made["stills"], "slides": made["slides"]}
        write_json_atomically(out / "lesson.json", lesson_document(course, module, lesson, scene, {
            "plant": made["plant"], "base": scene["base"], "files": files,
            "duration_seconds": whole_seconds, "take": take_number, "version": made["version"],
            "commit": commit_of_repo(), "scene_file": args.scene.as_posix(), "recorded": recorded,
        }))
    (out / "README.md").write_text(readme(scene, {
        "said": made["said"],
        "plant": made["plant"],
        "version": made["version"],
        "badge": made["badge"],
        "base": scene["base"],
        "commit": commit_of_repo(),
        "scene_file": args.scene.as_posix(),
        "command": f"python {Path(__file__).relative_to(REPO).as_posix()} {args.scene.as_posix()}"
                   + (f" --base {args.base}" if args.base else ""),
        "when": time.strftime("%Y-%m-%d %H:%M %Z"),
        "gif_seconds": length,
        "gif_bytes": gif_bytes,
        "gif_setting": setting,
        "gif_name": gif.name, "mp4_name": mp4.name, "script_name": script.name,
        "slides": made["slides"],
        "lesson_line": (f"Lesson {lesson['id']} of the class, *{lesson['title']}* — take {take_number}"
                        + (", the teaser only: the lesson itself is not filmed yet." if teaser_only else ".")
                        ) if lesson else None,
    }), encoding="utf-8")

    if not args.keep_raw:
        shutil.rmtree(out / "raw", ignore_errors=True)

    print("\n== what this take came to", flush=True)
    print(f"  gif   {gif.name} ({gif_bytes / 1024 / 1024:.1f} MB, {length:.1f} s, {setting})")
    print(f"  mp4   {mp4.name} ({mp4.stat().st_size / 1024 / 1024:.1f} MB, {whole_seconds:.1f} s)")
    print(f"  script {script.name} ({len(made['said'])} lines)")
    if ok:
        print(f"  badge  '{made['badge']}' in {len(colours)} of {len(colours)} GIF frames and"
              f" {len(film)} of {len(film)} mp4 frames"
              f" (the worst frame off by {worst} of a tolerance of {BADGE_TOLERANCE})")
    else:
        at = ", ".join(f"{index} ({index / fps:.1f}s)" for index in missing[:8])
        print(f"  badge  MISSING from {len(missing)} of {len(colours)} GIF frames: {at}"
              f"{' …' if len(missing) > 8 else ''}; from {len(missing_on_film)} of {len(film)} mp4 frames")
    print(f"  watch  mpv --loop {mp4}")

    if not ok:
        raise SystemExit("the badge is not in every frame of the GIF and the mp4; the take cannot be published")
    # The whole take and not the loop: `mpv --loop` plays the mp4, and the mp4
    # is the one a person sits through. Its length is its own frames counted.
    if whole_seconds > budget + 0.5:
        raise SystemExit(f"the take runs {whole_seconds:.1f} s, over the scene's own {budget:.0f} s budget "
                         f"— the GIF is {length:.1f} s of it, and the mp4 is the whole of it")
    if lesson and length > TEASER_SECONDS + 0.5:
        raise SystemExit(f"the teaser runs {length:.1f} s, over the {TEASER_SECONDS} s a teaser may")
    if not lesson and not math.isclose(length, gif_seconds(scene), abs_tol=max(4.0, gif_seconds(scene) * 0.25)):
        print(f"  note   the take ran {length:.1f} s against a plan of {gif_seconds(scene):.1f} s"
              " — a `wait_for` waited, or the plant was slow")


if __name__ == "__main__":
    main()
