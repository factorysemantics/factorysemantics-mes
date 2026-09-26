"""The header menu, and the breadcrumb tree, in a real browser.

Two things Scott asked for on the screens themselves, twelve days apart, and
neither of them had a test:

- /dashboard/line, 2026-09-02: "I'd like the menu to persist on the
  dashboard/line page. I can't get back to the main site from here" ... "the
  header menu that persists throughtout the site" ... "all pages".
- /dashboard/machine/ASSEM1_Kit, 2026-09-04: "why can't i click along the
  tree Mega-Factory > Mega-Factory Works > Assembly > ASSEM1LINE >
  ASSEM1_Kit? ... I can only click the ASSEM1LINE, not any of the others."

Both are promises about rendered pages, so both are checked by rendering the
pages. `fsmes ui-check` crawls every screen in every theme and now files a
finding for a screen with no header on it; this file is the narrower,
faster gate that runs with the suite: two routes, one theme, a real plant,
and a click on every crumb to prove each one goes somewhere.

Marked slow, like everything here that starts a real server: it seeds a
database, serves it on a loopback port of the operating system's choosing,
and drives Chromium. Nothing here touches a plant anyone else is running.
Marked `browser` as well, which is what CI's `browser` job selects on.
Run it with `pytest -m browser tests/test_ui_nav.py`.
"""

import socket
import threading
from urllib.parse import urlparse

import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

DEMO_MACHINE = "MIX01"
# What the demo plant's hierarchy says above MIX01, outermost first - the
# same five-level shape as the mega-factory Scott was looking at.
DEMO_ANCESTORS = ["ACME", "KC1", "PKG", "LINE1"]


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A seeded demo plant, served over HTTP on a port nobody chose in
    advance. A file database rather than the suite's in-memory one, because
    the server answers on its own threads and must open the same rows."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("nav") / "plant.db"
    url = f"sqlite:///{path}"

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_sessionmaker.cache_clear()

        engine = make_engine(url)
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            seed_demo_plant(session)
            auth.ensure_builtin_roles(session)
            session.commit()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(
            create_app(), log_level="warning", lifespan="on"))
        thread = threading.Thread(
            target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            port = sock.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            _wait_until_answering(base)
            yield base
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()


def _wait_until_answering(base: str, seconds: float = 20.0) -> None:
    import time
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=1) as reply:
                if reply.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    raise AssertionError(f"the plant at {base} never answered /health")


@pytest.fixture(scope="module")
def browser(plant):
    """Chromium, signed in the way a person's browser is - the login sets an
    HttpOnly cookie that Playwright shares between its request jar and its
    pages."""
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


def _open(browser, base, route):
    page = browser.new_page()
    page.goto(f"{base}{route}", wait_until="load", timeout=30000)
    # The nav fills in once /auth/me answers; waiting for the first chip is
    # what makes this a check on the page rather than on the network.
    page.wait_for_selector("header[data-nav] nav.nav a", timeout=15000)
    return page


# ------------------------------------------------------------------ the header

@pytest.mark.parametrize("route", [
    "/dashboard",
    "/dashboard/line",
    f"/dashboard/machine/{DEMO_MACHINE}",
    "/dashboard/machines",
    "/dashboard/station",
    "/dashboard/orders",
])
def test_every_screen_carries_the_header_menu_and_a_way_back(browser, plant, route):
    """Scott, on the Line page: "I can't get back to the main site from
    here". A screen that cannot be left is a dead end, whatever else is
    right about it."""
    page = _open(browser, plant, route)
    try:
        header = page.locator("header[data-nav]")
        assert header.count() == 1, f"{route} has {header.count()} headers"
        assert header.is_visible(), f"{route} has a header nobody can see"

        brand = header.locator("a.brand")
        assert brand.is_visible(), f"{route} has no link home"
        assert urlparse(brand.get_attribute("href")).path == "/dashboard"

        chips = header.locator("nav.nav a")
        assert chips.count() >= 2, (
            f"{route}'s header menu lists {chips.count()} screens - a menu "
            f"nobody can go anywhere from is the dead end again")
    finally:
        page.close()


def test_the_header_menu_can_be_reached_by_keyboard(browser, plant):
    """A touchscreen is not the only way onto these screens. Every chip is a
    real link in the document's order, so tabbing from the top of the page
    reaches the menu without anyone trapping focus."""
    page = _open(browser, plant, "/dashboard/line")
    try:
        reached = page.evaluate(
            """() => {
                const header = document.querySelector('header[data-nav]');
                const focusable = [...header.querySelectorAll('a[href], button')];
                return focusable.every(el => el.tabIndex >= 0);
            }""")
        assert reached, "something in the header is out of the tab order"
    finally:
        page.close()


