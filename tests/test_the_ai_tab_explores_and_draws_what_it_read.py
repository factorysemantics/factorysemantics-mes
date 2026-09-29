"""Explore on the AI tab, in a browser: the exploration Scott asked for.

`docs/design/deep-analysis.md` §1 is a management question answered end to end -
*"what is the biggest problem for our operators?"* - and §9 D5 is the milestone
that shows it answered. The Python half of this (where a chart's numbers come
from, what reaches the trace, what a reply costs) is in
`tests/test_an_exploration_draws_what_the_plant_measured.py`. This file is the
half that only exists in a browser:

- the chart beside the reply is `kit.js`'s own, and its `data-*` are the
  **tool result's own figures**, not something this page worked out (rule 1);
- the holes are drawn - `unattributed` carries its degree, the empty node kinds
  are on the picture rather than left out;
- the picture comes for a question with no "draw" in it, which is the half of
  §1 the first live run got wrong;
- the reply says what it cost;
- a node expands into a **question put to the agent**, which reads and re-draws,
  and the re-drawn picture re-states its total;
- the export button gives back a file with the coverage footer still in it;
- all four themes;
- and with the agent off, Explore is still there and says why.

The model is scripted in this process - the server runs on a thread of it - so
no key is used, nothing leaves the box, and the reply is the fact under test.

TIMING. One thing here is timing-shaped and is tested as such: the chart arrives
**after** the reply's own fetch, so the assertion that the picture is there is
raced against the page still drawing it. `test_a_chart_arrives_after_a_reply_a_
slow_line_held_back` holds `/assist/agent` back with a route intercept and waits
on the chart's own `data-total`, never on the element that will hold it and
never on a sleep. Everything else in this file is a fact about markup with no
race in it, and is looked at on loopback only.
"""

import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from fsmes.services import agent

pytestmark = [pytest.mark.slow, pytest.mark.browser]

THEMES = ("control-room", "daylight", "high-contrast", "night-shift")

#: How long `/assist/agent` is held back in the one timing-shaped test. Well
#: past anything loopback does on its own, so a pass means the page waited.
DELAY_SECONDS = 0.8

#: The trace graph this plant's `trace_graph` stands in with. Fixed, because
#: what is under test is the drawing and not the plant - and shaped exactly the
#: way the route serves one, holes and empty kinds included.
A_GRAPH = {
    "window": {"hours": 24, "requested_hours": 24, "kept_days": 90},
    "nodes": [
        {"id": "question_group:label a stop", "kind": "question_group",
         "label": "how do I label a stop", "weight": 41, "degree": 3},
        {"id": "role:operator", "kind": "role", "label": "operator",
         "weight": 63, "degree": 2},
        {"id": "machine:FILL01", "kind": "machine", "label": "FILL01",
         "weight": 4220, "degree": 2, "unit": "s"},
        {"id": "unattributed", "kind": "unattributed", "label": "unattributed",
         "weight": 3, "degree": 1},
        {"id": "unlabelled", "kind": "unlabelled", "label": "unlabelled",
         "weight": 1180, "degree": 1, "unit": "s"},
    ],
    "edges": [
        {"from": "role:operator", "to": "question_group:label a stop",
         "kind": "asked", "weight": 41, "watched_seconds": None},
        {"from": "unattributed", "to": "question_group:label a stop",
         "kind": "asked", "weight": 3, "watched_seconds": None},
        {"from": "machine:FILL01", "to": "unlabelled",
         "kind": "stopped_with", "weight": 1180, "watched_seconds": 86400},
    ],
    "nodes_total": 5, "nodes_showing": 5,
    "edges_total": 3, "edges_showing": 3,
    "threshold": 1,
    "node_kinds": [
        {"kind": "question_group", "nodes": 1}, {"kind": "role", "nodes": 1},
        {"kind": "machine", "nodes": 1}, {"kind": "unattributed", "nodes": 1},
        {"kind": "unlabelled", "nodes": 1},
        {"kind": "screen", "nodes": 0,
         "note": "no question on this plant records the screen it was asked from"},
        {"kind": "workcenter", "nodes": 0,
         "note": "nobody here has been placed at a work centre"},
    ],
    "coverage": "absent",
    "coverage_note": "a graph of records, not a share of a window anybody watched",
}

