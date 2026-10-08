"""An exploration draws the plant's own payload, or it draws nothing.

`docs/design/deep-analysis.md` §1 is a management question answered end to end,
and the picture beside the answer is half of it. This file holds the half that
can be checked without a browser: **where the numbers in a chart come from.**

The rule, and it is one rule seen from three sides:

1. The agent names a tool call it made - `draw(from=<its own tool_use id>)` -
   and the loop attaches the envelope out of what the plant actually returned.
   There is no field on that tool for numbers, and a spec that carries some is
   refused by name rather than ignored.
2. A spec naming no tool result is refused with a plain sentence that says what
   *was* read, because a refusal with no next step is half an answer. `from`
   takes a `tool_use` id **or a tool name** - that tool's most recent answer -
   and a guessed id like `downtime_pareto_1` is neither, so it is still refused,
   with a sentence that now teaches the shorter way to say it.
3. What reaches the trace is a summary - shape, tool, total, coverage word - and
   never the envelope. `ai_turns` records what the person was shown; a second
   copy of the plant's rows in it would be a way round the capabilities those
   rows are behind, which is the promise the AI screen makes in so many words.

Beside them: the prompt's rule that a question about people starts at the trace,
the chain that rule produces on the scripted model, the per-conversation cost
every reply carries, and the cap that stops an exploration and says what the
month has left.

The browser half - that the drawn `data-*` are the payload's own figures, that
the holes are drawn, that a node expands into a question, and that the picture
reaches the screen for a question with no "draw" in it - is in
`tests/test_the_ai_tab_explores_and_draws_what_it_read.py`, which needs a
browser to say anything.
"""

from types import SimpleNamespace

import pytest

from fsmes.services import agent

#: The envelope this file draws from: a trace graph, shaped the way the route
#: serves one - a coverage that is the word `absent` rather than a figure,
#: because a graph of records is not a rate over a window anybody watched.
A_GRAPH = {
    "nodes": [{"id": "question_group:label a stop", "kind": "question_group",
               "label": "how do I label a stop", "weight": 41, "degree": 3},
              {"id": "unattributed", "kind": "unattributed", "weight": 3, "degree": 1}],
    "edges": [{"from": "role:operator", "to": "question_group:label a stop",
               "kind": "asked", "weight": 41, "watched_seconds": None}],
    "nodes_total": 13, "edges_total": 14,
    "coverage": "absent",
    "coverage_note": "a graph of records, not a share of a watched window",
}

A_PARETO = {"reasons": [{"reason": "mechanical", "seconds": 2140},
                        {"reason": "unlabelled", "seconds": 1180}],
            "total_seconds": 3320, "unlabelled_share": 0.355,
            "unknown_seconds": 900, "unknown_share": 0.04}

#: A control chart, shaped the way `/quality/spc/{material}/{characteristic}`
#: serves one: the limits the plant worked out, the rules it found, and its own
#: verdict. The only payload in this file whose whole meaning is in the lines
#: and not in the points.
A_CONTROL_CHART = {
    "material": "FG-COLA", "characteristic": "fill height", "unit": "mm",
    "kind": "xbar_r", "sample_size": 5, "n": 2, "readings": 10,
    "lower_spec": 59.0, "upper_spec": 63.0,
    "points": [{"value": 61.2, "ts": "2026-10-07T21:18:00", "sample": 11, "check": 101,
                "range": 0.4},
               {"value": 62.6, "ts": "2026-10-07T21:38:00", "sample": 12, "check": 106,
                "range": 0.6}],
    "control": {"centre": 61.555, "lower": 60.3, "upper": 62.5, "sigma": 0.38},
    "signals": [{"rule": 1, "index": 1, "label": "beyond the upper control limit"}],
    "verdict": "out of control, not out of spec",
    "coverage": "absent",
    "coverage_note": "a list of the records in this window, not a rate over a "
                     "watched one - so there is no coverage figure to give",
}


def _usage():
    return SimpleNamespace(input_tokens=1000, output_tokens=50,
                           cache_read_input_tokens=800, cache_creation_input_tokens=0)


def _turn(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=_usage())


def _said(text):
    return _turn(SimpleNamespace(type="text", text=text))


def _wants(id_, tool, **args):
    return _turn(SimpleNamespace(type="tool_use", id=id_, name=tool, input=args),
                 stop="tool_use")


