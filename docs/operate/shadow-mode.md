# Running beside an existing MES

*How-to. For the engineer putting this MES next to a line that another
system is already running.*

The safest way to find out whether an MES is any good is to let it watch a
real plant for a fortnight and then compare what it says against what the
plant actually did. The unsafe part is everything else it might do while it
is watching. Two systems writing setpoints to the same machine, or two
systems confirming the same production to the same ERP, is a worse problem
than the one you were trying to solve.

**Shadow mode is one setting that closes all of it.**

```bash
MES_SHADOW=true
```

Set it, restart, and this MES reads the plant's OPC UA tags and books
production exactly as it would if it were in charge — orders, counts,
states, downtime, OEE, quality, genealogy, the audit trail, all of it — and
cannot act. No setpoint reaches a machine. No live ERP is contacted.
Nothing is published to a broker. Nothing about this plant leaves the box.

## What it guarantees

Every path in the package by which this process can reach past its own
database is listed in one place, `fsmes/shadow.py`, with what shadow mode
does to each. You can read that list from a running installation:

```bash
curl -s http://localhost:8000/shadow | jq
```

Two layers hold it shut.

**At start-up.** A setting that would open a live connection is refused
before anything starts, so a live adapter is never constructed at all:

| Setting | In shadow mode |
|---|---|
| `MES_ERP_MODE` | only `off` or `file`. `rest`, `erpnext` and any installed connector module refuse to start |
| `MES_UNS_MODE` | only `off` or `log`. `mqtt` refuses to start |

If you never set one of these, shadow mode leaves it off rather than
refusing: `MES_ERP_MODE` defaults to `rest` so that a laptop runs with no
setup, and a default is not a decision. A mode you *did* set and that shadow
mode cannot honour is refused out loud, in one sentence, naming the
variable. `/shadow` reports what is actually in force.

**At the call site.** The paths that settings alone cannot settle refuse
where the work happens, and log the reason:

- **Setpoints.** An engineer can still see a recommendation, read its
  evidence and approve it. The OPC agent will not dispatch it. Approved
  recommendations stay approved and unwritten — they are not marked failed,
  because nothing was attempted and a queue full of failures that never
  happened is a lie about your plant.
- **Order codes.** Normally the agent writes each machine's next dispatched
  order into its `OrderCode` tag. That write-back was never approval-gated —
  a machine gets an order node by default — so it is the path shadow mode
  most needed to close. It writes nothing and says so once in the log.
- **The ERP file inbox.** The file adapter normally renames a consumed file
  into the archive folder, which is how it acknowledges. Point that inbox at
  a folder the plant's own MES is reading and that rename takes its orders
  away. In shadow mode nothing moves: each file is read and left exactly
  where it was.
- **The broker.** `MqttTransport` refuses to connect or publish even if
  something builds one directly.
- **The cloud model.** The optional cloud brain changes nothing in your
  plant, but it carries your plant's numbers off the box. In shadow mode it
  is refused and says why; the local model on the same machine still
  answers.
- **`fsmes demo`.** It runs a simulated line and a mock ERP in the same
  process, around every other gate. It refuses.

## What it does not guarantee

Shadow mode changes what leaves the MES. It never changes what the MES
records — the whole point is that the numbers are the ones it would have
produced in charge.

It is **not** a read-only OPC client in the protocol sense. It still opens
an OPC UA session and subscribes, so the plant's server has to permit a
client that reads. Give it its own account with browse and read on the tags
in your tag map and nothing else; that account is your outer guarantee, and
shadow mode is the inner one.

It still needs an order feed. It cannot take one from a live ERP, so use
[the file adapter](#the-file-handoff).

It still writes files, on purpose. A file changes nothing until something
imports it, so:

## The file handoff

With `MES_ERP_MODE=file`, orders arrive as XML or JSON in an inbox folder
and one confirmation XML per operation and per order lands in an outbox
folder. That is the shape of a shadow run: your ERP's schedule goes in, this
MES's confirmations come out, and somebody compares them against what the
incumbent confirmed for the same shift.

```bash
MES_SHADOW=true
MES_ERP_MODE=file
MES_ERP_INBOX=/srv/fsmes/erp/inbox
MES_ERP_OUTBOX=/srv/fsmes/erp/outbox
```

> Give both folders to this MES alone. The inbox should be a copy of the
> schedule, dropped there by whatever already drops it for the incumbent —
> shadow mode will not move or delete anything in it, but a folder two
> systems share is a folder two systems will eventually disagree about. The
> outbox must be one **no ERP is watching**: writing a confirmation where an
> ERP's watcher picks it up is a live ERP connection with extra steps.
>
> Config, not code, at the plant boundary. The MES will not guess which
> folder is safe.

To see the plant's namespace without a broker, set `MES_UNS_MODE=log`: every
topic and payload is built and written to the log, and nothing is published.

## How you can tell it is on

It is on every screen, at the top, above the header, on a hatched bar that
cannot be dismissed. Deliberately not a state colour — running, idle and
down mean machines, and this is not a machine.

It is also in:

- `fsmes info` — in words, with the count of closed paths.
- `GET /health` — `{"status": "ok", "shadow": true}`. Health is the endpoint
  everything already asks; a monitor that knows a plant is up and does not
  know it is only watching will read its silence as everything being fine.
- `GET /shadow` — the whole register. Public, like health: the person who
  most needs it is deciding whether it is safe to point this at a running
  plant, and does not have an account yet.
- The MCP server's description, and `list_plants()`, which reports it per
  plant from that plant's own health. One server can operate several plants
  and they need not agree; a plant that did not answer reports `null`, which
  is not the same as "not shadow".
- The audit trail: `shadow.on` and `shadow.off`, written by the API at
  start-up when it differs from the last thing recorded.

## Turning it off

Change the setting and restart. There is no runtime switch, on purpose: what
an MES is allowed to do to a plant must not be able to change halfway
through a shift.

```bash
# in the unit file, the compose environment, or .env
MES_SHADOW=false
```

The next start-up writes `shadow.off` to the audit trail with the time. Who
decided is in whatever change record covers the setting; when it took effect
is here.

Before you do it, the questions worth having answered: does the incumbent
still write the setpoints, or does this one now? Which system confirms to
the ERP, and what happens to the other one's outbox? Who is on the floor the
first shift? None of that is a software setting.

## Related

- [The shadow scorecard](shadow-scorecard.md) — the comparison at the end of
  the run, which is the reason for doing it.
- [The unified namespace (MQTT)](uns.md) — `log` mode, which is what a
  shadow plant runs.
- [The ERPNext connector](erpnext.md) — a live connector, which shadow mode
  refuses.
- [Security](security.md) — the threat model, and what not to expose.
- [Settings](../reference/settings.md) — every variable, generated.
