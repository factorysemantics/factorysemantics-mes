"""Two tags on one chart, a window that has finished, and a click on a reading.

What Scott asked for on 2026-10-11 - *"I agree. Clicks first. Run all."* - as a
person meets it: the Trend tab of one machine, two named lines on two scales, a
window with a start and an end, and a panel beside the chart that says what else
was happening at the second somebody pressed. The reads are pinned in
`test_two_tags_on_one_axis_and_the_records_behind_a_point`; the chain's own walk
is pinned in `test_the_trend_graph_walks_link_three_of_the_chain`. This is the
screen, in Chromium, in all four themes.

**THE PANEL'S FETCH IS HELD BACK BY OVER A SECOND.** A check on loopback proves
the code path and not the experience: this page draws in single-digit
milliseconds here and in rather more on Scott's phone over the tailnet. A route
intercept delays the point read, and every assertion waits on the panel's own
`data-point` attribute - which the page writes LAST, after the blocks are on the
screen - rather than on an element that exists before the answer does. That is
the lesson of #112 and #113 (2026-09-26), where `wait_for_selector` then a read
gave "" on GitHub's runner and the right answer on loopback.

Two tags in **different units** is the thing no existing fixture could produce,
because units are config and the demo plant ships no tag manifest. So this one
writes its own: a tag map and a `tags.json` into a temporary directory, pointed
at by `MES_TAG_MAP_FILE` and `MES_REPLAY_DIR`. Degrees on the left, bar on the
right, both named in the legend - config, not code, at the plant boundary.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for, and a Playwright file marked only `slow` is a test nothing runs.
"""

import json
import socket
import threading
from datetime import UTC, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

THEMES = ("control-room", "daylight", "high-contrast", "night-shift")

#: How long the point read is held back for. Comfortably longer than the page
#: takes to draw on loopback, so a passing assertion means the panel waited for
#: its answer rather than that the answer was already there.
HELD_BACK_MS = 1200

#: The cadence the fixture's tags arrive at. Five seconds is what this product's
#: shipped configuration stores analogs at.
SAMPLE_SECONDS = 5

#: The window the page is opened on: ninety minutes that finished ninety minutes
#: ago. Not a trailing span - the whole point of this screen is a window that is
#: over, which is the one thing the chips could not ask for.
SPAN_MINUTES = 90
ENDED_MINUTES_AGO = 90

#: Minutes into the window. The machine stops unlabelled at 30, changes over at
#: 40, and is running again from 50; a reading clicked in the forties is
#: therefore a reading taken during a changeover somebody named.
STOP_AT = 30
SETUP_AT = 40
RUNNING_AT = 50


