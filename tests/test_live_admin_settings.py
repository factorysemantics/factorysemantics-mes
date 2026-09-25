"""Plant administration's own numbers, live - and where IT's three landed.

The plant-scope administration rows of the configuration audit of 2026-09-21,
built on the mechanism #100 proved for Quality: seeded from the pack when the
plant is built, owned by the database from then on, edited by somebody holding
`users.manage`, in force at once with no restart and no pack to re-apply.

This file pins what the change has to be true of, in order:

1. **A plant that configures nothing behaves exactly as it did.** Rule one of
   the audit, and the reason no data moves anywhere.
2. Every number that *can* be live **is** live, and the three that cannot say
   so on the page rather than offering an input that half works.
3. The browser reads its fifteen from the server and keeps no copy of any of
   them, which is what stops the same number existing in two places again.
4. IT's three are on this page, gated on `users.manage`, and IT still has no
   `ConfigDomain` - decision 0035 section 2.
5. The clauses that keep a drafted document honest are not editable, whatever
   a plant writes into its house style.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from fsmes import modules
from fsmes.domain import PlantSetting
from fsmes.services import plant_settings

#: A pack that states four of them and leaves the rest to the product, which
#: is what a real pack looks like: a plant writes down the numbers it
#: disagrees with the product about and says nothing about the others.
FOUR_OF_THEM = '''
[pack]
format = 1
requires = ">=0.1.2"

[plant]
name = "runs-its-own-screens"
label = "A plant that states four of its own administration numbers"
timezone = "Europe/Berlin"
profile = "laptop"

[admin]
default_new_account_role = "viewer"
walkthrough_max_steps = 90

[screens]
floor_refresh_ms = 10000

[system]
local_model_name = "llama3:70b"
'''


@pytest.fixture()
def pack(tmp_path):
    from fsmes.pack import check as checker
    from fsmes.pack import format as fmt

    (tmp_path / "plant.toml").write_text(FOUR_OF_THEM, encoding="utf-8")
    report = checker.check(tmp_path)
    assert report.ok, report.render()
    return fmt.read(tmp_path)


# ------------------------------------- 1. a plant that configures nothing


def test_a_plant_that_configures_nothing_reads_exactly_what_it_read_before(session):
    """Rule one. Not one row in `plant_settings`, and every administration
    number answers the literal that was in the source before any of them was a
    setting. A plant upgrading to this version and never opening the page is
    this test."""
    from fsmes.services import drafting, walkthroughs

    assert session.scalars(select(PlantSetting)).all() == []

    assert plant_settings.setting(session, "admin", "default_new_account_role") == "operator"
    assert plant_settings.setting(session, "admin", "pending_approvals_page_size") == 20
    assert plant_settings.setting(session, "admin", "agent_max_rounds") == 12
    assert plant_settings.setting(session, "admin", "agent_session_ttl_seconds") == 30 * 60
    assert plant_settings.setting(session, "admin", "agent_result_limit") == 6000
    assert plant_settings.setting(session, "admin", "assistant_context_chars") == 3000
    assert plant_settings.setting(session, "admin", "assistant_timeout_seconds") == 60.0
    assert plant_settings.setting(session, "admin", "ai_rollup_stale_hours") == 40
    assert walkthroughs.limits(session) == {
        "max_steps": 60, "title_chars": 120, "body_chars": 1000,
        "fill_chars": 200, "tab_chars": 60}
    assert walkthroughs.default_needs(session) == "plant.read"
    assert drafting.instructions(session).startswith("Write a work instruction")

    assert plant_settings.setting(session, "screens", "floor_refresh_ms") == 2000
    assert plant_settings.setting(session, "screens", "admin_refresh_ms") == 8000
    assert plant_settings.setting(session, "screens", "all_pages_cap") == 2000
    assert plant_settings.setting(session, "screens", "toast_ms") == 3500

    assert plant_settings.setting(session, "system", "local_model_name") == "qwen3:8b"
    assert plant_settings.setting(session, "system", "log_rotation_backups") == 5


def test_a_new_account_gets_the_role_this_plant_starts_people_on(session):
    """The literal `operator` decided this for every plant that ever ran the
    product. It is still what a plant that says nothing gets."""
    from fsmes.services import auth

    made = auth.create_user(session, code="NEW1", name="Somebody", password="x")
    assert made.role == "operator"

    plant_settings.write(session, domain="administration",
                         key="default_new_account_role", written="viewer", actor="admin")
    later = auth.create_user(session, code="NEW2", name="Somebody else", password="x")
    assert later.role == "viewer"


def test_a_role_this_product_does_not_ship_is_refused_before_it_is_saved(admin):
    """A default nobody can be given would make every account creation fail
    afterwards, which is a worse way to find out."""
    answer = admin.patch("/dashboard/config/administration/settings/default_new_account_role",
                         json={"value": "chief_bottle_washer"})
    assert answer.status_code == 422
    assert "not a role this product ships" in answer.json()["detail"]


# ------------------------------------- 2. what is live, and what is not


def test_every_administration_number_that_can_move_is_edited_on_this_page(admin):
    """What this change is, counted. Nineteen sections: sixteen with an input
    and three read at start-up, and every one of them gated on the one
    capability this domain has."""
    page = admin.get("/dashboard/config/administration/sections").json()
    live = [row for row in page["items"] if row["edit_here"]]
    read_only = [row for row in page["items"] if not row["edit_here"]]

    assert page["total"] == 19
    assert len(live) == 16
    assert {row["key"] for row in read_only} == {"list_paging", "log_rotation", "fleet_probe"}
    assert all(row["define"] == "users.manage" for row in page["items"])
    assert all(row["approve"] is None for row in page["items"])


def test_the_three_that_cannot_move_say_so_rather_than_offering_a_box(admin):
    """`edit_here=False` is the honest answer for a setting that genuinely
    cannot move while a plant is running, and the page's own sentence for it is
    *nobody - it changes when the pack is applied and the plant restarts*. A
    box that appeared to work and took effect at the next restart would be
    worse."""
    page = admin.get("/dashboard/config/administration/sections").json()
    for key in ("list_paging", "log_rotation", "fleet_probe"):
        row = next(r for r in page["items"] if r["key"] == key)
        assert row["pack_keys"], key
        assert all(k["set_by"] is None for k in row["pack_keys"]), key

    refused = admin.patch("/dashboard/config/administration/settings/list_max_limit",
                          json={"value": "1000"})
    assert refused.status_code == 404
    assert "nothing editable in the 'administration' workspace" in refused.json()["detail"]


def test_a_number_edited_on_the_page_is_in_force_on_the_next_reading(admin, session):
    """No restart, no pack, no cache to expire: the next request reads the
    table."""
    from fsmes.services import walkthroughs

    assert walkthroughs.limits(session)["max_steps"] == 60
    saved = admin.patch("/dashboard/config/administration/settings/walkthrough_max_steps",
                        json={"value": "90"})
    assert saved.status_code == 200, saved.text
    plant_settings.forget(session)
    assert walkthroughs.limits(session)["max_steps"] == 90


def test_a_walkthrough_is_refused_by_this_plants_own_number_and_told_which(admin, session):
    """The sentence names the number it refused by, so *at most sixty* does not
    stay on the screen of a plant that raised it to ninety."""
    from fsmes.services import documents, walkthroughs

    steps = [{"page": "/dashboard", "anchor": "x", "title": "t"}] * 70
    with pytest.raises(documents.Invalid) as refused:
        walkthroughs.validate_steps(steps, walkthroughs.limits(session))
    assert "at most 60 steps" in str(refused.value)

    plant_settings.write(session, domain="administration",
                         key="walkthrough_max_steps", written="90", actor="admin")
    # Seventy is now allowed; the first step is refused for a real reason
    # instead, which is the point - the limit stopped being the answer.
    with pytest.raises(documents.Invalid) as other:
        walkthroughs.validate_steps(steps, walkthroughs.limits(session))
    assert "at most" not in str(other.value)


def test_a_conversation_keeps_the_agent_budget_it_opened_with(session):
    """A budget that moved under a turn already in flight would cut somebody
    off mid-sentence because somebody else pressed Save."""
    from fsmes.services import agent

    opened = agent.open_session("SCOTT", "demo", set(), max_rounds=4, ttl=60, result_limit=99)
    assert (opened.max_rounds, opened.ttl, opened.result_limit) == (4, 60, 99)
    agent.forget(opened.id)


def test_the_ai_panel_reads_this_plants_own_idea_of_late(session):
    """Forty hours was chosen for one encrypted laptop that sleeps overnight.
    A server that never sleeps answers differently, and the panel is asked
    rather than told."""
    from fsmes.services import ai_status

    assert ai_status.consumers(timedelta(hours=0)) != ai_status.consumers(timedelta(days=3650))


# ------------------------------------- 3. the browser keeps no copy


def test_the_browser_is_told_what_this_plants_screens_are_set_to(client):
    """Fifteen numbers, and the `[screens]` pack table is the list: nothing
    here enumerates them a second time."""
    from fsmes.pack import format as fmt

    page = client.get("/dashboard/ui-settings").json()
    declared = {key.name for key in fmt.BY_SECTION["screens"].keys}
    assert set(page["settings"]) == declared
    assert page["total"] == len(declared)
    assert page["settings"]["floor_refresh_ms"] == 2000
    assert page["settings"]["toast_ms"] == 3500


def test_a_screen_setting_saved_on_the_page_reaches_the_browser(admin):
    assert admin.patch("/dashboard/config/administration/settings/toast_ms",
                       json={"value": "9000"}).status_code == 200
    assert admin.get("/dashboard/ui-settings").json()["settings"]["toast_ms"] == 9000


def test_no_screen_carries_its_own_copy_of_a_number_the_server_owns():
    """The one thing that would undo this change: a default in the browser
    beside the default on the server, drifting apart the way common.js's 3500
    and admin.js's 4000 did. Every one of these literals is now in `config.py`
    and nowhere else."""
    from pathlib import Path

    web = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"
    gone = {
        "app.js": ("const REFRESH_MS", "const PENDING_REFRESH_MS", "const MACHINE_PAGE",
                   "const ORDER_PAGE", "const SPEC_CHOICES"),
        "admin.js": ("const userPageSize", "const routingPageSize", "refresh, 8000", ", 250)"),
        "common.js": ('"hidden"), 3500', "limit = 500", "cap = 2000"),
        "assist.js": ("slice(-60)", "attempt < 20", ", 150)"),
    }
    for name, literals in gone.items():
        text = (web / name).read_text(encoding="utf-8")
        for literal in literals:
            assert literal not in text, f"{name} still decides {literal!r} for the plant"


def test_the_admin_screen_stopped_keeping_its_own_toast():
    """3500 in one file and 4000 in another was two files disagreeing about
    how long a confirmation stays up. Settled by deleting the copy, not by
    choosing between the numbers."""
    from pathlib import Path

    web = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"
    text = (web / "admin.js").read_text(encoding="utf-8")
    assert "function toast(message" not in text
    assert "window.FS.toast(message, kind)" in text


# ------------------------------------- 4. where IT's three went


def test_it_has_no_configuration_workspace_of_its_own(admin):
    """Decision 0035 section 2 keeps IT outside the role model: no capability,
    no domain. A workspace of its own would need both."""
    assert "it" not in modules.DOMAIN_BY_SLUG
    assert set(modules.DOMAIN_BY_SLUG) == {"engineering", "quality", "administration"}


def test_its_three_settings_are_on_the_administration_page_and_gated_there(admin):
    """The person who administers a plant's accounts is already the only
    person who can reach these, so `users.manage` is the gate rather than a
    capability invented for the occasion."""
    page = admin.get("/dashboard/config/administration/sections").json()
    theirs = [row for row in page["items"]
              if any(k["key"].startswith("[system]") for k in row["pack_keys"])]
    assert {row["key"] for row in theirs} == {"local_model", "log_rotation", "fleet_probe"}
    assert all(row["define"] == "users.manage" for row in theirs)


def test_somebody_without_users_manage_reads_these_and_writes_none(client):
    """The page lists itself to everybody, as the other two Configuration
    pages do, and the gate is the server's rather than the screen's."""
    page = client.get("/dashboard/config/administration/sections").json()
    assert page["total"] == 19
    assert all(row["may_define"] is False for row in page["items"])

    refused = client.patch("/dashboard/config/administration/settings/toast_ms",
                           json={"value": "9000"})
    assert refused.status_code == 403
    assert "users.manage" in refused.json()["detail"]


# ------------------------------------- 5. what a plant may not edit away


def test_a_plant_writes_its_own_document_structure(session):
    """A plant whose quality system mandates Scope / Hazards / Steps / Records
    was getting the wrong shape, and that shape is the plant's."""
    from fsmes.services import drafting

    plant_settings.write(session, domain="administration", key="document_house_style",
                         written="- Scope, Hazards, Steps, Records, in that order.",
                         actor="admin")
    assert "Scope, Hazards, Steps, Records" in drafting.instructions(session)


