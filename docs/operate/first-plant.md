# Running FactorySemantics MES beside your existing MES

*A guide for plant engineers. One afternoon, in order, with a controls person
and an IT person. No Python required, and nothing here changes anything on
your plant.*

You have an MES already. This one goes **beside** it: it watches the same
machines through the same OPC UA server, computes states, downtime, OEE and
production from what it sees, and writes nothing back. After a week you can
compare the two and decide whether this one is worth more of your time.

If it turns out not to be, you stop the process and untrust one certificate,
and nothing on your plant has changed.

## What you will need before you start

| From whom | What |
|---|---|
| **IT / whoever administers the OPC UA server** | the endpoint URL, confirmation the OPC UA interface is on, the security policy, a **read-only** account, and one certificate trusted. There is [a page to hand them](../onboarding/GUIDE-IT.md). |
| **Engineering / whoever knows the machines** | one CSV row per machine: four tag addresses, what the state values mean, and the rated cycle time. There is [a page to hand them too](../onboarding/GUIDE-ENGINEERING.md), with a [template](../onboarding/worksheet-template.csv). |
| **You** | a PC that can reach the OPC UA server, with Python 3.12 or 3.13 on it. A plant PC or a small VM is plenty. |
| **Your ERP team, only if you want orders** | a folder both systems can reach, and a file per schedule. Step 5. |

Send the two guides out first; they are the long pole. Everything else is an
afternoon.

## The afternoon, in order

