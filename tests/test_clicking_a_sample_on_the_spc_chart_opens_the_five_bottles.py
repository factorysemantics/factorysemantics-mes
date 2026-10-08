"""Click a dot on an X-bar chart in a browser, and read the five bottles.

The companion to `test_clicking_a_point_on_the_spc_chart_opens_the_records_
behind_it`, for the other kind of chart. Scott, 2026-10-06: *"test if this type
of graphing and functionality can apply to a new type of SPC."* The read is
pinned in `test_a_sample_on_the_control_chart_carries_the_five_bottles_behind_
it`; this is the screen, in Chromium, in all four themes, as a person meets it.

**THE DOSSIER FETCH IS HELD BACK BY OVER A SECOND.** A check on loopback proves
the code path and not the experience: this page renders in single-digit
milliseconds here and in rather more on Scott's phone over the tailnet. A route
intercept delays the dossier, and every assertion waits on the panel's own
`data-sample` attribute - which the page writes LAST, after the blocks are on
the screen - rather than on an element that exists before the answer does. That
is the 2026-09-26 lesson from #112 and #113.

The plant underneath is the bottling floor's own shape: fill height measured
five bottles at a time, and a filler that came back from a changeover running
high and never came down. Whether that cause is findable in ONE CLICK is the
question this file answers, and it is answered by identity - the flagged sample
is sample 15 and no other, the bottle that pulled its average is the 147.0 mm
one - rather than by counting rows.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for, and a Playwright file marked only `slow` is a test nothing runs.
"""

import socket
import threading
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

THEMES = ("control-room", "daylight", "high-contrast", "night-shift")

#: How long the dossier is held back for. Comfortably longer than the page
#: takes to draw on loopback, so a passing assertion means the panel waited for
#: its answer rather than that the answer was already there.
HELD_BACK_MS = 1200

#: Fourteen settled samples of five, which is past this plant's
#: `[quality] spc_min_points` counted in samples. They alternate half a
#: millimetre either side of the centre rather than repeating one value,
#: because the shifted sample pulls `X̿` up: fourteen identical means would
#: then all sit below the centre line, a run rule would fire across half the
#: chart, and "exactly one dot is flagged" would stop being a sentence about
#: the shift. The range inside every one of them is exactly 2.0 mm, so the
#: lower chart's limits are flat.
STEADY_LOW = [140.5, 141.0, 141.5, 142.0, 142.5]
STEADY_HIGH = [141.5, 142.0, 142.5, 143.0, 143.5]
SAMPLES = 14

#: The sample somebody clicks: five bottles that all sit high after a
#: changeover, two of them outside the 139-145 mm specification, and one -
#: 147.0 - further from the sample's own mean than the rest. Mean 145.2 is
#: beyond `X̿ + A2·R̄`; range 3.0 is well inside `D4·R̄`.
SHIFTED = [144.0, 144.5, 145.0, 145.5, 147.0]
SHIFTED_MEAN, FURTHEST = 145.2, 147.0