def _epoch_ms(moment) -> int:
    return int(moment.replace(tzinfo=UTC).timestamp() * 1000)


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A mixer with two tags in two units over a window that has finished.

    Everything a block of the panel is about is declared here: a temperature in
    degrees stepping up halfway through, a pressure in bar on the other scale, a
    changeover somebody named and a stop nobody did, the order that was open, a
    brix reading out of specification taken inside the window, and a chiller job
    assigned and never started - the shape the chain's own key gives link 2.

    Written as rows rather than through the services wherever the record has to
    be in the past: every service that writes a state or a tag writes it *now*,
    and a window that has finished cannot be made out of intervals that start at
    this second.
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
        ShiftPattern,
        TagValue,
    )
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth, quality, workorders

    room = tmp_path_factory.mktemp("trendpoint")
    path = room / "plant.db"
    url = f"sqlite:///{path}"

    # The tag manifest and the tag map, as a plant that has written its units
    # down has them. The unit on a tag is the only reason this chart has two
    # scales rather than one, and it comes from here - not from the code.
    replay = room / "replay"
    replay.mkdir()
    (replay / "tags.json").write_text(json.dumps({"tables": {"MIX01": {"tags": {
        "Temperature": {"kind": "pv", "unit": "degC", "min": 55.0, "max": 70.0,
                        "writable": False},
        "Pressure": {"kind": "pv", "unit": "bar", "writable": False},
    }}}}), encoding="utf-8")
    tag_map = room / "tag_map.json"
    tag_map.write_text(json.dumps({"machines": [
        {"equipment": "MIX01", "object": "MIX01", "cycle_seconds": 4.0},
    ]}), encoding="utf-8")

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        env.setenv("MES_PLANT_TIMEZONE", "UTC")
        env.setenv("MES_REPLAY_DIR", str(replay))
        env.setenv("MES_TAG_MAP_FILE", str(tag_map))
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_sessionmaker.cache_clear()

        engine = make_engine(url)
        Base.metadata.create_all(engine)
        end = (utcnow() - timedelta(minutes=ENDED_MINUTES_AGO)).replace(
            microsecond=0, second=0)
        start = end - timedelta(minutes=SPAN_MINUTES)
        with Session(engine, expire_on_commit=False) as session:
            seed_demo_plant(session)
            auth.ensure_builtin_roles(session)

            # Three shifts covering the whole clock, every day of the week, so
            # that "this shift" and "last shift" name a shift whatever hour a CI
            # runner starts at. A plant with no patterns has no current shift,
            # and the control would then be offering a window that errors.
            session.add_all([
                ShiftPattern(code="DAY", name="Day", starts=time(6, 0),
                             ends=time(14, 0), days="1111111"),
                ShiftPattern(code="EVE", name="Evening", starts=time(14, 0),
                             ends=time(22, 0), days="1111111"),
                ShiftPattern(code="NIGHT", name="Night", starts=time(22, 0),
                             ends=time(6, 0), days="1111111"),
            ])

            mixer = session.scalar(select(Equipment).where(Equipment.code == "MIX01"))

            # The two lines. Temperature across the whole window, stepping up two
            # degrees at the halfway mark; pressure in bar, on the other scale.
            at = start
            half = start + timedelta(minutes=SPAN_MINUTES // 2)
            while at <= end:
                session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Temperature",
                                     ts=at, value_num=62.0 if at < half else 64.0))
                session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Pressure",
                                     ts=at, value_num=2.6))
                at += timedelta(seconds=SAMPLE_SECONDS)

            session.add_all([
                # Watched since long before the window, so the ninety minutes the
                # panel asks about are ninety it can answer for.
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=start - timedelta(hours=2),
                               ended_at=start + timedelta(minutes=STOP_AT)),
                # A stop nobody named - house rule 3 - then a changeover somebody
                # did, then running again.
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.DOWN,
                               started_at=start + timedelta(minutes=STOP_AT),
                               ended_at=start + timedelta(minutes=SETUP_AT)),
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.SETUP,
                               reason="Product change", reason_code="changeover",
                               started_at=start + timedelta(minutes=SETUP_AT),
                               ended_at=start + timedelta(minutes=RUNNING_AT)),
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=start + timedelta(minutes=RUNNING_AT)),
                # Link 2's shape: raised before the window, given to somebody, and
                # never started because it needs the line stopped.
                MaintenanceOrder(
                    code="MO-CHILLER", equipment_id=mixer.id,
                    kind=MaintenanceKind.PREVENTIVE,
                    status=MaintenanceStatus.ASSIGNED,
                    summary="Chiller condenser clean",
                    reason="Needs the line stopped; no window this shift",
                    raised_at=start - timedelta(hours=3), started_at=None),
            ])

            # The job that was open. Started through the service, so the order and
            # its operation are in the states the service puts them in, then moved
            # back into the window - which is where it has to have been running.
            workorders.create(session, code="WO-TREND", material_code="FG-COLA",
                              quantity=120)
            workorders.release(session, "WO-TREND")
            operation = workorders.start_operation(session, "WO-TREND", 10)
            operation.started_at = start - timedelta(minutes=10)
            order = workorders.get(session, "WO-TREND")
            order.started_at = start - timedelta(minutes=10)

            # And what the lab measured while it ran: one brix reading above the
            # specification, taken inside the window.
            check, _nc, _signals = quality.record_check(
                session, material_code="FG-COLA", characteristic="brix",
                value=12.4, equipment_code="MIX01", actor="OP-NIGHT")
            check.ts = start + timedelta(minutes=SETUP_AT + 2)
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
            yield base, start, end
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()


