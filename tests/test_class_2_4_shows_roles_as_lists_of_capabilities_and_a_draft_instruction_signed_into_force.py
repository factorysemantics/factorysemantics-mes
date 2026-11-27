"""Class 2.4 shows roles as lists of capabilities and a draft instruction signed into force, on a scratch plant.

*People, roles and work instructions*: a card of a start-up sheet taped to a
mould, the words capability, role and revision in force, the roles on Admin
read as lists, a new operator's account made, the shift supervisor drafting the
fork mould's start-up instruction and unable to sign it, the administrator
signing it and opening the next revision, the new operator reading the one in
force, and Admin refusing the new operator. This file holds the scene file to
what the outline asks of it, and every role it names to the roles the product
ships. No plant and no browser: the scene is loaded and walked the way the
recorder walks it.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from fsmes import modules
from fsmes.services import capabilities

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "scripts" / "scenes" / "course" / "03-04-people-roles-and-work-instructions.toml"

A_DATE = re.compile(
    r"\b(19|20)\d\d\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b"
)


@pytest.fixture(scope="module")
def scene(runner):
    return runner.load_scene(SCENE)


def over_a_card(scene: dict) -> list[bool]:
    """For each step, whether a card the recorder painted is what is on screen."""
    card, shown = False, []
    for step in scene["step"]:
        if step["do"] == "slide":
            card = True
        elif step["do"] == "goto":
            card = False
        shown.append(card)
    return shown


def everything_written(scene: dict) -> list[str]:
    """Every sentence a viewer or a reader of the folder will see."""
    written = [scene["title"], scene["what_is_written"], *scene["notes"], *scene["honesty"]]
    for step in scene["step"]:
        if step["do"] == "say":
            written.append(step["line"])
        if step["do"] == "slide":
            written += [step["title"], *step["lines"]]
        if step["do"] == "fill":
            written.append(step["text"])
    return written


def signed_in_as(scene: dict) -> list[tuple[str, list[dict]]]:
    """The steps in order, grouped by who is signed in while they play."""
    who, groups = scene["user"], [(scene["user"], [])]
    for step in scene["step"]:
        if step["do"] == "sign_in":
            who = step["user"]
            groups.append((who, []))
        groups[-1][1].append(step)
    return groups


def held_by(role: str) -> set[str]:
    return set(capabilities.BUILTIN_ROLES[role]["capabilities"])


def test_the_scene_is_a_full_take_of_class_2_4_named_by_its_label_that_runs_four_and_a_half_to_six_minutes(
    runner, scene
):
    assert scene["lesson"] == "2.4" and scene["take_is"] == "lesson"
    assert (scene["width"], scene["height"]) == (1280, 720)
    planned = runner.planned_seconds(scene)
    assert 270 <= planned <= scene["budget_seconds"] <= 360, planned
    print(f"planned {planned} s against a {scene['budget_seconds']} s budget")


def test_it_films_into_the_people_folder_fourth_on_the_floor(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])

    assert lesson["path"] == "03-the-floor/04-people-roles-and-work-instructions"
    assert lesson["scene"] == SCENE.relative_to(REPO).as_posix()


def test_the_scene_walks_the_five_parts_in_order(runner, scene):
    order = []
    for step in scene["step"]:
        if not order or order[-1] != step["part"]:
            order.append(step["part"])

    assert order == list(runner.PARTS)


def test_the_hook_is_a_card_of_the_sheet_on_the_mould_then_the_empty_instructions_and_is_short_enough_for_the_teaser(
    runner, scene
):
    hook = [step for step in scene["step"] if step["part"] == "hook"]

    assert hook[0]["do"] == "slide" and hook[0]["name"] == "the-sheet-on-the-mould"
    assert [step["url"] for step in hook if step["do"] == "goto"] == ["/dashboard/instructions"]
    assert runner.planned_seconds(dict(scene, step=hook)) - scene["gif_skip_seconds"] <= 30


def test_the_idea_cards_name_capability_role_and_revision_in_force_and_say_a_draft_is_not_an_instruction(scene):
    cards = {step["name"]: step for step in scene["step"] if step["do"] == "slide" and step["part"] == "idea"}

    assert [line.split(":")[0] for line in cards["three-words"]["lines"]] == [
        "Capability",
        "Role",
        "Revision in force",
    ]
    assert cards["draft-and-sign"]["title"] == "A draft is not an instruction"
    assert any("AI" in line for line in cards["draft-and-sign"]["lines"])


def test_what_it_says_each_role_holds_is_what_the_product_ships(scene):
    said = {
        step["at"]: step["line"] for step in scene["step"] if step["do"] == "say" and ".grants" in step.get("at", "")
    }

    def about(role: str) -> str:
        return next(line for at, line in said.items() if f'text-is("{role}")' in at)

    assert {"documents.approve", "users.manage"} <= held_by("admin")
    assert "orders.release" in held_by("operator") and "release an order" in about("Operator")
    assert held_by("planner") == {"plant.read", "orders.create"} and "may not release" in about("Planner")
    assert "documents.write" in held_by("supervisor") and "documents.approve" not in held_by("supervisor")
    assert "Not approving them" in about("Supervisor")


def test_the_operator_role_may_release_an_order_so_the_outlines_refusal_is_said_to_be_wrong(scene):
    notes = " ".join(scene["notes"])

    assert "orders.release" in held_by("operator")
    assert "The product says otherwise" in notes
    assert "operator role this product ships may release an order" in " ".join(scene["honesty"])


def test_one_caption_points_at_class_4_2_instead_of_showing_the_split_again(scene):
    pointing = [step["line"] for step in scene["step"] if step["do"] == "say" and "4.2" in step["line"]]

    assert len(pointing) == 1


def test_the_people_list_is_filtered_before_the_camera_leaves_its_first_rows(scene):
    screen = [step for step in scene["step"] if step["part"] == "screen"]
    admin = next(i for i, s in enumerate(screen) if s["do"] == "click" and "Admin" in s["selector"])
    first_scroll = next(i for i, s in enumerate(screen) if i > admin and s.get("scroll_to"))
    fill = next(i for i, s in enumerate(screen) if s["do"] == "fill" and s["selector"] == "#u-q")

    assert admin < fill < first_scroll
    assert screen[fill]["text"] == "operator"


def test_the_account_it_makes_is_an_operator_whose_name_the_filter_still_matches(scene):
    typed = {s["selector"]: s["text"] for s in scene["step"] if s["do"] == "fill" and "#form-user" in s["selector"]}
    made = [s for s in scene["step"] if s["do"] == "sign_in" and s["user"] == typed['#form-user input[name="code"]']]

    assert "operator" in typed['#form-user input[name="name"]']
    assert len(made) == 1 and made[0]["password"] == typed['#form-user input[name="password"]']


def test_the_supervisor_drafts_and_the_administrator_signs_and_opens_the_next_revision(scene):
    clicks = {who: [s["selector"] for s in steps if s["do"] == "click"] for who, steps in signed_in_as(scene)}
    who_signs = [
        who
        for who, steps in signed_in_as(scene)
        for s in steps
        if s["do"] == "click" and "Approve revision 1" in s["selector"]
    ]

    assert "#form-doc button" in clicks["WALK-SUP"]
    assert who_signs == ["ADMIN"]
    assert any("Open next revision" in sel for sel in clicks["ADMIN"])


def test_the_new_operator_reads_the_revision_in_force_and_is_refused_admin(scene):
    who, steps = signed_in_as(scene)[-1]
    waits = [(s["selector"], s["text"]) for s in steps if s["do"] == "wait_for"]

    assert who == "NEW-OP"
    assert ("#doc-status", "approved") in waits
    assert ("#denied h2", "Not your screen") in waits
    assert not [s for s in steps if s["do"] == "click"], "the operator only reads"


def test_what_it_refuses_is_the_admin_screen_typed_by_address_and_it_runs_under_twenty_five_seconds(runner, scene):
    refuses = [step for step in scene["step"] if step["part"] == "refuses"]
    said = " ".join(step["line"] for step in refuses if step["do"] == "say")

    assert [s["url"] for s in refuses if s["do"] == "goto"] == ["/dashboard/admin"]
    assert "users.manage" in said and "users.manage" not in held_by("operator")
    assert runner.planned_seconds(dict(scene, step=refuses)) < 25


def test_the_you_now_know_card_says_the_classs_own_outcomes_word_for_word(runner, scene):
    course = runner.load_course(REPO / "scripts" / "course" / "course.toml")
    _, lesson = runner.find_lesson(course, scene["lesson"])
    know = [step for step in scene["step"] if step["part"] == "know" and step["do"] == "slide"]

    assert len(lesson["outcomes"]) == 3
    assert len(know) == 1 and know[0]["lines"] == lesson["outcomes"]


def test_every_page_it_visits_is_a_screen_the_app_serves(scene):
    served = [page.path for module in modules.REGISTRY for page in module.pages]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in served]
    visited = {urlparse(step["url"]).path for step in scene["step"] if step["do"] == "goto"}

    unserved = sorted(path for path in visited if not any(p.match(path) for p in patterns))
    assert unserved == [], f"{len(unserved)} of {len(visited)} pages are not served: {unserved}"


def test_pages_opened_by_address_are_the_instructions_after_a_card_the_floor_after_a_card_and_the_refused_admin(scene):
    steps = scene["step"]
    pages = [n for n, step in enumerate(steps) if step["do"] == "goto"]

    assert [steps[n]["url"] for n in pages] == ["/dashboard/instructions", "/dashboard", "/dashboard/admin"]
    assert all(over_a_card(scene)[n - 1] for n in pages[:2]), "each comes straight after a card"
    assert steps[pages[2]]["part"] == "refuses"


def test_every_frame_carries_the_pre_alpha_simulated_plant_badge(scene):
    assert scene["badge"] == "pre-alpha · simulated plant · v{version}"


def test_no_caption_card_note_or_typed_word_carries_a_date(scene):
    dated = [line for line in everything_written(scene) if A_DATE.search(line)]

    assert dated == [], f"{len(dated)} lines carry a date: {dated}"


def test_it_is_filmed_on_a_scratch_cutlery_plant_on_loopback_and_never_on_one_somebody_else_runs(runner, scene):
    base = urlparse(scene["base"])

    assert scene["plant"]["pack"] == "labs/cutlery"
    assert base.hostname == "127.0.0.1"
    assert base.port not in runner.PORTS_THAT_BELONG_TO_SOMETHING_ELSE
    assert scene["allow_writes"]
    runner.refuse_to_write_off_this_box(scene)
    with pytest.raises(SystemExit):
        runner.refuse_to_write_off_this_box(dict(scene, base="http://100.64.0.1:8040"))


def test_the_missing_material_the_quiet_queue_the_fixed_words_and_the_replay_are_owned_up_to(scene):
    honesty = " ".join(scene["honesty"]).lower()

    assert "tied to no material" in honesty
    assert "station screen shows none" in honesty
    assert "does not list a draft instruction" in honesty
    assert "cannot be reworded" in honesty
    assert "ten times" in honesty


def test_every_line_said_over_the_screen_points_at_what_it_is_about_and_no_line_over_a_card_does(scene):
    said = [(s, card) for s, card in zip(scene["step"], over_a_card(scene), strict=True) if s["do"] == "say"]
    on_screen = [s for s, card in said if not card]
    on_cards = [s for s, card in said if card]

    assert [s["line"] for s in on_screen if not s.get("at")] == []
    assert [s["line"] for s in on_cards if s.get("at")] == []
    tables = ("#users", "#catalogue", "#history", "#pending", "table", "#toast")
    assert all(len(s["label"].split()) <= 3 for s in on_screen if s.get("label"))
    assert not [s for s in on_screen if s.get("label") and any(t in s["at"] for t in tables)]


def test_the_recorder_plays_every_step_of_it(runner, scene):
    handed: list[str] = []

    class Stage:
        def __getattr__(self, verb):
            return lambda *args, **pointer: handed.append(verb)

    runner.play(scene, Stage())

    for verb in ("goto", "click", "fill", "sign_in"):
        assert handed.count(verb) == sum(1 for step in scene["step"] if step["do"] == verb), verb
    assert handed.count("scroll_to") == sum(1 for step in scene["step"] if step.get("scroll_to"))
    assert handed.count("slide") == 4 and "terminal" not in handed
