"""Every "Show me" walk, driven step by step in a real browser.

`tests/test_agent.py` proves each authored step names an anchor that appears
somewhere in a page's markup or its scripts. That is a grep, and a grep cannot
tell a control that renders from one that is hidden behind a tab nobody opened,
drawn only after two round trips, or sitting in a panel this role never sees.
The promise "Show me" makes is that the person ends up standing in front of the
real control with the proposed value already in it, and the only way to check a
promise about a rendered page is to render it.

So: one seeded plant on a loopback port, Chromium, and for every surface the
walk is put into sessionStorage exactly the way `saveWalk()` writes it - which
is what pressing "Show me" does, minus the model - and then pressed through to
the end. Each step has to ring something, and each fill that lands in a text
box has to be the proposed value.

**Timing.** Per the 2026-09-25 lesson (a walk that sampled its target once,
400 ms after load, passed on loopback for every executor and failed on Scott's
phone), the last test here holds a page's own data back behind a delay and
walks onto it anyway. Loopback alone would prove nothing about a control that
is drawn after a fetch.

Marked `browser`; run with `pytest -m browser`.
"""

import json
import socket
import threading
import time

import pytest
from sqlalchemy.orm import Session

from fsmes.services import agent as agent_service
from fsmes.services import assistant

pytestmark = [pytest.mark.slow, pytest.mark.browser]

