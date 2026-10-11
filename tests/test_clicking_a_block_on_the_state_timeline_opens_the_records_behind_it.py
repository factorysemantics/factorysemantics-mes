"""Click a block on the machine's state timeline in a browser, and read the panel.

The bar of running / idle / down / setup was a picture. This is a person
meeting it: the bar, a block, a click - or a tab and Enter - and the records
beside it. The read itself is pinned in
`test_a_block_on_the_state_timeline_carries_the_records_behind_it`; this file is
the screen, in Chromium, in all four themes.

**THE BLOCK READ IS HELD BACK BY OVER A SECOND.** A check on loopback proves
the code path and not the experience: this page draws in single-digit
milliseconds here and in rather more on Scott's phone over the tailnet. A route
intercept delays `/analysis/timeline/block/...`, and every assertion waits on
the panel's own `data-block` attribute - which the page writes LAST, after the
blocks are on the screen, and clears while a fetch is in flight - rather than
on an element that is on the page before the answer is. That is the 2026-09-26
lesson from #112 and #113, where `wait_for_selector` and then a read gave ""
on GitHub's runner and the right answer on loopback.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for, and a Playwright file marked only `slow` is a test nothing runs.
"""

import socket
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

THEMES = ("control-room", "daylight", "high-contrast", "night-shift")

#: How long the block read is held back for. Comfortably longer than the page
#: takes to draw on loopback, so a passing assertion means the panel waited for
#: its answer rather than that the answer was already there.
HELD_BACK_MS = 1200

#: The window the page is opened on. Two hours holds the whole story below and
#: draws each block wide enough for a person - and for Playwright - to hit.
WINDOW_HOURS = 2

