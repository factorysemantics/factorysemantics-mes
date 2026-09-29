# The GPU budget

One RTX 3070 Ti, 8 GB. qwen3:8b takes ~5.6 GB loaded; nomic-embed-text is
small. There is no second slot — a vision model would evict qwen on every
swap, which is why the UI loop is deterministic instead.

**Standing priority order** (ratified by Scott, 2026-09-02): when the model
is busy, later entries wait for earlier ones; nothing here ever queues a
product feature behind dev tooling.

1. **Floor assistant and product features** — the agentic MES is the point
   of the project. Whatever the product ships that needs the local model
   (assistant routing and answers, instruction drafting, future agent
   features) goes first.
2. **Scored-run log triage** — reading each run's logs for what nobody wrote
   a test for. Bounded per run, and its findings are promoted to store
   columns.
3. **Nightly rollup narration** — one note a day; the model is handed
   computed numbers and asked only what changed.
4. **Design chat** — Scott's capture surface. Useful, interruptible, and
   the deep thinking happens in Claude Code anyway.

**The UI conformance loop consumes zero GPU by design.** Its checks are a
real browser and file comparisons. Its entire claim on the model is the
one paragraph the nightly rollup already writes, and only when drift exists.

# The cloud budget

The floor agent (the Assistant panel's act mode) runs on Claude Sonnet 5
over the API, not the GPU. **Cap: $10 a month** (Scott, 2026-09-03), set in
the Anthropic Console *and* enforced by the plant: every turn's token usage
is appended to `~/.local/share/fsmes/agent-usage.jsonl`, the month is summed
at list prices, and past `MES_AGENT_MONTHLY_USD` (default 10) the panel says
the budget is spent and the local assistant carries on. A 10-turn floor task
with the tool catalogue cached costs roughly 1–3 cents. The Local AI panel
shows the running total beside the GPU rows.

## Two agents, one bill

There are two agent kinds now — the floor assistant, which proposes changes,
and the **analysis agent**, which holds every read tool and no write tool.

**The month's cap is shared.** Both kinds' turns go into the same usage file
and are summed together, so `MES_AGENT_MONTHLY_USD` is the whole box's
budget whichever agent spent it. A second kind that could get around the
month's cap would not be a cap.

**A conversation has a cap of its own**, and that is the new number:

| Key | Default | What it is |
|---|---|---|
| `MES_AGENT_CONVERSATION_USD` | `0` (uncapped) | What one floor conversation may spend. Uncapped is what it has always been: a floor conversation is bounded by `[admin] agent_max_rounds` and by somebody standing at a machine waiting for it |
| `MES_ANALYSIS_CONVERSATION_USD` | `0.25` | What one analysis conversation may spend — a fortieth of the month |
| `MES_AGENT_BRAIN` | `auto` | `off` switches the floor assistant off |
| `MES_ANALYSIS_BRAIN` | `auto` | `off` switches the analysis agent off, on its own |

Why the analysis agent gets one and the floor assistant does not: it holds
every read tool, it is asked to explore, and nobody is watching each round
of it. The cap is a **speed bump, not a wall** — the next conversation
starts at zero, and the wall is the month's cap, which no new conversation
gets around. When a conversation reaches it, it says so, says what the
month has left, and stops; it does not hand over to the local model,
because there is no local analysis agent.

**These four are environment keys today and become `[ai]` settings in M2**
(`docs/design/agentic-harness.md` §9), with the same words in the same
order: `analysis_brain`, `analysis_conversation_usd`. The rename is a move,
not a redesign, which is why they are named this way now.

**In shadow mode the analysis agent is off** and says so (answer 9 of the
harness design). The floor assistant falls back to the local model on a
shadow plant; the analysis agent has no local stand-in, and answering worse
was the option that was turned down.

Embeddings (nomic-embed-text) are effectively free beside qwen and are not
budgeted. The Local AI panel on `/dashboard/ops` displays this order so
"is the GPU doing what I decided it should" is answerable at a glance;
change the order there and here together, deliberately.