#: What each tool would have been proposed with, in this plant's own codes.
#: These are the arguments a card would carry, so the walk fills the real form
#: with them - which is the whole of what is under test. Codes that feed a
#: select are ones the demo plant actually has, because a select cannot be
#: filled with an option that does not exist and a test that pretended
#: otherwise would be testing its own fixture.
PROPOSED: dict[str, dict] = {
    "record_check": {"material": "FG-COLA", "characteristic": "fill_weight", "value": 500},
    "book_output": {"equipment": "PACK01", "good": 10, "scrap": 1, "order": "WO-1001"},
    "issue_material": {"order": "WO-1001", "lot": "LOT-SUGAR-001", "quantity": 5},
    # Step 10 of WO-1001 is on the demo plant's routing, so the queue this
    # walk points into really has a row for it.
    "start_operation": {"order": "WO-1001", "seq": 10},
    "complete_operation": {"order": "WO-1001", "seq": 10},
    # A whole number: the walk types the quantity into a real box and the box
    # shows what was typed, so 240.0 would be asserting the wrong string.
    "create_lot": {"code": "LOT-DOCK-002", "material": "RAW-SUGAR", "quantity": 240},
    "set_machine_state": {"equipment": "MIX01", "state": "down", "reason": "jam at the infeed"},
    "create_order": {"code": "WO-NEW-01", "material": "FG-COLA", "quantity": 500,
                     "release": True},
    "order_action": {"code": "WO-1001", "action": "release"},
    # Vocabulary codes are lowercase and underscored - they are grouped on
    # and published, not sentences.
    "draft_downtime_reason": {"code": "jam_infeed", "name": "Jam at the infeed",
                              "description": "Bottles bridging before the starwheel."},
    "draft_nc_severity": {"code": "cosmetic", "name": "Cosmetic",
                          "description": "Marks a customer would see and nothing more."},
    "write_plant_setting": {"domain": "quality", "key": "nc_code_prefix", "value": "CR"},
    "raise_corrective_maintenance": {"machine": "MIX01", "summary": "Replace the infeed belt",
                                     "reason": "Worn through on one edge."},
    "review_nonconformance": {"code": "NC-00001"},
    "disposition_nonconformance": {"code": "NC-00001", "disposition": "scrap",
                                   "reason": "Out of specification on brix."},
    # The one that has been decided; nothing is closed before that.
    "close_nonconformance": {"code": "NC-00002"},
    "perform_maintenance": {"order": "CM-00001", "action": "complete",
                            "findings": "Belt replaced, tension set."},
    "raise_due_maintenance": {},
    "create_maintenance_plan": {"code": "PM-BELT", "name": "Belt check", "machine": "MIX01",
                                "trigger": "runtime_hours", "interval": 200,
                                "expected_minutes": 30, "document_code": "WI-BELT"},
    "create_material": {"code": "CAP-28MM", "name": "Bottle cap 28mm", "unit": "ea",
                        "type": "raw"},
    "create_equipment": {"code": "CAP01", "name": "Capper 01", "level": "work_unit",
                         "parent": "LINE1", "ideal_cycle_seconds": 2.5,
                         "cost_center": "PKG-01"},
    "create_spec": {"material": "FG-COLA", "characteristic": "fill_weight", "unit": "g",
                    "min_value": 495, "max_value": 505},
    "add_bom_component": {"material": "FG-COLA", "component": "RAW-SUGAR", "quantity": 2,
                          "operation_seq": 10},
    "create_user": {"code": "JSMITH", "name": "J Smith", "password": "first-password",
                    "role": "operator"},
    "create_role": {"code": "line_lead", "name": "Line lead",
                    "capabilities": ["plant.read", "orders.release"],
                    "description": "Runs a line and releases its work."},
    "update_role": {"code": "line_lead", "name": "Line lead",
                    "capabilities": ["plant.read", "orders.release", "orders.hold"],
                    "description": "Runs a line, releases and holds its work."},
    "assign_role": {"user": "SCOTT", "role": "supervisor"},
    "create_routing": {"code": "RT-NEW", "name": "Make it", "material": "FG-COLA",
                       "operations": [{"seq": 10, "name": "Mix", "equipment": "MIX01"},
                                      {"seq": 20, "name": "Pack", "equipment": "PACK01"}]},
    "create_document": {"code": "WI-CHANGEOVER", "title": "Changeover",
                        "body": "Stop the line. Purge the hopper."},
    "revise_document": {"code": "WI-CHANGEOVER", "title": "Changeover at the filler",
                        "body": "Stop the line. Purge the hopper. Run three bottles off."},
    "draft_instruction": {"code": "WI-FILL", "title": "Measuring fill weight",
                          "body": "Take three bottles from the middle of the run.",
                          "material": "FG-COLA", "characteristic": "fill_weight"},
    "draft_trigger": {"code": "T-WASH-HOT", "name": "Wash water too hot", "tag": "WashTemp",
                      "condition": "above", "threshold": 80, "machine": "MIX01",
                      "sustained_seconds": 30, "cooldown_seconds": 600,
                      "action": "log_event", "action_params": ""},
    "propose_adjustment": {"machine": "MIX01", "tag": "WashSetpoint", "value": 62.5,
                           "rationale": "Two degrees down; the last batch ran hot."},
    "plan_order": {"order": "WO-1001"},
    "plan_all_orders": {},
    "add_shift": {"code": "NIGHT", "name": "Night", "starts": "22:00", "ends": "06:00",
                  "days": "1111100"},
    "add_calendar_exception": {"day": "2026-12-25", "kind": "shutdown",
                               "reason": "Annual service"},
    "produce_units": {"material": "FG-COLA", "count": 5, "order": "WO-1001",
                      "machine": "PACK01"},
}

#: How long the Configuration page's own sections are held back in the last
#: test. Comfortably past the 400 ms a resume used to wait, comfortably inside
#: the three seconds a walk now spends looking.
DELAY_SECONDS = 0.8


def test_every_surface_has_arguments_to_walk_with():
    """The table above is part of the promise: a surface added without a row
    here would be a walk nobody ever rendered."""
    assert set(PROPOSED) == set(assistant.SURFACES), (
        f"no proposed arguments for: {sorted(set(assistant.SURFACES) - set(PROPOSED))}")


# ------------------------------------------------------------- a real plant

