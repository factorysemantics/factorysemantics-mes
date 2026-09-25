"""Supply chain's own numbers: what this plant asks of the link to its ERP.

The second domain to take the live-settings pattern (`docs/design/
config-assistance.md` §11), and the first built from nothing since #92 made a
configuration domain possible at all. Six sections, ten `[erp]` keys, one new
capability - `erp.define`, named in decision 0035 §2 on 2026-09-21 and unused
until now.

The order this file is written in is the order the change has to be true in,
the same order `test_live_plant_settings.py` uses for Quality:

1. A plant that configures nothing behaves exactly as it did. Rule one of the
   configuration audit, and the reason no migration was needed at all: the
   table these rows live in has been there since 2026-09-24.
2. A plant whose pack already set one of these keeps it, through the second
   layer.
3. A number edited on the screen is in force on the next reading - including
   the four a *connector* applies, which the sync worker hands over once per
   cycle because a transport is handed no unit of work on purpose.
4. `fsmes pack apply` seeds each once and never overwrites.
5. Only somebody holding `erp.define` may write one, and `fsmes pack check`'s
   own rules refuse the rest in its own sentences.
6. The one section that is **not** live says so, in the words the page has
   always had for a setting that cannot move while a plant is running.
"""

import pytest
from sqlalchemy import select

from fsmes.domain import AuditLog, ErpMessage, MessageDirection, MessageStatus, PlantSetting
from fsmes.integrations.erp import base
from fsmes.integrations.erp.contract import ProductionRequest
from fsmes.services import erp, plant_settings

#: A pack that states three of the ten and says nothing about the rest,
#: because that is what a real pack looks like: a plant writes down the
#: numbers it disagrees with the product about.
A_PLANT_WITH_A_SLOW_ERP = '''
[pack]
format = 1
requires = ">=0.1.2"

[plant]
name = "slow-erp"
label = "A plant whose ERP lives across a VPN"
timezone = "Europe/Berlin"
profile = "laptop"

[erp]
max_attempts = 20
open_statuses = ["Not Started", "In Process", "Material Transferred"]
http_timeout = 120.0
'''


@pytest.fixture()
def pack(tmp_path):
    """The pack above, read and checked."""
    from fsmes.pack import check as checker
    from fsmes.pack import format as fmt

    (tmp_path / "plant.toml").write_text(A_PLANT_WITH_A_SLOW_ERP, encoding="utf-8")
    report = checker.check(tmp_path)
    assert report.ok, report.render()
    return fmt.read(tmp_path)


def a_failing_message(session) -> ErpMessage:
    """One confirmation in the outbox that the ERP has just refused to take."""
    message = ErpMessage(direction=MessageDirection.OUT, kind="operation_confirmation",
                         payload={"order": "WO-1"}, status=MessageStatus.PENDING)
    session.add(message)
    session.flush()
    return message


# ------------------------------------- 1. a plant that configures nothing


def test_a_plant_that_configures_nothing_asks_exactly_what_it_asked_before(session):
    """Rule one. Not a row in `plant_settings`, and every one of the ten
    answers the literal that was in the source before the audit named it."""
    assert session.scalars(select(PlantSetting)).all() == []

    assert erp.max_attempts(session) == 8
    assert erp.backoff_seconds(session, 1) == 5
    assert erp.backoff_seconds(session, 2) == 10
    assert erp.backoff_seconds(session, 20) == 3600
    assert erp.default_priority(session) == 50

    live = erp.policy(session)
    assert live.open_statuses == ("Not Started", "In Process")
    assert (live.float_rel_tol, live.float_abs_tol) == (1e-3, 0.01)
    assert (live.http_timeout, live.rest_timeout) == (30.0, 10.0)


def test_the_page_says_the_value_is_the_products_default_when_no_row_holds_it(admin):
    page = admin.get("/dashboard/config/supply_chain/sections").json()
    keys = {key["key"]: key for row in page["items"] for key in row["pack_keys"]}
    assert keys["[erp] max_attempts"]["value"] == "8"
    assert keys["[erp] max_attempts"]["is_default"] is True
    assert keys["[erp] max_attempts"]["set_by"] is None
    # A list reaches the screen as text, because that is what a setting is by
    # the time a plant reads one.
    assert keys["[erp] open_statuses"]["value"] == "Not Started,In Process"


# ------------------------------------- 2. what an upgrading plant keeps


