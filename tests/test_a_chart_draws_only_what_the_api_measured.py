"""The chart contract, in a browser, in all four themes.

House rule 6: a rendered chart can be completely convincing and completely
wrong, so a visualisation is looked at with real data and then pinned with a
test. This is the pinning half for `kit.js`'s five shapes — a line series,
bars and the pareto, the state timeline, a histogram and the network graph of
`docs/design/deep-analysis.md` §7 — against the six rules of
`docs/design/agentic-harness.md` §9 M1 and the chart contract in
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

And, since 2026-09-29, the seventh thing the frame does for every shape: what
the READER narrowed the picture to — a hover, a legend switch, a threshold, a
brushed axis — re-states the total, in `data-total` and in the footer. A
filtered chart that kept the old total is a list that reads complete.

Each shape is fed a FIXED envelope, so what is asserted is the drawing and not
the plant: a live plant's numbers change between runs, and a test that has to
be re-read every time is a test nobody reads. The plant is here for the last
test, which is the one that matters most — that a screen draws through this
kit, so the kit and the screens cannot drift apart.

Same shape as `test_ui_oee_above_rated.py`: a seeded plant on a loopback port
of the operating system's choosing, driven by Chromium, touching nothing
anybody else is running.

The interaction tests drive a REAL pointer — `page.mouse`, so the affordance
is exercised and not just the function behind it — and every one of them then
waits on the state it is about to assert (`wait_for_function` on the chart's
own `data-total` or `data-hover-value`), never on the element that will hold
it and never on a sleep. Three tests in three days went green on loopback and
red on a slower machine for exactly that reason (#112, #113).
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

#: The network of `docs/design/deep-analysis.md` §7, as its step-5 worked
#: example draws it: what the trace recorded, what the floor recorded, the two
#: holes that exist so the gaps are things with numbers on them, and three node
#: kinds the model declares that this plant has no record of at all.
#:
#: Every edge here is a recorded fact. There is deliberately NO edge from a
#: question to a stop: nothing in this product links the two, and the single
#: most important honesty rule on that page is that the picture must not draw
#: the link a plant manager most wants drawn.
GRAPH = {
    "window": dict(WINDOW, clamped=False, hours=2.0, requested_hours=2),
    "nodes": [
        {"id": "role:operator", "kind": "role", "label": "operator",
         "weight": 6, "degree": 2},
        {"id": "q:label-a-stop", "kind": "question_group",
         "label": "how do I label a stop", "weight": 41, "degree": 3},
        {"id": "q:next-order", "kind": "question_group",
         "label": "what is my next order", "weight": 22, "degree": 2},
        # Three turns with no person on them. Its number is its DEGREE.
        {"id": "unattributed", "kind": "unattributed", "label": "unattributed",
         "weight": 3, "degree": 1},
        {"id": "eq:FILL01", "kind": "machine", "label": "FILL01",
         "weight": 2420, "degree": 3},
        {"id": "eq:CAP02", "kind": "machine", "label": "CAP02",
         "weight": 900, "degree": 1},
        {"id": "reason:mechanical", "kind": "downtime_reason", "label": "mechanical",
         "weight": 2140, "degree": 2},
        # 1 180 stopped seconds nobody named. Its number is its DEGREE too.
        {"id": "unlabelled", "kind": "unlabelled", "label": "unlabelled",
         "weight": 1180, "degree": 1},
        {"id": "mo:MO-0344", "kind": "maintenance_order", "label": "MO-0344",
         "weight": 46, "degree": 1},
    ],
    "edges": [
        {"from": "role:operator", "to": "q:label-a-stop", "kind": "asked", "weight": 41},
        {"from": "unattributed", "to": "q:label-a-stop", "kind": "asked", "weight": 3},
        {"from": "role:operator", "to": "q:next-order", "kind": "asked", "weight": 22},
        {"from": "q:label-a-stop", "to": "q:next-order", "kind": "followed_by",
         "weight": 9},
        {"from": "eq:FILL01", "to": "reason:mechanical", "kind": "stopped_with",
         "weight": 1240, "unit": "seconds", "watched_seconds": 7200},
        {"from": "eq:CAP02", "to": "reason:mechanical", "kind": "stopped_with",
         "weight": 900, "unit": "seconds", "watched_seconds": 7200},
        # Seconds with no record of how much of the window anybody watched. The
        # harness's §6 rule 4: the number cannot be checked, so it is not
        # printed and the edge is drawn as the unknown it is.
        {"from": "eq:FILL01", "to": "unlabelled", "kind": "stopped_with",
         "weight": 1180, "unit": "seconds"},
        {"from": "eq:FILL01", "to": "mo:MO-0344", "kind": "repaired_by", "weight": 46},
    ],
    "node_kinds": [
        {"kind": "role", "nodes": 1},
        {"kind": "workcenter", "nodes": 0, "note": "no person records one",
         "unknown": True},
        {"kind": "question_group", "nodes": 2},
        {"kind": "screen", "nodes": 0, "note": "no question records one",
         "unknown": True},
        {"kind": "machine", "nodes": 2},
        {"kind": "downtime_reason", "nodes": 1},
        {"kind": "maintenance_order", "nodes": 1},
        {"kind": "shift", "nodes": 0, "note": "no shift stamp reaches a turn"},
        {"kind": "unattributed", "nodes": 1},
        {"kind": "unlabelled", "nodes": 1},
    ],
    "measures": {
        "threshold": 3,
        "components": 2,
        "biggest": {"label": "labelling a stop", "weight": 41, "of_weight": 380,
                    "unit": "turns",
                    "touches": ["role", "question_group", "unattributed"],
                    "absent": ["downtime reason", "machine"]},
        "refused": "betweenness, PageRank and eigenvector centrality are refused — "
                   "a centrality over an edge set that is whatever happens to be "
                   "recorded is a number with no meaning",
    },
    "showing": {"nodes": 9, "edges": 8},
    "total": {"nodes": 340, "edges": 20},
}

#: kind, envelope, options — the five shapes as a screen or the analysis agent
#: would ask for them.
SHAPES = {
    "pareto": ("pareto", PARETO, {"labelWidth": 96, "noun": "reason"}),
    "states": ("states", STATES, {}),
    "line": ("line", LINE, {"value": "mean", "band": {"low": "min", "high": "max"},
                            "zero": False, "y": {"label": "temperature °C"}}),
    "histogram": ("histogram", HISTOGRAM, {}),
    "graph": ("graph", GRAPH, {"height": 300}),
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
        /* Pinned to the corner of the viewport and above everything: the
           interaction tests below drive a real pointer at these marks, and a
           probe that sat below the fold of a screen full of panels could not
           be clicked at all. */
        box.style.position = 'fixed';
        box.style.top = '0';
        box.style.left = '0';
        box.style.zIndex = '9999';
        box.style.background = 'var(--panel)';
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
        footer: [...node.querySelectorAll('text[data-footer]')]
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
        bands: [...node.querySelectorAll('polygon.trend-band')]
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
        # Edges in the order the envelope carried them, then the nodes, then
        # the kinds this plant has no node of. The two holes carry their
        # DEGREE — 1 each — and not their 3 turns and 1 180 seconds, because
        # what a hole is, is what it touches (§7).
        "graph": ["41", "3", "22", "9", "1240", "900", "1180", "46",
                  "6", "41", "22", "1", "2420", "900", "2140", "1", "46",
                  "0", "0", "0"],
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
                                         ("pareto", True), ("histogram", True),
                                         ("graph", True)])
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
    # And the min/max band breaks in the same place. The first draft drew it as
    # one polygon across the hole, painted over the hatch that said there was
    # nothing there - on the daylight palette it was the most visible thing on
    # the chart, and it was a spread for half an hour nobody measured.
    assert len(got["bands"]) == 2, (
        f"{len(got['bands'])} band(s) - the spread must break where the readings do")


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


def test_a_kind_the_kit_does_not_have_is_refused_by_name(page):
    """And a name that happens to be on every object in JavaScript is refused
    too: `SHAPES["constructor"]` is a function, and a lookup that did not ask
    whether the key was its own would have called it as a chart shape."""
    for kind in ("sunburst", "constructor", "toString"):
        refused = page.evaluate(
            """(kind) => { try { FS.kit.chart(kind, {window: {start: 'a', end: 'b'}});
                                 return null; }
                           catch (e) { return String(e); } }""", kind)
        assert refused and "no such chart kind" in refused, (
            f"kit.chart({kind!r}) was not refused: {refused!r}")


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


# ------------------------------------------------ the network graph (§7)

#: Everything about the drawn graph a test needs, read in ONE pass inside the
#: page for the same reason `DRAW` is: a handle taken and then read across two
#: calls is a handle into a document that may have been redrawn in between.
GRAPH_FACTS = """() => {
    const node = document.querySelector('#fs-chart-probe svg.fs-chart');
    const attrs = (el, names) => Object.fromEntries(
        names.map((n) => [n, el.getAttribute(n)]));
    return {
        total: node.getAttribute('data-total'),
        coverage: node.getAttribute('data-coverage'),
        coverageKind: node.getAttribute('data-coverage-kind'),
        filtered: node.getAttribute('data-filtered'),
        desc: node.querySelector('desc').textContent,
        footer: [...node.querySelectorAll('text[data-footer]')]
            .map((t) => ({cls: t.getAttribute('class'), text: t.textContent})),
        nodes: [...node.querySelectorAll('[data-node]')].map((e) => ({
            ...attrs(e, ['data-node', 'data-node-kind', 'data-label', 'data-value',
                         'data-degree', 'data-unknown', 'fill']),
            cx: Number(e.getAttribute('cx')), cy: Number(e.getAttribute('cy')),
            cls: e.getAttribute('class'),
        })),
        edges: [...node.querySelectorAll('[data-edge-kind]')].map((e) => attrs(e,
            ['data-edge-kind', 'data-from', 'data-to', 'data-value',
             'data-watched', 'data-unknown'])),
        empties: [...node.querySelectorAll('[data-empty="true"]')].map((e) => attrs(e,
            ['data-node-kind', 'data-label', 'data-value', 'data-unknown'])),
        labels: [...node.querySelectorAll('text.graph-label')].map((t) => t.textContent),
        legend: [...node.querySelectorAll('[data-legend]')]
            .map((e) => e.getAttribute('data-legend')),
        markup: node.outerHTML,
    };
}"""


def graph(page, theme="control-room"):
    drawn(page, "graph", theme)
    return page.evaluate(GRAPH_FACTS)


def test_the_graph_draws_the_edges_somebody_recorded_and_no_others(page):
    """§7's first rule, and the harness's §6 rule 1 in the medium where it is
    hardest to keep: an edge nobody observed is not an edge. Every line on this
    picture is one row of the envelope, between two nodes the envelope carried,
    and nothing joins two nodes because they ended up near each other."""
    got = graph(page)
    assert len(got["nodes"]) == len(GRAPH["nodes"])
    assert len(got["edges"]) == len(GRAPH["edges"])
    ids = {n["id"] for n in GRAPH["nodes"]}
    for edge in got["edges"]:
        assert edge["data-from"] in ids and edge["data-to"] in ids, edge
    recorded = {(e["from"], e["to"], e["kind"]) for e in GRAPH["edges"]}
    assert {(e["data-from"], e["data-to"], e["data-edge-kind"])
            for e in got["edges"]} == recorded


def test_a_node_kind_this_plant_records_nothing_of_is_drawn_empty_not_omitted(page):
    """§1 step 5, and rule 5 applied to a KIND of thing rather than a count. A
    graph that quietly left `screen` and `workcenter` out would read as a
    complete picture of a plant where the questions came from nowhere. They are
    on the picture, with the zero the envelope stated and a name."""
    got = graph(page)
    empty = {k["data-node-kind"]: k for k in got["empties"]}
    assert set(empty) == {"workcenter", "screen", "shift"}, (
        f"the kinds drawn empty were {sorted(empty)}")
    for kind in empty.values():
        assert kind["data-value"] == "0", "an empty kind must carry its own zero"
        assert kind["data-label"], "an empty kind is drawn with no name on it"
    printed = " ".join(f["text"] for f in got["footer"])
    assert "declared by the model and recorded by nothing on this plant" in printed
    assert "3 node kinds declared and empty here" in got["total"]


def test_the_unattributed_node_carries_its_degree_because_a_hole_is_what_it_touches(page):
    """§7: the `unattributed` and `unlabelled` nodes carry their DEGREE, so the
    hole is a thing on the picture with a number on it rather than a tidy graph
    that happens to be three turns short. Degree and weight are different
    numbers here on purpose — three turns, one edge — so a shape that wrote the
    weight out of habit would fail this."""
    got = graph(page)
    holes = {n["data-node"]: n for n in got["nodes"]
             if n["data-node"] in ("unattributed", "unlabelled")}
    assert set(holes) == {"unattributed", "unlabelled"}
    assert holes["unattributed"]["data-value"] == "1", "that is the weight, not the degree"
    assert holes["unattributed"]["data-degree"] == "1"
    assert holes["unlabelled"]["data-value"] == "1"
    for hole in holes.values():
        assert hole["data-unknown"] == "true"
        assert (hole["fill"] or "").startswith("url(#"), (
            f"a hole is painted {hole['fill']} rather than hatched")
    printed = " ".join(f["text"] for f in got["footer"])
    assert "carry their degree and not a weight" in printed


def test_the_graph_never_claims_a_coverage_nobody_measured(page):
    """§7. A graph of records is not a rate over a watched window, and one
    carrying a coverage percentage would be claiming something nobody measured.
    `absent` is the third value `data-coverage` has for exactly this."""
    got = graph(page)
    assert got["coverage"] == "absent"
    assert got["coverageKind"] == "absent"


def test_the_graph_states_what_it_left_out_in_ss7s_own_words(page):
    """Rule 5. "Showing 60 of 340 nodes; 12 edges not drawn" — the sentence the
    design page writes, because a graph of nine nodes out of three hundred and
    forty is not a picture of the plant and nothing else on it says so."""
    got = graph(page)
    assert "showing 9 of 340 nodes" in got["total"], got["total"]
    assert "12 edges not drawn" in got["total"], got["total"]


def test_an_edge_in_seconds_with_no_watched_window_does_not_print_its_number(page):
    """The harness's §6 rule 4, on an edge: an edge labelled with seconds
    carries how much of the window was watched, or it carries nothing. The
    1 180 unlabelled seconds have no watched figure beside them, so the edge is
    drawn as the unknown it is and the footer says why."""
    got = graph(page)
    blind = [e for e in got["edges"] if e["data-unknown"] == "true"]
    assert len(blind) == 1, f"{len(blind)} edges drawn as unknown"
    assert blind[0]["data-to"] == "unlabelled"
    assert blind[0]["data-watched"] is None
    watched = [e for e in got["edges"]
               if e["data-edge-kind"] == "stopped_with" and e["data-watched"]]
    assert len(watched) == 2, "the two stops that DO carry a watched window"
    printed = " ".join(f["text"] for f in got["footer"])
    assert "so the seconds are not printed" in printed


def test_the_graph_prints_the_measures_it_was_given_and_refuses_a_centrality(page):
    """§7's fourth measure, which is a refusal. Betweenness, PageRank and
    eigenvector centrality over an edge set that is *whatever happens to be
    recorded* would be the most convincing wrong number this product could
    show. The chart does not offer one — and what it does print, it prints from
    the envelope: the component count and the biggest cluster are the API's
    arithmetic, never the browser's (rule 1)."""
    got = graph(page)
    printed = " ".join(f["text"] for f in got["footer"])
    assert "2 connected components" in printed, printed
    assert "41 of 380 turns" in printed
    # The absence IS the finding: a cluster with no edge to a stop says so,
    # rather than leaving a silence a plant manager will read a link into.
    assert "it touches no downtime reason" in printed
    assert "centrality" in printed and "refused" in printed
    assert "centrality" not in got["markup"].replace(
        GRAPH["measures"]["refused"], ""), "a centrality reached the markup"


