"""An Export button keeps saying how many rows it holds while its screen refreshes.

The button's name carries the count ("Export 5 checks as xlsx") from the
moment the list answers. Quality refreshes itself every five seconds, and each
refresh asked the button again with the same filters and no count yet - so
the name fell back to "Export checks as xlsx" until the reply landed. A screen
reader reading it then heard no number, and a test reading it then failed on
CI (#310, 2026-11-26). The list here is fetched with a delay so every refresh
has a moment in which the count could go; every name the button carries over
two refreshes is watched.
"""

import time

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

DELAY_SECONDS = 0.8
WATCH_SECONDS = 11  # two of Quality's five-second refreshes


def test_the_checks_export_keeps_its_count_through_two_refreshes(live_server):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context()
            live_server.sign_in(context)
            page = context.new_page()
            page.route("**/quality/checks?*",
                       lambda route: (time.sleep(DELAY_SECONDS), route.continue_()))
            page.goto(f"{live_server.base}/dashboard/quality")
            page.wait_for_function(
                "() => /\\d/.test(document.querySelector('#h-export')?.getAttribute('aria-label') || '')",
                timeout=20000)
            page.evaluate("""() => {
                const link = document.querySelector('#h-export');
                window.__names = [];
                new MutationObserver(() => window.__names.push(link.getAttribute('aria-label')))
                    .observe(link, { attributes: true, attributeFilter: ['aria-label'] });
            }""")
            page.wait_for_timeout(WATCH_SECONDS * 1000)
            names = page.evaluate("() => window.__names")
            assert names, "the screen did not refresh while it was watched"
            assert all(any(ch.isdigit() for ch in n) for n in names), names
        finally:
            browser.close()
