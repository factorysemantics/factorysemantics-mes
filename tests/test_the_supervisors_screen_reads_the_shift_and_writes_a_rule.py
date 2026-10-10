"""The maintenance supervisor's screen, in a browser, on a bottling plant.

Read top to bottom, this is the page handoff 3 asked for: what this shift
did, the rules that did it, who is on, and what nobody has. The point of
checking it in a browser rather than over HTTP is the wording - a supervisor
is promised the dispatcher's own sentences, and a second copy of that wording
in `maintenance.js` would be a lie waiting to happen. So every sentence this
file asserts is fetched from the server first and then looked for on the
screen, rather than spelled out here.

**The screen is drawn after fetches, so the test holds the fetches back.**
`/maintenance/shift` and `/maintenance/rules` are delayed by over a second -
the 2026-09-25 lesson, that a check on loopback renders in six milliseconds
and proves nothing about a phone over the tailnet. Every assertion waits on
the text it is about through `wait_for_function`, never on a `sleep` and never
on the element that will later hold the text (#112, 2026-09-26).

The plant is seeded from the bottling pack and its due work is raised, so
three orders are waiting before the dispatcher has seen them. Which shift is
on, and therefore which trades are rostered, depends on the hour the suite
runs at; nothing here asserts a shift code or a trade, only that the counts
on the screen are the counts the server gave.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for, and a Playwright file marked only `slow` is a test nothing runs.
"""

import json
import socket
import threading
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

ROOT = Path(__file__).resolve().parents[1]
BOTTLING = ROOT / "labs" / "multiplant" / "bottling"
#: How long the shift read and the rules read are held back for. Comfortably
#: longer than the page takes to render on loopback, so a passing test means
#: the screen waited for the answer rather than that the answer was already
#: there.
HELD_BACK_MS = 1200
#: The rule the test writes through the blanks: the palletiser, the mechanics,
#: whoever is nearest. The pack carries no rule for PAL01, so the sentence the
#: server builds is new and the page has to show it as the server says it.
NEW_RULE = {"equipment": "PAL01", "skill": "MECH", "priority_at_least": None,
            "strategy": "nearest"}


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A plant seeded from the bottling pack, with its due work raised."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.integrations.opc.tag_map import load_tag_map
    from fsmes.pack import masterdata
    from fsmes.services import auth, maintenance

    path = tmp_path_factory.mktemp("supervisor") / "plant.db"
    url = f"sqlite:///{path}"

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        env.setenv("MES_PLANT_TIMEZONE", "UTC")
        for leaked in ("MES_MODULES", "MES_WORDS", "MES_PLANT_NAME", "MES_TAG_MAP_FILE"):
            env.delenv(leaked, raising=False)
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_sessionmaker.cache_clear()

        engine = make_engine(url)
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            cycles = {m.equipment: m.cycle_seconds
                      for m in load_tag_map(BOTTLING / "tag_map.json")}
            masterdata.seed(session, BOTTLING / "masterdata", cycles)
            auth.ensure_builtin_roles(session)
            # A pack seeds a plant, not its people: who may sign in is the
            # fleet's business and a pack holds no password.
            auth.create_user(session, code="ADMIN", name="Plant administrator",
                             password="admin", role="admin")
            session.commit()
            # The plans the pack carries have never been done, so they are due.
            # Raising them is what gives the screen something to be about.
            raised = maintenance.raise_due(session, actor="test")
            session.commit()
            assert raised, "the bottling pack raised no due work to look at"

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
def browser(plant):
    """Chromium, signed in the way a person's browser is."""
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


def _said(browser, plant, path: str) -> dict:
    """What the server says, asked for the way the page asks for it."""
    reply = browser.request.get(f"{plant}{path}")
    assert reply.ok, f"{path} answered {reply.status}"
    return reply.json()


@pytest.fixture
def screen(browser, plant):
    """The Maintenance tab, with the shift and the rules held back."""
    page = browser.new_page()

    def held(route):
        # A fetch that does not come back instantly, which is every fetch on
        # a phone over a tailnet and no fetch at all on loopback.
        page.wait_for_timeout(HELD_BACK_MS)
        route.continue_()

    page.route("**/maintenance/shift*", held)
    page.route("**/maintenance/rules*", held)
    page.goto(f"{plant}/dashboard/maintenance", wait_until="load", timeout=30000)
    try:
        yield page
    finally:
        # The tab refreshes itself, so a held-back fetch is nearly always in
        # flight when the test ends; letting go of the routes first keeps the
        # teardown from reporting a closed page as a failure.
        page.unroute_all(behavior="ignoreErrors")
        page.close()


