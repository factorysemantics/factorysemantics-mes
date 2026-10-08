"""The chart follows the chip into the chat, stays pressable, and the answer
ends in buttons.

Scott, 2026-10-08, after using the chip #150 put on the SPC tab: *"When I
clicked the button, I was wanting to ask about a specific data point, but
thought it was hard to type what would have been easy to click … it would be
nice if a clickable graph continued into the chat and somehow still allowed
clicking … could the agent extract potential new paths of research and make
them buttons? … its flat now."*

Four facts, and all four only exist in a browser:

- the chip carries NAMES - the characteristic and the point a panel is open on
  - and the AI tab reads the plant itself, so the chart in the chat is the
  plant's answer and not this screen's copy of one (#150);
- the chart is drawn, and that dot ringed, BEFORE the first question is sent,
  and nothing is spent to draw it: a read the browser is already allowed is
  not a turn;
- a dot in the chat is pressable: it rings, and it writes itself into the
  question box **by sample id**, because `spc_sample` is asked by id and a
  model handed a clock time would have to guess which point that was;
- the answer ends in the two or three questions the model wrote, as buttons,
  and a press asks that question in the SAME conversation. The fence the model
  wrote them in never reaches the screen as words.

The model is scripted in this process - the server runs on a thread of it - so
no key is used and nothing leaves the box. What the model's tool calls return
is the plant's OWN `spc_chart` payload, trimmed exactly as `mcp/quality.py`
trims it, so this file also proves the chart still draws after the readings
were left out of it.

Everything here is looked at on loopback only: there is nothing timing-shaped
in it that the page does not already wait for, and every assertion waits on the
state it asserts - the ringed dot's own `data-sample`, the buttons' own text -
rather than on the box that will hold it (#112, 2026-09-26).

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for, and a Playwright file marked only `slow` is a test nothing runs.
"""

import json
import socket
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy.orm import Session

from fsmes.services import agent

pytestmark = [pytest.mark.slow, pytest.mark.browser]

#: Sixteen settled samples of five and one that came back high, which is the
#: shape the bottling lab has and past this plant's `spc_min_points`. They
#: alternate either side of the centre so the shifted sample is the only dot a
#: rule fires on.
STEADY_LOW = [140.5, 141.0, 141.5, 142.0, 142.5]
STEADY_HIGH = [141.5, 142.0, 142.5, 143.0, 143.5]
SAMPLES = 16
SHIFTED = [144.0, 144.5, 145.0, 145.5, 147.0]

#: The questions the model proposes, word for word. Written as whole questions
#: naming the thing they are about, which is what `ANALYSIS_NEXT` asks for.
NEXT_QUESTIONS = [
    "What was MIX01.ProductTemp doing in the twenty minutes before that sample?",
    "What state was MIX01 in when that sample was taken?",
    "Which gauge took that sample, and when was it last calibrated?",
]

#: An answer that opened something, ending the way the system words ask: the
#: sentences, then a fenced block of questions and nothing after it.
THE_ANSWER = (
    "Sample 17 is out of control: its mean of 145.2 mm is above the upper "
    "control limit, and two of its five bottles are outside the 139-145 mm "
    "specification. The sixteen samples before it sit either side of the "
    "centre line.\n\n"
    "```next\n" + "\n".join(NEXT_QUESTIONS) + "\n```"
)

#: And an answer that opened nothing, which ends at its last sentence.
A_CLOSED_ANSWER = (
    "That sample was taken on HEIGHT-01, calibrated 14 days ago, which is "
    "inside its 180-day interval. There is nothing else on this gauge to read."
)

#: What the answer to a pressed button says. A fourth question is written into
#: its block on purpose: the screen's cap is three, and a model that wrote ten
#: must not fill the panel with buttons.
AFTER_THE_BUTTON = (
    "MIX01.ProductTemp sat at 2 degrees below its usual 62 for the twenty "
    "minutes before that sample, on 240 of 240 five-second readings.\n\n"
    "```next\n"
    "What did the washer do in that window?\n"
    "Was any other characteristic measured in those twenty minutes?\n"
    "Which maintenance orders were open on MIX01 then?\n"
    "Who was signed in on the line at the time?\n"
    "```"
)


