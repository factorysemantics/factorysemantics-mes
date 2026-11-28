"""The Gauges page says a gauge's status in words, every underscore gone.

Scott, 2026-11-22: "Quality inspections don't include gauges! How can we
track them?" Class 4.4's film (#323) recorded a failed calibration and the
page answered "now out of_service": the page turned only the first
underscore into a space. The register's pill, the toast and the sentence
under the form now read "out of service" and "fail as found".
"""

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


@pytest.fixture(scope="module")
def plant(serve_plant):
    from fsmes.services import gauges

    def seed(session):
        gauges.register(session, code="CAL-98", name="Pulled caliper", resolution=0.01)
        gauges.register(session, code="CAL-99", name="Bench caliper", resolution=0.01)
        session.flush()
        gauges.calibrate(session, "CAL-98", result="fail_as_found", performed_by="LAB")

    with serve_plant(seed, name="gauge-words") as served:
        yield served


def test_out_of_service_and_fail_as_found_read_as_words(plant):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            plant.sign_in(context)
            page = context.new_page()
            page.goto(f"{plant.base}/dashboard/gauges")
            row = page.locator("#gauges-table tbody tr", has_text="CAL-98")
            row.wait_for(timeout=20000)
            assert row.locator(".pill").text_content() == "out of service"

            page.select_option("#cal-gauge", "CAL-99")
            page.select_option("#cal-result", "fail_as_found")
            page.fill("#cal-by", "LAB")
            page.click("#cal-submit")
            text = page.locator("#cal-result-text")
            text.wait_for(state="visible", timeout=20000)
            said = text.inner_text()
            assert "CAL-99 fail as found: now out of service" in said, said
            assert "_" not in said, said
        finally:
            browser.close()
