"""Class 4.1 records what the gauge read against the spec and keeps an out-of-spec reading as typed.

*Specifications and quality checks* is the first class of the quality tier: a
card of words (the gauge reads 26.35 mm against a drawing of 24 to 26), the
four words of the class (characteristic, specification, quality check,
non-conformance), and then the product: the plant's specifications on the
Quality screen, the fork's width at 24 to 26 mm; the fork mould's station
screen, where 25.1 mm is typed and recorded in spec and 26.35 mm is typed,
recorded as typed, and raises a non-conformance by itself; then the Quality
screen's chart, the non-conformance, the inspection history and its export.
Then what it refuses: rounding the reading into spec, and a reading from
anybody whose role does not hold the right to record. This file holds the
scene file to what the handoff asks of it. No plant and no browser: the scene
is loaded and walked the way the recorder walks it.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules
from fsmes.services import capabilities

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "course" / "05-01-specifications-and-quality-checks.toml"
PACK = REPO / "labs" / "cutlery"
MACHINE = "FORK1_Mold"
IN_SPEC, OUT_OF_SPEC = "25.1", "26.35"

# What no caption, card or note may carry: a calendar date, a year, a month's
# name. The class is about any plant on any day.
A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene_class_4_1", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def the_forks_width() -> dict:
    """The pack's own specification for the fork's width."""
    specs = json.loads((PACK / "masterdata" / "quality_specs.json").read_text(encoding="utf-8"))
    (width,) = [s for s in specs if s["material"] == "UT-FORK" and s["characteristic"] == "width_mm"]
    return width


def everything_written(scene: dict) -> list[str]:
    """Every sentence a viewer or a reader of the folder will see."""
    written = [scene["title"], *scene["notes"], *scene["honesty"]]
    for step in scene["step"]:
        if step["do"] == "say":
            written.append(step["line"])
            if step.get("label"):
                written.append(step["label"])
        if step["do"] == "slide":
            written += [step["title"], *step["lines"]]
        if step["do"] == "fill":
            written.append(step["text"])
    return written


def said(scene: dict, part: str | None = None) -> list[str]:
    return [s["line"] for s in scene["step"] if s["do"] == "say" and (part is None or s["part"] == part)]


def steps_of(scene: dict, part: str) -> list[dict]:
    return [step for step in scene["step"] if step["part"] == part]


def clicked(scene: dict) -> list[str]:
    return [step["selector"] for step in scene["step"] if step["do"] == "click"]


def test_the_scene_is_a_full_take_of_class_4_1_named_by_its_label_that_runs_four_to_six_minutes(runner, scene):
    assert scene["lesson"] == "4.1" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 240 <= planned <= 360 <= scene["budget_seconds"] <= 390, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_first_folder_of_the_quality_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "05-quality/01-specifications-and-quality-checks"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    assert lesson["knows_before"] == ["2.1"]


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_words_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "the-gauge-reads"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_card_names_the_four_words_of_the_class(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]

    assert len(cards) == 1
    assert [line.split(":")[0] for line in cards[0]["lines"]] == [
        "Characteristic", "Specification", "Quality check", "Non-conformance"]


def test_each_word_of_the_class_is_explained_the_first_time_it_is_said(scene):
    lines = said(scene)

    for word, starts in (
        ("characteristic", "Four words. A characteristic is "),
        ("specification", "A specification gives "),
        ("quality check", "A quality check is "),
        ("non-conformance", "A non-conformance is "),
    ):
        first = next(line for line in lines if word in line.lower())
        assert first.startswith(starts), (word, first)
        assert first in said(scene, "idea")


def test_it_says_the_mes_keeps_two_limits_and_no_target_because_the_pack_has_none(scene):
    width = the_forks_width()
    lines = " ".join(said(scene))

    assert "target" not in width
    assert "this MES keeps no target, only the two limits" in lines
    assert any("no target" in line for line in scene["honesty"])


def test_the_only_page_opened_by_address_is_the_floor_and_everything_else_is_clicked(scene):
    pages = [step for step in scene["step"] if step["do"] == "goto"]
    path = clicked(scene)

    assert [urlparse(step["url"]).path for step in pages] == ["/dashboard"]
    assert pages[0] is steps_of(scene, "screen")[0]
    assert path.count('header nav a:text-is("Quality")') == 2
    assert path.index('header nav a:text-is("Floor")') + 1 == path.index('nav.subnav a:text-is("Station")')
    assert [s["value"] for s in scene["step"] if s["do"] == "choose" and s["selector"] == "#machine"] == [MACHINE]


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    on_screen = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("screen", "refuses")]
    on_cards = [s for s in scene["step"] if s["do"] == "say" and s["part"] not in ("screen", "refuses")]

    assert [s["line"] for s in on_screen if not s.get("at")] == []
    assert [s["line"] for s in on_cards if s.get("at")] == []
    # Rows and inner text are boxed, never labelled: a label there refused
    # three runs of 3.1.
    assert [s["at"] for s in on_screen if s.get("label")] == []


def test_the_beats_are_filmed_in_the_order_the_handoff_asks(scene):
    marks = [step["name"] for step in scene["step"] if step["do"] == "mark" and step["part"] in ("screen", "refuses")]

    assert marks == [
        "a-specification",
        "the-station-quality-card",
        "an-in-spec-reading",
        "an-out-of-spec-reading",
        "it-raised-a-non-conformance",
        "the-chart",
        "the-non-conformance",
        "the-inspection-history",
        "not-rounded",
        "who-may-record",
        "what-it-does-not-do-yet",
    ]


def test_both_readings_are_typed_through_the_station_against_the_packs_fork_width(scene):
    width = the_forks_width()
    typed = [step["text"] for step in scene["step"] if step["do"] == "fill" and step["selector"] == "#q-value"]

    assert typed == [IN_SPEC, OUT_OF_SPEC]
    assert width["min"] <= float(IN_SPEC) <= width["max"] < float(OUT_OF_SPEC)
    assert [s["value"] for s in scene["step"] if s["do"] == "choose" and s["selector"] == "#q-char"] == ["width_mm"]
    assert any(s.get("text") == f"Spec {width['min']:g} to {width['max']:g} mm." for s in scene["step"])
    assert clicked(scene).count("#q-submit") == 2


def test_the_out_of_spec_reading_is_waited_for_as_typed_and_its_non_conformance_as_raised(scene):
    js = " ".join(step.get("js", "") for step in scene["step"] if step["do"] == "wait_for")

    assert f"{OUT_OF_SPEC} was outside the specification" in js
    assert f"width_mm={OUT_OF_SPEC}mm" in js
    assert any(s.get("text") == f"Recorded {IN_SPEC} — in spec." for s in scene["step"])


def test_the_take_rolls_before_the_control_chart_has_enough_points_to_judge_the_reading(scene):
    # The pack inspects every specification every fifteen minutes of the line's
    # clock; at ten times, that is one fork width every ninety seconds. The
    # order's count is the clock the camera waits on.
    assert scene["plant"]["speed"] == 10
    assert scene["plant"]["roll_when_order"] == [{"code": "WO-FORK1-0001", "booked": 1800}]
    assert any("fewer than the twelve" in note for note in scene["notes"])


def test_what_it_refuses_is_rounding_and_a_reading_from_a_role_without_the_right(scene):
    lines = " ".join(said(scene, "refuses"))

    assert "did not round twenty-six point three five down to twenty-six" in lines
    assert "an operator, a supervisor, or a quality inspector" in lines
    assert "A viewer or an analyst" in lines
    # The roles it names are the built-in ones that hold the right, and the
    # two it names as without it do not.
    holds = {code for code, role in capabilities.BUILTIN_ROLES.items() if "quality.record" in role["capabilities"]}
    assert {"operator", "supervisor", "quality_inspector"} <= holds
    assert not {"viewer", "analyst"} & holds
    assert any(step.get("value") == "fail" and step["selector"] == "#h-result" for step in steps_of(scene, "refuses"))


def test_the_honest_limits_are_said_in_the_films_own_lines(scene):
    lines = " ".join(said(scene))
    honesty = " ".join(scene["honesty"]).lower()

    assert "a simulated plant, replaying a recorded line at ten times the clock" in lines
    assert "The station does not ask which gauge took a reading" in lines
    assert "today it takes one decimal only" in lines
    assert "simulated plant" in honesty and "ten times wall clock" in honesty
    assert "said, not filmed" in honesty


def test_no_screen_of_people_is_filmed_and_admin_is_not_opened(scene):
    urls = [step["url"] for step in scene["step"] if step["do"] == "goto"]
    path = " ".join(clicked(scene))

    assert not [url for url in urls if "people" in url or "/admin" in url]
    assert "Setup" not in path and "Admin" not in path


def test_the_you_now_know_card_says_the_classs_own_outcomes_word_for_word(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])
    know = [step for step in steps_of(scene, "know") if step["do"] == "slide"]

    assert len(lesson["outcomes"]) == 3
    assert len(know) == 1 and know[0]["lines"] == lesson["outcomes"]


def test_every_page_it_visits_is_a_screen_the_app_serves(scene):
    served = [page.path for module in modules.REGISTRY for page in module.pages]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in served]
    visited = {urlparse(step["url"]).path for step in scene["step"] if step["do"] == "goto"}
    visited |= {"/dashboard/quality", "/dashboard/station"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_note_or_typed_word_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_and_never_on_one_somebody_else_runs(runner, scene):
    base = urlparse(scene["base"])
    ports = runner.ports_of_pack(PACK)

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert base.hostname == "127.0.0.1" and base.port == 8040
    assert not set(ports) & set(runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE)
    assert scene["allow_writes"] is True


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill", "choose"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 3 and "terminal" not in handed
