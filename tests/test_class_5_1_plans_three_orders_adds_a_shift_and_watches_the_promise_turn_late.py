"""Class 5.1 plans three orders, adds a shift, and watches the promise turn late.

*Scheduling* is the first class of the running-the-plant tier: a card of words
(three fork orders, one moulding line, a customer asking when), the three
words of the class (schedule, calendar, promise) and the three rules the MES
plans by, and the product, signed in as the lab admin: a fork order made with
a due date, the empty board, three orders planned into a staircase, the
promise that says on time, the calendar that says the plant never stops, a day
shift added, the promise that does not move by itself, the order planned again
and now late, and the order held. Then what it refuses: planning a held order.
This file holds the scene file to what the handoff asks of it. No plant and no
browser: the scene is loaded and walked the way the recorder walks it.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules
from fsmes.services.capabilities import BUILTIN_ROLES

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "06-01-scheduling.toml"
SCHEDULING = REPO / "src" / "fsmes" / "services" / "scheduling.py"
NEW_ORDER = "WO-FORK1-0300"

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
        if step["do"] == "slide":
            written += [step["title"], *step["lines"]]
        if step["do"] == "fill":
            written.append(step["text"])
    return written


def said(scene: dict, part: str | None = None) -> list[str]:
    return [s["line"] for s in scene["step"] if s["do"] == "say" and (part is None or s["part"] == part)]


def steps_of(scene: dict, part: str) -> list[dict]:
    return [step for step in scene["step"] if step["part"] == part]


def test_the_scene_is_a_full_take_of_class_5_1_named_by_its_label_running_four_and_a_half_to_six_minutes(runner, scene):
    assert scene["lesson"] == "5.1" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 270 <= planned <= 345 and scene["budget_seconds"] <= 360, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_scheduling_folder_of_the_running_the_plant_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "06-running-the-plant/01-scheduling"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    assert lesson["knows_before"] == ["1.4"]


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_words_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "when-will-it-be-ready"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose", "sign_in")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_cards_name_the_three_words_and_each_is_explained_the_first_time_it_is_said(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]
    words = [line.split(":")[0] for line in cards[0]["lines"]]
    lines = said(scene)

    assert words == ["Schedule", "Calendar", "Promise"]
    for word, starts in (("schedule", "Three words. A schedule says"), ("calendar", "The calendar is "),
                         ("promise", "A promise is ")):
        first = next(line for line in lines if re.search(rf"\b{word}\b", line, re.IGNORECASE))
        assert first.startswith(starts), (word, first)
        assert first in said(scene, "idea")


def test_the_second_idea_card_says_the_three_rules_the_planner_keeps(scene):
    rules = [step for step in steps_of(scene, "idea") if step["do"] == "slide"][1]["lines"]
    planner = SCHEDULING.read_text(encoding="utf-8")

    assert rules == ["One job at a time on each machine", "The steps in routing order: mould, degate, mark",
                     "Only working hours count"]
    # The rules are the service's own: it waits for the machine and for the step before, in working time.
    assert "_busy_until" in planner and "add_working" in planner and "next_working" in planner


def test_it_signs_in_as_the_lab_admin_because_only_the_admin_role_may_plan(scene):
    holders = sorted(role for role, spec in BUILTIN_ROLES.items() if "scheduling.plan" in spec["capabilities"])

    assert holders == ["admin"]
    assert scene["user"] == "ADMIN"
    assert not [s for s in scene["step"] if s["do"] == "sign_in"]
    assert not [s for s in scene["step"] if s["do"] == "goto" and "login" in s["url"]]


def test_the_new_order_is_due_two_days_from_the_take_typed_as_a_token_not_a_date(scene):
    typed = {s["selector"]: s["text"] for s in steps_of(scene, "screen") if s["do"] == "fill"}

    assert typed["#no-code"] == NEW_ORDER and typed["#no-qty"] == "11000"
    assert typed["#no-due"] == "{today+2}"
    # Class 1.4 is pointed at in one caption, not repeated.
    assert len([line for line in said(scene) if "one point four" in line]) == 1


def test_three_orders_are_planned_one_at_a_time_and_the_board_is_waited_on_for_each(scene):
    steps = scene["step"]
    planned = [steps[i - 1]["text"] for i, s in enumerate(steps)
               if s["do"] == "click" and s["selector"] == "#plan-one"]
    board_waits = [s["js"] for s in steps if s["do"] == "wait_for" and "intervals" in s.get("js", "")]

    assert planned == ["WO-FORK1-0001", "WO-FORK1-0002", NEW_ORDER, NEW_ORDER, NEW_ORDER]
    assert [re.search(r" (\d+) intervals", js).group(1) for js in board_waits] == ["3", "6", "9"]
    assert any(s["do"] == "wait_for" and s.get("text") == "Nothing is scheduled in this window" for s in steps)


def test_the_promise_says_on_time_until_the_shift_and_a_new_plan_make_it_late(scene):
    steps = steps_of(scene, "screen")
    # A wait reads the page with querySelector, so a row is found by its text in the wait's own script.
    verdicts = [re.search(r"includes\('(on time|late by)'\)", s["js"]).group(1) for s in steps
                if s["do"] == "wait_for" and "#promises-table" in s.get("js", "") and NEW_ORDER in s["js"]]
    shift = next(i for i, s in enumerate(steps) if s["do"] == "click" and s["selector"] == "#shift-submit")
    replan = [i for i, s in enumerate(steps) if s["do"] == "click" and s["selector"] == "#plan-one"][-1]
    typed = {s["selector"]: s["text"] for s in steps if s["do"] == "fill"}

    assert verdicts == ["on time", "on time", "late by"]
    assert shift < replan
    assert typed["#shift-code"] == "DAY" and typed["#shift-name"] == "Day shift"
    assert any("nothing has planned it since" in line for line in said(scene, "screen"))


def test_the_honest_limits_are_said_in_the_films_own_lines(scene):
    lines = " ".join(said(scene))
    honesty = " ".join(scene["honesty"]).lower()

    assert "The steps never overlap." in lines
    assert "the list stops at fifty. A gap, written up." in lines
    assert "back of every machine's queue. A gap, written up." in lines
    assert "the plan does not watch the floor" in lines
    assert "No screen takes them off." in lines
    assert "class five point two" in lines
    assert "simulated plant at the real clock" in lines
    assert "simulated plant" in honesty and "real clock" in honesty
    assert "said, not filmed" in honesty


def test_what_it_refuses_is_planning_a_held_order_in_under_twenty_five_seconds(runner, scene):
    refuses = steps_of(scene, "refuses")
    toast = [s for s in refuses if s["do"] == "wait_for" and s.get("selector") == "#toast"]
    hold = [s for s in steps_of(scene, "screen") if s["do"] == "click" and s["selector"] == "#hold-confirm"]

    assert runner.planned_seconds(dict(scene, step=refuses)) < 25
    assert len(hold) == 1
    assert len(toast) == 1 and toast[0]["text"] == "is on hold; resume it before scheduling it"
    assert "is on hold; resume it before scheduling it" in SCHEDULING.read_text(encoding="utf-8")


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


def test_no_wait_leans_on_a_selector_only_playwright_understands(scene):
    # `wait_for` runs document.querySelector in the page, where :has-text and :text-is match nothing.
    waits = [s for s in scene["step"] if s["do"] == "wait_for" and s.get("selector")]

    assert [s["selector"] for s in waits if ":has-text" in s["selector"] or ":text-is" in s["selector"]] == []


def test_no_ai_chat_is_in_this_class(scene):
    assert not [s for s in scene["step"] if "AI" in s.get("selector", "") or "/dashboard/ai" in s.get("url", "")]


def test_every_page_it_visits_is_a_screen_the_app_serves(scene):
    served = [page.path for module in modules.REGISTRY for page in module.pages]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in served]
    visited = {urlparse(step["url"]).path for step in scene["step"] if step["do"] == "goto"}
    visited |= {"/dashboard/schedule"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_note_or_typed_text_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_at_the_real_clock_and_its_writes_are_said(runner, scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert scene["plant"]["speed"] == 1
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
    assert handed.count("slide") == 4 and "terminal" not in handed
