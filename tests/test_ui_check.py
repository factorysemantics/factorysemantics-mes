"""The UI conformance loop's judgment, without a browser.

The crawler needs Chromium and a live plant; the judgment does not, and the
judgment is where the promises live: drift is measured against a baseline
someone accepted, a known finding is filed once, and "never ran" is unknown
rather than a comforting zero.
"""


import pytest

from fsmes.sim import ui_check


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_check, "BASELINES", tmp_path / "baselines")
    monkeypatch.setattr(ui_check, "BACKLOG", tmp_path / "backlog")
    monkeypatch.setattr(ui_check, "WORKDIR", tmp_path / "work")
    monkeypatch.setattr(ui_check, "STATE", tmp_path / "work" / "state.json")
    yield


def a_run(styles=None, **over):
    run = {"broken_links": [], "console_errors": [], "failed_requests": [],
           "styles": styles or {}}
    run.update(over)
    return run


CARD = {"color": "rgb(230, 237, 243)", "border": "1px solid rgb(48, 54, 61)",
        "_count": 4}


# ------------------------------------------------------------------ compare

def test_a_screen_matching_its_baseline_is_clean():
    run = a_run(styles={"daylight": {"/dashboard/orders": {"kpi-card": CARD}}})
    ui_check.accept(run)
    assert ui_check.compare(run) == []


def test_the_tile_regression_shape_is_caught():
    """The exact 2026-09-01 incident: a border silently deleted. The finding
    names the route, the component, the property and both values - enough to
    fix without re-running anything."""
    run = a_run(styles={"daylight": {"/dashboard/orders": {"kpi-card": dict(CARD)}}})
    ui_check.accept(run)

    run["styles"]["daylight"]["/dashboard/orders"]["kpi-card"] = {
        **CARD, "border": "1px solid rgba(0, 0, 0, 0)"}
    found = ui_check.compare(run)
    assert len(found) == 1
    assert found[0]["kind"] == "style-drift"
    assert "border" in found[0]["what"]
    assert "rgb(48, 54, 61)" in found[0]["what"]


def test_a_theme_with_no_baseline_says_so_instead_of_passing():
    """Nothing accepted means nothing to compare - which is a state to fix,
    not a pass."""
    run = a_run(styles={"night-shift": {"/dashboard": {}}})
    found = ui_check.compare(run)
    assert [f["kind"] for f in found] == ["no-baseline"]
    assert "--accept" in found[0]["what"]


def test_broken_links_and_console_errors_need_no_baseline():
    run = a_run()
    run["broken_links"] = [{"url": "http://p/dashboard/gone", "status": 404}]
    run["console_errors"] = [{"route": "/dashboard", "theme": "daylight",
                              "text": "ReferenceError: x is not defined"}]
    kinds = sorted(f["kind"] for f in ui_check.compare(run))
    assert kinds == ["broken-link", "console-error"]


def test_a_vanished_component_is_drift():
    run = a_run(styles={"daylight": {"/dashboard/orders": {"kpi-card": CARD}}})
    ui_check.accept(run)
    run["styles"]["daylight"]["/dashboard/orders"] = {}
    found = ui_check.compare(run)
    assert len(found) == 1 and "vanished" in found[0]["what"]


# ------------------------------------------------------------------ filing

def test_a_finding_is_filed_once_not_every_run():
    """A known issue re-filed daily is a pipeline teaching people to ignore
    it."""
    finding = {"kind": "broken-link", "what": "x answers 404", "where": "x"}
    first = ui_check.file_new([finding], echo=lambda *_: None)
    second = ui_check.file_new([finding], echo=lambda *_: None)
    assert len(first) == 1
    assert second == []


def test_a_filed_note_is_an_inbox_note_the_backlog_understands():
    from fsmes.services import design_triage

    finding = {"kind": "console-error", "what": "boom on /dashboard", "where": "d"}
    [path] = ui_check.file_new([finding], echo=lambda *_: None)
    meta = design_triage.front_matter(path.read_text(encoding="utf-8"))
    assert meta["status"] == "inbox"
    assert "ui-check" in meta["tags"]
    assert "/design-triage" in path.read_text(encoding="utf-8")


def test_fingerprints_ignore_the_parts_that_change_between_runs():
    a = {"kind": "style-drift", "what": "border changed A -> B", "where": "r:c:p:t"}
    b = {"kind": "style-drift", "what": "border changed B -> C", "where": "r:c:p:t"}
    assert ui_check.fingerprint(a) == ui_check.fingerprint(b), (
        "the same drifting property must be one finding, however many times "
        "its value moves")


# ------------------------------------------------------------------ unknown

def test_never_run_is_unknown_not_zero():
    """The rollup must not print 'clean' about a loop that has never run."""
    assert ui_check.unaccepted_count() is None
    ui_check.file_new([], echo=lambda *_: None)
    assert ui_check.unaccepted_count() == 0


def test_routes_are_read_from_the_app_not_a_list():
    """A new screen is watched the day it exists."""
    pages = ui_check.routes()
    assert "/dashboard/orders" in pages
    assert "/dashboard/station" in pages, (
        "the station view shipped 2026-09-02 and must be crawled without "
        "anyone editing a list")


def test_baselines_are_json_in_the_repo():
    """A deliberate restyle ships its baseline in the same commit - that only
    works if the baseline is a reviewable text file inside the repository."""
    assert (ui_check.REPO / "pyproject.toml").is_file()
    # The fixture redirects BASELINES for isolation, so ask the source where
    # it really points rather than the patched module.
    src = (ui_check.REPO / "src" / "fsmes" / "sim" / "ui_check.py").read_text(encoding="utf-8")
    assert 'BASELINES = REPO / "tests" / "ui" / "baselines"' in src


def test_the_product_never_imports_playwright():
    """Principle 5: a plant PC renders the screens; it never carries the
    tooling that audits them. Importing the module must not import the
    browser driver."""
    # The real guarantee: the import lives inside crawl(), so everything
    # tested in this file worked without playwright being importable at all.
    src = (ui_check.REPO / "src" / "fsmes" / "sim" / "ui_check.py").read_text(encoding="utf-8")
    head = src.split("def crawl")[0]
    assert "from playwright" not in head
    assert "import playwright" not in head