def test_the_layout_is_the_same_picture_every_time_it_is_drawn(page):
    """A force layout seeded from `Math.random` would make a screenshot
    baseline, an export and this test three different pictures of one graph,
    and the first one to disagree would be blamed on the plant. The starting
    positions are a golden-angle spiral seeded by the node's index, and the
    iteration count is fixed."""
    first = graph(page)
    second = graph(page)
    assert [(n["data-node"], n["cx"], n["cy"]) for n in first["nodes"]] == \
           [(n["data-node"], n["cx"], n["cy"]) for n in second["nodes"]]


# ------------------------------------- what the reader narrowed it to (rule 5)

def _box(page, selector):
    """Where a mark is, in the viewport, so a REAL pointer can be put on it."""
    got = page.evaluate(
        """(sel) => { const e = document.querySelector(sel);
                      if (!e) return null;
                      const r = e.getBoundingClientRect();
                      return {x: r.x + r.width / 2, y: r.y + r.height / 2,
                              left: r.x, right: r.right, top: r.y,
                              bottom: r.bottom}; }""", selector)
    assert got, f"nothing on the page matches {selector}"
    return got


def _total(page):
    return page.evaluate(
        "() => document.querySelector('#fs-chart-probe svg.fs-chart')"
        ".getAttribute('data-total')")


