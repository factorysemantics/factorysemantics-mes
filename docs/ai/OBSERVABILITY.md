# The local AI layer — what runs, where it goes, how to check it

Written from the 2026-09-02 audit, performed live on `main`. This is the map
behind the **Local AI** panel on `/dashboard/ops` and `fsmes ai-status` —
when this document and the panel disagree, one of them has a bug.

## The consumers

| Job | Trigger | What it does | Output lands in | Check |
|---|---|---|---|---|
| Run triage | every scored run | qwen reads the run's logs for anomalies nobody asserted | `~/.local/share/fsmes/runs.db` (`triage_findings`, `triage_worst` columns; full detail inside the `scorecard` JSON) | `fsmes runs` or the panel |
| Nightly rollup | 05:30 timer, `Persistent=true` (catches up after boot) | qwen narrates numbers computed from the store | `~/.local/share/fsmes/reports/*.md` → pulled to `Vault/Sims/Reports/` by `fsmes-reports` (laptop) | `systemctl --user list-timers fsmes-rollup.timer` |
| Design chat | the Design button | qwen answers about the screen being looked at | `~/.local/share/fsmes/design.db` → `/design-triage` → `docs/design/backlog/` | `fsmes design-pending` |
| Floor assistant (local) | the Assistant button on a plant with no key | routes questions, quotes procedure, picks guides | answers live; stores nothing (falls back to lexical matching when Ollama is down) | ask it something |
| Floor agent (cloud) — *not a local consumer; read from `agent.status()`* | the Assistant button when `agent.available()` is true | a cloud model works the plant's own tools: reads freely — the four shift analyses included, each answering with the screen's own envelope, coverage ledger and all where the route serves one — proposes every write, puts a walkthrough on the screen | the plant's own `ai_turns` table (the **AI** screen), plus `~/.local/share/fsmes/agent-turns.jsonl` (one line per turn) and `agent-usage.jsonl` (the bill) | the AI screen, `fsmes ai conversations`, or read the turn log |
| Analysis agent (cloud) — *account `ANALYST`, role `analyst`* | the analysis conversation; off in shadow mode, which says so | every read tool and no write tool: the four shift analyses, plus `trace_rollup`, `trace_graph` and `maintenance_mttr` over this plant's own AI trace, stops and repairs. It counts people by role, workcenter or shift and names one only where a plant has granted `people.analyse` — which no shipped role holds | the same `ai_turns` table, with `brain: analysis`; an answer that named a person also writes `analysis.person_named` to the audit trail against `personnel/<code>` | the AI screen, or `GET /audit?entity_type=personnel` for who was named |
| Instruction drafting | `fsmes draft-instructions` | qwen drafts work instructions from facts the plant holds | documents module, `drafted_by_model` set, **arriving unapproved** | the Instructions screen |
| Embeddings | fleet job completion | nomic-embed-text embeds run summaries for semantic search | **`~/.local/share/fleet/fleet.db`** (the fleet platform — NOT the fsmes store) | `fleet ask` from the laptop |

The cloud agent's reads of the plant leave no record of their own, and are not
meant to: what a read returned is in the turn's transcript, and the turn is in
`ai_turns` and in the turn log below with the tool names it used. So "did the
assistant read the plant's own OEE or work one out for itself" is answered by
`tools` on the turn — `oee_breakdown`, `state_timeline`, `downtime_pareto` and
`tag_trend` are the four that reach the plant's own analyses, and an answer about
a KPI with none of them in the turn is an answer to look at twice. Of the four,
**`oee_breakdown` is the one that carries the coverage ledger**;
`downtime_pareto` carries only how blind its window was (`unknown_seconds`,
`unknown_share`), and `state_timeline` and `tag_trend` carry no coverage figure,
because their routes serve none.

Ollama itself: `127.0.0.1:11434` on `main` (override with `MES_OLLAMA`),
qwen3:8b ≈ 5.6 GB loaded, idle-unloads after a few minutes. `MES_LOCAL_AI=0`
declares a machine with no local model: the Ops screen's Local AI panel is
hidden, and the AI screen's Status tab says the layer is off and why. It does
**not** hide the cloud brain, which is a different model behind a different
setting — the AI tab and `fsmes ai-status` read that from `agent.status()`,
whatever `MES_LOCAL_AI` says, so a plant that runs no local model still sees
what its assistant costs against its cap.

## What the audit found (2026-09-02)

- **Runtime healthy.** qwen loaded and answering (a design-chat question that
  morning), rollup notes for both prior days, timer armed.
- **Bug, fixed same day:** the store wrote `len(findings) or None`, so a run
  triaged *clean* recorded NULL — indistinguishable from a run never triaged.
  Zero-vs-unknown, principle 4's lie pointed the other way. Now 0 means
  clean, NULL means never ran, and a test holds it.
