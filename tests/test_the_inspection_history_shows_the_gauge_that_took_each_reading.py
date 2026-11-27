"""Quality's Inspection history says which gauge took each reading.

Scott, 2026-11-22: "Quality inspections don't include gauges! How can we
track them?" The check has carried its gauge since #252, and the list's API
and its spreadsheet both name it - but the table on the Quality screen did
not, so the night walk of 2026-11-27 found "the Quality history list did not
obviously show a gauge column". Now the gauge sits beside the inspector: the
instrument and the person, the two halves of "is it the gauge or the people?".
A reading nobody named a gauge for says "not recorded", the export's word,
rather than an empty cell that could mean the column is broken.
"""

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


@pytest.fixture(scope="module")
def plant(serve_plant):
    from fsmes.services import gauges, quality

    def seed(session):
        gauges.register(session, code="MIC-77", name="Bench micrometer", resolution=0.01)
        session.flush()
        quality.record_check(session, material_code="FG-COLA", characteristic="brix",
                             value=10.4, gauge_code="MIC-77", actor="QI-GAUGED")
        quality.record_check(session, material_code="FG-COLA", characteristic="brix",
                             value=10.5, actor="QI-BY-HAND")

    with serve_plant(seed, name="history-gauge") as served:
        yield served


def test_each_row_names_its_gauge_or_says_none_was_recorded(plant):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            plant.sign_in(context)
            page = context.new_page()
            page.goto(f"{plant.base}/dashboard/quality")
            page.wait_for_function(
                """() => [...document.querySelectorAll('#checks tbody tr')]
                         .some(r => r.textContent.includes('QI-BY-HAND'))""",
                timeout=20000)
            header = page.eval_on_selector_all("#checks thead th", "ths => ths.map(t => t.textContent)")
            assert "Gauge" in header, header
            column = header.index("Gauge")
            rows = page.eval_on_selector_all(
                "#checks tbody tr", "trs => trs.map(r => [...r.cells].map(c => c.textContent))")
            by_inspector = {cells[-1]: cells for cells in rows if len(cells) == len(header)}
            assert by_inspector["QI-GAUGED"][column] == "MIC-77"
            assert by_inspector["QI-BY-HAND"][column] == "not recorded"
        finally:
            browser.close()
