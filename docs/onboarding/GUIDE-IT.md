# OPC UA access request — for IT / controls

*Hand this page to whoever administers the Kepware (or other OPC UA) server.*

## What is asking for access, and what it does

MES-TWIN is a monitoring application. It opens **one outbound OPC UA session**
to your server, subscribes to roughly **4 tags per machine** (state, two
counters, one process value) at a **500 ms–1 s** sampling interval, and
computes equipment states, downtime and OEE from what it sees.

**It never writes.** Every tag in its configuration is marked read-only, and
with that configuration the software has no code path that writes to a tag. It
does not use the Kepware Configuration API, does not modify the Kepware
project, and needs no anonymous access.

The load is negligible: one client session and a few dozen monitored items on
a server class that routinely handles thousands.

## The five things we need

1. **The OPC UA endpoint URL** — `opc.tcp://<server>:<port>`. Kepware's
   default is port **49320**. (Not the Configuration API ports 57412/57512 —
   different service.)
2. **Confirmation the OPC UA server interface is enabled** — in Kepware:
   Project Properties → OPC UA → Enable. It sometimes is not.
3. **Which security policy to use** — we support the usual Kepware default,
   `Basic256Sha256` with `SignAndEncrypt`. Tell us if your endpoint is
   configured differently.
4. **A username for UA access** — ideally a dedicated, **read-only** account
   (Kepware: OPC UA Configuration Manager → Server Endpoints / User Manager).
   We would rather not use an admin account.
5. **One client certificate trusted** — see below; it is a two-minute,
   one-time step.

Plus, if a firewall sits between the MES machine and the server: **allow TCP
from `<MES machine>` to `<server>:49320`** (or your port).

## The certificate step, plainly

OPC UA encrypts and mutually authenticates. Our client presents a self-signed
certificate; the **first connection attempt is always rejected**, by design,
because your server has never seen it. That first attempt is how the
certificate arrives on your side. Then:

- Open **Kepware OPC UA Configuration Manager → Instance Certificates**
  (rejected certificates also land under
  `ProgramData\...\UA\Server\RejectedCertificates`),
- find the certificate named **MES-TWIN Agent**,
- **Trust** it.

Our second connection attempt then succeeds. There is nothing to renew for ten
years, and you can revoke it at any time, which cleanly cuts our access.

## Rollback

Stop the MES-TWIN service (or just close the session) — nothing persists on
your side except the trusted certificate, which you can untrust. No project
changes, no tags, no users of ours on your server.

## Copy-paste request

> Subject: Read-only OPC UA access to <server> for line monitoring
>
> I am setting up a monitoring application (MES-TWIN) that reads machine
> states and counters over OPC UA to compute downtime and OEE. It is
> read-only — one client session, ~4 tags per machine at 1 Hz, no writes, no
> Configuration API, no project changes.
>
> Could you provide:
> 1. The OPC UA endpoint URL (default opc.tcp://<server>:49320)
> 2. Confirmation the OPC UA server interface is enabled
> 3. The security policy in use (we default to Basic256Sha256 / SignAndEncrypt)
> 4. A read-only user account for UA access
> 5. When my client first connects it will be rejected — could you trust the
>    certificate "MES-TWIN Agent" in the OPC UA Configuration Manager? I will
>    ping you right after the first attempt.
>
> If a firewall is in the path: TCP from <my machine> to <server>:49320.

## Troubleshooting, by symptom

| Symptom | What it actually means |
|---|---|
| `ConnectionResetError` / `10054` immediately | Wrong port — you are talking to the Configuration API (57412) or another service, not the UA endpoint (49320). |
| `BadCertificateUntrusted` / `BadSecurityChecksFailed` | The expected first-connection rejection. Trust the certificate and retry. |
| `BadUserAccessDenied` / `BadIdentityTokenRejected` | Certificate is fine; the username/password is wrong or the account lacks UA access. |
| `BadSecurityPolicyRejected` / `BadSecurityModeRejected` | The endpoint does not offer the policy we asked for — tell us what it does offer. |
| Connects, then all values stop after ~2 hours | Unlicensed Kepware runs in 2-hour demo windows; the runtime service needs a restart (and the server a licence). |
| Endpoint refuses everything, config looks right | Is the OPC UA interface enabled in Project Properties? A project reload (Runtime → Connect) may be needed after changing it. |
