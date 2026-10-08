"""A turn says everything the model wrote, in the order it wrote it.

Scott, 2026-10-08, of the analysis agent on a real plant: *"its flat now."* One
cause was not in the words at all - it was in the loop. `_drive` runs a round
per model call, and each round computed `say` from that round's text blocks; a
round that ended in a tool call dropped its `say` and looped, and only the last
round's words were returned. A model that answered and then checked one more
record had its answer thrown away.

The manager question is exactly that shape. The chip on the SPC tab asks for
*two sentences for my boss and the one graph for a slide. And why is this
sample where it is?* - so the model writes the two sentences, draws the chart,
reads the dossier for the *why*, and writes again. Before this, the two
sentences never reached the screen.

So a reply now carries, beside `say`:

- `parts`, the order it happened in - `{"text": ...}` for a round's words and
  `{"chart": "c1"}` naming one of `charts` by the id its spec already carries.
  The envelope is NOT copied into the part: an SPC envelope is a few hundred
  readings and a reply holding it twice would put the plant's rows on the wire
  twice.
- `say`, which is now every round's words joined in order with a blank line
  between them, so a caller that never reads `parts` - the eval suite, the
  trace, the panel - gets the whole answer rather than its last paragraph.

The refusal, "spent" and "error" paths are untouched, and each has a test here
saying so: a turn that was stopped is not a turn with something to say.
"""

from types import SimpleNamespace

import pytest

from fsmes.services import agent

A_CONTROL_CHART = {
    "material": "FG-COLA", "characteristic": "fill height", "unit": "mm",
    "kind": "xbar_r", "sample_size": 5, "n": 2, "readings": 10,
    "lower_spec": 59.0, "upper_spec": 63.0,
    "points": [{"value": 61.2, "ts": "2026-10-07T21:18:00", "sample": 11,
                "check": 101, "range": 0.4},
               {"value": 62.6, "ts": "2026-10-07T21:38:00", "sample": 12,
                "check": 106, "range": 0.6}],
    "control": {"centre": 61.555, "lower": 60.3, "upper": 62.5, "sigma": 0.38},
    "signals": [{"rule": 1, "index": 1, "label": "beyond the upper control limit"}],
    "verdict": "out of control, not out of spec",
    "coverage": "absent",
}

#: The two sentences, written before the agent went looking for the why.
FOR_HIS_BOSS = ("Sample 12 is out of control: its mean of 62.6 mm is above the "
                "upper control limit of 62.5. The sample before it sits on the "
                "centre line.")
#: And the why, written after the dossier came back.
THE_WHY = ("MIX01 came out of a changeover 40 seconds before that sample. "
           "Nothing in this product links the two, so that is what the records "
           "say and no more.")


def _usage():
    return SimpleNamespace(input_tokens=1000, output_tokens=50,
                           cache_read_input_tokens=800,
                           cache_creation_input_tokens=0)


def _turn(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=_usage())


def _text(words):
    return SimpleNamespace(type="text", text=words)


def _use(id_, tool, **args):
    return SimpleNamespace(type="tool_use", id=id_, name=tool, input=args)


