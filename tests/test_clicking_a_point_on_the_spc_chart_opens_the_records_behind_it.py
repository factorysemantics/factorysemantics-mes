"""Click a dot on the SPC chart in a browser, and read the panel beside it.

What Scott asked for on 2026-10-05, as a person meets it: the chart, a dot, a
click, and the records. The read is pinned in
`test_a_point_on_the_control_chart_carries_the_records_behind_it`; this is the
screen, in Chromium, in all four themes.

**THE DOSSIER FETCH IS HELD BACK BY OVER A SECOND.** A check on loopback proves
the code path and not the experience: this page renders in single-digit
milliseconds here and in rather more on Scott's phone over the tailnet. A route
intercept delays the dossier, and every assertion waits on the panel's own
`data-point` attribute - which the page writes LAST, after the blocks are on the
screen - rather than on an element that exists before the answer does. That is
the 2026-09-26 lesson from #112 and #113, where `wait_for_selector` then a read
gave "" on GitHub's runner and the right answer on loopback.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for, and a Playwright file marked only `slow` is a test nothing runs.

Since 2026-10-06 the chart has two halves, and the last section of this file is
the second one: the moving-range chart under the individuals chart, its dots,
its limits, and the reading one of them opens.
"""

import socket
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

THEMES = ("control-room", "daylight", "high-contrast", "night-shift")

#: How long the dossier is held back for. Comfortably longer than the page
#: takes to draw on loopback, so a passing assertion means the panel waited for
#: its answer rather than that the answer was already there.
HELD_BACK_MS = 1200

#: Readings that sit still, so the one wild value is the only thing a rule can
#: be firing on - and more than this plant's `[quality] spc_min_points`, so the
#: limits are not drawn from the fewest points that mean anything.
STEADY = [11.0, 10.9, 11.1, 11.0, 10.95, 11.05, 11.0, 10.9,
          11.1, 11.0, 10.98, 11.02, 10.96, 11.04]

#: The reading somebody clicks: beyond three sigma and out of specification.
WILD = 14.0

