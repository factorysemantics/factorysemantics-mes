r"""Film a scene of the ERPNext desk the way `record-scene.py` films the MES.

    python scripts/record-erp-take.py scripts/scenes/film/coil-out-erp.toml
    python scripts/record-erp-take.py scripts/scenes/film/coil-out-erp.toml --plan

`record-scene.py` signs in to an MES, asks it its version and refuses any
request that is not a read. Everything else it does - the scene file, the
drawn cursor and boxes, the badge, the stills, `marks.json`, the mp4 and
the GIF, the README and the clear bottom-right corner - is the same for any
web page, and this reuses all of it. Only the one function that opens the
browser is this file's own, for three differences:

* it signs in to ERPNext (`/api/method/login`) with the login the MES's own
  settings hold for its ERPNext connection (`MES_ERPNEXT_USER` /
  `MES_ERPNEXT_PASSWORD`), and the scene names no user or password: the
  login is never in the scene, the command line, a caption or the output;
* it asks ERPNext its version instead of the MES's `/health`;
* the Frappe desk reads with POSTs (`/api/method/frappe.desk.reportview.get`
  and the like), so the read-only rule is the method's name: a POST to
  `/api/method/` whose name says save, submit, cancel, insert, delete, set,
  rename, amend or update fails the take, and so does any POST, PUT or
  DELETE to `/api/resource/`. Every POST the desk made is listed at the end
  of the run, so a person can see what "read" meant.

A developer tool, like `record-scene.py`; nothing in the MES imports it.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import time
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

_spec = importlib.util.spec_from_file_location("record_scene", REPO / "scripts" / "record-scene.py")
rs = importlib.util.module_from_spec(_spec)
sys.modules["record_scene"] = rs
_spec.loader.exec_module(rs)

WRITE_WORDS = ("save", "submit", "cancel", "insert", "delete", "set_value", "set_", "rename",
               "amend", "update", "make_", "create")
# The desk's own stylesheet keeps a scrollbar's width free down the right
# (`html { scrollbar-gutter: stable }`), which narrows the box a fixed badge is
# placed in: the badge would sit 15 px left of where the recorder looks for it.
# Set at document start, where the overlay is drawn, so the first paints have
# it too, as a sheet adopted by the document: the desk takes out a <style> added
# to the page, and resets <html>'s own style attribute.
HIDE_SCROLLBAR = """
(() => {
  const sheet = new CSSStyleSheet();
  sheet.replaceSync("html { scrollbar-gutter: auto !important; }");
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
})();
"""
# The two POSTs a person's sign-in and the desk's own bookkeeping make, which
# change no business record: the session itself, and "who looked at what".
HOUSEKEEPING = ("login", "frappe.desk.form.utils.add_comment_seen", "frappe.desk.form.load.getdoc",
                "frappe.desk.doctype.route_history.route_history.deferred_insert")


def is_a_write(method: str, url: str) -> bool:
    """Whether a request the desk made would change ERPNext."""
    if method in ("GET", "HEAD"):
        return False
    path = urllib.parse.urlsplit(url).path
    if path.startswith("/api/resource/"):
        return True
    if path.startswith("/api/method/"):
        name = path.removeprefix("/api/method/")
        if name in HOUSEKEEPING:
            return False
        return any(word in name.rsplit(".", 1)[-1] for word in WRITE_WORDS)
    # Socket.io, the desk's assets and the like: not a document.
    return False


def erpnext_version(context, base: str) -> str:
    answer = context.request.get(f"{base}/api/method/frappe.utils.change_log.get_versions")
    if answer.status != 200:
        return "unknown"
    return ((answer.json().get("message") or {}).get("erpnext") or {}).get("version") or "unknown"


def record(scene: dict, out: Path, run_fsmes=None) -> dict:
    """`record_scene.record`, signed in to ERPNext instead of an MES. See the module's docstring."""
    from playwright.sync_api import sync_playwright

    from fsmes.config import get_settings

    if scene["allow_writes"]:
        raise SystemExit("an ERP take writes nothing; take `allow_writes` out of the scene")
    settings = get_settings()
    base = scene["base"].rstrip("/")
    writes: list[str] = []
    posts: set[str] = set()

    shutil.rmtree(out / "raw", ignore_errors=True)
    shutil.rmtree(out / "stills", ignore_errors=True)
    shutil.rmtree(out / "slides", ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    width, height = int(scene["width"]), int(scene["height"])

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": height},
                                      record_video_dir=str(out / "raw"),
                                      record_video_size={"width": width, "height": height})
        signed_in = context.request.post(f"{base}/api/method/login",
                                         form={"usr": settings.erpnext_user, "pwd": settings.erpnext_password})
        if signed_in.status != 200:
            raise SystemExit(f"could not sign in to ERPNext at {base} (HTTP {signed_in.status}) with the "
                             "login the MES's settings hold for it (MES_ERPNEXT_USER / MES_ERPNEXT_PASSWORD)")
        version = erpnext_version(context, base)
        badge = scene["badge"].format(version=version, plant="ERPNext")
        context.add_init_script(rs.overlay_script(badge, bool(scene["captions"])))
        # The desk keeps a vertical scrollbar, which narrows the window a fixed
        # badge is placed in by its width: the badge would sit 15 px left of
        # where the recorder looks for it. Hidden, so it sits where every take's does.
        context.add_init_script(HIDE_SCROLLBAR)
        presses: list[tuple[int, int]] = []
        rs.listen_for_presses(context, presses)
        slides: dict[str, str] = {}
        context.route(f"{base}{rs.SLIDE_PATH}*", lambda route: (
            route.fulfill(status=200, content_type="text/html; charset=utf-8",
                          body=slides.get(route.request.url.rsplit("/", 1)[-1], "no such card"))
        ))

        page = context.new_page()

        def watch(request) -> None:
            if request.method in ("GET", "HEAD"):
                return
            path = urllib.parse.urlsplit(request.url).path
            posts.add(f"{request.method} {path}")
            if is_a_write(request.method, request.url):
                writes.append(f"{request.method} {path}")

        page.on("request", watch)
        stage = rs.BrowserStage(page, out, time.monotonic(), presses, base=base, slides=slides,
                                render=lambda card, path: rs.render_slide(browser, card, path, width, height),
                                run_fsmes=None, type_delay=float(scene["type_delay_seconds"]))
        page.goto(f"{base}/app", wait_until="load")
        scene_with_base = dict(scene)
        scene_with_base["step"] = [{**step, "url": base + step["url"]} if step["do"] == "goto" else step
                                   for step in scene["step"]]
        rs.play(scene_with_base, stage)
        stage.callout_down()
        stage.note("end", "end")
        video = Path(page.video.path())
        context.close()
        browser.close()

    print("== what the desk sent that was not a GET (each a read unless listed as a write):")
    for line in sorted(posts):
        print(f"  {line}")
    (out / "erp-requests.json").write_text(json.dumps({"not_get": sorted(posts), "writes": writes}, indent=2)
                                           + "\n", encoding="utf-8")
    if stage.said != rs.script_lines(scene):
        raise SystemExit("the overlay said something other than the scene's `say` lines; the take is not usable")
    if writes:
        raise SystemExit("the desk wrote to ERPNext during the take, which this take may not do:\n  "
                         + "\n  ".join(writes))
    if stage.corner_faults:
        raise SystemExit("writing of the recording's own landed in the avatar's corner, which stays empty:\n  "
                         + "\n  ".join(stage.corner_faults))
    return {
        "video": video, "plant": "ERPNext", "version": version, "badge": badge, "marks": stage.marks,
        "said": stage.said,
        "slides": sorted(p.relative_to(out).as_posix() for p in (out / "slides").glob("*.png")),
        "stills": sorted(p.relative_to(out).as_posix() for p in (out / "stills").glob("*.png")),
        "took": stage.marks[-1]["at"] if stage.marks else 0.0,
    }


if __name__ == "__main__":
    rs.record = record
    rs.main()
