"""The badge is in every frame, and the loop is short enough to be watched.

Two promises are made about every recording in this series, and both are the
kind nobody notices breaking.

The first is the badge: "pre-alpha · simulated plant · v0.4.0", in the corner,
for the whole take. A GIF gets screenshotted, cropped, and reposted by people
who never read the sentence under it, and a single frame without the badge is
the frame that goes around on its own looking like a product claim. So the
runner samples a patch of the badge in every frame of the finished GIF, and
these tests prove that check can fail: a film with the badge passes, the same
film without it fails, and it names the frames.

The second is the length. Scott asked for clips of forty-five seconds or less,
and a scene file is where that is decided — long before anybody waits on a
browser. The length is read off the scene, and compared with the budget the
scene itself states.

The frame checks that need `ffmpeg` make their own two-second film with a
coloured box where the badge goes and read it back; everything else is
arithmetic on a scene file and runs anywhere.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENES = REPO / "scripts" / "scenes"
SCENE = SCENES / "02-spc-on-the-line.toml"

#: What Scott asked for, and the longest any scene in the series may be.
THE_LONGEST_A_CLIP_MAY_BE = 45.0
#: What GitHub will animate rather than show as a still: ten million bytes, decimal.
THE_HEAVIEST_A_GIF_MAY_BE = 10_000_000

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg on this machine")


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ the clip


def test_every_scene_in_the_series_plans_a_clip_of_forty_five_seconds_or_less(runner):
    scenes = sorted(SCENES.glob("*.toml"))

    assert scenes, "no scene files at all"
    for path in scenes:
        scene = runner.load_scene(path)
        # The whole take, not the loop: the GIF skips the head of it, but the
        # mp4 is what `mpv --loop` plays and what the budget is about.
        planned = runner.planned_seconds(scene)
        assert scene["budget_seconds"] <= THE_LONGEST_A_CLIP_MAY_BE, path.name
        assert planned <= scene["budget_seconds"], f"{path.name}: {planned}s planned, {scene['budget_seconds']}s budget"
    print(f"{len(scenes)} scene file(s), all inside the budget: "
          + ", ".join(f"{path.stem} {runner.planned_seconds(runner.load_scene(path))}s" for path in scenes))


def test_the_published_loop_skips_the_seconds_before_the_first_sentence(runner):
    """A loop that opens on a page with no caption on it opens on nothing."""
    scene = runner.load_scene(SCENE)
    first_settle = next(step for step in scene["step"] if step["do"] == "goto")["settle"]

    assert 0 < scene["gif_skip_seconds"] <= first_settle
    assert runner.gif_seconds(scene) == pytest.approx(runner.planned_seconds(scene) - scene["gif_skip_seconds"])


# ----------------------------------------------------------------- the badge


def test_the_badge_names_the_version_the_plant_itself_reported(runner):
    """Never a version typed into the scene file: the plant is asked."""
    scene = runner.load_scene(SCENE)

    assert "{version}" in scene["badge"]
    assert "pre-alpha" in scene["badge"]
    assert "simulated" in scene["badge"]
    assert scene["badge"].format(version="0.4.0") == "pre-alpha · simulated plant · v0.4.0"


def test_the_patch_that_is_sampled_is_inside_the_badge_and_not_the_page_beside_it(runner):
    width, height = 1280, 720
    x, y, w, h = runner.badge_box(width, height)

    left, top = x * width, y * height
    assert left + w * width <= width - runner.BADGE_RIGHT, "sampling past the badge's right edge"
    assert left >= width - runner.BADGE_RIGHT - 200, "sampling away off to the left of any badge"
    assert top >= height - runner.BADGE_BOTTOM - runner.BADGE_HEIGHT
    assert top + h * height <= height - runner.BADGE_BOTTOM
    # Scaled down to the GIF's width, the patch is still several pixels across.
    assert w * 900 >= 4 and h * 500 >= 4


def test_a_film_with_the_badge_passes_and_the_same_film_without_it_does_not(runner):
    yellow = runner.BADGE_BACKGROUND
    page = (17, 20, 24)

    assert runner.badge_in_every_frame([yellow] * 40)
    assert runner.frames_without_the_badge([yellow] * 40) == []

    gone = [yellow] * 40
    gone[12] = page
    gone[13] = page
    assert not runner.badge_in_every_frame(gone)
    assert runner.frames_without_the_badge(gone) == [12, 13]


def test_a_film_with_no_frames_at_all_is_not_a_film_that_passes(runner):
    assert not runner.badge_in_every_frame([])


def test_the_palette_a_gif_is_quantised_to_is_allowed_to_shift_the_yellow_a_little(runner):
    """Within tolerance, a near-miss is the palette. Outside it, the badge is gone."""
    near = (251, 207, 70)
    far = (255, 160, 63)

    assert runner.badge_in_every_frame([near] * 10)
    assert not runner.badge_in_every_frame([far] * 10)
    assert max(runner.badge_distances([near] * 10)) <= runner.BADGE_TOLERANCE


@needs_ffmpeg
@pytest.mark.parametrize("with_badge", [True, False])
def test_the_frame_sampler_reads_a_real_film_and_can_tell_the_two_apart(runner, tmp_path, with_badge):
    """Two seconds of film, one with a box where the badge goes and one without.

    The arithmetic above is only worth as much as the geometry it is handed, and
    the geometry is only right if ffmpeg crops where this says it does.
    """
    width, height = 1280, 720
    film = tmp_path / f"{'with' if with_badge else 'without'}.gif"
    red, green, blue = runner.BADGE_BACKGROUND
    box = (
        f",drawbox=x={width - runner.BADGE_RIGHT - 300}:y={height - runner.BADGE_BOTTOM - runner.BADGE_HEIGHT}"
        f":w=300:h={runner.BADGE_HEIGHT}:color=0x{red:02x}{green:02x}{blue:02x}@1:t=fill"
        if with_badge
        else ""
    )
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=0x101418:s={width}x{height}:d=2:r=10",
         "-vf", f"format=rgb24{box},scale=1100:-1", str(film)],
        check=True, capture_output=True,
    )

    colours = runner.frame_colours(film, runner.badge_box(width, height))

    assert len(colours) == 20, "two seconds at ten frames a second"
    assert runner.badge_in_every_frame(colours) is with_badge, colours[0]


# ---------------------------------------------------------------- the folder


def test_the_folder_says_which_plant_and_which_version_it_was_filmed_from(runner):
    """A recording nobody can date is a recording nobody can trust."""
    scene = runner.load_scene(SCENE)

    page = runner.readme(scene, {
        "said": ["the first sentence", "the second sentence"],
        "plant": "bottling",
        "version": "0.4.0",
        "badge": "pre-alpha · simulated plant · v0.4.0",
        "base": "http://a.plant:8110",
        "commit": "148620b2",
        "scene_file": "scripts/scenes/02-spc-on-the-line.toml",
        "command": "python scripts/record-scene.py scripts/scenes/02-spc-on-the-line.toml",
        "when": "2026-10-23 12:40 CDT",
        "gif_seconds": 35.2,
        "gif_bytes": 7_900_000,
        "gif_setting": "10 fps, 1100 wide",
    })

    assert "**bottling**" in page
    assert "v0.4.0" in page
    assert "148620b2" in page
    assert "35.2 s" in page and "7.5 MB" in page
    assert "the first sentence" in page and "the second sentence" in page
    assert "caption bar" in page and "badge" in page, "the two things on screen that are not the product's"
    assert "mpv --loop" in page


def test_the_folder_says_the_plant_was_this_takes_own_and_what_was_written_to_it(runner):
    """A take that started a plant and wrote to it says both, on the page beside the GIF.

    This script used to promise, on every page it wrote, that nothing had been
    started, stopped or written to. That is still true of most scenes and is no
    longer true of this one: it builds its own plant, runs the line fast, and
    an operator takes a hold under review on camera. A page that still carried
    the old sentence would be the recording lying about itself, which is the
    one thing the badge and the script file exist to prevent.
    """
    scene = runner.load_scene(SCENE)
    page = runner.readme(scene, {
        "said": ["the first sentence"],
        "plant": "bottling",
        "version": "0.4.1",
        "badge": "pre-alpha · simulated plant · v0.4.1",
        "base": "http://127.0.0.1:8010",
        "commit": "e63138fc",
        "scene_file": "scripts/scenes/02-spc-on-the-line.toml",
        "command": "python scripts/record-scene.py scripts/scenes/02-spc-on-the-line.toml",
        "when": "2026-10-23 15:40 CDT",
        "gif_seconds": 35.2,
        "gif_bytes": 7_900_000,
        "gif_setting": "10 fps, 1100 wide",
    })

    assert "Nothing was started, stopped or written to" not in page, (
        "this scene starts a plant and writes to it; the old promise would be a lie"
    )
    assert scene["plant"]["pack"] in page, "the page names the pack the plant was built from"
    assert "20x" in page, "and the speed the line was replayed at"
    assert "stopped and deleted" in page, "and that the plant was taken away afterwards"
    assert "under review" in page and "loopback" in page, (
        "and what was written, and the rule that says where it could be written"
    )


def test_a_gif_over_the_weight_github_will_animate_is_not_published(runner):
    """The ladder gets smaller, and the last rung still has to fit."""
    assert runner.GIF_LIMIT_BYTES == THE_HEAVIEST_A_GIF_MAY_BE
    rates = [fps for fps, _ in runner.GIF_SETTINGS]
    widths = [width for _, width in runner.GIF_SETTINGS]

    assert rates == sorted(rates, reverse=True)
    assert widths == sorted(widths, reverse=True)
    assert rates[0] <= 10, "a screen recording of captions does not need more"