#: The follow-up the thread leads to: the floor's own pareto, unlabelled and all.
A_PARETO = {
    "window": {"hours": 24, "requested_hours": 24, "clamped": False},
    "reasons": [
        {"reason": "mechanical", "seconds": 2140, "machines": ["FILL01", "CAP02"]},
        {"reason": "unlabelled", "seconds": 1180, "machines": ["FILL01"]},
    ],
    "rows_total": 2,
    "total_seconds": 3320,
    "unlabelled_share": 0.3554,
    "unknown_seconds": 3456,
    "unknown_share": 0.04,
    "vocabulary_total": 7,
}

#: What the agent says when a node is expanded. A second read, a second draw,
#: and a graph with one node fewer - so the total it re-states is its own.
A_NARROWER_GRAPH = {
    **A_GRAPH,
    "nodes": A_GRAPH["nodes"][:3],
    "edges": A_GRAPH["edges"][:1],
    "nodes_total": 3, "nodes_showing": 3,
    "edges_total": 1, "edges_showing": 1,
}


def _usage():
    return SimpleNamespace(input_tokens=1200, output_tokens=60,
                           cache_read_input_tokens=900, cache_creation_input_tokens=0)


def _turn(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=_usage())


def _text(words):
    return SimpleNamespace(type="text", text=words)


def _use(id_, tool, **args):
    return SimpleNamespace(type="tool_use", id=id_, name=tool, input=args)


#: The §1 answer this run gives, word for word, so the write-up and the live run
#: on bottling can be compared against the same sentence. A claim, its total, its
#: coverage, and its named silences.
THE_ANSWER = (
    "Over the 24 hours I read, the largest group of operator questions is how to "
    "label a stop — 41 turns, asked from the operator role, and 3 turns carry no "
    "person at all. Those questions sit beside FILL01, which was stopped for "
    "4,220 seconds, of which 1,180 carry no reason at all. The reason vocabulary "
    "they pick from has 7 words in force, and 4% of this window nobody was "
    "watching.\n\n"
    "Three things I cannot tell you. Which screens those questions came from: "
    "this plant records no screen on a question. Which workcenter these people "
    "work at: nobody here has been placed at one, so I grouped by role. And "
    "whether the questions cost any downtime: nothing links a question to a "
    "stop, and putting them side by side because the times are close would be "
    "inventing the link."
)

#: The exchange the whole file is driven by. Round for round, this is the chain
#: the prompt asks for: the trace first, then the graph, then the floor - each
#: read drawn from the payload the plant returned.
FIRST_TURN = [
    _turn(_use("tu_1", "trace_graph", hours=24), stop="tool_use"),
    _turn(_use("tu_2", agent.DRAW_TOOL, **{"from": "tu_1", "shape": "graph",
                                           "title": "Who asks what, and what it touches"}),
          stop="tool_use"),
    _turn(_use("tu_3", "downtime_pareto", hours=24), stop="tool_use"),
    # Named by tool rather than by id, which is the form added on 2026-09-29 after
    # a live model spent two rounds guessing ids. Both forms are played here, so
    # the page is drawn from each of them once.
    _turn(_use("tu_4", agent.DRAW_TOOL, **{"from": "downtime_pareto", "shape": "bars",
                                           "title": "Downtime by reason, worst first"}),
          stop="tool_use"),
    _turn(_text(THE_ANSWER)),
]

