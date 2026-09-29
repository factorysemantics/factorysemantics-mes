"""The chart contract, in a browser, in all four themes.

House rule 6: a rendered chart can be completely convincing and completely
wrong, so a visualisation is looked at with real data and then pinned with a
test. This is the pinning half for `kit.js`'s four shapes — a line series,
bars and the pareto, the state timeline and a histogram — against the six
rules of `docs/design/agentic-harness.md` §9 M1 and the chart contract in
`docs/design/STYLE.md`:

1. A chart draws what the API measured. Every number the envelope carried is
   on the chart verbatim, in a `data-value` attribute, and nothing else is.
2. Every figure carries its coverage, and a row below this plant's coverage
   floor is drawn withheld at full width with its ledger — never omitted,
   never averaged away, never as a shorter bar.
3. Unknown is drawn as unknown: hatched, and the line breaks rather than
   being drawn through the hole.
4. Honest axes. A y-axis that does not start at zero says so; a window the
   MES could not fill says what it truncated; a rate carries its denominator;
   a histogram states the bin width its shape depends on.
5. Every chart states its total, including the rows nobody drew.
6. Colour comes from the palette and nowhere else, in all four themes.

Each shape is fed a FIXED envelope, so what is asserted is the drawing and not
the plant: a live plant's numbers change between runs, and a test that has to
be re-read every time is a test nobody reads. The plant is here for the last
test, which is the one that matters most — that a screen draws through this
kit, so the kit and the screens cannot drift apart.

Same shape as `test_ui_oee_above_rated.py`: a seeded plant on a loopback port
of the operating system's choosing, driven by Chromium, touching nothing
anybody else is running.
"""

import socket
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

THEMES = ("control-room", "daylight", "high-contrast", "night-shift")

WINDOW = {
    "hours": 2.1,
    "requested_hours": 8,
    "start": "2026-09-28T10:00:00Z",
    "end": "2026-09-28T12:06:00Z",
    # The MES had not been watching for the eight hours that were asked for.
    "clamped": True,
}

#: A downtime pareto as `/analysis/downtime` returns one, with an unlabelled
#: bucket, a cumulative share, and fifteen minutes nobody was watching that is
#: deliberately NOT one of the reasons.
PARETO = {
    "line": {"code": "LINE1", "name": "Line 1"},
    "window": WINDOW,
    "total_seconds": 15120.0,
    "reasons": [
        {"reason": "changeover", "code": "CO", "seconds": 7200.0, "events": 4,
         "share": 0.4762, "cumulative": 0.4762, "machines": {"MIX01": 7200.0}},
        {"reason": "unlabelled", "code": None, "seconds": 5400.0, "events": 3,
         "share": 0.3571, "cumulative": 0.8333, "machines": {"FIL01": 5400.0}},
        {"reason": "jam", "code": "JAM", "seconds": 2520.0, "events": 9,
         "share": 0.1667, "cumulative": 1.0, "machines": {"PACK01": 2520.0}},
    ],
    "unlabelled_share": 0.3571,
    "from_the_list_seconds": 9720.0, "from_the_list_share": 0.6429,
    "typed_seconds": 0.0, "typed_share": 0.0, "vocabulary_total": 7,
    "unknown_seconds": 900.0, "unknown_share": 0.0198,
    "machines_total": 11,
}

#: A state timeline as `/analysis/timeline` returns one: one machine with a
#: disconnected stretch in the middle of it, and one whose figures this plant's
#: coverage floor withholds.
STATES = {
    "line": {"code": "LINE1", "name": "Line 1"},
    "window": dict(WINDOW, clamped=False, hours=2.0, requested_hours=2),
    "machines": [
        {"code": "MIX01", "name": "Mixer", "intervals": [
            {"state": "running", "reason": None, "start": "2026-09-28T10:00:00Z",
             "end": "2026-09-28T11:00:00Z", "seconds": 3600.0},
            {"state": "disconnected", "reason": "no data from the tag",
             "start": "2026-09-28T11:00:00Z", "end": "2026-09-28T11:20:00Z",
             "seconds": 1200.0},
            {"state": "down", "reason": "jam", "start": "2026-09-28T11:20:00Z",
             "end": "2026-09-28T12:06:00Z", "seconds": 2760.0},
        ]},
        {"code": "FIL01", "name": "Filler",
         "coverage": 0.02, "coverage_floor": 0.2,
         "coverage_note": "watched 2% of this window, below this plant's floor of 20%",
         "ledger": {"observed_seconds": 151.0, "window_seconds": 7560.0,
                    "seconds_by_cause": {"never_connected": 7409.0}},
         "intervals": [
             {"state": "running", "reason": None, "start": "2026-09-28T10:00:00Z",
              "end": "2026-09-28T10:02:31Z", "seconds": 151.0},
         ]},
    ],
    "machines_shown": 2, "machines_total": 11,
    "machines_available": ["MIX01", "FIL01"],
}

