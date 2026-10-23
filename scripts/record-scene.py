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
story and brings up its own two-node plant to film it. This one films a plant
somebody else started and **never starts, stops or writes to anything**: a take
that needed the lab plant restarted would not be a recording of the lab plant.
Any request the page makes that is not a GET (the one sign-in aside) fails the
take rather than being filmed, because these scenes are shot against plants
that are not ours to change.

Two things on screen are this file's and not the product's, and the README of
every folder it writes says so:

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
import json
import math
import os
import shutil
import subprocess
import time
import tomllib
import urllib.error
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
            stage.scroll_to(step["scroll_to"], step.get("scroll_block", "center"), int(step.get("scroll_offset", 0)))
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

    def scroll_to(self, selector: str, block: str, offset: int) -> None:
        self.page.evaluate(
            """([selector, block, offset]) => {
                 const node = document.querySelector(selector);
                 if (!node) throw new Error('nothing to scroll to: ' + selector);
                 node.scrollIntoView({ block, behavior: 'instant' });
                 if (offset) window.scrollBy(0, -offset);
               }""",
            [selector, block, offset],
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
        "",
        "Everything else is the plant's own screens, read live. Nothing was started, stopped or"
        " written to in order to make this.",
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
    if gif_seconds(scene) > budget:
        raise SystemExit(f"the scene is planned at {gif_seconds(scene):.1f} s, over its own {budget:.0f} s budget")

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

    made = record(scene, out)

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
    if length > budget + 0.5:
        raise SystemExit(f"the GIF runs {length:.1f} s, over the scene's own {budget:.0f} s budget")
    if not math.isclose(length, gif_seconds(scene), abs_tol=max(4.0, gif_seconds(scene) * 0.25)):
        print(f"  note   the take ran {length:.1f} s against a plan of {gif_seconds(scene):.1f} s"
              " — a `wait_for` waited, or the plant was slow")


if __name__ == "__main__":
    main()
