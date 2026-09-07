"""The UI conformance loop, layer 1: deterministic, and free of the GPU.

The KPI-tile regression of 2026-09-01 shipped because nothing watched the
rendered screens: a one-line CSS override deleted every card's outline and
the test suite had no opinion. The fix that time was a written rule turned
into a test. This module is that idea generalised - the screens are crawled
by a real browser, and what it finds are facts, not opinions:

- a link that answers with an error, on any screen, in any theme
- a console error or a failed request during load
- a computed-style drift on a component the style contract names, against a
  baseline that was deliberately accepted

No model is involved. Scott's standing rule (docs/ai/BUDGET.md) keeps qwen
for the agentic MES; guarding the UI is deterministic work, and the nightly
rollup may spend one paragraph narrating what this module found - that is
the loop's entire GPU cost.

Screenshots are captured and kept as evidence beside the run, but they are
NOT diffed for pass/fail: the plants are alive, so every screenshot differs
in its numbers, and a check that always fires is a check people learn to
ignore. Style snapshots carry the enforcement; pixels carry the context.

Baselines are JSON in the repository (tests/ui/baselines/), so a deliberate
restyle ships its new baseline in the same commit - drift someone chose is a
reviewed diff, drift nobody chose fails the check. That is the flexibility
mechanism, and it is the whole answer to "coherent but still developable".

Playwright imports lazily: the product never depends on it (principle 5).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

REPO = Path(__file__).resolve().parents[3]
BASELINES = REPO / "tests" / "ui" / "baselines"
BACKLOG = REPO / "docs" / "design" / "backlog"

# Evidence, not understanding: screenshots and run reports live outside the
# repository and are prunable. The same split the results store uses.
WORKDIR = Path.home() / ".local" / "share" / "fsmes" / "ui-check"
STATE = WORKDIR / "state.json"

THEMES = ("control-room", "daylight", "high-contrast", "night-shift")

# The components the style contract (docs/design/STYLE.md) speaks for. Each
# is snapshotted once per route and theme; a selector missing from a page is
# simply absent, not an error - not every screen has a KPI strip.
WATCHED = {
    "header": "header",
    "kpi-card": ".kpi",
    "panel": ".panel",
    # Not a bare "table": the Ops screen's Local AI panel is hidden until
    # its endpoint answers, so the FIRST table on that page depends on a race
    # the crawler cannot win deterministically - two honest runs disagreed
    # 12.5px vs 14px. Watch the tables that are always there.
    "table": "table:not(#ai-consumers)",
    "primary-button": "button",
    "filter-bar": ".filters",
    # Not .pill: a pill is coloured by the state it reports, so its computed
    # style is runtime data. The first crawl proved it - the station page
    # filed six findings because a machine went from running to idle between
    # two runs. A check that fires on the plant working is a check people
    # learn to ignore; pill palette conformance belongs to the themes test.
}

# The properties drift shows up in. Deliberately few: snapshotting every
# property turns each refactor into a wall of noise, and a check that cries
# wolf gets deleted.
PROPERTIES = (
    "color", "background-color", "border", "border-radius",
    "font-size", "font-weight", "padding", "display",
)

ROUTE_SOURCE = "src/fsmes/api/app.py"


def routes() -> list[str]:
    """Every dashboard page the app serves, read from the app itself so a
    new screen is watched the day it exists rather than when somebody
    remembers this list."""
    text = (REPO / ROUTE_SOURCE).read_text(encoding="utf-8")
    found = re.findall(r'@app\.get\("(/dashboard[^"]*)"', text)
    pages = sorted(set(found))
    assert pages, f"no dashboard routes found in {ROUTE_SOURCE}"
    return pages


# ------------------------------------------------------------------ crawling

def _sample_codes(context, base: str) -> dict[str, str]:
    """A real code for each templated segment an object page takes."""
    try:
        states = context.request.get(f"{base}/equipment/states").json()
        first = sorted(s["equipment"] for s in states)[0] if states else None
    except Exception:  # a plant with no machines yet: leave the template as is
        first = None
    return {"code": first} if first else {}


def _fill(route: str, sample: dict[str, str]) -> str:
    for key, value in sample.items():
        route = route.replace("{" + key + "}", value)
    return route


def _sign_in(context, base: str, user: str, password: str) -> None:
    """Sign in through the context's own request jar: login sets the
    `mes_session` HttpOnly cookie, and Playwright shares it between the API
    jar and the pages - so both the screens and the link checks are
    authenticated the way a person's browser is."""
    reply = context.request.post(
        f"{base}/auth/login",
        data=json.dumps({"code": user, "password": password}),
        headers={"Content-Type": "application/json"})
    if not reply.ok:
        raise RuntimeError(f"sign-in as {user!r} failed: {reply.status}")