@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A seeded demo plant, served over HTTP on a port nobody chose in advance.
    A file database rather than the suite's in-memory one, because the server
    answers on its own threads and must open the same rows."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.domain import RecommendedAdjustment
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth, documents, quality, reasons, severities, triggers

    path = tmp_path_factory.mktemp("show-me") / "plant.db"
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
            # Two non-conformances, because the three walks about a finding
            # point at buttons the quality screen only draws on a row that
            # exists, and only draws the ones that make sense for where that
            # finding has got to. A plant with an empty list would make those
            # three tests pass by never rendering anything at all.
            #
            # NC-00001 is newly open: it can be reviewed and decided.
            quality.open_nc(session, description="Brix 20.0 against a 9.5-11.5 band",
                            severity="major", actor="ADMIN")
            # NC-00002 has been decided, which is the only state from which
            # anything is closed.
            second = quality.open_nc(session, description="Label skewed on the neck",
                                     severity="minor", actor="ADMIN")
            session.flush()
            quality.review_nc(session, second.code, actor="ADMIN")
            quality.disposition_nc(session, second.code, disposition="use_as_is",
                                   reason="Inside the cosmetic tolerance.", actor="ADMIN")
            # One of each thing a person may put in force, waiting. The five
            # signing walks end on controls the screens draw per row, and a
            # plant with nothing waiting draws none of them - so a walk onto
            # an empty screen would pass by pointing at nothing.
            reasons.define(session, code="jam_infeed", name="Jam at the infeed",
                           description="Bottles bridging before the starwheel.",
                           actor="ADMIN")
            severities.define(session, code="cosmetic", name="Cosmetic",
                              description="Marks a customer would see, and nothing more.",
                              actor="ADMIN")
            documents.create(session, code="WI-CHANGEOVER", title="Changeover",
                             body="Stop the line. Purge the hopper.", actor="ADMIN")
            triggers.create(session, code="T-WASH-HOT", name="Wash water too hot",
                            tag="WashTemp", condition="above", threshold=80.0,
                            actor="ADMIN")
            # Written as a row rather than through `adjustments.propose`: the
            # service refuses a tag the manifest does not carry, the demo
            # plant publishes no manifest, and what is under test here is
            # whether the queue draws an Approve button on a waiting row.
            session.add(RecommendedAdjustment(
                code="ADJ-00001", equipment_code="MIX01", tag="WashSetpoint",
                drives="WashTemp", current_value=64.5, proposed_value=62.5,
                minimum=55.0, maximum=70.0,
                rationale="Two degrees down; the last batch ran hot.",
                proposed_by="ADMIN"))
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


