# Security

*Explanation. The threat model in plain words; the policy is [SECURITY.md](https://github.com/factorysemantics/factorysemantics-mes/blob/main/SECURITY.md).*

- **Where it is meant to run:** on a plant network behind a firewall, with a
  reverse proxy doing TLS. It is not hardened for the public internet.
- **Who can write:** a signed-in user with a role. Agents use the `agent`
  role on behalf of a named person; every action, human or agent, lands in
  the same append-only audit trail.
- **What it writes to machines:** the order code a routing says a machine
  displays, and nothing else by default. PLC write-back is a recommendation
  queue with human approval, off unless a plant turns it on.
- **Tokens:** PBKDF2 password hashing, HMAC-signed bearer tokens, lifetime
  one shift (`MES_TOKEN_TTL_SECONDS`), all invalidated by changing
  `MES_SECRET_KEY`.
- **Lab defaults:** `admin`/`admin`, `operator`/`operator`, the lab agent
  password. The API logs `well-known credentials in use` at start-up while
  any of them is in force. Set them before the instance serves a plant.
- **The MCP servers:** streamable HTTP with DNS-rebinding protection and no
  authentication of their own. Run them next to the MES, never exposed;
  `FSMES_MCP_ALLOWED_HOSTS` names the hostnames clients may use.
- **The public demo:** simulated, public on purpose, reset nightly, running
  lab accounts. It is out of scope for reports and proves nothing about a
  hardened install.

Report a vulnerability privately — the policy says how and what to expect.