def crawl(base: str, user: str = "AGENT", password: str = "agent-lab-only",
          themes: tuple[str, ...] = THEMES, echo=print) -> dict:
    """Visit every screen in every theme; come back with facts."""
    from playwright.sync_api import sync_playwright

    run: dict = {
        "base": base, "at": datetime.now(UTC).isoformat(),
        "routes": routes(), "themes": list(themes),
        "console_errors": [], "failed_requests": [],
        "broken_links": [], "styles": {},
    }
    shots_dir = WORKDIR / "shots" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    shots_dir.mkdir(parents=True, exist_ok=True)
    run["screenshots"] = str(shots_dir)

    hrefs: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        _sign_in(context, base, user, password)
        # Object pages are one file for many objects: `/dashboard/machine/{code}`
        # is crawled as the plant's first machine, and the route keeps its
        # template name so baselines survive a re-seed.
        sample = _sample_codes(context, base)

        for theme in themes:
            context.add_init_script(
                f"window.localStorage.setItem('fsmes-theme', {json.dumps(theme)});")
            run["styles"][theme] = {}
            for route in run["routes"]:
                page = context.new_page()
                page.on("console", lambda m, r=route, t=theme: (
                    m.type == "error" and run["console_errors"].append(
                        {"route": r, "theme": t, "text": m.text[:300]})))
                page.on("requestfailed", lambda q, r=route, t=theme: (
                    run["failed_requests"].append(
                        {"route": r, "theme": t, "url": q.url,
                         "why": q.failure or "failed"})))
                # "load" plus a settle, never "networkidle": these screens
                # poll for a living (the line view continuously), so idle
                # would never come. Two seconds lets the first data paint.
                page.goto(f"{base}{_fill(route, sample)}", wait_until="load", timeout=30000)
                page.wait_for_timeout(2000)

                for a in page.eval_on_selector_all(
                        "a[href]", "els => els.map(e => e.getAttribute('href'))"):
                    if a and not a.startswith(("#", "mailto:", "javascript:")):
                        hrefs.add(urljoin(f"{base}{route}", a))

                snap: dict = {}
                for name, selector in WATCHED.items():
                    got = page.evaluate(
                        """([sel, props]) => {
                            const el = document.querySelector(sel);
                            if (!el) return null;
                            const cs = getComputedStyle(el);
                            const out = {};
                            for (const p of props) out[p] = cs.getPropertyValue(p);
                            return out;
                        }""", [selector, list(PROPERTIES)])
                    if got is not None:
                        snap[name] = got
                run["styles"][theme][route] = snap

                page.screenshot(
                    path=shots_dir / f"{theme}{route.replace('/', '-')}.png",
                    full_page=True)
                page.close()
                echo(f"  {theme:<14} {route}")

        # Links once each, through the signed-in context - a 401 on a page
        # the nav links to is exactly the kind of break this exists to catch.
        origin = urlparse(base).netloc
        for href in sorted(hrefs):
            if urlparse(href).netloc != origin:
                continue                      # external links are not ours to promise
            reply = context.request.get(href)
            if reply.status >= 400:
                run["broken_links"].append({"url": href, "status": reply.status})

        browser.close()
    return run


# ------------------------------------------------------------------ judging

