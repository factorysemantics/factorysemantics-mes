"""Browser tests share one way of serving a plant.

Thirty-five test files each carried their own sixty lines that seed a demo
plant into a file database, start uvicorn on a free loopback port, wait for
`/health` and sign in as ADMIN - the same lines, copied, drifting a little
each time. `tests/conftest.py` now holds one copy: the `live_server` fixture
(the demo plant, served once for the run) and `serve_plant` (a plant with
rows of the test's own). A new browser test uses one of them.

Read from the source, like `test_core_purity.py`: a test file that calls
`uvicorn.Server` or `uvicorn.run` is starting its own server. Importing
uvicorn is not: `test_auth.py` reads uvicorn's defaults to pin what the
operate page promises about proxies, and starts nothing. Thirty files did
start their own on 2026-11-14; all but one have moved onto the fixture.
"""

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: The one file that starts its own server and stays that way, and why.
#: Never grows: a new reason is a new conversation, not a new line.
STAYS: dict[str, str] = {
    "test_a_plant_books_while_its_dashboard_is_read.py":
        "the lab's six-hour reproduction runs it as `python -m`, outside pytest, "
        "where no fixture exists",
}

MESSAGE = "use the `live_server` fixture in `tests/conftest.py`"


def _starts_a_server(source: str, filename: str = "<test>") -> bool:
    """True when the source calls `uvicorn.Server(...)` or `uvicorn.run(...)`,
    by attribute or by a name imported from uvicorn."""
    tree = ast.parse(source, filename=filename)
    starters = {"Server", "run"}
    imported = {alias.asname or alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "uvicorn"
                for alias in node.names if alias.name in starters}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        if (isinstance(called, ast.Attribute) and called.attr in starters
                and isinstance(called.value, ast.Name) and called.value.id == "uvicorn"):
            return True
        if isinstance(called, ast.Name) and called.id in imported:
            return True
    return False


def _starts_its_own_server(path: Path) -> bool:
    return _starts_a_server(path.read_text(encoding="utf-8"), str(path))


def _test_files() -> list[Path]:
    return sorted(path for path in TESTS.rglob("*.py") if path != TESTS / "conftest.py")


def test_no_new_test_file_starts_its_own_server():
    offenders = [path.relative_to(TESTS).as_posix() for path in _test_files()
                 if _starts_its_own_server(path)]
    new = sorted(set(offenders) - set(STAYS))
    assert not new, f"{new} start their own server - {MESSAGE}"


def test_the_file_that_stays_still_starts_its_own_server():
    """A file that no longer starts a server, or no longer exists, comes out
    of `STAYS`, so the list never holds a reason nothing needs."""
    gone = sorted(name for name in STAYS
                  if not (TESTS / name).exists() or not _starts_its_own_server(TESTS / name))
    assert not gone, f"no longer start their own server; take them out of STAYS: {gone}"


def test_reading_uvicorns_defaults_is_not_starting_a_server():
    """`test_auth.py` builds a `uvicorn.Config` to read its proxy defaults and
    never serves it; the fence counts servers started, not imports."""
    assert not _starts_its_own_server(TESTS / "test_auth.py")
    assert not _starts_a_server("import uvicorn\nuvicorn.Config('app:make', factory=True)\n")


def test_every_way_of_starting_a_server_is_seen():
    assert _starts_a_server("import uvicorn\nuvicorn.Server(uvicorn.Config(app)).run()\n")
    assert _starts_a_server("import uvicorn\nuvicorn.run(app, port=0)\n")
    assert _starts_a_server("from uvicorn import Server, Config\nServer(Config(app))\n")
    assert _starts_a_server("from uvicorn import run as serve\nserve(app)\n")


def test_the_shared_server_is_the_one_in_conftest():
    """The fence points at a fixture; the fixture has to be there."""
    assert _starts_its_own_server(TESTS / "conftest.py")
    source = (TESTS / "conftest.py").read_text(encoding="utf-8")
    assert "def live_server(" in source
