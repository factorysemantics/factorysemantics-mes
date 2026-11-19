"""A scene's `say` can point at the thing its line is about, and its typing can be read.

Scott, 2026-11-15, on the first five classes: "Just the screen doesn't explain
anything." So a `say` may carry `at` (the selector of the thing), `label` (a
few words on the box's tab) and `style` (`box`, the default, or
`spotlight`), and a scene may set `type_delay_seconds`, the pause between
typed keys. These are the scene file's half: what the recorder accepts,
refuses, and hands the stage. No plant and no browser here; the browser's
half is in `test_the_recorder_draws_a_cursor_and_a_box_a_viewer_can_follow.py`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("record_scene_pointer", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_scene(tmp_path: Path, steps: str, head: str = "") -> Path:
    path = tmp_path / "scene.toml"
    path.write_text('name = "pointer"\nbase = "http://127.0.0.1:8040"\n' + head + steps, encoding="utf-8")
    return path


SAY_AT = '''
[[step]]
do = "say"
line = "Its cost center comes from the line."
at = "#cost-center"
label = "Cost center: from the line"
'''


def test_a_say_accepts_at_label_and_style_and_a_box_is_the_default_style(runner, tmp_path):
    scene = runner.load_scene(write_scene(tmp_path, SAY_AT + '''
[[step]]
do = "say"
line = "The rest is dimmed."
at = "#oee"
style = "spotlight"
'''))
    first, second = scene["step"]

    assert (first["at"], first["label"], first["style"]) == ("#cost-center", "Cost center: from the line", "box")
    assert (second["at"], second["style"]) == ("#oee", "spotlight")


def test_a_style_the_recorder_does_not_draw_is_refused_before_filming(runner, tmp_path):
    with pytest.raises(SystemExit, match=r"step 1: `style = 'arrow'` is not one of box, spotlight"):
        runner.load_scene(write_scene(tmp_path, SAY_AT + 'style = "arrow"\n'))


def test_a_label_or_a_style_with_nothing_to_point_at_is_refused(runner, tmp_path):
    for key, value in (("label", '"Cost center"'), ("style", '"box"')):
        with pytest.raises(SystemExit, match=f"`{key}` belongs to a `say` that points `at` something"):
            runner.load_scene(write_scene(tmp_path, f'[[step]]\ndo = "say"\nline = "Hello."\n{key} = {value}\n'))


def test_an_empty_at_is_refused(runner, tmp_path):
    with pytest.raises(SystemExit, match="`at` is the selector"):
        runner.load_scene(write_scene(tmp_path, '[[step]]\ndo = "say"\nline = "Hello."\nat = " "\n'))


def test_the_stage_is_handed_the_pointer_and_a_say_without_one_is_handed_what_it_always_was(runner, tmp_path):
    scene = runner.load_scene(write_scene(tmp_path, SAY_AT + '[[step]]\ndo = "say"\nline = "Plain."\n'))
    handed = []

    class Stage:
        def say(self, line, hold, **pointer):
            handed.append((line, pointer))

    runner.play(scene, Stage())

    assert handed == [
        ("Its cost center comes from the line.",
         {"at": "#cost-center", "label": "Cost center: from the line", "style": "box"}),
        ("Plain.", {}),
    ]


def test_a_stage_that_knows_only_line_and_hold_still_plays_a_scene_without_pointers(runner, tmp_path):
    scene = runner.load_scene(write_scene(tmp_path, '[[step]]\ndo = "say"\nline = "Plain."\n'))
    said = []

    class OldStage:
        def say(self, line, hold):
            said.append(line)

    runner.play(scene, OldStage())

    assert said == ["Plain."]


def test_typing_is_about_twelve_keys_a_second_unless_the_scene_says_slower(runner, tmp_path):
    fill = '[[step]]\ndo = "fill"\nselector = "#a"\ntext = "RT-PALLET-LINE"\n'
    default = runner.load_scene(write_scene(tmp_path, fill))
    slower = runner.load_scene(write_scene(tmp_path, fill, head="type_delay_seconds = 0.15\n"))

    assert default["type_delay_seconds"] == runner.TYPE_DELAY_SECONDS >= 1 / 12
    assert slower["type_delay_seconds"] == 0.15
    assert runner.planned_seconds(slower) - runner.planned_seconds(default) == pytest.approx(14 * (0.15 - 0.085))


def test_typing_faster_than_the_eye_follows_is_refused(runner, tmp_path):
    for value in ("0.01", '"fast"', "true"):
        with pytest.raises(SystemExit, match="`type_delay_seconds` is the seconds between typed keys"):
            runner.load_scene(write_scene(tmp_path, '[[step]]\ndo = "hold"\n', head=f"type_delay_seconds = {value}\n"))


def test_a_press_is_planned_with_the_cursors_glide_to_it(runner, tmp_path):
    click = runner.load_scene(write_scene(tmp_path, '[[step]]\ndo = "click"\nselector = "#go"\n'))
    hold = runner.load_scene(write_scene(tmp_path, '[[step]]\ndo = "hold"\nseconds = 0\n'))

    assert runner.planned_seconds(click) - runner.planned_seconds(hold) == pytest.approx(
        runner.SMALL_STEP_SECONDS + runner.CURSOR_GLIDE_SECONDS + runner.CURSOR_REST_SECONDS)


def test_every_scene_in_the_repo_still_loads_and_points_at_nothing_it_did_not_before(runner):
    scenes = sorted((REPO / "scripts" / "scenes").rglob("*.toml"))
    assert scenes, "the scenes are where they were"
    for path in scenes:
        scene = runner.load_scene(path)
        for step in scene["step"]:
            assert "at" not in step or step["do"] == "say", f"{path.name}: {step}"
    print(f"{len(scenes)} scenes load")


def test_the_label_on_a_box_is_held_out_of_the_avatars_corner_like_the_caption(runner):
    assert "'fs-scene-callout-label'" in runner.CORNER_SCRIPT


def test_a_thing_the_page_replaced_while_it_was_scrolled_to_is_asked_for_again_and_a_real_fault_is_not(runner):
    """The orders table redraws on a timer; the third take of class 0.2 stopped on its row, "not attached"."""

    class Row:
        def __init__(self, faults):
            self.faults, self.asked = list(faults), 0

        def scroll_into_view_if_needed(self):
            self.asked += 1
            if self.faults:
                raise RuntimeError(self.faults.pop(0))

    redrawn = Row(["Element is not attached to the DOM"] * 2)
    runner.scroll_onto_frame(redrawn)
    assert redrawn.asked == 3

    with pytest.raises(RuntimeError, match="Timeout"):
        runner.scroll_onto_frame(Row(["Timeout 30000ms exceeded"]))
    with pytest.raises(RuntimeError, match="not attached"):
        runner.scroll_onto_frame(Row(["Element is not attached to the DOM"] * 9), tries=3)