@pytest.fixture()
def exploring(monkeypatch, tmp_path):
    """An analysis conversation with the model scripted and the plant's reads
    stood in for. What is under test is the loop, not the arithmetic: the
    envelopes above are what a real route returns, key for key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MES_ANALYSIS_BRAIN", "claude")
    monkeypatch.setattr(agent, "sdk_installed", lambda: True)
    monkeypatch.setattr(agent, "USAGE_FILE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(agent, "TURN_FILE", tmp_path / "turns.jsonl")
    script: list = []
    served: dict = {"trace_graph": A_GRAPH, "downtime_pareto": A_PARETO,
                    "spc_chart": A_CONTROL_CHART}

    def fake_model(sess):
        return script.pop(0)

    def fake_execute(name, args, *, plant, on_behalf_of=None, dry_run=None, client_ref=None):
        return served.get(name, {"plant": plant, "total": 1, "showing": 1})

    monkeypatch.setattr(agent, "_call_model", fake_model)
    monkeypatch.setattr(agent, "execute", fake_execute)
    return script, served


def _open(**kwargs):
    return agent.open_session("SCOTT", "testplant", {"plant.read", "audit.read"},
                              kind=agent.ANALYSIS, **kwargs)


# ------------------------------------------------ the chart is the plant's own

def test_a_chart_carries_the_envelope_the_plant_returned_and_not_a_number_of_its_own(
        exploring):
    """The whole design, in one exchange: read, then name the read, and what
    reaches the browser is the payload the plant computed - the same object,
    not a copy with rounder numbers in it."""
    script, served = exploring
    script += [_wants("tu_1", "trace_graph", hours=24),
               _wants("tu_2", agent.DRAW_TOOL, **{"from": "tu_1", "shape": "graph",
                                                  "title": "who asks what"}),
               _said("The biggest cluster is labelling a stop: 41 turns of 380.")]
    sess = _open()
    out = agent.message(sess, "what is the biggest problem for our operators?")

    assert len(out["charts"]) == 1
    chart = out["charts"][0]
    assert chart["shape"] == "graph"
    assert chart["tool"] == "trace_graph"
    assert chart["title"] == "who asks what"
    # The envelope, whole: the browser is handed what the plant returned.
    assert chart["envelope"] == served["trace_graph"]
    # And the two figures a frame is drawn from, read off that payload rather
    # than counted here.
    assert chart["total"] == 13
    assert chart["coverage"] == "absent"


def test_a_chart_spec_that_carries_its_own_numbers_is_refused_by_name(exploring):
    """The refusal that keeps rule 1 true. A model that worked out a series and
    handed it over would be stating a figure this MES never measured, in the
    most convincing medium the product has - so the tool has nowhere to put one,
    and a spec that finds a way is told which key to drop."""
    script, _served = exploring
    script += [_wants("tu_1", "trace_graph", hours=24),
               _wants("tu_2", agent.DRAW_TOOL,
                      **{"from": "tu_1", "shape": "graph",
                         "nodes": [{"id": "made-up", "weight": 999}]}),
               _said("Drawn.")]
    sess = _open()
    out = agent.message(sess, "graph it")

    assert "charts" not in out
    refusal = next(row for row in out["transcript"] if row["tool"] == agent.DRAW_TOOL)
    assert refusal["ok"] is False
    assert "no data of its own" in refusal["summary"]
    assert "nodes" in refusal["summary"]


def test_a_chart_spec_pointing_at_no_tool_result_is_refused_and_says_what_was_read(
        exploring):
    """The other half of the same rule. The sentence names what this
    conversation actually read, because a refusal that leaves somebody with no
    next step is half an answer."""
    script, _served = exploring
    script += [_wants("tu_1", "trace_graph", hours=24),
               _wants("tu_2", agent.DRAW_TOOL,
                      **{"from": "tu_nothing", "shape": "graph"}),
               _said("I could not draw that.")]
    sess = _open()
    out = agent.message(sess, "graph it")

    assert "charts" not in out
    refusal = next(row for row in out["transcript"] if row["tool"] == agent.DRAW_TOOL)
    assert refusal["ok"] is False
    assert "nothing in this conversation was read under the id" in refusal["summary"]
    assert "trace_graph" in refusal["summary"], "the refusal does not say what was read"


def test_a_chart_can_name_the_tool_instead_of_the_id_and_gets_its_latest_answer(
        exploring):
    """The fallback that costs a guess nothing. On 2026-09-29 the first live
    exploration that drew anything spent two rounds on invented ids before it
    used the real ones - the ids were in the conversation, but a model that has
    not looked has no way to be sure, and the person waits through the round
    trip. A tool name means that tool's most recent answer here."""
    script, served = exploring
    script += [_wants("tu_1", "trace_graph", hours=24),
               _wants("tu_2", agent.DRAW_TOOL, **{"from": "trace_graph",
                                                  "shape": "graph"}),
               _said("Drawn from the graph I read.")]
    sess = _open()
    out = agent.message(sess, "what is the biggest problem for our operators?")

    assert len(out["charts"]) == 1
    chart = out["charts"][0]
    # Resolved to the call, not left as the word: the spec says which tool_use
    # its numbers came out of, because that is what makes them checkable.
    assert chart["from"] == "tu_1"
    assert chart["tool"] == "trace_graph"
    assert chart["envelope"] == served["trace_graph"]