def _await_total(page, phrase):
    """Wait for the state being asserted — the chart's own total — and never
    for the element that will hold it."""
    page.wait_for_function(
        """(phrase) => {
            const node = document.querySelector('#fs-chart-probe svg.fs-chart');
            return node && (node.getAttribute('data-total') || '').includes(phrase);
        }""", arg=phrase, timeout=10000)


@pytest.mark.parametrize("name,selector", [
    ("pareto", '#fs-chart-probe [data-label="changeover"]'),
    ("states", '#fs-chart-probe [data-state="running"]'),
    ("line", '#fs-chart-probe circle.series-count[data-value="61.25"]'),
    ("histogram", '#fs-chart-probe [data-from="99.5"]'),
    ("graph", '#fs-chart-probe [data-node="eq:FILL01"]'),
])
def test_hovering_a_mark_says_that_marks_own_number_and_what_was_watched(
        page, name, selector):
    """Rule 1 in the hand and rule 2 beside it. What appears under the pointer
    is the `data-value` the mark already carries — never a number worked out on
    the way to a tooltip — with the chart's coverage sentence under it, so a
    figure and how much of the window it covers are read together."""
    drawn(page, name)
    at = _box(page, selector)
    page.mouse.move(at["x"], at["y"])
    page.wait_for_function(
        """() => { const h = document.querySelector('#fs-chart-probe .chart-hover');
                   return h && h.getAttribute('data-hover-value') !== null; }""",
        timeout=10000)
    said, want = page.evaluate(
        """(sel) => [
            document.querySelector('#fs-chart-probe .chart-hover')
                .getAttribute('data-hover-value'),
            document.querySelector(sel).getAttribute('data-value'),
        ]""", selector)
    assert said == want, f"{name} hovered {want!r} and said {said!r}"
    lines = page.evaluate(
        "() => [...document.querySelectorAll('#fs-chart-probe .chart-hover text')]"
        ".map((t) => t.textContent)")
    assert len(lines) > 1, f"{name} said {lines} — a number with no coverage beside it"