def test_no_plant_can_tell_an_operator_to_adjust_a_reading_toward_the_middle(session):
    """The product invariant. Not a house style: the difference between a
    measurement and a fiction. A plant may write any structure it likes and
    the clause is still in the brief the model is given."""
    from fsmes.services import drafting

    plant_settings.write(session, domain="administration", key="document_house_style",
                         written="- Write whatever you like.", actor="admin")
    brief = drafting.instructions(session)
    assert "never be told to adjust a reading toward the middle" in brief
    assert "Never invent a tolerance, a tool, or a machine" in brief


def test_the_style_a_plant_may_write_is_the_structure_and_nothing_else():
    """The two halves are separate constants so that no edit to one can reach
    the other, and the shipped structure is the default of its own key."""
    from fsmes.config import Settings
    from fsmes.services import drafting

    shipped = Settings.model_fields["admin_document_house_style"].default
    assert shipped == drafting.HOUSE_STYLE
    assert "adjust a reading" not in drafting.HOUSE_STYLE
    assert "adjust a reading" in drafting.INVARIANTS


# ------------------------------------- the pack seeds them, once


def test_pack_apply_seeds_an_administration_setting_once_and_never_moves_it(session, pack):
    """The rule every other kind `fsmes pack apply` seeds already keeps. A
    pack that reached back into a number somebody had deliberately changed on
    a running plant would be the pack overruling the plant."""
    first = plant_settings.seed(session, pack)
    assert first["made"] == 4 and first["present"] == 0
    assert plant_settings.setting(session, "admin", "default_new_account_role") == "viewer"
    assert plant_settings.setting(session, "admin", "walkthrough_max_steps") == 90
    assert plant_settings.setting(session, "screens", "floor_refresh_ms") == 10000
    assert plant_settings.setting(session, "system", "local_model_name") == "llama3:70b"

    plant_settings.write(session, domain="administration", key="walkthrough_max_steps",
                         written="30", actor="admin")
    again = plant_settings.seed(session, pack)
    assert again == {"made": 0, "present": 4}
    assert plant_settings.setting(session, "admin", "walkthrough_max_steps") == 30


