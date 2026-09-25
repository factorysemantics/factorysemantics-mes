"""Engineering's own numbers: seventeen settings, live on the same seam.

Scott, 2026-09-24/25, after Quality's eleven became editable: *"can the
methodology be expanded to all tabs?"*

This is that question answered for process and controls engineering, on the
mechanism §11 of `docs/design/config-assistance.md` wrote down - **seeded by
the pack, owned by the database** - and the point of the file is that the
mechanism needed nothing added to it. Seventeen `ConfigSection`s set
`edit_here=True` and named a `define`, and everything else was already there.

So this file does not re-prove the mechanism; `test_live_plant_settings.py`
does that, and its registry-side tests already cover these seventeen. What it
proves is the same five things about *these* keys, in the order the change has
to be true in:

1. A plant that configures nothing behaves exactly as it did. Rule one of the
   configuration audit, and the reason nothing had to be migrated.
2. A plant that had already changed a pack key keeps that value: the compiled
   setting is still the second layer.
3. A number edited on the screen is in force on the next reading, including in
   the services a person can see it in.
4. `fsmes pack apply` seeds each of them once and never overwrites one.
5. The right capability gates each of them - `process.define` for the eleven
   process rows and `signals.define` for the six controls ones - and the pack
   checker's own rules refuse the rest in its own sentences.
"""

from datetime import time

import pytest
from sqlalchemy import select

from fsmes.domain import PlantSetting
from fsmes.services import (
    analysis,
    coverage,
    maintenance,
    plant_settings,
    scheduling,
    triggers,
    uns,
)
from fsmes.services import calendar as calendar_service

#: A pack that states four of the twenty-two and says nothing about the rest,
#: because that is what a real pack looks like: a plant writes down the numbers
#: it disagrees with the product about and leaves the others alone.
FOUR_OF_THEM = '''
[pack]
format = 1
requires = ">=0.1.2"

[plant]
name = "twelve-hour-plant"
label = "A plant that works twelve-hour shifts, Sunday to Thursday"
timezone = "Europe/Berlin"
profile = "laptop"

[process]
default_report_hours = 12
working_week_mask = "0111110"
report_windows = [1, 12, 24]

[controls]
uns_max_attempts = 20
'''


@pytest.fixture()
def pack(tmp_path):
    """The pack above, read - and checked, because a pack this file builds a
    claim on has to be one `fsmes pack check` would accept."""
    from fsmes.pack import check as checker
    from fsmes.pack import format as fmt

    (tmp_path / "plant.toml").write_text(FOUR_OF_THEM, encoding="utf-8")
    report = checker.check(tmp_path)
    assert report.ok, report.render()
    return fmt.read(tmp_path)


# ------------------------------------- 1. a plant that configures nothing


def test_a_plant_that_configures_nothing_reads_exactly_what_it_read_before(session):
    """Rule one, for all seventeen. Not one row in `plant_settings`, and every
    accessor answers the literal that was in the source before any of them was
    a key. A plant upgrading to this version and never opening the page is this
    test, and it is why nothing needed migrating: `plant_settings` was already
    there, and an empty table is the product's own behaviour."""
    assert session.scalars(select(PlantSetting)).all() == []

    # Process engineering's eleven.
    assert maintenance.due_soon_fraction(session) == 0.8
    assert maintenance.default_job_minutes(session) == 60.0
    assert maintenance.plan_default_minutes(session) == 30.0
    assert scheduling.default_cycle_seconds(session) == 3.0
    assert scheduling.default_horizon_hours(session) == 24.0
    assert calendar_service.default_report_hours(session) == 8.0
    assert calendar_service.previous_horizon_days(session) == 14
    assert calendar_service.working_week_mask(session) == "1111100"
    assert analysis.gantt_screenful(session) == 12
    assert coverage.min_observed_seconds(session) == 10.0
    assert plant_settings.value(session, "process", "report_windows",
                                [0.25, 1, 8, 24, 168]) == [0.25, 1, 8, 24, 168]

    # Controls engineering's six.
    assert triggers.reload_seconds(session) == 30.0
    assert triggers.default_cooldown_seconds(session) == 300.0
    policy = uns.retry_policy(session)
    assert (policy.attempts, policy.base_seconds, policy.max_seconds) == (8, 5, 3600)
    read = plant_settings.value
    assert read(session, "controls", "opc_book_attempts", 4) == 4
    assert read(session, "controls", "opc_book_backoff_s", 0.5) == 0.5
    assert read(session, "controls", "opc_history_ratio", 10) == 10
    assert read(session, "controls", "opc_min_history_ms", 1000) == 1000
    assert read(session, "controls", "opc_order_sync_seconds", 2.0) == 2.0
    assert read(session, "controls", "opc_adjustment_poll_seconds", 5.0) == 5.0