def test_a_tool_name_takes_the_most_recent_answer_that_tool_gave(exploring):
    """"Most recent" is the rule, and it is the useful one: an exploration that
    narrowed a graph and then drew it means the narrower picture, not the first
    one it read."""
    script, served = exploring
    narrower = {**A_GRAPH, "nodes_total": 3, "nodes": A_GRAPH["nodes"][:1]}
    script += [_wants("tu_1", "trace_graph", hours=168),
               _wants("tu_2", "trace_graph", hours=24),
               _wants("tu_3", agent.DRAW_TOOL, **{"from": "trace_graph",
                                                  "shape": "graph"}),
               _said("Drawn.")]
    sess = _open()

    def serve_two(name, args, *, plant, on_behalf_of=None, dry_run=None, client_ref=None):
        if name == "trace_graph":
            return narrower if args.get("hours") == 24 else A_GRAPH
        return served.get(name, {"plant": plant, "total": 1, "showing": 1})

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as env:
        env.setattr(agent, "execute", serve_two)
        out = agent.message(sess, "narrow it and draw it")

    assert out["charts"][0]["from"] == "tu_2"
    assert out["charts"][0]["total"] == 3


def test_a_guessed_id_is_still_refused_and_the_refusal_names_the_shorter_way(
        exploring):
    """The two shapes the live run guessed - `downtime_pareto_1`, `trace_graph_1`
    - are neither an id nor a tool name, and nothing here matches them to one:
    drawing a payload because a string looked a bit like its tool would be
    picking the numbers for the model. So the refusal stands, and it now says
    both ways of naming a read, so the next call is right."""
    script, _served = exploring
    for guess in ("downtime_pareto_1", "trace_graph_1"):
        script[:] = [_wants("tu_1", "downtime_pareto", hours=8),
                     _wants("tu_2", agent.DRAW_TOOL, **{"from": guess, "shape": "bars"}),
                     _said("I could not draw that.")]
        sess = _open()
        out = agent.message(sess, "draw the pareto")

        assert "charts" not in out, f"{guess} drew something"
        refusal = next(row for row in out["transcript"] if row["tool"] == agent.DRAW_TOOL)
        assert refusal["ok"] is False
        assert guess in refusal["summary"]
        assert "or the tool name" in refusal["summary"]
        assert "the tool's name" in refusal["summary"], (
            "the refusal does not teach the form that would have worked")
        assert "downtime_pareto" in refusal["summary"]


def test_a_read_that_failed_is_not_something_a_chart_can_be_drawn_from(exploring):
    """A chart of a refusal is not a chart. The payload of a failed read is a
    sentence about why there was no answer, and drawing it would put a title
    over an error."""
    script, served = exploring
    served["trace_graph"] = {"error": "that window is outside what this plant keeps"}
    script += [_wants("tu_1", "trace_graph", hours=99999),
               _wants("tu_2", agent.DRAW_TOOL, **{"from": "tu_1", "shape": "graph"}),
               _said("Nothing to draw.")]
    sess = _open()
    out = agent.message(sess, "graph it")

    assert "charts" not in out
    assert sess.payloads == {}