#: The two orders either side of the changeover, in the words of the lab's own
#: answer key: `labs/multiplant/bottling/line.json` -> `_chain` -> link 1.
BEFORE_ORDER = "4711"
AFTER_ORDER = "4712"


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A shift on the demo mixer with every block a test below clicks on.

    Reading down the clock, where `at` is when the fixture is built:

    * `at - 100 min`  five minutes down that nobody has named - the stop the
      labelling test names from the panel;
    * `at - 80 min`   five more that nobody has named - the stop the *nobody
      has said why yet* test reads, left alone by that write;
    * `at - 50 min`   the changeover: four minutes of setup, named *Changeover*
      by ANNA afterwards, with order 4711 booked before it and 4712 after;
    * `at - 30 min`   ten minutes down for a chiller clean, with a maintenance
      order in progress across it and named in the stop's own reason;
    * `at - 20 min`   running, still open.

    Two brix readings five minutes after the changeover, one out of
    specification; the machine's temperature either side of it; and a viewer
    account, because what the panel says when it refuses is part of the screen.
    """
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine, utcnow
    from fsmes.domain import (
        Equipment,
        EquipmentState,
        EquipmentStateName,
        MaintenanceKind,
        MaintenanceOrder,
        MaintenanceStatus,
        OperationStatus,
        ProductionLog,
        ProductionSource,
        TagValue,
    )
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth, quality, reasons, workorders
    from fsmes.services import equipment as equipment_service

    path = tmp_path_factory.mktemp("stopblock") / "plant.db"
    url = f"sqlite:///{path}"

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        env.setenv("MES_PLANT_TIMEZONE", "UTC")
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_sessionmaker.cache_clear()

        engine = make_engine(url)
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            seed_demo_plant(session)
            auth.ensure_builtin_roles(session)
            auth.create_user(session, code="LOOKER", name="Looker",
                             password="looker-password", role="viewer")
            at = utcnow()
            first = (at - timedelta(minutes=100), at - timedelta(minutes=95))
            second = (at - timedelta(minutes=80), at - timedelta(minutes=75))
            setup = (at - timedelta(minutes=50), at - timedelta(minutes=46))
            down = (at - timedelta(minutes=30), at - timedelta(minutes=20))

            reasons.define(session, code="changeover", name="Changeover",
                           description="Product change on the machine.", actor="ADMIN")
            reasons.approve(session, "changeover", 1, actor="ADMIN")

            mixer = session.scalar(select(Equipment).where(Equipment.code == "MIX01"))
            for code, started, completed in (
                    (BEFORE_ORDER, at - timedelta(hours=3),
                     setup[0] - timedelta(seconds=30)),
                    (AFTER_ORDER, setup[1] + timedelta(minutes=1), None)):
                workorders.create(session, code=code, material_code="FG-COLA",
                                  quantity=1000)
                order = workorders.release(session, code)
                operation = order.operations[0]
                operation.started_at = started
                operation.completed_at = completed
                operation.status = (OperationStatus.DONE if completed
                                    else OperationStatus.RUNNING)
                session.flush()
            for code, when, good in ((BEFORE_ORDER, setup[0] - timedelta(minutes=10), 500.0),
                                     (AFTER_ORDER, setup[1] + timedelta(minutes=2), 20.0)):
                order = workorders.get(session, code)
                session.add(ProductionLog(
                    work_order_id=order.id, operation_id=order.operations[0].id,
                    equipment_id=mixer.id, good_qty=good, scrap_qty=0.0,
                    source=ProductionSource.OPC, ts=when))

            for state, started, ended, reason in (
                    (EquipmentStateName.RUNNING, at - timedelta(hours=3), first[0], None),
                    (EquipmentStateName.DOWN, *first, None),
                    (EquipmentStateName.RUNNING, first[1], second[0], None),
                    (EquipmentStateName.DOWN, *second, None),
                    (EquipmentStateName.RUNNING, second[1], setup[0], None),
                    (EquipmentStateName.SETUP, *setup, None),
                    (EquipmentStateName.RUNNING, setup[1], down[0], None),
                    (EquipmentStateName.DOWN, *down, "planned maintenance MO-CHILL"),
                    (EquipmentStateName.RUNNING, down[1], None, None)):
                session.add(EquipmentState(
                    equipment_id=mixer.id, state=state, reason=reason,
                    started_at=started, ended_at=ended))
            session.flush()
            # Somebody names the changeover afterwards, over the window they
            # chose - the write the panel's own control posts to.
            equipment_service.name_stops(
                session, equipment_code="MIX01", start=setup[0], end=setup[1],
                reason_code="changeover", reason="Changeover", actor="ANNA")

            session.add(MaintenanceOrder(
                code="MO-CHILL", equipment_id=mixer.id, kind=MaintenanceKind.PREVENTIVE,
                status=MaintenanceStatus.IN_PROGRESS,
                summary="Clean the chiller condenser", needs_stop=True,
                raised_at=at - timedelta(hours=6),
                started_at=down[0] - timedelta(minutes=2), performed_by="NILS"))

            moment = setup[0] - timedelta(minutes=15)
            while moment <= setup[1] + timedelta(minutes=5):
                session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Temperature",
                                     ts=moment,
                                     value_num=62.0 if moment < setup[0] else 58.5))
                moment += timedelta(seconds=10)

            for value in (14.0, 11.0):
                check, _nc, _signals = quality.record_check(
                    session, material_code="FG-COLA", characteristic="brix",
                    value=value, equipment_code="MIX01", actor="OP-DAY")
                check.ts = setup[1] + timedelta(minutes=5)
            session.commit()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(
            create_app(), log_level="warning", lifespan="on"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
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
def chromium(plant):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="the [dev] extra is not installed").sync_playwright

    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            yield browser
            browser.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


def _signed_in(chromium, base, code, password):
    import json

    context = chromium.new_context(viewport={"width": 1500, "height": 1100})
    reply = context.request.post(
        f"{base}/auth/login",
        data=json.dumps({"code": code, "password": password}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in as {code} failed: {reply.status}"
    return context


def _open_timeline(context, base, theme="control-room"):
    """The machine page on its Timeline tab, with the block read held back.

    The folds a reader opened are remembered in `localStorage` and the context
    is shared by every test in this file, so one test's click would otherwise
    decide what the next one finds open.
    """
    page = context.new_page()
    page.add_init_script(
        f"try {{ localStorage.setItem('fsmes-theme', {theme!r}); }} catch (e) {{}}")
    page.add_init_script(
        """try {
             for (const key of Object.keys(localStorage)) {
               if (key.startsWith('fsmes-stop-fold-')) localStorage.removeItem(key);
             }
           } catch (e) {}""")

    def held(route):
        page.wait_for_timeout(HELD_BACK_MS)
        route.continue_()

    page.route("**/timeline/block/**", held)
    page.goto(f"{base}/dashboard/machine/MIX01?hours={WINDOW_HOURS}#timeline",
              wait_until="load", timeout=30000)
    # The bar is drawn after its fetch settles. Wait for the blocks a reader
    # can open, not for the chart node, which is there before the answer is.
    page.wait_for_function(
        "() => document.querySelectorAll('#tl rect.state-mark[role=\\\"button\\\"]')"
        ".length >= 4", timeout=30000)
    return page


def _close(page):
    """Take the delay off before closing, or a held fetch still in flight
    raises into a route callback whose page has gone away."""
    page.unroute_all(behavior="ignoreErrors")
    page.close()


@pytest.fixture(scope="module")
def admin(chromium, plant):
    context = _signed_in(chromium, plant, "ADMIN", "admin")
    yield context
    context.close()


@pytest.fixture()
def page(admin, plant):
    node = _open_timeline(admin, plant)
    yield node
    _close(node)


def _blocks(page, state=None):
    """Every openable block on the bar, oldest first, as the page drew them."""
    rows = page.evaluate(
        """() => [...document.querySelectorAll('#tl rect.state-mark')].map((r) => ({
               state: r.getAttribute('data-state'),
               label: r.getAttribute('data-label'),
               aria: r.getAttribute('aria-label'),
               role: r.getAttribute('role'),
               tabindex: r.getAttribute('tabindex'),
               x: Number(r.getAttribute('x')),
             })).sort((a, b) => a.x - b.x)""")
    return [row for row in rows if state is None or row["state"] == state]


def _open_block(page, which=0, state=None, key=None):
    """Click (or key) one block and wait on the panel's own attribute.

    Not on `#block-body`, and not on a sleep: the box is on the page before the
    read is, and a non-empty `data-block` means *this block is drawn*.
    """
    rows = _blocks(page, state)
    assert rows, f"no {state or 'openable'} block was drawn"
    target = f"#tl rect.state-mark[data-state='{rows[which]['state']}']"
    handle = page.query_selector_all(target)[which if state else 0]
    if key:
        handle.focus()
        page.keyboard.press(key)
    else:
        handle.click()
    _await_panel(page)
    return rows[which]


def _await_panel(page):
    page.wait_for_function(
        """() => {
             const panel = document.querySelector('#block-panel');
             return panel.dataset.state === 'open' && panel.dataset.block !== '';
           }""", timeout=30000)


def _unfold(page, key):
    """Make sure one of the panel's folds is open, and wait for its own `open`
    attribute - a `<details>` is laid out by the browser and not by this page's
    script (#112). The page opens two of the three by default and remembers
    what a reader did with them, so this asks whether it is open before
    clicking: a blind click closes the ones that already were."""
    if not page.evaluate("(k) => document.querySelector(`[data-fold=\"${k}\"]`).open",
                         key):
        page.click(f'[data-fold="{key}"] > summary')
    page.wait_for_function(
        "(k) => document.querySelector(`[data-fold=\"${k}\"]`).open === true",
        arg=key, timeout=10000)


# ------------------------------------------------------------ it is clickable


def test_the_panel_says_nothing_is_open_until_a_block_is_clicked(page):
    panel = page.query_selector("#block-panel")
    assert panel.get_attribute("data-state") == "idle"
    assert panel.get_attribute("data-block") == ""
    assert "No block is open" in page.inner_text("#block-idle")
    assert page.query_selector("#block-body").is_hidden()


def test_every_block_on_the_bar_is_a_button_a_person_can_reach_with_a_keyboard(page):
    rows = _blocks(page)
    assert len(rows) >= 8, f"the shift drew {len(rows)} blocks"
    assert {row["role"] for row in rows} == {"button"}
    assert {row["tabindex"] for row in rows} == {"0"}
    for row in rows:
        assert row["aria"].endswith("open the records behind it")
        assert row["aria"].startswith("MIX01 ")
    # And the states the fixture wrote are the states on the bar.
    assert {row["state"] for row in rows} == {"running", "down", "setup"}


def test_the_panel_waits_for_its_answer_rather_than_drawing_an_empty_one(page):
    """The 2026-09-25 lesson. While the read is in flight the panel says it is
    opening and carries no block: a reader on a slow line must not be shown a
    panel that is about the last thing they clicked."""
    page.click("#tl rect.state-mark[data-state='setup']")
    panel = page.query_selector("#block-panel")
    assert panel.get_attribute("data-state") == "loading"
    assert panel.get_attribute("data-block") == ""
    # Lower-cased because this line is set in small capitals by the stylesheet,
    # and `inner_text` reads what the page renders.
    assert "opening" in page.inner_text("#block-which").lower()
    _await_panel(page)
    assert page.query_selector("#block-panel").get_attribute("data-state") == "open"


def test_the_block_a_reader_opened_is_outlined_on_the_bar_and_only_that_one(page):
    _open_block(page, state="setup")
    outlines = page.evaluate(
        """() => [...document.querySelectorAll('#tl [data-open-block=\\"true\\"]')]
               .map((r) => ({ fill: r.getAttribute('fill'),
                              role: r.getAttribute('role'),
                              hidden: r.getAttribute('aria-hidden') }))""")
    assert len(outlines) == 1
    # A ring, not a block: it paints no area, takes no clicks and is not a
    # second thing for a screen reader to find.
    assert outlines[0] == {"fill": None, "role": None, "hidden": "true"}


# -------------------------------------------------------- what the panel says


def test_clicking_the_changeover_says_what_it_was_who_named_it_and_both_orders(page):
    """Link 1 of the chain, as a person meets it: one click on the bar and the
    four-minute setup, the word the floor gave it, the person who gave it, and
    the orders either side are all on the screen."""
    _open_block(page, state="setup")
    text = page.inner_text("#block-panel")
    assert "Setup, 4 min" in text
    assert "Changeover" in text
    assert "ANNA" in text
    assert "named it Changeover" in text
    assert f"{BEFORE_ORDER} before, {AFTER_ORDER} after" in text
    assert "the hand-over between two orders" in text
    # Derived, and said so on the screen rather than in a docstring.
    assert "derived from what was running, not a recorded link" in text
    assert "one — one state record, as drawn" in text


def test_the_planned_down_block_reads_as_planned_work_and_not_as_a_breakdown(page):
    """The steward's question. A chiller clean somebody was doing must read as
    the job it was, with the order, its state and the person on it."""
    _open_block(page, state="down", which=2)
    text = page.inner_text("#block-panel")
    assert "Down, 10 min" in text
    assert "planned work: this stop's own reason names MO-CHILL" in text
    assert "done by NILS" in text
    assert "This is the job, not a breakdown" in text
    assert "preventive" in text and "in_progress" in text
    # Needing a stop is what put the chiller clean on the bar at all (#158 put
    # that in the data), and it is on the row with the order.
    row = page.evaluate(
        """() => [...document.querySelectorAll('#block-panel table tr')]
               .map((tr) => tr.innerText)
               .find((text) => text.includes('MO-CHILL'))""")
    assert row and "yes" in row, row


def test_a_stop_nobody_has_named_says_nobody_has(page):
    _open_block(page, state="down", which=1)
    text = page.inner_text("#block-panel")
    assert "nobody has said why yet" in text
    assert "Nobody is recorded as saying so" in text
    assert "No audit row matches this stretch" in text
    # And it is not called a breakdown either: nothing in the records says
    # anybody planned it, and nothing says anybody broke it.
    assert ("maintenance orders touch this window but none was in progress "
            "across the block") in text


def test_a_block_opened_with_a_keyboard_opens_the_same_panel(page):
    """Reachable means usable. Tab to a block, press Enter, same records."""
    block = _open_block(page, state="setup", key="Enter")
    assert block["state"] == "setup"
    assert "Setup, 4 min" in page.inner_text("#block-panel")
    assert page.query_selector("#block-panel").get_attribute("data-state") == "open"


def test_the_machines_own_signals_and_what_quality_saw_are_one_fold_each(page):
    _open_block(page, state="setup")
    assert page.query_selector('[data-fold="tags"]')
    _unfold(page, "tags")
    tags = page.inner_text('[data-fold="tags"]')
    assert "Temperature" in tags
    assert "The block's start and end are marked on each." in tags
    assert page.query_selector('[data-fold="tags"] svg.fs-chart')

    _unfold(page, "quality")
    saw = page.inner_text('[data-fold="quality"]')
    assert "14" in saw and "over spec" in saw
    assert "fail" in saw
    assert "no rule fired" in saw


def test_the_panel_states_how_much_of_the_block_anybody_was_watching(page):
    _open_block(page, state="setup")
    _unfold(page, "watched")
    watched = page.inner_text('[data-fold="watched"]')
    assert "100% watched" in watched


def test_every_row_that_belongs_to_another_page_carries_the_link_to_it(page):
    _open_block(page, state="setup")
    _unfold(page, "quality")
    hrefs = page.evaluate(
        "() => [...document.querySelectorAll('#block-panel a[href]')]"
        ".map((a) => a.getAttribute('href'))")
    assert f"/dashboard/orders?q={BEFORE_ORDER}" in hrefs
    assert f"/dashboard/orders?q={AFTER_ORDER}" in hrefs
    assert "/dashboard/reasons" in hrefs
    assert any(href.startswith("/dashboard/spc?spec=FG-COLA") for href in hrefs)


def test_the_panel_carries_the_anchor_a_guide_points_at(page):
    """Style rule 7. Renaming this is a deliberate act that re-authors whatever
    points at it, which is why it is asserted here rather than left to be
    noticed."""
    assert page.query_selector('[data-assist="machine-block-panel"]')


def test_the_panel_sits_beside_the_bar_rather_than_under_it(page):
    """"A panel beside the bar" is the handoff's own words, and on a desktop
    width it has to actually be beside it: a reader comparing the panel with
    the block they clicked cannot scroll away from one to read the other."""
    _open_block(page, state="setup")
    boxes = page.evaluate(
        """() => {
             const bar = document.querySelector('#tl').getBoundingClientRect();
             const panel = document.querySelector('#block-panel').getBoundingClientRect();
             return { barRight: bar.right, panelLeft: panel.left,
                      barTop: bar.top, panelTop: panel.top };
           }""")
    assert boxes["panelLeft"] >= boxes["barRight"] - 1
    assert abs(boxes["panelTop"] - boxes["barTop"]) < 200


# ------------------------------------------------------ naming a stop from here


def test_a_reader_whose_role_cannot_name_a_stop_is_told_who_can(chromium, plant):
    """`refusal-names-who-can`. A viewer sees no control and is not left
    guessing why: the panel names the capability and the roles that hold it."""
    context = _signed_in(chromium, plant, "LOOKER", "looker-password")
    try:
        node = _open_timeline(context, plant)
        try:
            _open_block(node, state="down", which=1)
            text = node.inner_text("#block-panel")
            assert "Naming this stop needs equipment.state" in text
            assert "can" in text
            assert node.query_selector("#stop-label") is None
        finally:
            _close(node)
    finally:
        context.close()


def test_naming_a_stop_from_the_panel_uses_the_plants_own_list_and_then_says_so(
        page):
    """The door to the labelling the product already had. One list, one write,
    and the panel comes back with the reason and the person on it - which is
    the whole point: the next reader does not have to ask who said so.

    This is the only test here that writes, and it names the first unnamed stop
    of the shift. The second one is left for the test above.
    """
    _open_block(page, state="down", which=0)
    assert "nobody has said why yet" in page.inner_text("#block-panel")
    page.wait_for_function(
        "() => document.querySelectorAll('#stop-reason-code option').length > 1",
        timeout=20000)
    options = page.evaluate(
        "() => [...document.querySelectorAll('#stop-reason-code option')]"
        ".map((o) => o.textContent)")
    assert "Changeover" in options
    assert any(option.startswith("Choose one of") for option in options)

    page.select_option("#stop-reason-code", "changeover")
    page.click("#stop-label button[type='submit']")
    # The write reloads the bar and reopens the block, so the wait is on the
    # panel saying the thing that was not there before the click.
    page.wait_for_function(
        """() => document.querySelector('#block-panel').textContent
                 .includes('named by ADMIN')""", timeout=30000)
    text = page.inner_text("#block-panel")
    assert "Changeover" in text
    assert "ADMIN" in text
    assert "named it Changeover" in text
    assert "the window this label was applied over overlaps the block" in text


# --------------------------------------------------------- and in all four themes


@pytest.mark.parametrize("theme", THEMES)
def test_the_panel_reads_in_all_four_themes(admin, plant, theme):
    """Style rule 3. A change is not done until it looks right in all four, and
    "looks right" begins with nothing on the panel resolving to a colour the
    palette does not define."""
    node = _open_timeline(admin, plant, theme=theme)
    try:
        _open_block(node, state="setup")
        _unfold(node, "tags")
        got = node.evaluate(
            """([theme]) => {
                 const panel = document.querySelector('#block-panel');
                 const probe = document.createElement('span');
                 probe.style.display = 'none';
                 document.body.appendChild(probe);
                 const palette = {};
                 for (const name of ['--text', '--muted', '--line', '--accent',
                                     '--panel', '--panel-2', '--bg', '--unknown',
                                     '--running', '--idle', '--down', '--setup',
                                     '--quality', '--disconnected', '--track',
                                     '--glow']) {
                   probe.style.color = `var(${name})`;
                   palette[name] = getComputedStyle(probe).color;
                 }
                 probe.remove();
                 const marks = [...panel.querySelectorAll('svg.fs-chart line, '
                   + 'svg.fs-chart text, svg.fs-chart polyline')];
                 return {
                   theme: document.documentElement.getAttribute('data-theme'),
                   ground: getComputedStyle(panel).backgroundColor,
                   text: getComputedStyle(panel).color,
                   palette: Object.values(palette),
                   paints: marks.map((el) => {
                     const cs = getComputedStyle(el);
                     return cs.stroke === 'none' ? cs.fill : cs.stroke;
                   }),
                   charts: panel.querySelectorAll('svg.fs-chart').length,
                 };
               }""", [theme])
        assert got["theme"] == theme
        assert got["charts"] >= 1
        assert got["ground"] in got["palette"], (
            f"the panel's ground is not a palette colour under {theme}: {got['ground']}")
        assert got["text"] in got["palette"]
        strays = sorted({paint for paint in got["paints"]
                         if paint not in got["palette"]
                         and paint not in ("none", "rgba(0, 0, 0, 0)")
                         and not paint.startswith("url(")})
        assert not strays, f"colours the {theme} palette does not define: {strays}"
    finally:
        _close(node)


# ------------------------------------------------------------- and an operator


def test_an_operator_opens_a_block_the_way_a_supervisor_does(chromium, plant):
    """It is a read of the plant's own records, and `plant.read` is the gate on
    every read in this product. A panel the shop floor cannot open is a panel
    Scott's operators do not have."""
    context = _signed_in(chromium, plant, "SCOTT", "operator")
    try:
        node = _open_timeline(context, plant)
        try:
            _open_block(node, state="down", which=2)
            text = node.inner_text("#block-panel")
            assert "MO-CHILL" in text
            assert "This is the job, not a breakdown" in text
        finally:
            _close(node)
    finally:
        context.close()


