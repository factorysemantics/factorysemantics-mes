"""`.github/scripts/release_notes.sh` — the body of the GitHub Release.

A release body may be 125,000 characters; over that the API answers 422 with
nothing that explains itself. Until 2026-10-12 the body was the version's
section of CHANGELOG.md, handed straight to the release API, and 0.3.0's
section is 256,271 characters. The job that publishes it, `github-release`,
is tag-only and runs *after* PyPI and ghcr.io, so the failure would have
landed at the one point in that workflow where nothing can be retried from
the top — with the version already public on two registries and no release.

A `workflow_dispatch` rehearsal found it before the tag existed. These tests
are what keeps it found: the body is the release's own page under
`docs/releases/`, the length is asserted rather than discovered, and the
assertion runs in `build`, which a rehearsal does run.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "release_notes.sh"
LIMIT = 125_000

# The script under test is a shell script, and Windows cannot execute one: the
# whole file read `WinError 193, %1 is not a valid Win32 application` on the
# `windows-latest` cells of CI. Skipped there rather than rewritten, because
# the thing being tested only ever runs on one machine - the Ubuntu runner that
# `release.yml` composes the body on - and a Python rewrite of it would be a
# second copy of the logic, tested, beside the copy that ships.
pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="release_notes.sh is a shell script; the release workflow runs it on Ubuntu",
)


def notes(version: str, cwd: Path = REPO) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), version], cwd=cwd, capture_output=True, text=True, check=False
    )


def this_version() -> str:
    text = (REPO / "src" / "fsmes" / "__init__.py").read_text()
    match = re.search(r'^__version__ = "(.+)"$', text, re.M)
    assert match, "no __version__ in src/fsmes/__init__.py"
    return match.group(1)


def test_the_body_for_the_version_this_tree_would_release_is_short_enough():
    version = this_version()
    assert (REPO / "docs" / "releases" / f"{version}.md").exists(), (
        f"this tree is version {version} and has no docs/releases/{version}.md, "
        "so the release body would fall back to the changelog section"
    )
    result = notes(version)
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) <= LIMIT, (
        f"the release body for {version} is {len(result.stdout)} characters; "
        f"GitHub allows {LIMIT}"
    )


def test_the_body_is_the_release_page_and_not_the_changelog_section():
    version = this_version()
    page = (REPO / "docs" / "releases" / f"{version}.md").read_text()
    body = notes(version).stdout
    # The page's first heading, and a sentence only the page carries.
    assert body.startswith(f"# {version}\n")
    assert "what it does not, and what is simulated" in body
    assert len(body) < len(page) + 2_000, "the body is much longer than the page it is"


def test_the_note_about_a_page_written_before_the_tag_is_dropped_and_dated(
    tmp_path: Path,
):
    """A page is written before the tag, so it carries a note to the maintainer
    and no date. Neither belongs in what a stranger reads on the release.

    Asked of a page this test writes rather than of the tree's own. A page
    whose release has been cut has had the box taken out of it - that is the
    last step of tagging, and two of them sat in `docs/releases/` with it
    still in weeks afterwards - so the tree is sometimes the before state and
    sometimes the after one, and only one of those proves the stripping.
    """
    (tmp_path / "docs" / "releases").mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n")
    (tmp_path / "docs" / "releases" / "9.9.9.md").write_text(
        "# 9.9.9\n\n*Reference.*\n\n"
        "> **Not yet tagged.** This page is written before the tag so that the\n"
        "> release body is reviewed rather than typed on release night.\n\n"
        "**Released:** *on tagging.* **Previous:** 9.9.8.\n\n---\n\nWhat it is.\n")
    body = notes("9.9.9", cwd=tmp_path).stdout

    assert "Not yet tagged" not in body
    assert "*on tagging.*" not in body
    assert re.search(r"\*\*Released:\*\* \d{4}-\d{2}-\d{2}\.", body), body[:400]
    assert "What it is." in body, "the page itself survived the stripping"


def test_the_body_for_this_tree_names_a_date_and_no_note_to_the_maintainer():
    """Whichever state the tree is in: before the tag the box is stripped and
    the date is filled in, after it the page carries its own date already."""
    body = notes(this_version()).stdout

    assert "Not yet tagged" not in body
    assert "*on tagging.*" not in body
    assert re.search(r"\*\*Released:\*\* \d{4}-\d{2}-\d{2}", body), body[:400]


def test_links_between_documentation_pages_become_links_a_reader_can_follow():
    body = notes(this_version()).stdout
    assert "](../" not in body, "a relative docs link is dead in a release body"
    assert "https://docs.factorysemantics.com/operate/upgrade/#" in body


def test_the_whole_changelog_is_linked_at_the_tag_that_carries_it():
    version = this_version()
    body = notes(version).stdout
    assert f"/blob/v{version}/CHANGELOG.md" in body


def test_a_body_over_the_limit_fails_the_job_instead_of_being_published(tmp_path: Path):
    (tmp_path / "docs" / "releases").mkdir(parents=True)
    (tmp_path / "docs" / "releases" / "9.9.9.md").write_text("# 9.9.9\n\n" + "x" * LIMIT)
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n")
    result = notes("9.9.9", cwd=tmp_path)
    assert result.returncode != 0
    assert "GitHub allows" in result.stderr
    assert "x" * 100 not in result.stdout


def test_a_version_with_no_page_falls_back_to_its_changelog_section(tmp_path: Path):
    (tmp_path / "docs" / "releases").mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [9.9.9] — 2026-01-01\n\n### Added\n\n- the one thing\n\n"
        "## [9.9.8] — 2025-01-01\n\n- an older thing\n"
    )
    result = notes("9.9.9", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "- the one thing" in result.stdout
    assert "an older thing" not in result.stdout


def test_a_version_with_neither_a_page_nor_a_section_says_so_rather_than_nothing(
    tmp_path: Path,
):
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n")
    result = notes("9.9.9", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "See CHANGELOG.md." in result.stdout


@pytest.mark.parametrize("version", ["0.2.0"])
def test_an_earlier_release_whose_page_exists_still_composes(version: str):
    result = notes(version)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"# {version}\n")


def test_a_release_already_cut_carries_no_note_about_a_page_written_before_it():
    """The box is a note to the maintainer about a page written ahead of the
    tag, and taking it out is the last step of tagging. 0.3.0's page kept its
    for three weeks and 0.3.1's for a day - both on PyPI, both telling a
    reader the release was not yet tagged. This is the only version whose page
    may still say it."""
    unreleased = this_version()
    stale = sorted(page.stem for page in (REPO / "docs" / "releases").glob("*.md")
                   if page.stem != unreleased
                   and "Not yet tagged" in page.read_text(encoding="utf-8"))
    assert not stale, (
        f"{', '.join(stale)} are released and their pages still say they are not "
        f"tagged; this tree is {unreleased}")
