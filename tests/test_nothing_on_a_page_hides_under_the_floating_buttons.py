"""A control at the bottom of a page can be brought out from under the floating buttons.

The Assistant and Design buttons float in the bottom-right corner of every
screen. On the night of 2026-11-26 the engineer's walk (card
routing-to-cost-centers) met them on Setup > Admin > Routings: the step rows'
"x" buttons sit in that same corner, so a routing of three steps puts the
last one's remove button under the Assistant, even scrolled to the very end.

So the page keeps room at its end for the two buttons, and a control brought
into view stops above them. Asked here on a scratch plant with the design
surface on (as the lab runs it), at a laptop's 1280 x 720.
"""

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

#: Where a person (or a walk) would press each button, and what is on top there.
UNDER = """(names) => names.map((name) => {
  const btn = [...document.querySelectorAll("button")].find((b) => b.getAttribute("aria-label") === name
      || b.textContent.trim() === name);
  const r = btn.getBoundingClientRect();
  const spots = [[r.left + 2, r.top + 2], [r.right - 2, r.top + 2], [r.left + 2, r.bottom - 2],
                 [r.right - 2, r.bottom - 2], [r.left + r.width / 2, r.top + r.height / 2]];
  const over = spots.map(([x, y]) => document.elementFromPoint(x, y))
                    .filter((top) => top && !btn.contains(top) && !top.contains(btn));
  return over.length ? `${name} is under ${over[0].className || over[0].tagName}` : null;
}).filter(Boolean)"""


def test_the_last_step_of_a_routing_is_not_under_the_floating_buttons(serve_plant, tmp_path_factory):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    store = tmp_path_factory.mktemp("floating-design") / "design.db"
    env = {"MES_DESIGN_CHAT": "1", "MES_DESIGN_CLAUDE": "0", "MES_DESIGN_STORE": str(store)}
    with serve_plant(name="floating-buttons", env=env) as plant, sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            plant.sign_in(context)
            page = context.new_page()
            page.goto(f"{plant.base}/dashboard/admin")
            page.locator("#ops select option").first.wait_for(state="attached", timeout=10000)
            page.locator(".design-launch").wait_for(timeout=10000)
            page.locator(".assist-launch").wait_for(timeout=10000)
            for _ in range(2):
                page.get_by_role("button", name="Add operation").click()
            buttons = ["Remove step 10", "Remove step 20", "Remove step 30",
                       "Add operation", "Create routing"]

            # Scrolled to the very end, every button of the form is clear.
            page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
            assert page.evaluate(UNDER, buttons) == []

            # Brought into view from the top, the way a walk or the Tab key
            # brings it, the last step's button stops above them too.
            page.evaluate("scrollTo(0, 0)")
            page.get_by_role("button", name="Remove step 30").scroll_into_view_if_needed()
            assert page.evaluate(UNDER, ["Remove step 30"]) == []
        finally:
            browser.close()
