"""A test that drives Chromium is in the tier CI runs Chromium for.

The 2026-09-25 lesson, made mechanical. Every Playwright test in this suite
was marked `slow`, the default selection and every CI job deselect `slow`, so
nothing ran them — and `test_ui_nav.py`'s section count had been red on `main`
since PR #104 merged without a single check going red. Opt-in tests rot.

CI now has a `browser` job that runs `pytest -m browser`. The failure mode
that survives is quieter than the one that was fixed: somebody adds a new
Playwright file, marks it `slow` because that is what the neighbouring files
say, and it is never selected by the job — back to a tier with a hole in it
and no red anywhere.

So this reads the test files themselves. It costs milliseconds, needs no
browser, and runs in the *default* selection, which is the point: the check
that the browser tier is whole must not itself live inside the browser tier.
"""

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: A file reaches the browser by importing Playwright - either outright or
#: through `pytest.importorskip`, which is how every browser test here does it
#: so the suite still collects on a machine without the [dev] extra. Matching
#: the import rather than the word: `test_ui_check.py` asserts in prose that
#: the *product* never imports Playwright, and drives nothing itself.
DRIVES_A_BROWSER = re.compile(
    r"""(?m)^\s*(?:from|import)\s+playwright\b|^\s*["']playwright[.\w]*["']""")

MARKER = "pytest.mark.browser"


def _driving_files() -> list[Path]:
    """Every test file that reaches Playwright, this one excepted: it holds
    the pattern above and drives nothing."""
    here = Path(__file__).resolve()
    return sorted(path for path in TESTS.glob("test_*.py")
                  if path.resolve() != here
                  and DRIVES_A_BROWSER.search(path.read_text(encoding="utf-8")))


def test_every_test_file_that_drives_chromium_carries_the_browser_marker():
    """The marker is what CI's `browser` job selects on. A file without it is
    a browser test nothing runs — which is exactly how one stayed red on
    `main` for a day nobody noticed."""
    driving = _driving_files()
    # Stating the total, so this going quiet is visible: a rename that made
    # the glob match nothing would otherwise pass in silence.
    assert driving, (
        f"no test file under {TESTS} imports Playwright; if the browser tests "
        "moved, this check has to move with them")

    missing = [path.name for path in driving
               if MARKER not in path.read_text(encoding="utf-8")]
    assert not missing, (
        f"{len(missing)} of {len(driving)} Playwright test files are outside "
        f"the tier CI runs — add `{MARKER}` to: {', '.join(missing)}")


def test_the_browser_marker_is_declared_so_the_suite_does_not_warn_on_it():
    """An undeclared marker is a warning, not an error, and `--strict-markers`
    is not on here: a typo in the marker name would silently select nothing
    and the job would report a green zero."""
    declared = (TESTS.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert '"browser: ' in declared, (
        "the `browser` marker is not declared in pyproject.toml's "
        "[tool.pytest.ini_options] markers")
