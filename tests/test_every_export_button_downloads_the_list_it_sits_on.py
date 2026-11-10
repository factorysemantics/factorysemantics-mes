"""Every Export xlsx button downloads the list it sits on, named for it.

One button per list a supervisor would take to Excel: work orders,
non-conformances, completed maintenance, the audit trail, lots, OEE by station
and the stops behind the downtime pareto. For each, the list's own fetch is
held back by an artificial delay, the range is picked where the list has one,
and the test waits for the link to carry it before pressing it - so the link
is proved to follow the screen once the list has answered, not on loopback's
six milliseconds. The download's name says the list and the range, and its
rows are counted back out of the zip.
"""

import io
import json
import re
import socket
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

DELAY_SECONDS = 0.8
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SEPTEMBER = [datetime(2026, 9, day, 10, 0) for day in (2, 9, 16, 23, 29)]
OUTSIDE = [datetime(2026, 8, 31, 10, 0), datetime(2026, 10, 1, 10, 0)]


def _seed(session) -> None:
    from sqlalchemy import select

    from fsmes.db import utcnow
    from fsmes.domain import AuditLog, Equipment, EquipmentState, EquipmentStateName
    from fsmes.services import execution, maintenance, quality, workorders

    for n, when in enumerate(SEPTEMBER + OUTSIDE):
        workorders.create(session, code=f"WO-B-{n}", material_code="FG-COLA", quantity=10, due_date=when)
        nc = quality.open_nc(session, description=f"browser {n}", severity="minor", actor="ines")
        nc.created_at = when
        job = maintenance.raise_corrective(session, equipment_code="MIX01", summary=f"job {n}")
        session.flush()
        maintenance.complete(session, job.code, findings="done", downtime_minutes=5)
        job.completed_at = when
        session.add(AuditLog(actor="marek", action="browser.write", entity_type="test",
                             entity_id=str(n), ts=when))
        execution.create_lot(session, code=f"LOT-B-{n}", material_code="FG-COLA", quantity=5)
    session.flush()
    # Two stops inside the analysis page's window, and nothing else down.
    now = utcnow()
    for state in session.scalars(select(EquipmentState)):
        if state.state == EquipmentStateName.DOWN:
            session.delete(state)
    for code, ago in (("MIX01", 40), ("PACK01", 25)):
        unit = session.scalar(select(Equipment).where(Equipment.code == code))
        session.add(EquipmentState(equipment_id=unit.id, state=EquipmentStateName.DOWN,
                                   started_at=now - timedelta(minutes=ago),
                                   ended_at=now - timedelta(minutes=ago - 5)))


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.services import auth

    path = tmp_path_factory.mktemp("export-buttons") / "plant.db"
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
            _seed(session)
            session.commit()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(create_app(), log_level="warning", lifespan="on"))
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
def browser():
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright
    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            chromium = pw.chromium.launch()
            yield chromium
            chromium.close()
    except PlaywrightError as err:
        pytest.skip(f"chromium is not installed for playwright: {err}")


#: page, the list fetch to hold back, the range boxes (or none), the button,
#: the file's name, and how many rows the file must hold.
BUTTONS = {
    "work orders": ("/dashboard/orders", "**/workorders?*", ("#f-due-after", "#f-due-before"),
                    "#order-export", r"work-orders-due-2026-09-01-to-2026-09-30\.xlsx", 5),
    "non-conformances": ("/dashboard/quality", "**/quality/nonconformances?*", ("#n-from", "#n-to"),
                         "#n-export", r"non-conformances-2026-09-01-to-2026-09-30\.xlsx", 5),
    "completed maintenance": ("/dashboard/maintenance#history", "**/maintenance/orders?*",
                              ("#history-from", "#history-to"), "#history-export",
                              r"maintenance-work-2026-09-01-to-2026-09-30\.xlsx", 5),
    "audit trail": ("/dashboard/ops", "**/ops/activity?*", ("#activity-from", "#activity-to"),
                    "#activity-export", r"audit-trail-2026-09-01-to-2026-09-30\.xlsx", 5),
    "lots": ("/dashboard/trace", "**/execution/lots?*", None, "#lots-export", r"lots-all\.xlsx", None),
    "oee by station": ("/dashboard/analysis", "**/analysis/oee?*", None, "#oee-export",
                       r"oee-by-station-\w+-\d{4}-\d\d-\d\d-\d{4}-to-\d{4}-\d\d-\d\d-\d{4}\.xlsx", None),
    "downtime stops": ("/dashboard/analysis", "**/analysis/downtime?*", None, "#downtime-export",
                       r"downtime-\w+-\d{4}-\d\d-\d\d-\d{4}-to-\d{4}-\d\d-\d\d-\d{4}\.xlsx", 2),
}


def _rows(body: bytes) -> int:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        sheet = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
    return len(sheet.find("m:sheetData", NS))