#: A tag trend as `/analysis/tag/{code}` returns one, with one bucket in which
#: nothing was read. That hole is the whole reason this shape exists.
LINE = {
    "equipment": "MIX01", "tag": "temperature",
    "window": dict(WINDOW, clamped=False, hours=2.0, requested_hours=2),
    "points": [
        {"t": "2026-09-28T10:15:00Z", "mean": 61.25, "min": 60.1, "max": 62.4, "n": 90},
        {"t": "2026-09-28T10:45:00Z", "mean": 61.9, "min": 61.0, "max": 63.0, "n": 90},
        {"t": "2026-09-28T11:15:00Z", "mean": None, "min": None, "max": None, "n": 0},
        {"t": "2026-09-28T11:45:00Z", "mean": 60.75, "min": 59.5, "max": 61.2, "n": 88},
        {"t": "2026-09-28T12:00:00Z", "mean": 60.4, "min": 59.9, "max": 60.8, "n": 45},
    ],
    "coverage": 0.83,
    "bucket_seconds": 1800.0,
}

#: A distribution. Nothing in the product returns this payload yet; the shape
#: is fed the bins and will not make them, because a bin width decides the
#: shape of the picture and that choice has to be stated, not made quietly in
#: a browser.
HISTOGRAM = {
    "characteristic": "fill weight", "unit": "g", "bin_width": 0.5,
    "n": 338, "n_total": 402, "outside": {"below": 2, "above": 62},
    "lower_spec": 99.0, "upper_spec": 101.5,
    "bins": [
        {"from": 99.0, "to": 99.5, "count": 12},
        {"from": 99.5, "to": 100.0, "count": 148},
        {"from": 100.0, "to": 100.5, "count": None},
        {"from": 100.5, "to": 101.0, "count": 143},
        {"from": 101.0, "to": 101.5, "count": 35},
    ],
}

#: kind, envelope, options — the four shapes as a screen or the analysis agent
#: would ask for them.
SHAPES = {
    "pareto": ("pareto", PARETO, {"labelWidth": 96, "noun": "reason"}),
    "states": ("states", STATES, {}),
    "line": ("line", LINE, {"value": "mean", "band": {"low": "min", "high": "max"},
                            "zero": False, "y": {"label": "temperature °C"}}),
    "histogram": ("histogram", HISTOGRAM, {}),
}


# ------------------------------------------------------------------ the plant

