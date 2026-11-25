"""Admin's routing form fills even when the people-and-roles calls are refused.

The pretend engineer opened Setup > Admin > Routings on the lab plant every
night from 2026-11-22 and found the Material and machine pickers empty under
"Could not load materials and machines" (cards routing-to-cost-centers and
new-user-groups-shifts). Materials and machines had loaded fine: the walk
refuses every `/admin/...` request, and the page fetched the capability list
in one Promise.all with materials and machines - so one refused call that
only the role form needs blanked the routing form, and the toast blamed the
wrong lists. The same happened to the routing list, fetched in one
Promise.all with people and roles.

A person whose access to people and roles is refused, or whose request for
them fails, still has routings to read and to define: they are master data
(`/masterdata/...`). This refuses every `/admin/...` call in the browser,
the way the walk does, and holds the page to that. Served by conftest's
`serve_plant`; run it with `pytest -m browser` on this file.
"""

from urllib.parse import urlparse

import pytest

pytestmark = [pytest.mark.browser]


def _refused(url: str) -> bool:
    return urlparse(url).path.startswith("/admin/")


def test_the_routing_form_and_list_fill_with_people_and_roles_refused(serve_plant):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    with serve_plant(name="admin-lists") as plant, sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context()
            plant.sign_in(context)
            context.route(_refused, lambda route: route.abort())
            page = context.new_page()
            page.goto(f"{plant.base}/dashboard/admin")
            # The refusal is said once, in its own words, not as a failure
            # to load materials and machines.
            page.locator("#toast", has_text="people and roles").wait_for(timeout=10000)

            # The demo plant's routing RT-COLA is listed, and its material
            # is offered in the new routing's picker.
            page.locator("#routings tbody", has_text="RT-COLA").wait_for(timeout=10000)
            materials = page.locator("#routing-material option").all_inner_texts()
            assert any(m.startswith("FG-COLA") for m in materials), materials
            machines = page.locator("#ops select").first.locator("option").all_inner_texts()
            assert len(machines) > 1, machines
        finally:
            browser.close()