def _wait_until_answering(base: str, seconds: float = 20.0) -> None:
    import time as clock
    import urllib.error
    import urllib.request

    deadline = clock.monotonic() + seconds
    while clock.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=1) as reply:
                if reply.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            clock.sleep(0.1)
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
    context = chromium.new_context(viewport={"width": 1500, "height": 1100})
    reply = context.request.post(
        f"{base}/auth/login",
        data=json.dumps({"code": code, "password": password}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in as {code} failed: {reply.status}"
    return context


def _open_trend(context, base, start, end, theme="control-room",
                remembered_folds=False):
    """The Trend tab of MIX01, two tags, the window that has finished.

    The window is in the URL because that is how a link to "what happened at
    6:20" travels - `FS.span` reads it back out - and because a test that
    clicked its way to a window would be asserting the control twice.

    The folds a reader opened are remembered in `localStorage` and the context is
    shared by every test in this file, so one test's click would otherwise decide
    what the next test finds open.
    """
    page = context.new_page()
    page.add_init_script(
        f"try {{ localStorage.setItem('fsmes-theme', {theme!r}); }} catch (e) {{}}")
    if not remembered_folds:
        page.add_init_script(
            """try {
                 for (const key of Object.keys(localStorage)) {
                   if (key.startsWith('fsmes-trend-fold-')) localStorage.removeItem(key);
                 }
               } catch (e) {}""")

    def held(route):
        page.wait_for_timeout(HELD_BACK_MS)
        route.continue_()

    page.route("**/trend/*/point*", held)
    asked = (f"tag=Temperature&tag2=Pressure"
             f"&start={start.isoformat()}Z&end={end.isoformat()}Z")
    page.goto(f"{base}/dashboard/machine/MIX01?{asked}#trend",
              wait_until="load", timeout=30000)
    # The chart is drawn after the window and the trend read settle. Wait for
    # both lines' own dots, not for the chart node.
    page.wait_for_function(
        """() => document.querySelectorAll(
             '#trend circle[data-series="Temperature"]').length > 5
           && document.querySelectorAll(
             '#trend circle[data-series="Pressure"]').length > 5""",
        timeout=30000)
    return page


@pytest.fixture(scope="module")
def admin(chromium, plant):
    base, _start, _end = plant
    context = _signed_in(chromium, base, "ADMIN", "admin")
    yield context
    context.close()


def _close(page):
    """Take the delay off before closing, or a held fetch still in flight raises
    into a route callback whose page has gone away."""
    page.unroute_all(behavior="ignoreErrors")
    page.close()


@pytest.fixture()
def page(admin, plant):
    base, start, end = plant
    node = _open_trend(admin, base, start, end)
    yield node
    _close(node)


def _await_panel(page, at):
    """Wait on the panel's own attribute, which the page writes last.

    Not on `#trend-point-body` and not on a sleep. The box is on the page before
    the fetch is, and `data-point` means *this reading is drawn*.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    try:
        page.wait_for_function(
            "(at) => document.querySelector('#trend-point')"
            ".getAttribute('data-point') === String(at)",
            arg=at, timeout=30000)
    except PlaywrightTimeout as err:          # pragma: no cover - a red run only
        # What the panel was showing when it never opened. A timeout that only
        # says "the attribute never came" sends the next person back to the
        # browser to find out what this already knows.
        said = page.evaluate(
            """() => { const n = document.querySelector('#trend-point');
                       return n ? {state: n.dataset.state, point: n.dataset.point,
                                   text: n.innerText.slice(0, 400)} : null; }""")
        raise AssertionError(
            f"the panel never opened on {at!r}; it was showing {said!r}") from err


#: Pick the dot of one series whose bucket falls inside a stretch of the clock,
#: by the instant it carries rather than by its position in the list: a chart
#: that drew the wrong window would still have a dot at the same index.
_DOT_IN = """
([series, lo, hi]) => {
  const ms = (s) => Date.parse(
    s.endsWith('Z') || /[+-]\\d\\d:?\\d\\d$/.test(s) ? s : s + 'Z');
  const dots = [...document.querySelectorAll(
    `#trend circle[data-series="${series}"]`)];
  const inside = dots.filter((d) => {
    const at = ms(d.getAttribute('data-t'));
    return at >= lo && at <= hi;
  });
  const dot = inside[Math.floor(inside.length / 2)];
  return dot ? dot.getAttribute('data-t') : null;
}
"""


def _reading_during_the_changeover(page, plant, series="Temperature"):
    """The instant of a dot taken while the machine was changing over."""
    _base, start, _end = plant
    at = page.evaluate(_DOT_IN, [
        series,
        _epoch_ms(start + timedelta(minutes=SETUP_AT)),
        _epoch_ms(start + timedelta(minutes=RUNNING_AT)),
    ])
    assert at, (f"no {series} dot was drawn between minute {SETUP_AT} and "
                f"{RUNNING_AT} of the window - the chart is not on the window "
                f"that was asked for")
    return at


def _click(page, series, at):
    page.click(f'#trend circle[data-series="{series}"][data-t="{at}"]')
    _await_panel(page, at)


def _unfold(page, key):
    """Open one of the blocks the panel opens folded, and wait for its own
    `open` attribute - which the browser sets, not this page's script (#112)."""
    page.click(f'[data-fold="{key}"] > summary')
    page.wait_for_function(
        "(k) => document.querySelector(`[data-fold=\"${k}\"]`).open === true",
        arg=key, timeout=10000)


def _footer(page):
    """Every footer line of the trend chart, as one string.

    The footer is a set of SVG `<text>` rows rather than one element, so it is
    read the way `test_a_chart_draws_only_what_the_api_measured` reads it - and
    `inner_text` does not work on a node that is not an HTML element.
    """
    return " \u00b7 ".join(page.eval_on_selector_all(
        "#trend text[data-footer]", "els => els.map((e) => e.textContent)"))


def _stamps(page, *isos):
    """The instants as the page itself words them, in the plant's own zone.

    Computed in the browser rather than in Python: which clock a `datetime-local`
    box and a `toLocaleString` are on is the runner's business, and a test that
    hard-coded "09:00" would pass here and fail on a machine set to Lisbon.
    """
    return page.evaluate(
        """(isos) => isos.map((iso) => new Date(
             iso.endsWith('Z') ? iso : iso + 'Z'
           ).toLocaleString(undefined, { timeZone: 'UTC' }))""",
        [f"{iso}Z" if not str(iso).endswith("Z") else str(iso) for iso in isos])


# ------------------------------------------------------- two tags, two scales


def test_two_tags_are_drawn_as_two_named_lines_on_two_scales(page):
    """Definition of done 1, and the thing the chain needs.

    Degrees and bar on one scale would draw the pressure as a flat line along
    the bottom, which is a picture of the units and not of the plant. So each
    unit gets a side, the side says which unit it is, and the legend names both
    series in the colours the lines are drawn in.
    """
    legend = page.eval_on_selector_all(
        "#trend [data-legend]", "els => els.map((e) => e.textContent)")
    assert len(legend) == 2, f"two series, two switches in the legend: {legend}"
    assert any("Temperature" in said and "degC" in said for said in legend), legend
    assert any("Pressure" in said and "bar" in said for said in legend), legend

    footer = _footer(page)
    assert "left axis: degC" in footer, footer
    assert "right axis: bar" in footer, footer
    # Rule 5: the chart states its own total, series included.
    assert "2 series of 2" in footer, footer
    # Rule 4: with two units on the sides, the bucket width has to be said
    # somewhere, and the axis label is no longer free to carry it.
    assert "each point is one" in footer, footer

    colours = page.evaluate(
        """() => {
             const of = (s) => getComputedStyle(document.querySelector(
               `#trend polyline.trend-line.${s}`)).stroke;
             return [of('series-1'), of('series-2')];
           }""")
    assert colours[0] != colours[1], (
        f"both lines are drawn in {colours[0]} - a reader cannot tell them apart")


def test_the_chart_says_in_words_which_window_it_is_showing(page, plant):
    """Definition of done 2. A window that has finished is only honest if the
    chart says which window it is, because "the last eight hours" was the one
    sentence the chips could say and it is now the wrong one."""
    _base, start, end = plant
    said = page.inner_text("#trend-window")
    from_stamp, to_stamp = _stamps(page, start.isoformat(), end.isoformat())
    assert said.startswith("Showing "), said
    assert from_stamp in said and to_stamp in said, (said, from_stamp, to_stamp)
    assert "UTC time" in said, said
    assert "the last" not in said, (
        f"a window with two ends is not a trailing span: {said}")


def test_this_shift_and_last_shift_are_offered_and_named_in_words(page):
    """"The obvious ones offered for him: this shift, last shift." - and when one
    is chosen the chart says the shift's own name, from the plant's calendar,
    not a span of hours that happens to be the same length."""
    options = page.eval_on_selector_all(
        '#machine-span select[data-span="kind"] option',
        "els => els.map((e) => e.textContent)")
    assert "This shift" in options, options
    assert "Last shift" in options, options
    assert "A start and an end…" in options, options

    before = page.inner_text("#trend-window")
    page.select_option('#machine-span select[data-span="kind"]', "shift:previous")
    page.wait_for_function(
        "(was) => document.querySelector('#trend-window').textContent !== was",
        arg=before, timeout=30000)
    said = page.inner_text("#trend-window")
    assert "Showing " in said and "UTC time" in said, said
    # The window's own name, which only the plant's shift calendar knows.
    assert any(word in said for word in ("Day", "Evening", "Night")), said


def test_a_start_and_an_end_typed_in_redraws_the_chart_on_that_stretch(page, plant):
    """The control is two boxes and an Apply, and nothing is reloaded until
    Apply: a half-typed start is not a question."""
    _base, start, _end = plant
    narrow_start = start + timedelta(minutes=10)
    narrow_end = start + timedelta(minutes=70)
    page.select_option('#machine-span select[data-span="kind"]', "between")
    local = page.evaluate(
        """(isos) => isos.map((iso) => {
             const at = new Date(iso + 'Z');
             const pad = (n) => String(n).padStart(2, '0');
             return `${at.getFullYear()}-${pad(at.getMonth() + 1)}`
                  + `-${pad(at.getDate())}T${pad(at.getHours())}`
                  + `:${pad(at.getMinutes())}`;
           })""", [narrow_start.isoformat(), narrow_end.isoformat()])
    page.fill('#machine-span input[data-span="start"]', local[0])
    page.fill('#machine-span input[data-span="end"]', local[1])
    before = page.inner_text("#trend-window")
    page.click('#machine-span button[data-span="apply"]')
    page.wait_for_function(
        "(was) => document.querySelector('#trend-window').textContent !== was",
        arg=before, timeout=30000)
    said = page.inner_text("#trend-window")
    from_stamp, to_stamp = _stamps(page, narrow_start.isoformat(),
                                   narrow_end.isoformat())
    assert from_stamp in said and to_stamp in said, (said, from_stamp, to_stamp)


def test_a_window_with_its_ends_the_wrong_way_round_is_refused_in_words(page):
    """And refused by the control, before anything is asked of the plant: an
    end before its start is not a window somebody mistyped the length of."""
    page.select_option('#machine-span select[data-span="kind"]', "between")
    was = page.input_value('#machine-span input[data-span="start"]')
    page.fill('#machine-span input[data-span="end"]', was)
    page.click('#machine-span button[data-span="apply"]')
    page.wait_for_function(
        "() => document.querySelector('[data-span=\"says\"]')"
        ".textContent.includes('swap them')", timeout=10000)


# --------------------------------------------------- and the records behind one


def test_the_panel_says_nothing_is_open_until_a_reading_is_clicked(page):
    """The column is on the page from the start, so clicking a dot moves nothing
    else - and an empty column has to say what it is for rather than sitting
    there looking broken."""
    panel = page.query_selector("#trend-point")
    assert panel.get_attribute("data-state") == "idle"
    assert panel.get_attribute("data-point") == ""
    idle = page.inner_text("#trend-point-idle")
    assert "No reading is open" in idle
    assert "what the machine was doing and who said so" in idle
    assert "each block says how much of its window anybody was watching" in idle


def test_the_panel_is_empty_while_the_read_is_in_flight_and_says_it_is_opening(
        page, plant):
    """`data-point` means *this reading is drawn*. Leaving the last one on it
    while a new answer is on the wire would have a reader - and a test -
    believing the panel beside them is about the dot they just clicked."""
    at = _reading_during_the_changeover(page, plant)
    page.click(f'#trend circle[data-series="Temperature"][data-t="{at}"]')
    page.wait_for_function(
        "() => document.querySelector('#trend-point').getAttribute('data-state')"
        " === 'loading'", timeout=10000)
    assert page.query_selector("#trend-point").get_attribute("data-point") == ""
    assert "opening" in page.inner_text("#trend-point-which").lower()
    _await_panel(page, at)


def test_clicking_a_reading_opens_every_record_behind_that_second(page, plant):
    """Definition of done 3, block by block, on a reading taken during a
    changeover - so the state has a reason on it, the order was open, the lab
    had measured, and the chiller job was sitting there unstarted."""
    at = _reading_during_the_changeover(page, plant)
    _click(page, "Temperature", at)
    assert page.query_selector("#trend-point").get_attribute("data-state") == "open"
    text = page.inner_text("#trend-point")

    # Scott's words, not the schema's.
    for heading in ("The reading", "What the machine was doing",
                    "The job that was open", "Maintenance open on this machine",
                    "What the lab measured in this window",
                    "This machine's other tags at that instant"):
        assert heading in text, f"{heading!r} is not on the panel"

    # The reading and its time.
    assert "Temperature" in text
    assert "degC" in text
    assert "62" in text or "64" in text, text[:400]

    # What the machine was doing at that second, and who said why.
    assert "setup" in text
    assert "Product change" in text
    assert "recorded in this MES" in text
    assert "audit log" in text
    # The stop nobody labelled, counted rather than quietly dropped.
    assert "1 unlabelled" in text, text

    # The job, the maintenance order and its reason, the lab's verdict.
    assert "WO-TREND" in text
    assert "MO-CHILLER" in text
    assert "assigned, never started" in text
    assert "Needs the line stopped" in text
    assert "brix" in text
    assert "out of spec" in text


def test_the_order_the_maintenance_and_the_sample_link_to_the_pages_that_own_them(
        page, plant):
    """"Every row links through to the page that owns it." Three different
    screens, each opened already narrowed to the thing that was clicked - a link
    that lands on an unfiltered list is a link that asks the reader to search."""
    at = _reading_during_the_changeover(page, plant)
    _click(page, "Temperature", at)
    links = page.eval_on_selector_all(
        "#trend-point-body a", "els => els.map((e) => e.getAttribute('href'))")
    assert "/dashboard/orders?q=WO-TREND" in links, links
    assert "/dashboard/maintenance?equipment=MIX01#work" in links, links
    assert "/dashboard/spc?spec=FG-COLA%7Cbrix" in links, links
    # And the state timeline, which is the other half of this same page.
    assert "#timeline" in links, links


def test_every_block_says_how_much_of_its_window_anybody_was_watching(page, plant):
    """House rule 2, per block. A list of records has no rate to state and says
    so in those words; a share that could not be computed is a different fact
    from a share of nought."""
    at = _reading_during_the_changeover(page, plant)
    _click(page, "Temperature", at)
    said = page.eval_on_selector_all(
        "#trend-point .point-watched", "els => els.map((e) => e.textContent)")
    assert said, "no block stated its coverage"
    assert any("Watched" in line for line in said), said
    # A block that is a list of records says it has NO figure, in those words,
    # rather than printing a share of something it is not counting.
    assert any("no coverage figure to give" in line for line in said), said
    assert any("a list of the records in this window" in line for line in said), said
    # The last line of all is the window the whole panel is about.
    assert "UTC time" in said[-1], said[-1]


def test_a_reading_out_of_specification_opens_the_lab_block_without_being_asked(
        page, plant):
    """The blocks that are folded are the ones a reader usually does not need.
    A reading the chart judged out of specification is not one of those, so the
    lab's block opens itself; the machine's other tags stay folded until asked
    for, and say what is inside before they are opened."""
    at = _reading_during_the_changeover(page, plant)
    _click(page, "Temperature", at)
    folds = page.evaluate(
        """() => [...document.querySelectorAll('#trend-point [data-fold]')]
             .map((d) => [d.dataset.fold, d.open,
                          d.querySelector('.point-fold-says').textContent])""")
    assert dict((key, state) for key, state, _says in folds) == {
        "quality": True, "tags": False}, folds
    tags = next(says for key, _open, says in folds if key == "tags")
    assert "tag" in tags, tags
    _unfold(page, "tags")
    assert "Pressure" in page.inner_text('[data-fold="tags"]')


def test_the_reading_that_is_open_wears_a_ring_and_the_chart_does_not_move(
        page, plant):
    """"Click another dot, the panel follows. Nothing else moves." The dot keeps
    its own size and colour; the ring sits outside it, takes no pointer and no
    keyboard, and carries no value - a ring is not a reading."""
    before = page.evaluate(
        """() => ({
             chart: document.querySelector('#trend').getBoundingClientRect().width,
             footer: [...document.querySelectorAll('#trend text[data-footer]')]
                       .map((e) => e.textContent).join(' | '),
             window: document.querySelector('#trend-window').textContent,
           })""")
    at = _reading_during_the_changeover(page, plant)
    _click(page, "Temperature", at)
    rings = page.evaluate(
        """() => [...document.querySelectorAll('#trend circle.spc-ring')].map(
             (r) => ({ aria: r.getAttribute('aria-hidden'),
                       value: r.getAttribute('data-value'),
                       events: r.getAttribute('pointer-events') }))""")
    assert len(rings) == 1, f"one reading is open, so one ring: {rings}"
    assert rings[0] == {"aria": "true", "value": None, "events": "none"}
    after = page.evaluate(
        """() => ({
             chart: document.querySelector('#trend').getBoundingClientRect().width,
             footer: [...document.querySelectorAll('#trend text[data-footer]')]
                       .map((e) => e.textContent).join(' | '),
             window: document.querySelector('#trend-window').textContent,
           })""")
    assert after == before


def test_a_keyboard_reaches_a_reading_and_opens_it(page, plant):
    """There is no `<button>` inside an SVG, so the dot carries what one would
    carry and answers Enter - the same way the control chart's dots and the
    kit's own legend do."""
    at = _reading_during_the_changeover(page, plant)
    dot = page.query_selector(f'#trend circle[data-series="Temperature"][data-t="{at}"]')
    assert dot.get_attribute("role") == "button"
    assert dot.get_attribute("tabindex") == "0"
    label = dot.get_attribute("aria-label")
    assert "Temperature" in label and "degC" in label, label
    dot.focus()
    page.keyboard.press("Enter")
    _await_panel(page, at)


def test_changing_the_window_shuts_a_panel_that_is_no_longer_about_it(page, plant):
    """A panel open on a reading the new window does not draw is a panel about a
    different question. It shuts, back to the sentence that says what the column
    is for, rather than sitting there looking current."""
    at = _reading_during_the_changeover(page, plant)
    _click(page, "Temperature", at)
    page.select_option('#machine-span select[data-span="kind"]', "shift:previous")
    page.wait_for_function(
        "() => document.querySelector('#trend-point').getAttribute('data-point')"
        " === ''", timeout=30000)
    assert page.query_selector("#trend-point").get_attribute("data-state") == "idle"
    assert page.query_selector("#trend-point-body").is_hidden()
    assert "No reading is open" in page.inner_text("#trend-point-idle")


def test_the_second_tag_can_be_taken_off_and_the_chart_goes_back_to_one_scale(
        page, plant):
    """Two tags is the ask, not a workbench: the second picker leads with
    nothing, and choosing it leaves one line, one scale, and the unit back in
    the axis label where a single-series chart has always had it."""
    page.select_option("#trend-tag-2", "")
    page.wait_for_function(
        """() => document.querySelectorAll(
             '#trend circle[data-series="Pressure"]').length === 0""",
        timeout=30000)
    footer = _footer(page)
    assert "1 series of 1" in footer, footer
    assert "right axis" not in footer, footer
    assert "y: Temperature" in footer, footer


def test_the_panel_and_its_control_carry_the_anchors_a_guide_points_at(page):
    """Style rule 7. Renaming one of these is a deliberate act that re-authors
    whatever points at it, which is why they are asserted here rather than left
    to be noticed."""
    assert page.query_selector('[data-assist="trend-point-panel"]')


def test_an_operator_opens_a_reading_the_way_a_supervisor_does(chromium, plant):
    """It is a read of the plant's own records. `plant.read` is the gate on every
    read in this product, and this is not a special one - a panel the shop floor
    cannot open is a panel Scott's operators do not have."""
    base, start, end = plant
    context = _signed_in(chromium, base, "SCOTT", "operator")
    try:
        page = _open_trend(context, base, start, end)
        try:
            at = _reading_during_the_changeover(page, plant)
            _click(page, "Temperature", at)
            text = page.inner_text("#trend-point")
            assert "MO-CHILLER" in text
            assert "WO-TREND" in text
        finally:
            _close(page)
    finally:
        context.close()


def test_a_tag_a_link_named_is_drawn_even_where_the_plant_publishes_few_of_them(
        admin, plant):
    """Found on the lab and not in a test. The filler there publishes four tags
    of twelve through its tag map, and the screen was checking the tag a LINK
    named against that live snapshot: a reader who opened `?tag=ProductTemp`
    got FillWeight drawn instead, with nothing on the screen saying so. The
    pickers were always built from everything the machine has ever published;
    the tag the chart opens on is now too, and a named tag with no readings is
    reported by name instead of quietly swapped."""
    base, start, end = plant
    page = admin.new_page()

    def thin(route):
        """The snapshot cut down the way a tag map cuts it down: one tag,
        which is not the one the link asked for."""
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"source": "tag map", "tags": [
                          {"tag": "Temperature", "value": 41.0,
                           "primary": True, "kind": "pv"}]}))

    page.route("**/equipment/*/tags*", thin)
    try:
        page.goto(f"{base}/dashboard/machine/MIX01"
                  f"?tag=Pressure&start={start.isoformat()}Z"
                  f"&end={end.isoformat()}Z#trend",
                  wait_until="load", timeout=30000)
        page.wait_for_function(
            """() => document.querySelectorAll(
                 '#trend circle[data-series="Pressure"]').length > 5""",
            timeout=30000)
        assert page.input_value("#trend-tag") == "Pressure"
        assert "Pressure" in _footer(page)
    finally:
        page.unroute_all(behavior="ignoreErrors")
        page.close()