def test_a_window_a_caller_asks_for_nothing_about_is_the_plants_own(session):
    """The five analysis paths took `hours: float = 8.0` in their signatures,
    which was the product telling a twelve-hour plant that its own default was
    somebody else's. None is the plant's answer now, and it is the same answer
    the old literal gave until the plant says otherwise."""
    drawn = analysis.oee_breakdown(session)
    assert drawn["window"]["requested_hours"] == 8.0

    plant_settings.write(session, domain="engineering", key="default_report_hours",
                         written="12", actor="PE")
    assert analysis.oee_breakdown(session)["window"]["requested_hours"] == 12.0


def test_the_page_says_the_value_is_the_products_default_when_no_row_holds_it(admin):
    """The column exists so nobody has to guess what their plant is set to, and
    with no row and no pack setting it gives the first of its two honest
    answers. `set_by` is null: *nothing here has taken ownership of this key*."""
    page = admin.get("/dashboard/config/engineering/sections").json()
    keys = {key["key"]: key for row in page["items"] for key in row["pack_keys"]}
    assert keys["[process] default_report_hours"]["value"] == "8.0"
    assert keys["[process] default_report_hours"]["is_default"] is True
    assert keys["[process] default_report_hours"]["set_by"] is None
    assert keys["[controls] uns_max_attempts"]["value"] == "8"
    assert keys["[oee] min_observed_seconds"]["value"] == "10.0"


# ------------------------------------- 2. what an upgrading plant keeps


def test_a_plant_that_had_already_changed_a_pack_key_keeps_that_value(
        session, monkeypatch):
    """Nothing was migrated, which is what makes this true.

    There is no new table and no new column in this change - `plant_settings`
    arrived with Quality's eleven - so an upgrading plant has nothing to move.
    A plant already running on a compiled `MES_PROCESS_*` setting reads it
    through the second layer and goes on behaving as it did.
    """
    from fsmes.config import get_settings

    monkeypatch.setattr(get_settings(), "process_default_report_hours", 12.0,
                        raising=False)
    monkeypatch.setattr(get_settings(), "controls_uns_max_attempts", 20, raising=False)
    monkeypatch.setattr(get_settings(), "process_working_week_mask", "0111110",
                        raising=False)

    assert session.scalars(select(PlantSetting)).all() == []
    assert calendar_service.default_report_hours(session) == 12.0
    assert calendar_service.working_week_mask(session) == "0111110"
    assert uns.retry_policy(session).attempts == 20


# ------------------------------------- 3. editing one from the screen


