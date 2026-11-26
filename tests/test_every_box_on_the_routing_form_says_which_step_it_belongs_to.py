"""Every box on the routing form says what it is and which step it belongs to.

The pretend engineer stuck at step 17 of 20 on the night of 2026-11-26 (card
routing-to-cost-centers): on Setup > Admin > Routings, "Define a routing",
the second step's Operation box and machine picker had no labels, so they
could not be told from the first step's. Nor could the code and name boxes,
whose only words were placeholders - and the code box's placeholder was cut
off. A screen reader had the same trouble a walker did: four rows of
"edit text, edit text, combo box, button x".

So each box carries its name: "Routing code", "Routing name", and per step
"Step 20 number", "Step 20 operation", "Step 20 machine" and a "Remove step
20" button, renamed when the step number is changed. Asked here the way a
person's tools ask, by label and by role, on the shared demo plant.
"""

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def test_each_box_on_the_routing_form_is_named_for_its_step(live_server):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context()
            live_server.sign_in(context)
            page = context.new_page()
            page.goto(f"{live_server.base}/dashboard/admin")
            form = page.locator("#form-routing")
            form.locator("#ops select option").first.wait_for(state="attached", timeout=10000)

            for name in ("Routing code", "Routing name", "Material"):
                assert form.get_by_label(name, exact=True).count() == 1, name

            form.get_by_role("button", name="Add operation").click()
            for step in (10, 20):
                for part in ("number", "operation", "machine"):
                    label = f"Step {step} {part}"
                    assert form.get_by_label(label, exact=True).count() == 1, label
                assert form.get_by_role("button", name=f"Remove step {step}", exact=True).count() == 1

            # The names follow the step number, so a renumbered step is not
            # left answering to its old one.
            form.get_by_label("Step 20 number", exact=True).fill("30")
            assert form.get_by_label("Step 30 operation", exact=True).count() == 1
            assert form.get_by_label("Step 20 operation", exact=True).count() == 0

            # Typing into step 30's box lands in step 30's row, not step 10's.
            form.get_by_label("Step 30 operation", exact=True).fill("Wrap")
            rows = form.locator("#ops .op-row")
            assert rows.nth(1).locator('[name="op-name"]').input_value() == "Wrap"
            assert rows.nth(0).locator('[name="op-name"]').input_value() == ""
        finally:
            browser.close()
