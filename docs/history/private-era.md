# The private era

*Reference. What happened between 2026-08-28 and 2026-09-07, before the repository was public. The public history starts from one commit ([decision 0007](../decisions/0007-fresh-public-history.md)); this page keeps the narrative.*

## Pull requests

| # | Merged | Title |
|---|---|---|
| 1 | 2026-09-02 | Foundation: a real CI gate, the quality-filter crash, plant migrate, honest docs |
| 2 | 2026-09-02 | WIP at each stage, and whose cost center it sits in |
| 3 | 2026-09-02 | Surfaces, phase 1: object pages, workspaces, the tag surface, and two ratchets |
| 4 | 2026-09-02 | Stale means the machine went quiet, not that a state has not changed |
| 5 | 2026-09-02 | Surfaces, phase 2a: the tag browser, and alarms on the floor |
| 6 | 2026-09-02 | Surfaces, phase 2b: agent identity — the agent role, on_behalf_of, idempotency keys |
| 7 | 2026-09-02 | Surfaces, phase 3: the Maintenance workspace and tools; idempotency deadlock and migrate wait fixed |
| 8 | 2026-09-02 | Surfaces, phase 4: Orders › Schedule — the board, promises, and the calendar |
| 9 | 2026-09-02 | Surfaces, phase 5: SPC and the gauge register, on screen and as tools |
| 10 | 2026-09-03 | Surfaces, phases 6 and 7: trace, master data, and the ratchets close |
| 11 | 2026-09-03 | Agent evals v1: can an agent, given only the tools, answer what the plant knows? |
| 12 | 2026-09-03 | The SAP-shaped ERP contract: typed port, per-operation confirmations, an outbox that can die |
| 13 | 2026-09-03 | OPC suite, phase 3: triggers as data, an action catalog, the evaluator in the agent |
| 14 | 2026-09-03 | OPC suite, phase 4: write-back as a recommendation queue with three guards |
| 15 | 2026-09-03 | OPC suite, phase 5: the certificate of analysis at the end of the line |
| 16 | 2026-09-03 | Scale hardening: retention, the latest-value index, cheap metrics, many agents, the lock split |
| 17 | 2026-09-03 | The washer story: cross-station effects, alarm history, and the causal agent eval |
| 18 | 2026-09-03 | SPC: capability is withheld on screen, not only in words |
| 19 | 2026-09-03 | Environments: the registry is the environment, and prod is promoted by tag |
| 20 | 2026-09-04 | Scale spike: multi-line plants, an asyncua log-spam fix, and a real OPC throughput ceiling at 27 stations |
| 21 | 2026-09-04 | OPC throughput: sample at the server, book in ordered batches, score honestly |
| 22 | 2026-09-04 | The agent behind the floor assistant: MCP tools, confirm cards, teach-and-do |
| 23 | 2026-09-04 | Five more surfaces, a book_output tool, and screen-aware suggestions |
| 24 | 2026-09-07 | The full mega-factory: 108 stations, 302 people, 50 measurement kinds, scored |
| 25 | 2026-09-04 | The assistant follows you across screens; create_order gets a walk |
| 26 | 2026-09-04 | Recorded walkthroughs, phase 1: a document kind the assistant can play |
| 27 | closed | The screens at plant scale: filtered, paged and counted; every crumb a link (its commits reached `main` through #32) |
| 28 | 2026-09-04 | Recorded walkthroughs, phase 2: the recorder |
| 29 | 2026-09-04 | Recorder: one step per control, focus stays where the person is typing, trigger form anchored |
| 30 | 2026-09-04 | deploy: install the agent extra in prod |
| 31 | 2026-09-04 | web: tell the edge that code must revalidate |
| 32 | 2026-09-07 | Serialisation at ten million pieces a day: the cutlery plant |
| 33 | 2026-09-07 | One migration head again |
| 34 | 2026-09-07 | The merge migration passes the linter |

Before #1 (2026-08-28 to 2026-09-01): the bootstrap, and the wholesale
adoption of the maintainer's earlier mini-MES as the engine on 2026-08-31,
which is where M1 to M6 of the roadmap came from.

## Tags

The private repository carried deployment tags — `v0.1.0` (2026-09-03),
`v0.2.0`, `v0.2.1` (2026-09-04), `v0.3.0`, `v0.3.1` (2026-09-07) — used by
`deploy/promote.sh` to move the maintainer's own environments. The package
version stayed `0.1.0.dev0` throughout. The public repository's tags start
again at `v0.1.0`: the first public release is the tree after #34 plus the
publication work, and the two `v0.1.0`s are different trees.

## What was measured

The labs directory holds the experiments: `labs/kepsim` (a Kepware-fed
line), `labs/multiplant` (two plants side by side), `labs/megafactory`
(108 stations), `labs/cutlery` (ten million serialised pieces a day). Each
README states its numbers with hardware, speed and date. None of it is a
real plant.