def test_switching_a_kind_off_in_the_legend_takes_it_out_of_the_total_too(page):
    """§7's "what is interactive", and the reason it is one sentence: *every one
    of those re-states the totals*, because a filtered graph that kept the old
    total is a list that reads complete. Clicked with a real pointer, because a
    legend nobody can click is not a legend."""
    drawn(page, "pareto")
    before = _total(page)
    assert "showing 3 of 3 reasons" in before
    at = _box(page, '#fs-chart-probe [data-legend="row:jam"]')
    page.mouse.click(at["x"], at["y"])
    _await_total(page, "showing 2 of 3 reasons")
    after = _total(page)
    assert after != before
    assert "1 switched off in the legend" in after, after
    gone = page.evaluate(
        "() => document.querySelectorAll('#fs-chart-probe [data-label=\"jam\"]').length")
    assert gone == 0, "the row left the total but not the picture"
    printed = page.evaluate(
        "() => [...document.querySelectorAll('#fs-chart-probe text.chart-total')]"
        ".map((t) => t.textContent)")
    assert printed == [after], "the screen and the markup disagree about the total"


def test_moving_the_threshold_re_states_the_total_and_drops_only_lighter_edges(page):
    """§7 again. The threshold is a choice the shape depends on, so rule 4 says
    the chart states it — and rule 5 says the total is of what is drawn. Dragged
    with a real pointer to the far end of its own track; what is asserted is the
    invariant, not a count: every edge still on the picture is at least as heavy
    as the number the handle is sitting on."""
    drawn(page, "graph", "control-room")
    before = _total(page)
    track = _box(page, '#fs-chart-probe [data-chrome="slider-track"]')
    handle = _box(page, "#fs-chart-probe .chart-slider-handle")
    page.mouse.move(handle["x"], handle["y"])
    page.mouse.down()
    page.mouse.move(track["right"] - 1, track["y"], steps=4)
    page.mouse.up()
    page.wait_for_function(
        """(before) => {
            const node = document.querySelector('#fs-chart-probe svg.fs-chart');
            return node && node.getAttribute('data-total') !== before;
        }""", arg=before, timeout=10000)
    got = page.evaluate(GRAPH_FACTS)
    at = float(page.evaluate(
        "() => document.querySelector('#fs-chart-probe .chart-slider-handle')"
        ".getAttribute('data-threshold')"))
    assert at > 0, "the handle did not move"
    for edge in got["edges"]:
        assert float(edge["data-value"]) >= at, (
            f"an edge of {edge['data-value']} survived a threshold of {at}")
    assert len(got["edges"]) < len(GRAPH["edges"])
    assert got["filtered"] == "true"
    notes = [f["text"] for f in got["footer"] if f["cls"] == "chart-filter-note"]
    assert any("threshold" in n for n in notes), notes
    assert f"{len(got['edges'])} of 20 edges drawn" in got["total"], got["total"]


