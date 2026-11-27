"""Class 4.5 reads a certificate of analysis and reissues it without editing it.

*COA — the certificate of analysis* is the fifth class of the quality tier: a
card of words (a pallet at a customer's dock with one sheet on it), the four
words of the class (certificate of analysis, lot, capability, immutable), and
the product, signed in as the cutlery pack's lab shift supervisor: the
certificates in force, each issued by itself as its order completed; the fork
order's certificate read top to bottom; a pallet's certificate with the
capability of every dimension; Print, pointed at; and Reissue, whose revision
2 supersedes revision 1 and keeps it. Then what it refuses: a pallet nobody
has issued a certificate for, and the first pallet's certificate, its
capability withheld. This file holds the scene file to what the handoff asks
of it. No plant and no browser: the scene is loaded and walked the way the
recorder walks it.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules
from fsmes.plant import LAB_ONLY_WALK_PASSWORD, LAB_USERS
from fsmes.services.capabilities import BUILTIN_ROLES

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "05-05-the-certificate-of-analysis.toml"
SUPERVISOR = "WALK-SUP"

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


def clicked(scene: dict) -> list[str]:
    return [step["selector"] for step in scene["step"] if step["do"] == "click"]


def test_the_scene_is_a_full_take_of_class_4_5_that_runs_four_and_a_half_to_six_minutes(runner, scene):
    assert scene["lesson"] == "4.5" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    assert 270 <= planned <= 360 and scene["budget_seconds"] <= 390, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_certificate_folder_of_the_quality_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "05-quality/05-the-certificate-of-analysis"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()
    assert lesson["knows_before"] == ["4.1", "4.3"]


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_words_short_enough_for_the_teaser(runner, scene):
    hook = steps_of(scene, "hook")

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "a-sheet-on-the-pallet"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose", "sign_in")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_card_names_the_four_words_and_each_is_explained_the_first_time_it_is_said(scene):
    (card,) = [step for step in steps_of(scene, "idea") if step["do"] == "slide"]
    words = [line.split(":")[0] for line in card["lines"]]
    lines = said(scene)

    assert words == ["Certificate of analysis, a COA", "Lot", "Capability, Cpk", "Immutable"]
    for word, starts in (
        ("certificate of analysis", "Four words. A certificate of analysis, a COA, is "),
        ("lot", "A lot is "),
        ("capability", "Capability, or Cpk, says "),
        ("immutable", "Immutable means "),
    ):
        first = next(line for line in lines if re.search(rf"\b{word}\b", line, re.IGNORECASE))
        assert first.startswith(starts), (word, first)
        assert first in said(scene, "idea")


def test_it_points_at_the_earlier_classes_in_one_caption_each_and_does_not_teach_them_again(scene):
    lines = said(scene)

    assert [line for line in lines if "Class 4.3" in line] == [
        "Capability, or Cpk, says how comfortably the line holds each spec. Class 4.3 showed where it comes from."
    ]
    assert len([line for line in lines if "Class 2.3" in line]) == 1
    assert not [line for line in lines if "control limit" in line.lower()]


def test_it_signs_in_as_the_packs_lab_supervisor_who_holds_the_right_to_reissue(scene):
    (user,) = [u for u in LAB_USERS if u[0] == SUPERVISOR]

    assert scene["user"] == SUPERVISOR and scene["password"] == LAB_ONLY_WALK_PASSWORD
    assert user[3] == "supervisor" and user[1].startswith("Walker: ")
    assert "quality.close_nc" in BUILTIN_ROLES["supervisor"]["capabilities"]
    assert not [s for s in scene["step"] if s["do"] == "goto" and "login" in s["url"]]


def test_the_only_page_opened_by_address_is_the_floor_and_certificates_is_reached_by_its_menu(scene):
    pages = [step for step in scene["step"] if step["do"] == "goto"]
    path = clicked(scene)

    assert [urlparse(step["url"]).path for step in pages] == ["/dashboard"]
    assert pages[0] is steps_of(scene, "screen")[0]
    assert path[:2] == ['header nav a:text-is("Quality")', 'nav.subnav a:text-is("Certificates")']


def test_print_is_pointed_at_and_never_pressed(scene):
    assert "#print" not in clicked(scene)
    assert any(s["do"] == "say" and s.get("at") == "#print" for s in scene["step"])


def test_reissue_is_pressed_once_and_the_take_waits_for_revision_two_to_supersede_one(scene):
    steps = scene["step"]
    (at,) = [i for i, s in enumerate(steps) if s["do"] == "click" and s["selector"] == "#reissue"]
    wait = steps[at + 1]

    assert wait["do"] == "wait_for" and "revision 2" in wait["js"] and "1, 2" in wait["js"]
    assert any("superseded, not deleted" in line for line in said(scene, "screen"))
    assert "Reissue" in scene["what_is_written"] and "scratch plant" in scene["what_is_written"]


def test_what_it_refuses_is_a_pallet_with_no_certificate_and_a_capability_withheld_in_under_25_seconds(runner, scene):
    refuses = steps_of(scene, "refuses")
    waits = " ".join(s.get("js", "") for s in refuses if s["do"] == "wait_for")

    assert "no certificate has been issued for pallet PL1-000000002" in waits
    assert "capability needs" in waits
    assert "Unknown is not zero." in " ".join(said(scene, "refuses"))
    assert runner.planned_seconds(dict(scene, step=refuses)) < 25


def test_the_pallets_it_opens_are_the_ones_issued_before_the_camera_rolled_and_the_one_it_refuses_is_not(scene):
    issued = scene["plant"]["pallet_certificates_before_roll"]
    searched = [s["text"] for s in scene["step"] if s["do"] == "fill" and s["selector"] == "#q"]

    assert issued == ["PL1-000000001", "PL1-000000015"]
    assert searched == ["PL1-000000015", "PL1-000000002", "PL1-000000001"]
    assert "PL1-000000002" not in issued


def test_the_honest_limits_are_said_in_the_films_own_lines(scene):
    lines = " ".join(said(scene))
    honesty = " ".join(scene["honesty"]).lower()

    assert "no screen asks. This one was issued before the class, through the plant's own API." in lines
    assert "This screen only shows the one in force, though: reading the old one takes the API." in lines
    assert "simulated plant, replaying a recorded line at ten times the clock" in lines
    assert "simulated plant" in honesty and "ten times wall clock" in honesty


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
    assert [s for s in on_screen if s.get("label")] == []


def test_every_page_it_visits_is_a_screen_the_app_serves(scene):
    served = [page.path for module in modules.REGISTRY for page in module.pages]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in served]
    visited = {urlparse(step["url"]).path for step in scene["step"] if step["do"] == "goto"} | {"/dashboard/coa"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_or_note_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback(runner, scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery" and scene["plant"]["speed"] == 10
    assert base.hostname == "127.0.0.1" and base.port == 8040
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    assert scene["allow_writes"]


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 3 and "terminal" not in handed