def test_the_line_page_kept_the_header_it_was_missing(browser, plant):
    """The screen the complaint was made on, named on its own so the day it
    regresses the failure says which screen."""
    page = _open(browser, plant, "/dashboard/line")
    try:
        assert page.locator("header[data-nav] nav.nav a").count() >= 2
    finally:
        page.close()


# ------------------------------------------------------------------ the crumbs

def test_every_level_of_the_machine_tree_is_a_link_except_the_one_you_are_on(browser, plant):
    """Scott: "I really like that structure and it makes sense to me and I
    want to be able to click those." Enterprise, site, area and line are
    links; the machine you are looking at is text, because a link to the
    page you are already on is a lie about where it would take you."""
    page = _open(browser, plant, f"/dashboard/machine/{DEMO_MACHINE}")
    try:
        page.wait_for_selector("#crumbs a", timeout=15000)
        codes = page.eval_on_selector_all(
            "#crumbs a", "els => els.map(e => e.getAttribute('href'))")
        assert len(codes) == len(DEMO_ANCESTORS), (
            f"{len(codes)} of {len(DEMO_ANCESTORS)} ancestors are clickable: {codes}")
        for code in DEMO_ANCESTORS:
            assert any(code in href for href in codes), f"{code} is not a link"

        last = page.locator("#crumbs > *").last
        assert last.evaluate("el => el.tagName") != "A", (
            "the machine you are on links to itself")
        assert last.get_attribute("aria-current") == "page"
    finally:
        page.close()


def test_a_line_crumb_opens_the_line_and_the_levels_above_it_open_the_tree(browser, plant):
    """Where each level goes, not just that it goes somewhere. A work center
    is a line and has its own screen; a site or an area has no screen of its
    own yet, so it opens the Machines tree scoped to that node."""
    page = _open(browser, plant, f"/dashboard/machine/{DEMO_MACHINE}")
    try:
        page.wait_for_selector("#crumbs a", timeout=15000)
        hrefs = dict(page.eval_on_selector_all(
            "#crumbs a", "els => els.map(e => [e.textContent, e.getAttribute('href')])"))
        by_code = {href.split("=")[-1]: href for href in hrefs.values()}
        assert by_code["LINE1"].startswith("/dashboard/line?line=")
        for above in ("ACME", "KC1", "PKG"):
            assert by_code[above].startswith("/dashboard/machines?under=")
    finally:
        page.close()


@pytest.mark.parametrize("code", DEMO_ANCESTORS)
def test_clicking_a_crumb_lands_on_a_screen_that_answers(browser, plant, code):
    """The promise is not that the crumb is blue - it is that clicking it
    takes you somewhere that renders. Each ancestor is clicked, and the page
    it lands on has to come up with its own header on it."""
    page = _open(browser, plant, f"/dashboard/machine/{DEMO_MACHINE}")
    try:
        page.wait_for_selector("#crumbs a", timeout=15000)
        page.locator(f"#crumbs a[href*='{code}']").first.click()
        page.wait_for_load_state("load")
        page.wait_for_selector("header[data-nav] nav.nav a", timeout=15000)
        assert code in page.url, f"clicking {code} went to {page.url}"
        assert page.locator("header[data-nav]").is_visible()
    finally:
        page.close()


def test_the_tree_screen_draws_the_same_trail_as_the_machine_page(browser, plant):
    """Two screens drew this trail and each picked its own link for each
    level, so which ancestors were clickable depended on which screen you
    were standing on. One renderer now, and this is what holds it: from the
    Machines tree scoped to a machine, the line above it opens the Line view
    exactly as it does from the machine's own page."""
    page = _open(browser, plant, f"/dashboard/machines?under={DEMO_MACHINE}")
    try:
        page.wait_for_selector("#tree-crumbs a", timeout=15000)
        hrefs = page.eval_on_selector_all(
            "#tree-crumbs a", "els => els.map(e => e.getAttribute('href'))")
        assert "/dashboard/machines" in hrefs, "no link back to the whole plant"
        assert any(h.startswith("/dashboard/line?line=LINE1") for h in hrefs), hrefs
        last = page.locator("#tree-crumbs > *").last
        assert last.evaluate("el => el.tagName") != "A"
    finally:
        page.close()