def _usage():
    return SimpleNamespace(input_tokens=1200, output_tokens=60,
                           cache_read_input_tokens=900, cache_creation_input_tokens=0)


def _turn(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=_usage())


def _text(words):
    return SimpleNamespace(type="text", text=words)


def _use(id_, tool, **args):
    return SimpleNamespace(type="tool_use", id=id_, name=tool, input=args)


def _reads_the_chart_then_answers(words):
    """The exchange this file is driven by: read the control chart, draw it,
    answer. The same chain the live run makes."""
    return [
        _turn(_use("tu_1", "spc_chart", material="FG-COLA",
                   characteristic="fill_height"), stop="tool_use"),
        _turn(_use("tu_2", agent.DRAW_TOOL,
                   **{"from": "tu_1", "shape": "spc",
                      "title": "FG-COLA fill height"}), stop="tool_use"),
        _turn(_text(words)),
    ]


def _opens_the_sample_then_answers(words):
    return [
        _turn(_use("tu_3", "tag_history", equipment="MIX01",
                   tag="MIX01.ProductTemp"), stop="tool_use"),
        _turn(_text(words)),
    ]


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A bottling-shaped plant whose newest sample came back high, with the
    analysis agent on and its reads standing in for the plant's own.

    `spc_chart` returns what `mcp/quality.py` returns: the chart with the
    readings behind each sample left out and said to be left out. So the chart
    the chat draws here is drawn from the trimmed payload, which is the one the
    model is actually handed.
    """
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine, utcnow
    from fsmes.mcp.quality import _without_sample_readings
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth, gauges, quality
    from fsmes.services import spc as spc_service

    path = tmp_path_factory.mktemp("chatchart") / "plant.db"
    url = f"sqlite:///{path}"
    script: list = []

    def scripted(sess):
        return script.pop(0)

    def served(name, args, *, plant, on_behalf_of=None, dry_run=None,
               client_ref=None):
        if name == "spc_chart":
            with Session(db.get_engine(), expire_on_commit=False) as session:
                chart = spc_service.chart(session, "FG-COLA", "fill_height", 200)
            return {"plant": plant, **_without_sample_readings(chart)}
        return {"plant": plant, "total": 0, "showing": 0,
                "note": f"{name} is not what this file is about"}

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        env.setenv("MES_PLANT_TIMEZONE", "UTC")
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
            gauges.register(session, code="HEIGHT-01", name="Bench height gauge",
                            kind="height gauge", resolution=0.1, interval_days=180,
                            location="MIX01", actor="test")
            gauges.calibrate(session, "HEIGHT-01", result="pass",
                             performed_by="QA-LEAD", performed_on=utcnow().date(),
                             certificate="CERT-HEIGHT-01", actor="test")
            quality.create_spec(session, material_code="FG-COLA",
                                characteristic="fill_height", unit="mm",
                                min_value=139.0, max_value=145.0, sample_size=5,
                                actor="test")

            def sample(values):
                row, _checks, _nc, _signals = quality.record_sample(
                    session, material_code="FG-COLA", characteristic="fill_height",
                    values=values, gauge_code="HEIGHT-01", equipment_code="MIX01",
                    actor="OP-NIGHT")
                session.flush()
                return row.id

            settled = [sample(STEADY_LOW if turn % 2 else STEADY_HIGH)
                       for turn in range(SAMPLES)]
            shifted = sample(SHIFTED)
            session.commit()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(
            create_app(), log_level="warning", lifespan="on"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]),
                                  daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{sock.getsockname()[1]}"
            _wait_until_answering(base)
            yield SimpleNamespace(base=base, script=script, shifted=shifted,
                                  settled=settled)
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


@pytest.fixture(scope="module")
def admin(chromium, plant):
    context = chromium.new_context(viewport={"width": 1500, "height": 1100})
    reply = context.request.post(
        f"{plant.base}/auth/login",
        data=json.dumps({"code": "ADMIN", "password": "admin"}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in failed: {reply.status}"
    yield context
    context.close()


#: How many samples the chart draws.
DRAWN = SAMPLES + 1


def _open_spc(context, plant):
    """The SPC page on fill height, with the chart drawn."""
    page = context.new_page()
    page.goto(f"{plant.base}/dashboard/spc?spec=FG-COLA%7Cfill_height",
              wait_until="load", timeout=30000)
    page.wait_for_function(
        "n => document.querySelectorAll("
        " `#chart circle[data-series='sample-range']`).length === n",
        arg=DRAWN, timeout=30000)
    return page


def _chip_from_an_open_sample(context, plant, sample):
    """The SPC page with a sample's panel open, and the chip's address."""
    page = _open_spc(context, plant)
    try:
        page.locator(f'#chart circle[data-series="xbar"][data-sample="{sample}"]').click()
        page.wait_for_function(
            "want => document.querySelector('#point-panel').dataset.sample === want",
            arg=str(sample), timeout=20000)
        return page.locator("#explain-chart").get_attribute("href")
    finally:
        page.close()


def _explore(context, plant, where):
    page = context.new_page()
    page.goto(f"{plant.base}{where}", wait_until="load", timeout=30000)
    page.wait_for_selector("#explore-input", state="visible", timeout=20000)
    return page


def _wait_for_the_chart(page, how_many=1):
    page.wait_for_function(
        "n => document.querySelectorAll('#explore-log svg.fs-chart').length >= n",
        arg=how_many, timeout=25000)


def _ask(page):
    page.press("#explore-input", "Enter")


def _next_buttons(page):
    return page.locator("#explore-log .explore-next .next-row button")


# ------------------------------------------------- the chip carries the chart


def test_the_chip_carries_the_characteristic_and_the_point_a_panel_is_open_on(
        admin, plant):
    """Names and nothing else: no payload rides in the address and nothing is
    scraped off the page it came from (#150). The question is still in it, so
    somebody who presses it with no panel open still arrives with a question."""
    href = _chip_from_an_open_sample(admin, plant, plant.shifted)
    parts = urlparse(href)
    query = parse_qs(parts.query)
    assert parts.path == "/dashboard/ai"
    assert parts.fragment == "explore", "the hash is the tab FS.tabs reads"
    assert query["spec"] == ["FG-COLA/fill_height"]
    assert query["sample"] == [str(plant.shifted)]
    assert "check" not in query, (
        "a sample's panel was open, so there is no reading to carry")
    assert f"sample {plant.shifted}" in query["ask"][0]
    # Rule 1 of this address: nothing of the chart itself travels in it.
    for number in ("145.2", "147.0", "points", "control"):
        assert number not in href, f"{number} travelled in the address"


def test_the_chat_opens_with_the_chart_drawn_that_dot_ringed_and_nothing_spent(
        admin, plant):
    """Before the first question is sent. The chart is the plant's own answer,
    read by this tab; the ring is on the dot whose panel was open; and no turn
    was spent to put either on the screen."""
    href = _chip_from_an_open_sample(admin, plant, plant.shifted)
    page = _explore(admin, plant, href[href.index("/dashboard/ai"):])
    try:
        _wait_for_the_chart(page)
        figure = page.locator("#explore-log figure.explore-chart.explore-pinned")
        assert figure.count() == 1, "the chart somebody arrived with is not pinned"
        assert figure.get_attribute("data-shape") == "spc"

        # The ring is the kit's own, on the dot the panel was open on.
        ringed = page.locator("#explore-log circle.spc-selected")
        assert ringed.count() == 1
        assert ringed.get_attribute("data-sample") == str(plant.shifted)

        # The question is typed and not sent, and nothing has been asked yet.
        typed = page.locator("#explore-input").input_value()
        assert f"sample {plant.shifted}" in typed
        assert page.locator("#explore-log .explore-msg").count() == 0, (
            "a turn was spent before the person pressed anything")
        assert page.locator("#explore-cost").inner_text().strip() == ""
    finally:
        page.close()


def test_the_chart_in_the_chat_draws_from_the_payload_the_model_was_handed(
        admin, plant):
    """The pinned chart and the one the model drew are the same picture from
    the same arithmetic - and the model's copy has had the readings behind
    every sample trimmed out of it, so this is also the proof that the trim did
    not take the chart with it."""
    plant.script[:] = _reads_the_chart_then_answers(THE_ANSWER)
    href = _chip_from_an_open_sample(admin, plant, plant.shifted)
    page = _explore(admin, plant, href[href.index("/dashboard/ai"):])
    try:
        _wait_for_the_chart(page, 1)
        _ask(page)
        _wait_for_the_chart(page, 2)
        pinned, drawn = (page.locator("#explore-log svg.fs-chart").nth(i)
                         for i in (0, 1))
        for chart in (pinned, drawn):
            means = chart.locator('circle[data-series="xbar"]').evaluate_all(
                "dots => dots.map(d => d.getAttribute('data-value'))")
            assert len(means) == DRAWN
            assert "145.2" in means, (
                "the shifted sample's own mean is not on the picture")
            assert chart.locator("line.limit-line").count() >= 2, (
                "a control chart with no control limits is the #150 failure")
    finally:
        page.close()


# ------------------------------------------------- the dots stay pressable


def test_pressing_a_dot_in_the_chat_rings_it_and_names_the_sample_in_the_question(
        admin, plant):
    """Scott: *"it was hard to type what would have been easy to click."* The
    press writes the sentence, by sample id, and moves the ring onto the dot
    the sentence is about. Nothing is fetched and no turn is spent."""
    href = _chip_from_an_open_sample(admin, plant, plant.shifted)
    page = _explore(admin, plant, href[href.index("/dashboard/ai"):])
    try:
        _wait_for_the_chart(page)
        before = page.locator("#explore-input").input_value()
        another = plant.settled[4]
        page.locator("#explore-log "
                     f'circle[data-series="xbar"][data-sample="{another}"]').click()
        page.wait_for_function(
            """want => document.querySelector('#explore-input').value.includes(want)""",
            arg=f"(sample {another})", timeout=10000)

        typed = page.locator("#explore-input").input_value()
        assert typed.startswith(before), "the question somebody arrived with was lost"
        assert "— and the one at " in typed
        # By id, because `spc_sample` is asked by id and never by a clock time.
        assert f"(sample {another})" in typed

        # The ring followed the press, and there is still only one.
        page.wait_for_function(
            """want => {
                 const ringed = document.querySelectorAll('#explore-log circle.spc-selected');
                 return ringed.length === 1 && ringed[0].dataset.sample === want;
               }""", arg=str(another), timeout=10000)
        assert page.locator("#explore-log .explore-msg").count() == 0
        assert page.locator("#explore-cost").inner_text().strip() == ""

        # Pressed again, the same dot says nothing new.
        page.locator("#explore-log "
                     f'circle[data-series="xbar"][data-sample="{another}"]').click()
        page.wait_for_timeout(200)
        assert page.locator("#explore-input").input_value() == typed
    finally:
        page.close()


def test_a_chart_the_chat_drew_itself_is_pressable_too(admin, plant):
    """Not only the pinned one. A control chart the model drew later in the
    same conversation presses the same way, because it is the same shape drawn
    by the same call."""
    plant.script[:] = _reads_the_chart_then_answers(THE_ANSWER)
    page = _explore(admin, plant, "/dashboard/ai#explore")
    try:
        page.fill("#explore-input", "how does the fill height chart look?")
        _ask(page)
        _wait_for_the_chart(page)
        assert page.locator(
            "#explore-log figure.explore-pinned").count() == 0, (
            "nothing was arrived with, so nothing is pinned")
        dot = page.locator("#explore-log "
                           f'circle[data-series="xbar"][data-sample="{plant.shifted}"]')
        assert dot.get_attribute("role") == "button", (
            "a dot on the chart the chat drew is not pressable")
        dot.click()
        page.wait_for_function(
            "want => document.querySelector('#explore-input').value.includes(want)",
            arg=f"(sample {plant.shifted})", timeout=10000)
        assert page.locator("#explore-input").input_value().startswith("Why is the one at ")
    finally:
        page.close()


# ------------------------------------------------- and the answer ends in buttons


def test_the_answer_ends_in_the_questions_the_model_wrote_as_buttons(admin, plant):
    """Two or three, in the model's own words, under the answer. And the fence
    it wrote them in is not on the screen: unrendered is cosmetic, mangled is
    not, and a fence in the middle of a sentence is both."""
    plant.script[:] = _reads_the_chart_then_answers(THE_ANSWER)
    page = _explore(admin, plant, "/dashboard/ai#explore")
    try:
        page.fill("#explore-input", "how does the fill height chart look?")
        _ask(page)
        page.wait_for_function(
            "() => document.querySelectorAll("
            " '#explore-log .explore-next .next-row button').length > 0",
            timeout=25000)
        buttons = _next_buttons(page)
        assert 2 <= buttons.count() <= 3
        assert buttons.evaluate_all("all => all.map(b => b.textContent)") \
            == NEXT_QUESTIONS

        said = page.locator("#explore-log .explore-msg.bot").last.inner_text()
        assert "out of control" in said, "the answer itself is still there"
        assert "```" not in said, "the fence reached the screen as words"
        assert "next" not in said.split("\n")[-1].lower()
        for question in NEXT_QUESTIONS:
            assert question not in said, (
                "the questions are on the screen twice - as words and as buttons")
    finally:
        page.close()


def test_pressing_a_button_asks_that_question_in_the_same_conversation(
        admin, plant):
    """The point of the buttons. The press is a turn of the conversation
    already running - same session - and the answer that comes back carries its
    own questions."""
    plant.script[:] = _reads_the_chart_then_answers(THE_ANSWER)
    page = _explore(admin, plant, "/dashboard/ai#explore")
    asked: list = []
    page.on("request", lambda request: (
        asked.append(json.loads(request.post_data or "{}"))
        if request.method == "POST" and request.url.endswith("/assist/agent")
        else None))
    try:
        page.fill("#explore-input", "how does the fill height chart look?")
        _ask(page)
        page.wait_for_function(
            "() => document.querySelectorAll("
            " '#explore-log .explore-next .next-row button').length > 0",
            timeout=25000)
        first = page.locator("#explore-cost").inner_text()

        plant.script[:] = _opens_the_sample_then_answers(AFTER_THE_BUTTON)
        _next_buttons(page).first.click()

        # The button's words became the person's question...
        page.wait_for_function(
            """want => [...document.querySelectorAll('#explore-log .explore-msg.me')]
                     .some(line => line.textContent === want)""",
            arg=NEXT_QUESTIONS[0], timeout=20000)
        # ...the agent answered it...
        page.wait_for_function(
            """() => [...document.querySelectorAll('#explore-log .explore-msg.bot')]
                   .some(line => line.textContent.includes('240 of 240'))""",
            timeout=25000)
        # ...in the same conversation, which is what the session says...
        assert len(asked) == 2, f"two turns were expected, {len(asked)} were sent"
        assert asked[0]["session"] is None, "the first turn opened the conversation"
        assert asked[1]["session"], "the press opened a second conversation"
        assert asked[1]["message"] == NEXT_QUESTIONS[0]
        # ...and the running cost went up rather than starting again.
        page.wait_for_function(
            "before => document.querySelector('#explore-cost').textContent !== before",
            arg=first, timeout=20000)

        # The pressed button is spent; the other two are still there to press.
        buttons = _next_buttons(page)
        assert buttons.nth(0).is_disabled()
        assert not buttons.nth(1).is_disabled()
        # And the answer's own questions are under it, three of the four it
        # wrote: the cap is this screen's.
        assert page.locator("#explore-log .explore-next").count() == 2
        last = page.locator("#explore-log .explore-next").last
        assert last.locator(".next-row button").count() == 3
    finally:
        page.close()


def test_an_answer_that_opened_nothing_ends_in_no_buttons(admin, plant):
    """"If the model offers none, no buttons." A screen that invented three
    would be a screen spending money on questions nobody's answer raised."""
    plant.script[:] = _reads_the_chart_then_answers(A_CLOSED_ANSWER)
    page = _explore(admin, plant, "/dashboard/ai#explore")
    try:
        page.fill("#explore-input", "which gauge took that sample?")
        _ask(page)
        page.wait_for_function(
            """() => [...document.querySelectorAll('#explore-log .explore-msg.bot')]
                   .some(line => line.textContent.includes('HEIGHT-01'))""",
            timeout=25000)
        assert page.locator("#explore-log .explore-next").count() == 0
        said = page.locator("#explore-log .explore-msg.bot").last.inner_text()
        assert "nothing else on this gauge" in said
    finally:
        page.close()