#: The cadence the fixture's tags arrive at. Five seconds is what this
#: product's shipped configuration stores analogs at.
SAMPLE_SECONDS = 5


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A demo plant with one out-of-control brix reading and its whole story.

    Two scales, so the panel's *did the process move or did the gauge?* table
    has two rows; a changeover that ended forty seconds before the reading; a
    stop nobody labelled; a temperature that reports across the window and a
    pressure that stops halfway through it; and a maintenance order that was
    open at the time. Each is a fact a test below names.
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
        TagValue,
    )
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth, gauges, quality

    path = tmp_path_factory.mktemp("spcpoint") / "plant.db"
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
            today = utcnow().date()
            for code, name, days in (("SCALE-A", "Bench scale A", 5),
                                     ("SCALE-B", "Bench scale B", 200)):
                gauges.register(session, code=code, name=name, kind="scale",
                                resolution=0.05, interval_days=90, location="MIX01",
                                actor="test")
                gauges.calibrate(session, code, result="pass", performed_by="QA-LEAD",
                                 performed_on=today - timedelta(days=days),
                                 certificate=f"CERT-{code}", actor="test")

            def check(value, gauge):
                row, _nc, _signals = quality.record_check(
                    session, material_code="FG-COLA", characteristic="brix",
                    value=value, gauge_code=gauge, equipment_code="MIX01",
                    actor="OP-NIGHT")
                session.flush()
                return row

            for value in STEADY:
                check(value, "SCALE-A")
            for value in STEADY[:4]:
                # Half a degree high, every time: the drifting-instrument story.
                check(value + 0.5, "SCALE-B")
            wild = check(WILD, "SCALE-A")
            at = wild.ts

            mixer = session.scalar(select(Equipment).where(Equipment.code == "MIX01"))
            packer = session.scalar(select(Equipment).where(Equipment.code == "PACK01"))
            session.add_all([
                # Watching this machine since long before the window, so the
                # twelve minutes the panel asks for are twelve it can answer for.
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.IDLE,
                               started_at=at - timedelta(hours=3),
                               ended_at=at - timedelta(minutes=9)),
                # A stop nobody named (house rule 3), then a changeover that
                # ended forty seconds before the reading, then running.
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.DOWN,
                               started_at=at - timedelta(minutes=9),
                               ended_at=at - timedelta(minutes=8)),
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.SETUP,
                               reason="Product change", reason_code="changeover",
                               started_at=at - timedelta(minutes=8),
                               ended_at=at - timedelta(seconds=40)),
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=at - timedelta(seconds=40)),
                # And the packer beside it, changing over inside the same
                # window: the story #143 and #147 both found, where the thing
                # that explains the reading happened on the next machine along.
                EquipmentState(equipment_id=packer.id, state=EquipmentStateName.IDLE,
                               started_at=at - timedelta(hours=3),
                               ended_at=at - timedelta(minutes=8)),
                EquipmentState(equipment_id=packer.id, state=EquipmentStateName.SETUP,
                               reason="Size change", reason_code="changeover",
                               started_at=at - timedelta(minutes=8),
                               ended_at=at - timedelta(minutes=3)),
                EquipmentState(equipment_id=packer.id, state=EquipmentStateName.RUNNING,
                               started_at=at - timedelta(minutes=3)),
                MaintenanceOrder(
                    code="MO-WINDOW", equipment_id=mixer.id,
                    kind=MaintenanceKind.CORRECTIVE, status=MaintenanceStatus.IN_PROGRESS,
                    summary="Agitator seal weeping", raised_at=at - timedelta(hours=2),
                    started_at=at - timedelta(hours=1)),
            ])
            moment = at - timedelta(minutes=12)
            while moment <= at + timedelta(minutes=3):
                session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Temperature",
                                     ts=moment, value_num=62.0))
                if moment <= at - timedelta(minutes=6):
                    # And then nothing: a stale value is not a steady one.
                    session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Pressure",
                                         ts=moment, value_num=2.6))
                moment += timedelta(seconds=SAMPLE_SECONDS)
            session.commit()
            wild_id = wild.id

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(
            create_app(), log_level="warning", lifespan="on"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
            _wait_until_answering(base)
            yield base, wild_id
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


def _open_spc(context, base, theme="control-room", remembered_folds=False):
    """The SPC page, with the dossier read held back by over a second.

    The folds a reader opened are remembered in `localStorage` and the context
    is shared by every test in this file, so one test's click would otherwise
    decide what the next test finds open. Each page starts from the panel's own
    defaults unless a test says it is asking about the remembering.
    """
    page = context.new_page()
    page.add_init_script(
        f"try {{ localStorage.setItem('fsmes-theme', {theme!r}); }} catch (e) {{}}")
    if not remembered_folds:
        page.add_init_script(
            """try {
                 for (const key of Object.keys(localStorage)) {
                   if (key.startsWith('fsmes-spc-fold-')) localStorage.removeItem(key);
                 }
               } catch (e) {}""")

    def held(route):
        page.wait_for_timeout(HELD_BACK_MS)
        route.continue_()

    page.route("**/point/*", held)
    page.goto(f"{base}/dashboard/spc?spec=FG-COLA%7Cbrix", wait_until="load",
              timeout=30000)
    # The chart is drawn after two fetches settle. Wait for the dots.
    page.wait_for_function(
        "() => document.querySelectorAll('#chart circle[data-check]').length > 10",
        timeout=30000)
    return page


@pytest.fixture(scope="module")
def admin(chromium, plant):
    base, _check = plant
    context = _signed_in(chromium, base, "ADMIN", "admin")
    yield context
    context.close()


def _close(page):
    """Take the delay off before closing, or a held fetch still in flight
    raises into a route callback whose page has gone away."""
    page.unroute_all(behavior="ignoreErrors")
    page.close()


@pytest.fixture()
def page(admin, plant):
    base, _check = plant
    node = _open_spc(admin, base)
    yield node
    _close(node)


def _click_the_wild_reading(page, check_id):
    """Click the dot that is the out-of-control reading, by its own id."""
    page.click(f"#chart circle[data-check='{check_id}']")
    _await_panel(page, check_id)


def _await_panel(page, check_id):
    """Wait on the panel's own attribute, which the page writes last.

    Not on `#point-body` and not on a sleep. The box is on the page before the
    fetch is, and `data-point` means *this reading is drawn*.
    """
    page.wait_for_function(
        "(id) => document.querySelector('#point-panel')"
        ".getAttribute('data-point') === String(id)",
        arg=check_id, timeout=30000)


def _unfold(page, key):
    """Open one of the blocks the panel opens folded, and wait for its text.

    Since 2026-10-07 the panel opens about one screen long: the readings, the
    rules and the line's own events are open and the gauge, this station's
    trends and the maintenance block are one click each. The wait is on the
    block's own `open` attribute rather than on a timeout, because a `<details>`
    is laid out by the browser and not by this page's script (#112).
    """
    page.click(f'[data-fold="{key}"] > summary')
    page.wait_for_function(
        "(k) => document.querySelector(`[data-fold=\"${k}\"]`).open === true",
        arg=key, timeout=10000)


# -------------------------------------------------------------- it is clickable


def test_the_panel_says_nothing_is_open_until_a_reading_is_clicked(page):
    """The column is on the page from the start, so clicking a dot moves
    nothing else - and an empty column has to say what it is for rather than
    sitting there looking broken."""
    panel = page.query_selector("#point-panel")
    assert panel.get_attribute("data-state") == "idle"
    assert panel.get_attribute("data-point") == ""
    idle = page.inner_text("#point-idle")
    assert "Click one on the chart" in idle
    assert "each block says how much of its window anybody was watching" in idle


def test_clicking_a_dot_fills_the_panel_with_that_readings_records(page, plant):
    _base, check = plant
    _click_the_wild_reading(page, check)
    assert page.query_selector("#point-panel").get_attribute("data-state") == "open"
    text = page.inner_text("#point-panel")
    assert "14" in text, text[:400]
    assert "out of specification" in text
    assert "OP-NIGHT" in text


def test_the_panel_is_empty_while_the_fetch_is_in_flight_and_says_it_is_opening(
        page, plant):
    """`data-point` means *this reading is drawn*. Leaving the last one on it
    while a new answer is on the wire would have a reader - and a test -
    believing the panel beside them is about the dot they just clicked."""
    _base, check = plant
    page.click(f"#chart circle[data-check='{check}']")
    page.wait_for_function(
        "() => document.querySelector('#point-panel').getAttribute('data-state')"
        " === 'loading'", timeout=10000)
    panel = page.query_selector("#point-panel")
    assert panel.get_attribute("data-point") == ""
    # The heading is upper-cased by the panel's own style, so read it the way
    # a person does rather than the way the markup spells it.
    assert "opening" in page.inner_text("#point-which").lower()
    _await_panel(page, check)


def test_clicking_another_reading_moves_the_panel_and_nothing_else(page, plant):
    """"Click another dot, the panel follows. Nothing else moves." - the ask,
    verbatim. The chart keeps its size and the facts row keeps its numbers."""
    _base, check = plant
    before = page.evaluate(
        """() => ({
             chart: document.querySelector('#chart').getBoundingClientRect().width,
             facts: document.querySelector('.object-facts').textContent,
             verdict: document.querySelector('#verdict').textContent,
           })""")
    _click_the_wild_reading(page, check)
    page.click(f"#chart circle[data-check='{check - 1}']")
    _await_panel(page, check - 1)
    after = page.evaluate(
        """() => ({
             chart: document.querySelector('#chart').getBoundingClientRect().width,
             facts: document.querySelector('.object-facts').textContent,
             verdict: document.querySelector('#verdict').textContent,
           })""")
    assert after == before
    assert "in specification" in page.inner_text("#point-panel")


def test_a_keyboard_reaches_a_reading_and_opens_it(page, plant):
    """There is no `<button>` inside an SVG, so the dot carries what one would
    carry and answers Enter - the same way the kit's own legend and threshold
    controls do (style rule 6's intent)."""
    _base, check = plant
    dot = page.query_selector(f"#chart circle[data-check='{check}']")
    assert dot.get_attribute("role") == "button"
    assert dot.get_attribute("tabindex") == "0"
    assert "open the records behind it" in dot.get_attribute("aria-label")
    dot.focus()
    page.keyboard.press("Enter")
    _await_panel(page, check)


def test_the_open_a_reading_button_opens_the_one_a_rule_fired_on(page, plant):
    """A keyboard's way in without hunting for a dot, and it says which
    reading it will open rather than promising a flagged one that is not
    there."""
    _base, check = plant
    button = page.query_selector("#open-point")
    assert button.get_attribute("data-assist") == "spc-open-point"
    assert button.inner_text().strip() == "Open the flagged reading"
    button.click()
    _await_panel(page, check)


def test_the_reading_that_is_open_is_marked_on_the_chart(page, plant):
    """Ringed rather than recoloured: the dot's colour already says whether a
    rule fired on it, and overwriting that to show selection would hide the
    more important fact."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    page.wait_for_function(
        "(id) => { const d = document.querySelector(`#chart circle[data-check='${id}']`);"
        " return d && d.getAttribute('class').includes('spc-selected'); }",
        arg=check, timeout=10000)
    assert page.eval_on_selector_all(
        "#chart circle.spc-selected", "els => els.length") == 1


# ------------------------------------------------------------ what is in it


def test_the_gauge_and_its_last_calibration_are_on_the_panel(page, plant):
    """Scott's own question: *what source did it come from, and what is that
    source's calibration?*"""
    _base, check = plant
    _click_the_wild_reading(page, check)
    _unfold(page, "gauge")
    text = page.inner_text("#point-panel")
    assert "SCALE-A" in text
    assert "QA-LEAD" in text
    assert "adequate" in text, "the resolution verdict is not on the panel"


def test_the_other_gauge_in_the_same_hour_is_on_the_panel_with_the_difference(
        page, plant):
    """The table that separates *the process moved* from *the gauge moved*."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    _unfold(page, "gauge")
    text = page.inner_text("#point-panel")
    assert "SCALE-B" in text
    assert "this reading's gauge" in text
    assert "different pieces" in text, (
        "the panel presents the gauge comparison as proof rather than as a bound")


def test_what_the_machine_was_doing_and_what_it_had_just_come_out_of(page, plant):
    """A point above the limit forty seconds after a changeover ended is a
    point about the changeover."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    text = page.inner_text("#point-panel")
    assert "running" in text
    assert "Product change" in text
    assert "before the reading" in text


def test_an_unlabelled_stop_in_the_window_reads_as_unlabelled(page, plant):
    """House rule 3 on the screen: never filed under a reason nothing
    observed."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    assert "unlabelled" in page.inner_text("#point-panel")


def test_the_maintenance_order_and_the_finding_in_the_window_are_listed(page, plant):
    _base, check = plant
    _click_the_wild_reading(page, check)
    _unfold(page, "else")
    text = page.inner_text("#point-panel")
    assert "MO-WINDOW" in text
    assert "Agitator seal weeping" in text
    assert "never a station" in text, "the findings list does not say its scope"


def test_every_block_on_the_panel_says_what_it_knows_about_its_own_window(page, plant):
    """Rule 2 of the chart contract, as a panel: a figure, "unknown", and
    "this is a list of records" are three facts and the reader is owed which."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    said = page.eval_on_selector_all(
        "#point-panel .point-watched", "els => els.map(e => e.textContent)")
    assert len(said) >= 4, said
    assert any("Watched" in line for line in said)
    assert any("no coverage figure" in line.lower() for line in said)