# --------------------------------------------- one Configuration entry, not many

def _chips(page):
    """Every chip a person can see, workspace row and screen row together."""
    return [text.strip() for text in
            page.locator("header[data-nav] nav.nav a, nav.subnav a").all_inner_texts()]


def test_a_configurable_thing_is_a_row_in_configuration_not_a_chip_of_its_own(
        browser, plant):
    """Scott, 2026-09-21, testing the pilot build: the downtime vocabulary had
    arrived in the nav bar as a top-level chip, and he does not want one of
    those per configurable thing - there will eventually be hundreds of them.
    So Engineering carries one Configuration entry, and the vocabulary is a
    row on the page behind it."""
    page = _open(browser, plant, "/dashboard/machines")
    try:
        chips = _chips(page)
        assert "Configuration" in chips, f"Engineering has no Configuration entry: {chips}"
        assert "Downtime reasons" not in chips, (
            f"a configurable thing is still a chip of its own: {chips}")
    finally:
        page.close()


def test_the_configuration_page_lists_what_is_configurable_and_states_its_total(
        browser, plant):
    """A list of sections that happens to be short today, not a single screen
    wearing a plural name - so it says how many, even when it is one.

    The number is read from the registry, never written down here. This test
    asserted "one section" and went red the day Engineering gained seventeen
    more (PR #104), and nobody saw it, because nothing in CI ran it. A count
    that was true on one day is not a promise; that the page shows everything
    the registry holds, and says how many that is, is.
    """
    from fsmes import modules

    # The plant fixture serves every module this version has (no MES_MODULES
    # in its environment), so the registry unfiltered is what the page should
    # be showing. A plant with modules switched off would show fewer, and the
    # endpoint states that separately.
    expected = modules.config_sections("engineering")
    labels = [section.label for section in expected]

    page = _open(browser, plant, "/dashboard/config/engineering")
    try:
        page.wait_for_selector("#sections-table tbody tr", timeout=15000)
        # Group headings are rows too - one per module, with the module's
        # title - so the sections are the rows that are not headings.
        rows = page.locator("#sections-table tbody tr:not(.group)")
        assert rows.count() == len(expected), (
            f"the page shows {rows.count()} sections; "
            f"the registry holds {len(expected)}")
        assert [name.strip() for name in
                page.locator("#sections-table tbody tr:not(.group) a.obj")
                .all_inner_texts()] == labels

        noun = "section" if len(expected) == 1 else "sections"
        assert f"{len(expected)} {noun}" in page.locator("#section-count").inner_text()

        # The vocabulary that was a top-level chip until 2026-09-21 is a row
        # here, and its row opens the screen the registry says it opens.
        vocabulary = next(s for s in expected if s.key == "downtime_reasons")
        href = (page.locator("#sections-table tbody tr:not(.group)")
                .nth(labels.index(vocabulary.label))
                .locator("a.obj").get_attribute("href"))
        assert urlparse(href).path == urlparse(vocabulary.href).path
        # The page says where the door is; it does not move who may open it.
        assert vocabulary.define in page.locator("#sections-table").inner_text()
    finally:
        page.close()


def test_the_vocabulary_can_still_be_drafted_from_inside_configuration(browser, plant):
    """The whole point of moving the entry is that nothing behind it moved.
    Two clicks from the Configuration page to a drafted word, in a browser,
    the way an engineer does it."""
    page = _open(browser, plant, "/dashboard/config/engineering")
    try:
        page.wait_for_selector("#sections-table tbody tr a", timeout=15000)
        page.locator("#sections-table tbody tr a").first.click()
        page.wait_for_url("**/dashboard/reasons", timeout=15000)
        page.wait_for_selector("#reason-form", timeout=15000)

        # Standing inside a section, the workspace's Configuration entry is
        # the one marked - the way a machine page marks Machines.
        assert page.locator("nav.subnav a[aria-current='page']").inner_text().strip() \
            == "Configuration"

        page.fill("#r-code", "ui_seam_check")
        page.fill("#r-name", "Drafted from inside Configuration")
        page.fill("#r-description", "A browser check that the move changed no behaviour.")
        page.click("#r-submit")

        page.wait_for_selector("#reasons-table tbody tr:has-text('ui_seam_check')",
                               timeout=15000)
    finally:
        page.close()
