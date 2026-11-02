"""In a browser: the 3D line view draws a station from the plant's own model,
and keeps the built-in shape when a model is missing or late.

The plant is the six-station line with a pack holding three cubes: the
filler's, the washer's and the inspector's. In the browser, the filler's model
is held back by over a second (so the view has to wait for it, as it would on
a phone over the tailnet), the washer's answers 404, and the inspector's never
answers at all. The filler must end up as the cube; the washer and the
inspector must keep their primitives, each with one console warning, the
inspector's after the 3 s limit.

The palletiser's own model (`PAL01.glb`) is the cube drawn to the convention
the crew's models follow: its material is named `signal` and its node `spin`.
That one carries the state colour itself and turns while running, and gets
no floor ring; the plain cube on the filler, which has no `signal`, gets the
ring instead.

Marked `browser` as well as `slow`: `pytest -m browser` is the tier CI runs
Chromium for.
"""

from __future__ import annotations

import importlib.util
import socket
import threading
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
KEPSIM_MAP = REPO / "config" / "tag_map_kepsim.json"

#: Longer than the page takes to draw on loopback, shorter than the 3 s limit.
HELD_BACK_MS = 1200

_spec = importlib.util.spec_from_file_location("make_cube_glb", Path(__file__).parent / "make_cube_glb.py")
make_cube_glb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_cube_glb)


@pytest.fixture(scope="module")
def plant(tmp_path_factory):
    import uvicorn

    from fsmes import config, db
    from fsmes.api.app import create_app
    from fsmes.db import Base, make_engine
    from fsmes.seed import seed_demo_plant
    from fsmes.seed_kepsim import seed_kepsim_line
    from fsmes.services import auth

    root = tmp_path_factory.mktemp("line3dmodels")
    pack = root / "pack"
    for kind in ("filler", "washer", "inspector"):
        make_cube_glb.write_cube(pack / "models" / f"{kind}.glb")
    make_cube_glb.write_cube(pack / "models" / "PAL01.glb", material="signal", node="spin")
    url = f"sqlite:///{root / 'plant.db'}"

    with pytest.MonkeyPatch.context() as env:
        env.setenv("MES_DATABASE_URL", url)
        env.setenv("MES_PLANT_TIMEZONE", "UTC")
        env.setenv("MES_LINE_LAYOUT_FILE", str(pack / "line_layout.json"))
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
        db.get_sessionmaker.cache_clear()

        engine = make_engine(url)
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            seed_demo_plant(session)
            auth.ensure_builtin_roles(session)
            seed_kepsim_line(session, tag_map=KEPSIM_MAP)
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


def _signed_in(chromium, base):
    import json

    context = chromium.new_context(viewport={"width": 1400, "height": 900})
    reply = context.request.post(
        f"{base}/auth/login", data=json.dumps({"code": "ADMIN", "password": "admin"}),
        headers={"Content-Type": "application/json"})
    assert reply.ok, f"sign-in failed: {reply.status}"
    return context


#: For one station: is the model in the scene and showing, and is any of the
#: built-in body still showing.
DRESS = """code => {
  const m = window.__line.scene.machines.get(code);
  const kids = m.group.children;
  const model = kids.find((c) => c.name.startsWith("model:"));
  return {
    state: m.modelState,
    model: model ? model.name : null,
    modelShowing: Boolean(model && model.visible),
    builtInShowing: kids.some((c) => !c.name.startsWith("model:") && c.name !== "state-ring" && c.visible),
    ring: kids.some((c) => c.name === "state-ring"),
  };
}"""


def test_the_filler_is_drawn_from_its_model_and_a_missing_or_late_model_keeps_the_built_in_shape(
        chromium, plant):
    context = _signed_in(chromium, plant)
    page = context.new_page()
    warnings = []
    page.on("console", lambda msg: warnings.append(msg.text) if msg.type == "warning" else None)

    def models(route):
        name = route.request.url.rsplit("/", 1)[-1]
        if name == "filler.glb":
            page.wait_for_timeout(HELD_BACK_MS)
            route.continue_()
        elif name == "washer.glb":
            route.fulfill(status=404, body="no such model")
        elif name == "inspector.glb":
            pass  # never answered, so only the 3 s limit ends it
        else:
            route.continue_()

    page.route("**/line/models/*", models)
    page.goto(f"{plant}/dashboard/line/3d?line=SIMLINE&debug=1", wait_until="load", timeout=30000)

    page.wait_for_function("() => window.__line && window.__line.scene.machines.size === 6", timeout=20000)
    # Drawn at once, before any model has arrived: never blank because of one.
    before = page.evaluate(DRESS, "FILL01")
    assert before["builtInShowing"] and before["model"] is None

    page.wait_for_function(
        """() => ['FILL01', 'WASH01', 'QI01', 'PAL01'].every(
             (c) => ['loaded', 'failed'].includes(window.__line.scene.machines.get(c).modelState))""",
        timeout=15000)

    filler = page.evaluate(DRESS, "FILL01")
    assert filler == {"state": "loaded", "model": "model:/line/models/filler.glb",
                      "modelShowing": True, "builtInShowing": False, "ring": True}, warnings
    for code in ("WASH01", "QI01"):
        kept = page.evaluate(DRESS, code)
        assert kept == {"state": "failed", "model": None, "modelShowing": False,
                        "builtInShowing": True, "ring": False}, code
    # A machine with no model in the pack is exactly as it was.
    assert page.evaluate("() => window.__line.scene.machines.get('LD01').modelState") is None

    # A model drawn to the convention carries the state colour on its own
    # `signal` material - a copy of its own - and turns its `spin` node.
    palletiser = page.evaluate(DRESS, "PAL01")
    assert palletiser["state"] == "loaded" and palletiser["ring"] is False
    signals = page.evaluate("""() => {
      const m = window.__line.scene.machines.get('PAL01');
      return {
        bands: m.bands.map((b) => b.material.name),
        colour: m.bands[0].material.color.getHex(),
        spin: m.spin ? m.spin.name : null,
      };
    }""")
    assert signals == {"bands": ["signal"], "colour": 0x6E7681, "spin": "spin"}

    about = [w for w in warnings if "keeps its built-in shape" in w]
    assert len(about) == 2, warnings
    assert any("washer.glb" in w for w in about)
    assert any("inspector.glb" in w and "longer than 3 s" in w for w in about)
    context.close()
