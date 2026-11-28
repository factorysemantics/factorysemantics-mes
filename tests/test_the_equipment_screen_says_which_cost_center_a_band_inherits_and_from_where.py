"""The equipment screen says which cost center a band pays into, and from where.

Engineering > Master data > Equipment showed a dash for every heater band that
had no cost center of its own, although which cost center pays is the whole
point of the screen (the steward's look at #335's stills, 2026-11-27). Now a
row with its own shows it as before; a row that inherits shows the one it
resolves to, greyed, with the node it comes from ("from Moulding line FORK1");
a row nobody up the tree has given one keeps the dash. The resolving is the
service's (`masterdata.cost_center_holder`) and comes through the read as
`bills_to` and `bills_from`; the page only draws it.

The form's place boxes are labelled with their unit, the cost center box says
that empty means inherit, and the form's title says "Change <name>" while it
changes a picked row. Setup > Configuration says in one line where the screen is.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from fsmes.domain import EquipmentLevel
from fsmes.services import auth, masterdata

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def _seed(session) -> None:
    """A line with a cost center, its mould with none, two bands under the
    mould - band 1 inheriting, band 2 with its own - and a stand-alone
    node nobody has given one."""
    add = masterdata.create_equipment
    add(session, code="BCC-LINE", name="Moulding line BCC", level=EquipmentLevel.WORK_CENTER,
        cost_center="CC-BCC-LINE")
    add(session, code="BCC-MOLD", name="BCC Mold", level=EquipmentLevel.WORK_UNIT, parent_code="BCC-LINE")
    add(session, code="BCC-MOLD_HeaterZone1", name="Heater band 1",
        level=EquipmentLevel.EQUIPMENT_MODULE, parent_code="BCC-MOLD")
    add(session, code="BCC-MOLD_HeaterZone2", name="Heater band 2",
        level=EquipmentLevel.EQUIPMENT_MODULE, parent_code="BCC-MOLD", cost_center="CC-BAND02")
    add(session, code="BCC-LONE", name="Spare press", level=EquipmentLevel.WORK_UNIT)
    session.flush()


def _rows(client) -> dict[str, dict]:
    r = client.get("/masterdata/equipment", params={"q": "BCC-"})
    assert r.status_code == 200, r.text
    return {row["code"]: row for row in r.json()}


# ------------------------------------------------------------------ the read

def test_a_band_with_no_cost_center_of_its_own_reads_the_line_s_and_the_line_s_name(session, client):
    _seed(session)
    band = _rows(client)["BCC-MOLD_HeaterZone1"]
    assert band["cost_center"] is None
    assert (band["bills_to"], band["bills_from"]) == ("CC-BCC-LINE", "Moulding line BCC")


def test_a_band_with_its_own_cost_center_reads_it_and_says_it_comes_from_nowhere_else(session, client):
    _seed(session)
    band = _rows(client)["BCC-MOLD_HeaterZone2"]
    assert (band["cost_center"], band["bills_to"], band["bills_from"]) == ("CC-BAND02", "CC-BAND02", None)


def test_a_node_nobody_up_the_tree_has_given_one_reads_none_and_from_none(session, client):
    _seed(session)
    lone = _rows(client)["BCC-LONE"]
    assert (lone["cost_center"], lone["bills_to"], lone["bills_from"]) == (None, None, None)


def test_the_holder_is_the_node_the_cost_center_resolves_to(session):
    _seed(session)
    band = masterdata.get_equipment(session, "BCC-MOLD_HeaterZone1")
    holder = masterdata.cost_center_holder(session, band)
    assert holder.code == "BCC-LINE"
    assert masterdata.cost_center(session, band) == holder.cost_center
    assert masterdata.cost_center_holder(session, masterdata.get_equipment(session, "BCC-LONE")) is None


# ------------------------------------------------------------------ the page

def test_each_place_box_and_the_cost_center_box_has_a_label_with_its_unit():
    page = (WEB / "masterdata.html").read_text(encoding="utf-8")
    labels = dict(re.findall(r'<label for="(eq-[a-z]+)">([^<]+)</label>', page))
    assert labels == {"eq-cc": "Cost center", "eq-x": "Along (m)", "eq-y": "Across (m)", "eq-z": "Up (m)"}
    assert "Leave empty to inherit" in page


def test_setup_says_in_one_line_where_equipment_and_its_cost_centers_are_kept():
    """On Setup > Configuration, which an engineer opens, not on Setup > Admin,
    which needs users.manage (Scott, via the manager, 2026-11-28: "where an
    engineer can see it, since the engineer is who assigns")."""
    config = (WEB / "config.html").read_text(encoding="utf-8")
    link = re.search(r'cost centers\s+are kept on <a href="/dashboard/masterdata#equipment">([^<]+)</a>', config)
    assert link, "Setup > Configuration does not say where the equipment screen is"
    assert link.group(1) == "Engineering \u203a Master data \u203a Equipment"
    assert "/dashboard/masterdata#equipment" not in (WEB / "admin.html").read_text(encoding="utf-8")
    script = (WEB / "config.js").read_text(encoding="utf-8")
    assert '$("#equipment-pointer").classList.toggle("hidden", DOMAIN !== "administration")' in script


# --------------------------------------------------------------- in a browser

DELAY_SECONDS = 0.8


def _seed_with_a_supervisor(session) -> None:
    """The bands, and an account without users.manage, which is how an
    engineer signs in: Setup > Admin is not on his bar."""
    _seed(session)
    auth.create_user(session, code="BCC-SUPER", name="Bcc Super", password="bcc-super", role="supervisor")
    session.flush()


@pytest.fixture(scope="module")
def plant(serve_plant):
    with serve_plant(_seed_with_a_supervisor, name="band-cost-center") as served:
        yield served


@pytest.fixture(scope="module")
def browser():
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            chromium = pw.chromium.launch()
            yield chromium
            chromium.close()
    except PlaywrightError as err:
        pytest.skip(f"chromium is not installed for playwright: {err}")


def _cell(page, code: str):
    """The text and class of a row's cost center cell, read in the page."""
    return page.evaluate(
        """(code) => {
          for (const tr of document.querySelectorAll('#eq-table tbody tr')) {
            if (tr.cells[0].textContent.trim() === code)
              return {text: tr.cells[5].textContent.trim(), cls: tr.cells[5].className};
          }
          return null;
        }""", code)


@pytest.mark.slow
@pytest.mark.browser
def test_the_table_shows_own_inherited_and_none_and_the_title_follows_the_picked_row(browser, plant):
    context = browser.new_context(viewport={"width": 1400, "height": 1000})
    try:
        plant.sign_in(context)
        page = context.new_page()
        # The list held back, so the rows are proved drawn from the answer, not on loopback's speed.
        page.route("**/masterdata/equipment*", lambda route: (time.sleep(DELAY_SECONDS), route.continue_()))
        page.goto(f"{plant.base}/dashboard/masterdata", wait_until="load", timeout=30000)
        page.fill("#eq-q", "BCC-")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#eq-table tbody tr')]"
            ".some((tr) => tr.cells[0] && tr.cells[0].textContent.trim() === 'BCC-MOLD_HeaterZone1')",
            timeout=20000)

        inherited = _cell(page, "BCC-MOLD_HeaterZone1")
        assert inherited["text"] == "CC-BCC-LINE from Moulding line BCC"
        assert "muted" in inherited["cls"]
        own = _cell(page, "BCC-MOLD_HeaterZone2")
        assert own["text"] == "CC-BAND02"
        assert "muted" not in own["cls"]
        assert _cell(page, "BCC-LONE")["text"] == "—"

        assert page.text_content("#eq-form-title") == "Add equipment"
        page.click("#eq-table tbody tr:has(td.code:text-is('BCC-MOLD_HeaterZone2'))")
        page.wait_for_function(
            "() => document.querySelector('#eq-form-title').textContent === 'Change Heater band 2'",
            timeout=5000)
        assert page.input_value("#eq-cc") == "CC-BAND02"
        page.click("#eq-cancel")
        page.wait_for_function(
            "() => document.querySelector('#eq-form-title').textContent === 'Add equipment'", timeout=5000)
    finally:
        context.close()


@pytest.mark.slow
@pytest.mark.browser
def test_someone_without_users_manage_sees_the_line_on_setup_configuration_only(browser, plant):
    context = browser.new_context(viewport={"width": 1400, "height": 1000})
    try:
        plant.sign_in(context, code="BCC-SUPER", password="bcc-super")
        page = context.new_page()
        page.goto(f"{plant.base}/dashboard/config/administration", wait_until="load", timeout=30000)
        page.wait_for_function(
            "() => document.querySelector('#domain-title').textContent.includes('configuration')",
            timeout=20000)
        line = page.locator("#equipment-pointer")
        assert line.is_visible()
        assert line.locator("a").get_attribute("href") == "/dashboard/masterdata#equipment"

        page.goto(f"{plant.base}/dashboard/config/engineering", wait_until="load", timeout=30000)
        page.wait_for_function(
            "() => document.querySelector('#domain-title').textContent.includes('configuration')",
            timeout=20000)
        assert not page.locator("#equipment-pointer").is_visible()
    finally:
        context.close()