def test_a_process_number_edited_on_the_page_is_in_force_on_the_next_reading(
        admin, session):
    """No restart anywhere. The service reads the row through the session the
    next request already has."""
    answer = admin.patch("/dashboard/config/engineering/settings/gantt_screenful",
                         json={"value": "40"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["value"] == "40"
    assert answer.json()["is_default"] is False
    assert answer.json()["set_by"] == "ADMIN"

    plant_settings.forget(session)
    assert analysis.gantt_screenful(session) == 40


def test_a_controls_number_edited_on_the_page_is_in_force_on_the_next_reading(
        admin, session):
    """The same, through `signals.define` rather than `process.define`, and on
    a policy read as three keys at once."""
    for key, value in (("uns_max_attempts", "20"),
                       ("uns_base_backoff_s", "10"),
                       ("uns_max_backoff_s", "600")):
        assert admin.patch(f"/dashboard/config/engineering/settings/{key}",
                           json={"value": value}).status_code == 200, key

    plant_settings.forget(session)
    policy = uns.retry_policy(session)
    assert (policy.attempts, policy.base_seconds, policy.max_seconds) == (20, 10, 600)
    # And the curve the plant asked for is the curve the outbox waits on.
    assert uns.backoff_seconds(1, policy) == 10
    assert uns.backoff_seconds(2, policy) == 20
    assert uns.backoff_seconds(99, policy) == 600


def test_a_window_list_edited_on_the_page_is_what_the_screens_are_told_to_offer(
        admin, session):
    """`report_windows` is a list of measurements, which is a kind of key this
    product did not have until this change - `ints` cannot say that a quarter of
    an hour is a real window."""
    assert admin.patch("/dashboard/config/engineering/settings/report_windows",
                       json={"value": "1,12,24"}).status_code == 200
    said = admin.get("/dashboard/screens").json()
    assert said["report_windows"] == [1.0, 12.0, 24.0]


def test_the_screens_endpoint_answers_with_what_this_plant_draws_with(admin, session):
    """The four numbers the browser itself draws with, and the fifth it prints.

    They come through an endpoint rather than in the payload they belong to
    because a time picker is built before any panel has asked for anything -
    and `common.js` held its own copy of the list and its own eight hours until
    2026-09-25, so a plant that offered a twelve-hour window on the server
    offered the product's five in the browser.
    """
    said = admin.get("/dashboard/screens").json()
    assert said == {
        "report_windows": [0.25, 1.0, 8.0, 24.0, 168.0],
        "default_report_hours": 8.0,
        "working_week_mask": "1111100",
        "schedule_default_horizon_hours": 24.0,
        "maintenance_due_soon_fraction": 0.8,
    }

    admin.patch("/dashboard/config/engineering/settings/working_week_mask",
                json={"value": "0111110"})
    admin.patch("/dashboard/config/engineering/settings/schedule_default_horizon_hours",
                json={"value": "336"})
    moved = admin.get("/dashboard/screens").json()
    assert moved["working_week_mask"] == "0111110"
    assert moved["schedule_default_horizon_hours"] == 336.0


def test_a_shift_that_names_no_days_takes_this_plants_week(admin, session):
    """The working week is the plant's and the mask format is the product's.
    Every pattern that names its own days is untouched by this."""
    made = calendar_service.create_pattern(
        session, code="DAY-A", name="Days", starts=time(6),
        ends=time(18), actor="t")
    assert made.days == "1111100"

    assert admin.patch("/dashboard/config/engineering/settings/working_week_mask",
                       json={"value": "0111110"}).status_code == 200
    plant_settings.forget(session)
    after = calendar_service.create_pattern(
        session, code="DAY-B", name="Days B", starts=time(6),
        ends=time(18), actor="t")
    assert after.days == "0111110"
    # And the one made before it keeps the week it was made with.
    assert made.days == "1111100"


def test_a_new_maintenance_plan_takes_this_plants_house_default(admin, session):
    """The per-plan figure is the engineer's; only the house default was in
    code, in three places."""
    first = maintenance.create_plan(session, code="PM-A", name="Seals",
                                    equipment_code="MIX01",
                                    trigger="runtime_hours", interval=100.0)
    assert first.expected_minutes == 30.0

    assert admin.patch(
        "/dashboard/config/engineering/settings/maintenance_plan_default_minutes",
        json={"value": "45"}).status_code == 200
    plant_settings.forget(session)
    second = maintenance.create_plan(session, code="PM-B", name="Belts",
                                     equipment_code="MIX01",
                                     trigger="runtime_hours", interval=100.0)
    assert second.expected_minutes == 45.0
    # A plan that states its own minutes is not touched by the default at all.
    third = maintenance.create_plan(session, code="PM-C", name="Filter",
                                    equipment_code="MIX01", trigger="runtime_hours",
                                    interval=100.0, expected_minutes=5.0)
    assert third.expected_minutes == 5.0


def test_a_new_trigger_takes_this_plants_inherited_cooldown(admin, session):
    """Five minutes of silence is right on a continuous line and wrong on a
    station with forty-second cycles. Zero is a real answer and is not read as
    *nothing was said*, which is what `|| 0` in the browser used to make it."""
    first = triggers.create(session, code="T-A", name="Hot", tag="Temperature",
                            condition="above", threshold=90.0, actor="t")
    assert first.cooldown_seconds == 300.0

    assert admin.patch(
        "/dashboard/config/engineering/settings/trigger_default_cooldown_seconds",
        json={"value": "40"}).status_code == 200
    plant_settings.forget(session)
    assert triggers.create(session, code="T-B", name="Hot B", tag="Temperature",
                           condition="above", threshold=90.0,
                           actor="t").cooldown_seconds == 40.0
    assert triggers.create(session, code="T-C", name="Hot C", tag="Temperature",
                           condition="above", threshold=90.0, cooldown_seconds=0.0,
                           actor="t").cooldown_seconds == 0.0


def test_what_coming_due_means_is_sent_with_every_plan_it_judges(admin, session):
    """The browser coloured its bar amber at eight tenths of its own, so a plant
    that warned at 70% got a sentence saying *due soon* ten per cent before the
    bar agreed. The judgment is the server's now and rides on the row."""
    assert admin.patch(
        "/dashboard/config/engineering/settings/maintenance_due_soon_fraction",
        json={"value": "0.5"}).status_code == 200
    plant_settings.forget(session)

    plan = maintenance.create_plan(session, code="PM-D", name="Seals",
                                   equipment_code="MIX01",
                                   trigger="runtime_hours", interval=10.0)
    status = maintenance.status_of(session, plan)
    assert status["due_soon_fraction"] == 0.5


# ------------------------------------- 4. what a pack may and may not do


def test_pack_apply_seeds_each_of_them_once_and_never_overwrites_one(session, pack):
    """The rule every kind `fsmes pack apply` seeds already keeps, now for two
    more pack tables. A pack that reached back into a number somebody had
    deliberately changed on a running plant would be the pack overruling the
    plant."""
    first = plant_settings.seed(session, pack)
    session.flush()
    assert first == {"made": 4, "present": 0}
    assert calendar_service.default_report_hours(session) == 12.0
    assert calendar_service.working_week_mask(session) == "0111110"
    assert uns.retry_policy(session).attempts == 20
    # The eighteen it says nothing about stand at the product's own numbers.
    assert analysis.gantt_screenful(session) == 12
    assert coverage.min_observed_seconds(session) == 10.0

    again = plant_settings.seed(session, pack)
    session.flush()
    assert again == {"made": 0, "present": 4}

    plant_settings.write(session, domain="engineering", key="default_report_hours",
                         written="10", actor="PE")
    session.flush()
    assert plant_settings.seed(session, pack) == {"made": 0, "present": 4}
    session.flush()
    plant_settings.forget(session)
    assert calendar_service.default_report_hours(session) == 10.0
    by = {row.key: row.set_by for row in session.scalars(select(PlantSetting))}
    assert by["default_report_hours"] == "PE"
    assert by["working_week_mask"] == "pack-apply"
    assert by["uns_max_attempts"] == "pack-apply"


def test_a_pack_that_omits_a_shifts_days_is_seeded_this_plants_week_not_the_products(
        session, pack, tmp_path):
    """The question the audit left open on `pack/masterdata.py`, answered.

    It said the honest fix might be to **refuse** a pack that omits `days`,
    because seeding Monday-to-Friday was a guess about somebody else's plant.
    It is not a guess any more: the mask is `[process] working_week_mask`, the
    plant's own stated answer, so the seeder fills the field in from it the way
    it fills every other one - and refusing would break every pack that leaves
    `days` out to mean *the usual week here* while buying nothing the key does
    not already buy.
    """
    import json

    plant_settings.seed(session, pack)
    session.flush()

    masterdata_dir = tmp_path / "masterdata"
    masterdata_dir.mkdir()
    (masterdata_dir / "shifts.json").write_text(json.dumps(
        [{"code": "TWELVE", "name": "Twelve hours", "starts": "06:00", "ends": "18:00"}]),
        encoding="utf-8")

    from fsmes.pack import masterdata as seeder

    receipt = seeder.seed(session, masterdata_dir)
    assert receipt["shifts"]["made"] == 1

    from fsmes.domain import ShiftPattern

    made = session.scalar(select(ShiftPattern).where(ShiftPattern.code == "TWELVE"))
    assert made.days == "0111110", "the plant said Sunday to Thursday in its pack"


# ------------------------------------- 5. who may write, and what is refused


def test_process_rows_are_gated_on_process_define_and_controls_on_signals_define(admin):
    """Two capabilities on one page, which is what 0035 §2 said Engineering is:
    process engineering and controls engineering are different jobs, and the
    page says which door is which.

    `signals.define` is new. 0035 named it on 2026-09-21 and said adding it
    later would be cheap because a capability is a string and a role is a list
    of them; this is the first thing there has been to gate on it.
    """
    page = admin.get("/dashboard/config/engineering/sections").json()
    live = [row for row in page["items"] if row["edit_here"]]
    assert len(live) == 17
    assert page["total"] == 18
    assert sum(len(row["pack_keys"]) for row in live) == 22

    by_capability: dict[str, int] = {}
    for row in live:
        by_capability[row["define"]] = by_capability.get(row["define"], 0) + 1
    assert by_capability == {"process.define": 11, "signals.define": 6}
    assert all(row["approve"] is None for row in live)

    # The vocabulary beside them keeps its approval step: its words are written
    # onto records that outlive it, which is the line §11 draws.
    vocabulary = next(row for row in page["items"] if not row["edit_here"])
    assert vocabulary["key"] == "downtime_reasons"
    assert vocabulary["approve"] == "process.approve"


def test_a_caller_without_the_sections_define_capability_cannot_write_it(
        client, session):
    """The gate is the section's own, read live from the role rather than from
    the token. An operator may read the page - a setting nobody can read is a
    setting nobody can choose against - and may not write either half of it."""
    refused = client.patch("/dashboard/config/engineering/settings/gantt_screenful",
                           json={"value": "40"})
    assert refused.status_code == 403
    assert "process.define" in refused.json()["detail"]

    other = client.patch("/dashboard/config/engineering/settings/uns_max_attempts",
                         json={"value": "20"})
    assert other.status_code == 403
    assert "signals.define" in other.json()["detail"]

    assert session.scalars(select(PlantSetting)).all() == []
    assert client.get("/dashboard/config/engineering/sections").status_code == 200


def test_an_agent_may_not_retune_the_plants_signals(client, sign_in, session):
    """The `agent` role holds `process.define` - it may draft a vocabulary for
    somebody to approve - and deliberately not `signals.define`. Retuning how
    hard the OPC agent retries a booking is not drafting: there is nobody in
    the loop after it."""
    agent = sign_in("ROBOT", role="agent")
    assert agent.patch("/dashboard/config/engineering/settings/gantt_screenful",
                       json={"value": "40"}).status_code == 200
    refused = agent.patch("/dashboard/config/engineering/settings/opc_book_attempts",
                          json={"value": "8"})
    assert refused.status_code == 403
    assert "signals.define" in refused.json()["detail"]


@pytest.mark.parametrize("key, value, expect", [
    ("maintenance_due_soon_fraction", "0", "between 0 and 1"),
    ("maintenance_due_soon_fraction", "1.5", "between 0 and 1"),
    ("default_cycle_seconds", "0", "above 0"),
    ("default_report_hours", "800", "between 0 and 720"),
    ("gantt_screenful", "0", "above 0"),
    ("working_week_mask", "11111", "seven characters of 0 or 1"),
    ("working_week_mask", "1111112", "seven characters of 0 or 1"),
    ("report_windows", "", "is empty"),
    ("report_windows", "1,1,8", "twice"),
    ("report_windows", "1,900", "at most 720"),
    ("report_windows", "1,noon", "is not a number"),
    ("min_observed_seconds", "0", "above 0"),
    ("opc_book_attempts", "0", "above 0"),
    ("uns_max_attempts", "0", "above 0"),
    ("trigger_reload_seconds", "0", "above 0"),
    ("opc_history_ratio", "0", "at least 1"),
    ("gantt_screenful", "half", "is not a whole number"),
])
def test_a_value_the_pack_checker_refuses_is_refused_from_the_screen_too(
        admin, session, key, value, expect):
    """One wording for one rule. A number `fsmes pack check` refuses in a file
    is refused in the same sentence when somebody types it into the page, and a
    second copy of those ranges behind an input is how a screen comes to accept
    what a pack cannot."""
    answer = admin.patch(f"/dashboard/config/engineering/settings/{key}",
                         json={"value": value})
    assert answer.status_code == 422, answer.text
    assert expect in answer.json()["detail"]
    assert session.scalars(select(PlantSetting)).all() == []


@pytest.mark.parametrize("key, value", [
    ("opc_book_backoff_s", "0"),
    ("uns_base_backoff_s", "0"),
    ("trigger_default_cooldown_seconds", "0"),
    ("opc_min_history_ms", "0"),
    ("opc_history_ratio", "1"),
])
def test_zero_is_a_real_answer_where_zero_means_something(admin, key, value):
    """*Retry at once*, *no cooldown*, *no floor under the sampling interval*
    and *sample the rest of the tags as fast as the semantic ones* are plants
    answering their own question. A checker that refused them would be having an
    opinion rather than keeping a rule, which is why the ranges carry a flag for
    whether their own floor is allowed."""
    answer = admin.patch(f"/dashboard/config/engineering/settings/{key}",
                         json={"value": value})
    assert answer.status_code == 200, answer.text


def test_a_backoff_curve_may_not_start_above_the_ceiling_it_is_capped_at(admin):
    """The one pair in `[controls]`, and it is the same kind of thing as the two
    Cpk pairs: nothing is refused by the numbers crossing over, the curve is
    just quietly capped below where it starts, so every retry waits the ceiling
    and the doubling does nothing.

    Refused on the half the complaint is filed against - the base - exactly as
    the Cpk pair is refused on `cpk_marginal` and not on `cpk_capable`. That
    asymmetry is deliberate and is #100's: refusing both halves would leave
    somebody moving a whole pair unable to type either number first, and the
    page's Save already retries what a pair rule refused once the other half has
    landed.
    """
    # An hour's ceiling with a two-hour first wait is the curve capped below
    # where it starts. The refusal quotes the ceiling this plant is actually
    # running on rather than the product's default.
    crossed = admin.patch("/dashboard/config/engineering/settings/uns_base_backoff_s",
                          json={"value": "7200"})
    assert crossed.status_code == 422, crossed.text
    assert "cannot start above the ceiling" in crossed.json()["detail"]
    assert "`uns_max_backoff_s` is 3600" in crossed.json()["detail"]

    # Raise the ceiling first and the same base is fine, which is the point: the
    # pair is judged against what this plant is running on for the other half,
    # not against the product's default.
    assert admin.patch("/dashboard/config/engineering/settings/uns_max_backoff_s",
                       json={"value": "86400"}).status_code == 200
    assert admin.patch("/dashboard/config/engineering/settings/uns_base_backoff_s",
                       json={"value": "7200"}).status_code == 200


def test_every_default_this_product_ships_is_inside_its_own_range():
    """A range that refused the shipped value would be a range that refused a
    plant for behaving exactly as the product does."""
    from fsmes.config import Settings
    from fsmes.pack import check as checker

    for section, ranges in (("process", checker.PROCESS_RANGES),
                            ("controls", checker.CONTROLS_RANGES)):
        table = {}
        for name in ranges:
            field = f"{section}_{name}"
            assert field in Settings.model_fields, f"[{section}] {name} compiles to nothing"
            table[name] = Settings.model_fields[field].default
        judge = getattr(checker, f"{section}_numbers")
        assert judge(table) == [], f"a shipped [{section}] default is outside its own range"

    assert checker.oee_numbers({"min_observed_seconds":
                                Settings.model_fields["oee_min_observed_seconds"].default}) == []
    shipped = Settings.model_fields["process_report_windows"].default
    assert checker.process_numbers(
        {"report_windows": [float(h) for h in shipped.split(",")]}) == []


# ------------------------------------- the browser holds no second copy


def test_the_browser_keeps_no_copy_of_the_window_list_or_its_default():
    """`common.js` held both as literals, so a plant that offered a twelve-hour
    window on the server offered the product's five in the browser and opened
    on eight hours whatever it had said. The shipped five stay as the answer
    before the fetch comes back; what must not be there is a second *label*
    table, because a label written beside a number is a label that can disagree
    with it."""
    from pathlib import Path

    common = (Path(__file__).resolve().parents[1]
              / "src" / "fsmes" / "web" / "common.js").read_text(encoding="utf-8")
    assert "/dashboard/screens" in common, "the browser has to ask what this plant offers"
    assert '[[0.25, "15 min"]' not in common, "the hand-written label table is gone"
    assert "windowLabel" in common, "a label comes from the number it is for"


def test_the_shared_labeller_reproduces_the_five_labels_that_were_written_by_hand():
    """Pinned as prose rather than run, because this is JavaScript: the
    arithmetic is stated here so a change to it is a change to this test.

    0.25 → 15 min, 1 → 1 hour, 8 → 8 hours, 24 → 24 hours, 168 → 7 days. Those
    were the five `common.js` carried, and the labeller has to give the same
    five for the same five numbers or the plant that changes nothing sees its
    picker change.
    """
    def label(h):
        if h < 1:
            return f"{round(h * 60)} min"
        if h == 1:
            return "1 hour"
        if h < 48:
            return f"{h if h % 1 else int(h)} hours"
        return f"{int(h / 24)} days"

    assert [label(h) for h in (0.25, 1, 8, 24, 168)] == [
        "15 min", "1 hour", "8 hours", "24 hours", "7 days"]


def test_the_maintenance_bar_reads_the_servers_judgment_and_not_a_number_of_its_own():
    """The 0.8 in `web/maintenance.js` was the second copy of a judgment the
    server had already made and already sent."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1]
          / "src" / "fsmes" / "web" / "maintenance.js").read_text(encoding="utf-8")
    assert "fraction >= 0.8" not in js
    assert "p.due_soon" in js