def compare(run: dict) -> list[dict]:
    """Facts against the accepted baseline. Returns findings."""
    findings = []

    for entry in run["broken_links"]:
        findings.append({
            "kind": "broken-link",
            "what": f"{entry['url']} answers {entry['status']}",
            "where": entry["url"]})
    for entry in run["console_errors"]:
        findings.append({
            "kind": "console-error",
            "what": f"{entry['route']} ({entry['theme']}): {entry['text']}",
            "where": f"{entry['route']}#{entry['theme']}"})
    for entry in run["failed_requests"]:
        findings.append({
            "kind": "failed-request",
            "what": f"{entry['route']} ({entry['theme']}): {entry['url']} {entry['why']}",
            "where": entry["url"]})

    for theme, pages in run["styles"].items():
        base_path = BASELINES / f"{theme}.json"
        if not base_path.is_file():
            findings.append({
                "kind": "no-baseline",
                "what": f"theme {theme} has no accepted baseline - "
                        f"run with --accept once the look is right",
                "where": theme})
            continue
        accepted = json.loads(base_path.read_text(encoding="utf-8"))
        for route, snap in pages.items():
            before = accepted.get(route, {})
            for component in sorted(set(before) | set(snap)):
                if component not in before:
                    findings.append({
                        "kind": "style-drift",
                        "what": f"{route} ({theme}): component '{component}' is new "
                                f"- accept if deliberate",
                        "where": f"{route}:{component}:{theme}"})
                elif component not in snap:
                    findings.append({
                        "kind": "style-drift",
                        "what": f"{route} ({theme}): component '{component}' vanished",
                        "where": f"{route}:{component}:{theme}"})
                else:
                    for prop in PROPERTIES:
                        old, new = before[component].get(prop), snap[component].get(prop)
                        if old != new:
                            findings.append({
                                "kind": "style-drift",
                                "what": f"{route} ({theme}): {component} {prop} "
                                        f"changed {old!r} -> {new!r}",
                                "where": f"{route}:{component}:{prop}:{theme}"})
    return findings


def accept(run: dict) -> list[Path]:
    """Make the current look the accepted look, one file per theme -
    committed in the same branch as the change that made it right."""
    BASELINES.mkdir(parents=True, exist_ok=True)
    written = []
    for theme, pages in run["styles"].items():
        path = BASELINES / f"{theme}.json"
        path.write_text(json.dumps(pages, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


# ------------------------------------------------------------------ filing

def fingerprint(finding: dict) -> str:
    """Stable identity for a finding, so a known issue is filed once, not
    re-filed on every run until someone fixes it."""
    raw = f"{finding['kind']}|{finding['where']}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _known() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"filed": {}}


def file_new(findings: list[dict], echo=print) -> list[Path]:
    """New findings become backlog notes with status `inbox`, exactly where
    Scott's own ideas land, for /design-triage to judge. ui-check never
    judges - it only reports."""
    state = _known()
    filed = state["filed"]
    written = []
    for finding in findings:
        fp = fingerprint(finding)
        if fp in filed:
            continue
        slug = f"ui-{finding['kind']}-{fp}"
        path = BACKLOG / f"{slug}.md"
        today = datetime.now(UTC).date().isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([
            "---",
            f"title: {finding['what'][:90]}",
            "status: inbox",
            "conversation: 0",
            "turns: []",
            "route: ",
            "plant: ",
            f"created: {today}",
            f"updated: {today}",
            "branch: ",
            "tags: [fsmes, design, backlog, ui-check]",
            "---",
            "",
            f"# {finding['what'][:90]}",
            "",
            "**inbox** — found by `fsmes ui-check` (deterministic; no model involved).",
            "",
            f"- kind: `{finding['kind']}`",
            f"- detail: {finding['what']}",
            f"- fingerprint: `{fp}`",
            "",
            "Judged by `/design-triage`. If this drift was deliberate, the fix is",
            "`fsmes ui-check --accept` in the branch that made the change - not",
            "closing this note by hand.",
            "",
        ]), encoding="utf-8")
        filed[fp] = {"at": datetime.now(UTC).isoformat(), "note": path.name}
        written.append(path)
        echo(f"  filed {path.name}")
    state["unaccepted"] = len(findings)
    state["last_run"] = datetime.now(UTC).isoformat()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return written


def unaccepted_count() -> int | None:
    """For the nightly rollup: how much drift stands unaccepted, or None
    when ui-check has never run - unknown, not zero."""
    state = _known()
    if "last_run" not in state:
        return None
    return int(state.get("unaccepted", 0))
