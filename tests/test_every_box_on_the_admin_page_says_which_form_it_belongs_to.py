"""Every box on the Admin page says which form it belongs to.

The night walk of 2026-11-27 (card routing-to-cost-centers) filled in
"Define a routing" and once typed the routing's name into "Add someone"
instead. #309 had named every box on the routing form, but the two forms
above it still had placeholders only: "Full name" and "Display name" with no
label and no form name, so a reader of the page - a walker, a screen reader -
could not tell a person's name box from a routing's.

Now each of the three forms is named after its heading, and each box in it
carries a name saying whose box it is. Asked here the way a person's tools
ask, by role and by label, on the shared demo plant.
"""

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

FORMS = {
    "Add someone": ("New person's code", "New person's full name",
                    "New person's initial password", "New person's role"),
    "Define a role": ("Role code", "Role display name", "What this role is for"),
    "Define a routing": ("Routing code", "Routing name", "Material"),
}


def test_each_admin_form_is_named_and_each_box_says_whose_it_is(live_server):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context()
            live_server.sign_in(context)
            page = context.new_page()
            page.goto(f"{live_server.base}/dashboard/admin")
            page.locator("#form-routing #ops select option").first.wait_for(
                state="attached", timeout=10000)

            for form_name, boxes in FORMS.items():
                form = page.get_by_role("form", name=form_name, exact=True)
                assert form.count() == 1, form_name
                for box in boxes:
                    assert form.get_by_label(box, exact=True).count() == 1, (form_name, box)

            # A box named only "Name" or "Code" could belong to any of them.
            for vague in ("Name", "Full name", "Display name", "Code"):
                assert page.get_by_label(vague, exact=True).count() == 0, vague

            # Typing into the routing's name lands in the routing form.
            page.get_by_label("Routing name", exact=True).fill("Pallet wrap")
            assert page.locator('#form-routing [name="name"]').input_value() == "Pallet wrap"
            assert page.locator('#form-user [name="name"]').input_value() == ""
        finally:
            browser.close()
