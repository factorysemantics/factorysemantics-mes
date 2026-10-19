"""Film two nodes of one plant while one of them is killed.

`scripts/record-kill-a-node.sh` drives this; read that script first. It brings
the two nodes up, hands their addresses and container names to this file, and
turns the raw video this writes into an mp4 and a GIF. Running this file on its
own needs two nodes already answering.

What it does, in words:

  1. opens one 1600x900 browser page on the stage served beside it, which puts
     each node's Line view in an iframe with a caption bar over them and a
     terminal strip underneath
  2. signs in once and lets both frames use that session
  3. asks /health which node holds the `reader` lease - the one that reads the
     machines - and kills *that* container, whichever it is
  4. greys that node's frame the moment it stops answering, and captions the
     takeover when the other node's /health says it holds the reader, with the
     seconds measured between the two
  5. starts the dead node again and shows it standing by

Two things on screen are this file's and not the product's, and the caption
under any published recording says so: the yellow box is the dashboard's own
"served by ... machines read by ..." header line, enlarged by injected CSS, and
the grey "no answer" panel is drawn here. The real page underneath it greys
itself and says "reconnecting...".

It is a developer tool. It is not in the wheel, not in CI, and not asked to run
on Windows.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import socketserver
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
STAGE = HERE / "record-kill-a-node.html"

# How long each caption is left on screen. Generous on purpose: a reader has to
# find the yellow line in a thumbnail before it changes.
READ_A_CAPTION_MS = 4000
LOOK_AT_THE_TAKEOVER_MS = 9000
LOOK_AT_THE_COMEBACK_MS = 7000

# The dashboard's header line polls on its own schedule, so a caption written
# the instant `/health` changes is ahead of the pixels behind it: for a second
# or two the yellow line still names the node that just died. The caption is
# the claim and the yellow line is the evidence for it, so the caption waits
# for the line rather than guessing how long it takes.
HEADER_CATCH_UP_LIMIT_MS = 15000


def frame_css(port_a: int, port_b: int) -> str:
    """CSS for the node pages only: hide the replay bar, enlarge the node line.

    Injected into every frame of the page, so it names the two node ports and
    does nothing anywhere else - the stage itself must not be restyled.
    """
    ports = json.dumps([str(port_a), str(port_b)])
    rules = (
        "#replay-bar{display:none!important}"
        "#plant-node{position:fixed!important;top:10px!important;left:50%!important;"
        "transform:translateX(-50%)!important;white-space:nowrap!important;z-index:99999!important;"
        "display:inline-block!important;font-size:27px!important;font-weight:700!important;"
        "color:#111!important;line-height:1.2!important;background:#ffd23f!important;"
        "padding:8px 18px!important;border-radius:8px!important;opacity:1!important;"
        "box-shadow:0 2px 12px rgba(0,0,0,.6)!important}"
    )
    return f"""
(() => {{
  if (!{ports}.includes(location.port)) return;
  const add = () => {{
    if (!document.head || document.getElementById('__rec')) return;
    const style = document.createElement('style');
    style.id = '__rec';
    style.textContent = {json.dumps(rules)};
    document.head.appendChild(style);
  }};
  document.addEventListener('DOMContentLoaded', add);
  add();
}})();
"""


def health(base: str, timeout: float = 1.5) -> dict | None:
    """The node's /health, or None if it did not answer. Never raises."""
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=timeout) as answer:
            return json.load(answer)
    except (OSError, urllib.error.URLError, ValueError):
        return None


def reader_of(body: dict | None) -> str | None:
    """Which node holds the `reader` lease, according to a /health body."""
    if not body:
        return None
    return ((body.get("node") or {}).get("holders") or {}).get("reader")


class Quiet(http.server.SimpleHTTPRequestHandler):
    """The stage server. Silent: its one request is not news, and the lines it
    would print land in the middle of the marks a person is reading."""

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def serve(directory: Path) -> tuple[socketserver.TCPServer, int]:
    """Serve one directory on a free loopback port, in a thread."""
    handler = functools.partial(Quiet, directory=str(directory))
    server = socketserver.TCPServer(("127.0.0.1", 0), handler, bind_and_activate=False)
    server.allow_reuse_address = True
    server.server_bind()
    server.server_activate()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


