"""The AI screen, in a browser: the list, one conversation, and the gate.

The screen exists because a conversation that went wrong could only be
discussed by pasting a screenshot. So what has to be true is not that an
endpoint answers - `test_the_plant_keeps_a_trace_of_what_its_ai_did.py` pins
that - but that a person who was not there can open it and read what happened:
the proposal that was done, the one that was declined, and the turn that
failed, each saying which it was.

Same shape as `test_ui_nav.py`: a seeded plant on a loopback port of the
operating system's choosing, driven by Chromium, touching nothing anyone else
is running. The trace rows are written straight into the plant's database
rather than by driving the assistant, because what is under test here is the
reading of them and a scripted model in another process would only be a
slower way of writing the same four rows.

**Looked at on loopback only, and that is the honest answer for this file.**
Nothing on this screen is timing-shaped: it reads two endpoints and draws
tables, with no walk landing on a control another script is still rendering
and no redirect following a save. The one control that writes - the retention
box - is a PATCH and a redraw. There is no artificial delay here because
there is no race to hold open.
"""

import json
import socket
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

#: The conversation the page is read against: four turns, one of each thing
#: that can happen, in the order Scott's own afternoon went. Two of them
#: carry a screen and two do not - which is the ordinary state of a plant
#: that upgraded, because `ai_turns.screen` is not backfilled.
SESSION = "ab12cd34ef56"


def _trace_rows():
    from fsmes.db import utcnow

    now = utcnow()
    return [
        dict(ts=now - timedelta(minutes=9), session=SESSION, brain="floor",
             person="ADMIN", model="claude-sonnet-5", kind="proposals",
             screen="/dashboard/config/quality",
             asked="I want a non-conformance to have a prefix CR instead of NC",
             said="I will change it.",
             tools=[{"tool": "plant_settings", "args": {"find": "prefix"},
                     "ok": True, "summary": "total=1, showing=1"}],
             proposals=[{"id": "p1", "tool": "write_plant_setting",
                         "args": {"domain": "quality", "key": "nc_code_prefix",
                                  "value": "CR"},
                         "outcome": "confirmed", "entity_type": "plant_setting",
                         "entity_id": "[quality] nc_code_prefix",
                         "action": "plant_setting.set"}],
             input_tokens=2000, output_tokens=60, usd=0.0051),
        dict(ts=now - timedelta(minutes=7), session=SESSION, brain="floor",
             person="ADMIN", model="claude-sonnet-5", kind="guide",
             screen="/dashboard/quality",
             asked="could you show me where?",
             said="Here it is on your own screen.",
             guide="record-check", guide_steps=4, usd=0.0012),
        dict(ts=now - timedelta(minutes=5), session=SESSION, brain="floor",
             person="ADMIN", model="claude-sonnet-5", kind="reply",
             asked="actually leave it", said="Left as it was.",
             proposals=[{"id": "p2", "tool": "write_plant_setting",
                         "args": {"domain": "quality", "key": "nc_code_prefix",
                                  "value": "NC"},
                         "outcome": "declined"}],
             usd=0.0009),
        dict(ts=now - timedelta(minutes=3), session=SESSION, brain="floor",
             person="ADMIN", model="claude-sonnet-5", kind="error",
             asked="set it to 4",
             said="The assistant hit an error on that one.",
             error="BadRequestError"),
    ]


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A seeded demo plant with a trace in it, served over HTTP."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.domain import AiTurn, AuditLog
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("ai-screen") / "plant.db"
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
            for row in _trace_rows():
                session.add(AiTurn(**row))
            # The audit row the confirmed proposal wrote. Seeded beside the
            # trace because the claim under test is that the two agree, and a
            # link into an audit trail with nothing in it proves neither half.
            session.add(AuditLog(
                actor="AGENT", on_behalf_of="ADMIN", action="plant_setting.set",
                entity_type="plant_setting", entity_id="[quality] nc_code_prefix",
                before={"value": "NC"}, after={"value": "CR"}))
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


