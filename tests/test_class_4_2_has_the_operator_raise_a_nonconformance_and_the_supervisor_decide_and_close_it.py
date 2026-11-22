"""Class 4.2 has the operator raise a non-conformance and the supervisor decide and close it.

*Non-conformances, severities and who decides* is the second class of the
quality tier: a card of words (burrs on forks that no gauge measured), the
five words of the class (non-conformance, severity, review, disposition,
close), and the product, signed in as two of the cutlery pack's lab accounts:
the operator raises a non-conformance by hand against the fork order; the
supervisor reads the plant's own words for severity, takes it under review,
decides it (rework, and only with a reason) and closes it; and its history
says who did each step and when. Then what it refuses: the operator, signed in
again, has no button to review, decide or close; and, in the film's own lines,
what the product does not stop or does not show yet. This file holds the
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
from fsmes.plant import LAB_ONLY_WALK_PASSWORD, LAB_USERS
from fsmes.services.capabilities import BUILTIN_ROLES

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "record-scene.py"
SCENE = REPO / "scripts" / "scenes" / "course" / "05-02-nonconformances-and-who-decides.toml"
PACK = REPO / "labs" / "cutlery"
OPERATOR, SUPERVISOR = "WALK-OP", "WALK-SUP"

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
    spec = importlib.util.spec_from_file_location("record_scene_class_4_2", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def lab_user(code: str) -> dict:
    (user,) = [dict(zip(("code", "name", "password", "role"), u, strict=True)) for u in LAB_USERS if u[0] == code]
    return user


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


def test_the_scene_is_a_full_take_of_class_4_2_named_by_its_label_that_runs_four_to_six_minutes(runner, scene):
    assert scene["lesson"] == "4.2" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    # Page loads and waits add to the plan; the finished take is measured.
    assert 240 <= planned <= 345 and scene["budget_seconds"] <= 390, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_nonconformance_folder_of_the_quality_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "05-quality/02-nonconformances-and-who-decides"
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

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "burrs-on-the-forks"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose", "sign_in")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_card_names_the_five_words_and_each_is_explained_the_first_time_it_is_said(scene):
    (card,) = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]
    words = [line.split(":")[0] for line in card["lines"]]
    lines = said(scene)

    assert words == ["Non-conformance", "Severity", "Review", "Disposition", "Close"]
    for word, starts in (
        ("non-conformance", "Five words. A non-conformance is "),
        ("severity", "Severity is "),
        ("review", "Review means "),
        ("disposition", "A disposition is "),
        ("close", "Close means "),
    ):
        first = next(line for line in lines if re.search(rf"\b{word}\b", line, re.IGNORECASE))
        assert first.startswith(starts), (word, first)
        assert first in said(scene, "idea")
    disposition = next(line for line in lines if line.startswith("A disposition is "))
    assert all(choice in disposition for choice in ("as it is", "rework", "scrap", "back to the supplier"))


def test_it_signs_in_as_two_of_the_packs_lab_accounts_and_no_real_person(scene):
    signs = [step for step in scene["step"] if step["do"] == "sign_in"]

    assert scene["user"] == OPERATOR
    assert [step["user"] for step in signs] == [SUPERVISOR, OPERATOR]
    assert [step["part"] for step in signs] == ["screen", "refuses"]
    for code, role in ((OPERATOR, "operator"), (SUPERVISOR, "supervisor")):
        assert lab_user(code)["role"] == role
        # A lab account, named for what it is, and not a person's login.
        assert lab_user(code)["name"].startswith("Walker: ")
    assert scene["password"] == LAB_ONLY_WALK_PASSWORD
    assert all(step["password"] == LAB_ONLY_WALK_PASSWORD for step in signs)
    # And the sign-in page, which prints the demo accounts' hint, is never opened.
    assert not [s for s in scene["step"] if s["do"] == "goto" and "login" in s["url"]]


def test_the_roles_own_the_steps_the_film_says_they_own(scene):
    operator = set(BUILTIN_ROLES["operator"]["capabilities"])
    supervisor = set(BUILTIN_ROLES["supervisor"]["capabilities"])
    lines = " ".join(said(scene))

    assert "quality.record" in operator and "quality.close_nc" not in operator
    assert {"quality.record", "quality.close_nc"} <= supervisor
    assert "The operator raises it. The supervisor reviews, decides and closes." in lines


def test_the_only_page_opened_by_address_is_the_floor_and_everything_else_is_clicked(scene):
    pages = [step for step in scene["step"] if step["do"] == "goto"]
    screen = steps_of(scene, "screen")
    path = clicked(scene)

    assert [urlparse(step["url"]).path for step in pages] == ["/dashboard"]
    assert pages[0] is screen[0]
    assert path[0] == 'header nav a:text-is("Quality")'
    assert path.index('nav.subnav a:text-is("Configuration")') + 1 == path.index('a[href="/dashboard/severities"]')


def test_the_supervisor_works_it_review_then_decide_then_close_with_the_operator_signed_out(scene):
    steps = scene["step"]
    supervisor_from = next(i for i, s in enumerate(steps) if s["do"] == "sign_in" and s["user"] == SUPERVISOR)
    operator_again = next(i for i, s in enumerate(steps) if s["do"] == "sign_in" and s["user"] == OPERATOR)
    raise_at = next(i for i, s in enumerate(steps) if s.get("selector") == "#r-save")

    def at(words: str) -> int:
        return next(i for i, s in enumerate(steps) if s["do"] == "click" and words in s["selector"])

    assert raise_at < supervisor_from < at("Take under review") < at("Decide…") < at("#d-save") < at(
        'button:text-is("Close")') < operator_again
    (choice,) = [s for s in steps if s["do"] == "choose" and s["selector"] == "#d-choice"]
    assert choice["value"] == "rework"


def test_the_decision_is_refused_without_a_reason_and_filmed_so(scene):
    steps = scene["step"]
    chose = next(i for i, s in enumerate(steps) if s["do"] == "choose" and s["selector"] == "#d-choice")
    reason = next(i for i, s in enumerate(steps) if s["do"] == "fill" and s["selector"] == "#d-reason")
    between = steps[chose:reason]

    assert any(s["do"] == "say" and "without a reason" in s["line"] and s.get("at") == "#d-save" for s in between)
    assert any(s["do"] == "mark" and s["name"] == "no-reason-no-decision" for s in between)
    assert steps[reason]["text"].strip()


def test_the_operator_is_filmed_with_no_button_to_review_decide_or_close(scene):
    refuses = steps_of(scene, "refuses")
    waits = [s for s in refuses if s["do"] == "wait_for" and "nc-actions" in s.get("js", "")]
    lines = " ".join(said(scene, "refuses"))

    assert len(waits) == 1 and "!document.querySelector('#ncs .nc-actions')" in waits[0]["js"]
    assert "raised by FLOOR-SIM" in waits[0]["js"]
    assert "no button to review, decide or close it" in lines
    assert "A request sent anyway is refused" in lines


def test_the_history_it_films_has_all_four_steps_of_the_one_it_raised(scene):
    (claim,) = [i for i, s in enumerate(scene["step"]) if s["do"] == "say" and "Who did what, and when" in s["line"]]
    wait = scene["step"][claim - 1]

    assert wait["do"] == "wait_for"
    assert "ol.nc-history li').length === 4" in wait["js"] and ".pill.closed" in wait["js"]
    assert scene["step"][claim]["at"].endswith("ol.nc-history")


def test_severity_is_shown_in_the_plants_own_words_as_the_pack_writes_them(scene):
    severities = json.loads((PACK / "masterdata" / "nc_severities.json").read_text(encoding="utf-8"))
    codes = sorted(row["code"] for row in severities)
    lines = " ".join(said(scene, "screen"))

    assert codes == ["major", "minor"]
    assert "two words, minor and major" in lines
    assert "Minor: the plant can work through it in the normal course of the shift." in lines


def test_the_honest_limits_are_said_in_the_films_own_lines(scene):
    lines = " ".join(said(scene))
    honesty = " ".join(scene["honesty"]).lower()

    assert "Severity offers only the plant's default" in lines and "this form does not show them yet" in lines
    assert "a supervisor may raise a non-conformance and close it too" in lines
    assert "Reviewing it does not change it" in lines
    assert "simulated plant, replaying a recorded line at ten times the clock" in lines
    assert "simulated plant" in honesty and "ten times wall clock" in honesty
    assert "said, not filmed" in honesty


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
    assert [s for s in on_screen if s.get("label")] == []


def test_every_page_it_visits_is_a_screen_the_app_serves(scene):
    served = [page.path for module in modules.REGISTRY for page in module.pages]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in served]
    visited = {urlparse(step["url"]).path for step in scene["step"] if step["do"] == "goto"}
    visited |= {"/dashboard/quality", "/dashboard/config/quality", "/dashboard/severities"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_or_note_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_and_its_writes_are_said(scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert scene["plant"]["speed"] == 10
    assert base.hostname == "127.0.0.1"
    assert base.port not in NOT_OURS
    assert scene["allow_writes"] and "scratch plant" in scene["what_is_written"]


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
