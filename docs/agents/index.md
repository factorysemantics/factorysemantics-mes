# Agents

Every capability of the MES is an API and a tool first, a screen second.
This book is for the person — or the agent — connecting a model to a plant.

- [Connect an MCP client](connect-mcp-client.md) — how-to
- [Tools](../reference/tools.md) — reference, generated from the servers
- [The write discipline](write-discipline.md) — why every write takes
  `dry_run`, `on_behalf_of` and `client_ref`, and why the agent cannot
  approve anything
- [Evals](evals.md) — "can an agent, given only the tools, answer what the
  plant knows?" as a trend

Two servers exist. The **product** server operates the MES the way a person
does. The **simulation** server drives and scores fake plants and has no
business near a real one.