@pytest.mark.parametrize("name,phrase", [("line", "outside the brushed range"),
                                         ("states", "in the brushed range")])
def test_brushing_the_time_axis_re_states_the_total(page, name, phrase):
    """Rule 4's window half, applied to a choice the READER made. A chart zoomed
    into two of its eight hours that kept the old total is a picture claiming to
    be the whole window — so the axis narrows, the readings outside it leave,
    and the footer says which stretch of the clock is on the screen."""
    drawn(page, name)
    before = _total(page)
    surface = _box(page, "#fs-chart-probe [data-brush-surface]")
    page.mouse.move(surface["left"] + 30, surface["y"])
    page.mouse.down()
    page.mouse.move(surface["left"] + (surface["right"] - surface["left"]) * 0.55,
                    surface["y"], steps=6)
    page.mouse.up()
    _await_total(page, phrase)
    after = _total(page)
    assert after != before, "the brush did not change what the chart says it drew"
    notes = page.evaluate(
        "() => [...document.querySelectorAll('#fs-chart-probe text.chart-filter-note')]"
        ".map((t) => t.textContent)")
    assert any("brushed to" in n for n in notes), notes
    said = page.evaluate(
        "() => document.querySelector('#fs-chart-probe svg.fs-chart desc').textContent")
    for note in notes:
        assert note.rstrip(".") in said, (
            "the footer says the axis was brushed and the description does not")