def test_a_plant_whose_pack_already_set_one_of_these_keeps_it(session, monkeypatch):
    """The second layer. A plant that wrote `[erp] max_attempts = 20` in its
    pack has it in its environment, and nothing had to be migrated for it to
    go on being true."""
    from fsmes.config import get_settings

    monkeypatch.setattr(get_settings(), "erp_max_attempts", 20, raising=False)
    monkeypatch.setattr(get_settings(), "erp_open_statuses",
                        "Not Started,In Process,Material Transferred", raising=False)

    assert session.scalars(select(PlantSetting)).all() == []
    assert erp.max_attempts(session) == 20
    assert erp.policy(session).open_statuses == (
        "Not Started", "In Process", "Material Transferred")


# ------------------------------------- 3. editing one from the screen


def test_a_retry_policy_edited_on_the_screen_is_in_force_on_the_next_failure(
        admin, session):
    """The whole of what this change is, for the three numbers a service
    applies. An ERP with a four-hour maintenance window is why somebody moves
    them, and they moved without a restart."""
    message = a_failing_message(session)
    erp.mark_error(session, message, ConnectionError("the bench is down"))
    assert message.status is MessageStatus.PENDING

    assert admin.patch("/dashboard/config/supply_chain/settings/max_attempts",
                       json={"value": "1"}).status_code == 200
    assert admin.patch("/dashboard/config/supply_chain/settings/max_backoff_s",
                       json={"value": "21600"}).status_code == 200
    plant_settings.forget(session)

    assert erp.backoff_seconds(session, 20) == 21600
    erp.mark_error(session, message, ConnectionError("still down"))
    assert message.status is MessageStatus.DEAD, "one attempt is all this plant allows now"


def test_the_priority_an_erp_order_inherits_is_the_one_this_plant_chose(
        admin, session):
    """The ERPNext connector sends no priority and says why: inventing one at
    the edge would outrank this plant's own dispatch ordering with a number
    nobody set. So the border reports silence, and the plant answers it."""
    silent = ProductionRequest.from_payload(
        {"code": "ERP-1", "material": "FG-COLA", "quantity": 10})
    assert silent.priority is None

    first = erp.import_order(session, silent)
    assert first.priority == 50

    assert admin.patch("/dashboard/config/supply_chain/settings/default_order_priority",
                       json={"value": "5"}).status_code == 200
    plant_settings.forget(session)

    second = erp.import_order(session, ProductionRequest.from_payload(
        {"code": "ERP-2", "material": "FG-COLA", "quantity": 10}))
    assert second.priority == 5

    # And an order the ERP *did* give a priority keeps it, which is the half
    # of this that must not change.
    stated = erp.import_order(session, ProductionRequest.from_payload(
        {"code": "ERP-3", "material": "FG-COLA", "quantity": 10, "priority": 9}))
    assert stated.priority == 9


def test_the_sync_worker_hands_the_transport_this_plants_policy_each_cycle(
        admin, session):
    """The four a *connector* applies, and how they reach it.

    A transport is handed no unit of work on purpose - `sync.cycle` keeps every
    database transaction short and never lets one span an HTTP call - so the
    worker reads this plant's policy once a cycle, in a transaction closed
    before anything is sent, and hands it over. A number saved on the page is
    in force on the next cycle: seconds, and nothing restarted.
    """
    from fsmes.integrations.erp.erpnext_adapter import ErpNextAdapter, ErpNextClient

    adapter = ErpNextAdapter(ErpNextClient.__new__(ErpNextClient))
    adapter.client.timeout = 30.0
    assert adapter.open_statuses == ("Not Started", "In Process")

    for key, value in (("open_statuses", "Released,In Process"),
                       ("float_abs_tol", "0.0001"),
                       ("http_timeout", "120")):
        assert admin.patch(f"/dashboard/config/supply_chain/settings/{key}",
                           json={"value": value}).status_code == 200, key
    plant_settings.forget(session)

    base.configure(adapter, erp.policy(session))
    assert adapter.open_statuses == ("Released", "In Process")
    assert adapter.float_abs_tol == 0.0001
    assert adapter.client.timeout == 120.0


def test_a_connector_that_has_no_such_settings_is_left_exactly_as_it_was(session):
    """`configure` is read off the object, the same way `check` is. A connector
    published on its own and written against the older port has no such method,
    and the honest answer for it is to change nothing rather than to fail a
    sync cycle over a method nobody promised."""
    class OlderConnector:
        def fetch_orders(self):  # pragma: no cover - never called here
            return []

    older = OlderConnector()
    base.configure(older, erp.policy(session))  # says nothing, does nothing
    assert not hasattr(older, "open_statuses")


