"""In a browser: the 3D line view opens with the whole line in shot, every
station's label on the screen and about the same size, and draws what the line
carries - bodies on a body line, bottles on a bottling line.

One plant with two lines: the six-station bottling line (`SIMLINE`) and the
body shop's eight cells (`BS1`, from `labs/multiplant/bodyshop`, which draws
QI10 turned a quarter). Each is opened at a laptop's 1400x900 and a phone held
upright, 800x1200.

A label's place on screen is worked out the way three.js draws a sprite of
fixed screen size: its centre projected, its corners its scale times its
distance either side of that. Nothing here reads the code that sizes it.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for.
"""

from __future__ import annotations

import json
import math
import socket
import threading
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
KEPSIM_MAP = REPO / "config" / "tag_map_kepsim.json"
BODYSHOP = REPO / "labs" / "multiplant" / "bodyshop"
SIZES = [(1400, 900), (800, 1200)]


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.integrations.opc.tag_map import load_tag_map
    from fsmes.pack import masterdata as pack_masterdata
    from fsmes.seed import seed_demo_plant
    from fsmes.seed_kepsim import seed_kepsim_line
    from fsmes.services import auth

    root = tmp_path_factory.mktemp("line3dfit")
    layout = json.loads((REPO / "config" / "line_layout.json").read_text(encoding="utf-8"))
    bodyshop = json.loads((BODYSHOP / "line_layout.json").read_text(encoding="utf-8"))
    layout["lines"]["BS1"] = bodyshop["lines"]["BS1"]
    layout_file = root / "line_layout.json"
    layout_file.write_text(json.dumps(layout), encoding="utf-8")
    url = f"sqlite:///{root / 'plant.db'}"

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        env.setenv("MES_PLANT_TIMEZONE", "UTC")
        env.setenv("MES_LINE_LAYOUT_FILE", str(layout_file))
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_sessionmaker.cache_clear()

        engine = make_engine(url)
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            seed_demo_plant(session)
            auth.ensure_builtin_roles(session)
            seed_kepsim_line(session, tag_map=KEPSIM_MAP)
            cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(BODYSHOP / "tag_map.json")}
            pack_masterdata.seed(session, BODYSHOP / "masterdata", cycles)
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
def chromium(plant):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="the [dev] extra is not installed").sync_playwright

    from playwright.sync_api import Error as PlaywrightError

    try:
        with sync_playwright() as pw:
            # WebGL with no GPU: Chromium's software renderer, which it no
            # longer falls back to by itself.
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader", "--use-angle=swiftshader"])
            yield browser
            browser.close()
    except PlaywrightError as err:          # no browser binary on this machine
        pytest.skip(f"chromium is not installed for playwright: {err}")


