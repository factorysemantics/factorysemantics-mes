"""The Part used box on the Maintenance page, in a browser.

A plant on the file connection, whose parts file is the ERP's list: typing
"heat" in Part used offers "SP-HEATER-BAND — Heater band, 3 in stores"; a
code the list does not hold is warned about and still saves; with no parts
file the form says the ERP's list is not answering and a typed code
saves. The read of `/maintenance/parts` is held back 1.5 s in the browser,
so the box is proved to wait for the list rather than for loopback speed.
Served by conftest's `serve_plant`; run it with `pytest -m browser`.
"""

from __future__ import annotations

import json
import time

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

PARTS = [{"code": "SP-HEATER-BAND", "name": "Heater band", "unit": "Nos", "in_stores": 3,
          "valuation_rate": 140},
         {"code": "SP-SEAL-KIT", "name": "Pump seal kit", "unit": "Nos", "in_stores": 5}]


def _two_jobs_in_hand(session):
    from fsmes.services import maintenance

    for summary in ("Heater out", "Seal weeping", "Belt slipping"):
        order = maintenance.raise_corrective(session, equipment_code="MIX01", summary=summary)
        maintenance.start(session, order.code, actor="ADMIN")


def _held_back(route):
    time.sleep(1.5)
    route.continue_()


def _open_complete(page, base, summary):
    page.goto(f"{base}/dashboard/maintenance")
    page.locator('[data-tab="work"]').click()
    job = page.locator("#work-list li", has_text=summary)
    job.get_by_role("button", name="Complete…").click()


def test_the_box_offers_the_erps_parts_warns_on_an_unknown_code_and_says_when_the_erp_is_down(
        serve_plant, tmp_path):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    parts_file = tmp_path / "parts.json"
    env = {"MES_ERP_MODE": "file", "MES_ERP_PARTS_FILE": str(parts_file),
           "MES_ERP_INBOX": str(tmp_path / "in"), "MES_ERP_OUTBOX": str(tmp_path / "out"),
           "MES_ERP_ARCHIVE": str(tmp_path / "arc")}
    with serve_plant(_two_jobs_in_hand, name="parts-box", env=env) as plant, sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context()
            plant.sign_in(context)
            context.route("**/maintenance/parts", _held_back)
            page = context.new_page()

            # No parts file yet - the ERP's list is not answering: said
            # plainly, and a typed code saves. A failed read is not kept.
            _open_complete(page, plant.base, "Belt slipping")
            page.wait_for_function(
                "() => document.querySelector('#complete-box .part-note')?.textContent"
                "?.startsWith(\"The ERP's parts list is not answering\")", timeout=15000)
            page.fill("#part-code", "SP-BELT-A42")
            page.fill("#part-qty", "1")
            page.click("#complete-confirm")
            page.locator("#toast", has_text="complete").wait_for(timeout=10000)

            # The ERP's list there: offered as you type, no price shown.
            parts_file.write_text(json.dumps(PARTS), encoding="utf-8")
            _open_complete(page, plant.base, "Heater out")
            page.wait_for_function(
                "() => (document.querySelector('#complete-box .part-note') || {}).textContent"
                "?.startsWith(\"From the ERP's parts list\")", timeout=15000)
            assert "2 parts" in page.locator("#complete-box .part-note").inner_text()
            page.fill("#findings", "band replaced")
            page.locator("#part-code").press_sequentially("heat")
            option = page.locator("#complete-box .part-options li", has_text="SP-HEATER-BAND")
            option.wait_for(timeout=5000)
            assert option.inner_text() == "SP-HEATER-BAND — Heater band, 3 in stores"
            assert "140" not in page.locator("#complete-box").inner_text()
            # Half a code that the list matches is not warned about.
            assert page.locator("#complete-box .part-note").inner_text().startswith("From the ERP's parts list")
            option.dispatch_event("mousedown")
            page.wait_for_function("() => document.querySelector('#part-code').value === 'SP-HEATER-BAND'")
            page.fill("#part-qty", "1")
            page.click("#complete-confirm")
            page.locator("#toast", has_text="complete").wait_for(timeout=10000)

            # A code the ERP does not list: warned before the save, saved anyway.
            _open_complete(page, plant.base, "Seal weeping")
            page.wait_for_function(
                "() => document.querySelector('#complete-box .part-note')?.textContent"
                "?.startsWith(\"From the ERP's parts list\")", timeout=15000)
            page.locator("#part-code").press_sequentially("SP-NOT-IN-STORES")
            page.wait_for_function(
                "() => document.querySelector('#complete-box .part-note')?.textContent"
                "?.includes(\"is not in the ERP's parts list\")", timeout=5000)
            page.fill("#part-qty", "1")
            page.click("#complete-confirm")
            page.locator("#toast", has_text="complete").wait_for(timeout=10000)

            # History reads the ERP's name beside the code it knew.
            page.locator('[data-tab="history"]').click()
            page.locator("#history-table tbody", has_text="1 \u00d7 SP-HEATER-BAND — Heater band").wait_for(
                timeout=10000)
            history = page.locator("#history-table tbody").inner_text()
            assert "1 \u00d7 SP-NOT-IN-STORES" in history and "1 \u00d7 SP-BELT-A42" in history
        finally:
            browser.close()
