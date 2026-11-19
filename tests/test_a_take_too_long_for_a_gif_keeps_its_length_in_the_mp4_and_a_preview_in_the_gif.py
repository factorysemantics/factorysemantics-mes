"""A take too long for a GIF keeps all of itself in the mp4 and a preview in the GIF.

A film's screen take runs minutes. GitHub animates a GIF of ten million bytes
or less, and a two-minute take at the recorder's smallest setting already
weighs half again that, so the recorder refused the take as "too long" even
though the mp4 — the thing a film is cut from — was fine. A scene that is not a
lesson may now say `gif_seconds`: the GIF shows that many seconds from the
start, and the mp4 stays the whole take, held to the scene's own budget as
before.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
FILM = REPO / "scripts" / "scenes" / "film"

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg on this machine")


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scene_file(tmp_path: Path, top: str) -> Path:
    path = tmp_path / "scene.toml"
    path.write_text(f'name = "long"\n{top}\n[[step]]\ndo = "hold"\nseconds = 1.0\n', encoding="utf-8")
    return path


def test_a_scene_says_nothing_and_its_gif_is_the_whole_take_as_before(runner, tmp_path):
    assert runner.load_scene(scene_file(tmp_path, ""))["gif_seconds"] is None


def test_a_scene_that_names_its_gif_seconds_keeps_them(runner, tmp_path):
    assert runner.load_scene(scene_file(tmp_path, "gif_seconds = 40.0"))["gif_seconds"] == 40.0


@pytest.mark.parametrize("value", ["0", "-5", "true", '"forty"'])
def test_gif_seconds_that_are_not_a_positive_number_are_refused(runner, tmp_path, value):
    with pytest.raises(SystemExit, match="gif_seconds"):
        runner.load_scene(scene_file(tmp_path, f"gif_seconds = {value}"))


def test_a_lesson_cannot_name_gif_seconds_because_its_gif_is_its_teaser(runner, tmp_path):
    with pytest.raises(SystemExit, match="teaser"):
        runner.load_scene(scene_file(tmp_path, 'lesson = "0.1"\ngif_seconds = 20.0'))


def test_every_film_scene_names_a_preview_so_its_gif_fits(runner):
    films = sorted(FILM.glob("*.toml"))
    assert films, f"no scene in {FILM}"
    for path in films:
        assert runner.load_scene(path)["gif_seconds"] is not None, f"{path.name} has no `gif_seconds`"


@needs_ffmpeg
def test_the_gif_of_a_long_film_stops_where_gif_seconds_says(runner, tmp_path):
    raw = tmp_path / "raw.webm"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=0x101418:s=320x180:d=6:r=20", str(raw)],
        check=True, capture_output=True,
    )
    gif = tmp_path / "long.gif"

    _, _, fps = runner.make_gif(raw, gif, skip=1.0, length=2.0)

    frames = runner.frame_colours(gif, (0.0, 0.0, 0.1, 0.1))
    assert len(frames) / fps == pytest.approx(2.0, abs=0.2), f"{len(frames)} frames at {fps} fps"