EXPANDED = [
    _turn(_use("tu_5", "trace_graph", hours=24, kinds="question_group,role,machine"),
          stop="tool_use"),
    _turn(_use("tu_6", agent.DRAW_TOOL, **{"from": "tu_5", "shape": "graph",
                                           "title": "That cluster on its own"}),
          stop="tool_use"),
    _turn(_text("That cluster is 3 nodes of the 5 in the whole window.")),
]


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A seeded demo plant with the analysis agent on, its reads standing in for
    the plant's own, and a model that plays the exchange above."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("explore") / "plant.db"
    url = f"sqlite:///{path}"
    script: list = []

    def scripted(sess):
        return script.pop(0)

    def served(name, args, *, plant, on_behalf_of=None, dry_run=None, client_ref=None):
        if name == "trace_graph":
            return A_NARROWER_GRAPH if args.get("kinds") else A_GRAPH
        if name == "downtime_pareto":
            return A_PARETO
        return {"plant": plant, "total": 0, "showing": 0}

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        env.setenv("ANTHROPIC_API_KEY", "not-a-key-the-model-is-scripted")
        env.setenv("MES_AGENT_BRAIN", "claude")
        env.setenv("MES_ANALYSIS_BRAIN", "claude")
        env.setattr(agent, "sdk_installed", lambda: True)
        env.setattr(agent, "_call_model", scripted)
        env.setattr(agent, "execute", served)
        env.setattr(agent, "USAGE_FILE", path.parent / "usage.jsonl")
        env.setattr(agent, "TURN_FILE", path.parent / "turns.jsonl")
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
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            port = sock.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            _wait_until_answering(base)
            yield SimpleNamespace(base=base, script=script)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()


def _wait_until_answering(base: str, seconds: float = 20.0) -> None:
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
def playwright():
    sync_playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="the [dev] extra is not installed").sync_playwright
    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            chromium = pw.chromium.launch()
            yield pw, chromium
            chromium.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


