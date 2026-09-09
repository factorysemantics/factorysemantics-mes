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
| Floor assistant | the Assistant button | routes questions, quotes procedure, picks guides | answers live; stores nothing (falls back to lexical matching when Ollama is down) | ask it something |
| Instruction drafting | `fsmes draft-instructions` | qwen drafts work instructions from facts the plant holds | documents module, `drafted_by_model` set, **arriving unapproved** | the Instructions screen |
| Embeddings | fleet job completion | nomic-embed-text embeds run summaries for semantic search | **`~/.local/share/fleet/fleet.db`** (the fleet platform — NOT the fsmes store) | `fleet ask` from the laptop |

Ollama itself: `127.0.0.1:11434` on `main` (override with `MES_OLLAMA`),
qwen3:8b ≈ 5.6 GB loaded, idle-unloads after a few minutes. `MES_LOCAL_AI=0`
declares a machine deliberately AI-free and hides the panel.

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