def _opened(chromium, base, line, width, height):
    context = chromium.new_context(viewport={"width": width, "height": height})
    reply = context.request.post(
        f"{base}/auth/login", data=json.dumps({"code": "ADMIN", "password": "admin"}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in failed: {reply.status}"
    page = context.new_page()
    page.goto(f"{base}/dashboard/line/3d?line={line}&debug=1", wait_until="load", timeout=30000)
    # Framed for this window, and held still so what is measured is the first view.
    page.wait_for_function(
        """([w, h]) => {
          const s = window.__line && window.__line.scene;
          if (!s || !s.fitted || s.fitted.width !== w || s.fitted.height !== h) return false;
          s.controls.autoRotate = false;
          return true;
        }""", arg=[width, height], timeout=20000)
    return context, page


#: Every label's rectangle on screen, in CSS pixels, from the camera and the
#: sprite alone: a fixed-size sprite is its scale times its distance across.
LABELS = """() => {
  const s = window.__line.scene, cam = s.camera;
  cam.updateMatrixWorld();
  const W = window.innerWidth, H = window.innerHeight;
  const V = cam.position.constructor;
  return s.labels.map((label) => {
    const centre = new V().setFromMatrixPosition(label.matrixWorld).applyMatrix4(cam.matrixWorldInverse);
    const depth = -centre.z;
    const corners = [[-1, -1], [1, 1]].map(([sx, sy]) => {
      const p = new V(centre.x + sx * label.scale.x * depth / 2, centre.y + sy * label.scale.y * depth / 2, centre.z)
        .applyMatrix4(cam.projectionMatrix);
      return [(p.x + 1) / 2 * W, (1 - p.y) / 2 * H];
    });
    return { name: label.name, depth, left: corners[0][0], right: corners[1][0],
             top: corners[1][1], bottom: corners[0][1] };
  });
}"""


@pytest.mark.parametrize("width, height", SIZES)
@pytest.mark.parametrize("line, stations", [("BS1", 8), ("SIMLINE", 6)])
def test_every_station_s_label_is_on_the_screen_and_the_far_ones_as_readable_as_the_near(
        chromium, plant, line, stations, width, height):
    context, page = _opened(chromium, plant, line, width, height)
    labels = page.evaluate(LABELS)
    assert len(labels) == stations, [label["name"] for label in labels]
    for label in labels:
        assert label["depth"] > 0, f"{label['name']} is behind the camera"
        assert label["left"] >= 0 and label["right"] <= width, label
        assert label["top"] >= 0 and label["bottom"] <= height, label
    near = min(labels, key=lambda label: label["depth"])
    far = max(labels, key=lambda label: label["depth"])
    tall = lambda label: label["bottom"] - label["top"]   # noqa: E731
    assert tall(far) >= 18, f"the farthest label is {tall(far):.1f} px tall"
    assert tall(near) / tall(far) <= 2.0, (near, far)
    context.close()


def test_a_body_line_carries_slabs_that_fit_its_belts_and_ride_square(chromium, plant):
    context, page = _opened(chromium, plant, "BS1", 1400, 900)
    belts = page.evaluate("""() => window.__line.scene.beltViews.map((v) => {
      const g = v.pool.geometry;
      g.computeBoundingBox();
      return { type: g.type, colour: v.pool.material.color.getHex(),
               long: g.boundingBox.max.x - g.boundingBox.min.x, length: v.length };
    })""")
    assert len(belts) == 7
    assert {b["type"] for b in belts} == {"BoxGeometry"}
    assert {b["colour"] for b in belts} == {0xC3CCD6}           # bare steel, all the way
    capacity = 3                                                 # the pack's conveyor_capacity
    for belt in belts:
        assert belt["long"] <= belt["length"] / capacity, belt   # a full belt's bodies do not overlap

    # A body put on the first belt is drawn on it, lying along it.
    placed = page.evaluate("""() => {
      const { scene, flow } = window.__line;
      flow.belts[0].items.push({ id: 999, s: 0.5 });
      scene.updateBelts(flow);
      const view = scene.beltViews[0], m = new view.dummy.matrix.constructor();
      view.pool.getMatrixAt(view.pool.count - 1, m);
      const e = m.elements;
      return { count: view.pool.count, yaw: Math.atan2(-e[2], e[0]), angle: view.angle };
    }""")
    assert placed["count"] >= 1
    assert placed["yaw"] == pytest.approx(placed["angle"], abs=1e-6)
    context.close()


def test_a_bottling_line_still_carries_bottles_filled_from_the_filler_on(chromium, plant):
    context, page = _opened(chromium, plant, "SIMLINE", 1400, 900)
    belts = page.evaluate("""() => {
      const s = window.__line.scene;
      const codes = window.__line.layout.stations.map((st) => st.code);
      return s.beltViews.map((v, i) => ({ from: codes[i], type: v.pool.geometry.type,
                                          opacity: v.pool.material.opacity }));
    }""")
    assert len(belts) == 5
    assert "BoxGeometry" not in {b["type"] for b in belts}
    after_filler = [b["from"] for b in belts].index("FILL01")
    empty, full = belts[:after_filler], belts[after_filler:]
    assert empty and full
    assert {b["opacity"] for b in empty} == {0.55} and {b["opacity"] for b in full} == {0.85}
    context.close()


def test_a_cell_drawn_turned_a_quarter_is_drawn_turned_and_its_belt_meets_its_side(chromium, plant):
    context, page = _opened(chromium, plant, "BS1", 1400, 900)
    got = page.evaluate("""() => {
      const s = window.__line.scene;
      const turn = (c) => s.machines.get(c).group.rotation.y;
      const last = s.beltViews[s.beltViews.length - 1];
      return { qi10: turn('QI10'), mf10: turn('MF10'), kind: s.machines.get('QI10').station.kind,
               end: last.startX + last.ux * last.length, start: s.beltViews[0].startX };
    }""")
    assert got["kind"] == "cell"
    assert got["qi10"] == pytest.approx(math.pi / 2)
    assert got["mf10"] == pytest.approx(0.0)
    # The cell is 6 m long and 5.5 m wide: turned, the belt meets it 2.75 m
    # short of its centre at x 84; unturned, UB10's belt leaves 3 m past x 0.
    assert got["end"] == pytest.approx(84 - 2.75)
    assert got["start"] == pytest.approx(3.0)
    context.close()