# ------------------------------------------------------------ in all four themes


@pytest.mark.parametrize("theme", THEMES)
def test_the_chart_and_its_panel_read_in_all_four_themes(admin, plant, theme):
    """Style rule 3. A change is not done until it looks right in all four, and
    "looks right" begins with nothing on the chart or the panel resolving to a
    colour the palette does not define - including the second line, which is the
    one colour this chart did not have before."""
    base, start, end = plant
    page = _open_trend(admin, base, start, end, theme=theme)
    try:
        at = _reading_during_the_changeover(page, plant)
        _click(page, "Temperature", at)
        got = page.evaluate(
            """() => {
                 const panel = document.querySelector('#trend-point');
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
                 const marks = [...document.querySelectorAll(
                   '#trend svg.fs-chart line, #trend svg.fs-chart text, '
                   + '#trend svg.fs-chart polyline, #trend svg.fs-chart circle')];
                 return {
                   theme: document.documentElement.getAttribute('data-theme'),
                   ground: getComputedStyle(panel).backgroundColor,
                   text: getComputedStyle(panel).color,
                   palette: Object.values(palette),
                   paints: marks.map((el) => {
                     const cs = getComputedStyle(el);
                     return cs.stroke === 'none' ? cs.fill : cs.stroke;
                   }),
                 };
               }""")
        assert got["theme"] == theme
        assert got["ground"] in got["palette"], (
            f"the panel's ground is not a palette colour under {theme}: "
            f"{got['ground']}")
        assert got["text"] in got["palette"]
        strays = sorted({paint for paint in got["paints"]
                         if paint not in got["palette"]
                         and paint not in ("none", "rgba(0, 0, 0, 0)")
                         and not paint.startswith("url(")})
        assert not strays, f"colours the {theme} palette does not define: {strays}"
    finally:
        _close(page)