def test_the_shift_panel_opens_with_the_sentence_the_plant_wrote_about_itself(
        screen, browser, plant):
    """One line, at the top, in the words the server chose: how many came due,
    how many the rules handed out, how many are waiting and what for. The page
    prints it verbatim, so what a supervisor reads first is a sentence the
    product can be held to."""
    shift = _said(browser, plant, "/maintenance/shift")
    sentence = shift["sentence"]
    assert "came due" in sentence, sentence
    screen.wait_for_function(
        """(said) => document.querySelector('#shift-sentence').textContent.trim() === said""",
        arg=sentence, timeout=20000)
    # The shift it is about, said beside the heading rather than left to guess.
    screen.wait_for_function(
        """(key) => document.querySelector('#shift-key').textContent.includes(key)""",
        arg=shift["shift"]["key"], timeout=20000)
    # Every list states its total: the orders this shift raised are on the
    # screen, not a sample of them.
    listed = screen.eval_on_selector_all(
        "#shift-list li", "rows => rows.length")
    assert listed >= shift["counts"]["came_due"], (
        f"{shift['counts']['came_due']} came due and the list shows {listed}")


def test_the_rules_read_back_as_the_dispatchers_own_sentences_in_the_order_tried(
        screen, browser, plant):
    """Every rule, as `says()` writes it, in the order the dispatcher tries
    them, with what it is doing and what it has handed out. The sentences are
    not spelled out here: they are read off the server and looked for on the
    screen, which is the whole promise of the panel."""
    said = _said(browser, plant, "/maintenance/rules")
    sentences = [rule["says"] for rule in said["rules"]]
    assert len(sentences) >= 3, f"the pack's rules are missing: {said['rules']}"
    screen.wait_for_function(
        """(sentences) => {
             const rows = [...document.querySelectorAll('#rules-list li')]
               .map(li => li.textContent);
             return sentences.every((s, i) => rows[i] && rows[i].includes(s));
           }""",
        arg=sentences, timeout=20000)
    rows = screen.eval_on_selector_all(
        "#rules-list li", "rows => rows.map(r => r.innerText)")
    for rule, row in zip(said["rules"], rows, strict=False):
        assert rule["code"] in row, f"{rule['code']} is not named on its row: {row}"
        assert ("on" if rule["active"] else "off") in row.lower(), row
        assert "handed out" in row, f"{rule['code']} does not say what it gave out: {row}"
    # Up and down, and the first rule cannot be moved above itself.
    assert screen.eval_on_selector(
        "#rules-list li:first-child button[title='Try this rule earlier']",
        "b => b.disabled") is True
    # `text_content`, not `inner_text`: the kit puts these counts in small
    # capitals, and what the panel owes the reader is the number, not the case
    # the stylesheet renders it in.
    assert screen.text_content("#rules-count").strip() == (
        f"— {len(sentences)} written, {len(said['tried_in_order'])} tried in this order")


def test_a_supervisor_fills_in_the_blanks_and_the_preview_is_the_servers_sentence(
        screen, browser, plant):
    """A sentence with blanks, and nothing to name. The preview is the server's
    draft - built, said and thrown away behind `?dry_run=1` - so what the
    supervisor reads before pressing Add is the sentence the dispatcher will
    act on, not this file's guess at it. Then Add, and the same sentence is in
    the list and in the plant."""
    draft = browser.request.post(
        f"{plant}/maintenance/rules?dry_run=1",
        data=json.dumps(NEW_RULE), headers={"Content-Type": "application/json"})
    assert draft.ok, f"the draft was refused: {draft.status} {draft.text()}"
    wanted = draft.json()["says"]
    assert "PAL01" in wanted and "MECH" in wanted, wanted

    screen.wait_for_function(
        """() => document.querySelector('#rules-list li') !== null""", timeout=20000)
    screen.click("#rule-new")
    screen.wait_for_function(
        """() => !document.querySelector('#rule-form').classList.contains('hidden')""",
        timeout=20000)
    screen.select_option("#rule-equipment", NEW_RULE["equipment"])
    screen.select_option("#rule-skill", NEW_RULE["skill"])
    screen.select_option("#rule-priority", "")
    screen.select_option("#rule-strategy", NEW_RULE["strategy"])
    screen.wait_for_function(
        """(said) => document.querySelector('#rule-preview').textContent.trim() === said""",
        arg=wanted, timeout=20000)

    screen.click("#rule-add")
    screen.wait_for_function(
        """(said) => [...document.querySelectorAll('#rules-list li')]
                       .some(li => li.textContent.includes(said))""",
        arg=wanted, timeout=20000)
    kept = _said(browser, plant, "/maintenance/rules")
    assert wanted in [rule["says"] for rule in kept["rules"]], (
        "the rule the screen shows is not in the plant")
    # Written last, so tried last: a new rule does not quietly outrank the
    # ones already there.
    assert kept["rules"][-1]["says"] == wanted, kept["tried_in_order"]
    # And the form is put away, not left open over the list it changed.
    assert "hidden" in screen.get_attribute("#rule-form", "class")