@pytest.fixture(scope="module")
def admin(playwright, plant):
    """Signed in as ADMIN, who holds every capability any surface needs - so a
    control that is missing here is missing, not withheld."""
    _pw, chromium = playwright
    context = chromium.new_context(viewport={"width": 1400, "height": 1000})
    reply = context.request.post(
        f"{plant}/auth/login",
        data=json.dumps({"code": "ADMIN", "password": "admin"}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in failed: {reply.status}"
    yield context
    context.close()


# ---------------------------------------------------------------- the walk

def _open_walk(context, base, walk, *, delay=None):
    """A browser mid-walk, the way `saveWalk()` leaves one that crossed a
    screen. Which is what "Show me" does, minus the model."""
    page = context.new_page()
    if delay is not None:
        page.route(delay, lambda route: (time.sleep(DELAY_SECONDS), route.continue_()))
    page.goto(f"{base}/dashboard", wait_until="load", timeout=30000)
    page.evaluate("saved => sessionStorage.setItem('fsmes-guide', saved)",
                  json.dumps({"walk": walk, "index": 0, "exact": True, "moved": 0}))
    page.goto(f"{base}{walk['steps'][0]['page']}", wait_until="load", timeout=30000)
    return page


def _coach(page, step_number=1, title=None, timeout=20000):
    """The card as it stands on step `step_number`, with `title` on it.

    Waited for by what it is about to be asserted, never by a sleep: pressing
    Next scrolls the next control into view and repaints the card a quarter of
    a second later, so reading it straight after the click reads the step
    before.

    The counter alone is not that state. On 2026-09-26 this waited on the
    number only, and on GitHub's runner read `Step 5 of 6` over step 4's words
    - so when the caller knows which title it expects, the wait is for the
    number *and* the title, and the test can only fail on what the words say
    rather than on when they arrived."""
    page.wait_for_selector(".assist-coach", state="visible", timeout=timeout)
    page.wait_for_function(
        """([n, title]) => {
            const card = document.querySelector('.assist-coach');
            const counter = card && card.querySelector('.step');
            if (!counter || !counter.textContent.trim().startsWith('Step ' + n + ' of')) return false;
            return title === null || card.innerText.includes(title);
        }""", arg=[step_number, title], timeout=timeout)
    return page.locator(".assist-coach").inner_text()


def _ring_is_round(page, anchor, timeout=20000):
    """The ring ends up round this anchor's control. Waited for rather than
    measured once: the step scrolls its control into view smoothly and the
    ring follows, so the two rectangles agree a moment after they exist."""
    page.wait_for_function(
        """anchor => {
            const ring = document.querySelector('.assist-ring');
            const box = document.querySelector(`[data-assist="${anchor}"]`);
            if (!ring || !box || ring.style.display === 'none') return false;
            const r = ring.getBoundingClientRect();
            const b = box.getBoundingClientRect();
            return r.left <= b.left + 1 && r.top <= b.top + 1
                && r.right >= b.right - 1 && r.bottom >= b.bottom - 1;
        }""", arg=anchor, timeout=timeout)


#: Tags whose value a walk types. A `select` is filled from the API and a
#: proposal may legitimately name an option this plant does not have; a text
#: box has no such excuse. A checkbox is not typed into at all - `applyFill`
#: ticks it - so it is checked separately.
TYPED = {"INPUT", "TEXTAREA"}
TICKED = ("true", "1", "yes", "on")


@pytest.mark.parametrize("tool", sorted(assistant.SURFACES))
def test_show_me_lands_on_every_control_of_this_walk(tool, admin, plant):
    """Press "Show me" and then Next to the end. Every step rings a control
    that is really on the screen, and every value the card carried is in the
    box by the time the person is looking at it."""
    surface = assistant.surface_for(tool, PROPOSED[tool], capabilities=None)
    # Built the way `renderProposal` builds it when "Show me" is pressed -
    # title and steps, no id - so this walk crosses a screen the way a real
    # one does. The evidence step is walked too: it is what the person is
    # shown after "Do it", and an anchor that exists in the markup and never
    # renders would look identical to a grep.
    walk = {"title": surface["title"],
            "steps": [*surface["steps"], surface["evidence"]]}
    page = _open_walk(admin, plant, walk)
    try:
        for index, step in enumerate(walk["steps"]):
            said = _coach(page, index + 1, step["title"])
            assert "could not find that control" not in said, (
                f"{tool} step {index + 1} ({step['anchor']} on {step['page']}): {said}")
            assert step["title"] in said, f"{tool} step {index + 1}: {said}"
            _ring_is_round(page, step["anchor"])

            want = (step.get("fill") or {}).get("value")
            if want not in (None, ""):
                target = page.locator(f'[data-assist="{step["anchor"]}"]')
                kind = target.evaluate("el => el.tagName + ':' + (el.type || '')")
                if kind.endswith(":checkbox"):
                    assert target.is_checked() == (str(want).lower() in TICKED), (
                        f"{tool} step {index + 1}: the walk found "
                        f"{step['anchor']} but did not tick it to {want!r}")
                elif kind.split(":")[0] in TYPED:
                    assert target.input_value() == str(want), (
                        f"{tool} step {index + 1}: the walk found "
                        f"{step['anchor']} but did not type {want!r} into it")

            last = index == len(walk["steps"]) - 1
            page.locator(".assist-coach button",
                         has_text="Done" if last else "Next").click()
    finally:
        page.close()


# ------------------------- and the newest control on the floor really works

def test_the_floor_screen_books_a_lot_in(admin, plant):
    """The form `create_lot`'s walk ends on does what the walk says it does.

    It is new here, and it is new because the assistant could book a lot in
    and no person could: production booked lots, the ERP link sent lots, and a
    delivery on the dock or a quantity somebody counted had no screen at all.
    `test_route_coverage` read the page as covering `POST /execution/lots`
    because its match is textual and the floor page reads that path on every
    refresh - so the hole was invisible to the ratchet that exists to find it.

    A walk onto a button that does nothing would satisfy every other test in
    this file, so this one presses it and then asks the plant.
    """
    page = admin.new_page()
    try:
        page.goto(f"{plant}/dashboard", wait_until="load", timeout=30000)
        page.fill('[data-assist="lot-code"]', "LOT-DOCK-001")
        page.fill('[data-assist="lot-material"]', "RAW-SUGAR")
        page.fill('[data-assist="lot-quantity"]', "240")
        page.click('[data-assist="lot-submit"]')

        # The lot is issuable from the form beside it, which is the whole
        # point of booking it in.
        page.wait_for_function(
            """() => [...document.querySelectorAll('[data-assist="consume-lot"] option')]
                     .some((o) => o.value === 'LOT-DOCK-001')""", timeout=20000)

        booked = admin.request.get(f"{plant}/execution/lots?limit=200").json()
        row = next((lot for lot in booked["items"] if lot["code"] == "LOT-DOCK-001"), None)
        assert row is not None, f"the plant has no LOT-DOCK-001: {booked['total']} lots"
        assert row["material"] == "RAW-SUGAR"
        assert row["quantity"] == 240
    finally:
        page.close()


# ------------------------------ the counter and the words are one card

def test_a_card_never_shows_one_steps_number_over_another_steps_words(admin, plant):
    """The card cannot be caught saying "Step 5 of 6" over step 4's words.

    It could, until 2026-09-26, and it was caught on GitHub's runner and never
    once on loopback: `standOn()` scrolls the control into view and paints the
    card a quarter of a second later, and that paint read the step from its own
    closure and the *number* from `walk.index` as it stood when the timer fired.
    Press Next inside that quarter second - which a test waiting only on the
    counter does, because a scroll event paints the counter early - and the
    stale paint lands under the new number.

    Driven here deliberately: two Next presses in one synchronous script, so
    the second one certainly lands inside the first one's pending paint. Every
    card the walk ever draws is recorded as it is drawn, and each one has to
    have its own step's title under its own step's number.
    """
    surface = assistant.surface_for("record_check", PROPOSED["record_check"])
    walk = {"title": surface["title"], "steps": list(surface["steps"])}
    titles = [step["title"] for step in walk["steps"]]
    page = _open_walk(admin, plant, walk)
    try:
        # Every version of the card, recorded as it is painted. The card is
        # rebuilt from nothing on each paint, so watching the body for added
        # nodes sees every one of them.
        page.evaluate("""() => {
            window.__cards = [];
            const look = () => {
                const card = document.querySelector('.assist-coach');
                const counter = card && card.querySelector('.step');
                const title = card && card.querySelector('h4');
                if (!counter || !title) return;
                const seen = counter.textContent.trim() + ' | ' + title.textContent.trim();
                if (window.__cards[window.__cards.length - 1] !== seen) window.__cards.push(seen);
            };
            new MutationObserver(look).observe(document.body,
                {childList: true, subtree: true, characterData: true});
            look();
        }""")
        _coach(page, 1, titles[0])
        # Both presses inside one script: no timer can fire between them, so
        # the walk is on step 3 while step 2's paint is still pending.
        page.evaluate("""() => {
            const next = [...document.querySelectorAll('.assist-coach button')]
                .find((b) => b.textContent.trim() === 'Next');
            next.click();
            next.click();
        }""")
        _coach(page, 3, titles[2])
        # Long enough for the pending paint to have landed if it was going to.
        page.wait_for_timeout(600)

        cards = page.evaluate("() => window.__cards")
        for card in cards:
            number, shown = (part.strip() for part in card.split("|", 1))
            index = int(number.split()[1]) - 1
            assert shown == titles[index], (
                f"the card said {number!r} over {shown!r}, which is step "
                f"{titles.index(shown) + 1}'s title: {cards}")
    finally:
        page.close()


# ------------------------------- and once against a page that is still drawing

def test_a_walk_onto_a_form_whose_page_is_still_fetching_waits_for_it(admin, plant):
    """The 2026-09-25 lesson, applied to the new walks. Loopback renders in
    single-digit milliseconds, which is why a walk that looked once at 400 ms
    passed every executor's check and failed the one person on a laptop. So
    one of these walks is run against a page whose own data is held back, and
    it still has to end up standing on the control with the value in it.

    Maintenance is the case worth holding back: the plan form's machine list
    is a select filled from `/equipment/states`, so this delays both the fetch
    and the option the walk is about to choose.
    """
    surface = assistant.surface_for("create_maintenance_plan",
                                    PROPOSED["create_maintenance_plan"])
    step = surface["steps"][0]                      # the plan's code, on the plans tab
    walk = {"title": surface["title"], "steps": [step]}
    page = _open_walk(admin, plant, walk, delay="**/maintenance/plans*")
    try:
        said = _coach(page, 1, step["title"], timeout=25000)
        assert "could not find that control" not in said, said
        assert step["title"] in said, said
        _ring_is_round(page, step["anchor"])
        box = page.locator(f'[data-assist="{step["anchor"]}"]')
        assert box.input_value() == PROPOSED["create_maintenance_plan"]["code"]
    finally:
        page.close()



# ------------------------------------------ the walks to a signing control

#: The five signing walks, and how far a browser can be driven along each
#: without actually approving anything. The last step of every one of them is
#: a control that puts something in force, and this suite must not press it -
#: so each walk is driven to its last step and the ring is checked there.
#:
#: The two vocabularies are signed on the floor screen's review panel, and
#: that panel is not on the page until somebody presses Review on a row. So
#: those two walks say `press` - the test presses the real Review button when
#: the walk reaches it, which is exactly what the step tells the person to do.
SIGNING_WALKS = {
    "approve-a-downtime-reason": "pending-review",
    "approve-a-severity": "pending-review",
    "approve-a-document": None,
    "approve-a-trigger": None,
    "approve-an-adjustment": None,
}


def test_the_signing_walks_are_the_five_things_a_person_may_put_in_force():
    assert set(SIGNING_WALKS) == {g["id"] for g in assistant.GUIDES if g.get("signing")}


@pytest.mark.parametrize("guide_id", sorted(SIGNING_WALKS))
def test_a_signing_walk_ends_on_a_control_that_is_really_there(guide_id, admin, plant):
    """Decision 0035 says the agent never approves, so the faithful answer to
    "approve the draft severity" is a walk to the button. A walk to a button
    that is not drawn would be a worse answer than the refusal it replaced -
    and every one of these five buttons is built by a page's own script, on a
    row, only when there is something waiting. Which is why this is a browser
    test and not a grep.

    Nothing is approved here: the walk stops with the ring round the control.
    """
    guide = next(g for g in assistant.GUIDES if g["id"] == guide_id)
    press = SIGNING_WALKS[guide_id]
    walk = {"title": guide["title"], "steps": list(guide["steps"])}
    page = _open_walk(admin, plant, walk)
    try:
        for index, step in enumerate(walk["steps"]):
            said = _coach(page, index + 1, step["title"])
            assert "could not find that control" not in said, (
                f"{guide_id} step {index + 1} ({step['anchor']} on {step['page']}): {said}")
            _ring_is_round(page, step["anchor"])
            if index == len(walk["steps"]) - 1:
                # The last step is the signature. It is there, it is visible,
                # and it stays unpressed.
                assert page.locator(f'[data-assist="{step["anchor"]}"]').first.is_visible(), (
                    f"{guide_id}: the control that signs is in the page but not shown")
                break
            if step["anchor"] == press:
                # What the step tells the person to do: open the review. The
                # panel behind it is what the next steps point at.
                page.locator(f'[data-assist="{press}"]').first.click()
            page.locator(".assist-coach button", has_text="Next").click()
    finally:
        page.close()


def test_a_proposals_own_walk_survives_crossing_a_screen(admin, plant):
    """The walk behind "Show me" has an id - `proposal` - so the model can
    name it and the turn log can record it. No endpoint serves that id: it is
    one conversation's proposal, filled with that proposal's arguments.

    `saveWalk()` used to decide by id alone: a guide with an id was saved as
    its id and fetched again on the next page. A walk like this one crossed a
    screen, was asked for by an id no endpoint knows, and vanished - the
    person arrived on the right page with no card and no explanation. It is
    saved whole now, and this is the crossing that proves it.
    """
    surface = assistant.surface_for("record_check", PROPOSED["record_check"])
    walk = {"id": agent_service.PROPOSAL_WALK, "ephemeral": True,
            "title": surface["title"],
            # The last step of this one is on another screen, which is the
            # whole point: the first step is on the floor screen.
            "steps": [surface["steps"][0], surface["evidence"]]}
    page = _open_walk(admin, plant, walk)
    try:
        assert "The quality check panel" in _coach(page, 1, "The quality check panel")
        page.locator(".assist-coach button", has_text="Next").click()

        # Over on the quality screen, still walking, still on the right step.
        said = _coach(page, 2, surface["evidence"]["title"], timeout=25000)
        # The page adds query parameters of its own, which are none of the
        # walk's business - `elsewhere()` compares only what the step names.
        assert page.evaluate("location.pathname") == "/dashboard/quality", page.url
        assert surface["evidence"]["title"] in said, said
        assert "could not find that control" not in said, said
        _ring_is_round(page, surface["evidence"]["anchor"])
    finally:
        page.close()


# ------------------- the button a step names, on a document that has one

def test_open_next_revision_is_a_control_a_walk_can_name(admin, plant):
    """`revise_document`'s walk says "press Open next revision", and this is
    the button.

    Its step rings `instruction-actions`, the row the button sits in, because
    that row is what is on the screen before anybody has pressed a document's
    row - a walk points, it does not click for you. Which leaves the button
    itself unchecked by that walk, and it had no `data-assist` at all until
    now while both of its neighbours did. So it is checked here, on a document
    that has a revision in force, which is the only state the screen offers it
    in: created and approved through the API, then opened by pressing its row.
    """
    made = admin.request.post(
        f"{plant}/documents",
        data=json.dumps({"code": "WI-PURGE", "title": "Purge the hopper",
                         "body": "Run the line dry, then purge."}),
        headers={"Content-Type": "application/json"})
    assert made.ok, f"could not draft WI-PURGE: {made.status} {made.text()}"
    put = admin.request.post(f"{plant}/documents/WI-PURGE/approve/1")
    assert put.ok, f"could not approve WI-PURGE: {put.status} {put.text()}"

    page = admin.new_page()
    try:
        page.goto(f"{plant}/dashboard/instructions", wait_until="load", timeout=30000)
        page.locator("#catalogue tbody tr", has_text="WI-PURGE").first.click()
        button = page.locator('[data-assist="instruction-revise"]')
        button.wait_for(state="visible", timeout=20000)
        assert button.inner_text().strip() == "Open next revision", button.inner_text()
        # Inside the row the walk's step rings, which is what makes the step's
        # words and the ring the same instruction.
        assert page.locator('[data-assist="instruction-actions"] '
                            '[data-assist="instruction-revise"]').count() == 1
    finally:
        page.close()
