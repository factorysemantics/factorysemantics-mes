# Connecting read-only to an OPC UA server you do not own

*How-to. Commissioning against a server that is already serving another
system, without disturbing it.*

At a real plant the OPC UA server exists, it is already feeding the system
in charge, and somebody else administers it. Your job is to become one more
read-only client on it and then prove your tag map is right. That is five
things to ask for, four commands, and one certificate step that always fails
the first time on purpose.

Nothing on this page writes to that server.

## What to ask the server's owner for

Send them the [OPC UA access request](../onboarding/GUIDE-IT.md), which is
written to be handed over as-is. It asks for five things:

1. **The endpoint URL** — `opc.tcp://<host>:<port>`. Not the vendor's
   configuration or administration port, which is usually a different
   service on a different port and will answer with a connection reset.
2. **Confirmation the OPC UA interface is enabled.** On some servers it
   ships off.
3. **The security policy the endpoint offers** — commonly
   `Basic256Sha256` with `SignAndEncrypt`. If theirs is different, they have
   to tell you, because the client asks for exactly what it is configured to
   ask for.
4. **A dedicated account with Browse and Read only.** Ask for read-only in
   writing. It is the cheapest guarantee either side gets, it is enforced by
   their server rather than by your good intentions, and it makes the rest of
   the conversation short.
5. **One client certificate trusted** — the step below.

Plus, if a firewall is in the path: TCP from the MES machine to the
endpoint's host and port.

### What the subscription costs their server

Say this before they ask. One client session. Roughly **four monitored items
per machine** — a state tag, two counters and one process value — at a
publishing interval of **500 ms** (`MES_OPC_PUBLISH_MS`, and 1000 ms is fine
if they would rather). No configuration API, no project changes, no browsing
after commissioning. That is a rounding error on a server class that carries
thousands of items, and saying the number is more convincing than saying
"negligible".

### What this MES will not do to their server

Worth putting in the same email, because it is what they are actually
worried about:

- **It does not write.** A tag map generated from the worksheet marks every
  machine read-only (`order_tag: null`), and with no write target configured
  there is no code path that writes to a tag.
- **It does not touch the project.** No configuration API, no tags added, no
  users of ours on their server.
- **Revoking is one click.** Untrust the certificate and the session stops.
  Nothing of ours persists on their side except that trust.

Two other outbound paths exist in this MES and are separate settings you
should check before pointing it at a real plant: `MES_ERP_MODE` decides
whether anything is posted to an ERP (`off` and `file` reach no live system),
and `MES_UNS_MODE` is `off` by default so no MQTT broker is contacted. They
are not part of the OPC conversation, but they are part of the same promise.

## Set the connection up

In `.env`:

```bash
MES_OPC_ENDPOINT=opc.tcp://their-server:49320
MES_OPC_SECURITY=Basic256Sha256,SignAndEncrypt
MES_OPC_USER=the-read-only-account
MES_OPC_PASSWORD=...
MES_TAG_MAP_FILE=config/tag_map_plant.json
```

`MES_OPC_SECURITY` empty means anonymous and unencrypted, which is what the
bundled simulator uses and what no real server offers. Setting it turns on
the certificate handshake.

### The certificate step

The client mints its own self-signed certificate the first time it needs
one, into `MES_OPC_CERT_DIR` (default `certs/`):

```text
certs/mes_twin_client.der
certs/mes_twin_client_key.pem
```

It is valid for ten years, its subject common name is **`MES-TWIN Agent`**,
and its subject-alternative-name URI matches `MES_OPC_APPLICATION_URI`
(`urn:mes-twin:agent` by default) — a UA server rejects a certificate whose
SAN URI disagrees with the application URI the client advertises, so change
one and you must change the other.

**The first connection is rejected. That is the protocol working.** Their
server has never seen this certificate, so it refuses it and files it under
rejected certificates; that refusal is how the certificate gets to their
side. They then trust it, and the second attempt succeeds. Run a browse or a
verify once, tell them it has arrived, and wait.

