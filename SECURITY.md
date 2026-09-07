# Security

FactorySemantics MES is plant software: it reads machines over OPC UA, holds
production and quality records, and can, when a plant enables it, write
recommendations back toward PLCs. Web-facing plant software is attacked, and
this project treats that as part of being honest rather than as a marketing
line.

## Reporting a vulnerability

- Preferred: GitHub's **private vulnerability reporting** on this repository
  (Security tab → Report a vulnerability).
- Or email **security@factorysemantics.com**.

You will get an acknowledgement within 72 hours. Fixes or mitigations target
30 days for high severity and 90 days for the rest; disclosure is coordinated
with you, and you are credited in the advisory unless you ask not to be.
Please do not open a public issue for a suspected vulnerability.

## Supported versions

While the project is `0.x`, the latest minor release is the only supported
one. A fix ships as a patch release of that minor.

## Scope

In scope: this package (`fsmes`), its connectors, the operator UI, the MCP
servers, and the container image published from this repository.

Out of scope: the public demo plants (they are simulated, public by design,
reset nightly, and run with lab accounts on purpose — see the docs), and
third-party servers you connect the MES to.

## Threat model, in plain words

- The MES is designed to sit on a plant network behind a firewall. It is
  **not hardened for the public internet**; the deployment docs say so.
- Every write requires a signed-in user with a role; agents act through the
  `agent` role on behalf of a named person, and every action — human or
  agent — lands in the same append-only audit trail.
- PLC write-back is off by default. When enabled it is a recommendation queue
  with human approval, not a direct write path.
- Authentication is PBKDF2 password hashing with HMAC-signed bearer tokens.
  Tokens last one shift (`MES_TOKEN_TTL_SECONDS`, default 12 hours) and are
  invalidated by changing `MES_SECRET_KEY`.
- Lab defaults exist so a laptop demo needs no setup (`admin`/`admin`, the
  lab agent password). An installation still running on them is told so in
  its log at start-up. Set them before an instance serves a plant.
- The MCP servers speak streamable HTTP with DNS-rebinding protection and no
  authentication of their own; run them co-located with the MES and never
  expose them directly. OAuth for remote MCP clients is intended and not
  built (see ROADMAP).

## Automated checks

Dependabot alerts and security updates, secret scanning with push
protection, CodeQL, and OpenSSF Scorecard run on this repository. Findings
that matter are fixed in a patch release and noted in the changelog under
**Fixed** with a link to the advisory.