def test_who_is_on_reads_as_counts_first_with_the_names_behind_an_expand(
        screen, browser, plant):
    """Grouped by trade, counts on the summary, names when it is opened. At
    three hundred people the counts are the answer and the names are the
    follow-up, so the names start closed - and the total is the roster's own,
    not the number of rows that happened to be drawn."""
    crew = _said(browser, plant, "/maintenance/roster")
    screen.wait_for_function(
        """(said) => document.querySelector('#roster-count').textContent.trim() === said""",
        arg=f"— {crew['total']} on, {crew['available']} available", timeout=20000)
    screen.wait_for_function(
        """() => document.querySelectorAll('#roster-trades details.trade').length > 0""",
        timeout=20000)
    first = screen.query_selector("#roster-trades details.trade")
    summary = first.query_selector("summary").inner_text()
    assert " on, " in summary and " free" in summary, (
        f"the trade's summary does not count its people: {summary}")
    names = [p["name"] for p in crew["people"]]
    assert not any(name in summary for name in names), (
        f"a name is on the summary, which does not read at three hundred: {summary}")
    assert first.query_selector("ul.trade-people li").is_visible() is False, (
        "the names are open before anybody asked for them")
    first.query_selector("summary").click()
    screen.wait_for_function(
        """() => {
             const li = document.querySelector('#roster-trades details.trade ul.trade-people li');
             return li !== null && li.offsetParent !== null;
           }""",
        timeout=20000)
    opened = first.query_selector("ul.trade-people").inner_text()
    assert any(name in opened for name in names), (
        f"the trade opened and named nobody: {opened}")


def test_what_is_waiting_is_grouped_by_the_dispatchers_reason_and_why_prints_the_walk(
        screen, browser, plant):
    """Orders nobody has, grouped by the dispatcher's own reason, each group
    with its label, its total and what to do about it - all the server's
    wording. *Why?* prints the walk the command line prints: lines, in the
    order the dispatcher took them, and more than one of them."""
    shift = _said(browser, plant, "/maintenance/shift")
    groups = shift["waiting"]
    assert groups, "nothing is waiting on this plant, so there is nothing to group"
    screen.wait_for_function(
        """(wanted) => {
             const heads = [...document.querySelectorAll('#waiting-groups h3')]
               .map(h => h.textContent);
             return wanted.every((w, i) => heads[i] === w);
           }""",
        arg=[f"{g['label']} — {g['total']}" for g in groups], timeout=20000)
    screen.wait_for_function(
        """(said) => document.querySelector('#waiting-count').textContent.trim() === said""",
        arg=f"— {shift['waiting_total']} waiting", timeout=20000)
    first = groups[0]
    body = screen.inner_text("#waiting-groups .waiting-group")
    assert first["why"] in body, f"the group does not say what to do: {body}"
    assert first["orders"][0]["code"] in body, body

    screen.click("#waiting-groups .waiting-group li button[data-assist='maintenance-why']")
    screen.wait_for_function(
        """(code) => {
             const box = document.querySelector('#why-box');
             return !box.classList.contains('hidden')
               && document.querySelector('#why-order').textContent.trim() === code
               && document.querySelector('#why-lines').textContent.split('\\n').length > 3;
           }""",
        arg=first["orders"][0]["code"], timeout=20000)
    walk = screen.inner_text("#why-lines")
    assert "rule(s) tried, in order:" in walk, walk
    assert "as things stand it would" in walk, walk
    # *Give to…* offers the people who could actually take it, by name.
    screen.click("#waiting-groups .waiting-group li button[data-assist='maintenance-give']")
    screen.wait_for_function(
        """(code) => !document.querySelector('#give-box').classList.contains('hidden')
                     && document.querySelector('#give-order').textContent.trim() === code""",
        arg=first["orders"][0]["code"], timeout=20000)
    offered = screen.eval_on_selector_all(
        "#give-person option", "o => o.map(x => x.textContent)")
    assert offered, "the person picker offered nothing at all, not even a reason"