def test_a_control_chart_is_drawn_as_a_control_chart(exploring):
    """The picture Scott asked for, end to end. He reads a control chart as a
    control chart - limits, specification band, the dots that fired a rule -
    and the shape that draws one is named on the tool, so the model asking for
    his slide gets his chart and not a series."""
    script, served = exploring
    script += [_wants("tu_1", "spc_chart", material="FG-COLA",
                      characteristic="fill height"),
               _wants("tu_2", agent.DRAW_TOOL,
                      **{"from": "spc_chart", "shape": "spc",
                         "title": "Fill height, last 30 samples"}),
               _said("Fill height is out of control, not out of spec.")]
    sess = _open()
    out = agent.message(sess, "how does the fill height chart look?")

    chart = out["charts"][0]
    assert chart["shape"] == "spc"
    # The limits and the verdict travel whole: the browser draws the plant's
    # own numbers, and nothing between here and the SVG works any of them out.
    assert chart["envelope"] == served["spc_chart"]
    assert chart["envelope"]["control"]["upper"] == 62.5
    assert chart["envelope"]["verdict"] == "out of control, not out of spec"
    assert chart["coverage"] == "absent"


def test_a_control_chart_asked_for_as_a_line_is_refused_and_told_which_shape(exploring):
    """The failure Scott saw on 2026-10-07, refused a round trip before the
    browser. Asked for the fill-height chart the model drew `spc_chart` as a
    `line`: every number right, on a zero-based axis, with no control limits,
    no specification band and nothing marking the point that fired rule 1 - a
    picture of a settled process, of a process that is not settled. `kit.js`
    refuses it by name; so does this, and the refusal says `spc` rather than
    only saying no."""
    script, _served = exploring
    script += [_wants("tu_1", "spc_chart", material="FG-COLA",
                      characteristic="fill height"),
               _wants("tu_2", agent.DRAW_TOOL, **{"from": "tu_1", "shape": "line"}),
               _said("Drawn.")]
    sess = _open()
    out = agent.message(sess, "chart the fill height")

    assert "charts" not in out
    refusal = next(row for row in out["transcript"] if row["tool"] == agent.DRAW_TOOL)
    assert refusal["ok"] is False
    assert "`spc`" in refusal["summary"]
    for missing in ("control limits", "specification", "fired a rule"):
        assert missing in refusal["summary"]


def test_a_payload_that_is_not_a_control_chart_is_still_drawn_as_it_was_asked(exploring):
    """And the refusal is narrow. It fires on the three keys that make a
    payload a control chart, so a tag trend asked for as a `line` is a tag
    trend drawn as a line - a guard that caught everything with points in it
    would be this file deciding what every reader is looking at."""
    script, served = exploring
    served["tag_trend"] = {"tag": "FillWeight", "points": [{"ts": "x", "mean": 61.0}],
                           "total": 1, "coverage": 0.72}
    script += [_wants("tu_1", "tag_trend", tag="FillWeight", hours=8),
               _wants("tu_2", agent.DRAW_TOOL, **{"from": "tu_1", "shape": "line"}),
               _said("Drawn.")]
    sess = _open()
    out = agent.message(sess, "trend it")

    assert out["charts"][0]["shape"] == "line"


def test_only_the_kind_that_explores_is_offered_the_draw_tool():
    """The floor assistant answers beside somebody standing at a machine, and a
    picture there is a dashboard nobody asked for (design page §2). It is a
    property of the kind, checked on the kind, so a third kind arrives with an
    answer rather than inheriting one."""
    analysis = _open()
    assert agent.DRAW_TOOL in {t["name"] for t in analysis.tools}
    assert agent.KINDS[agent.ANALYSIS].draws is True

    floor = agent.open_session("SCOTT", "testplant", {"plant.read"}, kind=agent.FLOOR)
    assert agent.DRAW_TOOL not in {t["name"] for t in floor.tools}
    assert agent.KINDS[agent.FLOOR].draws is False


