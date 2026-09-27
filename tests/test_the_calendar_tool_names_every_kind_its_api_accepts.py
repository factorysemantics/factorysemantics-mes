"""The calendar tool's docstring is the only place a model learns the kinds.

On 2026-09-26 a live faithfulness run asked the assistant to mark Christmas Day
a holiday and it proposed `add_calendar_exception(kind="shutdown")`. The API
accepts no such kind - it accepts two - and neither the docstring nor the suite
case said which. The model was not guessing badly; it was guessing, because the
tool description it was handed listed nothing to guess from.

A tool description is the whole of what a model knows about an argument. So the
docstring names every kind, and this file fails if a third is ever added to the
enum and not to the sentence - keyed on the enum itself, so nobody has to
remember this file exists.
"""

import pytest

from fsmes.domain import ExceptionKind
from fsmes.services import agent


def _flat(text: str) -> str:
    """The description as one line, without its backticks: how it is wrapped in
    the source is not something a test should have an opinion about."""
    return " ".join(text.replace("`", "").split()).lower()


def _description() -> str:
    for tool in agent.registry_tools():
        if tool.name == "add_calendar_exception":
            return tool.description or ""
    raise AssertionError("the plant serves no add_calendar_exception tool")


@pytest.mark.parametrize("kind", [k.value for k in ExceptionKind])
def test_the_docstring_names_every_kind_the_api_accepts(kind):
    assert kind in _description(), (
        f"{kind!r} is a kind this API takes and the tool description does not "
        f"mention it, so a model has no way to send it")


def test_the_docstring_says_what_each_kind_is_for():
    """Naming the word is not enough: `non_working` has to say that the plant is
    dark and `working` that it is an overtime day, or a model picks by the
    sound of it - which is how `shutdown` happened."""
    said = _flat(_description())
    assert "dark" in said and "overtime" in said
    assert "holiday" in said, "the word a person actually types has to appear somewhere"


def test_the_words_a_model_might_reach_for_are_ruled_out_rather_than_left_open():
    """`holiday` and `shutdown` are what the old docstring's prose suggested and
    what the live model sent. The description says they are not kinds."""
    said = _flat(_description())
    assert "no holiday kind" in said and "no shutdown kind" in said


def test_the_suite_asks_for_a_kind_this_api_would_take():
    """A case that expects an argument the plant refuses is scoring the model
    against a call that could never have worked."""
    from fsmes.services import assist_eval

    accepted = {k.value for k in ExceptionKind}
    for case in assist_eval.load():
        if case.tool == "add_calendar_exception" and "kind" in case.args:
            assert case.args["kind"] in accepted, (
                f"{case.id} expects kind={case.args['kind']!r}; this API takes "
                f"{sorted(accepted)}")