def _context(playwright, plant, code, password):
    _pw, chromium = playwright
    context = chromium.new_context(viewport={"width": 1400, "height": 1000})
    reply = context.request.post(
        f"{plant.base}/auth/login",
        data=json.dumps({"code": code, "password": password}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in as {code} failed: {reply.status}"
    return context


@pytest.fixture(scope="module")
def supervisor(playwright, plant):
    """Somebody who holds `audit.read` - the gate on the trace, and so the gate
    on an analysis of it."""
    context = _context(playwright, plant, "ADMIN", "admin")
    yield context
    context.close()


@pytest.fixture(scope="module")
def operator(playwright, plant):
    """Somebody who holds `plant.read` and not `audit.read`. They may not read
    the trace, and must still be able to read their own record."""
    context = _context(playwright, plant, "SCOTT", "operator")
    yield context
    context.close()


def _explore(context, plant, *, delay_reply=False):
    page = context.new_page()
    if delay_reply:
        page.route("**/assist/agent",
                   lambda route: (time.sleep(DELAY_SECONDS), route.continue_()))
    page.goto(f"{plant.base}/dashboard/ai#explore", wait_until="load", timeout=30000)
    page.wait_for_selector("#explore-input", state="visible", timeout=20000)
    return page


def _ask(page, words):
    page.fill("#explore-input", words)
    page.press("#explore-input", "Enter")


def _wait_for_charts(page, how_many, timeout=25000):
    """Wait on the state being asserted - how many charts are drawn - never on
    the box that will hold them."""
    page.wait_for_function(
        "n => document.querySelectorAll('#explore-log svg.fs-chart').length >= n",
        arg=how_many, timeout=timeout)


def _chart(page, index=0):
    return page.locator("#explore-log svg.fs-chart").nth(index)


# ------------------------------------------------- the exploration, end to end

def test_an_exploration_draws_the_graph_then_follows_the_thread_onto_the_floor(
        supervisor, plant):
    """§1, in one exchange. The trace graph is drawn from `trace_graph`'s own
    payload, the pareto from `downtime_pareto`'s, and the sentence beside them
    names what the plant cannot say."""
    plant.script[:] = list(FIRST_TURN)
    page = _explore(supervisor, plant)
    try:
        _ask(page, "what is the biggest problem for our operators?")
        _wait_for_charts(page, 2)

        graph, bars = _chart(page, 0), _chart(page, 1)
        assert graph.get_attribute("class").split()[0] == "fs-chart"

        # Rule 1: every figure on the picture is a figure the payload carried.
        weights = graph.locator("circle.graph-node").evaluate_all(
            "nodes => nodes.map(n => n.getAttribute('data-value'))")
        for node in A_GRAPH["nodes"]:
            expected = str(node["degree"] if node["kind"] in ("unattributed", "unlabelled")
                           else node["weight"])
            assert expected in weights, (
                f"{node['id']} was drawn without the figure the tool returned")

        seconds = bars.locator("[data-value]").evaluate_all(
            "marks => marks.map(m => m.getAttribute('data-value'))")
        for reason in A_PARETO["reasons"]:
            assert str(reason["seconds"]) in seconds

        # And the three silences, in the reply itself.
        said = page.locator("#explore-log .explore-msg.bot").last.inner_text()
        assert "no screen on a question" in said
        assert "placed at one" in said
        assert "inventing the link" in said
    finally:
        page.close()


def test_the_picture_comes_without_the_person_asking_for_one(supervisor, plant):
    """The question Scott typed, and nothing in it says "draw".

    On 2026-09-29 the live run on bottling read the chain, answered well, and
    drew nothing; the same question with the word "draw" in it drew both
    pictures. The prompt says the default now (`agent.ANALYSIS_CHARTS`), the
    suite scores it (`analyst-follows-the-question-from-the-trace-into-the-floor`
    carries `draws`), and this is the assertion that the graph reaches the
    screen: `svg.fs-chart[data-kind="graph"]`, in the log, for a question that
    only asked what the problem is.
    """
    plant.script[:] = list(FIRST_TURN)
    page = _explore(supervisor, plant)
    try:
        question = "what is the biggest problem for our operators?"
        assert "draw" not in question
        _ask(page, question)
        # Wait on the state being asserted - the graph is on the page - and not
        # on the box that will hold it.
        page.wait_for_function(
            "() => document.querySelectorAll("
            "'#explore-log svg.fs-chart[data-kind=\"graph\"]').length >= 1",
            timeout=25000)
        graph = page.locator('#explore-log svg.fs-chart[data-kind="graph"]').first
        assert graph.get_attribute("data-coverage") == "absent"
        # And the pareto beside it, drawn from a `draw` that named the tool
        # rather than the id - the form a guessing model gets right first time.
        _wait_for_charts(page, 2)
        assert _chart(page, 1).get_attribute("data-kind") == "bars"
    finally:
        page.close()


def test_the_graph_draws_its_holes_rather_than_a_tidy_picture_three_turns_short(
        supervisor, plant):
    """The hole is the finding. `unattributed` and `unlabelled` are nodes with a
    degree on them, hatched, and the kinds this plant records nothing of are on
    the picture rather than left off it."""
    plant.script[:] = list(FIRST_TURN)
    page = _explore(supervisor, plant)
    try:
        _ask(page, "what is the biggest problem for our operators?")
        _wait_for_charts(page, 1)
        graph = _chart(page, 0)

        holes = graph.locator('circle.graph-node[data-unknown="true"]')
        drawn = holes.evaluate_all(
            "nodes => nodes.map(n => [n.getAttribute('data-node'), "
            "n.getAttribute('data-degree'), n.getAttribute('data-weight')])")
        assert {row[0] for row in drawn} == {"unattributed", "unlabelled"}
        for node_id, degree, weight in drawn:
            source = next(n for n in A_GRAPH["nodes"] if n["id"] == node_id)
            assert degree == str(source["degree"])
            assert weight == str(source["weight"])

        empties = graph.locator('circle[data-empty="true"]').evaluate_all(
            "nodes => nodes.map(n => n.getAttribute('data-node-kind'))")
        assert "screen" in empties and "workcenter" in empties, (
            "a kind this plant records nothing of was omitted, which reads as a "
            "plant where those questions came from nowhere")

        # Rule 2 seen from the third side: a graph of records claims no coverage.
        assert graph.get_attribute("data-coverage") == "absent"
    finally:
        page.close()


def test_every_reply_says_what_it_cost(supervisor, plant):
    """Cost is on the screen, not in a log. Three numbers: this answer, this
    exploration against what one may spend, and the month."""
    plant.script[:] = list(FIRST_TURN)
    page = _explore(supervisor, plant)
    try:
        _ask(page, "what is the biggest problem for our operators?")
        page.wait_for_function(
            "() => document.querySelector('#explore-cost').textContent.includes('$')",
            timeout=25000)
        line = page.locator("#explore-cost").inner_text()
        assert "That answer cost about $" in line
        assert "this exploration may spend" in line
        assert "this month" in line
        assert "the Console is the bill" in line
    finally:
        page.close()


def test_expanding_a_node_asks_the_agent_and_the_redrawn_picture_restates_its_total(
        supervisor, plant):
    """The second half of "interactive". A node is not a query this page runs -
    it is a question put to the agent, which reads and draws again; and the
    picture that comes back states its own total, not the one before it."""
    plant.script[:] = list(FIRST_TURN)
    page = _explore(supervisor, plant)
    try:
        _ask(page, "what is the biggest problem for our operators?")
        _wait_for_charts(page, 2)
        before = _chart(page, 0).get_attribute("data-total")

        plant.script[:] = list(EXPANDED)
        page.locator('#explore-log circle[data-node="question_group:label a stop"]').click()

        # The click became a question in the person's own column...
        page.wait_for_function(
            """() => [...document.querySelectorAll('#explore-log .explore-msg.me')]
                   .some(line => line.textContent.includes('Expand the question group'))""",
            timeout=20000)
        # ...the agent read again...
        page.wait_for_function(
            "() => document.querySelectorAll('#explore-log svg.fs-chart').length >= 3",
            timeout=25000)
        # ...and the new picture states its own total.
        page.wait_for_function(
            """() => {
                 const charts = document.querySelectorAll('#explore-log svg.fs-chart');
                 return charts[charts.length - 1].getAttribute('data-total') !== null;
               }""", timeout=20000)
        after = _chart(page, 2).get_attribute("data-total")
        assert after != before, (
            "the re-drawn graph kept the total of the one before it, which is a "
            "filtered list reading complete")
        assert "3" in after
    finally:
        page.close()


def test_a_chart_can_be_taken_off_the_page_with_its_coverage_still_on_it(
        supervisor, plant):
    """A chart is presentation-ready when its footer survives being pasted into
    a slide. The button is on the chart; what it hands back still says what the
    picture is of and how much of the window anybody watched."""
    plant.script[:] = list(FIRST_TURN)
    page = _explore(supervisor, plant)
    try:
        _ask(page, "what is the biggest problem for our operators?")
        _wait_for_charts(page, 1)
        buttons = page.locator("#explore-log .explore-chart").first.locator(
            ".chart-tools button")
        assert buttons.count() == 2
        assert [buttons.nth(i).inner_text() for i in range(2)] == ["SVG", "PNG"]

        markup = page.evaluate(
            """async () => {
                 const node = document.querySelector('#explore-log svg.fs-chart');
                 const blob = await FS.kit.export(node, 'svg');
                 return await blob.text();
               }""")
        assert "<svg" in markup and "fs-chart" in markup
        assert 'data-coverage="absent"' in markup
        assert "<desc" in markup, "the exported file lost the sentence a reader is given"
        for node in A_GRAPH["nodes"][:3]:
            assert str(node["weight"]) in markup
    finally:
        page.close()


@pytest.mark.parametrize("theme", THEMES)
def test_an_exploration_is_legible_in_every_theme(supervisor, plant, theme):
    """Four themes are first-class: a chart that reads in control-room and
    disappears in daylight is not finished. Every mark's colour has to resolve to
    something this theme defines."""
    plant.script[:] = list(FIRST_TURN)
    page = _explore(supervisor, plant)
    try:
        page.evaluate("t => document.documentElement.setAttribute('data-theme', t)", theme)
        _ask(page, "what is the biggest problem for our operators?")
        _wait_for_charts(page, 2)
        got = page.evaluate(
            """() => {
                 const style = getComputedStyle(document.documentElement);
                 const palette = {};
                 for (const sheet of document.styleSheets) {
                   let rules;
                   try { rules = sheet.cssRules; } catch (e) { continue; }
                   for (const rule of rules || []) {
                     if (!rule.style) continue;
                     for (const name of rule.style) {
                       if (name.startsWith('--')) {
                         palette[name] = style.getPropertyValue(name).trim();
                       }
                     }
                   }
                 }
                 const resolved = {};
                 const probe = document.createElement('span');
                 document.body.appendChild(probe);
                 for (const [name, value] of Object.entries(palette)) {
                   probe.style.color = '';
                   probe.style.color = `var(${name})`;
                   resolved[name] = getComputedStyle(probe).color;
                 }
                 probe.remove();
                 const paints = [];
                 /* The marks, which is every element that PAINTS. `<title>`
                    and `<desc>` are what a screen reader is given and are never
                    drawn, and a browser reports black for a fill nobody asked
                    for - so reading them would be measuring a colour nothing
                    chose. The same selector `test_a_chart_draws_only_what_the_
                    api_measured.py` uses, for the same reason. */
                 for (const el of document.querySelectorAll(
                        '#explore-log svg.fs-chart rect, '
                        + '#explore-log svg.fs-chart circle, '
                        + '#explore-log svg.fs-chart polyline, '
                        + '#explore-log svg.fs-chart polygon, '
                        + '#explore-log svg.fs-chart line, '
                        + '#explore-log svg.fs-chart text')) {
                   const cs = getComputedStyle(el);
                   const tag = el.tagName.toLowerCase();
                   paints.push({tag, cls: el.getAttribute('class') || '',
                                fill: (tag === 'line' || tag === 'polyline') ? null : cs.fill,
                                stroke: cs.stroke === 'none' ? null : cs.stroke});
                 }
                 return {palette: resolved, paints};
               }""")
        allowed = set(got["palette"].values()) | {"none", "rgba(0, 0, 0, 0)"}
        assert got["paints"], "the exploration drew no marks at all"
        for paint in got["paints"]:
            for channel in ("fill", "stroke"):
                value = paint[channel]
                if not value or value.startswith("url("):
                    continue          # the hatch, itself painted from the palette
                assert value in allowed, (
                    f"in {theme}: a {paint['tag']}.{paint['cls']} is painted {value}, "
                    f"which is not in this theme's palette")
    finally:
        page.close()


# --------------------------------------------------------- the timing-shaped one

def test_a_chart_arrives_after_a_reply_a_slow_line_held_back(supervisor, plant):
    """The one race on this panel, tested against an artificial delay.

    The picture is drawn when `/assist/agent` answers, and on loopback that is
    single-digit milliseconds - which is why every loopback check of this passes
    and a phone over Tailscale is the thing that finds the bug. The reply is held
    back here, and the assertion waits on the chart's own `data-total` rather
    than on the box that will hold it (#112, #113).
    """
    plant.script[:] = list(FIRST_TURN)
    page = _explore(supervisor, plant, delay_reply=True)
    try:
        started = time.monotonic()
        _ask(page, "what is the biggest problem for our operators?")
        # While the line is held: the panel says it is working and draws nothing.
        assert page.locator("#explore-log .explore-msg.thinking").count() == 1
        assert page.locator("#explore-log svg.fs-chart").count() == 0

        page.wait_for_function(
            """() => {
                 const chart = document.querySelector('#explore-log svg.fs-chart');
                 return chart && chart.getAttribute('data-total');
               }""", timeout=30000)
        assert time.monotonic() - started > DELAY_SECONDS, (
            "the reply came back faster than the delay, so the intercept did not "
            "take and a pass here would prove nothing")
        assert page.locator("#explore-log .explore-msg.thinking").count() == 0
    finally:
        page.close()


# ------------------------------------------------------------ the agent off

def test_with_the_analysis_agent_off_explore_is_still_there_and_says_why(
        supervisor, plant, monkeypatch):
    """Shadow mode, a plant with no key, a spent month: all three are states with
    a reason, and none of them is a missing screen. A person who opens Explore
    on a plant where it cannot run is told what would have to change."""
    monkeypatch.setenv("MES_ANALYSIS_BRAIN", "off")
    page = _explore(supervisor, plant)
    try:
        page.wait_for_function(
            """() => document.querySelector('#explore-state')
                     .textContent.includes('analysis agent is off')""", timeout=20000)
        state = page.locator("#explore-state").inner_text()
        assert "off" in state
        # The panel is still a panel: the input is there, and so is the sentence
        # saying what an exploration is.
        assert page.locator("#explore-input").is_visible()
    finally:
        page.close()
        monkeypatch.undo()


# ------------------------------------------------- reciprocity, 0039 clause 4

def test_an_operator_who_may_not_read_the_trace_can_still_read_their_own_record(
        operator, plant):
    """Clause 4. The AI tab is behind `audit.read` and an operator does not hold
    it - and the clause would have promised nothing if their own record were
    behind the same gate. So `My agent` is the one tab they get, it is theirs,
    and the tabs they may not open are not on the page at all."""
    page = operator.new_page()
    try:
        page.goto(f"{plant.base}/dashboard/ai", wait_until="load", timeout=30000)
        page.wait_for_selector('.tab[data-tab="mine"]', state="visible", timeout=20000)
        tabs = page.locator("#ai-tabs .tab").evaluate_all(
            "tabs => tabs.map(t => t.dataset.tab)")
        assert tabs == ["mine"], f"an operator was offered {tabs}"
        # And they can get here by clicking rather than by typing the address:
        # a reciprocity clause with no way in promises nothing.
        assert page.locator('a[href="/dashboard/ai"]').count() >= 1, (
            "an operator has no link to the screen their own record is on")
        assert page.locator('[data-panel="conversations"]').count() == 0
        assert page.locator('[data-panel="explore"]').count() == 0

        page.wait_for_function(
            """() => document.querySelector('#mine-who')
                     .textContent.includes('SCOTT')""", timeout=20000)
        # Their own conversations, and the record of every analysis that named
        # them - which on this plant is none, said as a measurement.
        page.wait_for_function(
            """() => document.querySelector('#named-count')
                     .textContent.trim() !== '—'""", timeout=20000)
        assert "No analysis on this plant has named you" in (
            page.locator("#named-empty").inner_text())
    finally:
        page.close()


def test_a_supervisor_sees_their_own_record_and_only_their_own(supervisor, plant):
    """The same tab for somebody who holds every gate on this page. `My agent`
    is not an operator's screen - it is everybody's own record, and a supervisor
    reading it reads theirs, not the plant's."""
    page = supervisor.new_page()
    try:
        page.goto(f"{plant.base}/dashboard/ai#mine", wait_until="load", timeout=30000)
        page.wait_for_function(
            """() => document.querySelector('#mine-who')
                     .textContent.includes('ADMIN')""", timeout=20000)
        rows = page.locator("#mine-table tbody tr").count()
        if rows:
            # Nothing on this tab belongs to anybody else. Their own turns are
            # the only rows the route will serve, and the screen has no way to
            # ask for another account's.
            assert page.locator("#mine-table tbody tr").evaluate_all(
                "rows => rows.length") == rows
        mine = page.request.get(f"{plant.base}/ai/me?person=SCOTT").json()
        assert mine["person"] == "ADMIN", (
            "the route answered about the account the query string named, which "
            "would make a personal record a way of reading somebody else's")
    finally:
        page.close()
