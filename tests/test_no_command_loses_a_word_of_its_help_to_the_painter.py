"""`fsmes <anything> --help` is painted by rich, and rich eats square brackets.

A help string that says `[storage]` prints as nothing at all: rich reads the
brackets as a style tag, finds no style of that name, and drops the word. It
is the only way this product's help can lie - every other sentence in it is
printed as written - and it has happened six times, quietly, because the
person who wrote the sentence read the source and not the screen.

So this walks every command and every option the CLI has, takes the help text
**as declared** and runs rich's own markup over it, and refuses any text that
comes out shorter than it went in. It reads the declared string and never the
painted output: pinning painted help is its own trap, and cost #163 a CI round
on 2026-10-12 when an assertion met a terminal of a different width.

The fix, when this fails, is to write the table's name in words - "the storage
table's `database_url`" - rather than to escape the brackets. A person reading
`\\[storage]` in the source is being asked to know about the painter, and the
next person writing a help string will not.
"""

import pytest
import typer.main

from fsmes.cli import app

markup = pytest.importorskip("rich.markup", reason="rich paints typer's help")


def _help_texts():
    """Every help string the CLI declares: one per command, one per option.

    Yields (what, where, text) so a failure names the command and the option
    rather than only the sentence.
    """
    def walk(command, path):
        yield path, "its help", command.help or ""
        yield path, "its short help", getattr(command, "short_help", "") or ""
        for parameter in command.params:
            yield path, f"--{parameter.name}", getattr(parameter, "help", "") or ""
        for name, sub in (getattr(command, "commands", {}) or {}).items():
            yield from walk(sub, f"{path} {name}")

    yield from walk(typer.main.get_command(app), "fsmes")


def test_no_command_loses_a_word_of_its_help_to_the_painter():
    lost = []
    counted = 0
    for path, where, text in _help_texts():
        if not text:
            continue
        counted += 1
        plain = markup.render(text).plain
        if plain != text:
            missing = [
                (was, now)
                for was, now in zip(text.splitlines(), plain.splitlines())
                if was != now
            ]
            lost.append(f"{path} ({where}):\n" + "\n".join(
                f"      written: {was}\n      printed: {now}" for was, now in missing))

    assert not lost, (
        f"{len(lost)} of {counted} help texts lose a word when rich paints them "
        "- write the table's name in words instead of in brackets:\n"
        + "\n".join(lost))


def test_the_walk_actually_found_the_commands():
    """A walk that silently descended into nothing would pass the test above
    for the wrong reason - which is how the first scan of this fault reported
    zero: `isinstance(cmd, click.Group)` is false for typer's own group class,
    so it never looked at a subcommand."""
    paths = {path for path, _, _ in _help_texts()}
    assert len(paths) > 50, f"the walk found only {len(paths)} commands: {sorted(paths)}"
    for expected in ("fsmes plant", "fsmes pack status", "fsmes fleet start",
                     "fsmes init-db", "fsmes jev ask"):
        assert expected in paths, f"the walk never reached `{expected}`"