- **Data loss, recovered:** the triage-columns migration on 2026-08-31 kept a
  backup (`runs.db.pre-triage`) but the live store came out with 1 run where
  the backup held 6. The five lost runs were restored at deploy (as
  never-triaged rows, which is the truth). The unmanaged backup file stays
  until Scott deletes it deliberately.
- **A stale claim in the convergence plan:** the results store was described
  as persisting "summary and embedding" per run. It has neither column;
  embeddings exist only in the fleet database. Semantic search over *scored
  fsmes runs* therefore does not exist today — recorded as an open idea, not
  silently assumed.

## The agent's turn log (2026-09-26)

`MES_AGENT_TURN_FILE`, default `~/.local/share/fsmes/agent-turns.jsonl`. One
JSON line per turn — per thing the person got back, not per model call:

```json
{"ts": "2026-09-26T19:55:02+00:00", "plant": "bottling", "session": "ea4a5a9da3c3",
 "user": "ADMIN", "model": "claude-sonnet-5", "kind": "proposals",
 "tools": ["plant_settings", "write_plant_setting"],
 "proposals": [{"id": "956d7f527a29", "tool": "write_plant_setting",
                "args": {"domain": "quality", "key": "nc_code_prefix", "value": "CR"},
                "outcome": "open"}],
 "input": 2000, "output": 100, "cache_read": 1600, "cache_write": 0, "usd": 0.00532}
```

`kind` is one of `reply`, `proposals`, `guide`, `unavailable`, `error`; a
proposal's `outcome` is `open`, `confirmed`, `declined` or `failed`; a failed
turn carries `error` with the exception class. Nothing in the file that is not
already in the transcript the panel shows the person — no message text, no
plant rows, no key.

**The same turn is also written into the plant's own database** (2026-09-26),
as a row in `ai_turns`, and that is what the **AI** screen reads. The two are
the same write: this file is the machine's copy and belongs to whoever
administers the box; the table is the plant's, is behind `audit.read`, carries
the words on both sides as well as the tool names, and is pruned to
`[admin] ai_trace_days`. See
[the AI screen](../operate/ai.md) for what it holds and what it deliberately
does not.

**Why it exists.** On 2026-09-26 Scott had a thirty-turn conversation with the
assistant and nothing on the machine could say how many of those turns reached
the model. `agent-usage.jsonl` is the bill and only records calls that
happened, so a turn answered by a regex, or one that failed before the call,
left no trace at all. (The answer was five.) Read it with
`agent.turn_rows()`, or `jq -r '.kind' agent-turns.jsonl | sort | uniq -c`.

The rule of the house applies to it as much as to the rest: a turn missing
from this file is *unknown*, not *did not happen* — the writer swallows
`OSError` so a full disk loses the line rather than the answer.

## How to act on this from Claude Code

- **"Is the AI layer OK?"** → `fsmes ai-status` (or the Ops panel). Check
  this before blaming the model for anything.
- **UI findings** (`fsmes ui-check`, deterministic, zero GPU) → land as
  `inbox` notes in `docs/design/backlog/` → judge with `/design-triage`.
  A deliberate restyle is accepted with `fsmes ui-check --accept` **in the
  branch that made the change**, committing the baselines with it — never by
  hand-closing the note.
- **Design ideas** → `fsmes design-pending` → `/design-triage`.
- **The nightly note** is the daily digest: run health, triage findings, and
  one UI-conformance paragraph when drift stands unaccepted.
- The rule of the house: **unknown is never zero, and a claim about this
  layer that was not checked on the machine is not a fact.**

## Known follow-ups

- Five modules carry their own `OLLAMA = "http://127.0.0.1:11434"` constant
  (assistant, design, drafting, rollup, triage); fold them onto
  `ai_status.OLLAMA_URL` / `MES_OLLAMA` in one small change.
- Fleet's AI activity could join the panel via a small JSON endpoint on
  fleetd (open question in the plan).
- Embeddings for scored fsmes runs: decide whether wanted; if so, a column
  and a backfill, budgeted under nomic (cheap), not qwen.

## Agent evals (`fsmes agent-eval`)

**What:** asks a headless Claude Code, given *only* the fsmes MCP server
(`--strict-mcp-config`, tools `mcp__fsmes__*`), questions whose right answer
is computed from the plant's API at the moment of asking - which station
holds the most WIP, which machines are alarming, the line's OEE constraint,
which plans are due, which machines have gone quiet. **Trigger:** by hand,
`fsmes agent-eval <plant>`; `--agent none` proves the machinery without a
model. **Output:** `~/.local/share/fsmes/agent-evals.jsonl`, one row per
question with truth, answer, score and pass; `fsmes agent-eval --summary`
prints the pass rate per scenario. **Check it is alive:** the file's mtime.
Not on the nightly timer yet - each run is a paid Claude session.