@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A demo plant with a shift's worth of states, stops and readings on it."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine, utcnow
    from fsmes.domain import (
        ConnectionStateName,
        Equipment,
        EquipmentState,
        EquipmentStateName,
        ProductionLog,
        ProductionSource,
        TagValue,
    )
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth, connection, workorders

    path = tmp_path_factory.mktemp("chartkit") / "plant.db"
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
            now = utcnow()
            order = workorders.create(session, code="WO-CHART", material_code="FG-COLA",
                                      quantity=600)
            workorders.release(session, "WO-CHART")
            session.flush()

            def unit(code):
                return session.scalar(select(Equipment).where(Equipment.code == code))

            def state(code, name, *, ago, minutes, reason=None):
                session.add(EquipmentState(
                    equipment_id=unit(code).id, state=name, reason=reason,
                    started_at=now - timedelta(minutes=ago),
                    ended_at=now - timedelta(minutes=ago - minutes)))

            # A shift with something to explain: running, a named changeover,
            # and a stop nobody labelled - which the pareto has to report as
            # unlabelled rather than file under a reason (house rule 3).
            state("MIX01", EquipmentStateName.RUNNING, ago=120, minutes=60)
            state("MIX01", EquipmentStateName.DOWN, ago=60, minutes=20, reason="changeover")
            state("MIX01", EquipmentStateName.DOWN, ago=40, minutes=15)
            state("MIX01", EquipmentStateName.RUNNING, ago=25, minutes=24)
            state("PACK01", EquipmentStateName.RUNNING, ago=120, minutes=100)
            state("PACK01", EquipmentStateName.DOWN, ago=20, minutes=12, reason="jam")
            session.add(ProductionLog(
                work_order_id=order.id, operation_id=order.operations[0].id,
                equipment_id=unit("MIX01").id, good_qty=900.0, scrap_qty=30.0,
                source=ProductionSource.OPC, ts=now - timedelta(minutes=30)))
            # A stretch where the MES could not see the machine at all, so the
            # timeline has an unknown interval to hatch (decision 0030).
            connection.set_connection(
                session, equipment_code="PACK01", state=ConnectionStateName.DISCONNECTED,
                at=now - timedelta(minutes=50), detected_at=now - timedelta(minutes=48),
                reason="no data from the tag")
            connection.set_connection(
                session, equipment_code="PACK01", state=ConnectionStateName.CONNECTED,
                at=now - timedelta(minutes=35))
            for i in range(40):
                session.add(TagValue(
                    equipment_id=unit("MIX01").id, tag="MIX01.temperature",
                    value_num=60.0 + (i % 7) * 0.4,
                    ts=now - timedelta(minutes=120 - i * 3)))
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
def page(plant):
    """The analysis screen, signed in, with its charts drawn.

    The screen is also where the four fixed envelopes are drawn from: it has
    `common.js` and `kit.js` on it and the palette applied, which is exactly
    the context a chart is rendered in for real.
    """
    import json

    sync_playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="the [dev] extra is not installed").sync_playwright

    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            chromium = pw.chromium.launch()
            context = chromium.new_context(viewport={"width": 1400, "height": 1000})
            reply = context.request.post(
                f"{plant}/auth/login",
                data=json.dumps({"code": "ADMIN", "password": "admin"}),
                headers={"Content-Type": "application/json"})
            assert reply.ok, f"sign-in failed: {reply.status}"
            open_page = context.new_page()
            open_page.goto(f"{plant}/dashboard/analysis", wait_until="load", timeout=30000)
            # Wait for the state the assertions need, not for the box that will
            # hold it: the panels fill after four fetches settle, and a
            # selector that exists before they do reads empty on a slow runner.
            open_page.wait_for_function(
                "() => document.querySelectorAll('#pareto svg.fs-chart').length > 0",
                timeout=30000)
            yield open_page
            open_page.close()
            chromium.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


#: Draw one of the four shapes from its fixed envelope and read the facts off
#: the markup. Everything the tests below assert is read in ONE pass inside the
#: page: a handle taken and then read across two calls is a handle into a
#: document that may have been redrawn in between.
DRAW = """([kind, envelope, options, theme]) => {
    document.documentElement.setAttribute('data-theme', theme);
    const host = document.getElementById('fs-chart-probe') || (() => {
        const box = document.createElement('div');
        box.id = 'fs-chart-probe';
        box.style.width = '760px';
        document.body.appendChild(box);
        return box;
    })();
    const node = FS.kit.draw(host, kind, envelope, options);
    const styleOf = ({el, tag}) => {
        const cs = getComputedStyle(el);
        /* A <line> and a <polyline> are strokes, not fills: a browser reports
           black for a fill nobody asked for, and that is not a colour anybody
           chose. Everything else is read on both channels. */
        return {tag, cls: el.getAttribute('class') || '',
                fill: (tag === 'line' || tag === 'polyline') ? null : cs.fill,
                stroke: cs.stroke === 'none' ? null : cs.stroke};
    };
    const marks = [...node.querySelectorAll('rect, circle, polyline, polygon, line, text')]
        .map((el) => ({el, tag: el.tagName.toLowerCase()}));
    return {
        kind: node.getAttribute('data-kind'),
        total: node.getAttribute('data-total'),
        coverage: node.getAttribute('data-coverage'),
        coverageKind: node.getAttribute('data-coverage-kind'),
        carriesUnknown: node.getAttribute('data-carries-unknown'),
        role: node.getAttribute('role'),
        labelledby: node.getAttribute('aria-labelledby'),
        title: node.querySelector('title') ? node.querySelector('title').textContent : null,
        desc: node.querySelector('desc') ? node.querySelector('desc').textContent : null,
        footer: [...node.querySelectorAll('text')]
            .filter((t) => /^chart-/.test(t.getAttribute('class') || ''))
            .map((t) => ({cls: t.getAttribute('class'), text: t.textContent})),
        values: [...node.querySelectorAll('[data-value]')]
            .map((e) => e.getAttribute('data-value')),
        unknownMarks: [...node.querySelectorAll('[data-unknown="true"]')].map((e) => ({
            tag: e.tagName, fill: e.getAttribute('fill') || '',
            withheld: e.getAttribute('data-withheld') || '',
            width: e.getAttribute('width') || '',
        })),
        withheldMarks: [...node.querySelectorAll('[data-withheld="true"]')]
            .map((e) => ({label: e.getAttribute('data-label') || '',
                          width: Number(e.getAttribute('width') || 0)})),
        polylines: [...node.querySelectorAll('polyline.trend-line')]
            .map((p) => p.getAttribute('points')),
        hatchPatterns: node.querySelectorAll('defs pattern').length,
        /* Rule 6: every colour a mark resolves to, and the palette the theme
           says those may come from. Resolved in the page, because a variable
           is only a colour once a browser has applied a theme to it. */
        paints: marks.map(styleOf),
        palette: (() => {
            const probe = document.createElement('span');
            probe.style.display = 'none';
            document.body.appendChild(probe);
            const out = {};
            for (const name of ['--text', '--muted', '--line', '--accent', '--running',
                                '--idle', '--down', '--setup', '--unknown',
                                '--disconnected', '--quality', '--panel', '--panel-2',
                                '--bg', '--track', '--glow']) {
                probe.style.color = `var(${name})`;
                out[name] = getComputedStyle(probe).color;
            }
            probe.remove();
            return out;
        })(),
        width: Number(node.getAttribute('width')),
        height: Number(node.getAttribute('height')),
    };
}"""


