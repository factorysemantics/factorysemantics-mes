"""The settings a plant owns: seeded by the pack, edited on the screen, in
force at once.

Scott, 2026-09-24, clicking through Quality's Configuration page: *"when I
click on stuff, it doesn't seem to take me to where I can actually make those
changes. Shouldn't it?"* Eleven of the twelve sections were pack-file literals
with no editor anywhere, and the link took him to the screen where the value's
*effect* was visible - the SPC chart, the gauge register - with no control to
change it.

Decision 0035 §3 had already named the shape: **tier one is seeded by the pack
and owned by the database**, which is what `shifts` had always done. This file
pins that shape, in the order the change has to be true in:

1. A plant that configures nothing behaves exactly as it did. This is rule one
   of the configuration audit and the reason the migration moves no data.
2. A plant that had already changed a pack key keeps that value across the
   upgrade, because the compiled setting is still the second layer.
3. A number edited on the screen persists, is audited, and the services read
   it on the next reading with no restart.
4. `fsmes pack apply` never overwrites a setting that is already there.
5. Only somebody holding the section's own `define` capability may write it,
   and the pack checker's own rules refuse the rest in the same sentences.
"""

import pytest
from sqlalchemy import select

from fsmes.domain import AuditLog, PlantSetting
from fsmes.services import coa, gauges, plant_settings, quality, serialization, spc

#: A pack that writes three of the eleven and leaves the rest to the product,
#: because that is what a real pack looks like: a plant states the numbers it
#: disagrees with the product about and says nothing about the others.
THREE_OF_THEM = '''
[pack]
format = 1
requires = ">=0.1.2"

[plant]
name = "owns-its-numbers"
label = "A plant that states three of its own quality numbers"
timezone = "Europe/Berlin"
profile = "laptop"

[quality]
spc_min_points = 25
hold_rules = [1, 3]
nc_code_prefix = "NCR"
'''


@pytest.fixture()
def pack(tmp_path):
    """The pack above, read."""
    from fsmes.pack import check as checker
    from fsmes.pack import format as fmt

    (tmp_path / "plant.toml").write_text(THREE_OF_THEM, encoding="utf-8")
    report = checker.check(tmp_path)
    assert report.ok, report.render()
    return fmt.read(tmp_path)


# ------------------------------------- 1. a plant that configures nothing


def test_a_plant_that_configures_nothing_reads_exactly_what_it_read_before(session):
    """Rule one, and the whole reason this change needed no data migration.

    Not one row in `plant_settings`, and every one of the eleven answers the
    literal the product shipped before any of them was a setting. A plant
    upgrading to this version and never touching the page is this test.
    """
    assert session.scalars(select(PlantSetting)).all() == []

    assert spc.min_points(session) == 12
    assert spc.history(session) == 200
    assert spc.cpk_bars(session) == (1.33, 1.0)
    assert spc.hold_rules(session) == (1, 2, 3, 4)
    assert spc.major_rules(session) == (1,)
    assert gauges.ratios(session) == (10.0, 4.0)
    assert gauges.default_interval_days(session) == 365
    assert coa.serials_listed(session) == 200
    assert serialization.max_depth(session) == 6
    assert serialization.serial_digits(session) == 6
    assert quality.nc_code_prefix(session) == "NC"


def test_the_page_says_the_value_is_the_products_default_when_no_row_holds_it(admin):
    """The column exists so nobody has to guess what their plant is set to, and
    it has two honest answers. With no row and no pack setting, it is the first
    of them - and `set_by` is null, which is *nothing here has taken ownership
    of this key*."""
    page = admin.get("/dashboard/config/quality/sections").json()
    keys = {key["key"]: key for row in page["items"] for key in row["pack_keys"]}
    assert keys["[quality] spc_min_points"]["value"] == "12"
    assert keys["[quality] spc_min_points"]["is_default"] is True
    assert keys["[quality] spc_min_points"]["set_by"] is None


# ------------------------------------- 2. what an upgrading plant keeps


