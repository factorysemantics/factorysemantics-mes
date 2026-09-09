# The write discipline

*Explanation. Why an agent can run production and record what it sees, but never approve, administer or define.*

The product's MCP tools do not touch the database. Every call goes through
a plant's HTTP API as the `AGENT` account, which holds the `agent` role.
That single design choice gives four properties for free:

1. **The role system constrains the agent exactly as it would a person.**
   Production and recording are granted; approvals, accounts and master
   data are not. A tool that needs more comes back as a readable refusal
   naming the capability, and an administrator grants it per plant,
   deliberately.
2. **Every action lands in the same append-only audit trail**, attributed
   to `AGENT` *for* the person named in `on_behalf_of`. "What did the agent
   do to my plant" is one audit query.
3. **`dry_run=true` on every write** returns exactly the request that would
   be sent and sends nothing — the preview an approval gate wants to show.
4. **`client_ref` makes a write idempotent.** A repeated write with the same
   reference returns its first answer instead of running twice; the API
   enforces it with an `Idempotency-Key` header. An agent that retries
   cannot double-book.

Tools stay thin: argument parsing and one API call. Anything smarter belongs
in the product, where a person's screen gets it too — the dogfood rule.

## What this is not

It is not a sandbox. An agent with the `AGENT` credentials can release
orders and book output on any plant the server reaches. Treat the MCP
server's host and the agent password as plant credentials, because they are.

## See also

- [Connect an MCP client](connect-mcp-client.md)
- [Tools reference](../reference/tools.md)
- Decision record [0003](../decisions/0003-one-mcp-server.md)
