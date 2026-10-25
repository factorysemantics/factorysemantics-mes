"""Film one scene of a running plant from a scene file, and write the folder.

    python scripts/record-scene.py scripts/scenes/02-spc-on-the-line.toml
    python scripts/record-scene.py <scene.toml> --base http://127.0.0.1:8110 --out /tmp/take

A scene file is a list of steps — `goto`, `click`, `wait_for`, `say`, `hold`,
`mark` — against a plant that is already running. This file drives a browser
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
  * `marks.json`, the seconds each mark landed at and the planned timings

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
    `[[plant.roll_when_order]]`, orders that have finished or drawn on a lot —
    because a wait inside a scene is filmed, and a clip has forty-five seconds.
  * `allow_writes` — let the page write. Some stories *are* writes: a hold is
    taken under review by somebody deciding to, and a clip of quality work
    with nothing written in it is a tour. This is honoured only when `base` is
    a loopback address and refused everywhere else, before the browser opens,
    because the lab fleet and the public demo answer on this box too.

Two things on screen are this file's and not the product's — three, when a
scene starts its own plant — and the README of every folder it writes says so:

  1. the caption bar along the bottom — the `say` lines, burnt in
  2. the badge at the right-hand end of that bar, which names the version the
     plant itself reports and says in as many words that the plant is simulated
     and the software is pre-alpha

It also hides the floating Assistant and Design buttons, which are the
product's and sit exactly where the caption bar goes.

It is a developer tool. It is not in the wheel, not in CI, and not asked to
run on Windows. It needs `ffmpeg`, and a Python with Playwright and its
Chromium (`pip install -e ".[dev]" && playwright install chromium`).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
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

# The six words a step can be. A seventh is a newer scene file than this
# script, and guessing what it meant is how a recording comes out missing the
# thing it was made to show.
VERBS = ("goto", "click", "wait_for", "say", "hold", "mark")

# The overlay's own geometry, in the viewport's pixels. The badge check reads
# these: it samples a strip of the badge's right-hand padding, where there is
# never any text, in every frame of the finished GIF.
# It rides at the right-hand end of the caption bar rather than over the page:
# a chip over the product's own header reads as the product being broken, and
# it covered the name of the person signed in. In the bar it is plainly part of
# the recording, and it covers nothing.
BADGE_BACKGROUND = (255, 210, 63)  # #ffd23f, the series' yellow
BADGE_BOTTOM = 18
BADGE_RIGHT = 26
BADGE_HEIGHT = 34
BADGE_SAMPLE_INSET = 6  # from the badge's right edge
BADGE_SAMPLE_SIZE = 10  # the square sampled, px
# A flat colour survives GIF quantisation closely but not exactly, and a frame
# is 128 colours chosen from the whole take. 32 per channel is loose enough for
# the palette and far tighter than the gap to any page background.
BADGE_TOLERANCE = 32

# What the GIF may weigh and how it is made smaller until it does. GitHub
# renders an image up to 10 MB and refuses to animate one over it. How long a
# clip may run is a judgement rather than a limit, and it is the scene file's
# own `budget_seconds`.
GIF_LIMIT_BYTES = 10 * 1024 * 1024
GIF_SETTINGS = ((10, 1100), (8, 1100), (8, 1000), (6, 1000), (6, 900), (5, 800))

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
}

# Per-step defaults, in seconds. `settle` is how long a page is left to draw
# and fill itself after a `goto`; `hold` is how long a caption stays up. Both
# are generous: a reader has to find the sentence in a thumbnail, read it, and
# look at what it points to, before it changes.
SETTLE_SECONDS = 3.0
HOLD_SECONDS = 4.0
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

    for number, step in enumerate(steps, start=1):
        where = f"{path}: step {number}"
        verb = step.get("do")
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
            step.setdefault("hold", HOLD_SECONDS)
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
        else:
            total += SMALL_STEP_SECONDS
    return round(total, 2)


def gif_seconds(scene: dict) -> float:
    """The planned length of the published loop: the take, less the head it skips."""
    return round(planned_seconds(scene) - float(scene["gif_skip_seconds"]), 2)


def play(scene: dict, stage) -> None:
    """Walk the steps, handing each to the stage. The one place order lives.

    The stage is anything with these six methods, which is what lets the test
    run a real scene file without a browser in front of it.
    """
    for step in scene["step"]:
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


# --------------------------------------------------------------- the overlay


def overlay_script(badge: str) -> str:
    """The caption bar, the badge, and the two buttons that are hidden.

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
        "#fs-scene-cap{position:fixed!important;left:0!important;right:0!important;bottom:0!important;"
        "z-index:2147483646!important;background:rgba(8,11,17,.94)!important;color:#fff!important;"
        "font:600 25px/1.3 system-ui,-apple-system,Segoe UI,sans-serif!important;"
        "padding:18px 380px 18px 26px!important;min-height:34px!important;letter-spacing:.1px!important;"
        "border-top:2px solid #ffd23f!important;text-align:left!important}"
        f"#fs-scene-badge{{position:fixed!important;bottom:{BADGE_BOTTOM}px!important;right:{BADGE_RIGHT}px!important;"
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

    def __init__(self, page, out: Path, started: float) -> None:
        self.page = page
        self.out = out
        self.started = started
        self.marks: list[dict] = []
        self.said: list[str] = []
        self.caption = ""

    def _at(self) -> float:
        return round(time.monotonic() - self.started, 2)

    def note(self, what: str, **rest: object) -> None:
        mark = {"at": self._at(), "what": what, **rest}
        self.marks.append(mark)
        print(f"{mark['at']:7.2f}  {what}", flush=True)

    def goto(self, url: str, settle: float) -> None:
        # The caption carries itself across the navigation in sessionStorage,
        # drawn before the new page's first paint; nothing to put back here.
        self.page.goto(url, wait_until="load")
        self.note(f"goto {url}")
        self.page.wait_for_timeout(int(settle * 1000))

    def click(self, selector: str) -> None:
        self.page.click(selector)
        self.note(f"click {selector}")
        self.page.wait_for_timeout(int(SMALL_STEP_SECONDS * 1000))

    def wait_for(self, step: dict, timeout: float) -> None:
        waited = time.monotonic()
        self.page.wait_for_function(wait_expression(step), timeout=timeout * 1000)
        self.note(f"waited {time.monotonic() - waited:.1f}s for {describe_wait(step)}")

    def say(self, line: str, hold: float) -> None:
        self.caption = line
        self.page.evaluate("text => window.__sceneSay(text)", line)
        self.said.append(line)
        self.note(f"say: {line}")
        self.page.wait_for_timeout(int(hold * 1000))

    def hold(self, seconds: float) -> None:
        self.note(f"hold {seconds}s")
        self.page.wait_for_timeout(int(seconds * 1000))

    def mark(self, name: str) -> None:
        stills = self.out / "stills"
        stills.mkdir(parents=True, exist_ok=True)
        shot = stills / f"{len(list(stills.glob('*.png'))) + 1:02d}-{name.replace(' ', '-')}.png"
        self.page.screenshot(path=str(shot))
        self.note(f"mark: {name}", still=shot.name)

    def scroll_to(self, selector: str, block: str, offset: int, text: str | None = None) -> None:
        # `text` picks, among everything the selector matches, the first whose
        # words contain it: the row about the cap lot in a list of a hundred
        # issues, which sits inside a box that scrolls on its own. scrollIntoView
        # scrolls that box as well as the page, so the row the caption is about
        # is the row on screen.
        self.page.evaluate(
            """([selector, block, offset, text]) => {
                 const nodes = [...document.querySelectorAll(selector)];
                 const node = text
                   ? nodes.find((n) => (n.innerText || '').includes(text))
                   : nodes[0];
                 if (!node) throw new Error('nothing to scroll to: ' + selector + (text ? ' saying ' + text : ''));
                 node.scrollIntoView({ block, behavior: 'instant' });
                 if (offset) window.scrollBy(0, -offset);
               }""",
            [selector, block, offset, text],
        )
        self.page.wait_for_timeout(int(SCROLL_SECONDS * 1000))


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
    lines = [
        f"# {scene['title']}",
        "",
        f"`{scene['name']}.gif` — {facts['gif_seconds']:.1f} s, "
        f"{facts['gif_bytes'] / 1024 / 1024:.1f} MB, {facts['gif_setting']}. "
        f"Watch it: `mpv --loop {scene['name']}.mp4`",
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
        "- the caption bar along the bottom: the scene file's `say` lines, burnt in",
        f"- the badge at the right-hand end of that bar: `{facts['badge']}`,"
        " in every frame of the GIF, which the recording checks before it writes this page",
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
            lines += [
                f"- **when the camera started**: it waited, off camera, until order `{wanted['code']}` had"
                f" {' and '.join(asked)}, as the plant's own genealogy reported it. The waiting is cut"
                " from the clip.",
            ]
    lines += [
        "",
        "Everything else is the plant's own screens, read live, and every figure on them is the"
        " plant's own.",
    ]
    if scene.get("allow_writes"):
        lines += [
            "",
            "This scene is allowed to write, which is how the operator takes the hold under review:"
            " that click is a real `POST` to a real plant, and the hold really is under review"
            " afterwards. The recording refuses to run at all unless the plant is at a loopback"
            " address.",
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
        f"- `{scene['name']}.gif` — the loop",
        f"- `{scene['name']}.mp4` — the same take, full length and full rate",
        f"- `{scene['name']}.script.txt` — the overlay lines in order, for whoever narrates",
        "- `stills/` — one screenshot per `mark`",
        "- `marks.json` — the seconds every step landed at",
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


def make_gif(raw: Path, gif: Path, skip: float) -> tuple[str, int, int]:
    """The first setting whose GIF fits. Returns how it was made, its bytes, its fps."""
    for fps, width in GIF_SETTINGS:
        run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", str(skip), "-i", str(raw),
             "-filter_complex",
             f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];"
             "[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3",
             str(gif)])
        size = gif.stat().st_size
        print(f"   {fps} fps at {width} wide: {size / 1024 / 1024:.1f} MB", flush=True)
        if size <= GIF_LIMIT_BYTES:
            return f"{fps} fps, {width} wide", size, fps
    raise SystemExit(f"no setting brought the GIF under {GIF_LIMIT_BYTES // 1024 // 1024} MB; the take is too long")


