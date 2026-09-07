"""The operator screens.

Principle 5 keeps these pages free of a build step, which is right for a plant
PC that must render them for years. The cost is that nothing compiles them, so
a typo in a selector, a link to a route that does not exist, or a stylesheet
that was never copied all fail silently in front of an operator. These tests
are the compiler that principle deliberately gave up.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsmes.api.app import create_app

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"

PAGES = [
    ("index.html", "app.js"),
    ("orders.html", "orders.js"),
    ("station.html", "station.js"),
    ("quality.html", "quality.js"),
    ("analysis.html", "analysis.js"),
    ("line.html", "line-page.js"),
    ("line3d.html", "line/app.js"),
    ("machine.html", "machine.js"),
    ("machines.html", "machines.js"),
    ("tags.html", "tags.js"),
    ("maintenance.html", "maintenance.js"),
    ("schedule.html", "schedule.js"),
    ("spc.html", "spc.js"),
    ("gauges.html", "gauges.js"),
    ("trace.html", "trace.js"),
    ("masterdata.html", "masterdata.js"),
    ("triggers.html", "triggers.js"),
    ("adjustments.html", "adjustments.js"),
    ("coa.html", "coa.js"),
    ("admin.html", "admin.js"),
    ("instructions.html", "instructions.js"),
    ("ops.html", "ops.js"),
]
ROUTES = ["/dashboard", "/dashboard/station", "/dashboard/orders", "/dashboard/quality",
          "/dashboard/line", "/dashboard/line/3d", "/dashboard/machines", "/dashboard/tags",
          "/dashboard/maintenance", "/dashboard/schedule", "/dashboard/spc", "/dashboard/gauges", "/dashboard/trace",
          "/dashboard/masterdata", "/dashboard/triggers", "/dashboard/adjustments", "/dashboard/coa",
          "/dashboard/machine/MIX01", "/dashboard/analysis", "/dashboard/admin",
          "/dashboard/instructions", "/dashboard/ops"]
# Screens reached by a link or a tab rather than the nav: an object page has
# no nav entry because there is one per machine, and the 3D scene is a tab on
# the Line page. Each must be reachable from somewhere that IS in the nav.
LINKED = {
    "/dashboard/machine/MIX01": "FS.link(\"machine\", ...) from every machine mention",
    "/dashboard/line/3d": "the 3D tab on /dashboard/line",
}


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


@pytest.mark.parametrize("route", ROUTES)
def test_every_screen_is_served(client, route):
    r = client.get(route)
    assert r.status_code == 200
    assert "<html" in r.text.lower()


@pytest.mark.parametrize(("page", "script"), PAGES)
def test_every_selector_the_script_uses_exists_in_its_page(page, script):
    """A mistyped id renders an empty panel and raises nothing."""
    html = (WEB / page).read_text(encoding="utf-8")
    js_path = WEB / script
    if not js_path.is_file():
        pytest.skip(f"{script} not present")
    js = js_path.read_text(encoding="utf-8")

    ids = set(re.findall(r'id="([^"]+)"', html))
    # The shared header is rendered by common.js, so the ids it provides
    # (live dot, identity, sign-out, toast) exist on every page at runtime
    # even though no page's HTML carries them.
    ids |= set(re.findall(r'\.id = "([\w-]+)"', (WEB / "common.js").read_text(encoding="utf-8")))
    used = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)', js))
    used |= set(re.findall(r'querySelector\("#([A-Za-z0-9_-]+)', js))
    missing = sorted(u for u in used if u not in ids)
    assert not missing, f"{script} reaches for ids {page} does not have: {missing}"


@pytest.mark.parametrize("page", [p for p, _ in PAGES])
def test_every_asset_a_page_asks_for_is_present(page):
    """A stylesheet nobody copied is a screen that renders unstyled."""
    html = (WEB / page).read_text(encoding="utf-8")
    for ref in re.findall(r'(?:href|src)="/static/([^"]+)"', html):
        assert (WEB / ref).is_file(), f"{page} references missing asset {ref}"


@pytest.mark.parametrize("page", [p for p, _ in PAGES])
def _nav_manifest():
    """The one nav, as data, from common.js.

    The old tests grepped eight copies of the nav out of eight HTML files -
    and their own docstring admitted the copies were "exactly the thing that
    drifts". Now there is one manifest to check, which is the stronger
    guarantee the copies were compensating for the absence of.
    """
    source = (WEB / "common.js").read_text(encoding="utf-8")
    hrefs = re.findall(r'href: "(/dashboard[^"]*)"', source)
    caps = re.findall(r'cap: (?:"([a-z_.]+)"|null)', source)
    return hrefs, [c for c in caps if c]


def test_every_manifest_link_points_at_a_real_route(client):
    """A dead nav link is the most visible possible bug."""
    hrefs, _ = _nav_manifest()
    assert hrefs, "the nav manifest must exist in common.js"
    for href in hrefs:
        assert client.get(href).status_code == 200, f"manifest links dead route {href}"


@pytest.mark.parametrize("page", [p for p, _ in PAGES])
def test_the_product_is_called_by_its_own_name(page):
    """The engine arrived from MES-TWIN and the branding came with it. A user
    should not be told they are running a project that no longer exists."""
    assert "MES-TWIN" not in (WEB / page).read_text(encoding="utf-8")


def test_every_screen_is_in_the_manifest(client):
    """Every served screen must be reachable from the one nav."""
    hrefs, _ = _nav_manifest()
    missing = set(ROUTES) - set(hrefs) - set(LINKED)
    assert not missing, f"screens unreachable from the nav: {missing}"
    common = (WEB / "common.js").read_text(encoding="utf-8")
    assert "/dashboard/machine/" in common, "object links must be minted by FS.link"
    assert "/dashboard/line/3d" in (WEB / "line-page.js").read_text(encoding="utf-8")


def test_manifest_caps_are_real_capabilities():
    """A gate on a capability the product does not recognise hides the link
    from everybody, forever, silently."""
    from fsmes.services.capabilities import CAPABILITIES

    _, caps = _nav_manifest()
    unknown = [c for c in caps if c not in CAPABILITIES]
    assert not unknown, f"nav gates on unknown capabilities: {unknown}"


def test_the_orders_screen_can_actually_be_narrowed():
    """The complaint that made this screen feel like a toy. The scale pass gave
    the API filters and paging; the page offered neither, and orders.js even
    carried `filterStatus` and `filterText` that nothing on earth could set.

    So this asserts both halves: the control exists, and something listens to
    it. A filter nobody wired is indistinguishable from no filter at all.
    """
    html = (WEB / "orders.html").read_text(encoding="utf-8")
    js = (WEB / "orders.js").read_text(encoding="utf-8")

    for control in ("f-text", "f-status", "f-material", "f-due-after",
                    "f-due-before", "f-clear", "page-prev", "page-next",
                    "order-count"):
        assert f'id="{control}"' in html, f"the orders screen lost its {control}"

    for control in ("#f-text", "#f-status", "#f-material", "#f-due-after",
                    "#f-due-before", "#f-clear", "#page-prev", "#page-next"):
        assert f'$("{control}").addEventListener' in js, f"{control} is not wired"

    # set() for the single-valued filters, append() for status, which is
    # repeatable so that "open" can mean released or running.
    for param in ("q", "material", "due_after", "due_before"):
        assert f'query.set("{param}"' in js, f"the screen never sends {param}"
    assert 'query.append("status"' in js, "the screen never sends status"


def test_the_orders_list_shows_when_work_is_due():
    """A supervisor asking what is late needs the date on the row, not in a
    detail pane behind a click."""
    assert "<th>Due</th>" in (WEB / "orders.html").read_text(encoding="utf-8")
    assert "dueCell" in (WEB / "orders.js").read_text(encoding="utf-8")


def test_the_orders_tiles_are_a_way_into_the_list():
    """Scott: "I should be able to click on the status cards up top. OPEN
    ORDERS should link to actual open orders."

    Buttons for the filters and an anchor for the one that navigates, so the
    keyboard and a screen reader get the behaviour without any extra code.
    """
    html = (WEB / "orders.html").read_text(encoding="utf-8")
    js = (WEB / "orders.js").read_text(encoding="utf-8")

    for card in ("card-open", "card-running", "card-done"):
        assert f'id="{card}"' in html, f"the orders screen lost its {card} tile"
        # Wired either directly or through the TILE_VALUES map - the redesign
        # moved the three tiles into one loop so a fourth tile cannot be
        # added without also being wired.
        wired = (f'$("#{card}").addEventListener' in js
                 or (f'"{card}"' in js and "TILE_VALUES" in js
                     and "addEventListener" in js))
        assert wired, f"{card} is not wired"

    assert 'id="card-yield"' in html and 'href="/dashboard/analysis"' in html, (
        "the yield tile should navigate, not act - an anchor, to a real screen")


def test_the_tiles_and_the_status_dropdown_can_express_the_same_filter():
    """Two controls that cannot agree will disagree on screen. The dropdown
    carries the grouped options the tiles ask for."""
    html = (WEB / "orders.html").read_text(encoding="utf-8")
    assert 'value="released,running"' in html
    assert 'value="completed,closed"' in html


def test_a_clickable_tile_still_looks_like_the_card_it_was():
    """Scott, on the first version: "they look ugly now ... can't they have the
    new functionality without messing with the looks?"

    He was right, and the cause was one line. `.kpi` owns the card - its
    background, its 1px outline, its padding, its grid. The affordance set
    `border: 1px solid transparent`, which is the same specificity and comes
    later in the cascade, so it silently deleted the outline from every tile.
    Adding sub-labels grew the strip a third line taller on top of that.

    So: an interactive tile keeps the `.kpi` class, and `.kpi-action` may not
    set a property `.kpi` already owns. New behaviour is not a licence to
    restyle the page it lands on.
    """
    html = (WEB / "orders.html").read_text(encoding="utf-8")
    css = (WEB / "orders.css").read_text(encoding="utf-8")

    for card in ("card-open", "card-running", "card-done", "card-yield"):
        assert f'class="kpi kpi-action" id="{card}"' in html, (
            f"{card} must keep .kpi, or it stops looking like a card")

    block = css.split(".kpi-action {")[1].split("}")[0]
    for owned in ("border:", "background", "padding", "display", "border-radius"):
        assert owned not in block, (
            f".kpi-action must not set {owned!r} - .kpi owns it, and overriding "
            f"it is what made the tiles look wrong")



@pytest.mark.parametrize("page", [p for p, _ in PAGES])
def test_every_page_uses_the_shared_header(page):
    """One header, rendered from the manifest. A page with its own copy is a
    page that will drift - a stale ninth nav link proved it."""
    html = (WEB / page).read_text(encoding="utf-8")
    assert re.search(r'<header data-nav="[a-z0-9]+"></header>', html), (
        f"{page} does not use the shared header")
    assert 'class="ghost link"' not in html, (
        f"{page} still carries a hand-pasted nav")


@pytest.mark.parametrize("page", [p for p, _ in PAGES])
def test_common_js_loads_before_the_page_script(page):
    """Page scripts bind to elements the shared header renders, so the order
    is load-bearing, not stylistic."""
    html = (WEB / page).read_text(encoding="utf-8")
    scripts = re.findall(r'src="(/static/[^"]+\.js)"', html)
    assert "/static/common.js" in scripts, f"{page} does not load common.js"
    assert scripts.index("/static/common.js") == 0, (
        f"{page} loads {scripts[0]} before common.js")


# ----------------------------------------------------- the dedupe ratchet

# Pages still carrying their own copy of the shared helpers. This list only
# ever shrinks: each screen migrated to FS.* comes off it, and a new page may
# never join it. orders.js was the first off.
STILL_DUPLICATED = {
    "admin.js", "app.js", "assist.js", "design.js",
    "instructions.js", "ops.js", "quality.js",
}


def test_no_new_page_grows_its_own_api_helper():
    """api() was once defined nine times with drifted signatures - one was
    GET-only, one never handled 401. The ratchet stops the count growing
    while the migration shrinks it."""
    offenders = set()
    for path in WEB.glob("*.js"):
        if path.name in ("common.js", "themes.js"):
            continue
        if "async function api(" in path.read_text(encoding="utf-8"):
            offenders.add(path.name)
    assert offenders <= STILL_DUPLICATED, (
        f"new duplicate api() in: {offenders - STILL_DUPLICATED}")


def test_orders_stays_on_the_shared_helpers():
    text = (WEB / "orders.js").read_text(encoding="utf-8")
    assert "async function api(" not in text
    assert "window.FS" in text


def test_the_orders_tiles_admit_to_being_selected():
    """The old tiles were clickable filters that gave no sign they had been
    clicked - the 'unintuitive' half of the complaint that started the
    redesign."""
    assert "aria-pressed" in (WEB / "orders.js").read_text(encoding="utf-8")
    assert '[aria-pressed="true"]' in (WEB / "orders.css").read_text(encoding="utf-8")


def test_the_orders_filters_kept_their_contract():
    """The redesign moved controls into popovers; the ids and the grouped
    option values are pinned by earlier tests and survive."""
    html = (WEB / "orders.html").read_text(encoding="utf-8")
    for control in ("f-text", "f-status", "f-material", "f-due-after",
                    "f-due-before", "f-clear"):
        assert f'id="{control}"' in html, f"{control} went missing"
    assert 'value="released,running"' in html
    assert 'value="completed,closed"' in html
    assert 'value="on_hold"' in html, "the new status must be filterable"


def test_the_misaligned_inline_label_idiom_is_gone():
    """The word 'Due' floated four pixels off its inputs' baseline because an
    inline label carried a bottom margin inside align-items:center. Labels
    now sit above their controls, inside the popover."""
    html = (WEB / "orders.html").read_text(encoding="utf-8")
    assert '<label class="muted small" for="f-due-after">' not in html


def test_code_assets_revalidate_after_a_release(client):
    """A promote used to leave returning browsers on the previous release's
    JavaScript: the origin sent no Cache-Control, so Cloudflare applied a
    four-hour default of its own. The demo then showed the old assistant with
    no sign anything was wrong, because assist.js reads a missing
    /assist/agent/status as 'no agent' and quietly uses the local model."""
    for asset in ("/static/assist.js", "/static/styles.css"):
        got = client.get(asset)
        assert got.status_code == 200, asset
        assert got.headers["cache-control"] == "no-cache", asset


def test_pages_revalidate_too(client):
    """The pages name the scripts, so a stale page is as bad as a stale script."""
    got = client.get("/dashboard")
    assert got.status_code == 200
    assert got.headers["cache-control"] == "no-cache"
