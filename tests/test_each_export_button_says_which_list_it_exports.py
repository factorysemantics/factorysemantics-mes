"""Each Export button says, in its own words, which list it downloads.

The pretend supervisor on the night of 2026-11-26 (card export-quality-xlsx):
"The page has two Export xlsx buttons and the plain click always hit the
first one, which is the non-conformances one. The two date filters are also
both labelled From/To." Quality holds two lists, each with its own range and
export; Analysis holds two as well (OEE by station, the stops). The hidden
names already differed; the words on screen did not.

So the button reads "Export checks (xlsx)" or "Export non-conformances
(xlsx)", made in one place (FS.exportXlsx) for every list, and Quality's date
boxes read "Raised from" / "Taken from" where a person can see them. Asked
here by the visible text, the way a person - or a walk - picks.
"""

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def test_the_two_exports_and_the_two_ranges_on_quality_read_differently(live_server):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context()
            live_server.sign_in(context)
            page = context.new_page()
            page.goto(f"{live_server.base}/dashboard/quality")
            page.locator("#h-export[aria-label]").wait_for(state="attached", timeout=10000)

            checks = page.get_by_text("Export checks (xlsx)", exact=True)
            ncs = page.get_by_text("Export non-conformances (xlsx)", exact=True)
            assert checks.count() == 1 and ncs.count() == 1
            assert "/quality/checks/export.xlsx" in checks.get_attribute("href")
            assert "/quality/nonconformances/export.xlsx" in ncs.get_attribute("href")
            assert page.get_by_text("Export xlsx", exact=True).count() == 0

            for words in ("Raised from", "Raised to", "Taken from", "Taken to"):
                assert page.locator("label.range-field", has_text=words).count() == 1, words
                assert page.get_by_label(words, exact=True).count() == 1, words
        finally:
            browser.close()


def test_the_two_exports_on_analysis_read_differently(live_server):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context()
            live_server.sign_in(context)
            page = context.new_page()
            page.goto(f"{live_server.base}/dashboard/analysis")
            page.locator("a.export-xlsx").nth(1).wait_for(state="attached", timeout=10000)
            words = sorted(t.strip() for t in page.locator("a.export-xlsx").all_text_contents())
            assert words == ["Export stations (xlsx)", "Export stops (xlsx)"], words
        finally:
            browser.close()