def drawn(page, name, theme="control-room"):
    kind, envelope, options = SHAPES[name]
    return page.evaluate(DRAW, [kind, envelope, dict(options, width=760), theme])


# ------------------------------------------------------- rule 1: what it drew

@pytest.mark.parametrize("name", sorted(SHAPES))
def test_every_number_the_envelope_carried_is_on_the_chart_verbatim(page, name):
    """Rule 1, and the one the other five hang off. A chart that worked out its
    own figure would be stating something the MES never measured, in the most
    convincing medium this product has — so the number on each mark is the
    envelope's own string, unrounded and unscaled, and a test can say so."""
    got = drawn(page, name)
    expected = {
        "pareto": ["7200", "5400", "2520"],
        # Every interval's seconds, in the order the clock put them, for the
        # machine that has intervals and the one whose figures are withheld.
        "states": ["3600", "1200", "2760"],
        "line": ["61.25", "61.9", "60.75", "60.4"],
        # The empty bin carries no value, because there was no count to carry.
        "histogram": ["12", "148", "", "143", "35"],
    }[name]
    assert got["values"] == expected, (
        f"{name} drew {got['values']} — every plotted value must be the "
        f"envelope's own number, verbatim")


def test_a_rounded_number_never_reaches_the_markup(page):
    """The specific way rule 1 breaks quietly: a chart that formats a value for
    the axis and then writes the formatted string onto the mark. 61.25 becomes
    61 and nobody can tell the chart from the reading again."""
    got = drawn(page, "line")
    assert "61.25" in got["values"], "the mean the API measured was 61.25"
    assert "61" not in got["values"], "a rounded value reached a data attribute"


# ------------------------------------------------- rule 2: coverage and ledger

def test_a_chart_carries_the_coverage_of_the_envelope_it_was_given(page):
    """Rule 2. Three different facts, told apart: a figure, a figure that could
    not be computed, and a payload with no coverage field on it at all. The
    third is the one a reader would otherwise mistake for the first."""
    assert drawn(page, "line")["coverage"] == "0.83"
    assert drawn(page, "line")["coverageKind"] == "known"
    # /analysis/downtime states its blindness in seconds and a share, because a
    # disconnection is not downtime and must never be sorted beside a reason.
    pareto = drawn(page, "pareto")
    assert pareto["coverageKind"] == "unknown_share"
    assert any("nobody was watching" in f["text"] for f in pareto["footer"]), \
        "the pareto drew the 15 minutes nobody was watching nowhere"
    # /analysis/timeline carries no coverage figure today. The chart says
    # "absent" rather than inventing one or implying full coverage.
    assert drawn(page, "states")["coverage"] == "absent"