def test_a_brushed_chart_gives_the_whole_window_back_on_a_click(page):
    """The way out, which a filter without one does not have. A drag narrows; a
    single click gives the window back, and the total goes back with it."""
    drawn(page, "line")
    whole = _total(page)
    surface = _box(page, "#fs-chart-probe [data-brush-surface]")
    page.mouse.move(surface["left"] + 30, surface["y"])
    page.mouse.down()
    page.mouse.move(surface["left"] + 260, surface["y"], steps=6)
    page.mouse.up()
    _await_total(page, "outside the brushed range")
    page.mouse.click(surface["left"] + 120, surface["y"])
    page.wait_for_function(
        """(whole) => {
            const node = document.querySelector('#fs-chart-probe svg.fs-chart');
            return node && node.getAttribute('data-total') === whole;
        }""", arg=whole, timeout=10000)
    assert page.evaluate(
        "() => document.querySelector('#fs-chart-probe svg.fs-chart')"
        ".getAttribute('data-filtered')") is None


def test_expanding_a_node_is_an_event_the_page_handles_and_the_kit_draws_nothing(page):
    """§7's "expanding a node into its records" belongs to the page (D5), not to
    the chart: the kit says which node was asked for, with its id and its kind,
    and draws nothing new itself. A chart that went and fetched the records
    would be a chart with a page inside it."""
    drawn(page, "graph")
    page.evaluate(
        """() => { window.__expanded = [];
                   document.querySelector('#fs-chart-probe svg.fs-chart')
                     .addEventListener('fs-chart-expand',
                       (e) => window.__expanded.push(e.detail)); }""")
    # The PLOT, not the whole frame: the click also moved the pointer onto the
    # node, so the tooltip it left is the hover working, not the kit drawing.
    before = page.evaluate(
        "() => document.querySelector('#fs-chart-probe g.chart-plot').innerHTML")
    at = _box(page, '#fs-chart-probe [data-node="q:label-a-stop"]')
    page.mouse.click(at["x"], at["y"])
    page.wait_for_function("() => (window.__expanded || []).length > 0", timeout=10000)
    said = page.evaluate("() => window.__expanded")
    assert said == [{"id": "q:label-a-stop", "kind": "question_group",
                     "label": "how do I label a stop", "value": 41, "degree": 3}]
    after = page.evaluate(
        "() => document.querySelector('#fs-chart-probe g.chart-plot').innerHTML")
    assert after == before, "the kit drew something of its own when a node was clicked"


