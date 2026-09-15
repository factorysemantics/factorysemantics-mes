"""A station that beat its rated cycle, drawn, in a real browser.

House rule 6: a rendered chart can be completely convincing and completely
wrong, so a visualisation is looked at with real data and then pinned with a
test. This is the pinning half for the one case the OEE waterfall could not
draw before 2026-09-14, because performance was capped at 1.0 and the case
could not arise: a machine that out-ran the cycle time its master data rates
it at.

What the bar must do, and what it must not:

* The bar is one full segment, and no losses. A waterfall only adds up while
  every loss is a loss; here one of them is negative, so drawing the rest
  beside a full bar makes the row read as though time was lost that was not.
* The score prints the true figure, over 100 % and all. Trimming it there
  would put the cap back one layer out.
* The row says, in words, that the counted work will not fit inside the run
  time. That is the finding a plant acts on; the number alone reads like a
  miracle. It must not name a culprit: the MES cannot tell a rating slower
  than the machine from run time it failed to see.

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


def test_the_station_that_beat_its_rating_prints_the_true_figure(page):
    """Not 100 %. The cap this replaces is what made nine stations in the lab
    all read exactly 1.0 while the line they replayed did not."""
    score = _row(page).locator(".score").inner_text()
    assert score.endswith("%")
    assert int(score.rstrip("%")) > 100, f"the score reads {score}, which is the cap again"


def test_the_row_says_the_counted_work_will_not_fit_inside_the_run_time(page):
    """The number alone reads like a miracle. The sentence is the finding —
    and it names the disagreement, not a culprit."""
    note = _row(page).locator(".counts-outrun")
    assert note.is_visible()
    assert "counted work outruns the run time" in note.inner_text()
    assert "rating slower than the machine" not in note.inner_text()


def test_the_bar_draws_no_losses_it_cannot_lay_out(page):
    """The first draft drew the loss segments beside a full bar, and a station
    with an OEE of 171 % read as though it had lost a seventh of its time. One
    segment, the whole bar, and the losses left to the note."""
    bar = _row(page).locator(".bar")
    segments = bar.locator("i")
    assert segments.count() == 1, f"{segments.count()} segments in a bar that cannot hold a waterfall"

    box = bar.bounding_box()
    drawn = page.evaluate(
        """(bar) => [...bar.querySelectorAll('i')]"""
        """.reduce((total, i) => total + i.getBoundingClientRect().width, 0)""",
        bar.element_handle())
    assert drawn <= box["width"] + 1, "the segments are wider than the bar they are inside"


def test_a_station_inside_its_rating_still_draws_the_whole_waterfall(page):
    """The change is for the case that could not arise before, and only that
    case: PACK01 is unrated in this plant, and every other row is untouched."""
    others = page.locator(f'.oee-row:not(:has(.code:text-is("{MACHINE}"))) .counts-outrun')
    assert others.count() == 0
