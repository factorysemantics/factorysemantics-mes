# 0015 — The MCP server is registered as `com.factorysemantics/mes`

- **Status:** accepted
- **Date:** 2026-09-06
- **Deciders:** @kalwei

## Context
The official MCP registry has been the only official listing since the curated server list was retired on 2026-04-14. Its API has been frozen at v0.1 since 2025-10-24. Names there are reverse-DNS and verified against their namespace: the GitHub form is proved by logging in, the domain form by one DNS TXT record. A bare `factorysemantics/mes` is not a name the registry accepts.

## Options considered
| Option | For | Against |
|---|---|---|
| `com.factorysemantics/mes` | matches the product and the documentation domain; survives a GitHub rename; the DNS record is a two-minute edit the publisher CLI prints for you | needs control of the domain, and a namespace change later would be a new listing |
| `io.github.factorysemantics/mes` | proved by a GitHub login, nothing else to set up | ties the name to an account host; a rename of the organisation orphans it |
| Do not list | nothing to do | the registry is where a developer searching "manufacturing", "OPC UA" or "MES" looks first, and sub-registries pull from it |

## Decision
One listing, `com.factorysemantics/mes`, verified through the domain, for the product's own MCP server. The simulation server is not listed separately — it exists to test the product and must never be near a real plant (0003); the description mentions it and nothing more. The listing is published after the package exists on the package index, because the registry entry names the package. The description leads with what is unusual rather than with the tool count: reads are free, writes are idempotent and take `dry_run`, every action carries the human it acts on behalf of, and the tool surface is filtered by what the account may do.

## Consequences
Easy: one listing feeds every client store that pulls from the registry. Hard: the name is now a thing that must stay true — the domain, the organisation and the namespace are one string, so renaming any of them is a decision, not a rename.

## House rules touched
Rule 3 in spirit: the listing advertises the write discipline rather than the tool count, because the discipline is what makes the tools safe to hand an agent.