def _signed_in(playwright, base, code, password):
    _pw, chromium = playwright
    context = chromium.new_context(viewport={"width": 1400, "height": 1000})
    reply = context.request.post(
        f"{base}/auth/login",
        data=json.dumps({"code": code, "password": password}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in as {code} failed: {reply.status}"
    return context


@pytest.fixture(scope="module")
def admin(playwright, plant):
    context = _signed_in(playwright, plant, "ADMIN", "admin")
    yield context
    context.close()


@pytest.fixture(scope="module")
def operator(playwright, plant):
    """Scott's own demo account. An operator does not hold `audit.read`."""
    context = _signed_in(playwright, plant, "SCOTT", "operator")
    yield context
    context.close()


def _open(context, base):
    page = context.new_page()
    page.goto(f"{base}/dashboard/ai", wait_until="load", timeout=30000)
    return page


def test_the_conversation_list_names_the_person_and_says_its_total(admin, plant):
    page = _open(admin, plant)
    page.wait_for_selector("#conv-table tbody tr", timeout=15000)
    assert page.locator("#conv-table tbody tr").count() == 1
    row = page.locator("#conv-table tbody tr").first.inner_text()
    assert "ADMIN" in row and "floor" in row
    # Every list states its total (STYLE.md rule 4).
    assert "of 1" in page.locator("#conv-count").inner_text()
    page.close()


def test_the_list_says_which_screen_a_conversation_was_opened_from(admin, plant):
    """And that it moved. Naming only the first screen of a conversation
    that crossed two would read as a fact about the whole of it, so the row
    says how many there were rather than quietly picking one."""
    page = _open(admin, plant)
    page.wait_for_function(
        "() => (document.querySelector('#conv-table tbody tr') || {}).innerText"
        "        ?.includes('/dashboard/config/quality')", timeout=15000)
    row = page.locator("#conv-table tbody tr").first.inner_text()
    assert "+1" in row, row
    page.close()


def test_one_conversation_reads_as_what_happened_not_as_a_log(admin, plant):
    """The four things the screen exists to tell apart: a proposal that was
    done and names its audit row, one that was declined, a walk that was put
    on the screen, and a turn that failed."""
    page = _open(admin, plant)
    page.wait_for_selector("#conv-table tbody tr", timeout=15000)
    page.locator("#conv-table tbody button").first.click()
    page.wait_for_selector("#turns .turn", timeout=15000)

    assert page.locator("#turns .turn").count() == 4
    text = page.locator("#turns").inner_text()
    # A pill is upper-cased by the stylesheet, so the words are compared as
    # the screen says them rather than as the database spells them.
    said = text.lower()

    # In the order it happened, with the person's own words.
    assert text.index("prefix CR") < text.index("could you show me where?")

    # 1. done, and linked to the audit row it wrote.
    assert "confirmed" in said
    assert "[quality] nc_code_prefix" in text
    assert page.locator("#turns a", has_text="on Ops").count() == 1

    # 2. declined is an outcome, not an absence.
    assert "declined" in said

    # 3. the walk, and the one thing the plant does not know about it.
    assert "record-check" in text
    assert "How far they got is not recorded" in text

    # 4. the failed turn, by its class, and marked as one.
    assert "badrequesterror" in said
    assert page.locator("#turns .turn.failed").count() == 1
    page.close()


def test_the_audit_link_lands_on_ops_with_that_action_already_chosen(admin, plant):
    """A link that promised a filter and landed on an unfiltered list would be
    the same half-truth this whole screen exists to stop."""
    page = _open(admin, plant)
    page.wait_for_selector("#conv-table tbody tr", timeout=15000)
    page.locator("#conv-table tbody button").first.click()
    page.wait_for_selector("#turns .turn", timeout=15000)

    page.locator("#turns a", has_text="on Ops").first.click()
    page.wait_for_selector("#action-filter", timeout=15000)
    page.wait_for_function(
        "() => document.querySelector('#action-filter').value === 'plant_setting.set'",
        timeout=15000)
    page.wait_for_function(
        "() => document.querySelector('#actor-filter').value === 'AGENT'",
        timeout=15000)
    page.close()


def test_the_settings_tab_says_the_horizon_and_lets_an_admin_move_it(admin, plant):
    page = _open(admin, plant)
    page.wait_for_selector("#conv-table tbody tr", timeout=15000)
    page.locator('.tab[data-tab="settings"]').click()
    # The box is drawn empty and filled when the administration sections
    # arrive; reading it the instant it exists read "" on GitHub's runner
    # (2026-09-26) and "90" on every loopback. Wait for the value, not the box.
    page.wait_for_function(
        "() => (document.querySelector('#trace-days') || {}).value === '90'",
        timeout=15000)

    page.fill("#trace-days", "30")
    page.locator("#trace-save").click()
    page.wait_for_function(
        "() => document.querySelector('#trace-now')"
        "        .textContent.includes('Now 30')", timeout=15000)
    # Put it back, so the module's other tests read the plant they expect.
    page.fill("#trace-days", "90")
    page.locator("#trace-save").click()
    page.wait_for_function(
        "() => document.querySelector('#trace-now')"
        "        .textContent.includes('Now 90')", timeout=15000)
    page.close()


def test_the_status_tab_says_which_brains_are_on(admin, plant):
    """The cloud brain is on this table whatever the local AI setting says -
    `test_the_ai_tab_says_what_the_assistant_costs_without_local_ai.py` is the
    file that drives both settings; this one only pins that the tab this
    module opens draws it at all."""
    page = _open(admin, plant)
    page.locator('.tab[data-tab="status"]').click()
    page.wait_for_function(
        "() => document.querySelector('#status-table tbody')"
        "        .innerText.includes('Floor agent (cloud)')", timeout=15000)
    page.close()


def test_an_operator_is_told_it_is_not_their_screen_and_sees_no_chip(operator, plant):
    """The gate. Not a blank page and not a refusal from an endpoint after the
    fact: the chip is not in their nav at all, and the screen says which
    capability it needs and who they are signed in as."""
    page = _open(operator, plant)
    page.wait_for_selector("#denied:not(.hidden)", timeout=15000)
    assert page.locator("#ai-main").is_hidden()
    assert "audit.read" in page.locator("#denied").inner_text()
    assert "SCOTT" in page.locator("#denied-who").inner_text()

    page.wait_for_selector("header[data-nav] nav.nav a", timeout=15000)
    chips = page.locator("header[data-nav] nav.nav a").all_inner_texts()
    assert "AI" not in [c.strip() for c in chips], chips
    page.close()