@pytest.fixture()
def exploring(monkeypatch, tmp_path):
    """An analysis conversation with the model scripted and the reads stood in
    for. What is under test is the loop, not the arithmetic."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MES_ANALYSIS_BRAIN", "claude")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    monkeypatch.setattr(agent, "USAGE_FILE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(agent, "TURN_FILE", tmp_path / "turns.jsonl")
    script: list = []
    served = {"spc_chart": A_CONTROL_CHART,
              "spc_sample": {"sample": 12, "machine": {"changeover_ended": 40}}}

    monkeypatch.setattr(agent, "_call_model", lambda sess: script.pop(0))
    monkeypatch.setattr(
        agent, "execute",
        lambda name, args, *, plant, on_behalf_of=None, dry_run=None,
        client_ref=None: served.get(name, {"plant": plant, "total": 1, "showing": 1}))
    return script, served


def _open(**kwargs):
    return agent.open_session("SCOTT", "testplant", {"plant.read", "audit.read"},
                              kind=agent.ANALYSIS, **kwargs)


# ------------------------------------------------------ the words of every round

def test_the_words_of_a_round_that_ended_in_a_tool_call_are_in_the_reply(exploring):
    """The failure itself. Two rounds of words with a read between them, and
    both halves come back - the first one is not the one that gets dropped."""
    script, _served = exploring
    script += [_turn(_text(FOR_HIS_BOSS), _use("tu_1", "spc_sample", sample_id=12),
                     stop="tool_use"),
               _turn(_text(THE_WHY))]
    out = agent.message(_open(), "how does the fill height chart look, and why?")

    assert out["kind"] == "reply"
    assert FOR_HIS_BOSS in out["say"], "the round that read one more record lost its words"
    assert THE_WHY in out["say"]
    assert out["say"].index(FOR_HIS_BOSS) < out["say"].index(THE_WHY)
    # Joined with a blank line, because they were two paragraphs when written
    # and a reader should not be handed them as one run-on sentence.
    assert out["say"] == f"{FOR_HIS_BOSS}\n\n{THE_WHY}"


def test_the_reply_says_what_order_the_words_and_the_pictures_came_in(exploring):
    """`parts`, which is the order itself - and the only account of it there
    is: `say` is the words and `charts` is the pictures, and neither of them
    can say that the chart came between the two paragraphs."""
    script, served = exploring
    script += [_turn(_use("tu_1", "spc_chart", material="FG-COLA",
                          characteristic="fill height"), stop="tool_use"),
               _turn(_text(FOR_HIS_BOSS),
                     _use("tu_2", agent.DRAW_TOOL,
                          **{"from": "tu_1", "shape": "spc",
                             "title": "FG-COLA fill height"}),
                     stop="tool_use"),
               _turn(_use("tu_3", "spc_sample", sample_id=12), stop="tool_use"),
               _turn(_text(THE_WHY))]
    out = agent.message(_open(), "two sentences for my boss, and why is sample 12 high?")

    assert out["parts"] == [{"text": FOR_HIS_BOSS}, {"chart": "c1"},
                            {"text": THE_WHY}]
    # The part names the chart; the chart carries the plant's own envelope, once.
    assert [chart["id"] for chart in out["charts"]] == ["c1"]
    assert out["charts"][0]["envelope"] == served["spc_chart"]


def test_a_part_names_its_chart_rather_than_carrying_a_second_copy_of_it(exploring):
    """Why the order is a list of ids and not a list of envelopes. A control
    chart's payload is the plant's own readings, and a reply that held one
    twice would put the same rows on the wire twice - for a picture the browser
    already has."""
    script, _served = exploring
    script += [_turn(_use("tu_1", "spc_chart", material="FG-COLA",
                          characteristic="fill height"), stop="tool_use"),
               _turn(_use("tu_2", agent.DRAW_TOOL,
                         **{"from": "tu_1", "shape": "spc", "title": "the chart"}),
                     stop="tool_use"),
               _turn(_text(THE_WHY))]
    out = agent.message(_open(), "draw the fill height chart")

    drawn = [part for part in out["parts"] if "chart" in part]
    assert drawn == [{"chart": "c1"}]
    assert "envelope" not in drawn[0]
    assert list(drawn[0]) == ["chart"]


def test_a_turn_with_one_round_of_words_says_them_once(exploring):
    """The ordinary answer, unchanged. One round, one paragraph, and `parts` is
    that paragraph - not the same words twice because they are now in two
    places in the reply."""
    script, _served = exploring
    script += [_turn(_text(THE_WHY))]
    out = agent.message(_open(), "why is sample 12 high?")

    assert out["say"] == THE_WHY
    assert out["parts"] == [{"text": THE_WHY}]


def test_the_words_of_every_round_are_in_the_turn_the_trace_records(exploring):
    """And the record of the turn, which is what the AI screen reads back later.
    An answer that reached the person in full and the trace in half would be a
    trace that cannot be used to judge the answer."""
    script, _served = exploring
    script += [_turn(_text(FOR_HIS_BOSS), _use("tu_1", "spc_sample", sample_id=12),
                     stop="tool_use"),
               _turn(_text(THE_WHY))]
    sess = _open()
    agent.message(sess, "how does the chart look, and why?")

    assert sess.last_turn is not None
    assert FOR_HIS_BOSS in sess.last_turn["said"]
    assert THE_WHY in sess.last_turn["said"]


# ------------------------------------------- and the paths that were left alone

def test_a_turn_that_ran_out_of_rounds_keeps_the_words_it_managed_and_says_so(
        exploring):
    """Out of rounds is not a blank screen. Whatever the model managed to write
    before the budget ran out is still the person's to read, and the sentence
    that says what happened is the last thing in it rather than the only
    thing."""
    script, _served = exploring
    sess = _open(max_rounds=2)
    script += [_turn(_text(FOR_HIS_BOSS), _use("tu_1", "spc_sample", sample_id=12),
                     stop="tool_use"),
               _turn(_text("Still looking."), _use("tu_2", "spc_sample", sample_id=11),
                     stop="tool_use"),
               _turn(_text("and again"), _use("tu_3", "spc_sample", sample_id=10),
                     stop="tool_use")]
    out = agent.message(sess, "how does the chart look, and why?")

    assert out["kind"] == "reply"
    assert FOR_HIS_BOSS in out["say"]
    assert "I stopped after too many steps" in out["say"]
    assert out["parts"][0] == {"text": FOR_HIS_BOSS}
    assert "stopped after too many steps" in out["parts"][-1]["text"]