def test_the_draw_tool_has_nowhere_to_put_a_number():
    """Not a rule the loop enforces after the fact - a shape of the tool itself.
    Three string fields and `additionalProperties: false`, so the first place a
    series would be refused is the API that took the call."""
    spec = agent.chart_tools()[0]
    schema = spec["input_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(agent.DRAW_KEYS)
    assert all(field["type"] == "string" for field in schema["properties"].values())
    assert schema["properties"]["shape"]["enum"] == list(agent.CHART_SHAPES)


def test_a_person_who_may_not_read_the_plant_is_offered_nothing_at_all():
    """The draw tool rides on the catalogue and not beside it: without
    `plant.read` there is no conversation, so there is nothing to draw and no
    tool offering to."""
    nothing = agent.open_session("NOBODY", "testplant", set(), kind=agent.ANALYSIS)
    assert nothing.tools == []


# --------------------------------------------------------------- the trace

def test_the_trace_records_the_chart_as_a_summary_and_never_the_envelope(exploring):
    """What `ai_turns` is allowed to keep of a picture. The turn the caller
    writes carries the `draw` call with shape, tool, total and coverage word in
    its one-line summary - and nowhere in that row is a node, an edge or a
    figure out of the payload."""
    import json

    script, _served = exploring
    script += [_wants("tu_1", "trace_graph", hours=24),
               _wants("tu_2", agent.DRAW_TOOL, **{"from": "tu_1", "shape": "graph",
                                                  "title": "who asks what"}),
               _said("Drawn.")]
    sess = _open()
    agent.message(sess, "graph it")

    row = sess.last_turn
    drawn = [call for call in row["tools"] if call["tool"] == agent.DRAW_TOOL]
    assert len(drawn) == 1
    summary = drawn[0]["summary"]
    assert "graph of trace_graph" in summary
    assert "total 13" in summary and "coverage absent" in summary

    # The row's own identifiers come out before the substring check, the way
    # `tu_1` already did. `session` is `uuid4().hex[:12]`, and a random
    # twelve-character hex string contains "41" about four times in a hundred -
    # so this assertion failed about one run in twenty-five, for ever, on
    # nobody's change (found on #142's CI, 2026-10-05).
    written = json.dumps(row, default=str).replace('"tu_1"', "").replace(row["session"], "")
    assert "how do I label a stop" not in written, "an envelope reached the trace"
    assert "41" not in written, "a figure off the payload reached the trace"


# ---------------------------------------------------------------- the cost

def test_every_reply_says_what_it_cost_and_what_the_conversation_has_spent(exploring):
    """Three numbers, because they answer three questions: what that answer
    cost, what this exploration has spent of what one may spend, and where the
    month stands. Estimates at list prices - the Console is the bill, which is
    what every screen drawing these says."""
    script, _served = exploring
    script += [_said("Nothing read; here is the answer.")]
    sess = _open()
    out = agent.message(sess, "how are we doing?")

    cost = out["cost"]
    assert cost["turn_usd"] > 0
    assert cost["conversation_usd"] >= cost["turn_usd"]
    assert cost["conversation_cap_usd"] == agent.KINDS[agent.ANALYSIS].conversation_usd
    assert cost["month_cap_usd"] == agent.monthly_cap_usd()


def test_an_exploration_at_its_cap_stops_and_says_what_the_month_has_left(exploring):
    """The speed bump, and the wall behind it. A conversation that has spent its
    own cap stops with a sentence naming the environment key, and points at the
    month's budget, which is the one that does not reset."""
    script, _served = exploring
    script += [_said("late")]
    sess = _open()
    sess.spent_usd = sess.cap_usd + 0.01
    out = agent.message(sess, "and again?")

    assert out["kind"] == "unavailable"
    assert out["reason"] == "spent"
    assert "MES_ANALYSIS_CONVERSATION_USD" in out["why"]
    assert "the month's budget" in out["why"]
    # Not "off": the plant's brain is fine, and the panel only hands over to the
    # local model on `off`.
    assert out["reason"] != "off"


# --------------------------------------------------------------- the prompt

def test_the_analysis_prompt_starts_a_question_about_people_at_the_trace():
    """The rule this handoff put in the prompt, in one paragraph: a question
    about the people here begins with what they asked, and only then follows the
    thread onto the floor. Asserted on the prompt because the prompt is where it
    lives; asserted as a chain in the request suite because a paragraph nobody
    follows is a paragraph."""
    prompt = agent.system_for(agent.ANALYSIS)
    assert "trace_rollup" in prompt and "trace_graph" in prompt
    for word in ("operators", "biggest problem", "questions"):
        assert word in prompt, f"the rule does not mention {word!r}"
    # And the silence it has to name: the absence is the finding, and the causal
    # claim is the one thing it must not make.
    assert "connect to nothing at all" in prompt
    assert "never say a question caused a stop" in prompt


def test_the_prompt_tells_the_model_how_to_ask_for_a_chart_and_that_it_passes_no_numbers():
    prompt = agent.system_for(agent.ANALYSIS)
    assert agent.DRAW_TOOL in prompt
    assert "you never pass numbers" in prompt
    for shape in agent.CHART_SHAPES:
        assert shape in prompt


def test_the_prompt_names_the_shape_a_control_chart_is_drawn_as():
    """Naming the shape in the enum is not enough: `line` is the shape a model
    reaches for when the answer is a series over time, and a control chart is
    a series over time. So the prompt says which, and says what a `line` would
    leave off."""
    prompt = agent.system_for(agent.ANALYSIS)
    assert "`spc_chart` is `spc`" in prompt
    assert "never `line`" in prompt
    # And the tool's own description says it too, for a model that reads the
    # tool and not the prompt.
    assert "`spc` for an `spc_chart` payload" in agent.chart_tools()[0]["description"]


def test_the_prompt_says_a_graph_it_read_is_drawn_and_not_described():
    """One sentence, added 2026-09-29. The first live exploration read the
    rollup, the graph and the pareto, answered honestly, and drew nothing; the
    same question with "draw" in it drew both. So the default is in the prompt
    now - and the other half of the same sentence says the tool name is a way to
    name a read, so the model that has not memorised its own ids still gets one
    call right."""
    prompt = agent.system_for(agent.ANALYSIS)
    assert "drawn, not described" in prompt
    assert "without being asked" in prompt
    assert "that tool's name" in prompt


def test_the_floor_assistants_prompt_says_nothing_about_charts():
    """One kind draws and the other does not, and the prompts say the same
    thing the catalogue does."""
    assert agent.DRAW_TOOL not in agent.system_for(agent.FLOOR)


# ----------------------------------------------------- the chain, as scored

def test_the_scripted_run_of_the_chain_case_reads_the_trace_before_the_floor():
    """The order, which the scorer does not check because `reads` is a set.

    A question about people that went straight to the downtime pareto would be
    answering a question about machines, and would look identical to a scorer
    counting which tools were called.
    """
    from fsmes.lab import assist_eval as runs
    from fsmes.services import assist_eval

    case = next(c for c in assist_eval.load()
                if c.id == "analyst-follows-the-question-from-the-trace-into-the-floor")
    order = [step["read"] for step in case.plan if step.get("read")]
    assert order == ["trace_rollup", "trace_graph", "downtime_pareto"]

    with runs.scripted_plant() as plant:
        turn = runs.run_scripted_case(case, plant.plant)
    assert list(turn.reads) == order, turn.reads
    assert assist_eval.score(case, turn).passed, assist_eval.score(case, turn).why


def test_the_scripted_chain_draws_the_graph_and_the_pareto_with_no_draw_in_the_question(
):
    """The prompt's new default, scored rather than described. The request the
    case carries never says "draw" - and the run has to come back with a picture
    of the graph and a picture of the pareto anyway, each carrying the payload of
    the read it names."""
    from fsmes.lab import assist_eval as runs
    from fsmes.services import assist_eval

    case = next(c for c in assist_eval.load()
                if c.id == "analyst-follows-the-question-from-the-trace-into-the-floor")
    assert "draw" not in case.request.lower(), (
        "the case now asks for the picture, which is the thing under test")
    assert case.draws == ("trace_graph", "downtime_pareto")

    with runs.scripted_plant() as plant:
        turn = runs.run_scripted_case(case, plant.plant)
    assert {chart["tool"] for chart in turn.charts} == {"trace_graph", "downtime_pareto"}
    # And the shapes are the ones the design page names for them.
    by_tool = {chart["tool"]: chart for chart in turn.charts}
    assert by_tool["trace_graph"]["shape"] == "graph"
    assert by_tool["downtime_pareto"]["shape"] == "bars"
    # The picture carries the plant's own payload, not the stand-in's idea of one.
    assert by_tool["trace_graph"]["envelope"]["coverage"] == "absent"
    assert assist_eval.score(case, turn).passed, assist_eval.score(case, turn).why


def test_the_suite_calls_the_conversations_own_tools_what_the_loop_calls_them():
    """The suite writes out the three tools the conversation owns rather than
    importing them, so the scorer can be read on its own. This is what keeps the
    copies honest: rename one in the loop and the copy stops matching here rather
    than quietly scoring nothing."""
    from fsmes.services import assist_eval

    assert assist_eval.DRAW_TOOL == agent.DRAW_TOOL
    assert (assist_eval.GUIDES_TOOL, assist_eval.SHOW_GUIDE_TOOL) == agent.GUIDE_TOOLS
    # And none of them is a read of the plant: a `draw` counted among a turn's
    # reads would break every case that names its reads exactly.
    assert {agent.DRAW_TOOL, *agent.GUIDE_TOOLS} == assist_eval._OURS


def test_a_case_that_asks_for_a_drawing_it_never_read_is_refused_by_the_suite():
    """The suite's own validation. A picture is drawn from a read, so `draws`
    naming a tool the case never reads is a requirement for a chart of nothing -
    which is the refusal `draw` gives at runtime, caught at load instead."""
    from fsmes.services import assist_eval

    problems: list[str] = []
    assist_eval._case({"id": "made-up", "request": "graph it", "expect": "read",
                       "reads": ["trace_rollup"], "draws": ["trace_graph"]},
                      "analyst", "analyst.toml", problems, kind="analysis")
    assert any("never reads" in problem for problem in problems), problems

    floor: list[str] = []
    assist_eval._case({"id": "made-up", "request": "graph it", "expect": "read",
                       "reads": ["trace_graph"], "draws": ["trace_graph"]},
                      "operator", "operator.toml", floor, kind="floor")
    assert any("only an analysis case" in problem for problem in floor), floor

    # And a `draws` on an expectation the scorer never reaches: a walk and a
    # refusal are not scored through the reads, so a drawing demanded there
    # would be an expectation nothing checks.
    walked: list[str] = []
    assist_eval._case({"id": "made-up", "request": "show me the graph",
                       "expect": "walk", "guide": "some-walk",
                       "reads": ["trace_graph"], "draws": ["trace_graph"]},
                      "analyst", "analyst.toml", walked, kind="analysis")
    assert any("scored on a question" in problem for problem in walked), walked


def test_an_exploration_that_described_a_graph_instead_of_drawing_it_fails_the_case():
    """The scorer's own half. A turn with the three reads and no chart is the
    turn the live run gave on 2026-09-29, and it has to fail - otherwise the
    sentence in the prompt is a sentence nobody checks."""
    from fsmes.services import assist_eval

    case = next(c for c in assist_eval.load()
                if c.id == "analyst-follows-the-question-from-the-trace-into-the-floor")
    described = assist_eval.Turn(
        kind="reply", say="The biggest cluster is labelling a stop.",
        reads=("trace_rollup", "trace_graph", "downtime_pareto"),
        facts="unattributed turns; unlabelled_share 0.36")
    outcome = assist_eval.score(case, described)
    assert not outcome.passed
    assert any("described it instead of drawing it" in why for why in outcome.why), outcome.why


def test_the_question_that_needed_a_shift_stamp_is_no_longer_waiting_on_a_handoff():
    """D5 owned one `not_yet` case - *"compare oee and scrap during SCOTT's
    shift"* - and this is the assertion that it is required now. The ratchet in
    `test_assist_suite_scripted.py` fails on a `not_yet` case that passes; this
    one fails if the mark comes back."""
    from fsmes.services import assist_eval

    case = next(c for c in assist_eval.load()
                if c.id == "analyst-asks-about-oee-and-scrap-in-one-persons-shift")
    assert case.expected == "pass"
    assert case.handoff is None or case.handoff == ""


def test_the_audit_trail_says_which_shift_a_row_fell_in(make_client, session):
    """What makes that case answerable: D1 stamped the rows and nothing read the
    stamp. Two facts side by side - the shift a person's rows fell in, and what
    that shift made - and never a third, because a booking carries no actor at
    all."""
    from fsmes.services import auth

    auth.create_user(session, code="STAMPED", name="Stamped", password="x",
                     role="admin")
    session.commit()
    client = make_client()
    client.post("/auth/login", json={"code": "STAMPED", "password": "x"})
    rows = client.get("/audit?limit=5").json()
    assert rows, "this plant recorded nothing to read"
    for row in rows:
        assert "shift_code" in row and "shift_day" in row