class Recorder:
    """The page being filmed, and the clock every mark is measured against."""

    def __init__(self, page, started: float) -> None:
        self.page = page
        self.started = started
        self.marks: list[tuple[float, str]] = []

    def mark(self, what: str) -> None:
        at = round(time.monotonic() - self.started, 2)
        self.marks.append((at, what))
        print(f"{at:7.2f}  {what}", flush=True)

    def caption(self, html: str, hold_ms: int = 0) -> None:
        self.page.evaluate("html => window.cap(html)", html)
        if hold_ms:
            self.page.wait_for_timeout(hold_ms)

    def dead_panel(self, which: str, html: str) -> None:
        self.page.evaluate("([which, html]) => window.dead(which, html)", [which, html])

    def terminal(self, command: str, speed_ms: int = 40) -> str:
        """Type a command on the strip, run it for real, show what it said."""
        self.page.evaluate("([cmd, ms]) => window.termType(cmd, ms)", [command, speed_ms])
        self.page.wait_for_timeout(350)
        ran = subprocess.run(command, shell=True, capture_output=True, text=True, check=False)
        said = (ran.stdout + ran.stderr).rstrip()
        self.page.evaluate("text => window.termOut(text)", said)
        return said


def wait_for_header(page, which: str, limit_ms: int = HEADER_CATCH_UP_LIMIT_MS) -> float:
    """Wait until the node's own header line names it as the one reading the machines.

    The line lives inside the node's page, which is a different origin from the
    stage, so it is read through Playwright's frame rather than from the stage's
    own scripts. Returns the seconds waited, for the marks file; gives up
    quietly, because a caption a little early is better than no recording.
    """
    want = f"read by {which}"
    started = time.monotonic()
    while (time.monotonic() - started) * 1000 < limit_ms:
        try:
            said = page.frame_locator(f"#f{which}").locator("#plant-node").inner_text(timeout=1000)
        except Exception:
            said = ""
        if want in said.lower():
            break
        page.wait_for_timeout(250)
    return time.monotonic() - started


