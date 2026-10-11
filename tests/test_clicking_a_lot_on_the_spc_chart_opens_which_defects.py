"""Click a dot on a p chart in a browser, and read which defects.

The third of the family. `test_clicking_a_point_on_the_spc_chart_opens_the_
records_behind_it` is the individuals chart, `test_clicking_a_sample_on_the_
spc_chart_opens_the_five_bottles` is the X-bar chart, and this is the chart
decision 0041 added: a characteristic nobody measures, where a point is a
stretch somebody looked at and the dot says *more of this lot came back than
usual*.

What is different about this one, and what it is therefore worth a file of its
own to pin: **the only useful next question is which ones.** A share has no
reading behind it and no five bottles - it has two whole numbers and a
breakdown - so the panel opens on the defects, and whether a reader can tell
*the fill went wrong* from *the caps went wrong* in one click is the question
this file answers.

**THE DOSSIER FETCH IS HELD BACK BY OVER A SECOND.** A check on loopback
proves the code path and not the experience. A route intercept delays the
lot read, and every assertion waits on the panel's own `data-count`
attribute - which the page writes LAST, after the blocks are on the screen -
rather than on an element that exists before the answer does. That is the
2026-09-26 lesson from #112 and #113.

The plant underneath is the bottling floor's own shape, counted rather than
measured: ten stretches of a quarter of an hour each, inspected at QI01, most
of them sending back a tenth of what they looked at and most of those sent
back for skewed caps. One stretch - a hundred units, twenty sent back, sixteen
of them short-filled - sits past its OWN upper limit while the product
temperature under it is below the band. Whether a reader who opens that dot
learns it was the fill and not the caps is asserted by identity: the lot is
`4711-Q06` and no other, and the defect at the top of its list is `low_fill`.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for, and a Playwright file marked only `slow` is a test nothing runs.
"""

import re
import socket
import threading
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

THEMES = ("control-room", "daylight", "high-contrast", "night-shift")

#: How long the lot read is held back for. Comfortably longer than this page
#: takes to draw on loopback, so a passing assertion means the panel waited
#: for its answer rather than that the answer was already there.
HELD_BACK_MS = 1200

