"""Every `fsmes` command the assistant folder tells a person to run is a real command.

The folder in `skills/assistant/` is read by somebody else's agent, which then
asks a person at a plant to type what it says. Nothing in the folder is
executed here, so a renamed sub-command, a dropped option or a typo in a page
is invisible until a stranger types it and gets `No such command`. That person
has no way to tell a stale page from a broken install, and the agent reading
the page will cheerfully insist.

So: pull every command out of the markdown and resolve it against the CLI
itself — the sub-command path through the typer app, and then every `--option`
against that command's own parameters. The pages and the CLI cannot drift apart
without this going red.

Commands are read from inline code spans and from fenced blocks that are shell
(` ``bash `, ` ``powershell `, ` ``console `, or no language at all). A fenced
` ``text ` block is output, not input: the pages quote what the computer
printed, and that text contains command lines with prose in brackets after
them.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
from typer.main import get_command

from fsmes import skills
from fsmes.cli import app

REPO = Path(__file__).resolve().parents[1]
FOLDER = REPO / "skills" / "assistant"

#: Fence info strings whose contents are things a person types. The empty one
#: is included on purpose: a fence with no language on it is a command block,
#: and a page that quotes what the computer printed must label it `text`.
SHELL_FENCES = {"", "bash", "sh", "shell", "powershell", "ps1", "console", "cmd"}

#: Ways a page may spell "run the fsmes CLI" before the sub-command starts.
PREFIXES = (
    ["fsmes"],
    ["py", "-m", "fsmes"],
    ["python", "-m", "fsmes"],
    ["python3", "-m", "fsmes"],
    [".venv/bin/fsmes"],
    [r".venv\Scripts\fsmes"],
)


def _candidate_lines(text: str) -> list[str]:
    """Lines from this markdown that are meant to be typed."""
    lines: list[str] = []
    in_shell_fence = False
    fenced = False
    for raw in text.splitlines():
        fence = re.match(r"^\s*```+\s*([A-Za-z0-9_+-]*)\s*$", raw)
        if fence:
            if fenced:
                fenced, in_shell_fence = False, False
            else:
                fenced = True
                in_shell_fence = fence.group(1).lower() in SHELL_FENCES
            continue
        if fenced:
            if in_shell_fence:
                lines.append(raw)
            continue
        # Outside a fence, only inline code spans are commands.
        lines.extend(re.findall(r"`([^`\n]+)`", raw))
    return lines


def _joined(lines: list[str]) -> list[str]:
    """Shell line continuations folded into one line."""
    out: list[str] = []
    pending = ""
    for line in lines:
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            pending += stripped[:-1].strip() + " "
            continue
        out.append((pending + stripped.strip()).strip())
        pending = ""
    if pending:
        out.append(pending.strip())
    return out


def _tokens_after_fsmes(line: str) -> list[str] | None:
    """The sub-command and options of an `fsmes` invocation, or None."""
    line = re.sub(r"\s+#.*$", "", line.strip())
    line = re.sub(r"^\s*[$>]\s+", "", line)
    if "fsmes" not in line:
        return None
    try:
        tokens = shlex.split(line, posix=True)
    except ValueError:
        return None                                  # an unbalanced quote: prose, not a command
    for prefix in PREFIXES:
        if tokens[:len(prefix)] == prefix:
            return tokens[len(prefix):]
    return None


def commands_in(path: Path) -> list[tuple[str, list[str]]]:
    """(the line as written, its tokens) for every fsmes command in this page."""
    found = []
    for line in _joined(_candidate_lines(path.read_text(encoding="utf-8"))):
        tokens = _tokens_after_fsmes(line)
        if tokens is not None:
            found.append((line, tokens))
    return found


# Typer vendors its own copy of click's core classes, so `isinstance(x,
# click.Group)` is False for a Typer app's command tree. These read the shape
# instead of the type: a group is the thing with a `commands` mapping, an
# option is the parameter whose spellings all begin with a dash.


def _subcommands(command) -> dict | None:
    found = getattr(command, "commands", None)
    return found if isinstance(found, dict) else None


def _options_of(command) -> tuple[set[str], set[str]]:
    """(every option spelling, the ones that take a value)."""
    spellings: set[str] = {"--help"}
    takes_a_value: set[str] = set()
    for param in command.params:
        opts = [*getattr(param, "opts", []), *getattr(param, "secondary_opts", [])]
        if not opts or not all(o.startswith("-") for o in opts):
            continue                                 # a positional argument
        for opt in opts:
            spellings.add(opt)
            if not getattr(param, "is_flag", False) and not getattr(param, "count", False):
                takes_a_value.add(opt)
    return spellings, takes_a_value


def _arguments_allowed(command) -> int:
    allowed = 0
    for param in command.params:
        opts = [*getattr(param, "opts", [])]
        if opts and all(o.startswith("-") for o in opts):
            continue                                 # an option
        nargs = getattr(param, "nargs", 1)
        if nargs == -1:
            return 99
        allowed += nargs
    return allowed


def why_this_is_not_a_command(tokens: list[str]) -> str | None:
    """Plain English for what is wrong with this command line, or None."""
    here = get_command(app)
    path = ["fsmes"]
    rest = list(tokens)
    while _subcommands(here) is not None and rest and not rest[0].startswith("-"):
        children = _subcommands(here)
        name = rest[0]
        if name not in children:
            near = ", ".join(sorted(children)[:12])
            return (f"`{' '.join(path)}` has no sub-command {name!r}. "
                    f"It has: {near}…")
        here = children[name]
        path.append(rest.pop(0))
    is_a_group = _subcommands(here) is not None
    if is_a_group and not rest:
        return None                                  # a group named on its own, for its help
    spellings, takes_a_value = _options_of(here)
    positional = 0
    while rest:
        token = rest.pop(0)
        if token.startswith("-"):
            opt = token.split("=", 1)[0]
            if opt not in spellings:
                return (f"`{' '.join(path)}` has no option {opt!r}. "
                        f"It has: {', '.join(sorted(o for o in spellings if o.startswith('--')))}")
            if opt in takes_a_value and "=" not in token and rest:
                rest.pop(0)
            continue
        positional += 1
    if is_a_group and positional:
        return f"`{' '.join(path)}` is a group of commands; it takes a sub-command, not an argument."
    allowed = _arguments_allowed(here)
    if positional > allowed:
        return (f"`{' '.join(path)}` takes {allowed} argument(s); this line gives it "
                f"{positional}.")
    return None


def pages() -> list[Path]:
    return [FOLDER / rel for rel in skills.SKILLS["assistant"] if rel.endswith(".md")]


@pytest.fixture(autouse=True)
def only_from_a_checkout():
    if not FOLDER.is_dir():
        pytest.skip("installed package: skills/ only exists in a checkout")


@pytest.mark.parametrize("page", pages(), ids=lambda p: p.name)
def test_every_fsmes_command_on_this_page_resolves_against_the_cli(page: Path) -> None:
    broken = []
    for line, tokens in commands_in(page):
        complaint = why_this_is_not_a_command(tokens)
        if complaint:
            broken.append(f"{page.name}: `{line}` — {complaint}")
    assert not broken, "\n".join(broken)


def test_the_pages_really_do_carry_commands_so_the_check_is_not_reading_nothing() -> None:
    """A scanner that finds nothing passes everything. This is the tripwire."""
    counted = {page.name: len(commands_in(page)) for page in pages()}
    total = sum(counted.values())
    assert total >= 30, f"only {total} command(s) found across {len(counted)} page(s): {counted}"
    for name in ("1-install.md", "2-your-plant.md", "3-shadow-mode.md"):
        assert counted[name] >= 4, f"{name} has {counted[name]} command(s); it is a stage page"


def test_a_misspelt_command_is_caught(tmp_path: Path) -> None:
    """The check has to be able to go red, or it is decoration on a folder."""
    assert why_this_is_not_a_command(["demo", "bottling", "status"]) is None
    assert "no sub-command" in (why_this_is_not_a_command(["demmo", "bottling"]) or "")
    assert "no sub-command" in (why_this_is_not_a_command(["skills", "unpackk", "assistant"]) or "")
    assert "no option" in (why_this_is_not_a_command(["demo", "bottling", "--forse"]) or "")
    assert "no option" in (
        why_this_is_not_a_command(["shadow", "scorecard", "--incumbant", "t.csv"]) or "")

    # One too many arguments is caught too.
    assert "takes 2 argument(s)" in (
        why_this_is_not_a_command(["demo", "bottling", "status", "please"]) or "")

    # And the same check, read out of a page the way CI reads one.
    page = tmp_path / "broken.md"
    page.write_text("Run this:\n\n```bash\nfsmes demmo bottling\n```\n", encoding="utf-8")
    found = commands_in(page)
    assert found, "the extractor read nothing out of a shell fence"
    assert "no sub-command 'demmo'" in (why_this_is_not_a_command(found[0][1]) or "")


def test_what_this_check_cannot_catch_is_written_down() -> None:
    """An argument's *value* is not checkable here, and pretending otherwise
    would be worse than saying so.

    `fsmes demo bottling stattus` is a well-formed command line: `stattus`
    lands in the `action` argument and is refused at run time, not by the
    signature. So the folder's pages must keep action words — start, stop,
    status — inside a sentence that names them, and this test exists to stop
    somebody later believing the check is stronger than it is.
    """
    assert why_this_is_not_a_command(["demo", "bottling", "stattus"]) is None


def test_output_quoted_on_a_page_is_not_read_as_a_command(tmp_path: Path) -> None:
    """Pages quote what the computer printed. That text has prose in it."""
    page = tmp_path / "out.md"
    page.write_text(
        "What it says:\n\n```text\n    fsmes demo bottling stop      (stops what is left)\n```\n",
        encoding="utf-8")
    assert commands_in(page) == []


def test_the_page_one_rules_are_the_first_thing_an_agent_reads() -> None:
    """Rule 1 is that the agent runs nothing. It has to be above the stages."""
    text = (FOLDER / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front = text.split("---", 2)[1]
    assert "name: assistant" in front
    assert "description:" in front
    body = text.split("---", 2)[2]
    rules = body.index("You run nothing")
    assert rules < body.index("1-install.md"), "the stages are listed before the rules"
    for must_say in ("You never ask for data", "Say what is not built", "One stage at a time"):
        assert must_say in body, f"SKILL.md does not say: {must_say}"
    readme = (FOLDER / "README.md").read_text(encoding="utf-8")
    assert "runs nothing" in readme or "run nothing" in readme
