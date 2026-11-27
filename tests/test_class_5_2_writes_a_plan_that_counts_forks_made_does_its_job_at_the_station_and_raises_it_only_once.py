"""Class 5.2 writes a plan that counts forks made, does its job at the station, and raises it only once.

*Maintenance — due on use* is the second class of the running-the-plant tier:
a card of words (two moulds greased every Monday, one that ran all week and
one that stood still), the three words of the class (preventive, due on use,
expected downtime); then the
product, signed in as the lab admin, who alone may write a plan: two plans on
the fork mould, one every thousand forks made and one every seven days, both
due; Raise due work makes two jobs and the tiles say what they cost the line;
then, as the pack's lab operator, the station screen of the mould, where one
job is started and completed with what was found; History, which names who did
it; and Plans, where the count has started again. Then what it refuses:
raising the same plan twice while its job is open. This file holds the scene
file to what the handoff asks of it. No plant and no browser: the scene is
loaded and walked the way the recorder walks it.
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
SCENE = REPO / "scripts" / "scenes" / "course" / "06-02-maintenance-due-on-use.toml"
OPERATOR = "WALK-OP"

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


def filled(scene: dict, selector: str) -> list[str]:
    return [s["text"] for s in scene["step"] if s["do"] == "fill" and s["selector"] == selector]


def chosen(scene: dict, selector: str) -> list[str]:
    return [s["value"] for s in scene["step"] if s["do"] == "choose" and s["selector"] == selector]


def test_the_scene_is_a_full_take_of_class_5_2_that_runs_four_and_a_half_to_six_minutes(runner, scene):
    assert scene["lesson"] == "5.2" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    assert 270 <= planned <= 360 and scene["budget_seconds"] <= 390, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_maintenance_folder_of_the_running_the_plant_tier(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "06-running-the-plant/02-maintenance-due-on-use"
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

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "every-monday"
    assert not [step for step in hook if step["do"] in ("goto", "click", "fill", "choose", "sign_in")]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_card_names_the_three_words_and_each_is_explained_the_first_time_it_is_said(scene):
    card = next(step for step in steps_of(scene, "idea") if step["do"] == "slide")
    words = [line.split(":")[0] for line in card["lines"]]
    lines = said(scene)

    assert words == ["Preventive", "Due on use", "Expected downtime"]
    for word, starts in (
        ("preventive", "Three words. Preventive maintenance is "),
        ("due on use", "Due on use means "),
        ("expected downtime", "Expected downtime is "),
    ):
        first = next(line for line in lines if re.search(rf"\b{word}\b", line, re.IGNORECASE))
        assert first.startswith(starts), (word, first)
        assert first in said(scene, "idea")


def test_it_points_at_the_station_class_in_one_caption_and_does_not_teach_the_station_again(scene):
    assert [line for line in said(scene) if "Class 2.1" in line] == [
        "Class 2.1 showed this card empty, because the plant had no plans. Now the mould owes two jobs."
    ]
    assert not [line for line in said(scene) if "Class " in line and "Class 2.1" not in line]


def test_the_plans_are_written_by_the_admin_and_the_job_is_done_by_the_packs_lab_operator(scene):
    (user,) = [u for u in LAB_USERS if u[0] == OPERATOR]
    (sign_in,) = [s for s in scene["step"] if s["do"] == "sign_in"]

    assert scene["user"] == "ADMIN"
    assert "maintenance.plan" in BUILTIN_ROLES["admin"]["capabilities"]
    assert "maintenance.plan" not in BUILTIN_ROLES["operator"]["capabilities"]
    assert sign_in["user"] == OPERATOR and sign_in["password"] == LAB_ONLY_WALK_PASSWORD
    assert user[3] == "operator" and "maintenance.perform" in BUILTIN_ROLES["operator"]["capabilities"]
    assert not [s for s in scene["step"] if s["do"] == "goto" and "login" in s["url"]]


def test_the_only_page_opened_by_address_is_the_floor_and_the_rest_are_reached_by_their_menus(scene):
    pages = [step for step in scene["step"] if step["do"] == "goto"]
    path = clicked(scene)

    assert [urlparse(step["url"]).path for step in pages] == ["/dashboard"]
    assert pages[0] is steps_of(scene, "screen")[0]
    assert path[0] == 'header nav a:text-is("Maintenance")'
    assert 'nav.subnav a:text-is("Station")' in path


def test_it_writes_one_plan_on_use_and_one_on_the_calendar_both_on_the_fork_mould(scene):
    assert filled(scene, "#plan-code") == ["PM-FORK1-VENTS", "PM-FORK1-GUARD"]
    assert chosen(scene, "#plan-machine") == ["FORK1_Mold", "FORK1_Mold"]
    assert chosen(scene, "#plan-trigger") == ["produced_qty", "calendar_days"]
    assert filled(scene, "#plan-interval") == ["1000", "7"]
    assert clicked(scene).count("#plan-submit") == 2


def test_the_fork_mould_has_made_more_than_the_interval_before_the_camera_rolls(scene):
    (roll,) = scene["plant"]["roll_when_order"]

    assert roll["code"] == "WO-FORK1-0001" and roll["booked"] > 1000
    assert any("hundred thousand" in line for line in said(scene, "screen"))


def test_raise_due_work_makes_two_jobs_and_the_tiles_say_what_they_cost(scene):
    steps = scene["step"]
    first = next(i for i, s in enumerate(steps) if s["do"] == "click" and s["selector"] == "#raise-due")
    wait = steps[first + 1]

    assert wait["do"] == "wait_for" and "Raised 2 job" in wait["js"]
    assert any(s.get("at") == ".kpi:has(#kpi-downtime)" for s in steps if s["do"] == "say")


def test_the_job_is_started_and_completed_at_the_station_with_what_was_found(scene):
    path = clicked(scene)
    start = path.index('#maint li:has-text("PM-00001") button:text-is("Start")')

    assert path[start + 1] == '#maint li:has-text("PM-00001") button:text-is("Complete…")'
    assert path[start + 2] == "#maint-confirm"
    assert filled(scene, "#maint-findings") == ["vents cleared, two blocked with flash"]
    assert "Station" in scene["what_is_written"] or "station screen" in scene["what_is_written"]


def test_history_names_who_did_it_and_the_plan_counts_again_from_the_job(scene):
    lines = " ".join(said(scene, "screen"))

    assert "Nobody typed the name; it is whoever was signed in." in lines
    assert "The fork count started again from the job" in lines


def test_what_it_refuses_is_a_second_job_for_an_open_plan_in_under_25_seconds(runner, scene):
    refuses = steps_of(scene, "refuses")
    waits = " ".join(s.get("js", "") for s in refuses if s["do"] == "wait_for")

    assert "#raise-due" in [s["selector"] for s in refuses if s["do"] == "click"]
    assert "Nothing new to raise" in waits
    assert "a plan never has two open jobs" in " ".join(said(scene, "refuses"))
    assert runner.planned_seconds(dict(scene, step=refuses)) < 25


def test_the_honest_limits_are_said_in_the_films_own_lines(scene):
    lines = " ".join(said(scene))
    honesty = " ".join(scene["honesty"]).lower()

    assert "this plant came with no maintenance plans" in lines
    assert "This form cannot set that; a plant's setup files can." in lines
    assert "The screen never asked whether the mould actually stopped." in lines
    assert "simulated plant at ten times the clock" in lines
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
    visited = {urlparse(step["url"]).path for step in scene["step"] if step["do"] == "goto"}
    visited |= {"/dashboard/maintenance", "/dashboard/station"}

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

    for verb in ("goto", "click", "fill", "choose"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 3 and "terminal" not in handed


def test_every_table_cell_it_boxes_sits_under_the_column_heading_its_caption_is_about(scene):
    page = (REPO / "src" / "fsmes" / "web" / "maintenance.html").read_text(encoding="utf-8")
    heads = {
        table: [re.sub(r"<[^>]+>", "", h) for h in re.findall(r"<th[^>]*>.*?</th>", row)]
        for table, row in re.findall(r'<table id="([\w-]+)".*?<thead><tr>(.*?)</tr></thead>', page, re.S)
    }
    boxed = {
        (m.group(1), heads[m.group(1)][int(m.group(2)) - 1])
        for s in scene["step"]
        if s["do"] == "say" and (m := re.match(r"#([\w-]+) tbody tr.* td:nth-child\((\d+)\)$", s.get("at", "")))
    }

    assert boxed == {
        ("plans-table", "Progress"),
        ("plans-table", "Used"),
        ("plans-table", "Last done"),
        ("history-table", "By"),
        ("history-table", "Down (min)"),
    }