#: Ten inspected stretches, worked out so every figure on the screen can be
#: checked on paper. The sizes are perfect squares divisible by a hundred and
#: the totals come to 1,110 sent back of 11,100 looked at, so:
#:
#:     pooled centre p-bar = 1110 / 11100       = 0.1 exactly
#:     sqrt(p-bar * (1 - p-bar)) = sqrt(0.09)   = 0.3 exactly
#:     three sigma at n = 0.9 / sqrt(n)
#:
#: which is 0.09 on the lot of a hundred and 0.018 on the lot of two and a
#: half thousand. The same share - a tenth sent back - therefore sits a third
#: of the way to the limit on the small lot and dead on the centre on the big
#: one, and one straight pair of lines across the picture would have flagged
#: the small lots and excused the large ones. That is the error this kind of
#: chart exists to prevent, and it is why the limits here are steps.
SIZES = [900, 400, 2500, 900, 1600, 2500, 900, 400, 900, 100]
LOTS = [(n, n // 10) for n in SIZES]

#: The stretch that goes wrong, and the compensation that keeps the centre
#: exactly where it was. 20 of 100 is a share of 0.2, past that lot's own
#: upper limit of 0.19; ten fewer sent back from the sixth lot of 2,500 puts
#: it at 0.096, well inside its own [0.082, 0.118]. The totals are unchanged,
#: so the centre line and every band stay exactly as above and the only thing
#: that moved is the one lot.
#:
#: It is the LAST of the ten, and that is deliberate: the rules are run when a
#: count is written, against the limits the chart had at that moment. A lot
#: that went wrong before this plant had enough lots to draw limits from is
#: judged by the chart now and was held by nothing then - true, and not what a
#: reader opening a flagged dot should meet first. So the lot that goes wrong
#: is the tenth, by which time there are ten.
WRONG, COMPENSATED = 9, 5
LOTS[WRONG] = (100, 20)
LOTS[COMPENSATED] = (2500, 240)

#: The lot a person clicks, by its own name, and the lot nobody broke down -
#: the largest one, because 250 units sent back with no breakdown is exactly
#: the record a screen must not draw as a clean lot.
WRONG_LOT, SMALLEST, SENT_BACK = "4711-Q10", 100, 20
UNCODED_LOT, UNCODED_SENT_BACK = "4711-Q03", "250 of 2,500"

#: What was wrong with the twenty. Sixteen short-filled and four badly capped:
#: a majority, not a sweep, because *mostly the fill* is the shape of a real
#: breakdown and *all of it the fill* is the shape of a fixture.
WRONG_DEFECTS = [{"code": "cap_skew", "count": 4}, {"code": "low_fill", "count": 16}]

#: How long each inspected stretch covers, and the cadence the fixture's
#: process values arrive at. A quarter of an hour is what the bottling pack's
#: `inspection` section counts in.
STRETCH_MINUTES = 15
TAG_SECONDS = 60

#: The product temperature band the chiller holds, and what it fell to under
#: the stretch that went wrong. Below the band is the planted cause, and it is
#: planted in the telemetry rather than in the count: nothing in this fixture
#: tells the panel that the cold product and the short fills are the same
#: event, and the panel does not claim it. It draws both and lets a person
#: read them.
IN_BAND, TOO_COLD = 4.0, 1.4


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A bottling-shaped plant whose characteristic is counted, not measured.

    Everything the panel is meant to find is written down with its own stamp:
    ten stretches each with both ends recorded, a station that counted them, a
    chiller that tripped a few minutes before the stretch that went wrong, a
    product temperature that fell below its band while it was counted, and one
    stretch at the end that nobody broke down - because *not broken down* and
    *nothing found* are different facts and this screen has to say which.

    `[quality] spc_min_points` is moved to ten. The number is the plant's, and
    a plant that inspects a lot every quarter of an hour has ten of them in
    two and a half hours; this fixture is about what the chart does once it
    has limits, not about how many lots it waits for.
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
    from fsmes.services import auth, plant_settings, quality

    path = tmp_path_factory.mktemp("spclot") / "plant.db"
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
            plant_settings.write(session, domain="quality", key="spc_min_points",
                                 written=str(len(LOTS)), actor="test")
            quality.create_spec(session, material_code="FG-COLA",
                                characteristic="sent_back", unit="",
                                counted=True, actor="test")

            now = utcnow()
            #: The ten stretches end one after another and the last of them
            #: ends now, so the newest lot is the one a reader meets.
            def stretch(turn):
                ends = now - timedelta(minutes=STRETCH_MINUTES * (len(LOTS) - 1 - turn))
                return ends - timedelta(minutes=STRETCH_MINUTES), ends

            rows = []
            for turn, (inspected, rejected) in enumerate(LOTS):
                started, ended = stretch(turn)
                lot = f"4711-Q{turn + 1:02d}"
                if lot == UNCODED_LOT:
                    # Nobody coded these. Null, and the panel says so.
                    defects = None
                elif lot == WRONG_LOT:
                    defects = WRONG_DEFECTS
                else:
                    # Skewed caps are this line's ordinary complaint, so the
                    # stretch that went wrong stands out by WHAT was wrong
                    # with it and not only by how much.
                    defects = [{"code": "cap_skew", "count": rejected - rejected // 4},
                               {"code": "label_off", "count": rejected // 4}]
                count, _nc, _signals = quality.record_count(
                    session, material_code="FG-COLA", characteristic="sent_back",
                    inspected=inspected, rejected=rejected, lot_ref=lot,
                    defects=defects, equipment_code="MIX01",
                    started_at=started, ended_at=ended, actor="QI01")
                # `record_count` stamps the row when it is written, and this
                # fixture writes ten of them in one second. The stamp a reader
                # sees is when the stretch ended.
                count.ts = ended
                session.flush()
                rows.append(count)

            wrong = rows[WRONG]
            cold_from, cold_to = wrong.started_at, wrong.ended_at

            mixer = session.scalar(select(Equipment).where(Equipment.code == "MIX01"))
            packer = session.scalar(select(Equipment).where(Equipment.code == "PACK01"))
            watched_from = rows[0].started_at - timedelta(hours=1)
            session.add_all([
                # Watched since before the first stretch, so the minutes the
                # panel asks about are minutes it can answer for.
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=watched_from,
                               ended_at=cold_from - timedelta(minutes=9)),
                # The chiller trip: a stop nobody labelled, then a utility
                # stop that ended four minutes before the stretch that went
                # wrong was counted.
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.DOWN,
                               started_at=cold_from - timedelta(minutes=9),
                               ended_at=cold_from - timedelta(minutes=8)),
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.DOWN,
                               reason="Chiller trip", reason_code="utility",
                               started_at=cold_from - timedelta(minutes=8),
                               ended_at=cold_from - timedelta(minutes=4)),
                EquipmentState(equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                               started_at=cold_from - timedelta(minutes=4)),
                # And the station beside it, which changed size inside the
                # same stretch - named, with no claim that it is the cause.
                EquipmentState(equipment_id=packer.id, state=EquipmentStateName.RUNNING,
                               started_at=watched_from,
                               ended_at=cold_from + timedelta(minutes=2)),
                EquipmentState(equipment_id=packer.id, state=EquipmentStateName.SETUP,
                               reason="Size change", reason_code="changeover",
                               started_at=cold_from + timedelta(minutes=2),
                               ended_at=cold_from + timedelta(minutes=6)),
                EquipmentState(equipment_id=packer.id, state=EquipmentStateName.RUNNING,
                               started_at=cold_from + timedelta(minutes=6)),
                MaintenanceOrder(
                    code="MO-CHILLER", equipment_id=mixer.id,
                    kind=MaintenanceKind.CORRECTIVE,
                    status=MaintenanceStatus.IN_PROGRESS,
                    summary="Chiller short-cycling on the glycol loop",
                    raised_at=cold_from - timedelta(minutes=8),
                    started_at=cold_from - timedelta(minutes=6)),
            ])
            # Two process values across the whole history, so any lot's window
            # is a window this plant can answer for. The product temperature
            # falls below the band for the stretch that went wrong and comes
            # back after it; the line pressure does not move at all, which is
            # what makes the first one worth looking at.
            moment = watched_from
            while moment <= now + timedelta(minutes=10):
                cold = cold_from <= moment <= cold_to
                session.add(TagValue(equipment_id=mixer.id, tag="MIX01.ProductTemp",
                                     ts=moment, value_num=TOO_COLD if cold else IN_BAND))
                session.add(TagValue(equipment_id=mixer.id, tag="MIX01.Pressure",
                                     ts=moment, value_num=2.6))
                moment += timedelta(seconds=TAG_SECONDS)
            session.commit()
            counts = {row.lot_ref: row.id for row in rows}

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(
            create_app(), log_level="warning", lifespan="on"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
            _wait_until_answering(base)
            yield base, counts
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


def _open_spc(context, base, theme="control-room"):
    """The SPC page on the counted characteristic, with the lot read held back.

    The folds a reader opened are remembered in `localStorage` and the context
    is shared by every test in this file, so each page starts from the panel's
    own defaults.
    """
    page = context.new_page()
    page.add_init_script(
        f"try {{ localStorage.setItem('fsmes-theme', {theme!r}); }} catch (e) {{}}")
    page.add_init_script(
        """try {
             for (const key of Object.keys(localStorage)) {
               if (key.startsWith('fsmes-spc-fold-')) localStorage.removeItem(key);
             }
           } catch (e) {}""")

    def held(route):
        page.wait_for_timeout(HELD_BACK_MS)
        route.continue_()

    page.route("**/sent_back/lot/*", held)
    page.goto(f"{base}/dashboard/spc?spec=FG-COLA%7Csent_back", wait_until="load",
              timeout=30000)
    # The chart is drawn after two fetches settle. Wait for one dot per lot,
    # which is the last thing appended.
    page.wait_for_function(
        "n => document.querySelectorAll("
        " `#chart circle[data-series='share']`).length === n",
        arg=len(LOTS), timeout=30000)
    return page


@pytest.fixture(scope="module")
def admin(chromium, plant):
    base, _counts = plant
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
    base, _counts = plant
    node = _open_spc(admin, base)
    yield node
    _close(node)


def _await_panel(page, count_id):
    """Wait on the panel's own attribute, which the page writes last.

    Not on `#point-body` and not on a sleep. The box is on the page before the
    fetch is, and `data-count` means *this lot is drawn*.
    """
    page.wait_for_function(
        "(id) => document.querySelector('#point-panel')"
        ".getAttribute('data-count') === String(id)",
        arg=count_id, timeout=30000)


def _click_the_lot(page, counts, lot=WRONG_LOT):
    page.click(f"#chart circle[data-series='share'][data-count='{counts[lot]}']")
    _await_panel(page, counts[lot])


def _unfold(page, key):
    """Open one of the blocks the panel opens folded, and wait for it to be
    open - on the `<details>`' own state rather than on a timeout, because the
    browser lays it out and this page's script does not (#112)."""
    page.click(f'[data-fold="{key}"] > summary')
    page.wait_for_function(
        "(k) => document.querySelector(`[data-fold=\"${k}\"]`).open === true",
        arg=key, timeout=10000)


def _dots(page):
    """The dots, in drawing order, as `{count, cx, cy}`."""
    return page.evaluate(
        """() => [...document.querySelectorAll(
             "#chart circle[data-series='share']")]
           .map((c) => ({ count: Number(c.getAttribute('data-count')),
                          cx: Number(c.getAttribute('cx')),
                          cy: Number(c.getAttribute('cy')) }))""")


# ------------------------------------------------- the chart, before any click


def test_the_picker_names_this_characteristic_as_counted_per_lot(page):
    """A reader choosing `sent_back` from the list is told, in the list, that
    the picture they are about to get is a different picture. "counted, per
    lot" and not "attribute data": the second is a phrase nobody on a line
    uses."""
    label = page.evaluate(
        """() => [...document.querySelectorAll('#spec option')]
             .filter((o) => o.value === 'FG-COLA|sent_back')
             .map((o) => o.textContent)[0]""")
    assert label == "sent_back — counted, per lot (p)"
    assert page.get_attribute("#chart [data-spc-kind]", "data-spc-kind") == "p"
    assert "sent back" in page.text_content("#chart-heading").lower()


def test_one_dot_per_lot_and_no_second_chart_under_them(page):
    """Ten stretches, ten dots, and nothing below. A range needs more than one
    number in a point, and a lot is two whole numbers - so the lower half the
    other two charts carry is absent here, and the page says why rather than
    leaving a reader to wonder what is missing."""
    assert len(_dots(page)) == len(LOTS)
    assert page.eval_on_selector_all("#chart svg.fs-chart", "els => els.length") == 1
    assert page.evaluate(
        """() => document.querySelectorAll(
             "#chart g[data-half='moving-range'], "
             + "#chart g[data-half='sample-range']").length""") == 0
    assert "no chart below this one" in page.inner_text("#mr-verdict").lower()


def test_the_limits_step_and_the_step_over_the_small_lot_is_the_wider_one(page, plant):
    """The one thing this kind of chart exists for, in the browser. Every lot
    is a different number of units, so there is no pair of lines - there is a
    pair of staircases, and the tread over the lot of a hundred is wider than
    the tread over the lot of two and a half thousand. A reader who saw one
    straight pair would believe the small lots were out of control.
    """
    _base, counts = plant
    got = page.evaluate(
        """() => ({
             steps: [...document.querySelectorAll('#chart polyline.limit-step')]
               .map((l) => l.getAttribute('data-steps')),
             flat: document.querySelectorAll('#chart line.limit-line').length,
           })""")
    assert sorted(got["steps"]) == ["lower", "upper"]
    assert got["flat"] == 0, "a straight limit on a chart whose limits move"
    # Measured off the staircase itself at the two lots, rather than off the
    # envelope: this is a test about what was drawn.
    bands = page.evaluate(
        """(ids) => {
             const dots = new Map([...document.querySelectorAll(
               "#chart circle[data-series='share']")]
               .map((c) => [Number(c.getAttribute('data-count')),
                            Number(c.getAttribute('cx'))]));
             const read = (key, x) => {
               const pts = document.querySelector(
                 `#chart polyline.limit-step[data-steps='${key}']`)
                 .getAttribute('points').trim().split(/\\s+/)
                 .map((p) => p.split(',').map(Number));
               let best = pts[0];
               for (const p of pts) if (Math.abs(p[0] - x) < Math.abs(best[0] - x)) best = p;
               return best[1];
             };
             return ids.map((id) => {
               const x = dots.get(id);
               return read('lower', x) - read('upper', x);
             });
           }""", [counts[WRONG_LOT], counts[UNCODED_LOT]])
    small, big = bands
    assert small > big * 2, (
        f"the band over the lot of {SMALLEST} is {small:.1f}px and the band "
        f"over the lot of 2,500 is {big:.1f}px, which is not the staircase "
        f"this chart is for")


# ----------------------------------------------------------- opening a lot


def test_the_panel_says_nothing_is_open_until_a_lot_is_clicked(page):
    """The column is on the page from the start, so clicking a dot moves
    nothing else - and an empty column has to say what it is for. On this
    chart what it is for is which defects, and it says so before anybody
    clicks."""
    panel = page.query_selector("#point-panel")
    assert panel.get_attribute("data-state") == "idle"
    assert panel.get_attribute("data-count") == ""
    assert panel.get_attribute("data-sample") == ""
    assert panel.get_attribute("data-point") == ""
    idle = page.inner_text("#point-idle")
    assert "No lot is open" in idle
    # `text_content`, not `inner_text`: the heading is upper-cased by the
    # stylesheet, and asserting on the shouted form would tie this test to a
    # text-transform rather than to the words somebody wrote.
    assert page.text_content("#point-title").strip() == "Why this lot is here"
    assert "Click a lot to see which defects" in page.inner_text("#point-hint")


def test_the_panel_is_empty_while_the_read_is_in_flight_and_says_it_is_opening(
        page, plant):
    """`data-count` means *this lot is drawn*. Leaving the last one on it
    while a new answer is on the wire would have a reader - and a test -
    believing the panel beside them is about the dot they just clicked. The
    read here is held back by over a second, so this is the state a person on
    a phone over the tailnet actually sees."""
    _base, counts = plant
    page.click(f"#chart circle[data-series='share'][data-count='{counts[WRONG_LOT]}']")
    page.wait_for_function(
        "() => document.querySelector('#point-panel').getAttribute('data-state')"
        " === 'loading'", timeout=10000)
    panel = page.query_selector("#point-panel")
    assert panel.get_attribute("data-count") == ""
    assert "opening" in page.inner_text("#point-which").lower()
    _await_panel(page, counts[WRONG_LOT])


def test_clicking_a_lot_opens_which_defects_it_was_sent_back_for(page, plant):
    """The whole question, in one click: twenty of a hundred came back, and
    sixteen of those twenty were short fills. Keyed on which defect is at the
    top of the list and not on how many rows there are, because *was it the
    fill or the caps* is what a reader of this dot came to find out.

    First block on the panel, too. On the other two charts the reading is
    first, because the question there is what was measured; here the two
    numbers are already on the chart and the question is what was wrong with
    them.
    """
    _base, counts = plant
    _click_the_lot(page, counts)
    assert page.query_selector("#point-panel").get_attribute("data-state") == "open"
    assert page.query_selector("#point-panel").get_attribute("data-sample") == ""
    headings = page.eval_on_selector_all(
        "#point-body h3", "els => els.map(e => e.textContent.trim())")
    assert headings[0] == "Which defects", headings
    rows = page.evaluate(
        """() => [...document.querySelectorAll('#point-body table tbody tr')]
             .slice(0, 2).map((tr) => [...tr.children].map(
               (td) => td.textContent.trim()))""")
    assert [row[0] for row in rows] == ["low_fill", "cap_skew"]
    assert [row[1] for row in rows] == ["16", "4"]
    # The share of the REJECTS, not of the lot: "which of the ones we sent
    # back was this" is the question this block is holding.
    assert rows[0][2].startswith("80"), rows[0]
    text = page.inner_text("#point-body")
    assert "Defect codes" in text and "Coded against them" in text
    # `text_content`, not `inner_text`: this line is upper-cased by the
    # stylesheet, and asserting on the shouted form would tie the test to a
    # text-transform rather than to the words somebody wrote.
    assert f"\u2014 lot {WRONG_LOT}" in page.text_content("#point-which")


def test_the_lot_block_says_the_two_numbers_the_stretch_and_what_a_lot_is_here(
        page, plant):
    """The record, in the plant's words, above the arithmetic on it: "20 of
    100 sent back" and then the share. Both ends of the stretch, the station
    that counted it, who counted it - and, because this plant does not ship
    lots yet, the sentence saying what a lot means on it instead of an empty
    link where a lot number would be."""
    _base, counts = plant
    _click_the_lot(page, counts)
    text = page.inner_text("#point-body")
    assert f"{SENT_BACK} of {SMALLEST}" in text
    assert "sent back — 20" in text, "the share is printed beside the two numbers"
    assert "QI01" in text
    assert "MIX01" in text
    assert "a lot here is one inspection stretch on one order" in text
    assert "nobody gauged this lot" in text, (
        "a measured panel's gauge block, left empty rather than explained")


def test_a_lot_nobody_broke_down_says_so_rather_than_drawing_a_clean_lot(page, plant):
    """Null is *not broken down*. An empty table here would be this screen
    telling a reader that an inspector found nothing wrong with 240 units he
    sent back."""
    _base, counts = plant
    _click_the_lot(page, counts, lot=UNCODED_LOT)
    text = page.inner_text("#point-body")
    assert "without a breakdown" in text
    assert "not broken down" in text, "not nought, where nobody counted"
    assert page.evaluate(
        """() => document.querySelectorAll(
             '#point-body h3')[0].closest('section')
             .querySelectorAll('table tbody tr').length""") == 0, (
        "a table of defects behind a lot nobody broke down")
    assert UNCODED_SENT_BACK in text


def test_the_rules_block_names_this_lots_own_limits_and_the_hold_it_raised(
        page, plant):
    """Judged against its own pair of lines, which is the pair a reader cannot
    find on a staircase by eye - and the hold the MES put on when the count
    arrived, because a signal nobody acted on is a signal nobody saw."""
    _base, counts = plant
    _click_the_lot(page, counts)
    text = page.inner_text("#point-body")
    assert "Rule 1: a lot beyond its own upper limit" in text
    assert "19.00%" in text, "this lot's own upper limit, as a percentage"
    assert "Centre (p\u0304)" in text
    assert "when the count arrived" in text
    assert re.search(r"NC-\S+", text), "the hold rule 1 raised on this lot"


def test_the_process_values_are_open_on_a_counted_lot_and_the_cold_is_in_them(
        page, plant):
    """*Was the product cold while they were sending bottles back* is the
    question a p chart sends a reader to the process values with, so this
    block opens OPEN here rather than folded - a reader who has to open a fold
    to ask it is a reader who will not.

    Both ends of the stretch are marked on every trend, not one instant in the
    middle: a count covers minutes of production, and a single marker would
    hide which side of the chiller trip the lot was counted on.
    """
    _base, counts = plant
    _click_the_lot(page, counts)
    folds = page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('[data-fold]')]
             .map((d) => [d.dataset.fold, d.open]))""")
    assert folds["tags"] is True, folds
    assert folds["else"] is False, folds
    block = page.inner_text('[data-fold="tags"]')
    assert "ProductTemp" in block
    charts = page.evaluate(
        """() => [...document.querySelectorAll('#point-body svg.fs-chart')]
             .map((c) => ({ kind: c.getAttribute('data-kind'),
                            total: c.getAttribute('data-total'),
                            marks: c.querySelectorAll('[data-marker]').length,
                            said: [...c.querySelectorAll('text')]
                              .map((t) => t.textContent) }))""")
    lines = [c for c in charts if c["kind"] == "line"]
    assert len(lines) == 2, charts
    for chart in charts:
        assert chart["total"], f"a chart with no total: {chart}"
        # Two marks, not one, and each said in words under the picture: a
        # single marker in the middle would hide which side of the chiller
        # stop the lot was counted on.
        assert chart["marks"] == 2, chart
        assert "stretch starts" in chart["said"], chart["said"]
        assert "stretch ends" in chart["said"], chart["said"]
    assert "Both ends of the stretch are marked" in page.inner_text(
        '[data-fold="tags"]')


def test_what_the_machine_had_just_come_out_of_is_on_the_lot_panel(page, plant):
    """The planted cause, in one click and without a claim: a chiller stop
    that ended four minutes before the stretch, an unlabelled stop before it,
    the open maintenance order, and the station beside this one changing size
    inside the same half hour."""
    _base, counts = plant
    _click_the_lot(page, counts)
    _unfold(page, "else")
    text = page.inner_text("#point-body")
    assert "Chiller trip" in text
    assert "before the lot" in text, (
        "the stop is named without saying how long before the lot it ended")
    assert "MO-CHILLER" in text
    assert "unlabelled" in text
    assert "never a station" in text, "the findings list does not say its scope"
    block = page.inner_text('[data-fold="line"]')
    assert "PACK01" in block and "Size change" in block


def test_every_block_on_the_lot_panel_says_what_it_knows_about_its_window(
        page, plant):
    """Rule 2 of the chart contract, as a panel: a figure, "unknown", and
    "this is a list of records" are three facts and the reader is owed
    which."""
    _base, counts = plant
    _click_the_lot(page, counts)
    said = page.eval_on_selector_all(
        "#point-body .point-watched", "els => els.map(e => e.textContent)")
    assert len(said) >= 4, said
    assert any("Watched" in line for line in said)
    assert any("not a rate" in line for line in said)


# --------------------------------------- the ways in, and the dot that is open


def test_a_keyboard_reaches_a_lot_and_opens_it(page, plant):
    """There is no `<button>` inside an SVG, so the dot carries what one would
    carry and answers Enter - and its label says the lot by name, with the two
    numbers, for a reader who is hearing the chart rather than seeing it."""
    _base, counts = plant
    dot = page.query_selector(
        f"#chart circle[data-series='share'][data-count='{counts[WRONG_LOT]}']")
    assert dot.get_attribute("role") == "button"
    assert dot.get_attribute("tabindex") == "0"
    aria = dot.get_attribute("aria-label")
    assert aria.startswith(f"Lot {WRONG_LOT}, {SENT_BACK} of {SMALLEST} sent back"), aria
    assert "which defects" in aria
    dot.focus()
    page.keyboard.press("Enter")
    _await_panel(page, counts[WRONG_LOT])


def test_the_button_opens_the_lot_a_rule_fired_on_and_says_which(page, plant):
    """A keyboard's way in without hunting for a dot, and it says what it will
    open: the worst lot a rule fired on, named by that rule, and which defects
    rather than a reading."""
    _base, counts = plant
    button = page.query_selector("#open-point")
    assert re.fullmatch(r"Open the rule \d lot", button.inner_text().strip()), \
        button.inner_text()
    assert "which defects" in button.get_attribute("title")
    button.click()
    _await_panel(page, counts[WRONG_LOT])


def test_the_lot_that_is_open_is_ringed_on_the_chart_and_nothing_recoloured(
        page, plant):
    """Ringed rather than recoloured: the dot's colour already says whether a
    rule fired on it. One ring, on the lot that is open, and no second ring
    left behind from the last one."""
    _base, counts = plant
    _click_the_lot(page, counts)
    page.wait_for_function(
        "(id) => document.querySelectorAll('#chart [data-ring-count]').length === 1"
        " && document.querySelector('#chart [data-ring-count]')"
        ".getAttribute('data-ring-count') === String(id)",
        arg=counts[WRONG_LOT], timeout=10000)
    _click_the_lot(page, counts, lot=UNCODED_LOT)
    page.wait_for_function(
        "(id) => document.querySelectorAll('#chart [data-ring-count]').length === 1"
        " && document.querySelector('#chart [data-ring-count]')"
        ".getAttribute('data-ring-count') === String(id)",
        arg=counts[UNCODED_LOT], timeout=10000)


def test_clicking_another_lot_moves_the_panel_and_nothing_else(page, plant):
    """"Click another dot, the panel follows. Nothing else moves." - the ask
    from #143, which holds on this chart too. The chart keeps its size and the
    facts row keeps its numbers."""
    _base, counts = plant
    measure = """() => ({
             chart: document.querySelector('#chart').getBoundingClientRect().width,
             facts: document.querySelector('.object-facts').textContent,
             verdict: document.querySelector('#verdict').textContent,
             dots: document.querySelectorAll('#chart circle[data-count]').length,
           })"""
    before = page.evaluate(measure)
    _click_the_lot(page, counts)
    _click_the_lot(page, counts, lot=UNCODED_LOT)
    assert page.evaluate(measure) == before


# ----------------------------------------------------------------- four themes


@pytest.mark.parametrize("theme", THEMES)
def test_the_lot_panel_reads_in_all_four_themes(admin, plant, theme):
    """Style rule 3. A change is not done until it looks right in all four,
    and "looks right" begins with nothing on the panel - or on the staircase
    beside it - resolving to a colour the palette does not define."""
    base, counts = plant
    page = _open_spc(admin, base, theme=theme)
    try:
        _click_the_lot(page, counts)
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
        assert got["charts"] == 3, (
            "the lot panel's own pictures: the station's timeline and its two "
            "process values")
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


# -------------------------------------------------------------- and an operator


def test_an_operator_opens_a_lot_the_way_a_supervisor_does(chromium, plant):
    """It is a read of the plant's own records. `plant.read` is the gate on
    every read in this product, and a panel the shop floor cannot open is a
    panel Scott's inspectors do not have - which on this chart would be the
    whole point of it."""
    base, counts = plant
    context = _signed_in(chromium, base, "SCOTT", "operator")
    try:
        page = _open_spc(context, base)
        try:
            _click_the_lot(page, counts)
            text = page.inner_text("#point-body")
            assert "low_fill" in text
            assert "Chiller trip" in text
            assert f"{SENT_BACK} of {SMALLEST}" in text
        finally:
            _close(page)
    finally:
        context.close()


# ------------------------------- and the same chart, explained by the chat


def _explain_question(page):
    """The question the *Explain this chart* link is carrying, decoded off the
    href rather than out of a click, so a test can say what the sentence is
    before anything navigates."""
    href = page.locator("#explain-chart").get_attribute("href")
    parts = urlparse(href)
    assert parts.path == "/dashboard/ai"
    assert parts.fragment == "explore", (
        "the hash is the tab the AI page opens on, so the question may not be "
        f"in it: {href}")
    return parse_qs(parts.query)["ask"][0]


def test_explain_this_chart_asks_about_the_counted_characteristic(page):
    """The names travel in the sentence and nothing else travels at all. So
    the chat reads this plant itself and the figures in its answer are the
    plant's, not this screen's copy of them."""
    asked = _explain_question(page)
    assert "FG-COLA" in asked and "sent_back" in asked
    assert "lot" not in asked, "no lot is open, so none is asked about"


def test_with_a_lot_open_it_asks_why_that_lot_is_where_it_is(page, plant):
    """Somebody with the panel open is already holding the second question.
    The link picks it up - by the count's own id, which is what the chart's
    tool takes."""
    _base, counts = plant
    _click_the_lot(page, counts)
    asked = _explain_question(page)
    assert f"why is inspected lot {counts[WRONG_LOT]} where it is" in asked
    assert "sent_back" in asked
