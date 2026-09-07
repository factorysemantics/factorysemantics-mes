# 0003 — One product MCP server with per-area tool files

- **Status:** accepted
- **Date:** 2026-09-02
- **Deciders:** @kalwei

## Context
"Every function callable via REST and MCP" was half true — the REST half. The architecture sketch had one MCP server per module. The first real agent surface (PR #22, #23) needed to ship in an evening and be operated as one thing.

## Options considered
| Option | For | Against |
|---|---|---|
| One server, one tool file per area | one process, one URL for a client, one audit identity (`AGENT`), tools grouped by file for ownership | a plant cannot expose a subset without configuration (capability filtering does that instead) |
| One server per module | isolation, per-module deployment | N processes and N client entries for a five-person shop; agents lose cross-module questions |

## Decision
The product exposes one MCP server (`fsmes.mcp_server`) whose tools live in `src/fsmes/mcp/<area>.py`. All tools go through each plant's HTTP API as the `AGENT` role; nothing touches the database. The simulation server stays separate because it must never be near a real plant.

## Consequences
Easy: a client connects once and sees the whole plant. Hard: the tool count (85 on 2026-09-07) needs the server's own instructions to steer an agent; capability-filtered tool surfaces per plant are the next step.

## House rules touched
Rule 3 (agent-native): every write takes `dry_run`, `on_behalf_of` and `client_ref` — the write discipline page.