def test_a_row_below_the_coverage_floor_is_drawn_withheld_and_not_as_a_smaller_bar(page):
    """Rule 2, and decision 0033. A shorter bar reads as a measurement of a
    machine; what actually happened is that nobody watched it. Full width, in
    the unknown hatch, with the ledger printed — never omitted from the picture
    and never averaged into the others."""
    got = drawn(page, "states")
    assert len(got["withheldMarks"]) == 1, "the withheld machine was not drawn"
    withheld = got["withheldMarks"][0]
    assert withheld["label"] == "FIL01"
    # The plot is 760 less the label gutter and the right margin.
    assert withheld["width"] == 760 - 82 - 14, \
        f"the withheld row is {withheld['width']}px wide, so it reads as a figure"
    assert any("below this plant's floor" in f["text"] for f in got["footer"]), \
        "the withheld row's own note is not on the chart"
    assert "watched 2%" in got["desc"]


def test_a_withheld_row_prints_its_ledger_and_not_only_the_word_withheld(page):
    """Rule 2 asks for the ledger, not just the word. "Nobody watched it" is not
    a finding anybody can act on; "never connected, 2h 03m of a 2h 06m window"
    is — it names the thing to go and fix."""
    got = drawn(page, "states")
    printed = " ".join(f["text"] for f in got["footer"])
    assert "never connected" in printed, printed
    assert "watched of" in printed, "the ledger does not state its own total"


# ------------------------------------------------------ rule 3: unknown drawn

@pytest.mark.parametrize("name,expect", [("line", True), ("states", True),
                                         ("pareto", True), ("histogram", True)])
def test_unknown_is_a_rendering_of_its_own_and_never_a_gap(page, name, expect):
    """Rule 3, and the reason this kit exists rather than a polyline. A hole in
    a chart reads as "nothing happened"; a zero reads as a measurement; a line
    drawn through the hole reads as a measurement of the hole. Hatched, and
    marked so a test — and the AI tab — can tell it apart from a figure."""
    got = drawn(page, name)
    assert got["hatchPatterns"] == 1, "the chart defines no hatch to draw unknown with"
    assert got["unknownMarks"], f"{name} drew nothing as unknown"
    assert got["carriesUnknown"] == "true"
    for mark in got["unknownMarks"]:
        if mark["tag"] == "rect":
            assert mark["fill"].startswith("url(#"), \
                f"an unknown rect is painted {mark['fill']} rather than hatched"


def test_an_unknown_reading_breaks_the_line_rather_than_being_joined_through(page):
    """The specific lie this shape refuses to draw. The envelope has four
    buckets and one of them has no reading; a single polyline through all four
    would draw a temperature for half an hour nobody measured."""
    got = drawn(page, "line")
    assert len(got["polylines"]) == 2, (
        f"{len(got['polylines'])} run(s) of readings — a hole in the middle of a "
        f"series must break the line into two")
    joined = " ".join(got["polylines"])
    assert joined.count(",") == 4, "four readings, in two runs, and no more"
    assert any("no reading" in f["text"] for f in got["footer"]), \
        "the chart draws the hole but does not say what it is"


def test_a_disconnected_stretch_is_hatched_and_not_a_shade_of_grey(page):
    """Decision 0030, on screen. A solid grey block in a Gantt is the same
    shape as a measurement, and this one means nobody was looking. Before
    2026-09-28 the timeline painted it `var(--disconnected)` — a colour that
    tells a reader nothing they can act on."""
    got = drawn(page, "states")
    hatched = [m for m in got["unknownMarks"] if m["tag"] == "rect" and not m["withheld"]]
    assert hatched, "the disconnected interval is not drawn as unknown"
    assert any("lost sight of the machine" in f["text"] for f in got["footer"])


# ------------------------------------------------------- rule 4: honest axes

def test_a_y_axis_that_does_not_start_at_zero_says_so_on_itself(page):
    """Rule 4. A truncated axis turns a one per cent wobble into a cliff, and
    nothing else in the picture tells a reader which of the two they are
    looking at. A process value has no meaningful zero, so this axis is right —
    and it has to admit it."""
    got = drawn(page, "line")
    notes = [f["text"] for f in got["footer"] if f["cls"] == "chart-axis-note"]
    assert any("not 0" in n for n in notes), f"axis notes were {notes}"