If you see `BadCertificateUntrusted` or `BadSecurityChecksFailed`, that is
this and nothing else — the CLI says so rather than printing a bare status
code. The other symptoms are in the
[access request's troubleshooting table](../onboarding/GUIDE-IT.md).

!!! warning "Back the certificate up"
    Losing `certs/` means minting a new certificate, which means going back
    to the server's owner for another trust step and another change window.
    [`fsmes backup`](backup.md) copies it. This is the single most annoying
    thing to lose on a plant PC.

## Look at what the server actually exposes

```bash
fsmes opc-browse --contains Filler
fsmes opc-browse --depth 4
```

It walks the address space under `Objects/` and prints the browse name, the
current value and the full node id of everything it finds, plus the server's
namespace table. `--contains` filters by substring, which is what makes it
usable on a real server with thousands of nodes; `--depth` defaults to 3.

This is where worksheet typos surface: the node id you were given and the
node id the server has differ by a character, or by a namespace index, or the
tag was auto-generated with a sanitised name that is not the display name.
Exit code 2 means it could not connect at all — that is the certificate or
the credentials, not the tag map.

Browse once, at commissioning. Nothing browses afterwards.

## Draft the tag map from the worksheet

The tag map is not written by hand. It comes from a one-page CSV that
whoever knows the machines fills in — one row per machine, four node ids and
two facts. The columns, and what a blank in each of them costs you, are in
the [Engineering worksheet guide](../onboarding/GUIDE-ENGINEERING.md);
the template is
[worksheet-template.csv](../onboarding/worksheet-template.csv) and a filled
example is
[worksheet-example.csv](../onboarding/worksheet-example.csv).

```bash
fsmes make-tag-map worksheet.csv --out config/tag_map_plant.json
```

It refuses anything it could not honestly interpret and exits 1 saying what.
For every blank that has a consequence it prints a note rather than a
default — no scrap counter means quality reads *unknown*, no cycle time
means performance reads *unknown*. That is the point: a guessed cycle time
turns performance into fiction quietly, and a blank turns it into *unknown*
loudly.

The decision that matters most in that file is which state values count as
`down`. A changeover mapped to `down` wrecks every availability figure the
system will ever report, and nobody will be able to say why. Spend the time
there.

## Prove the map before trusting it

```bash
fsmes opc-verify --seconds 15
```

It resolves every tag in the map through the agent's own resolution code —
the same code that will run in production, so a map that verifies is a map
the agent can read — then reads everything twice, `--seconds` apart, and
reports per machine and per tag:

- **readable?** the tag resolved and returned a value;
- **changing?** the value moved between the two reads;
- **do the State values map?** every observed raw state value is in the
  worksheet's `state_map`.

```text
PASS: every mapped tag is readable and every observed State value maps.
```

Exit codes: **0** pass, **1** at least one tag unreadable or a State value
with no mapping — the agent would misbehave with this map — and **2** could
not connect.

Run it **while the line is running**. A stopped line makes every counter
look static and every state look correct, and "changing?" is the column that
catches a counter pointed at the wrong machine.

Re-run it whenever the worksheet changes. It is the regression test for the
one file that decides what every number means.

## Then give the MES its equipment

```bash
fsmes init-db
fsmes seed-line LINE1 --line-name "Filling Line 1" --site SITE1 --site-name "Main Site"
```

Stations, names and cycle times come out of the same tag map, so the
equipment model and the subscription cannot disagree. `--no-routing`
observes only: states, downtime and trends, with no production booking.

## What runs afterwards

```bash
fsmes run-opc-agent     # subscribes and records
fsmes run-api           # dashboards at /dashboard
```

One session, held open. If it drops, the window it was not watching is
reported as *unknown* and is not reconstructed later — so run it under a
supervisor from day one ([deploy](deploy.md)).

## One MES, several OPC servers

A plant whose line spans two servers sets `MES_OPC_ENDPOINTS` to a JSON list
instead of the single endpoint:

```bash
MES_OPC_ENDPOINTS='[{"endpoint":"opc.tcp://a:49320","tag_map":"config/tag_map_a.json"},
                    {"endpoint":"opc.tcp://b:49320","tag_map":"config/tag_map_b.json"}]'
```

One agent process, one subscription per entry, each with its own tag map.
Each server's owner needs the same five things and the same certificate
step.

## Which servers this has been tested against

Read [compatibility](compatibility.md) before you promise anybody anything.
As of 2026-09-10 the only third-party server tested is KEPServerEX, in a
lab, with a trial licence; everything else is untested, and untested is
stated as a fact rather than as a warning label. The client is `asyncua`,
and the tag map is the only server-specific part — which is a reason to
expect it to work, not evidence that it does.

## See also

- [IT access request](../onboarding/GUIDE-IT.md) — the page to hand over
- [Engineering worksheet](../onboarding/GUIDE-ENGINEERING.md) — the page to hand over
- [Backup and restore](backup.md) — including the certificate
- [What to expect in the first week](first-week.md)