# ------------------------------------------------------ off the page (§3)

EXPORT = """(async ([kind, format]) => {
    const node = document.querySelector('#fs-chart-probe svg.fs-chart');
    const blob = await FS.kit.export(node, format);
    return {type: blob.type, size: blob.size,
            text: format === 'svg' ? await blob.text() : null};
})"""


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_an_exported_chart_still_states_its_total_and_its_coverage(page, name):
    """§3's rule, and the one worth checking in review: **a chart is
    presentation-ready when its footer survives being pasted into a slide.** An
    export whose coverage sentence was stripped is not an export this product
    makes. The file is the chart's own markup, so the total, the coverage and
    every footer sentence are in it by construction — this is what says they
    stay there."""
    want = drawn(page, name)
    got = page.evaluate(EXPORT, [name, "svg"])
    assert got["type"].startswith("image/svg+xml")
    assert f'data-total="{want["total"]}"' in got["text"].replace("&amp;", "&") \
        or want["total"] in got["text"]
    assert f'data-coverage="{want["coverage"]}"' in got["text"]
    for note in want["footer"]:
        assert note["text"].split("—")[0].strip()[:40] in got["text"].replace(
            "&#8212;", "—"), f"{name}: the footer sentence {note['text']!r} was stripped"


@pytest.mark.parametrize("theme", THEMES)
def test_an_export_carries_the_colour_the_theme_resolved_not_a_variable(page, theme):
    """The picture has to look the same off the page as on it, and a `var(--down)`
    in a file nobody pasted a stylesheet with is a black chart. The colours are
    read back from what the theme already resolved — which is not the same as
    `kit.js` naming one (rule 6): nothing here chooses a colour, it copies the
    one the reader is looking at."""
    drawn(page, "pareto", theme)
    got = page.evaluate(EXPORT, ["pareto", "svg"])
    assert "var(--" not in got["text"], "an unresolved palette variable left the page"
    assert "style=" in got["text"], "the export carries no colours at all"
    # The hatch is a pattern inside the file, so it travels with it.
    assert "<pattern" in got["text"]


def test_an_export_leaves_the_tooltip_behind(page):
    """A tooltip is where somebody's mouse happened to be, which is not a fact
    about the plant. Everything else the reader can see goes into the file —
    the legend and the threshold included, because those are the choices the
    footer is stating."""
    drawn(page, "pareto")
    at = _box(page, '#fs-chart-probe [data-label="changeover"]')
    page.mouse.move(at["x"], at["y"])
    page.wait_for_function(
        """() => { const h = document.querySelector('#fs-chart-probe .chart-hover');
                   return h && h.getAttribute('data-hover-value') !== null; }""",
        timeout=10000)
    got = page.evaluate(EXPORT, ["pareto", "svg"])
    assert "chart-hover" not in got["text"], "the tooltip was exported"
    assert "chart-legend-item" in got["text"], "the legend was not"


def test_a_chart_exports_as_a_png_the_browser_actually_drew(page):
    """PNG is the same SVG through a canvas — no server round trip and no second
    renderer, because a second renderer is the one that drifts (§6 (iii)). It is
    the one asynchronous thing in this kit: the browser has to decode the SVG
    before the canvas can take it, so the export waits on the decode rather than
    hoping it has happened."""
    drawn(page, "graph")
    got = page.evaluate(EXPORT, ["graph", "png"])
    assert got["type"] == "image/png"
    assert got["size"] > 1000, f"a {got['size']}-byte PNG is an empty canvas"


def test_export_refuses_anything_that_is_not_a_chart(page):
    """Including a format it does not have. The refusal names what it wanted,
    because the reason this function takes the chart node rather than an
    envelope is that the footer it has to carry is already on that node."""
    refused = page.evaluate(
        """() => { const out = [];
                   try { FS.kit.export(document.body, 'svg'); } catch (e) { out.push(String(e)); }
                   try { FS.kit.export(
                       document.querySelector('#fs-chart-probe svg.fs-chart'), 'pdf'); }
                   catch (e) { out.push(String(e)); }
                   return out; }""")
    assert len(refused) == 2, f"export accepted something it should not have: {refused}"
    assert "give it a chart" in refused[0]
    assert "no such format" in refused[1]