#: The cadence the fixture's tags arrive at. Five seconds is what this
#: product's shipped configuration stores analogs at.
SAMPLE_SECONDS = 5


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A bottling-shaped plant whose newest sample is the one after a changeover.

    Everything the panel is meant to find is written down with its own stamp:
    two height gauges so the *did the process move or did the gauge?* table has
    two rows; a changeover that ended forty seconds before the sample; a stop
    nobody labelled; a nozzle pressure that stops partway through the window
    and a temperature that does not; and a maintenance order that was open at
    the time.

    The five readings' stamps are then spread over four minutes.
    `record_sample` stamps them together, because the floor posts a sample
    whole - but a plant feeding readings in one at a time through an
    integration will have them apart, and *the window is the stretch the five
    bottles span* is the claim the panel rests on.
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

    path = tmp_path_factory.mktemp("spcsample") / "plant.db"
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
            for code, name, days in (("HEIGHT-01", "Bench height gauge", 14),
                                     ("HEIGHT-02", "Line height gauge", 200)):
                gauges.register(session, code=code, name=name, kind="height gauge",
                                resolution=0.1, interval_days=180, location="MIX01",
                                actor="test")
                gauges.calibrate(session, code, result="pass", performed_by="QA-LEAD",
                                 performed_on=today - timedelta(days=days),
                                 certificate=f"CERT-{code}", actor="test")
            quality.create_spec(session, material_code="FG-COLA",
                                characteristic="fill_height", unit="mm",
                                min_value=139.0, max_value=145.0, sample_size=5,
                                actor="test")

            def sample(values, gauge="HEIGHT-01"):
                row, checks, _nc, _signals = quality.record_sample(
                    session, material_code="FG-COLA", characteristic="fill_height",
                    values=values, gauge_code=gauge, equipment_code="MIX01",
                    actor="OP-NIGHT")
                session.flush()
                return row, checks

            for turn in range(SAMPLES):
                sample(STEADY_LOW if turn % 2 else STEADY_HIGH)
            # One sample on the other gauge, reading half a millimetre high:
            # the drifting-instrument story, which is what the neighbours
            # table is for.
            sample([v + 0.5 for v in STEADY_HIGH], gauge="HEIGHT-02")
            shifted, checks = sample(SHIFTED)
            at = shifted.ts
            for offset, check in zip(range(4, -1, -1), checks, strict=True):
                check.ts = at - timedelta(minutes=offset)

            mixer = session.scalar(select(Equipment).where(Equipment.code == "MIX01"))
            packer = session.scalar(select(Equipment).where(Equipment.code == "PACK01"))
            session.add_all([
                # Watching this machine since long before the window, so the
                # minutes the panel asks for are minutes it can answer for.
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.IDLE,
                               started_at=at - timedelta(hours=3),
                               ended_at=at - timedelta(minutes=13)),
                # A stop nobody named (house rule 3), then the changeover that
                # ended forty seconds before the sample, then running.
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.DOWN,
                               started_at=at - timedelta(minutes=13),
                               ended_at=at - timedelta(minutes=12)),
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.SETUP,
                               reason="Product change", reason_code="changeover",
                               started_at=at - timedelta(minutes=12),
                               ended_at=at - timedelta(seconds=40)),
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=at - timedelta(seconds=40)),
                # And the station beside it, which changed size six minutes
                # before these five bottles were taken and which neither panel
                # could name until 2026-10-07.
                EquipmentState(equipment_id=packer.id, state=EquipmentStateName.IDLE,
                               started_at=at - timedelta(hours=3),
                               ended_at=at - timedelta(minutes=11)),
                EquipmentState(equipment_id=packer.id, state=EquipmentStateName.SETUP,
                               reason="Size change", reason_code="changeover",
                               started_at=at - timedelta(minutes=11),
                               ended_at=at - timedelta(minutes=6)),
                EquipmentState(equipment_id=packer.id, state=EquipmentStateName.RUNNING,
                               started_at=at - timedelta(minutes=6)),
                MaintenanceOrder(
                    code="MO-FILLER", equipment_id=mixer.id,
                    kind=MaintenanceKind.CORRECTIVE, status=MaintenanceStatus.IN_PROGRESS,
                    summary="Filler nozzle seal weeping", raised_at=at - timedelta(hours=2),
                    started_at=at - timedelta(hours=1)),
            ])
            moment = at - timedelta(minutes=20)
            while moment <= at + timedelta(minutes=4):
                session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Temperature",
                                     ts=moment, value_num=62.0))
                if moment <= at - timedelta(minutes=8):
                    # And then nothing: a stale value is not a steady one.
                    session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Pressure",
                                         ts=moment, value_num=2.6))
                moment += timedelta(seconds=SAMPLE_SECONDS)
            session.commit()
            shifted_id = shifted.id

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(
            create_app(), log_level="warning", lifespan="on"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
            _wait_until_answering(base)
            yield base, shifted_id
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


#: How many samples the chart draws: the settled ones, the one taken on the
#: other gauge, and the shifted one.
DRAWN = SAMPLES + 2


def _open_spc(context, base, theme="control-room", remembered_folds=False):
    """The SPC page on fill height, with the dossier read held back.

    The folds a reader opened are remembered in `localStorage` and the context
    is shared by every test in this file, so each page starts from the panel's
    own defaults unless a test says it is asking about the remembering.
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

    page.route("**/sample/*", held)
    page.goto(f"{base}/dashboard/spc?spec=FG-COLA%7Cfill_height", wait_until="load",
              timeout=30000)
    # The chart is drawn after two fetches settle. Wait for the dots of the
    # lower half, which are the last of them to be appended.
    page.wait_for_function(
        "n => document.querySelectorAll("
        " `#chart circle[data-series='sample-range']`).length === n",
        arg=DRAWN, timeout=30000)
    return page


@pytest.fixture(scope="module")
def admin(chromium, plant):
    base, _sample = plant
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
    base, _sample = plant
    node = _open_spc(admin, base)
    yield node
    _close(node)


def _await_panel(page, sample_id):
    """Wait on the panel's own attribute, which the page writes last.

    Not on `#point-body` and not on a sleep. The box is on the page before the
    fetch is, and `data-sample` means *this sample is drawn*.
    """
    page.wait_for_function(
        "(id) => document.querySelector('#point-panel')"
        ".getAttribute('data-sample') === String(id)",
        arg=sample_id, timeout=30000)


def _click_the_shifted_sample(page, sample_id):
    page.click(f"#chart circle[data-series='xbar'][data-sample='{sample_id}']")
    _await_panel(page, sample_id)


def _unfold(page, key):
    """Open one of the blocks the panel opens folded, and wait for it to be open.

    Since 2026-10-07 the panel opens about one screen long: the readings, the
    rules and the line's own events are open and the gauge, this station's
    trends and the maintenance block are one click each. The wait is on the
    `<details>`' own `open` state rather than on a timeout, because the browser
    lays it out and this page's script does not (#112).
    """
    page.click(f'[data-fold="{key}"] > summary')
    page.wait_for_function(
        "(k) => document.querySelector(`[data-fold=\"${k}\"]`).open === true",
        arg=key, timeout=10000)


def _dots(page, series):
    """The dots of one half, in drawing order, as `{sample, cx, cy}`."""
    return page.evaluate(
        """(series) => [...document.querySelectorAll(
             `#chart circle[data-series='${series}']`)]
           .map((c) => ({ sample: Number(c.getAttribute('data-sample')),
                          cx: Number(c.getAttribute('cx')),
                          cy: Number(c.getAttribute('cy')) }))""",
        series)


# --------------------------------------------------------------- the two halves


def test_the_range_half_is_drawn_under_the_average_half_in_one_chart(page):
    """One dot per sample on each half, the spread under the average it
    belongs to, in the same `<svg>`: an X-bar and R chart is a pair and is read
    as one. They share the x scale, and `FS.kit.export` takes the node rather
    than half of it."""
    means, ranges = _dots(page, "xbar"), _dots(page, "sample-range")
    assert len(means) == len(ranges) == DRAWN
    assert [d["sample"] for d in means] == [d["sample"] for d in ranges]
    assert [round(d["cx"]) for d in means] == [round(d["cx"]) for d in ranges]
    assert min(d["cy"] for d in ranges) > max(d["cy"] for d in means)
    assert page.eval_on_selector_all("#chart svg.fs-chart", "els => els.length") == 1
    # One kit shape draws both kinds since 2026-10-07, so the <svg> says
    # which SHAPE drew it and the plot group says which kind of chart it is
    # — in the payload's own word.
    assert page.get_attribute("#chart svg.fs-chart", "data-kind") == "spc"
    assert page.get_attribute("#chart [data-spc-kind]", "data-spc-kind") == "xbar_r"


def test_the_lower_half_has_the_three_lines_an_r_chart_has(page):
    """`R̄`, `D4·R̄` and `D3·R̄`, drawn below every average dot. The lower
    limit is drawn even when it sits at nought, because *the lower limit of a
    range chart with five bottles in a sample is zero* is a fact about the
    constants and not a line nobody got round to."""
    below = page.evaluate(
        """() => {
             const half = document.querySelector("#chart g[data-half='sample-range']");
             const of = (cls) => half.querySelectorAll(`line.${cls}`).length;
             return { limit: of('limit-line'), centre: of('centre-line') };
           }""")
    assert below == {"limit": 2, "centre": 1}
    lower = page.evaluate(
        """() => [...document.querySelectorAll(
             "#chart g[data-half='sample-range'] text")]
           .map((t) => t.textContent).join(' | ')""")
    assert "D4·R̄" in lower
    assert "D3·R̄" in lower
    assert "R̄" in lower
    assert "Range within each sample" in lower


def test_the_sample_a_rule_fired_on_is_the_one_marked_and_no_other(page, plant):
    """Identity, not counting. The five bottles after the changeover are the
    only sample in this history whose mean is beyond the control limit, so
    exactly one dot on the upper half is drawn as a firing - and their spread
    is ordinary, so none on the lower half is."""
    _base, shifted = plant
    assert page.evaluate(
        """() => [...document.querySelectorAll(
             "#chart circle[data-series='xbar'].spc-flag")]
           .map((c) => Number(c.getAttribute('data-sample')))""") == [shifted]
    assert page.evaluate(
        """() => document.querySelectorAll(
             "#chart circle[data-series='sample-range'].spc-flag").length""") == 0


def test_one_export_takes_both_halves_off_the_page(page):
    """`FS.kit.export` is given the node, and the node is the pair. Two buttons
    and one file: a reader who saved half an X-bar and R chart into a slide
    would be showing the half that cannot be read on its own."""
    assert page.eval_on_selector_all("#chart-tools button", "els => els.length") == 2
    got = page.evaluate(
        """async () => {
             const node = document.querySelector('#chart svg.fs-chart');
             const blob = await window.FS.kit.export(node, 'svg');
             const text = await blob.text();
             return { bytes: blob.size,
                      ranges: (text.match(/data-series="sample-range"/g) || []).length };
           }""")
    assert got["bytes"] > 0
    assert got["ranges"] == DRAWN


# ------------------------------------------------------------- opening a sample


def test_the_panel_says_nothing_is_open_until_a_sample_is_clicked(page):
    """The column is on the page from the start, so clicking a dot moves
    nothing else - and an empty column has to say what it is for. On this chart
    what it is for is five readings, and it says so before anybody clicks."""
    panel = page.query_selector("#point-panel")
    assert panel.get_attribute("data-state") == "idle"
    assert panel.get_attribute("data-sample") == ""
    assert panel.get_attribute("data-point") == ""
    idle = page.inner_text("#point-idle")
    assert "No sample is open" in idle
    assert "5 readings behind it" in idle
    # `text_content`, not `inner_text`: the heading is upper-cased by the
    # stylesheet, and asserting on the shouted form would tie this test to a
    # text-transform rather than to the words somebody wrote.
    assert page.text_content("#point-title").strip() == "Why this sample is here"


def test_clicking_a_dot_opens_the_five_readings_behind_that_point(page, plant):
    _base, shifted = plant
    _click_the_shifted_sample(page, shifted)
    assert page.query_selector("#point-panel").get_attribute("data-state") == "open"
    assert page.query_selector("#point-panel").get_attribute("data-point") == ""
    text = page.inner_text("#point-panel")
    assert "The 5 readings behind this point" in text
    assert str(SHIFTED_MEAN) in text
    assert "OP-NIGHT" in text
    values = page.evaluate(
        """() => [...document.querySelectorAll('#point-panel table tbody tr')]
             .slice(0, 5).map((tr) => tr.children[1].textContent.trim())""")
    # `144`, not `144.0`: the envelope's JSON numbers are JS numbers by the
    # time they reach a cell, and a reading is printed as it is.
    assert [v.split(" ")[0] for v in values] == [f"{v:g}" for v in SHIFTED]


def test_the_bottle_that_pulled_the_average_is_the_one_marked_furthest(page, plant):
    """The question an X-bar chart cannot answer on its own, answered in one
    click: five bottles high is a filler setting, one bottle high is a nozzle.
    Keyed on which bottle - 147.0 mm - and not on how many are marked."""
    _base, shifted = plant
    _click_the_shifted_sample(page, shifted)
    marked = page.evaluate(
        """() => [...document.querySelectorAll('#point-panel table tbody tr')]
             .filter((tr) => /furthest out/.test(tr.textContent))
             .map((tr) => tr.children[1].textContent.trim())""")
    assert len(marked) == 1, marked
    assert marked[0].startswith(f"{FURTHEST:g}")
    assert "always the furthest" in page.inner_text("#point-panel"), (
        "the panel names a bottle without saying that one of them always is")


def test_a_bottle_outside_the_specification_is_said_on_its_own_row(page, plant):
    """A sample whose mean is inside the limits can still hold a bottle that is
    outside the tolerance, and the average hides that by construction."""
    _base, shifted = plant
    _click_the_shifted_sample(page, shifted)
    rows = page.evaluate(
        """() => [...document.querySelectorAll('#point-panel table tbody tr')]
             .slice(0, 5).map((tr) => tr.children[1].textContent.trim())""")
    assert sum("above spec" in row for row in rows) == 2, rows


def test_the_panel_is_empty_while_the_fetch_is_in_flight_and_says_it_is_opening(
        page, plant):
    """`data-sample` means *this sample is drawn*. Leaving the last one on it
    while a new answer is on the wire would have a reader - and a test -
    believing the panel beside them is about the dot they just clicked."""
    _base, shifted = plant
    page.click(f"#chart circle[data-series='xbar'][data-sample='{shifted}']")
    page.wait_for_function(
        "() => document.querySelector('#point-panel').getAttribute('data-state')"
        " === 'loading'", timeout=10000)
    panel = page.query_selector("#point-panel")
    assert panel.get_attribute("data-sample") == ""
    assert "opening" in page.inner_text("#point-which").lower()
    _await_panel(page, shifted)


def test_clicking_another_sample_moves_the_panel_and_nothing_else(page, plant):
    """"Click another dot, the panel follows. Nothing else moves." - the ask
    from #143, which holds on this chart too. The chart keeps its size and the
    facts row keeps its numbers."""
    _base, shifted = plant
    before = page.evaluate(
        """() => ({
             chart: document.querySelector('#chart').getBoundingClientRect().width,
             facts: document.querySelector('.object-facts').textContent,
             verdict: document.querySelector('#verdict').textContent,
             dots: document.querySelectorAll('#chart circle[data-sample]').length,
           })""")
    _click_the_shifted_sample(page, shifted)
    other = _dots(page, "xbar")[0]["sample"]
    page.click(f"#chart circle[data-series='xbar'][data-sample='{other}']")
    _await_panel(page, other)
    after = page.evaluate(
        """() => ({
             chart: document.querySelector('#chart').getBoundingClientRect().width,
             facts: document.querySelector('.object-facts').textContent,
             verdict: document.querySelector('#verdict').textContent,
             dots: document.querySelectorAll('#chart circle[data-sample]').length,
           })""")
    assert after == before


def test_a_dot_on_the_lower_half_opens_the_same_sample_as_the_one_above_it(page, plant):
    """A range and a mean are two facts about one sample, so both dots open the
    same five bottles. A reader who came from the lower chart asking *why is
    this one spread so wide* needs the readings, which is the same panel."""
    _base, shifted = plant
    page.click(f"#chart circle[data-series='sample-range'][data-sample='{shifted}']")
    _await_panel(page, shifted)
    assert "The 5 readings behind this point" in page.inner_text("#point-panel")


def test_a_keyboard_reaches_a_sample_and_opens_it(page, plant):
    """There is no `<button>` inside an SVG, so the dot carries what one would
    carry and answers Enter - the same way the kit's own legend and threshold
    controls do (style rule 6's intent)."""
    _base, shifted = plant
    dot = page.query_selector(f"#chart circle[data-series='xbar'][data-sample='{shifted}']")
    assert dot.get_attribute("role") == "button"
    assert dot.get_attribute("tabindex") == "0"
    assert "open the readings behind it" in dot.get_attribute("aria-label")
    dot.focus()
    page.keyboard.press("Enter")
    _await_panel(page, shifted)


def test_the_button_opens_the_sample_a_rule_fired_on(page, plant):
    """A keyboard's way in without hunting for a dot, and it says what it will
    open: the flagged sample, and five bottles rather than one reading."""
    _base, shifted = plant
    button = page.query_selector("#open-point")
    assert button.get_attribute("data-assist") == "spc-open-point"
    assert button.inner_text().strip() == "Open the flagged sample"
    assert "opens all 5 of them" in button.get_attribute("title")
    button.click()
    _await_panel(page, shifted)


def test_the_sample_that_is_open_is_marked_on_both_halves(page, plant):
    """Ringed rather than recoloured: the dot's colour already says whether a
    rule fired on it. Marked on both halves because it is one sample, and a
    reader looking at the lower chart has to be able to see which point they
    are reading."""
    _base, shifted = plant
    _click_the_shifted_sample(page, shifted)
    page.wait_for_function(
        "(id) => document.querySelectorAll("
        " `#chart circle[data-sample='${id}'].spc-selected,"
        "   #chart circle[data-sample='${id}'].spc-mr-selected`).length === 2",
        arg=shifted, timeout=10000)


# --------------------------------------------------- the rest of the panel


def test_the_chart_block_names_both_halves_limits(page, plant):
    """The reader came from one half and may need the other: where the mean sat
    against `X̿ ± A2·R̄`, and where the spread sat against `D4·R̄`."""
    _base, shifted = plant
    _click_the_shifted_sample(page, shifted)
    text = page.inner_text("#point-panel")
    assert "rule 1" in text
    assert "X̿" in text
    assert "Sigma (within)" in text
    assert "Mean range (R̄)" in text
    # The range against its own limit, rather than a bare figure: `D4·R̄` is
    # the line on the chart, and this is where the sample sat under it.
    assert "allowed" in text


def test_what_the_machine_was_doing_and_what_it_had_just_come_out_of(page, plant):
    """The planted cause, in one click: five bottles high forty seconds after a
    changeover ended is a sample about the changeover."""
    _base, shifted = plant
    _click_the_shifted_sample(page, shifted)
    text = page.inner_text("#point-panel")
    assert "running" in text
    assert "Product change" in text
    assert "before the sample" in text


def test_the_gauge_the_maintenance_order_and_the_unlabelled_stop_are_all_there(
        page, plant):
    _base, shifted = plant
    _click_the_shifted_sample(page, shifted)
    _unfold(page, "gauge")
    _unfold(page, "else")
    text = page.inner_text("#point-panel")
    assert "HEIGHT-01" in text
    assert "QA-LEAD" in text
    assert "MO-FILLER" in text
    assert "unlabelled" in text
    assert "never a station" in text, "the findings list does not say its scope"


def test_every_one_of_the_five_readings_is_marked_on_each_trend(page, plant):
    """The whole reason the picture answers the question. One marker in the
    middle of a sample would hide which bottle was measured when, and the
    nozzle pressure before the first of them is the thing a reader is looking
    for."""
    _base, shifted = plant
    _click_the_shifted_sample(page, shifted)
    got = page.evaluate(
        """() => [...document.querySelectorAll('#point-panel svg.fs-chart')]
             .map((c) => ({ kind: c.getAttribute('data-kind'),
                            total: c.getAttribute('data-total'),
                            coverage: c.getAttribute('data-coverage'),
                            markers: c.querySelectorAll('[data-marker]').length }))""")
    assert [c["kind"] for c in got] == ["states", "line", "line"], got
    for chart in got:
        assert chart["total"], f"a chart with no total: {chart}"
        assert chart["coverage"] not in (None, ""), chart
    assert [c["markers"] for c in got if c["kind"] == "line"] == [5, 5], got


def test_every_block_on_the_panel_says_what_it_knows_about_its_own_window(page, plant):
    """Rule 2 of the chart contract, as a panel: a figure, "unknown", and
    "this is a list of records" are three facts and the reader is owed which."""
    _base, shifted = plant
    _click_the_shifted_sample(page, shifted)
    said = page.eval_on_selector_all(
        "#point-panel .point-watched", "els => els.map(e => e.textContent)")
    assert len(said) >= 4, said
    assert any("Watched" in line for line in said)
    assert any("no coverage figure" in line.lower() for line in said)


def test_the_panel_and_its_control_carry_the_anchors_a_guide_points_at(page):
    """Style rule 7. Renaming one of these is a deliberate act that re-authors
    whatever points at it."""
    assert page.query_selector('[data-assist="spc-point-panel"]')
    assert page.query_selector('[data-assist="spc-open-point"]')


# ----------------------------------------------------------------- four themes


@pytest.mark.parametrize("theme", THEMES)
def test_the_panel_reads_in_all_four_themes(admin, plant, theme):
    """Style rule 3. A change is not done until it looks right in all four, and
    "looks right" begins with nothing on the panel - or on the chart's second
    half - resolving to a colour the palette does not define."""
    base, shifted = plant
    page = _open_spc(admin, base, theme=theme)
    try:
        _click_the_shifted_sample(page, shifted)
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
                 const marks = [...document.querySelectorAll(
                   '#point-panel svg.fs-chart line, #point-panel svg.fs-chart text, '
                   + '#point-panel svg.fs-chart polyline, #chart svg.fs-chart line, '
                   + '#chart svg.fs-chart circle, #chart svg.fs-chart polyline')];
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


def test_an_operator_opens_a_sample_the_way_a_supervisor_does(chromium, plant):
    """It is a read of the plant's own records. `plant.read` is the gate on
    every read in this product, and a panel the shop floor cannot open is a
    panel Scott's operators do not have."""
    base, shifted = plant
    context = _signed_in(chromium, base, "SCOTT", "operator")
    try:
        page = _open_spc(context, base)
        try:
            _click_the_shifted_sample(page, shifted)
            _unfold(page, "gauge")
            _unfold(page, "else")
            text = page.inner_text("#point-panel")
            assert "HEIGHT-01" in text
            assert "MO-FILLER" in text
            assert "Product change" in text
        finally:
            _close(page)
    finally:
        context.close()


# ----------------------------------- the rest of the line, and one screen of it


def test_the_station_beside_this_one_is_on_the_sample_panel_too(page, plant):
    """The same block on the sample panel, because the question is the same
    one: five bottles went high, and the station next to the filler had
    changed size six minutes before them. Named, with its own page a click
    away, and with no claim that one caused the other."""
    _base, sample = plant
    _click_the_shifted_sample(page, sample)
    block = page.inner_text('[data-fold="line"]')
    assert "The rest of the line" in block
    assert "PACK01" in block
    assert "changeover" in block
    assert "Size change" in block
    assert "before this sample" in block
    assert page.get_attribute(
        '[data-fold="line"] a.obj[href*="PACK01"]', "href") == "/dashboard/machine/PACK01"


def test_the_sample_panel_opens_with_its_three_longest_blocks_folded(page, plant):
    """What a reader meets on a sample, and how much shorter it is.

    Open: the five readings (618 pixels of them here, because five bottles are
    five rows and their chart), what the rules said (419), what the machine was
    doing (362), its timeline (248) and the rest of the line (391) - 2,243 in
    all on this fixture. Folded to one line each: the gauge, the station's
    process values and the maintenance block, which is where the scrolling was.

    The number asserted is the ratio, not the height: the same markup measured
    2,243 on one machine and 2,436 on GitHub's runner, so a pixel bound here
    would be a test about a font. And a sample's panel is not one screen even
    folded - this says so rather than claiming otherwise.
    """
    _base, sample = plant
    _click_the_shifted_sample(page, sample)
    open_now = page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('[data-fold]')]
             .map((d) => [d.dataset.fold, d.open]))""")
    assert open_now == {"gauge": False, "line": True, "tags": False, "else": False}
    says = page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('[data-fold]')]
             .map((d) => [d.dataset.fold, d.querySelector('summary').innerText]))""")
    assert "HEIGHT-01" in says["gauge"]
    assert "2 process values" in says["tags"]
    assert "maintenance order" in says["else"]
    folded = page.evaluate(
        "() => document.querySelector('#point-body').scrollHeight")
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


