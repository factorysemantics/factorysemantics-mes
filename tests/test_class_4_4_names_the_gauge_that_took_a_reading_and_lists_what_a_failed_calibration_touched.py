"""Class 4.4 names the gauge that took a reading and lists what a failed calibration touched.

*Gauges and calibration* is the fourth class of the quality tier: a card of
words (a caliper reads a knife in spec, but it was dropped), the four words of
the class (gauge, calibration, the rule of ten, gauge study), and the product,
signed in as the cutlery pack's lab quality engineer: the gauge register, what
a caliper measured and whether it reads fine enough, the inspection history's
gauge column, two gauge studies (the gauge is the problem; the people are),
and a calibration found out of tolerance that takes the caliper off the floor
and lists the hundred readings it touched. Then what it refuses: a reading
naming a gauge the register does not have. This file holds the scene file to
what the handoff asks of it. No plant and no browser: the scene is loaded and
walked the way the recorder walks it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules
from fsmes.plant import LAB_ONLY_WALK_PASSWORD, LAB_USERS
from fsmes.services import gauges
from fsmes.services.capabilities import BUILTIN_ROLES

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "05-04-gauges-and-calibration.toml"
STUDIES = REPO / "labs" / "cutlery" / "gage_studies.json"
QUALITY_ENGINEER = "WALK-QE"

A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def everything_written(scene: dict) -> list[str]:
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


def test_the_scene_is_a_full_take_of_class_4_4_named_by_its_label_running_four_and_a_half_to_six_minutes(runner, scene):
    assert scene["lesson"] == "4.4" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 270 <= planned <= 345 and scene["budget_seconds"] <= 360, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_gauges_folder_of_the_quality_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "05-quality/04-gauges-and-calibration"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    assert lesson["knows_before"] == ["4.1"]


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_words_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "is-the-caliper-right"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose", "sign_in")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_card_names_the_four_words_and_each_is_explained_the_first_time_it_is_said(scene):
    (card,) = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]
    words = [line.split(":")[0] for line in card["lines"]]
    lines = said(scene)

    assert words == ["Gauge", "Calibration", "Rule of ten", "Gauge study"]
    for word, starts in (
        ("calibration", "Calibration is "),
        ("rule of ten", "The rule of ten: "),
        ("gauge study", "A gauge study "),
    ):
        first = next(line for line in lines if re.search(rf"\b{word}\b", line, re.IGNORECASE))
        assert first.startswith(starts), (word, first)
        assert first in said(scene, "idea")
    assert said(scene, "idea")[0].startswith("Four words. A gauge is the instrument")


def test_the_rule_of_ten_is_said_with_the_plants_own_ratios(scene):
    rule = next(line for line in said(scene, "idea") if line.startswith("The rule of ten"))

    assert (gauges.RATIO_ADEQUATE, gauges.RATIO_FLOOR) == (10.0, 4.0)
    assert "ten to one adequate" in rule and "under four to one not usable" in rule


def test_gauge_studies_are_said_to_be_an_optional_module(scene):
    assert any("Gauge studies are an optional module" in line for line in said(scene, "idea"))
    assert any("the optional module" in line for line in said(scene, "screen"))


def test_it_signs_in_as_the_packs_lab_quality_engineer_who_may_record_a_calibration(scene):
    (user,) = [u for u in LAB_USERS if u[0] == QUALITY_ENGINEER]
    supervisor = set(BUILTIN_ROLES["supervisor"]["capabilities"])
    operator = set(BUILTIN_ROLES["operator"]["capabilities"])

    assert scene["user"] == QUALITY_ENGINEER and scene["password"] == LAB_ONLY_WALK_PASSWORD
    assert user[1].startswith("Walker: ") and user[3] == "supervisor"
    # Recording a calibration takes quality.close_nc: the supervisor holds it, the operator not.
    assert "quality.close_nc" in supervisor and "quality.close_nc" not in operator
    assert not [s for s in scene["step"] if s["do"] == "sign_in"]
    assert not [s for s in scene["step"] if s["do"] == "goto" and "login" in s["url"]]


def test_the_gauges_and_studies_are_given_to_the_plant_before_the_camera_rolls(scene):
    cases = {case["study"] for case in json.loads(STUDIES.read_text(encoding="utf-8"))["cases"]}

    assert scene["plant"]["before_roll"] == [["sim-gage-study", "labs/cutlery/gage_studies.json", "all"]]
    assert {"GS-WORN-MIC", "GS-ONE-READER", "GS-GOOD-CAL"} <= cases
    assert any("written in before the camera rolled" in line for line in said(scene))


def test_it_films_which_gauge_took_a_reading_and_a_reading_that_names_none(scene):
    screen = steps_of(scene, "screen")
    boxed = [s["at"] for s in screen if s["do"] == "say"]

    assert '#checks tbody tr:has-text("CAL-12") td:nth-child(7)' in boxed
    assert '#checks tbody tr:has-text("not recorded") td:nth-child(7)' in boxed
    assert any("does not guess one" in line for line in said(scene, "screen"))
    # Class 4.1 is pointed at in one caption, not repeated.
    assert len([line for line in said(scene) if "four point one" in line]) == 1


def test_one_study_blames_the_gauge_and_the_other_the_people(scene):
    lines = " ".join(said(scene, "screen"))
    clicks = [s["selector"] for s in steps_of(scene, "screen") if s["do"] == "click"]

    assert clicks.index('#studies-table tbody tr:has(td:text-is("GS-WORN-MIC"))') < clicks.index(
        '#studies-table tbody tr:has(td:text-is("GS-ONE-READER"))')
    assert "The gauge is the problem." in lines and "The people, not the gauge." in lines


def test_the_failed_calibration_takes_the_caliper_off_the_floor_and_lists_what_it_touched(scene):
    steps = scene["step"]
    chosen = {s["selector"]: s["value"] for s in steps if s["do"] == "choose"}
    submit = next(i for i, s in enumerate(steps) if s["do"] == "click" and s["selector"] == "#cal-submit")
    wait = steps[submit + 1]

    assert chosen["#cal-gauge"] == "CAL-12" and chosen["#cal-result"] == "fail_as_found"
    assert wait["do"] == "wait_for" and "now out of.service" in wait["js"] and "100 suspect" in wait["js"]
    assert any(s["do"] == "say" and s.get("at") == "#cal-suspect li.alarm" and "a hundred readings" in s["line"]
               for s in steps)


def test_the_honest_limits_are_said_in_the_films_own_lines(scene):
    lines = " ".join(said(scene))
    honesty = " ".join(scene["honesty"]).lower()

    assert "It does not reopen a non-conformance or change a reading by itself" in lines
    assert "still says every gauge is in calibration" in lines and "A gap, written up." in lines
    assert "One overdue or out of service is not: it is kept, and named." in lines
    assert "an operator gets no calibration form" in lines.lower()
    assert "simulated plant at ten times the clock" in lines
    assert "simulated plant" in honesty and "ten times wall clock" in honesty
    assert "said, not filmed" in honesty


def test_what_it_refuses_is_a_gauge_the_register_does_not_have_in_under_twenty_five_seconds(runner, scene):
    refuses = steps_of(scene, "refuses")
    typed = {s["selector"]: s["text"] for s in refuses if s["do"] == "fill"}
    toast = [s for s in refuses if s["do"] == "wait_for" and s.get("selector") == "#toast"]

    assert runner.planned_seconds(dict(scene, step=refuses)) < 25
    assert typed["#form-quality input[name=gauge]"] == "CAL-99"
    assert len(toast) == 1 and toast[0]["text"] == "no gauge CAL-99"


def test_the_you_now_know_card_says_the_classs_own_outcomes_word_for_word(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])
    know = [step for step in steps_of(scene, "know") if step["do"] == "slide"]

    assert len(lesson["outcomes"]) == 3
    assert len(know) == 1 and know[0]["lines"] == lesson["outcomes"]


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    on_screen = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("screen", "refuses")]
    on_cards = [s for s in scene["step"] if s["do"] == "say" and s["part"] not in ("screen", "refuses")]

    assert [s["line"] for s in on_screen if not s.get("at")] == []
    assert [s["line"] for s in on_cards if s.get("at")] == []
    # Rows and inner text are boxed, never labelled.
    assert [s for s in scene["step"] if s.get("label")] == []


def test_no_ai_chat_is_in_this_class(scene):
    assert not [s for s in scene["step"] if "AI" in s.get("selector", "") or "/dashboard/ai" in s.get("url", "")]


def test_every_page_it_visits_is_a_screen_the_app_serves(scene):
    served = [page.path for module in modules.REGISTRY for page in module.pages]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in served]
    visited = {urlparse(step["url"]).path for step in scene["step"] if step["do"] == "goto"}
    visited |= {"/dashboard/quality", "/dashboard/gauges/studies", "/dashboard"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_or_note_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_and_its_writes_are_said(runner, scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert scene["plant"]["speed"] == 10
    assert base.hostname == "127.0.0.1"
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    assert scene["allow_writes"] and "scratch plant" in scene["what_is_written"]


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
