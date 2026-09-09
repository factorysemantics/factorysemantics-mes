# Connect an MCP client

*How-to. Start the product's MCP server next to a running plant and point a client at it.*

## 1. Have a plant running

`fsmes demo` (one process, port 8000) or `fsmes plant all start` from a
registry. The MCP server talks to plants over their HTTP API, signed in as
the `AGENT` account that `fsmes plant init` creates in the `agent` role.

## 2. Start the server

```bash
pip install "factorysemantics-mes[mcp]"
FSMES_AGENT_PASSWORD=<the agent password> python -m fsmes.mcp_server --host 127.0.0.1 --port 8310
```

It serves streamable HTTP at `http://127.0.0.1:8310/mcp`. It finds the
registry by walking up from the working directory; `FSMES_ROOT` points it
at another checkout, `FSMES_PLANT_REGISTRY` at a registry outside one. If
clients reach it by a hostname other than `localhost`, list that name in
`FSMES_MCP_ALLOWED_HOSTS` (comma-separated) or DNS-rebinding protection
refuses them.

The simulation server is `python -m fsmes.sim.mcp_server --port 8300`.

## 3. Point a client at it

=== "Claude Code"

    ```bash
    claude mcp add --transport http fsmes http://127.0.0.1:8310/mcp
    ```

=== "Any client with a JSON config"

    ```json
    {
      "mcpServers": {
        "fsmes": { "type": "streamable-http", "url": "http://127.0.0.1:8310/mcp" }
      }
    }
    ```

Then: *"list the plants, then show me the open orders on the first one"* —
`list_plants` is where the server's own instructions tell an agent to start.

## 4. Do not expose it

The server has no authentication of its own; the `AGENT` credentials are
its authority on every plant it can reach. Keep it co-located with the MES
and off the internet. Remote clients with OAuth are intended and not built
(2026-09-07).

## See also

- [Tools reference](../reference/tools.md)
- [The write discipline](write-discipline.md)
- [Security](../operate/security.md)