def badge_box(width: int, height: int) -> tuple[float, float, float, float]:
    """Where the badge is sampled, as fractions of the frame: x, y, w, h.

    Fractions and not pixels because the GIF is scaled down from the viewport,
    and a box in pixels would sample the page beside the badge instead.
    """
    right = BADGE_RIGHT + BADGE_SAMPLE_INSET
    x = (width - right - BADGE_SAMPLE_SIZE) / width
    y = (height - BADGE_BOTTOM - BADGE_HEIGHT + (BADGE_HEIGHT - BADGE_SAMPLE_SIZE) / 2) / height
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
    if spec["roll_when_chart"] and "/" not in str(spec["roll_when_chart"]):
        raise SystemExit("[plant]: `roll_when_chart` is `MATERIAL/characteristic`, e.g. FG-BOTTLE/fill_weight")
    orders = spec.setdefault("roll_when_order", [])
    if not isinstance(orders, list):
        raise SystemExit("[plant]: `roll_when_order` is a list of tables — `[[plant.roll_when_order]]`")
    for wanted in orders:
        if not wanted.get("code"):
            raise SystemExit("[plant]: every `roll_when_order` needs the `code` of the order it waits on")
        if not (wanted.get("completed") or wanted.get("consumed")):
            # An order with no condition is already "ready" the moment the plant
            # is up, and the take would roll on an order with nothing in it.
            raise SystemExit(
                f"[plant]: `roll_when_order` {wanted['code']} waits for nothing — "
                "say `completed = true`, `consumed = \"LOT-…\"`, or both"
            )
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
    env = {**os.environ, "FSMES_PLANT_REGISTRY": str(home / "fleet.toml")}
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
    genealogy says went in. Both are read from the plant; nothing is assumed
    from the pack.
    """
    if not order or not genealogy:
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
            for one, order, genealogy, _ in states
        )
        if said != last:
            print(said, flush=True)
            last = said
        if all(ready for *_, ready in states):
            print("  rolling: " + "; ".join(
                f"{one['code']}" + (" completed" if one.get("completed") else "")
                + (f", drew on {one['consumed']}" if one.get("consumed") else "")
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
    # Imported here and not at the top: --plan, and every test, works without a browser installed.
    from playwright.sync_api import sync_playwright

    refuse_to_write_off_this_box(scene)
    base = scene["base"].rstrip("/")
    plant, version = the_plant(base)
    badge = scene["badge"].format(version=version, plant=plant)
    writes: list[str] = []

    shutil.rmtree(out / "raw", ignore_errors=True)
    shutil.rmtree(out / "stills", ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

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
        context.add_init_script(overlay_script(badge))

        page = context.new_page()
        # A scene films a plant that is not this script's to change. Anything
        # the page sends that is not a read is written down and fails the take.
        page.on("request", lambda request: (
            writes.append(f"{request.method} {request.url}")
            if request.method not in ("GET", "HEAD") else None
        ))

        stage = BrowserStage(page, out, time.monotonic())
        page.goto(f"{base}/dashboard", wait_until="load")

        scene_with_base = dict(scene)
        scene_with_base["step"] = [
            {**step, "url": base + step["url"]} if step["do"] == "goto" else step
            for step in scene["step"]
        ]
        play(scene_with_base, stage)
        stage.note("end")

        video = Path(page.video.path())
        context.close()
        browser.close()

    if stage.said != script_lines(scene):
        raise SystemExit("the overlay said something other than the scene's `say` lines; the take is not usable")
    if writes and not scene["allow_writes"]:
        raise SystemExit("the page wrote to the plant during the take, which a scene may not do:\n  "
                         + "\n  ".join(writes))

    return {
        "video": video,
        "plant": plant,
        "version": version,
        "badge": badge,
        "marks": stage.marks,
        "said": stage.said,
        "took": stage.marks[-1]["at"] if stage.marks else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Film one scene of a running plant from a scene file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("scene", type=Path, help="the scene file, e.g. scripts/scenes/02-spc-on-the-line.toml")
    parser.add_argument("--base", help="the plant to film (default: the scene's own `base`)")
    parser.add_argument("--out", help="the folder to write (default: the scene's own `out`)")
    parser.add_argument("--plan", action="store_true",
                        help="print the steps, the planned length and the script, and film nothing")
    parser.add_argument("--keep-raw", action="store_true", help="keep the raw video beside the mp4 and the GIF")
    args = parser.parse_args()

    scene = load_scene(args.scene)
    if args.base:
        scene["base"] = args.base
    if os.environ.get("MES_SCENE_PASSWORD"):
        scene["password"] = os.environ["MES_SCENE_PASSWORD"]
    out = Path(args.out or scene["out"]).expanduser().resolve()

    budget = float(scene["budget_seconds"])
    print(f"== {scene['title']} — {len(scene['step'])} steps, "
          f"{planned_seconds(scene):.1f} s filmed, {gif_seconds(scene):.1f} s in the GIF "
          f"against a {budget:.0f} s budget", flush=True)
    if planned_seconds(scene) > budget:
        raise SystemExit(f"the scene is planned at {planned_seconds(scene):.1f} s of film, "
                         f"over its own {budget:.0f} s budget")

    if args.plan:
        for number, step in enumerate(scene["step"], start=1):
            what = describe_wait(step) if step["do"] == "wait_for" else (
                step.get("line") or step.get("url") or step.get("selector") or step.get("name") or ""
            )
            print(f"{number:3d}  {step['do']:9s} {what}")
        print("\n" + "\n".join(script_lines(scene)))
        return

    for tool in ("ffmpeg",):
        if not shutil.which(tool):
            raise SystemExit(f"no `{tool}` on PATH; it makes the mp4 and the GIF")

    # A take that writes may only write to a plant on this box, and the answer
    # cannot wait until the browser is open: the refusal comes before anything
    # is started, asked or filmed.
    refuse_to_write_off_this_box(scene)

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
    mp4 = out / f"{scene['name']}.mp4"
    gif = out / f"{scene['name']}.gif"
    make_mp4(made["video"], mp4)
    setting, gif_bytes, fps = make_gif(made["video"], gif, float(scene["gif_skip_seconds"]))

    colours = frame_colours(gif, badge_box(int(scene["width"]), int(scene["height"])))
    missing = frames_without_the_badge(colours)
    ok = badge_in_every_frame(colours)
    worst = max(badge_distances(colours), default=255)
    length = len(colours) / fps

    script = write_script_file(out, scene["name"], made["said"])
    (out / "marks.json").write_text(json.dumps({
        "planned_seconds": planned_seconds(scene),
        "took_seconds": made["took"],
        "gif_seconds": round(length, 2),
        "marks": made["marks"],
    }, indent=1), encoding="utf-8")
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
    }), encoding="utf-8")

    if not args.keep_raw:
        shutil.rmtree(out / "raw", ignore_errors=True)

    print("\n== what this take came to", flush=True)
    print(f"  gif   {gif} ({gif_bytes / 1024 / 1024:.1f} MB, {length:.1f} s, {setting})")
    print(f"  mp4   {mp4} ({mp4.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"  script {script} ({len(made['said'])} lines)")
    if ok:
        print(f"  badge  '{made['badge']}' in {len(colours)} of {len(colours)} frames"
              f" (the worst frame off by {worst} of a tolerance of {BADGE_TOLERANCE})")
    else:
        at = ", ".join(f"{index} ({index / fps:.1f}s)" for index in missing[:8])
        print(f"  badge  MISSING from {len(missing)} of {len(colours)} frames: {at}"
              f"{' …' if len(missing) > 8 else ''}")
    print(f"  watch  mpv --loop {mp4}")

    if not ok:
        raise SystemExit("the badge is not in every frame of the GIF; the take cannot be published")
    # The whole take and not the loop. The GIF drops the head of the take, but
    # `mpv --loop` plays the mp4, and the mp4 is the one a person sits through:
    # a take whose GIF came in at 44 s and whose mp4 ran 46.5 s is over budget,
    # and the first version of this check called it fine.
    whole = round(length + float(scene["gif_skip_seconds"]), 1)
    if whole > budget + 0.5:
        raise SystemExit(f"the take runs {whole:.1f} s, over the scene's own {budget:.0f} s budget "
                         f"— the GIF is {length:.1f} s of it, and the mp4 is the whole of it")
    if not math.isclose(length, gif_seconds(scene), abs_tol=max(4.0, gif_seconds(scene) * 0.25)):
        print(f"  note   the take ran {length:.1f} s against a plan of {gif_seconds(scene):.1f} s"
              " — a `wait_for` waited, or the plant was slow")


if __name__ == "__main__":
    main()