def test_a_plant_that_had_already_changed_a_pack_key_keeps_that_value(
        session, monkeypatch):
    """The migration seeds nothing, which is what makes this true.

    A plant running on `[quality] spc_min_points = 25` has that number in its
    environment, compiled there by `fsmes pack apply`. After the upgrade the
    table is empty, so the second layer answers - the plant goes on drawing
    limits from twenty-five readings, and nobody had to find its pack file from
    inside a migration to make that so.
    """
    from fsmes.config import get_settings

    monkeypatch.setattr(get_settings(), "quality_spc_min_points", 25, raising=False)
    monkeypatch.setattr(get_settings(), "quality_hold_rules", "1,3", raising=False)

    assert session.scalars(select(PlantSetting)).all() == []
    assert spc.min_points(session) == 25
    assert spc.hold_rules(session) == (1, 3)


def test_the_page_says_this_plant_set_it_for_a_value_the_pack_compiled(
        admin, monkeypatch):
    from fsmes.config import get_settings

    monkeypatch.setattr(get_settings(), "quality_spc_history", 400, raising=False)
    page = admin.get("/dashboard/config/quality/sections").json()
    keys = {key["key"]: key for row in page["items"] for key in row["pack_keys"]}
    assert keys["[quality] spc_history"]["value"] == "400"
    assert keys["[quality] spc_history"]["is_default"] is False
    assert keys["[quality] spc_history"]["default"] == "200"


# ------------------------------------- 3. editing one from the screen


