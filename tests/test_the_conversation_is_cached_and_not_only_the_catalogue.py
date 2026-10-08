"""The conversation itself is cached, not just the words in front of it.

Until 2026-10-08 one request carried two cache breakpoints: the system words
and the end of the tool catalogue. Both are large and neither grows, so between
them they covered the part of a request that was already free to send twice.
The part that grows was paid for at full price every round: a round that has
read a tool sends that tool's answer again on the next round, and a second
question sends the whole of the first one back with it.

Measured on a lab-sized plant that morning, the manager question and one *why*
follow-up sent 25,400 uncached input tokens between them - half of what the
pair cost - for words the model had already been sent. One breakpoint on the
tail of the conversation turned almost all of it into cache reads at a tenth
of the price.

What this file pins is the shape of the request, because that is the whole of
the change: nothing is dropped, nothing is summarised, nothing is reordered,
and the conversation the model is sent is word for word the one the session
holds.
"""

import copy

from fsmes.services import agent


def _session(history):
    sess = agent.open_session("ADMIN", "testplant", {"plant.read"},
                              kind=agent.ANALYSIS)
    sess.history = history
    return sess


def _marks(message):
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content
            if isinstance(block, dict) and "cache_control" in block]


def test_the_last_thing_the_model_is_sent_is_where_the_cache_ends():
    """One breakpoint, on the tail: each round reads what the round before it
    wrote and pays full price only for what is new."""
    history = [{"role": "user", "content": "how does the chart look"},
               {"role": "assistant", "content": [{"type": "text", "text": "reading"}]},
               {"role": "user", "content": [
                   {"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]}]
    sent = agent._cached_history(_session(history))
    assert _marks(sent[-1]) == [{"type": "tool_result", "tool_use_id": "t1",
                                 "content": "{}",
                                 "cache_control": {"type": "ephemeral"}}]
    assert not any(_marks(message) for message in sent[:-1])


def test_the_first_question_of_a_conversation_is_marked_too():
    """The person's words arrive as a string and are sent as one text block
    with the mark on it. A conversation one message long is the first round of
    every conversation there is."""
    sent = agent._cached_history(_session([{"role": "user", "content": "why?"}]))
    assert sent[-1]["content"] == [
        {"type": "text", "text": "why?",
         "cache_control": {"type": "ephemeral"}}]


def test_marking_the_tail_changes_nothing_the_session_holds():
    """The session's own history is what the trace and `_repair_history` read.
    A request is a copy of it with one key added, not an edit of it."""
    history = [{"role": "user", "content": "how does the chart look"},
               {"role": "assistant", "content": [{"type": "text", "text": "reading"}]},
               {"role": "user", "content": [
                   {"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]}]
    before = copy.deepcopy(history)
    sess = _session(history)
    sent = agent._cached_history(sess)
    assert sess.history == before
    # And the words are the same words: strip the mark and the two agree.
    stripped = [{**m, "content": (
        [{k: v for k, v in b.items() if k != "cache_control"} for b in m["content"]]
        if isinstance(m["content"], list) else m["content"])} for m in sent]
    assert stripped[:-1] == before[:-1]
    assert stripped[-1] == before[-1]


def test_a_tail_this_module_did_not_build_is_left_alone():
    """An assistant message carries the SDK's own block objects. It is never
    last - `_drive` appends the model's reply and then the tool results
    answering it before it asks again - and if it ever were, the request goes
    out uncached rather than rebuilt from objects this module does not own."""

    class SdkBlock:                     # what `response.content` holds
        type = "text"

    history = [{"role": "user", "content": "why?"},
               {"role": "assistant", "content": [SdkBlock()]}]
    sent = agent._cached_history(_session(history))
    assert sent == history
    assert agent._cached_history(_session([])) == []


def test_three_breakpoints_leaves_one_spare():
    """`anthropic` takes four. The system words and the catalogue hold two, the
    conversation holds the third, and a request that asked for a fifth would be
    refused by the API rather than silently uncached - so the count is a fact
    worth a test."""
    sess = agent.open_session("ADMIN", "testplant", {"plant.read"},
                              kind=agent.ANALYSIS)
    sess.history = [{"role": "user", "content": "why?"}]
    tools = agent._anthropic_tools(sess)
    in_tools = sum(1 for tool in tools if "cache_control" in tool)
    in_history = sum(len(_marks(message)) for message in agent._cached_history(sess))
    assert in_tools == 1
    assert in_history == 1
    # The system block is the third, written in `_call_model` itself.
    assert in_tools + in_history + 1 <= 4