def test_editing_a_setting_is_audited_with_what_it_was_and_what_it_became(
        admin, session):
    """A number that decides how long this plant waits on somebody else's
    system does not move without a record of who moved it."""
    admin.patch("/dashboard/config/supply_chain/settings/http_timeout",
                json={"value": "60"})
    admin.patch("/dashboard/config/supply_chain/settings/http_timeout",
                json={"value": "90"})

    rows = session.scalars(select(AuditLog)
                           .where(AuditLog.entity_type == "plant_setting")
                           .order_by(AuditLog.id)).all()
    assert [r.entity_id for r in rows] == ["[erp] http_timeout"] * 2
    assert rows[0].before is None, "the first write took ownership; there was no row"
    assert rows[0].after["value"] == "60.0"
    assert rows[1].before["value"] == "60.0" and rows[1].after["value"] == "90.0"


# ------------------------------------- 4. what a pack may and may not do


def test_pack_apply_seeds_an_erp_setting_once_and_never_overwrites_it(session, pack):
    """The rule every kind `fsmes pack apply` seeds already keeps."""
    made = plant_settings.seed(session, pack)
    session.flush()
    assert made == {"made": 3, "present": 0}
    assert erp.max_attempts(session) == 20
    assert erp.policy(session).http_timeout == 120.0
    assert erp.policy(session).open_statuses == (
        "Not Started", "In Process", "Material Transferred")
    # And the seven it says nothing about stand at the product's own numbers.
    assert erp.default_priority(session) == 50

    plant_settings.write(session, domain="supply_chain", key="max_attempts",
                         written="3", actor="SUPPLY")
    session.flush()
    assert plant_settings.seed(session, pack) == {"made": 0, "present": 3}
    session.flush()
    plant_settings.forget(session)
    assert erp.max_attempts(session) == 3

    by = {row.key: row.set_by for row in session.scalars(select(PlantSetting))}
    assert by == {"max_attempts": "SUPPLY", "open_statuses": "pack-apply",
                  "http_timeout": "pack-apply"}


# ------------------------------------- 5. who may write, and what is refused


def test_a_caller_without_erp_define_cannot_write_one_of_these(client, session):
    """The gate is the section's own `erp.define`, read live from the role.
    An operator may read the page - a setting nobody can read is a setting
    nobody can choose against - and may not write."""
    refused = client.patch("/dashboard/config/supply_chain/settings/max_attempts",
                           json={"value": "3"})
    assert refused.status_code == 403
    assert "erp.define" in refused.json()["detail"]
    assert session.scalars(select(PlantSetting)).all() == []

    assert client.get("/dashboard/config/supply_chain/sections").status_code == 200


def test_a_caller_with_no_session_at_all_is_refused_before_anything_else(anon):
    assert anon.patch("/dashboard/config/supply_chain/settings/max_attempts",
                      json={"value": "3"}).status_code == 401


@pytest.mark.parametrize("key, value, expect", [
    ("max_attempts", "0", "offered at least once"),
    ("base_backoff_s", "0", "a wait of nothing is a loop"),
    ("float_rel_tol", "2", "between 0 and 1"),
    ("http_timeout", "0", "waiting for no time is not trying"),
    ("default_order_priority", "-1", "priority is a positive number"),
    ("max_attempts", "eight", "is not a whole number"),
    ("http_timeout", "soon", "is not a number"),
    ("open_statuses", "", "is empty"),
    ("open_statuses", "Not Started,Not Started", "twice"),
])
def test_a_value_the_pack_checker_refuses_is_refused_from_the_screen_too(
        admin, session, key, value, expect):
    """One wording for one rule. A value `fsmes pack check` refuses in a file
    is refused in the same sentence when somebody types it into the page."""
    answer = admin.patch(f"/dashboard/config/supply_chain/settings/{key}",
                         json={"value": value})
    assert answer.status_code == 422, answer.text
    assert expect in answer.json()["detail"]
    assert f"[erp] {key}" in answer.json()["detail"]
    assert session.scalars(select(PlantSetting)).all() == []