def test_a_key_the_pack_does_not_carry_is_not_given_a_row(session, pack):
    """A row per key would make every plant's table the whole schema and
    nothing would ever read as *the product's default, unchanged*."""
    plant_settings.seed(session, pack)
    rows = {(r.section, r.key) for r in session.scalars(select(PlantSetting))}
    assert ("screens", "toast_ms") not in rows
    assert ("admin", "agent_max_rounds") not in rows


# ------------------------------------- the checker, on the same sentences


@pytest.mark.parametrize("key, value, expect", [
    ("toast_ms", "0", "a confirmation nobody has time to read"),
    ("floor_refresh_ms", "0", "without stopping"),
    ("agent_max_rounds", "-1", "an agent given no turns"),
    ("walkthrough_default_capability", "plant.imagine", "not a capability this version has"),
    ("assistant_timeout_seconds", "soon", "is not a number"),
    ("document_house_style", "   ", "Leave the key out"),
    ("local_model_name", " ", "Leave the key out"),
])
def test_a_value_the_pack_checker_refuses_is_refused_from_the_screen_too(
        admin, session, key, value, expect):
    """One wording for one rule. A number `fsmes pack check` refuses in a file
    is refused in the same sentence when somebody types it into the page."""
    answer = admin.patch(f"/dashboard/config/administration/settings/{key}",
                         json={"value": value})
    assert answer.status_code == 422, answer.text
    assert expect in answer.json()["detail"]
    assert session.scalars(select(PlantSetting)).all() == []


def test_a_ceiling_below_the_page_it_reads_is_refused_either_way_round(admin):
    """The pair rule, and the gap it closes: a rule that reports at whichever
    of its two keys reads best used to refuse the crossing-over one way round
    and accept it the other. Both are refused now, because a write is judged
    by what it *breaks*."""
    lowered = admin.patch("/dashboard/config/administration/settings/all_pages_cap",
                          json={"value": "100"})
    assert lowered.status_code == 422
    assert "read the first page and call the list incomplete" in lowered.json()["detail"]

    raised = admin.patch("/dashboard/config/administration/settings/all_pages_limit",
                         json={"value": "9000"})
    assert raised.status_code == 422
    assert "read the first page and call the list incomplete" in raised.json()["detail"]


def test_every_default_this_version_ships_is_inside_its_own_range():
    """A range that refused the shipped value would be a range that refused a
    plant for behaving exactly as the product does."""
    from fsmes.pack import check as checker

    for table, ranges in (("admin", checker.ADMIN_RANGES),
                          ("screens", checker.SCREEN_RANGES),
                          ("system", checker.SYSTEM_RANGES)):
        shipped = {name: plant_settings.shipped(table, name) for name in ranges}
        assert checker.CHECKERS[table](shipped) == [], table
