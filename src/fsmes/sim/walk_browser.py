"""The walker's hands and eyes: a headless browser behind a few MCP tools.

`fsmes walk` starts one headless Claude run per task and gives it this
server and nothing else - no shell, no files, no web. The run sees the MES
the way a person does (a screenshot and the text on the screen) and acts the
way a person does (click, type, choose, go back). It never sees a password:
this process signs in before the first tool call, from its own environment,
with the same sign-in `fsmes ui-check` uses.

What it promises, in code rather than in the prompt:

- **A step cap.** Every action is a step; at the cap the tools stop acting
  and say "call finish". The run cannot walk on past it.
- **No admin.** Any request to `/admin/...` is aborted in the browser and a
  `go` there is refused, whatever role the account has.
- **Evidence.** A screenshot after every step, named
  `<date>-<slug>-before-N.png`, and one line per step in a log beside them.
- **One verdict.** `finish` writes the walk's result once, as JSON, for the
  walker to turn into a card. A run that never calls it leaves no result,
  and the walker says so on the card rather than guessing.

Playwright and the `mcp` package are both optional extras (dev and mcp):
the product never imports this module, and a plant PC never carries either.
Linux first; it is run by the lab's walker, not by a plant.

The settings come from the environment `fsmes walk` builds:
FSMES_WALK_BASE, FSMES_WALK_ACCOUNT, FSMES_WALK_SECRET (the password),
FSMES_WALK_SHOTS (the screenshot directory), FSMES_WALK_PREFIX
(`<date>-<slug>`), FSMES_WALK_RESULT (where `finish` writes),
FSMES_WALK_LOG (the step log) and FSMES_WALK_STEPS (the cap).
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

OUTCOMES = ("done", "stuck")
RISKS = ("UI", "data", "schema")

CAP_REACHED = ("The step cap ({cap} steps) is reached; nothing more was done. "
               "Call finish now with what you found.")

# A route a walk never opens, whatever the account's role: the walker acts
# as a person on the floor, and accounts, roles and settings are not theirs.
FORBIDDEN_PREFIXES = ("/admin",)


def forbidden(url: str) -> bool:
    path = urlparse(url).path or "/"
    return any(path == p or path.startswith(p + "/") for p in FORBIDDEN_PREFIXES)


@dataclass
class Walk:
    """One walk's bookkeeping, apart from any browser so it can be tested.

    `driver` is anything with async `act(kind, **args) -> str` and
    `screenshot(path)`; the Playwright one is below.
    """

    driver: object
    shots: Path
    prefix: str
    result_path: Path
    log_path: Path | None = None
    cap: int = 20
    steps: int = 0
    shot_names: list[str] = field(default_factory=list)
    finished: bool = False

    async def step(self, kind: str, **args) -> tuple[str, Path | None]:
        """Do one action and take its screenshot, or refuse at the cap."""
        if self.finished:
            return "This walk is finished; there is nothing more to do.", None
        if self.steps >= self.cap:
            return CAP_REACHED.format(cap=self.cap), None
        self.steps += 1
        try:
            said = await self.driver.act(kind, **args)
        except Exception as exc:                 # a click that missed is a fact
            said = f"That did not work: {type(exc).__name__}: {str(exc)[:300]}"
        name = f"{self.prefix}-before-{self.steps}.png"
        shot = self.shots / name
        try:
            await self.driver.screenshot(shot)
            self.shot_names.append(name)
        except Exception:                        # no picture is not a crash
            shot = None
        self._log({"step": self.steps, "kind": kind, "args": args,
                   "said": said[:500], "shot": name if shot else None})
        left = self.cap - self.steps
        return f"{said}\n\n(step {self.steps} of {self.cap}; {left} left)", shot

    def finish(self, outcome: str, happened: str, stuck_where: str = "",
               hurdles: str = "", expected: str = "", risk: str = "UI") -> str:
        if self.finished:
            return "Already finished."
        if outcome not in OUTCOMES:
            return f"outcome must be one of {', '.join(OUTCOMES)}; nothing was written."
        lines = [ln.strip() for ln in happened.strip().splitlines() if ln.strip()]
        if not lines:
            return "happened needs your three lines; nothing was written."
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        self.result_path.write_text(json.dumps({
            "outcome": outcome,
            "happened": lines[:3],
            "stuck_where": stuck_where.strip(),
            "hurdles": hurdles.strip(),
            "expected": expected.strip(),
            "risk": risk if risk in RISKS else "UI",
            "steps": self.steps,
            "cap": self.cap,
            "shots": self.shot_names,
        }, indent=1), encoding="utf-8")
        self.finished = True
        self._log({"step": self.steps, "kind": "finish", "outcome": outcome})
        return "Written. The walk is over; stop here."

    def _log(self, row: dict) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")


# ----------------------------------------------------------------- browser

# What is on the screen, as text: the headings, the controls a person could
# use, and the start of what the page says. Short on purpose - the
# screenshot carries the rest.
SCREEN_PROBE = """() => {
    const seen = el => { const b = el.getBoundingClientRect();
        return b.width > 0 && b.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
    const txt = el => (el.innerText || el.value || el.getAttribute('aria-label')
                       || el.getAttribute('title') || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
    const label = el => { const id = el.id && document.querySelector(`label[for="${el.id}"]`);
        return (id && id.innerText.trim()) || el.getAttribute('aria-label')
               || el.getAttribute('placeholder') || el.name || el.type || ''; };
    const pick = (sel, f) => [...document.querySelectorAll(sel)].filter(seen).map(f).filter(Boolean);
    return {
        headings: pick('h1,h2,h3', txt).slice(0, 15),
        buttons: [...new Set(pick('button,[role=button],input[type=submit]', txt))].slice(0, 40),
        links: [...new Set(pick('a[href]', txt))].slice(0, 50),
        fields: pick('input:not([type=hidden]):not([type=submit]),textarea,select',
                     el => `${label(el)} [${el.tagName.toLowerCase()}${el.type ? ':' + el.type : ''}]`
                           + (el.tagName === 'SELECT'
                              ? ' options: ' + [...el.options].map(o => o.text.trim()).slice(0, 12).join(' | ')
                              : (el.value ? ` = ${String(el.value).slice(0, 40)}` : ''))).slice(0, 30),
        text: (document.body ? document.body.innerText : '').replace(/\\n{2,}/g, '\\n').slice(0, 2500),
    };
}"""


class PlaywrightDriver:
    """A signed-in headless Chromium on one plant."""

    def __init__(self, base: str, account: str, secret: str):
        self.base = base.rstrip("/")
        self.account, self.secret = account, secret
        self.downloads: list[str] = []
        self._pw = self._browser = self.context = self.page = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch()
        self.context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800}, accept_downloads=True)
        reply = await self.context.request.post(
            f"{self.base}/auth/login",
            data=json.dumps({"code": self.account, "password": self.secret}),
            headers={"Content-Type": "application/json"})
        if not reply.ok:
            raise RuntimeError(f"sign-in as {self.account!r} failed: {reply.status}")
        await self.context.route(
            lambda url: forbidden(url) and urlparse(url).netloc == urlparse(self.base).netloc,
            lambda route: route.abort())
        self.page = await self.context.new_page()
        self.page.on("download", lambda d: self.downloads.append(d.suggested_filename))
        await self.page.goto(f"{self.base}/", wait_until="load", timeout=30000)
        await self.page.wait_for_timeout(1500)

    async def stop(self) -> None:
        for closer in (self._browser and self._browser.close, self._pw and self._pw.stop):
            if closer:
                with contextlib.suppress(Exception):
                    await closer()

    async def _settle(self) -> None:
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("load", timeout=10000)
        await self.page.wait_for_timeout(1200)      # the screens fetch after load

    async def _target(self, target: str, kinds: tuple[str, ...]):
        """The element a person means by `target`: a button or link by its
        words first, then a field by its label or placeholder, then any
        text, then a CSS selector as written."""
        page = self.page
        tries = [page.get_by_role(k, name=target) for k in kinds]
        tries += [page.get_by_label(target), page.get_by_placeholder(target),
                  page.get_by_text(target)]
        if any(c in target for c in "#.[]>=:"):
            tries.append(page.locator(target))
        for loc in tries:
            try:
                if await loc.count():
                    first = loc.first
                    if await first.is_visible():
                        return first
            except Exception:
                continue
        raise LookupError(f"nothing on the screen called {target!r}")

    async def act(self, kind: str, **args) -> str:
        before = len(self.downloads)
        if kind == "look":
            pass
        elif kind == "click":
            el = await self._target(args["target"], ("button", "link", "tab", "menuitem",
                                                     "checkbox", "radio"))
            await el.click(timeout=5000)
            await self._settle()
        elif kind == "type":
            el = await self._target(args["target"], ("textbox", "spinbutton", "searchbox", "combobox"))
            await el.fill(args["text"], timeout=5000)
            await self.page.wait_for_timeout(400)
        elif kind == "choose":
            el = await self._target(args["target"], ("combobox", "listbox"))
            try:
                await el.select_option(label=args["option"], timeout=5000)
            except Exception:
                await el.select_option(args["option"], timeout=5000)
            await self._settle()
        elif kind == "go":
            url = urljoin(self.base + "/", args["path"])
            if urlparse(url).netloc != urlparse(self.base).netloc:
                return "Refused: that is not this plant."
            if forbidden(url):
                return "Refused: administration is not part of this walk."
            await self.page.goto(url, wait_until="load", timeout=30000)
            await self._settle()
        elif kind == "back":
            await self.page.go_back(wait_until="load", timeout=15000)
            await self._settle()
        else:
            return f"unknown action {kind}"
        return await self.describe(new_downloads=self.downloads[before:])

    async def describe(self, new_downloads=()) -> str:
        screen = await self.page.evaluate(SCREEN_PROBE)
        lines = [f"Page: {urlparse(self.page.url).path} - {await self.page.title()}"]
        if new_downloads:
            lines.append("A file downloaded: " + ", ".join(new_downloads))
        for key in ("headings", "buttons", "links", "fields"):
            if screen.get(key):
                lines.append(f"{key.title()} ({len(screen[key])}): " + " | ".join(screen[key]))
        lines += ["Text on the screen:", screen.get("text", "")]
        return "\n".join(lines)

    async def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(path))


# ---------------------------------------------------------------- the server

def build_server(walk: Walk, driver: PlaywrightDriver):
    from mcp.server.mcpserver import Image, MCPServer

    server = MCPServer(
        name="walk",
        instructions=("Your hands and eyes on the MES screen. Every action is "
                      "one step; call finish when the task is done or you are stuck."))
    started = {"ok": False}

    async def ensure() -> None:
        if not started["ok"]:
            await driver.start()
            started["ok"] = True

    async def act(kind: str, **args):
        await ensure()
        said, shot = await walk.step(kind, **args)
        return [said, Image(path=shot)] if shot else said

    @server.tool()
    async def look():
        """Look at the screen as it is now: a screenshot and its text. One step."""
        return await act("look")

    @server.tool()
    async def click(target: str):
        """Click a button, link, tab or menu item by the words on it (or a
        CSS selector if nothing else works). One step."""
        return await act("click", target=target)

    @server.tool(name="type")
    async def type_(target: str, text: str):
        """Type text into a field, named by its label or placeholder. One step."""
        return await act("type", target=target, text=text)

    @server.tool()
    async def choose(target: str, option: str):
        """Pick an option in a drop-down, named by its label. One step."""
        return await act("choose", target=target, option=option)

    @server.tool()
    async def go(path: str):
        """Open a page of this plant by its path, as a person typing it in the
        address bar would. Administration is refused. One step."""
        return await act("go", path=path)

    @server.tool()
    async def back():
        """The browser's back button. One step."""
        return await act("back")

    @server.tool()
    async def finish(outcome: str, happened: str, stuck_where: str = "",
                     hurdles: str = "", expected: str = "", risk: str = "UI") -> str:
        """End the walk. outcome: "done" or "stuck". happened: three short
        lines, first person, plain words, what you did and what you found.
        stuck_where: the screen and the thing that was missing or broken.
        hurdles: what slowed you down on the way. expected: what you
        expected to see. risk: your guess at what a fix touches - "UI" (a
        screen), "data" (what the plant records) or "schema" (the database)."""
        said = walk.finish(outcome, happened, stuck_where, hurdles, expected, risk)
        await driver.stop()
        return said

    return server


def main() -> None:
    env = os.environ
    driver = PlaywrightDriver(env["FSMES_WALK_BASE"], env["FSMES_WALK_ACCOUNT"],
                              env.get("FSMES_WALK_SECRET", ""))
    walk = Walk(driver=driver, shots=Path(env["FSMES_WALK_SHOTS"]),
                prefix=env["FSMES_WALK_PREFIX"], result_path=Path(env["FSMES_WALK_RESULT"]),
                log_path=Path(env["FSMES_WALK_LOG"]) if env.get("FSMES_WALK_LOG") else None,
                cap=int(env.get("FSMES_WALK_STEPS", "20")))
    build_server(walk, driver).run("stdio")


if __name__ == "__main__":
    main()
