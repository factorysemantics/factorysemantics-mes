"""In a browser: on the 3D line view, a click on a heater band's marker opens
the panel that names it, and an engineer who presses Move, clicks the spot on
the mould and saves finds the marker there after a reload (2026-11-28).

One plant built from the cutlery pack, whose moulds carry four heater bands
and a melt sensor each (#335), with a repair order open on FORK1's band 2.
The clicks are real mouse clicks on the canvas, at the point on the screen
where the camera shows the marker or the mould; nothing calls the page's own
click handler.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
CUTLERY = REPO / "labs" / "cutlery"
BAND2 = "FORK1_Mold_HeaterZone2"
SENSOR = "FORK1_Mold_MeltTemp"


@pytest.fixture(scope="module")
def plant(serve_plant):
    from fsmes.pack import masterdata as pack
    from fsmes.services import auth, maintenance

    def seed(session):
        auth.create_user(session, code="ENG", name="Engineer", password="engineer", role="admin")
        auth.create_user(session, code="SUP", name="Supervisor", password="supervisor", role="supervisor")
        pack.seed(session, CUTLERY / "masterdata")
        session.flush()
        maintenance.raise_corrective(session, equipment_code=BAND2, summary="Heater band 2 open circuit")

    env = {"MES_PLANT_TIMEZONE": "UTC", "MES_LINE_LAYOUT_FILE": str(CUTLERY / "line_layout.json")}
    with serve_plant(seed, name="pointassign", env=env, demo=False) as served:
        yield served.base


@pytest.fixture(scope="module")
def chromium(plant):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright

    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            # WebGL with no GPU: Chromium's software renderer.
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader", "--use-angle=swiftshader"])
            yield browser
            browser.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


def _signed_in(chromium, base, code, password):
    context = chromium.new_context(viewport={"width": 1400, "height": 900})
    reply = context.request.post(f"{base}/auth/login", data=json.dumps({"code": code, "password": password}),
                                 headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in failed: {reply.status}"
    return context


def _open(page, base):
    page.goto(f"{base}/dashboard/line/3d?line=FORK1LINE&debug=1", wait_until="load", timeout=30000)
    # Framed, its markers built, and held still so a point on the screen stays put.
    page.wait_for_function(
        """() => {
          const l = window.__line;
          if (!l || !l.scene.fitted || !l.picker) return false;
          l.scene.controls.autoRotate = false;
          return true;
        }""", timeout=20000)


#: Where on the screen the camera shows a marker's head (a band's ring, a
#: sensor's pin), in CSS pixels.
ON_SCREEN = """(code) => {
  const l = window.__line, cam = l.scene.camera, head = l.picker.markers.get(code).head;
  l.scene.controls.update(); cam.updateMatrixWorld();
  const v = head.getWorldPosition(cam.position.clone()).project(cam);
  const box = l.scene.renderer.domElement.getBoundingClientRect();
  return [box.left + (v.x + 1) / 2 * box.width, box.top + (1 - v.y) / 2 * box.height];
}"""

#: A point on the mould's own shape and where that is on the screen: the
#: first thing drawn on the ray through the middle of its top half.
ON_THE_MOULD = """() => {
  const l = window.__line, cam = l.scene.camera, group = l.scene.machines.get("FORK1_Mold").group;
  cam.updateMatrixWorld();
  const centre = group.position.clone(); centre.y = 1.4;
  const v = centre.clone().project(cam);
  const box = l.scene.renderer.domElement.getBoundingClientRect();
  return [box.left + (v.x + 1) / 2 * box.width, box.top + (1 - v.y) / 2 * box.height];
}"""


def test_a_click_on_band_2_s_red_marker_opens_its_panel_with_its_repair(chromium, plant):
    context = _signed_in(chromium, plant, "SUP", "supervisor")
    page = context.new_page()
    _open(page, plant)
    page.wait_for_function(f"() => window.__line.picker.markers.get('{BAND2}').mesh.userData.repair === true")
    x, y = page.evaluate(ON_SCREEN, BAND2)
    page.mouse.click(x, y)
    page.wait_for_function("() => document.querySelector('#pick-title').textContent === 'Heater band 2'")
    panel = page.inner_text("#pick")
    assert "CC-MOULD01 · from Moulding line FORK1" in panel
    assert "Heater band 2 open circuit" in panel
    link = page.get_attribute("#pick .pick-repair", "href")
    assert link == "/dashboard/maintenance?equipment=FORK1_Mold#work"
    # A supervisor may look, not move.
    assert page.locator("#pick-move").count() == 0
    context.close()


def test_an_engineer_moves_the_melt_sensor_and_after_a_reload_it_is_where_it_was_put(chromium, plant):
    context = _signed_in(chromium, plant, "ENG", "engineer")
    page = context.new_page()
    _open(page, plant)
    before = page.evaluate(f"() => window.__line.picker.markers.get('{SENSOR}').part.place")

    x, y = page.evaluate(ON_SCREEN, SENSOR)
    page.mouse.click(x, y)
    page.wait_for_function("() => document.querySelector('#pick-title').textContent === 'Melt temperature sensor'")
    page.click("#pick-move")
    page.wait_for_function("() => document.body.classList.contains('pick-moving')")

    x, y = page.evaluate(ON_THE_MOULD)
    page.mouse.click(x, y)
    page.wait_for_function("() => document.querySelector('#pick-place').textContent.includes('not saved yet')")
    put = page.evaluate("() => window.__line.picker.moving.place")
    assert put != before
    page.click("#pick-save")
    page.wait_for_function("() => !document.querySelector('#pick-place').textContent.includes('not saved')")

    [saved] = [e for e in context.request.get(f"{plant}/masterdata/equipment?q={SENSOR}").json()
               if e["code"] == SENSOR]
    assert (saved["x"], saved["y"], saved["z"]) == (put["x"], put["y"], put["z"])

    page.reload(wait_until="load")
    _open(page, plant)
    marker = page.evaluate(
        """(code) => {
          const m = window.__line.picker.markers.get(code);
          return { place: m.part.place, placed: m.part.placed, at: m.mesh.position.toArray() };
        }""", SENSOR)
    assert marker["place"] == {"x": put["x"], "y": put["y"], "z": put["z"]}
    assert marker["placed"] == "held"
    # Drawn where it was put: the cutlery layout's x is plant x less the first
    # machine's, its z minus plant y, its height plant z.
    origin = page.evaluate("() => window.__line.scene.origin")
    assert marker["at"] == pytest.approx([put["x"] - origin, put["z"], -put["y"]], abs=0.011)
    context.close()