# ------------------------------- and the same chart, explained by the chat

def _explain_question(page):
    """The question the *Explain this chart* link is carrying, decoded.

    Read off the href rather than out of a click, so a test can say what the
    sentence is before anything navigates. The hash has to stay `#explore` -
    `FS.tabs` reads the hash as the tab name - so the question travels as a
    query, and that is asserted here too.
    """
    href = page.locator("#explain-chart").get_attribute("href")
    parts = urlparse(href)
    assert parts.path == "/dashboard/ai"
    assert parts.fragment == "explore", (
        "the hash is the tab the AI page opens on, so the question may not be "
        f"in it: {href}")
    return parse_qs(parts.query)["ask"][0]


def test_explain_this_chart_asks_about_the_characteristic_on_the_screen(page):
    """The names travel in the sentence and nothing else travels at all: no
    payload, no scrape of this page's DOM. So the chat reads this plant itself
    and the figures in its answer are the plant's, not this screen's copy of
    them - which is the whole of why the question carries names and not data.

    The names are the plant's own, `fill_height` and not a tidied *fill
    height*: that is what the chart's tool takes, and a prettier sentence that
    cost the model a round of guessing would be a worse question.
    """
    asked = _explain_question(page)
    assert "FG-COLA" in asked and "fill_height" in asked
    assert "plant manager" in asked
    assert "Two sentences" in asked and "slide" in asked
    assert "sample" not in asked, "no sample is open, so none is asked about"


