"""Class 3.4 puts one shift on one page and reads a shift it never saw as unknown.

*The shift rollup* is the last class of the machines tier: a card of words
(shift change: how did it go?), the words of the class (shift, rollup, state
timeline, coverage), and the product as it is - the Analysis page's Shift list
greyed on a plant with no shift patterns, the plant's administrator adding a
day and a night shift with the Schedule page's own form on the scratch plant,
and the shift supervisor reading this shift: its name and how much has run,
the coverage banner, the tiles, each machine's watched share, the state
timeline, the pareto and production. Then what it will not do: call a shift it
never watched downtime - and the zeros its tiles still print, said aloud as
unknown. This file holds the scene file to what the handoff asks of it. No
plant and no browser: the scene is loaded and walked the way the recorder
walks it.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "04-04-the-shift-rollup.toml"
PACK = REPO / "labs" / "cutlery"
# The one line over the screen that is about nothing drawn there: the product
# has no box for the hand-over, so there is nothing to box.
POINTS_AT_NOTHING_ON_SCREEN = {
    "The MES has no box for the hand-over itself. The supervisor says the two sentences, from these figures."
}

# What no caption, card or note may carry: a calendar date, a year, a month's
# name. The class is about any plant on any day.
A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)


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


def clicked(scene: dict) -> list[str]:
    return [step["selector"] for step in scene["step"] if step["do"] == "click"]


def filled(scene: dict) -> list[tuple[str, str]]:
    return [(step["selector"], step["text"]) for step in scene["step"] if step["do"] == "fill"]


def test_the_scene_is_a_full_take_of_class_3_4_named_by_its_label_that_runs_four_and_a_half_to_six_minutes(
    runner, scene
):
    assert scene["lesson"] == "3.4" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 270 <= planned <= scene["budget_seconds"] <= 390, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_shift_rollup_folder_of_the_machines_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "04-the-machines-talk/03-the-shift-rollup"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    assert lesson["knows_before"] == ["2.2", "3.3"]


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_words_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "the-huddle"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_card_names_the_four_words_of_the_class(scene):
    cards = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]

    assert len(cards) == 1
    assert [line.split(":")[0] for line in cards[0]["lines"]] == ["Shift", "Rollup", "State timeline", "Coverage"]


def test_each_word_of_the_class_is_explained_the_first_time_it_is_said(scene):
    lines = said(scene)

    for word, starts in (
        ("shift", "A shift is "),
        ("rollup", "A rollup is "),
        ("state timeline", "A state timeline "),
        ("coverage", "Coverage is "),
    ):
        first = next(line for line in lines if word in line.lower() and line not in said(scene, "hook"))
        assert first.startswith(starts), (word, first)
        assert first in said(scene, "idea")


def test_the_only_page_opened_by_address_is_the_floor_and_everything_else_is_clicked(scene):
    pages = [step for step in scene["step"] if step["do"] == "goto"]
    screen = steps_of(scene, "screen")
    path = clicked(scene)

    assert [urlparse(step["url"]).path for step in pages] == ["/dashboard"]
    assert pages[0] is screen[0]
    # Analysis by the Engineering menu, twice; Schedule by the Orders menu.
    engineering = [i for i, s in enumerate(path) if s == 'header nav a:text-is("Engineering")']
    assert len(engineering) == 2
    assert all(path[i + 1] == 'nav.subnav a:text-is("Analysis")' for i in engineering)
    orders = path.index('header nav a:text-is("Orders")')
    assert path[orders + 1] == 'nav.subnav a:text-is("Schedule")'
    assert path[orders + 2] == 'button.tab[data-tab="calendar"]'


def test_the_shift_list_is_shown_greyed_with_the_plants_own_reason_before_any_shift_is_added(scene):
    steps = scene["step"]
    first_add = next(i for i, s in enumerate(steps) if s.get("selector") == "#shift-submit")
    greyed = [s for s in steps[:first_add] if s["do"] == "wait_for" and "no shift patterns" in s.get("js", "")]

    assert len(greyed) == 1 and "#shift').disabled" in greyed[0]["js"]
    assert "the Shift list is greyed out" in " ".join(said(dict(scene, step=steps[:first_add])))


def test_the_two_shifts_are_typed_into_the_schedule_form_and_cover_every_hour_of_a_working_day(scene):
    typed = filled(scene)

    assert typed == [
        ("#shift-code", "DAY"),
        ("#shift-name", "Day shift"),
        ("#shift-ends", "1800"),
        ("#shift-code", "NIGHT"),
        ("#shift-name", "Night shift"),
        ("#shift-starts", "1800"),
        ("#shift-ends", "0600AM"),
    ]
    assert clicked(scene).count("#shift-submit") == 2
    # The day shift keeps the form's own 06:00 start, and the days box the
    # plant's own working week: neither is typed over.
    assert "#shift-days" not in [selector for selector, _ in typed]
    assert any("Monday to Friday" in line for line in said(scene))
    assert scene["allow_writes"] is True and "Add shift" in scene["what_is_written"]


def test_the_shifts_are_added_by_the_administrator_and_the_rollup_read_by_the_shift_supervisor(runner, scene):
    signs = [step for step in scene["step"] if step["do"] == "sign_in"]
    last_add = max(i for i, s in enumerate(scene["step"]) if s.get("selector") == "#shift-submit")

    assert scene.get("user", runner.DEFAULTS["user"]) == "ADMIN"
    assert [step["user"] for step in signs] == ["WALK-SUP"]
    assert scene["step"].index(signs[0]) > last_add
    assert any("WALK-SUP" in note and "not a person" in note for note in scene["notes"])


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    on_screen = [s for s in scene["step"] if s["do"] == "say" and s["part"] in ("screen", "refuses")]
    on_cards = [s for s in scene["step"] if s["do"] == "say" and s["part"] not in ("screen", "refuses")]
    unpointed = [s["line"] for s in on_screen if not s.get("at") and s["line"] not in POINTS_AT_NOTHING_ON_SCREEN]

    assert unpointed == [], f"{len(unpointed)} of {len(on_screen)} lines over the screen point at nothing: {unpointed}"
    assert [s["line"] for s in on_cards if s.get("at")] == []
    labelled = [s for s in on_screen if s.get("label")]
    assert all(len(s["label"].split()) <= 3 for s in labelled)
    assert not [s["at"] for s in labelled if any(word in s["at"] for word in ("tr", ".bar", ":text", ".watched"))]


def test_the_beats_are_filmed_in_the_order_the_handoff_asks(scene):
    marks = [step["name"] for step in scene["step"] if step["do"] == "mark" and step["part"] in ("screen", "refuses")]

    assert marks == [
        "no-shifts-yet",
        "two-shifts",
        "this-shift",
        "the-rollup",
        "the-state-timeline",
        "the-hand-over",
        "a-shift-it-never-saw",
    ]


def test_what_earlier_classes_showed_is_pointed_at_in_one_caption_each(scene):
    lines = said(scene)

    assert len([line for line in lines if "class 2.2" in line]) == 1
    assert len([line for line in lines if "Class 3.3" in line]) == 1


def test_the_honest_limits_are_said_in_the_films_own_lines(scene):
    lines = " ".join(said(scene))
    honesty = " ".join(scene["honesty"]).lower()

    assert "this plant has no shift patterns" in lines
    assert "This plant started a few minutes ago" in lines
    assert "every stop is unlabelled" in lines
    assert "The MES has no box for the hand-over" in lines
    assert "simulated plant" in honesty and "ten times wall clock" in honesty


def test_what_it_refuses_is_downtime_it_never_saw_and_its_zeros_are_said_as_unknown(scene):
    refuses = steps_of(scene, "refuses")
    lines = " ".join(said(scene, "refuses"))

    assert refuses[0]["do"] == "choose" and refuses[0]["value"] == "previous"
    assert any("Showing 0.00 h" in step.get("js", "") for step in refuses)
    assert "will not call twelve hours it never saw downtime" in lines
    assert "Read those zeros as unknown" in lines


def test_what_it_refuses_runs_under_twenty_five_seconds(runner, scene):
    assert runner.planned_seconds(dict(scene, step=steps_of(scene, "refuses"))) < 25


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
    visited |= {"/dashboard/analysis", "/dashboard/schedule"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_the_cutlery_pack_on_disk_still_has_no_shift_patterns(scene):
    # The class adds its shifts to the scratch plant only; were the pack to
    # grow a shifts.json, the opening beat would no longer be true.
    assert not list(PACK.rglob("shifts.json"))


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_or_note_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback(runner, scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert base.hostname == "127.0.0.1"
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    assert scene["plant"]["roll_when_watched"] == {"equipment": "FORK1_Mold", "hours": 0.25, "at_least": 0.99}


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill", "choose", "sign_in"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 3 and "terminal" not in handed