def wait_for_both_nodes(base_a: str, base_b: str, seconds: int = 120) -> str:
    """Wait until both nodes answer and one of them holds the reader."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        body_a, body_b = health(base_a), health(base_b)
        holder = reader_of(body_a) or reader_of(body_b)
        if body_a and body_b and holder in ("a", "b"):
            return holder
        time.sleep(1)
    raise SystemExit(
        f"both nodes must answer /health and one of them must hold the reader "
        f"within {seconds}s before recording ({base_a}, {base_b})"
    )


def record(args: argparse.Namespace) -> dict:
    ports = {"a": args.port_a, "b": args.port_b}
    base = {which: f"http://127.0.0.1:{port}" for which, port in ports.items()}
    container = {"a": args.container_a, "b": args.container_b}
    ps = "docker ps --format 'table {{.Names}}\\t{{.Status}}'"

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    stage_dir = out / "stage"
    stage_dir.mkdir(exist_ok=True)
    (stage_dir / STAGE.name).write_text(STAGE.read_text(encoding="utf-8"), encoding="utf-8")
    server, stage_port = serve(stage_dir)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1600, "height": 900},
                record_video_dir=str(out / "raw"),
                record_video_size={"width": 1600, "height": 900},
            )
            # One sign-in for both frames. A cookie is set for the host and not
            # for the port, so signing in to one node signs in to the other -
            # which is the thing a failover is not allowed to undo, and worth
            # seeing in the recording.
            signed_in = context.request.post(
                f"{base['a']}/auth/login",
                data=json.dumps({"code": args.user, "password": args.password}),
                headers={"Content-Type": "application/json"},
            )
            if signed_in.status != 200:
                raise SystemExit(
                    f"could not sign in to {base['a']} as {args.user} (HTTP {signed_in.status}); "
                    "pass --user/--password, or MES_ADMIN_PASSWORD, to match the plant"
                )
            context.add_init_script(frame_css(args.port_a, args.port_b))

            holder = wait_for_both_nodes(base["a"], base["b"])
            other = "b" if holder == "a" else "a"

            page = context.new_page()
            rec = Recorder(page, time.monotonic())
            page.goto(
                f"http://127.0.0.1:{stage_port}/{STAGE.name}"
                f"?a={args.port_a}&b={args.port_b}&line={args.line}&hours={args.hours}",
                wait_until="load",
            )
            page.wait_for_timeout(6000)
            rec.mark("both frames up")

            rec.caption(
                "Two nodes, one bottling plant, one PostgreSQL. Both are up; both answer.",
                READ_A_CAPTION_MS,
            )
            rec.terminal(ps)
            rec.mark("docker ps")
            page.wait_for_timeout(4500)

            rec.caption(
                f"Node <b>{holder}</b> is reading the machines. "
                "Pull its plug — no shutdown, no goodbye.",
                3500,
            )
            rec.terminal(f"docker kill {container[holder]}")
            killed_at = time.monotonic()
            rec.mark(f"killed {holder}")

            took, seen_dead = None, False
            deadline = killed_at + args.patience
            while time.monotonic() < deadline:
                if not seen_dead and health(base[holder], 0.8) is None:
                    seen_dead = True
                    rec.dead_panel(holder, f"node {holder}<br>no answer")
                    rec.caption(f"<b>{holder}</b> is dead. Watch <b>{other}</b>.")
                    rec.mark(f"{holder} stopped answering")
                if reader_of(health(base[other], 0.8)) == other:
                    took = time.monotonic() - killed_at
                    break
                page.wait_for_timeout(250)
            if took is None:
                raise SystemExit(f"node {other} did not take the reader within {args.patience}s")
            rec.mark(f"{other} took the machines after {took:.1f} s")

            waited = wait_for_header(page, other)
            rec.mark(f"{other}'s own header line says so, {waited:.1f} s after it took them")
            rec.caption(
                f"<b>{took:.0f} seconds later:</b> {other} reads the machines. "
                "The numbers kept moving; nobody signed in again.",
                LOOK_AT_THE_TAKEOVER_MS,
            )
            rec.terminal(ps)
            rec.mark("docker ps, one node short")
            page.wait_for_timeout(4500)

            rec.caption(f"Plug {holder} back in.")
            rec.terminal(f"docker start {container[holder]}")
            started_at = time.monotonic()
            rec.mark(f"started {holder}")

            back = None
            deadline = started_at + args.patience
            while time.monotonic() < deadline:
                if health(base[holder], 0.8):
                    back = time.monotonic() - started_at
                    break
                page.wait_for_timeout(400)
            if back is None:
                rec.mark(f"{holder} did not answer again within {args.patience}s")
            else:
                rec.dead_panel(holder, "")
                page.evaluate(
                    "which => { const f = document.getElementById('f' + which); f.src = f.src; }",
                    holder,
                )
                rec.mark(f"{holder} answers again after {back:.1f} s")
                page.wait_for_timeout(5500)
                still = reader_of(health(base[other])) or "nobody yet"
                rec.caption(
                    f"<b>{holder}</b> is back. <b>{still}</b> keeps reading the machines — "
                    "nothing to hand back, nothing counted twice.",
                    LOOK_AT_THE_COMEBACK_MS,
                )
            rec.mark("end")

            video = Path(page.video.path())
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()

    # Rounded on the way out: these two numbers are read by a person and
    # printed in a caption, and a failover measured to the picosecond is a
    # claim this script cannot make.
    return {
        "video": str(video),
        "marks": rec.marks,
        "holder": holder,
        "survivor": other,
        "took": round(took, 2),
        "back": None if back is None else round(back, 2),
    }


def main() -> None:
    ask = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ask.add_argument("--port-a", type=int, required=True, help="host port node a answers on")
    ask.add_argument("--port-b", type=int, required=True, help="host port node b answers on")
    ask.add_argument("--container-a", required=True, help="node a's container name, for `docker kill`")
    ask.add_argument("--container-b", required=True, help="node b's container name")
    ask.add_argument("--out", required=True, help="directory for the raw video and marks.json")
    ask.add_argument("--line", default="SIMLINE", help="which line the dashboards show")
    ask.add_argument("--hours", default="8", help="the window the dashboards report over")
    ask.add_argument("--user", default="ADMIN", help="account to sign in as")
    ask.add_argument("--password", default="admin", help="its password")
    ask.add_argument("--patience", type=float, default=60.0, help="seconds to wait for a takeover or a comeback")
    args = ask.parse_args()

    marks = record(args)
    (Path(args.out) / "marks.json").write_text(json.dumps(marks, indent=1), encoding="utf-8")
    print("video", marks["video"], flush=True)


if __name__ == "__main__":
    main()