# ------------------------------------------------------------- the small charts


def test_the_stations_analogs_are_drawn_through_the_kit_with_the_reading_marked(
        page, plant):
    """Not a chart engine of its own (style rule 13), and the marker is the
    whole reason the picture answers the question: a pressure that stops and a
    fill weight are an explanation only if they are read against each other."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    got = page.evaluate(
        """() => {
             const charts = [...document.querySelectorAll('#point-panel svg.fs-chart')];
             return charts.map((c) => ({
               kind: c.getAttribute('data-kind'),
               total: c.getAttribute('data-total'),
               coverage: c.getAttribute('data-coverage'),
               markers: c.querySelectorAll('[data-marker]').length,
               title: c.querySelector('title').textContent,
             }));
           }""")
    assert [c["kind"] for c in got] == ["states", "line", "line"], got
    for chart in got:
        assert chart["total"], f"a chart with no total: {chart}"
        assert chart["coverage"] not in (None, ""), chart
        assert chart["markers"] == 1, f"the reading is not marked on {chart}"


def test_a_tag_that_stopped_arriving_breaks_its_line_rather_than_being_drawn_through(
        page, plant):
    """A stale value is not a steady one. The pressure stops halfway through
    the window, and a line drawn through that silence would be a measurement
    of six minutes nobody sampled."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    got = page.evaluate(
        """() => [...document.querySelectorAll('#point-panel .point-chart')]
             .filter((b) => /Pressure/.test(b.querySelector('h4').textContent))
             .map((b) => {
               const c = b.querySelector('svg.fs-chart');
               return {
                 unknown: c.querySelectorAll('[data-unknown="true"]').length,
                 carries: c.getAttribute('data-carries-unknown'),
                 total: c.getAttribute('data-total'),
               };
             })""")
    assert len(got) == 1, got
    assert got[0]["unknown"] > 0, got
    assert "with no reading" in got[0]["total"], got


