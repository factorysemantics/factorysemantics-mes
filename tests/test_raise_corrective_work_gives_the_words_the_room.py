"""Raise corrective work gives "What needs doing" the room, not the machine list.

On Maintenance > Work the machine select took nearly the whole row and the
"What needs doing" box was a sliver reading "What nee" (the steward's look at
the close form, 2026-11-28; the manager's word in Scott's name). The two text
boxes are the `wide` kind now, as the equipment form's code and name are.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def test_what_needs_doing_and_why_are_the_wide_kind():
    page = (WEB / "maintenance.html").read_text(encoding="utf-8")
    assert '<input id="cm-summary" class="wide"' in page
    assert '<input id="cm-reason" class="wide"' in page


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


@pytest.mark.slow
@pytest.mark.browser
@pytest.mark.parametrize("width", [1400, 1024])
def test_what_needs_doing_is_at_least_as_wide_as_the_machine_list(browser, live_server, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    try:
        live_server.sign_in(context)
        page = context.new_page()
        page.goto(f"{live_server.base}/dashboard/maintenance", wait_until="load", timeout=30000)
        page.click('button.tab[data-tab="work"]')
        page.wait_for_selector("#cm-summary", state="visible", timeout=10000)
        widths = page.evaluate(
            "() => ['#cm-machine', '#cm-summary', '#cm-reason']"
            ".map((s) => document.querySelector(s).getBoundingClientRect().width)")
        machine, summary, reason = widths
        assert summary >= machine, f"'What needs doing' is {summary:.0f}px beside a {machine:.0f}px machine list"
        assert reason >= machine * 0.5, f"'Why' is {reason:.0f}px"
        for box in ("#cm-summary", "#cm-reason"):
            page.eval_on_selector(box, "(el) => { el.value = el.placeholder; }")
            scroll, client = page.eval_on_selector(box, "(el) => [el.scrollWidth, el.clientWidth]")
            assert scroll <= client, f"{box} cuts its own label off at {width}px"
    finally:
        context.close()