def test_editing_a_setting_from_the_screen_persists_and_the_services_read_it_at_once(
        admin, session):
    """The whole of what Scott asked for, in one test. No restart anywhere: the
    services read the row through the session the request already has."""
    answer = admin.patch("/dashboard/config/quality/settings/spc_min_points",
                         json={"value": "25"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["value"] == "25"
    assert answer.json()["is_default"] is False
    assert answer.json()["set_by"] == "ADMIN"

    row = session.scalar(select(PlantSetting).where(PlantSetting.key == "spc_min_points"))
    assert row.section == "quality" and row.value == "25"

    plant_settings.forget(session)
    assert spc.min_points(session) == 25


def test_a_rule_list_edited_from_the_screen_changes_which_rules_hold(admin, session):
    """`hold_rules` is a list, and an empty one is a real answer: *draw and
    record every rule, raise a hold on none of them*."""
    assert admin.patch("/dashboard/config/quality/settings/hold_rules",
                       json={"value": "1,3"}).status_code == 200
    plant_settings.forget(session)
    assert spc.hold_rules(session) == (1, 3)

    assert admin.patch("/dashboard/config/quality/settings/hold_rules",
                       json={"value": ""}).status_code == 200
    plant_settings.forget(session)
    assert spc.hold_rules(session) == ()


def test_editing_a_setting_is_audited_with_what_it_was_and_what_it_became(
        admin, session):
    """A number that judges every chart on the plant does not move without a
    record of who moved it. Twice, because the second write is the one that has
    a *before*."""
    admin.patch("/dashboard/config/quality/settings/serial_digits", json={"value": "8"})
    admin.patch("/dashboard/config/quality/settings/serial_digits", json={"value": "4"})

    rows = session.scalars(select(AuditLog)
                           .where(AuditLog.entity_type == "plant_setting")
                           .order_by(AuditLog.id)).all()
    assert [r.entity_id for r in rows] == ["[quality] serial_digits"] * 2
    assert [r.actor for r in rows] == ["ADMIN", "ADMIN"]
    assert rows[0].before is None, "the first write took ownership; there was no row"
    assert rows[0].after["value"] == "8"
    assert rows[1].before["value"] == "8" and rows[1].after["value"] == "4"


def test_a_serial_a_plant_numbers_its_own_way_is_numbered_that_way_at_once(
        admin, session):
    """One of the eleven read on a hot path, proved end to end: the width comes
    from the row, and the hyphen stays the product's because the recovery scan
    reads `PREFIX-digits`."""
    assert serialization.next_serial(session, "F") == "F-000001"
    admin.patch("/dashboard/config/quality/settings/serial_digits", json={"value": "8"})
    plant_settings.forget(session)
    assert serialization.next_serial(session, "K") == "K-00000001"
    # And the counter this plant already has keeps counting, whatever the width.
    assert serialization.next_serial(session, "F") == "F-00000002"


# ------------------------------------- 4. what a pack may and may not do


def test_pack_apply_seeds_a_setting_once_and_never_overwrites_it(session, pack):
    """The rule every kind `fsmes pack apply` seeds already keeps. A pack that
    reached back into a number somebody had deliberately changed on a running
    plant would be the pack overruling the plant, which is the opposite of what
    a pack is for."""
    carried = set(pack.table("quality"))
    assert carried == {"spc_min_points", "hold_rules", "nc_code_prefix"}

    first = plant_settings.seed(session, pack)
    session.flush()
    assert first == {"made": 3, "present": 0}
    assert spc.min_points(session) == 25
    assert spc.hold_rules(session) == (1, 3)
    assert quality.nc_code_prefix(session) == "NCR"
    # And the eight it says nothing about stand at the product's own numbers.
    assert spc.history(session) == 200
    assert serialization.serial_digits(session) == 6

    again = plant_settings.seed(session, pack)
    session.flush()
    assert again == {"made": 0, "present": 3}

    # A value changed since is left exactly as it is, and `set_by` says which
    # door each of them came in by.
    plant_settings.write(session, domain="quality", key="spc_min_points",
                         written="30", actor="QE")
    session.flush()
    assert plant_settings.seed(session, pack) == {"made": 0, "present": 3}
    session.flush()
    plant_settings.forget(session)
    assert spc.min_points(session) == 30
    by = {row.key: row.set_by for row in session.scalars(select(PlantSetting))}
    assert by == {"spc_min_points": "QE", "hold_rules": "pack-apply",
                  "nc_code_prefix": "pack-apply"}


def test_a_setting_the_pack_seeded_reads_as_this_plants_own_on_the_page(
        admin, session, pack):
    """A row `fsmes pack apply` wrote is *this plant set it*, because the plant
    did - in its pack. Naming the pack would be a guess: by the time a plant is
    serving, the file it was built from is not recorded anywhere the running
    process can see."""
    plant_settings.seed(session, pack)
    session.flush()
    page = admin.get("/dashboard/config/quality/sections").json()
    keys = {key["key"]: key for row in page["items"] for key in row["pack_keys"]}
    assert keys["[quality] spc_min_points"]["value"] == "25"
    assert keys["[quality] spc_min_points"]["is_default"] is False
    assert keys["[quality] spc_min_points"]["set_by"] == "pack-apply"
    assert keys["[quality] hold_rules"]["value"] == "1,3"
    assert keys["[quality] spc_history"]["is_default"] is True


# ------------------------------------- 5. who may write, and what is refused


def test_a_caller_without_the_sections_define_capability_cannot_write_it(
        client, sign_in, session):
    """The gate is the section's own `quality.define`, read live from the role
    rather than from the token. An operator may read the page - a setting
    nobody can read is a setting nobody can choose against - and may not
    write."""
    refused = client.patch("/dashboard/config/quality/settings/spc_min_points",
                           json={"value": "25"})
    assert refused.status_code == 403
    assert "quality.define" in refused.json()["detail"]
    assert session.scalars(select(PlantSetting)).all() == []

    # The reading is open, which is the other half of the same rule.
    assert client.get("/dashboard/config/quality/sections").status_code == 200

    # And an agent holds it: the role is built with `quality.define`, so the
    # API is open to the tool `config-agent-tools` builds next.
    agent = sign_in("ROBOT", role="agent")
    assert agent.patch("/dashboard/config/quality/settings/spc_min_points",
                       json={"value": "25"}).status_code == 200


def test_a_caller_with_no_session_at_all_is_refused_before_anything_else(anon):
    assert anon.patch("/dashboard/config/quality/settings/spc_min_points",
                      json={"value": "25"}).status_code == 401


@pytest.mark.parametrize("key, value, expect", [
    ("spc_min_points", "1", "at least two readings"),
    ("spc_history", "0", "at least one reading"),
    ("serial_digits", "40", "between 1 and 12"),
    ("containment_max_depth", "99", "hard ceiling of twelve"),
    ("nc_code_prefix", "ncr", "upper case"),
    ("hold_rules", "1,7", "this product has four"),
    ("spc_min_points", "twelve", "is not a whole number"),
    ("cpk_capable", "", "is not a number"),
])
def test_a_value_the_pack_checker_refuses_is_refused_from_the_screen_too(
        admin, session, key, value, expect):
    """One wording for one rule. A number `fsmes pack check` refuses in a file
    is refused in the same sentence when somebody types it into the page, and a
    second copy of those ranges behind an input is how a screen comes to accept
    what a pack cannot."""
    answer = admin.patch(f"/dashboard/config/quality/settings/{key}",
                         json={"value": value})
    assert answer.status_code == 422, answer.text
    assert expect in answer.json()["detail"]
    assert f"[quality] {key}" in answer.json()["detail"]
    assert session.scalars(select(PlantSetting)).all() == []


def test_a_pair_is_judged_against_the_value_this_plant_is_running_on(admin, session):
    """The two pairs are each one judgment written as two numbers, and the
    numbers crossing over refuses nothing on its own - it quietly makes one of
    them unreachable. So `cpk_marginal` is checked against the `cpk_capable`
    this plant has, not against the product's default."""
    crossed = admin.patch("/dashboard/config/quality/settings/cpk_marginal",
                          json={"value": "1.5"})
    assert crossed.status_code == 422
    assert "nothing would ever be called marginal" in crossed.json()["detail"]

    # Raise the bar first and the same value is fine, which is the point: the
    # screen's Save retries what the pair rule refused for exactly this reason.
    assert admin.patch("/dashboard/config/quality/settings/cpk_capable",
                       json={"value": "2.0"}).status_code == 200
    assert admin.patch("/dashboard/config/quality/settings/cpk_marginal",
                       json={"value": "1.5"}).status_code == 200
    plant_settings.forget(session)
    assert spc.cpk_bars(session) == (2.0, 1.5)


def test_a_setting_this_version_does_not_have_is_refused_and_says_what_it_has(admin):
    """A setting somebody believes they saved is worse than one they were told
    does not exist."""
    answer = admin.patch("/dashboard/config/quality/settings/sorcery",
                         json={"value": "9"})
    assert answer.status_code == 404
    assert "spc_min_points" in answer.json()["detail"]

    workspace = admin.patch("/dashboard/config/sorcery/settings/spc_min_points",
                            json={"value": "9"})
    assert workspace.status_code == 404
    assert "quality" in workspace.json()["detail"]


# ------------------------------------- the pattern, not just Quality


def test_every_live_section_names_the_capability_that_may_write_it(admin):
    """The seam, held from the registry's side. A section that marked itself
    `edit_here` and named no `define` would be a setting anybody who can see
    the page may change - which is what `pack_keys` existed to avoid saying."""
    for section in plant_settings.live_sections():
        assert section.define, f"{section.key} is editable here and names nobody"
        assert section.approve is None, (
            f"{section.key} has an approval step; rule three of decision 0035 says a "
            "number that takes effect when it is saved has no pending state")
        assert section.pack_keys, f"{section.key} is editable here and has no keys"


def test_every_key_a_live_section_names_exists_in_this_versions_pack_schema(admin):
    """The other side of the same seam: a row with an input that writes a key
    the pack schema does not have would be an input that 404s on Save."""
    from fsmes.pack import format as fmt

    for section in plant_settings.live_sections():
        for written in section.pack_keys:
            where, name = plant_settings.split(written)
            key = fmt.key_named(where, name)
            assert key is not None, f"{written} is on a Configuration page and in no pack"
            assert key.becomes, f"{written} compiles to no setting, so nothing reads it"


def test_all_eleven_quality_sections_are_live_and_none_asks_for_an_approver(admin):
    """What this change is, counted. Eleven sections, thirteen keys - the Cpk
    bars and the gauge ratios are each one section of two - and the twelfth
    section is the severity vocabulary, which keeps its approval step because a
    word the whole quality record is written in is a controlled thing."""
    page = admin.get("/dashboard/config/quality/sections").json()
    live = [row for row in page["items"] if row["edit_here"]]
    assert len(live) == 11
    assert page["total"] == 12
    assert sum(len(row["pack_keys"]) for row in live) == 13
    assert all(row["define"] == "quality.define" for row in live)
    assert all(row["approve"] is None for row in live)

    vocabulary = next(row for row in page["items"] if not row["edit_here"])
    assert vocabulary["key"] == "nc_severities"
    assert vocabulary["approve"] == "quality.approve"