| # | Step | Budget |
|---|---|---|
| 1 | [What this guarantees](#1-what-this-guarantees-and-what-it-does-not) | 10 min, reading |
| 2 | [Install it and see it run](#2-install-it-and-see-it-run) | 15 min |
| 3 | [Get read-only access to the OPC UA server](#3-get-read-only-access-to-the-opc-ua-server) | 20 min, plus their change window |
| 4 | [Browse, map, and prove the map](#4-browse-map-and-prove-the-map) | 1–2 hours, and it is the part that matters |
| 5 | [Orders in, confirmations out, by file](#5-orders-in-confirmations-out-by-file) | 30 min, optional |
| 6 | [What people type into other systems](#6-what-people-type-into-other-systems) | 5 min, reading |
| 7 | [First-day checks, and week one](#7-first-day-checks-and-week-one) | 20 min, then a week |
| 8 | [Where the numbers live, and backing them up](#8-where-the-numbers-live-and-backing-them-up) | 20 min |

Those are budgets to plan an afternoon around, not measurements. Step 4 is
the one that decides whether any number this MES ever reports is worth
reading; the rest is plumbing.

---

## 1. What this guarantees, and what it does not

**It does not write to your PLCs.** A tag map generated from the worksheet
marks every machine read-only, and with no write target configured there is
no code path that writes a tag. Ask for a read-only OPC account as well —
then it is your server enforcing it, not this software's good intentions.

**It does not change your OPC server's project.** No configuration API, no
tags added, no users of ours. The only thing that persists on their side is
one trusted certificate, and untrusting it cuts the access cleanly.

**It does not talk to your ERP unless you tell it to.** `MES_ERP_MODE=off`
reaches nothing; `MES_ERP_MODE=file` reads and writes folders you name and
opens no connection to any live system. Step 5 sets that up deliberately.

**It does not publish to a broker unless you tell it to.** `MES_UNS_MODE` is
`off` by default.

As of 2026-09-10 those are four separate settings, each checked separately.
Check all four before you point this at a real line, and check them again
after anyone edits `.env`.

**What it does not guarantee:** it still needs to *read* your OPC server, so
that server must permit a read-only client and will carry one more session.
And it is pre-alpha software that has never run a real plant in production —
which is why it runs beside yours and not instead of it.

## 2. Install it and see it run

Nothing plant-specific yet. Prove the software works on the machine before
you point it at anything real.

```bash
pipx install factorysemantics-mes     # or: uv tool install factorysemantics-mes
fsmes info
fsmes demo
```

`fsmes demo` runs a whole simulated line in one process for about ninety
seconds — two stations over a real OPC UA server, one order released, booked
and completed — and prints a verdict at the end.

**Done looks like:** `Demo result: full loop closed - order booked, ERP
confirmed, OEE reported.` If that line does not appear, stop here; nothing
later will work either.

**Database:** SQLite is the default and needs no setup — one file, and it is
fine for one line. Use PostgreSQL when you have more than one plant on the
box, or when your IT department wants the backups in their existing regime:
set `MES_DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname`
and install with the `postgres` extra. Both are tested; see
[compatibility](compatibility.md).

Everything the MES is configured with is an environment variable starting
`MES_`, and it reads a `.env` file in the directory you run it from. There
is no other config format to learn.

## 3. Get read-only access to the OPC UA server

Send [the access request page](../onboarding/GUIDE-IT.md) to whoever
administers the server. It is written to be forwarded as-is and asks for
five things.

Put their answers in `.env`:

```bash
MES_OPC_ENDPOINT=opc.tcp://their-server:49320
MES_OPC_SECURITY=Basic256Sha256,SignAndEncrypt
MES_OPC_USER=the-read-only-account
MES_OPC_PASSWORD=...
MES_TAG_MAP_FILE=config/tag_map_plant.json
```

**Expect the first connection to be rejected.** That is the certificate
handshake working: their server has never seen your client's certificate, so
it refuses it and files it under rejected certificates — which is how the
certificate reaches their side. They trust it once; the second attempt
succeeds.

**Done looks like:** `fsmes opc-browse --depth 1` prints the server's
namespace table instead of a certificate error.

The details, the failure symptoms and what the subscription costs their
server are on [Connecting read-only to an OPC UA server you do not
own](opc-readonly.md).

## 4. Browse, map, and prove the map

Look at what the server actually exposes:

```bash
fsmes opc-browse --contains Filler
```

Turn Engineering's worksheet into the tag map:

```bash
fsmes make-tag-map worksheet.csv --out config/tag_map_plant.json
```

It refuses anything it could not honestly interpret, and prints a note for
every blank that has a consequence — no scrap counter means quality reads
*unknown*, no rated cycle time means performance reads *unknown*. It never
fills a blank with a default that looks like a measurement.

Prove it against the live server, **while the line is running**:

```bash
fsmes opc-verify --seconds 15
```

**Done looks like:** `PASS: every mapped tag is readable and every observed
State value maps.`

Then give the MES its equipment and start it:

```bash
fsmes init-db
fsmes seed-line LINE1 --line-name "Filling Line 1"
fsmes run-opc-agent      # subscribes and records
fsmes run-api            # dashboards at http://127.0.0.1:8000/dashboard
```

**The decision that matters** is in the worksheet, not in any command: which
state values count as `down`. A changeover mapped to `down` wrecks every
availability figure this system will ever report, and nobody will be able to
say why afterwards. Sit with the person who runs the line for that column.

Full detail: [Connecting read-only to an OPC UA server you do not
own](opc-readonly.md).

## 5. Orders in, confirmations out, by file

Optional, and worth doing: without orders the MES reports states, downtime
and trends but books no production against anything.

The simplest interface, and the one that needs no access to your ERP, is a
folder. Set:

```bash
MES_ERP_MODE=file
MES_ERP_INBOX=erp_exchange/inbox
MES_ERP_OUTBOX=erp_exchange/outbox
MES_ERP_ARCHIVE=erp_exchange/archive
```

```bash
fsmes erp requirements    # what this connector needs — the list to send your ERP team
fsmes erp setup           # makes the folders, idempotent
fsmes erp check           # says whether it would work, in plain language
fsmes run-erp-sync        # reads the inbox, writes the outbox, forever
```

**Orders in.** Drop a file in the inbox — either B2MML-lite
`ProductionSchedule` XML, or plain JSON, one order or a list:

```json
[{"code": "WO-10041", "material": "FG-LINE1", "quantity": 1200,
  "due_date": "2026-09-12T06:00:00", "priority": 20}]
```

Read files move to the archive. A file nobody could parse keeps its name with
`.rejected` on the end and stays where you can see it, rather than
disappearing.

**Confirmations out.** One `ProductionPerformance` XML per confirmation lands
in the outbox, named for the order:

```text
erp_exchange/outbox/confirmation_WO-10041_op10_20260910-141233.xml
erp_exchange/outbox/confirmation_WO-10041_completion_20260910-152551.xml
```

One per operation as it completes, and one for the order. Your ERP team
collects them; nothing here deletes them.

**Done looks like:** `fsmes erp check` says `Ready.`, an order file you drop
appears on the dashboard, and a confirmation file appears in the outbox when
the line makes something.

There is also a [live ERPNext connector](erpnext.md) if that is your ERP —
but for a first shadow run beside a system already in charge, files are the
right choice: two systems confirming into one ERP is a problem you do not
need this week.

## 6. What people type into other systems

Downtime reasons, scrap tickets, manual counts, changeover notes — the
things people type into a terminal, a spreadsheet or the incumbent MES.

**There is no inbound path for those yet.** The only thing this MES ingests
from outside is the order feed in step 5. That is why the downtime pareto
reads almost entirely *unlabelled* in week one: nothing on an OPC-fed line
labels a stop, and there is nowhere yet to tell it. The pareto names that bar
*unlabelled* rather than filing it under "other", because a chart that hides
its own ignorance convinces a plant it has data it does not have.

If you need that, say so in
[Discussions](https://github.com/factorysemantics/factorysemantics-mes/discussions).
A question asked twice becomes a page, and a need stated by a real plant
moves up the list.

## 7. First-day checks, and week one

On the first day, in this order:

```bash
fsmes info          # the version you think you are running
fsmes opc-verify    # every mapped tag still readable
fsmes erp check     # only if you did step 5
```

Then open `/dashboard` and look at the line, `/dashboard/analysis` for the
shift, and `/dashboard/line` for one line's machines, its work in progress
and its state timeline, with the 3D view as a tab.

**Three things will look wrong on day one and are correct:** OEE says
*unknown* rather than a number, the downtime pareto is nearly all
*unlabelled*, and a machine with no scrap counter shows no quality figure.
[What to expect in the first week](first-week.md) explains each, lists the
four mistakes that really are yours to fix, and — the useful part — says what
is worth comparing against the system already in charge and what is not.

Run the agent under a supervisor from the first day. The window it was not
watching is reported as *unknown* and is never reconstructed, by design;
[deploy](deploy.md) has the systemd units and the Compose file.

At the end of the week, if you did step 5, put the two records side by side.
Export the same period's bookings from the MES in charge and run
[the shadow scorecard](shadow-scorecard.md): it reports, per order and per
operation, where the two agree and where they do not, and — the part that
makes it worth trusting — what it could not compare.

## 8. Where the numbers live, and backing them up

Everything the MES recorded is in one database — `fsmes.db` beside where you
run it, unless you pointed `MES_DATABASE_URL` somewhere else. Everything it
was told is in `.env`, the tag map, and the certificate directory.

```bash
fsmes backup --out /mnt/somewhere-else
```

That copies the database, the tag map and the OPC client certificate, states
its totals, and names what it did not copy and why. It is safe to run while
the plant is running. **The certificate is the one worth caring about**:
losing it means going back to the server's owner for another trust step and
another change window.

It deliberately does not copy `.env`, which holds your OPC password — keep
that wherever this plant already keeps secrets.

Read [backup and restore](backup.md) for the schedule, and for how to prove
a backup actually restores, which is the only test that counts.

**If you put this on the plant network** rather than one PC, put TLS in
front of it: the MES does not terminate TLS itself, and
[TLS in front of the API](tls.md) has a Caddy config and an nginx config,
one each.

**Upgrading later** is [its own page](upgrade.md), including the one case —
a PyPI install upgrading across a schema change — that this project cannot
migrate for you yet, written down rather than papered over.

## Who to ask

[GitHub Discussions](https://github.com/factorysemantics/factorysemantics-mes/discussions)
for questions and ideas,
[issues](https://github.com/factorysemantics/factorysemantics-mes/issues) for
bugs. One maintainer, answering in days rather than hours; that is
[stated up front](https://github.com/factorysemantics/factorysemantics-mes/blob/main/GOVERNANCE.md)
rather than discovered.

**Never put real plant data in either.** No tag names from your site, no
screenshots of your dashboards, no order numbers, no company or site names.
Say what happened and what you expected; that is enough to help you, and it
keeps the project usable by people whose employers would not let them
otherwise.

## Every page this guide leans on

- [Connecting read-only to an OPC UA server you do not own](opc-readonly.md)
- [IT access request](../onboarding/GUIDE-IT.md) · [Engineering worksheet](../onboarding/GUIDE-ENGINEERING.md)
- [What to expect in the first week](first-week.md)
- [The shadow scorecard](shadow-scorecard.md)
- [Backup and restore](backup.md) · [TLS in front of the API](tls.md) · [Upgrading](upgrade.md)
- [Deploy](deploy.md) · [Security](security.md) · [Compatibility](compatibility.md)
- [Reading OEE](../plant/reading-oee.md) · [Never invent production](../plant/never-invent-production.md)
