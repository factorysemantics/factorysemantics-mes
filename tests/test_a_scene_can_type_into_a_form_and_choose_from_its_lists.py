"""A scene can fill a form in: type into a field and choose an option of a list.

Class 1.3 defines a routing in the Admin form, so the recorder needs two more
words than click: `fill`, which types a field's text a key at a time, and
`choose`, which picks an option of a `<select>`. Neither writes; the click on
the form's button does, and that still needs `allow_writes`. The product's
message after a save is lifted above the caption bar, which otherwise covers
it. No plant and no browser here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("record_scene_fill_choose", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_scene(tmp_path: Path, steps: str) -> Path:
    path = tmp_path / "scene.toml"
    path.write_text('name = "form"\nbase = "http://127.0.0.1:8040"\n' + steps, encoding="utf-8")
    return path


def test_fill_and_choose_are_words_a_step_can_be(runner):
    assert "fill" in runner.VERBS and "choose" in runner.VERBS


def test_the_stage_is_handed_the_field_and_its_text_and_the_list_and_its_option(runner, tmp_path):
    scene = runner.load_scene(write_scene(tmp_path, '''
[[step]]
do = "fill"
selector = "#code"
text = "RT-PALLET"

[[step]]
do = "choose"
selector = "#material"
value = "PALLET"
'''))
    handed = []

    class Stage:
        def fill(self, selector, text):
            handed.append(("fill", selector, text))

        def choose(self, selector, value):
            handed.append(("choose", selector, value))

    runner.play(scene, Stage())

    assert handed == [("fill", "#code", "RT-PALLET"), ("choose", "#material", "PALLET")]


def test_a_fill_without_its_text_or_a_choose_without_its_option_is_refused_before_filming(runner, tmp_path):
    with pytest.raises(SystemExit, match="needs a `selector` and the `text`"):
        runner.load_scene(write_scene(tmp_path, '[[step]]\ndo = "fill"\nselector = "#code"\n'))
    with pytest.raises(SystemExit, match="the option's `value`"):
        runner.load_scene(write_scene(tmp_path, '[[step]]\ndo = "choose"\nselector = "#material"\n'))


def test_typing_takes_time_in_the_plan_by_the_length_of_what_is_typed(runner, tmp_path):
    fill = '[[step]]\ndo = "fill"\nselector = "#a"\ntext = "{}"\n'
    short = runner.load_scene(write_scene(tmp_path, fill.format("RT")))
    long = runner.load_scene(write_scene(tmp_path, fill.format("RT-PALLET-LINE-ONE")))

    assert runner.planned_seconds(long) - runner.planned_seconds(short) == pytest.approx(
        16 * runner.TYPE_DELAY_SECONDS)


def test_the_products_message_after_a_save_is_drawn_above_the_caption_bar(runner):
    overlay = runner.overlay_script("badge")

    assert f".toast{{bottom:{runner.TOAST_LIFT}px!important}}" in overlay
    # Above two lines of caption: 18 px padding, two lines of 25 px at 1.3, the border.
    assert runner.TOAST_LIFT > 18 * 2 + 2 * 25 * 1.3 + 2