def test_a_count_axis_always_starts_at_zero_and_says_that_too(page):
    """The one case rule 4 does not let a caller opt out of: half a bar is half
    a reading, and there is no such thing."""
    got = drawn(page, "histogram")
    notes = [f["text"] for f in got["footer"] if f["cls"] == "chart-axis-note"]
    assert any("from 0" in n for n in notes), f"axis notes were {notes}"
    assert not any("not 0" in n for n in notes)


def test_a_window_the_mes_could_not_fill_says_what_it_truncated(page):
    """Rule 4, the window half, and house rule 1 behind it: time before the MES
    was watching is not downtime. The pareto's envelope asked for eight hours
    and got two."""
    got = drawn(page, "pareto")
    notes = " ".join(f["text"] for f in got["footer"])
    assert "2.10 h" in notes and "8 h" in notes, notes
    assert "not downtime" in notes


def test_a_shape_that_depends_on_a_choice_states_the_choice(page):
    """Rule 4's last clause. A bin width decides what a distribution looks like
    and a bar scale decides what a pareto looks like; a reader who cannot see
    the choice cannot check the picture."""
    histogram = " ".join(f["text"] for f in drawn(page, "histogram")["footer"])
    assert "bins of 0.5 g" in histogram, histogram
    assert "99 g" in histogram and "101.5 g" in histogram
    pareto = " ".join(f["text"] for f in drawn(page, "pareto")["footer"])
    assert "share of the longest" in pareto, pareto


def test_a_rate_carries_its_denominator(page):
    """Rule 4. A count per bucket is a rate, and a rate without its denominator
    is a number nobody can check. The envelope names the bucket it grouped
    into; the axis says it out loud."""
    got = drawn(page, "line")
    notes = " ".join(f["text"] for f in got["footer"])
    assert "30 min bucket" in notes, notes


def test_the_kit_refuses_to_bin_a_series_for_you(page):
    """Rule 1 and rule 4 together, as a refusal. Choosing a bin width is
    choosing the shape of the distribution, so it is the API's choice to make
    and to state — not a choice a browser makes quietly on the way to a
    picture."""
    refused = page.evaluate(
        """() => { try { FS.kit.chart('histogram', {n: 40, values: [1, 2, 3]}); return null; }
                   catch (e) { return String(e); } }""")
    assert refused, "the kit binned a series it was never given bins for"
    assert "does not bin a series" in refused
    assert "the API's choice" in refused


# ------------------------------------------------------- rule 5: every total

@pytest.mark.parametrize("name", sorted(SHAPES))
def test_every_chart_states_its_total_in_text_and_in_the_markup(page, name):
    """Rule 5, which is STYLE.md rule 4 applied to a picture: a chart that does
    not say what it left out looks complete."""
    got = drawn(page, name)
    assert got["total"], f"{name} carries no data-total"
    printed = [f["text"] for f in got["footer"] if f["cls"] == "chart-total"]
    assert printed == [got["total"]], (
        f"{name} says {printed} on the screen and {got['total']!r} in the markup")


def test_a_total_names_what_was_not_drawn(page):
    """The half of rule 5 that matters: "showing 60 of 340". A Gantt of two
    machines on a line of eleven is not a picture of the line, and a reader
    cannot tell from the picture."""
    assert "of 11 machines drawn" in drawn(page, "states")["total"]
    assert "withheld below the coverage floor" in drawn(page, "states")["total"]
    pareto = drawn(page, "pareto")["total"]
    assert "showing 3 of 3 reasons" in pareto and "4h 12m in total" in pareto
    # House rule 3: unlabelled stops are reported as unlabelled, on the chart.
    assert "unlabelled" in pareto
    histogram = drawn(page, "histogram")["total"]
    assert "338 of 402 readings" in histogram
    assert "outside the axis" in histogram
    line = drawn(page, "line")["total"]
    assert "4 readings drawn" in line and "1 with no reading" in line


# ---------------------------------------------- rule 6: the palette, x4 themes

