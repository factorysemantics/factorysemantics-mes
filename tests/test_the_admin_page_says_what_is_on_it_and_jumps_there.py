"""The Admin page names its three parts at the top, and each name jumps there.

The pretend engineer's walk on the night of 2026-11-26 (card
routing-to-cost-centers): "The routing form sits at the bottom of a long
Admin page, below People and Roles. At first I typed the code and name into
the "Add someone" form by mistake." Nothing at the top said routings were on
this page at all.

So a row "On this page: People · Roles · Routings" heads the page, and
"Routings" brings the routings panel, with its "Define a routing" form, into
view. Asked the way a person's tools ask, by role and name, on the shared
demo plant.
"""

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def test_routings_is_one_click_from_the_top_of_admin(live_server):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            live_server.sign_in(context)
            page = context.new_page()
            page.goto(f"{live_server.base}/dashboard/admin")
            page.locator("#form-routing #ops select option").first.wait_for(
                state="attached", timeout=10000)

            jump = page.get_by_role("navigation", name="On this page")
            assert [t.strip() for t in jump.get_by_role("link").all_inner_texts()] == [
                "People", "Roles", "Routings"]

            routings = page.get_by_role("region", name="Routings")
            form = page.get_by_role("form", name="Define a routing")
            assert form.count() == 1

            jump.get_by_role("link", name="Routings", exact=True).click()
            page.wait_for_function("window.scrollY > 0")
            # Routings is the last panel, so the page may stop scrolling short
            # of putting it at the very top: the heading and the form's first
            # box are both on screen, which is what the walker needed.
            heading = routings.get_by_role("heading", level=2).bounding_box()
            code = form.get_by_label("Routing code", exact=True).bounding_box()
            assert heading["y"] >= 0, heading
            assert code["y"] + code["height"] <= 720, code

            # The address says where you are, so it can be sent to someone.
            assert page.evaluate("location.hash") == "#routings-panel"
        finally:
            browser.close()
