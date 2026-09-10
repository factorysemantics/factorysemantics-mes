# Develop

For the person who changes it.

- [Architecture](../ARCHITECTURE.md) — written before the first line of code
  and kept truthful since; it says where intent and code differ.
- [Your first module](your-first-module.md) — the entry-point mechanism,
  with the ERPNext connector as the worked example.
- [Writing an ERP connector](erp-connectors.md) — the six-method port, the
  conformance suite every connector must pass, and what this project
  requires before it calls one supported.
- [Style](../design/STYLE.md) — the operator UI's rules.
- [Decisions](../decisions/index.md) — why things are the way they are.
- [CONTRIBUTING](https://github.com/factorysemantics/factorysemantics-mes/blob/main/CONTRIBUTING.md)
  — DCO, the six house rules, the provenance rule.

The layout, as of 2026-09-07:

```text
src/fsmes/
  api/           FastAPI app and routers — every capability is an endpoint first
  services/      the rules: execution, quality, OEE, scheduling, agent, …
  domain/        the tables (SQLAlchemy) and calendar arithmetic
  integrations/  opc/ (agent, simulator, worksheet, security), erp/ (contract, adapters)
  mcp/ + mcp_server.py   the product's tools, one file per area
  sim/           line generation, scoring, sweeps, agent evals, UI checks
  web/           the operator UI: plain HTML, CSS and JS, no build step
  migrations/    the Alembic chain — inside the package, so the wheel carries it
  plant.py       registries and the plant supervisor
  cli.py         every command
  schema.py      creating and upgrading a database, from wherever it was installed
labs/            simulated plants and the experiments that measured them
tests/           named as prose
```
