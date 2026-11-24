"""Quality > Gauge studies, in a browser, on a plant with the worn-gauge case in it.

The page draws its list and the study after two fetches, so the test holds
the list back by more than a second and waits on the text it asserts, never
on the element that will hold it (#112, 2026-09-26). Two things a quality
engineer does here: read why a study failed, and type a reading into a
study that is still collecting.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for.
"""

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

ROOT = Path(__file__).resolve().parents[1]
HELD_BACK_MS = 1200


@pytest.fixture(scope="module")
def plant(serve_plant):
    """The demo plant with the worn-gauge case run into it through its API,
    and a second study planned and not yet read."""
    import httpx

    from fsmes.sim import gage_study

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_PLANT_TIMEZONE", "UTC")
        with serve_plant(name="studies") as served:
            base = served.base
            with httpx.Client(base_url=base, timeout=30) as client:
                assert client.post("/auth/login", json={"code": "ADMIN", "password": "admin"}).is_success
                for code in ("UT-FORK", "UT-SPOON"):
                    client.post("/masterdata/materials",
                                json={"code": code, "name": code, "type": "finished"}).raise_for_status()
                client.post("/quality/specs", json={
                    "material": "UT-FORK", "characteristic": "thickness_mm", "unit": "mm",
                    "min_value": 1.9, "max_value": 2.3}).raise_for_status()
                gage_study.run(client, gage_study.load(ROOT / "labs" / "cutlery" / "gage_studies.json")["worn-gauge"])
                client.post("/quality/gage-studies", json={
                    "code": "GS-OPEN", "gauge": "MIC-07", "material": "UT-FORK",
                    "characteristic": "thickness_mm", "appraisers": ["Ana", "Ben"],
                    "parts": 5, "trials": 2}).raise_for_status()
            yield base


@pytest.fixture(scope="module")
def browser(plant):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            chromium = pw.chromium.launch()
            context = chromium.new_context(viewport={"width": 1400, "height": 900})
            reply = context.request.post(f"{plant}/auth/login",
                                         data=json.dumps({"code": "ADMIN", "password": "admin"}),
                                         headers={"Content-Type": "application/json"})
            assert reply.ok, f"sign-in failed: {reply.status}"
            yield context
            chromium.close()
    except PlaywrightError as err:
        pytest.skip(f"chromium is not installed for playwright: {err}")


def _open(browser, plant, study):
    page = browser.new_page()

    def held(route):
        # The list is what the page draws late; a plan posted to the same
        # path goes straight through.
        if route.request.method == "GET":
            page.wait_for_timeout(HELD_BACK_MS)
        route.continue_()

    page.route("**/quality/gage-studies", held)
    page.goto(f"{plant}/dashboard/gauges/studies?study={study}", wait_until="load", timeout=30000)
    return page


def test_the_worn_gauge_study_says_not_acceptable_and_draws_repeatability_as_the_long_bar(browser, plant):
    page = _open(browser, plant, "GS-WORN-MIC")
    try:
        page.wait_for_function(
            "() => /not acceptable/i.test(document.querySelector('#study-verdict').textContent)"
            " && document.querySelector('#study-chart svg')", timeout=20000)
        assert "GS-WORN-MIC" in page.inner_text("#study-title")
        rows = page.inner_text("#components-table tbody")
        assert "Repeatability" in rows and "Reproducibility" in rows and "Part-to-part" in rows
        assert "90 of 90" in page.inner_text("#studies-table tbody")
        assert page.text_content("#studies-count") == "2 in total"
    finally:
        page.unroute_all(behavior="ignoreErrors")
        page.close()


def test_a_reading_typed_into_the_grid_is_saved_and_the_count_goes_up(browser, plant):
    page = _open(browser, plant, "GS-OPEN")
    try:
        cell = 'input[aria-label="part 1, Ana, trial 1"]'
        page.wait_for_function(
            f"() => document.querySelector('{cell}') !== null"
            " && /0 of 20 in/.test(document.querySelector('#grid-count').textContent)", timeout=20000)
        page.fill(cell, "2.101")
        page.click("#grid-save")
        page.wait_for_function(
            "() => /1 of 20 in/.test(document.querySelector('#grid-count').textContent)", timeout=20000)
        assert page.query_selector(cell) is None
        assert "Not judged" in page.inner_text("#study-verdict")
    finally:
        page.unroute_all(behavior="ignoreErrors")
        page.close()


def test_a_refused_plan_says_why_beside_the_form_and_the_list_does_not_grow(browser, plant):
    page = _open(browser, plant, "GS-WORN-MIC")
    try:
        page.wait_for_function(
            "() => document.querySelector('#plan-gauge').options.length >= 1"
            " && /in total/.test(document.querySelector('#studies-count').textContent)", timeout=20000)
        before = page.text_content("#studies-count")
        page.fill("#plan-material", "UT-FORK")
        page.fill("#plan-characteristic", "thickness_mm")
        page.fill("#plan-appraisers", "Ana")
        page.click("#plan-submit")
        page.wait_for_function(
            "() => /at least 2 appraisers/.test(document.querySelector('#plan-refused').textContent)",
            timeout=20000)
        assert page.text_content("#plan-refused").startswith("Not planned:")
        assert page.text_content("#studies-count") == before
    finally:
        page.unroute_all(behavior="ignoreErrors")
        page.close()
