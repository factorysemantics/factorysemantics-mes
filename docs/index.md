# FactorySemantics MES

An open-source, modular, **agent-native** Manufacturing Execution System.
Apache-2.0. Pre-alpha, running simulated plants only (as of 2026-09-07).

!!! warning "Simulated"
    Every number on this site comes from a simulated plant unless the page
    says otherwise. The MES has not run a real plant in production yet.
    Pages say what works and what does not.

## Three doors

<div class="grid cards" markdown>

-   **I run a plant**

    ---

    Bring up a simulated line in a minute, then point the same MES at your
    Kepware or OPC UA server with a one-page worksheet. Read OEE that says
    *unknown* when it does not know.

    [:octicons-arrow-right-24: Plant](plant/index.md)

-   **I operate systems**

    ---

    Deploy with Compose or systemd, run several plants from one registry,
    connect ERPNext, and read the settings, CLI and API references generated
    from the code. Standing it up **beside the MES a plant already runs** has
    [its own guide](operate/first-plant.md).

    [:octicons-arrow-right-24: Operate](operate/index.md)

-   **I write code**

    ---

    The architecture, the house rules, the decision records, and how a
    module registers itself through an entry point.

    [:octicons-arrow-right-24: Develop](develop/index.md)

</div>

**Agents** get their own book: [connect an MCP client](agents/connect-mcp-client.md),
the [85 tools](reference/tools.md), and [why every write takes `dry_run`](agents/write-discipline.md).

## Install

```bash
pipx install factorysemantics-mes    # or: uv tool install factorysemantics-mes
fsmes info
fsmes demo
```

`fsmes demo` releases one order against a routing, runs it down a simulated
two-station line over a real OPC UA server, books honest production and
completes the order — in one process, no setup. The
[tutorial](plant/first-line.md) walks through what you are looking at.

## Where to talk

[GitHub Discussions](https://github.com/factorysemantics/factorysemantics-mes/discussions)
for questions and ideas; [issues](https://github.com/factorysemantics/factorysemantics-mes/issues)
for bugs; never real plant data in either. The maintainer answers within days,
not hours — [GOVERNANCE](https://github.com/factorysemantics/factorysemantics-mes/blob/main/GOVERNANCE.md)
says why.