def test_each_small_chart_can_be_taken_off_the_page(page, plant):
    """Style rule 15: a chart is presentation-ready when its footer survives
    the paste, and `FS.kit.export` is the one implementation of that."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    buttons = page.eval_on_selector_all(
        "#point-panel .chart-tools button",
        "els => els.map(e => [e.textContent.trim(), e.getAttribute('aria-label')])")
    assert len(buttons) == 6, buttons          # svg and png for each of three
    assert {name for name, _label in buttons} == {"SVG", "PNG"}
    assert all(label for _name, label in buttons)
    # And the export really carries the footer, read back through the kit.
    markup = page.evaluate(
        """async () => {
             const node = document.querySelector('#point-panel svg.fs-chart[data-kind="line"]');
             const blob = await FS.kit.export(node, 'svg');
             return await blob.text();
           }""")
    assert "data-total" in markup
    assert "chart-marker" in markup, "the reading's marker left the export"


# ----------------------------------------------------------------- four themes


@pytest.mark.parametrize("theme", THEMES)
def test_the_panel_reads_in_all_four_themes(admin, plant, theme):
    """Style rule 3. A change is not done until it looks right in all four, and
    "looks right" begins with nothing on the panel resolving to a colour the
    palette does not define."""
    base, check = plant
    page = _open_spc(admin, base, theme=theme)
    try:
        _click_the_wild_reading(page, check)
        got = page.evaluate(
            """([theme]) => {
                 const panel = document.querySelector('#point-panel');
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
        assert got["charts"] == 3
        assert got["ground"] in got["palette"], (
            f"the panel's ground is not a palette colour under {theme}: {got['ground']}")
        assert got["text"] in got["palette"]
        strays = sorted({paint for paint in got["paints"]
                         if paint not in got["palette"]
                         and paint not in ("none", "rgba(0, 0, 0, 0)")
                         and not paint.startswith("url(")})
        assert not strays, f"colours the {theme} palette does not define: {strays}"
    finally:
        _close(page)


# -------------------------------------------------------------- and an operator


def test_an_operator_opens_a_reading_the_way_a_supervisor_does(chromium, plant):
    """It is a read of the plant's own records. `plant.read` is the gate on
    every read in this product, and this is not a special one - a panel the
    shop floor cannot open is a panel Scott's operators do not have."""
    base, check = plant
    context = _signed_in(chromium, base, "SCOTT", "operator")
    try:
        page = _open_spc(context, base)
        try:
            _click_the_wild_reading(page, check)
            _unfold(page, "gauge")
            _unfold(page, "else")
            text = page.inner_text("#point-panel")
            assert "SCALE-A" in text
            assert "MO-WINDOW" in text
        finally:
            _close(page)
    finally:
        context.close()


def test_the_panel_and_its_control_carry_the_anchors_a_guide_points_at(page):
    """Style rule 7. Renaming one of these is a deliberate act that re-authors
    whatever points at it, which is why they are asserted here rather than
    left to be noticed."""
    assert page.query_selector('[data-assist="spc-point-panel"]')
    assert page.query_selector('[data-assist="spc-open-point"]')


# ------------------------------------------- and the moving-range half, 2026-10-06

# The second chart, under the first.
#
# Scott, 2026-10-06: *"so the current simulation runs an I chart. I want an IMR
# chart like a real MES should have."* The gap between each reading and the one
# before it, with its own centre line and upper limit, drawn into the same node
# as the individuals half so the two line up and one export carries both. The
# dossier read is held back here too: these tests click a dot and wait on the
# panel's `data-point`, the same way the ones above do.


def _dots(page, series):
    """The dots of one half, in drawing order, as `{check, cx, cy}`."""
    return page.evaluate(
        """(series) => [...document.querySelectorAll(
             `#chart circle[data-series='${series}']`)]
           .map((c) => ({ check: Number(c.getAttribute('data-check')),
                          cx: Number(c.getAttribute('cx')),
                          cy: Number(c.getAttribute('cy')) }))""",
        series)


def test_the_moving_range_half_is_drawn_under_the_individuals_chart(page):
    """One shorter than the readings, below all of them, in the same `<svg>`.

    The same node because an IMR chart is a pair and is read as one: they share
    the x scale, and `FS.kit.export` takes the node rather than half of it.
    """
    individuals = _dots(page, "individuals")
    moving = _dots(page, "moving-range")
    assert len(moving) == len(individuals) - 1
    assert min(d["cy"] for d in moving) > max(d["cy"] for d in individuals)
    # One node, and the one the kit will export.
    assert page.eval_on_selector_all(
        "#chart svg.fs-chart", "els => els.length") == 1
    assert page.eval_on_selector_all(
        "#chart svg.fs-chart circle[data-series='moving-range']",
        "els => els.length") == len(moving)


def test_the_moving_range_half_has_its_own_centre_line_and_upper_limit(page):
    """Its own, drawn in the same two styles the individuals limits use, and
    below every individuals dot: the mean moving range and D4 times it. A
    second chart with no lines on it would be a picture, not a control chart.
    """
    below = page.evaluate(
        """() => {
             const half = document.querySelector("#chart g[data-half='moving-range']");
             const of = (cls) => half.querySelectorAll(`line.${cls}`).length;
             return { limit: of('limit-line'), centre: of('centre-line') };
           }""")
    assert below == {"limit": 1, "centre": 1}
    lower = page.evaluate(
        """() => [...document.querySelectorAll(
             "#chart g[data-half='moving-range'] text")]
           .map((t) => t.textContent).join(' | ')""")
    assert "UCL" in lower
    assert "R̄" in lower
    assert "Moving range" in lower
    # And the lines are where the dots are, not over the chart above them.
    assert page.evaluate(
        """() => {
             const half = document.querySelector("#chart g[data-half='moving-range']");
             const lines = [...half.querySelectorAll('line.limit-line, line.centre-line')];
             const dots = [...document.querySelectorAll(
               "#chart circle[data-series='individuals']")];
             const floor = Math.max(...dots.map((d) => Number(d.getAttribute('cy'))));
             return lines.every((l) => Number(l.getAttribute('y1')) > floor);
           }""") is True


def test_each_moving_range_dot_is_the_gap_that_ends_at_the_reading_it_opens(page):
    """The identity the whole half rests on: gap *n* is between readings *n*
    and *n + 1*, is drawn over the later of them, and opens that one. Pinned by
    reading id rather than by counting dots, because an off-by-one here puts a
    person in front of the wrong pair of readings."""
    individuals = _dots(page, "individuals")
    moving = _dots(page, "moving-range")
    assert [d["check"] for d in moving] == [d["check"] for d in individuals[1:]]
    # And drawn in the same column as that later reading, which is what makes
    # the pair readable as one chart.
    assert [round(d["cx"]) for d in moving] == [round(d["cx"]) for d in individuals[1:]]


def test_clicking_a_moving_range_dot_opens_the_panel_on_the_later_reading(page):
    """A dot on the lower chart is two readings, and the one it opens is the
    later. Deliberately not the wild reading's own gap: that one carries the
    same id as the individuals dot above it, so it would pass whichever of the
    two readings the page had decided to open."""
    moving = _dots(page, "moving-range")
    target = moving[-3]
    page.click(f"#chart circle[data-series='moving-range'][data-check='{target['check']}']")
    _await_panel(page, target["check"])
    assert page.query_selector("#point-panel").get_attribute("data-state") == "open"
    # And the panel says this reading's moving range, beside the limits it was
    # judged against.
    assert "Moving range" in page.inner_text("#point-panel")


def test_the_range_that_is_beyond_its_limit_is_the_one_marked(page, plant):
    """Identity again: the 2.5 jump into the wild reading is the only gap in
    this plant's history beyond D4 times the mean moving range, so it is the
    only dot on the lower chart drawn as a firing."""
    _base, wild = plant
    flagged = page.evaluate(
        """() => [...document.querySelectorAll(
             "#chart circle[data-series='moving-range'].spc-flag")]
           .map((c) => Number(c.getAttribute('data-check')))""")
    assert flagged == [wild]


def test_the_moving_range_firing_is_in_the_table_of_what_fired(page):
    """One table for the chart, not one per half: a reader asking *what fired*
    is asking about the chart. Rule 5 is the moving-range one, and this plant
    does not hold on it - so the row says so rather than leaving a firing that
    opened nothing to be noticed (decision 0036)."""
    rows = page.inner_text("#signals")
    assert "rule 5" in rows
    assert "does not hold on this rule" in rows
    assert "rule 5" in page.inner_text("#hold-rules").lower()
    # And the lower chart's own sentence, under it. Its own, because the two
    # halves answer two questions and one sentence for both is how a reader
    # comes to think a process that jumps is a process behaving.
    said = page.inner_text("#mr-verdict")
    assert "rule 5 fired on 1 of 18 ranges" in said
    assert "out of control" in said


def test_one_export_takes_both_halves_off_the_page(page):
    """`FS.kit.export` is given the node, and the node is the pair. Two buttons
    and one file: a reader who saved half an IMR chart into a slide would be
    showing the half that cannot be read on its own."""
    assert page.eval_on_selector_all("#chart-tools button", "els => els.length") == 2
    size = page.evaluate(
        """async () => {
             const node = document.querySelector('#chart svg.fs-chart');
             const blob = await window.FS.kit.export(node, 'svg');
             const text = await blob.text();
             return { bytes: blob.size,
                      moving: (text.match(/data-series="moving-range"/g) || []).length };
           }""")
    assert size["bytes"] > 0
    assert size["moving"] > 10


# ----------------------------------- the rest of the line, and one screen of it


def test_the_station_beside_this_one_is_on_the_panel_with_its_name_on_it(page, plant):
    """The ask of 2026-10-07: a reading says what *this* station was doing, and
    the packer changing over in the same twelve minutes was nowhere on it. Not
    "upstream" and not "downstream" - this MES does not record which way the
    bottles go - but *the rest of this line in the same window*, each station
    named."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    block = page.inner_text('[data-fold="line"]')
    assert "The rest of the line" in block
    assert "PACK01" in block
    assert "Packer 01" in block
    assert "changeover" in block
    assert "Size change" in block
    # Relative to the reading, worked out by the service and not by the page.
    assert "before this reading" in block
    # And the station whose reading this is does not appear as its own sibling.
    assert "MIX01" not in block


def test_a_row_about_another_station_links_to_that_stations_page(page, plant):
    """Reading *PACK01 changed over five minutes before this* and then hunting
    the machine list for PACK01 is the step the handoff asked to remove. An
    `<a href>`, not a div with a handler (style rule 6), so it opens in a new
    tab and a keyboard reaches it."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    href = page.get_attribute('[data-fold="line"] a.obj[href*="PACK01"]', "href")
    assert href == "/dashboard/machine/PACK01"
    assert page.eval_on_selector(
        '[data-fold="line"] a.obj[href*="PACK01"]',
        "a => a.tagName") == "A"


def test_the_line_block_opens_open_when_it_has_events_and_the_quiet_ones_folded(
        page, plant):
    """Which blocks a reader finds open is the whole of the fold: the line's
    own events are why this block exists, so they are not behind a click, and
    the three blocks that made the panel four thousand pixels long are."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    open_now = page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('[data-fold]')]
             .map((d) => [d.dataset.fold, d.open]))""")
    assert open_now == {"gauge": False, "line": True, "tags": False, "else": False}


