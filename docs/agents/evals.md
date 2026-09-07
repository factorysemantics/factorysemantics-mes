# Evals

*Explanation. The scorecard asks whether the MES told the truth about the plant. The evals ask the next question: can an agent, working through the tools alone, find that truth?*

A scenario is a question and a way to compute the right answer from the
API at the moment of asking. The agent gets the question and the MCP
server, nothing else. Its answer is scored against the truth and kept, so
"is the MES getting more usable by agents" becomes a trend the way honesty
already is.

Two rules:

- **Truth is computed through the same public API the agent uses.** If the
  API cannot answer a question, the scenario cannot exist. This keeps the
  evals from measuring a private shortcut.
- **The agent is a real one.** `fsmes agent-eval` drives a headless Claude
  Code session against the running MCP server; nothing is mocked on the
  agent side.

Results accumulate in a local JSONL store. The washer story
(`tests/test_washer_story.py`) is the causal eval: a cross-station effect
the agent must explain from alarm history and states, not guess.

## See also

- [Tools reference](../reference/tools.md)
- `fsmes agent-eval --help` in the [CLI reference](../reference/cli.md)