@pytest.mark.parametrize("name", list(BUTTONS))
def test_the_export_button_downloads_its_list_named_for_the_list_and_the_range(browser, plant, name):
    path, held, boxes, button, filename, expected = BUTTONS[name]
    context = browser.new_context(viewport={"width": 1400, "height": 1000}, accept_downloads=True)
    try:
        reply = context.request.post(
            f"{plant}/auth/login", data=json.dumps({"code": "ADMIN", "password": "admin"}),
            headers={"Content-Type": "application/json"})
        assert reply.ok, f"sign-in failed: {reply.status}"
        if expected is None:
            # The lots and the stations: however many the plant holds, which
            # the list's own endpoint says.
            api = {"lots": "/execution/lots?limit=1", "oee by station": "/analysis/oee"}[name]
            body = context.request.get(f"{plant}{api}").json()
            expected = body["total"] if "total" in body else len(body["stations"])
        assert expected > 0
        page = context.new_page()
        page.route(held, lambda route: (time.sleep(DELAY_SECONDS), route.continue_()))
        page.goto(f"{plant}{path}", wait_until="load", timeout=30000)
        page.wait_for_function(f"() => document.querySelector({button!r}) !== null", timeout=20000)
        if boxes:
            # Set as a person's date picker would, then the change the page
            # listens for. The order list's boxes are in a popover; the value
            # is what matters here, not the popover's own toggle.
            for box, day in zip(boxes, ("2026-09-01", "2026-09-30"), strict=True):
                page.evaluate("""([sel, day]) => { const el = document.querySelector(sel);
                                 el.value = day; el.dispatchEvent(new Event('change', {bubbles: true})); }""",
                              [box, day])
            page.wait_for_function(
                f"""() => {{ const a = document.querySelector({button!r});
                          return a && a.getAttribute('href').includes('2026-09-01')
                                   && a.getAttribute('href').includes('2026-09-30'); }}""",
                timeout=20000)
        elif name == "lots":
            # The count comes from its own fetch, after the delay.
            page.wait_for_function(
                f"() => /\\d/.test(document.querySelector({button!r}).getAttribute('aria-label') || '')",
                timeout=20000)
        else:
            page.wait_for_function(
                f"() => document.querySelector({button!r}).getAttribute('href').includes('hours=')"
                f" || document.querySelector({button!r}).getAttribute('href').includes('shift=')",
                timeout=20000)
        assert page.is_visible(button)
        assert page.text_content(button).strip() == "Export xlsx"

        with page.expect_download(timeout=20000) as caught:
            page.click(button)
        download = caught.value
        assert re.fullmatch(filename, download.suggested_filename), download.suggested_filename
        body = Path(download.path()).read_bytes()
    finally:
        context.close()
    assert _rows(body) == 1 + expected


def test_a_late_reply_to_the_from_box_does_not_take_the_to_box_out_of_the_file(browser, plant):
    """Picking From then To fetches the list twice. On a slow link the first
    reply can land last; the walk on 2026-11-08 caught the button downloading
    From's range with To's already in the box. Here the page's own fetch
    holds From's reply three times as long as To's, so it lands last - a
    route handler cannot do this, because the sync API runs them one at a
    time and would answer them in order."""
    context = browser.new_context(viewport={"width": 1400, "height": 1000}, accept_downloads=True)
    try:
        reply = context.request.post(
            f"{plant}/auth/login", data=json.dumps({"code": "ADMIN", "password": "admin"}),
            headers={"Content-Type": "application/json"})
        assert reply.ok
        page = context.new_page()
        page.add_init_script(f"""(() => {{
          const real = window.fetch.bind(window);
          window.__answered = [];
          window.fetch = async (input, init) => {{
            const url = String(input && input.url || input);
            if (!url.includes("/workorders?")) return real(input, init);
            const from = url.includes("due_after"), to = url.includes("due_before");
            const reply = await real(input, init);
            await new Promise((r) => setTimeout(r, {int(DELAY_SECONDS * 1000)} * (from && !to ? 3 : 1)));
            window.__answered.push(from && !to ? "from" : to ? "both" : "none");
            return reply;
          }};
        }})()""")
        page.goto(f"{plant}/dashboard/orders", wait_until="load", timeout=30000)
        page.wait_for_function("() => window.__answered.includes('none')", timeout=20000)
        for box, day in (("#f-due-after", "2026-09-01"), ("#f-due-before", "2026-09-30")):
            page.evaluate("""([sel, day]) => { const el = document.querySelector(sel);
                             el.value = day; el.dispatchEvent(new Event('change', {bubbles: true})); }""",
                          [box, day])
        page.wait_for_function(
            "() => window.__answered.includes('both') && window.__answered.includes('from')",
            timeout=20000)
        order = page.evaluate("() => window.__answered")
        assert order.index("from") > order.index("both"), order
        assert "5 orders" in page.get_attribute("#order-export", "aria-label")
        with page.expect_download(timeout=20000) as caught:
            page.click("#order-export")
        download = caught.value
        assert download.suggested_filename == "work-orders-due-2026-09-01-to-2026-09-30.xlsx"
        body = Path(download.path()).read_bytes()
    finally:
        context.close()
    assert _rows(body) == 1 + 5
