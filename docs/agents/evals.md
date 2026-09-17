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

## How an answer is read

An answer is read twice, and both readings are kept on the same row.

**The check** is a rule about tokens: does the reply name every expected
machine code and none of the distractors. A code is named when the reply
writes it as a whole token — `MIX01` is not named by `MIX011` — and case is
ignored only for a code carrying a digit, a hyphen or an underscore, since
`mix01` can be nothing but a code. A code made only of letters, like
`DRAWING`, is an ordinary English word in lowercase, so it counts only where
the reply writes it in capitals. `NONE` is the answer vocabulary rather than
a code and is read in any case. **This is the number the trend is drawn
from.**

**The judgment** asks a hosted judgment model one typed question about the
same reply — does it name exactly the expected codes and no distractor — and
records the probability that comes back, with the model version as served
and the time. It is a second opinion recorded beside the check, never in
place of it: it gates nothing, it has no threshold, and it is off unless
`MES_JEV_API_KEY` is set, which is what every installation of this package
has. With no key the row says `not asked (no MES_JEV_API_KEY in this
environment)` and the eval behaves exactly as it did before. See
[the judgment model in the build loop](../ai/JUDGMENT-IN-THE-BUILD-LOOP.md).

`fsmes agent-eval --summary` prints both, labelled: the pass rate from the
check, and beside it how many results were judged out of how many kept, the
mean probability, and — separately — that mean where the check passed and
where it failed. Those last two are the beginning of the calibration plot
any threshold would need, and until it exists there is no threshold.

## See also

- [Tools reference](../reference/tools.md)
- `fsmes agent-eval --help` in the [CLI reference](../reference/cli.md)
