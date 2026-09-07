"""Tool definitions, one module per product module.

One MCP server, tool files per module (decided 2026-09-02): the server in
`fsmes.mcp_server` owns the AGENT session and the `call`/`write` helpers,
and asks each module here to register its tools against them. A module a
plant pack disables takes its tools with it by not being registered.
"""