def test_a_folded_block_says_what_is_inside_it_before_it_is_opened(page, plant):
    """A fold that says "Gauge" and nothing else makes a reader open all three
    to find out which one holds their answer, which is the long panel again
    with extra clicks. Every summary states its contents and their count
    (style rule 4)."""
    _base, check = plant
    _click_the_wild_reading(page, check)
    says = page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('[data-fold]')]
             .map((d) => [d.dataset.fold,
                          d.querySelector('summary').innerText]))""")
    assert "SCALE-A" in says["gauge"]
    assert "2 process values" in says["tags"]
    assert "maintenance order" in says["else"]
    assert "PACK01" in says["line"] or "1 of" in says["line"]


def test_a_fold_a_reader_opened_is_still_open_on_the_next_reading(admin, plant):
    """Remembered per block, so a reader who always wants the trends opens
    them once. In `localStorage`, in a try/catch: a browser with storage off
    gets the defaults and nothing throws."""
    base, check = plant
    page = _open_spc(admin, base, remembered_folds=True)
    try:
        page.evaluate("() => localStorage.removeItem('fsmes-spc-fold-tags')")
        _click_the_wild_reading(page, check)
        _unfold(page, "tags")
        # Another reading in the same panel: the choice is the reader's, not
        # the reading's.
        other = page.evaluate(
            """(id) => Number([...document.querySelectorAll(
                 "#chart circle[data-check][data-series='individuals']")]
               .map((c) => Number(c.getAttribute('data-check')))
               .find((c) => c !== id))""",
            check)
        page.click(f"#chart circle[data-check='{other}'][data-series='individuals']")
        _await_panel(page, other)
        assert page.evaluate(
            """() => document.querySelector('[data-fold="tags"]').open""") is True
        # And across a fresh page, which is what "remembered" means.
        again = _open_spc(admin, base, remembered_folds=True)
        try:
            _click_the_wild_reading(again, check)
            assert again.evaluate(
                """() => document.querySelector('[data-fold="tags"]').open""") is True
            assert again.evaluate(
                """() => document.querySelector('[data-fold="gauge"]').open""") is False
        finally:
            _close(again)
    finally:
        page.evaluate("() => localStorage.removeItem('fsmes-spc-fold-tags')")
        _close(page)


def test_the_panel_opens_about_half_as_long_with_three_trends_behind_a_click(
        page, plant):
    """The second half of the ask, as a number rather than a feeling.

    On 2026-10-07 a reading's panel ran to about 4,100 pixels on the lab plant
    and a reader scrolled past three process-value trends to reach the machine.
    On this fixture, on one developer's machine, it is 1,840 folded against
    3,437 with every block open - the reading (283), what the rules said (366),
    what the machine was doing (362), its timeline (234) and the rest of the
    line (391), with the gauge, the trends and the maintenance block one line
    each.

    The assertion is the **ratio** and not either number. A height in pixels is
    a font metric, and this panel measured 2,243 here and 2,436 on GitHub's
    runner for the same markup - the 2026-09-26 lesson from #112 wearing
    different clothes. What the fold claims is *about half as long*, and that
    survives a runner with wider glyphs.

    That it is still not one screen on a laptop this test does not hide. The
    five open blocks are the five the ask names.
    """
    _base, check = plant
    _click_the_wild_reading(page, check)
    folded = page.evaluate(
        "() => document.querySelector('#point-body').scrollHeight")
    where = """els => els.map((e) => {
                 const fold = e.closest('[data-fold]');
                 return fold ? `${fold.dataset.fold}:${fold.open}` : 'open page';
               })"""
    assert page.eval_on_selector_all("#point-body svg.fs-chart", where) == [
        "open page", "tags:false", "tags:false"]
    for key in ("gauge", "tags", "else"):
        _unfold(page, key)
    page.wait_for_function(
        "(was) => document.querySelector('#point-body').scrollHeight > was",
        arg=folded, timeout=10000)
    opened = page.evaluate(
        "() => document.querySelector('#point-body').scrollHeight")
    assert folded < 0.7 * opened, (
        f"folded the panel is {folded}px of the {opened}px it is with every "
        f"block open, which is not the saving the fold claims")
    assert page.eval_on_selector_all("#point-body svg.fs-chart", where) == [
        "open page", "tags:true", "tags:true"]
