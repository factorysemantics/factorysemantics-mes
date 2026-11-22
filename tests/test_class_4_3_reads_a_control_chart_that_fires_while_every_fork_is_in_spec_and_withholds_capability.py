"""Class 4.3 reads a control chart that fires while every fork is in spec, and withholds capability.

*SPC - the chart notices before people do* is the third class of the quality
tier: the fork-length chart in the plant's first minutes, with too few readings
for limits or a capability figure; a card of the words (specification limits,
control limits, a rule, Cp and Cpk); then the fork-width chart the way the
product draws it - its limits, the verdict in control and capable, a rule
firing on a fork still inside the specification, that fork's own panel with
the mould's process values just before, the hold the rule raised, the same
hold on the Quality page - and what it refuses: capability while the chart is
out of control. This file holds the scene file to what the handoff asks of it.
No plant and no browser: the scene is loaded and walked the way the recorder
walks it.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from fsmes import modules

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "course" / "05-03-spc-the-chart-notices-first.toml"
HOT_MOULD = REPO / "labs" / "cutlery" / "scenes" / "hot-mould.json"
MACHINE = "FORK1_Mold"
# The one line over the screen that is about nothing drawn there: the plant
# itself is simulated.
POINTS_AT_NOTHING_ON_SCREEN = {
    "This was a simulated plant, sped up ten times, with its fault written in. "
    "A real plant's charts are rarely this tidy."
}

# The lab fleet, the public demo and the stock demo ports: a class is never
# filmed on a plant somebody else is looking at.
NOT_OURS = {8090, 8110, 8120, 8140, 9030, 5433, 5434, 4944, 4945}

# What no caption, card or note may carry: a calendar date, a year, a month's
# name. The class is about any plant on any day.
A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)


@pytest.fixture(scope="module")
def runner():
    """`scripts/record-scene.py`, imported by path because of the hyphen."""
    spec = importlib.util.spec_from_file_location("record_scene_class_4_3", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


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
    return written


def said(scene: dict, part: str | None = None) -> list[str]:
    return [s["line"] for s in scene["step"] if s["do"] == "say" and (part is None or s["part"] == part)]


def steps_of(scene: dict, part: str) -> list[dict]:
    return [step for step in scene["step"] if step["part"] == part]


def first_index(scene: dict, test) -> int:
    return next(i for i, step in enumerate(scene["step"]) if test(step))


def test_the_scene_is_a_full_take_of_class_4_3_that_runs_four_to_six_minutes(runner, scene):
    assert scene["lesson"] == "4.3" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 240 <= planned <= scene["budget_seconds"] <= 400, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_spc_folder_of_the_quality_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "05-quality/03-spc-the-chart-notices-first"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    assert lesson["knows_before"] == ["4.1"]


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_the_chart_with_too_few_readings_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")
    lines = " ".join(said(scene, "hook"))

    assert hook[0]["do"] == "goto" and "length_mm" in hook[0]["url"]
    assert any("control limits need at least" in step.get("js", "") for step in hook)
    assert "withheld" in lines
    assert [step["name"] for step in hook if step["do"] == "mark"] == ["too-few-readings"]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 45


def test_the_idea_card_names_specification_limits_control_limits_a_rule_and_capability(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]

    assert len(cards) == 1
    assert [line.split(":")[0] for line in cards[0]["lines"]] == [
        "Specification limits",
        "Control limits",
        "A rule",
        "Cp, Cpk",
    ]


def test_each_word_of_the_class_is_explained_on_the_card_in_one_sentence(scene):
    lines = said(scene, "idea")

    for starts in (
        "SPC is short for statistical process control",
        "Specification limits are what the customer allows",
        "Control limits are what the process does",
        "A rule is a pattern in the readings that chance rarely makes",
    ):
        assert any(line.startswith(starts) for line in lines), starts
    (cp,) = [line for line in lines if line.startswith("Cp compares")]
    # Cp and Cpk, one sentence each.
    assert cp.count(".") == 2 and "Cpk" in cp.split(".")[1]


def test_the_rule_is_said_to_fire_in_spec_only_after_the_film_has_checked_it(scene):
    claim = first_index(scene, lambda s: s["do"] == "say" and s["line"].startswith("And that fork is still inside"))
    guard = scene["step"][claim - 1]

    assert guard["do"] == "wait_for" and float(guard["timeout"]) <= 5
    assert "circle.spc-flag[data-series=" in guard["js"] and "individuals" in guard["js"]
    assert "v > 24 && v < 26" in guard["js"]


def test_the_fork_it_clicks_is_the_one_the_rule_fired_on_and_its_panel_names_the_mould(scene):
    checked = first_index(scene, lambda s: s["do"] == "say" and s["line"].startswith("And that fork is still inside"))
    clicks = [i for i, s in enumerate(scene["step"]) if s["do"] == "click" and "spc-flag" in s["selector"]]

    # Clicked straight after the check, before the chart's next redraw can
    # move the first pink dot onto another fork.
    assert len(clicks) == 1 and clicks[0] > checked
    between = scene["step"][checked + 1 : clicks[0]]
    assert not [s for s in between if s["do"] in ("goto", "choose", "click", "wait_for")]
    assert any(MACHINE in s.get("js", "") and "What the machine was doing" in s.get("js", "") for s in scene["step"])


def test_the_beats_are_filmed_in_the_order_the_product_allows(scene):
    marks = [step["name"] for step in scene["step"] if step["do"] == "mark"]

    assert marks == [
        "too-few-readings",
        "the-words",
        "reading-the-chart",
        "in-control-and-capable",
        "a-rule-fires-in-spec",
        "the-tags-before-the-reading",
        "the-rule-raises-a-hold",
        "the-hold-on-the-quality-page",
        "capability-withheld-out-of-control",
        "capability-given-when-it-means-something",
        "a-simulated-plant",
        "you-now-know",
    ]


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    on_screen = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("hook", "screen", "refuses")]
    on_cards = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("idea", "know")]
    unpointed = [s["line"] for s in on_screen if not s.get("at") and s["line"] not in POINTS_AT_NOTHING_ON_SCREEN]

    assert unpointed == [], f"{len(unpointed)} of {len(on_screen)} lines over the screen point at nothing: {unpointed}"
    assert [s["line"] for s in on_cards if s.get("at")] == []


def test_the_hold_is_found_on_the_quality_page_by_its_rule_and_characteristic(scene):
    quality = first_index(scene, lambda s: s["do"] == "click" and s["selector"] == 'nav.subnav a:text-is("Quality")')
    boxed = [s["at"] for s in scene["step"][quality:] if s["do"] == "say" and "#ncs" in s.get("at", "")]

    assert boxed and all("UT-FORK/width_mm" in at and "SPC rule" in at for at in boxed)


def test_capability_is_withheld_for_too_few_readings_and_for_a_process_out_of_control(scene):
    refuses = steps_of(scene, "refuses")
    lines = " ".join(said(scene, "refuses"))

    assert any(s["do"] == "wait_for" and s.get("text") == "withheld" for s in refuses)
    assert "out of control" in lines and "withheld" in lines
    assert "Too few readings, or a process out of control" in lines


def test_the_plant_is_the_hot_mould_scene_whose_fault_the_notes_describe(scene):
    notes = " ".join(scene["notes"])
    honesty = " ".join(scene["honesty"]).lower()

    assert HOT_MOULD.is_file()
    assert "labs/cutlery/scenes/hot-mould.json" in notes and MACHINE in notes
    assert "simulated plant" in honesty and "ten times wall clock" in honesty
    assert "which rule fires first varies" in honesty


def test_the_you_now_know_card_says_the_classs_own_outcomes_word_for_word(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])
    know = [step for step in steps_of(scene, "know") if step["do"] == "slide"]

    assert len(lesson["outcomes"]) == 3
    assert len(know) == 1 and know[0]["lines"] == lesson["outcomes"]


def test_every_page_it_visits_is_a_screen_the_app_serves_and_every_chart_is_a_fork_characteristic(scene):
    served = [page.path for module in modules.REGISTRY for page in module.pages]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in served]
    gotos = [step["url"] for step in scene["step"] if step["do"] == "goto"]
    visited = {urlparse(url).path for url in gotos} | {"/dashboard/quality"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"
    specs = [parse_qs(urlparse(url).query)["spec"][0] for url in gotos]
    specs += [step["value"] for step in scene["step"] if step["do"] == "choose"]
    assert all(spec.startswith("UT-FORK|") for spec in specs), specs


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_or_note_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_plant_on_loopback_and_writes_nothing(scene):
    base = urlparse(scene["base"])

    assert base.hostname == "127.0.0.1"
    assert base.port not in NOT_OURS
    assert not scene.get("allow_writes")
    assert not [step for step in scene["step"] if step["do"] == "fill"]


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill", "choose"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 2 and "terminal" not in handed