def test_with_a_sample_open_it_asks_why_that_sample_is_where_it_is(page, plant):
    """Somebody with the panel open is already holding the second question.
    The link picks it up - by the sample's own id, which is what `spc_sample`
    takes - and drops it again when another characteristic is chosen."""
    _base, sample = plant
    _click_the_shifted_sample(page, sample)
    _await_panel(page, sample)
    asked = _explain_question(page)
    assert f"why is sample {sample} where it is" in asked
    assert "fill_height" in asked


def test_explain_this_chart_lands_on_explore_with_the_question_typed(page):
    """End to end, in the browser, as Scott would do it: press the link and
    the Explore tab is in front of you with the sentence in the box.

    Typed and NOT sent. The budget is spent by the person whose question it is:
    this is a guess at what they wanted to know - the chart they were looking
    at - and the guess is worth making where acting on it is not. So the log is
    empty and nothing has been asked.
    """
    page.click("#explain-chart")
    page.wait_for_function(
        """() => document.querySelector('#explore-input')
             && document.querySelector('#explore-input').value.includes('fill_height')""",
        timeout=30000)
    assert page.locator("[data-panel='explore']").is_visible()
    typed = page.input_value("#explore-input")
    assert "FG-COLA" in typed and "plant manager" in typed
    assert page.locator("#explore-log .explore-msg").count() == 0, (
        "the question was sent for the person rather than handed to them")
