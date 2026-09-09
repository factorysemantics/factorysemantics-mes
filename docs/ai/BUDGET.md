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

Embeddings (nomic-embed-text) are effectively free beside qwen and are not
budgeted. The Local AI panel on `/dashboard/ops` displays this order so
"is the GPU doing what I decided it should" is answerable at a glance;
change the order there and here together, deliberately.