@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("name", sorted(SHAPES))
def test_every_mark_takes_its_colour_from_the_palette(page, name, theme):
    """Rule 6, and STYLE.md rule 2. Every colour on every mark resolves to a
    variable `themes.css` defines — checked by resolving the whole palette in
    the same theme and asking whether the mark's colour is one of them. A hex
    literal in a chart is how four colours escaped theming before the
    four-theme pass caught them, and a chart is the easiest place to hide one.

    All four themes are first-class: a chart that is legible in control-room
    and invisible in daylight is not finished."""
    got = drawn(page, name, theme)
    allowed = set(got["palette"].values()) | {"none", "rgba(0, 0, 0, 0)"}
    assert got["paints"], f"{name} drew no marks at all"
    for paint in got["paints"]:
        for channel in ("fill", "stroke"):
            value = paint[channel]
            if not value or value.startswith("url("):
                continue          # the hatch, which is itself painted from the palette
            assert value in allowed, (
                f"{name} in {theme}: a {paint['tag']}.{paint['cls']} is painted {value}, "
                f"which is not in this theme's palette — colour comes from the palette "
                f"and nowhere else")


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_a_theme_actually_reaches_the_chart(page, name):
    """The check that stops the one above passing vacuously. If the palette did
    not reach the marks at all, every theme would agree — and control-room is a
    dark palette and daylight is a light one, so their text cannot match."""
    dark = drawn(page, name, "control-room")
    light = drawn(page, name, "daylight")
    assert dark["palette"]["--text"] != light["palette"]["--text"]
    dark_paints = {(p["fill"], p["stroke"]) for p in dark["paints"]}
    light_paints = {(p["fill"], p["stroke"]) for p in light["paints"]}
    assert dark_paints != light_paints, (
        f"{name} draws the same colours in a dark theme and a light one, so the "
        f"palette is not reaching its marks")


# --------------------------------------------------------- the screen reader

@pytest.mark.parametrize("name", sorted(SHAPES))
def test_what_a_screen_reader_is_told_is_what_the_screen_says(page, name):
    """STYLE.md asks for the text summary and the total in text. Both are here,
    and the summary is assembled from the same sentences as the visible footer,
    so the two cannot come to disagree — which is the failure mode of every
    hand-written alt text on a chart that changes with its data."""
    got = drawn(page, name)
    assert got["role"] == "img"
    assert got["labelledby"], "the chart names no title or description"
    assert got["title"], "the chart has no <title>"
    assert got["desc"], "the chart has no <desc>"
    assert got["title"] in got["desc"]
    for note in got["footer"]:
        # Trailing punctuation is added for prose; the sentence itself must match.
        assert note["text"].rstrip(".") in got["desc"], (
            f"{name}: the footer says {note['text']!r} and the description does not")


# ----------------------------------------- and a screen draws through the kit

def test_the_analysis_screen_draws_its_pareto_through_the_kit(page):
    """The drift-prevention half, and the reason `kit.js` exists at all: two
    screens must not be able to state coverage differently. Until 2026-09-28
    the analysis screen had its own forty lines of pareto SVG, which drew the
    bars and stated neither the total nor the fifteen minutes nobody was
    watching — both of which were in the payload it was drawing from.

    This is a live plant, so the numbers are not asserted; that the screen's
    own chart is a kit chart carrying a total is."""
    chart = page.locator("#pareto svg.fs-chart")
    assert chart.count() == 1, "the analysis screen's pareto is not a kit chart"
    assert chart.get_attribute("data-kind") == "bars"
    total = chart.get_attribute("data-total")
    assert total and "in total" in total, f"the screen's pareto states no total: {total!r}"
    assert chart.get_attribute("role") == "img"
    # text_content, not inner_text: an SVG <text> is not an HTMLElement, and
    # Playwright says so rather than guessing.
    printed = page.locator("#pareto svg.fs-chart text.chart-total").text_content()
    assert printed == total


def test_the_analysis_screen_draws_its_timeline_through_the_same_shape(page):
    """Four screens draw the state timeline, so it is one implementation — and
    since 2026-09-28 that implementation is the `states` shape above. It used
    to be a second copy of the same layout in this file, which is how a
    disconnected interval came to be hatched in one place and a solid grey
    block in another: the drift between two screens that `kit.js` exists to
    prevent, one level down."""
    page.wait_for_function(
        "() => document.querySelectorAll('#timeline svg.fs-chart').length > 0",
        timeout=15000)
    chart = page.locator("#timeline svg.fs-chart")
    assert chart.get_attribute("data-kind") == "states"
    total = chart.get_attribute("data-total")
    assert "machines drawn" in total, f"the timeline states no total: {total!r}"
