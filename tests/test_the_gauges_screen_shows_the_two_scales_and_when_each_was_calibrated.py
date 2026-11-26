"""The Gauges screen, on a plant built from the bottling pack, in a browser.

What Scott asked for on 2026-10-05 begins here: *"What source did it come
from? What's that source's calibration?"* The register page has existed for
weeks and every plant built from a pack came up with nothing on it, because a
pack could not carry a gauge. This is the screen with the pack's gauges on it,
each with the date it was last calibrated and whether it is due.

The pack carried two checkweigher scales when this file was written. It
carries a third instrument now - the bench height gauge the QI station
measures fill height with, five bottles at a time - so the register is three
rows and the count below says three. The scales' own assertions are
untouched: what changed is the plant, not what the screen owes a reader about
a scale.

**The list is drawn after a fetch, so the test holds the fetch back.** A check
on loopback proves the code path and not the experience: the page renders in
six milliseconds here and in rather more on Scott's phone over the tailnet. A
route intercept delays `/quality/gauges` by more than a second, and the
assertions wait on the *text* they are about rather than on the element that
will hold it - the 2026-09-26 lesson from #112, where `wait_for_selector` then
`input_value` read "" on GitHub's runner and "90" on loopback.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for, and a Playwright file marked only `slow` is a test nothing runs.
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

ROOT = Path(__file__).resolve().parents[1]
BOTTLING = ROOT / "labs" / "multiplant" / "bottling"
#: How long the gauge register is held back for. Comfortably longer than the
#: page takes to render on loopback, so a passing test means the screen waited
#: for the answer rather than that the answer was already there.
HELD_BACK_MS = 1200


@pytest.fixture(scope="module")
def plant(serve_plant):
    """A plant seeded from the bottling pack, served on a port nobody chose."""
    from fsmes.integrations.opc.tag_map import load_tag_map
    from fsmes.pack import masterdata
    from fsmes.services import auth

    def seed(session):
        cycles = {m.equipment: m.cycle_seconds
                  for m in load_tag_map(BOTTLING / "tag_map.json")}
        masterdata.seed(session, BOTTLING / "masterdata", cycles)
        # A pack seeds a plant, not its people: who may sign in is the
        # fleet's business and a pack holds no password. So this test
        # makes the one account it needs.
        auth.create_user(session, code="ADMIN", name="Plant administrator",
                         password="admin", role="admin")

    env = {"MES_PLANT_TIMEZONE": "UTC", "MES_MODULES": None, "MES_WORDS": None,
           "MES_PLANT_NAME": None, "MES_TAG_MAP_FILE": None}
    with serve_plant(seed, name="gauges", env=env, demo=False) as served:
        yield served.base


@pytest.fixture(scope="module")
def browser(plant):
    """Chromium, signed in the way a person's browser is."""
    import json

    sync_playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="the [dev] extra is not installed").sync_playwright

    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            chromium = pw.chromium.launch()
            context = chromium.new_context(viewport={"width": 1400, "height": 900})
            reply = context.request.post(
                f"{plant}/auth/login",
                data=json.dumps({"code": "ADMIN", "password": "admin"}),
                headers={"Content-Type": "application/json"})
            assert reply.ok, f"sign-in failed: {reply.status}"
            yield context
            chromium.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


@pytest.fixture
def register(browser, plant):
    """The Gauges screen, with its register held back by over a second."""
    page = browser.new_page()

    def held(route):
        # A fetch that does not come back instantly, which is every fetch on
        # a phone over a tailnet and no fetch at all on loopback.
        page.wait_for_timeout(HELD_BACK_MS)
        route.continue_()

    page.route("**/quality/gauges*", held)
    page.goto(f"{plant}/dashboard/gauges", wait_until="load", timeout=30000)
    try:
        yield page
    finally:
        page.close()


def test_the_screen_lists_the_packs_two_scales_with_the_date_each_was_calibrated(register):
    """Both scales, on the filler, each with its own last-calibrated date -
    and the one the pack says is nearly due warned about as due soon."""
    register.wait_for_function(
        """() => {
             const rows = [...document.querySelectorAll('#gauges-table tbody tr')];
             return rows.filter(r => /SCALE-FILL/.test(r.textContent)).length === 2;
           }""",
        timeout=20000)
    rows = {}
    for row in register.query_selector_all("#gauges-table tbody tr"):
        text = row.inner_text()
        for code in ("SCALE-FILL-01", "SCALE-FILL-02"):
            if code in text:
                rows[code] = text
    assert set(rows) == {"SCALE-FILL-01", "SCALE-FILL-02"}, rows
    for code, text in rows.items():
        assert "FILL01" in text, f"{code} does not say where it is: {text}"
        assert "0.1" in text, f"{code} does not say what it resolves to: {text}"
        assert "never" not in text, f"{code} shows as never calibrated: {text}"
        # A date, drawn from the register's own `last_calibrated`.
        assert "-" in text and "20" in text, f"{code} shows no calibration date: {text}"
    assert "due" in rows["SCALE-FILL-02"].lower(), (
        "the scale six days from its calibration is not warned about: "
        f"{rows['SCALE-FILL-02']}")


def test_the_screen_lists_the_bench_gauge_the_fill_height_sample_is_measured_with(register):
    """The third instrument in the pack. Fill height is inspected five bottles
    at a time on the QI bench, and the gauge that measures them is a gauge like
    any other: on the register, where it is, what it resolves to, and when it
    was last calibrated. A sampled characteristic measured through an
    instrument nobody could look up would be the gap this screen exists to
    close."""
    register.wait_for_function(
        """() => [...document.querySelectorAll('#gauges-table tbody tr')]
                   .some(r => /HEIGHT-FILL-01/.test(r.textContent))""",
        timeout=20000)
    row = next(r.inner_text() for r in register.query_selector_all("#gauges-table tbody tr")
               if "HEIGHT-FILL-01" in r.inner_text())
    assert "QI01" in row, f"the height gauge does not say where it is: {row}"
    assert "0.1" in row, f"the height gauge does not say what it resolves to: {row}"
    assert "never" not in row, f"the height gauge shows as never calibrated: {row}"
    assert "due" not in row.lower(), (
        "the height gauge is 168 days from due and is being warned about: "
        f"{row}")


def test_the_page_says_how_many_gauges_there_are_and_that_none_is_overdue(register):
    """Every list states its total, and the verdict is the whole register's -
    a plant whose instruments are in calibration should be told so rather than
    shown a blank. Three now: the filler's two scales and the QI bench's
    height gauge."""
    register.wait_for_function(
        """() => document.querySelector('#kpi-gauges').textContent.trim() === '3'""",
        timeout=20000)
    register.wait_for_function(
        """() => document.querySelector('#kpi-overdue').textContent.trim() === '0'""",
        timeout=20000)
    verdict = register.inner_text("#kpi-verdict")
    assert "calibration" in verdict.lower(), verdict
    assert register.inner_text("#kpi-soon").strip() == "1", (
        "one scale is six days from due; the screen does not say so")