def test_the_backoff_pair_is_refused_from_either_half(admin):
    """One judgment written as two numbers. A first wait longer than the
    ceiling on a wait refuses nothing on its own - it quietly makes the
    ceiling the only wait there is - so it is said under both names, and
    typing either half of it into the page is refused."""
    assert admin.patch("/dashboard/config/supply_chain/settings/base_backoff_s",
                       json={"value": "7200"}).status_code == 422
    assert admin.patch("/dashboard/config/supply_chain/settings/max_backoff_s",
                       json={"value": "2"}).status_code == 422

    # Raise the ceiling first and the same first wait is fine, which is what
    # the page's Save retries for.
    assert admin.patch("/dashboard/config/supply_chain/settings/max_backoff_s",
                       json={"value": "21600"}).status_code == 200
    assert admin.patch("/dashboard/config/supply_chain/settings/base_backoff_s",
                       json={"value": "7200"}).status_code == 200


def test_a_status_name_with_a_comma_in_it_is_refused_rather_than_split(admin):
    """The one thing this list cannot carry, said out loud rather than
    discovered by a plant whose ERP has a status called `Hold, pending QA`."""
    answer = admin.patch("/dashboard/config/supply_chain/settings/open_statuses",
                         json={"value": "Not Started"})
    assert answer.status_code == 200
    # A comma *is* the separator, so this arrives as two statuses and neither
    # of them has a comma in it - which is why the refusal has to live where a
    # list is still a list, in `fsmes pack check` on a pack file.
    from fsmes.pack import check as checker

    problems = checker.erp_numbers({"open_statuses": ["Hold, pending QA"]})
    assert problems and "cannot survive the trip" in problems[0].says


# ------------------------------------- 6. the one that is not live


def test_the_confirmation_tolerance_is_read_only_and_the_page_says_why(admin):
    """`fsmes erp validate` reads files and no database - that is its contract,
    and a plant's ERP team runs it on a laptop that never had an MES database
    on it. There is no session to read a live row through, so a box saying *in
    force the moment you save it* would have been false. It is the first
    section in this product to take the honest other answer, which the page
    has had words for since the day the live ones arrived."""
    page = admin.get("/dashboard/config/supply_chain/sections").json()
    row = next(r for r in page["items"] if r["key"] == "erp_confirmation_tolerance")
    assert row["edit_here"] is False
    assert row["define"] is None and row["approve"] is None
    assert row["pack_keys"][0]["key"] == "[erp] confirmation_seconds_tolerance"
    assert row["pack_keys"][0]["value"] == "1.0"

    refused = admin.patch(
        "/dashboard/config/supply_chain/settings/confirmation_seconds_tolerance",
        json={"value": "5"})
    assert refused.status_code == 404
    assert "max_attempts" in refused.json()["detail"], (
        "the refusal names what this workspace can write")


def test_the_validator_reads_the_setting_the_pack_compiled(monkeypatch):
    """Two layers rather than three, and both of them are real."""
    from fsmes.config import get_settings
    from fsmes.integrations.erp import validate

    assert validate.seconds_tolerance() == 1.0
    monkeypatch.setattr(get_settings(), "erp_confirmation_seconds_tolerance", 90.0,
                        raising=False)
    assert validate.seconds_tolerance() == 90.0


# ------------------------------------- the workspace, counted


def test_supply_chain_has_six_sections_and_five_of_them_are_live(admin):
    """What this change is, counted. Six sections, ten keys, one capability -
    and the sixth section is the one that cannot move while a plant runs."""
    page = admin.get("/dashboard/config/supply_chain/sections").json()
    assert page["title"] == "Supply chain"
    assert page["total"] == 6

    live = [row for row in page["items"] if row["edit_here"]]
    assert len(live) == 5
    assert sum(len(row["pack_keys"]) for row in page["items"]) == 10
    assert all(row["define"] == "erp.define" for row in live)
    assert all(row["approve"] is None for row in live)


def test_the_two_retry_policies_this_product_has_stay_two(session):
    """Audit rows C8 and S1 name the same three constants with the same three
    values in two modules, and they are deliberately not merged. The namespace
    broker on this site and the ERP across a VPN are two systems with two
    outages; a plant that widened one because its ERP has a weekly maintenance
    window did not mean to widen the other."""
    from fsmes.services import uns

    assert (uns.MAX_ATTEMPTS, uns.BASE_BACKOFF_SECONDS, uns.MAX_BACKOFF_SECONDS) == (
        erp.MAX_ATTEMPTS, erp.BASE_BACKOFF_SECONDS, erp.MAX_BACKOFF_SECONDS)

    plant_settings.write(session, domain="supply_chain", key="max_attempts",
                         written="30", actor="SUPPLY")
    session.flush()
    plant_settings.forget(session)
    assert erp.max_attempts(session) == 30
    assert uns.MAX_ATTEMPTS == 8, "the namespace kept its own answer"
