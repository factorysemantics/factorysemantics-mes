"""The questions an answer opened are asked for in words, and they are questions.

Scott, 2026-10-08, after using the chip #150 put on the SPC tab: *"could the
agent extract potential new paths of research and make them buttons? … its flat
now and its easier just to get that flat of an answer in the 'Assistant'."* So
the analysis agent is asked to end an answer with the two or three questions it
opened, and the AI tab turns each into a button.

This file pins the three decisions behind that, each of which is a line
somebody could quietly take out:

1. the questions are asked for as a **fenced block the model writes**, not as a
   tool it calls - a tool call sends the whole conversation through the model
   one more time for three sentences it already had in hand, and that round
   trip is most of what a turn costs;
2. every line is a **question** and never an instruction, because the account
   behind this agent holds nothing that writes and a button saying "raise a
   finding" would promise what it cannot do;
3. the **floor assistant is not asked for any of it**. That one can propose,
   and a button that asked it to act would be a proposal nobody signed.

The screen's half - the buttons, and what a press does - is in
`test_the_chart_comes_into_the_chat_and_stays_clickable`.
"""

from fsmes.services import agent

ANALYSIS = agent.system_for(agent.ANALYSIS)
FLOOR = agent.system_for(agent.FLOOR)


def test_the_analysis_words_ask_for_two_or_three_questions_in_a_fenced_block():
    """Named exactly as `ai.js` reads them: a fence whose opening line is
    ```next, one question per line, two or three of them."""
    assert "```next" in ANALYSIS
    assert "two or three questions" in ANALYSIS
    assert "one per line" in ANALYSIS


def test_the_block_is_words_the_model_writes_and_not_a_tool_it_calls():
    """The cost argument, as a fact about the catalogue: there is no tool for
    this, so asking for it costs its own words and not another round trip of
    the whole conversation."""
    kind = agent.kind_named(agent.ANALYSIS)
    tools = agent.catalogue({"plant.read", "audit.read"}, for_kind=kind)
    tools += agent.chart_tools()
    names = [tool["name"] for tool in tools]
    assert agent.DRAW_TOOL in names, "the draw tool is still there"
    assert len(names) > 1, "the analysis catalogue came back empty"
    assert not [name for name in names if "next" in name], (
        f"something in the analysis catalogue offers to hand the next questions "
        f"back as a tool result: {names}")


def test_every_line_is_a_question_and_never_something_to_do():
    """A button is a question put back to an agent that changes nothing. The
    words say so, and name the verbs a reader of a button might expect."""
    assert "never an instruction" in ANALYSIS
    for verb in ("booked", "adjusted", "approved", "raised", "fixed"):
        assert verb in agent.ANALYSIS_NEXT, (
            f"the words do not rule out a button asking for something to be {verb}")


def test_an_answer_that_opened_nothing_is_told_to_end_without_a_block():
    """Three invented questions are worse than none: they cost money to ask and
    the reader cannot tell them from the ones the answer really opened."""
    assert "only when the answer really opened something" in agent.ANALYSIS_NEXT
    assert "no block at all is a better answer" in agent.ANALYSIS_NEXT


def test_the_floor_assistant_is_asked_for_none_of_it():
    """It holds tools that change this plant. Buttons it wrote would be
    proposals in question marks."""
    assert "```next" not in FLOOR
    assert agent.ANALYSIS_NEXT not in FLOOR
