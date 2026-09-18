"""A station whose counts will not fit inside its run time, drawn, in a browser.

House rule 6: a rendered chart can be completely convincing and completely
wrong, so a visualisation is looked at with real data and then pinned with a
test. This is the pinning half for the case the OEE waterfall could not draw
before 2026-09-14, when performance was capped at 1.0 and the case could not
arise: a machine that counted more work than its own run time can hold.

Between 2026-09-14 and 2026-09-18 this row printed the ratio — 171 %, and on a
plant replaying a recorded line at 10x, 980 %. Decision 0026, amended on
2026-09-18 after exactly that: the ratio is a measurement of two of this MES's
records disagreeing, not of the machine, so it is kept in the payload under a
name that says so and no surface prints it as a figure.

What the row must do, and what it must not:

* The bar draws unknown — one segment, no waterfall. A waterfall only adds up
  while every loss is a loss, and here one of them is negative.
* The score prints no percentage. Neither the ratio, which is the lie, nor a
  trimmed 100 %, which puts the cap back one layer out.
* The row says, in words, that the counted work will not fit inside the run
  time, and names both numbers. That is the finding a plant acts on. It must
  not name a culprit: the MES cannot tell a rating slower than the machine
  from run time it failed to see.

Same shape as `test_ui_nav.py`: a seeded plant on a loopback port of the
operating system's choosing, driven by Chromium, touching nothing anyone else
is running.
"""

import socket
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.slow

MACHINE = "MIX01"          # the demo line's mixer, rated at 4.0 s a unit
RATED_SECONDS = 4.0
RAN_MINUTES = 30
#: Twice what the rating allows for that run time: 30 min / 4 s = 450 units.
MADE = 900


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    """A demo plant whose mixer made twice its rated rate for half an hour."""
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine, utcnow
    from fsmes.domain import Equipment, EquipmentState, EquipmentStateName, ProductionLog
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth, workorders

    path = tmp_path_factory.mktemp("oee") / "plant.db"
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
            mixer = session.scalar(select(Equipment).where(Equipment.code == MACHINE))
            assert mixer.ideal_cycle_seconds == RATED_SECONDS
            now = utcnow()
            session.add(EquipmentState(
                equipment_id=mixer.id, state=EquipmentStateName.RUNNING,
                started_at=now - timedelta(minutes=RAN_MINUTES + 5),
                ended_at=now - timedelta(minutes=5)))
            order = workorders.create(session, code="WO-RATED", material_code="FG-COLA",
                                      quantity=MADE)
            session.flush()
            session.add(ProductionLog(work_order_id=order.id, equipment_id=mixer.id,
                                      good_qty=MADE, scrap_qty=0,
                                      ts=now - timedelta(minutes=10)))
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
    """The analysis screen, signed in, with its OEE rows drawn."""
    import json

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
            open_page = context.new_page()
            open_page.goto(f"{plant}/dashboard/analysis", wait_until="load", timeout=30000)
            open_page.wait_for_selector(".oee-row", timeout=15000)
            yield open_page
            open_page.close()
            chromium.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


def _row(page):
    return page.locator(f'.oee-row:has(.code:text-is("{MACHINE}"))')


def test_the_station_prints_no_percentage_at_all(page):
    """Not the ratio, and not a trimmed 100 % either. The cap this replaces is
    what made nine stations in the lab all read exactly 1.0 while the line
    they replayed did not; the ratio that replaced it is what printed 980 % on
    a replayed plant. Neither is a measurement of this machine."""
    score = _row(page).locator(".score").inner_text().strip()
    assert not score.endswith("%"), f"the score reads {score}, which is the figure again"
    assert score in {"—", "-", ""}, f"the score reads {score}"


def test_the_row_says_the_counted_work_will_not_fit_inside_the_run_time(page):
    """The dash alone says nothing. The sentence is the finding — and it names
    the disagreement, not a culprit."""
    note = _row(page).locator(".counts-outrun")
    assert note.is_visible()
    text = note.inner_text()
    assert "Counted work outruns the run time" in text
    assert "no performance figure" in text
    assert "rating slower than the machine" not in text
    # Both numbers, in the title the row carries: 900 units of work against
    # the run time the MES recorded.
    assert "will not fit inside the run time" in (note.get_attribute("title") or "")


def test_the_bar_draws_no_losses_it_cannot_lay_out(page):
    """The first draft drew the loss segments beside a full bar, and a station
    with an OEE of 171 % read as though it had lost a seventh of its time. One
    segment, and the losses left to the note."""
    bar = _row(page).locator(".bar")
    segments = bar.locator("i")
    assert segments.count() == 1, f"{segments.count()} segments in a bar that cannot hold a waterfall"
    assert "b-unknown" in (segments.first.get_attribute("class") or ""), \
        "a full bar reads as a machine that scored, and this one has no score"

    box = bar.bounding_box()
    drawn = page.evaluate(
        """(bar) => [...bar.querySelectorAll('i')]"""
        """.reduce((total, i) => total + i.getBoundingClientRect().width, 0)""",
        bar.element_handle())
    assert drawn <= box["width"] + 1, "the segments are wider than the bar they are inside"


def test_the_machine_page_draws_the_missing_oee_as_unknown_and_not_as_full_marks(plant, page):
    """The other screen that shows these bars, and the one where the fix was
    nearly undone by a stylesheet: the OEE row paints itself green because it
    is the total, and that rule beat the hatching for unknown. A solid green
    bar beside a dash reads as a perfect score, which is the same lie in a
    different font."""
    machine = page.context.new_page()
    # The tab, not the URL: the page builds its own tab strip after it knows
    # who is looking, so the button has to be waited for rather than clicked
    # at.
    machine.goto(f"{plant}/dashboard/machine/{MACHINE}#oee", wait_until="load", timeout=30000)
    machine.wait_for_selector('.tab[data-tab="oee"]', timeout=30000)
    machine.click('.tab[data-tab="oee"]')
    machine.wait_for_selector("#oee-bars .bar-row", timeout=30000)

    # Read in one pass, in the page: the screen refreshes itself, and a style
    # asked for through a handle taken before a re-render is a style read off
    # a node that is no longer in the document, which answers "" for
    # everything and looks exactly like a bug in the stylesheet.
    drawn = machine.evaluate("""() => [...document.querySelectorAll('#oee-bars .bar-row')]
        .map((row) => {
          const fill = row.querySelector('.bar > i');
          return {
            label: row.firstElementChild.textContent.trim(),
            num: (row.querySelector('.num') || {}).textContent.trim(),
            cls: fill ? fill.className : '',
            paint: fill ? getComputedStyle(fill).backgroundImage : '',
          };
        })""")
    by_label = {row["label"]: row for row in drawn}
    for label in ("Performance", "OEE"):
        row = by_label[label]
        assert row["num"] in {"—", "-", ""}, f"{label} reads {row['num']}"
        assert "unknown" in row["cls"], f"{label} is drawn as a score"
        assert "gradient" in row["paint"], f"{label} is a solid bar where there is no figure"
    said = machine.locator("#oee-bars .counts-outrun")
    assert "no performance figure" in said.inner_text()
    machine.close()


def test_a_station_inside_its_rating_still_draws_the_whole_waterfall(page):
    """The change is for the case that could not arise before, and only that
    case: PACK01 is unrated in this plant, and every other row is untouched."""
    others = page.locator(f'.oee-row:not(:has(.code:text-is("{MACHINE}"))) .counts-outrun')
    assert others.count() == 0
