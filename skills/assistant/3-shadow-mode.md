# Stage 3 — Shadow mode, beside the MES they already have

**Done with this stage when** `fsmes shadow scorecard` has printed an
agreement table from their incumbent's export and this MES's own
confirmations, and they have an HTML file they can mail around.

Shadow mode means: this MES reads their real line, books production in its own
database, and **cannot write a setpoint, reach a live ERP, publish to a
broker, or send anything about that plant off the box**. It runs beside their
system, not instead of it. Nobody has yet run it on a real plant; say so
before they start.

## What you need before you start

This is the stage with other people in it. Two requests go out first and they
are the long pole — days, not minutes.

**From IT, or whoever administers the OPC UA server** — five things: the
endpoint URL; confirmation that the OPC UA interface is switched on; the
security policy; a **read-only account** with browse and read on the tags in
the tag map and nothing else; and one certificate trusted. The project ships a
page written to be forwarded as-is: `docs/onboarding/GUIDE-IT.md`.

Be honest about the limit when they ask: this is **not** a read-only OPC client
in the protocol sense. It opens an OPC UA session and subscribes. The read-only
account is the outer guarantee; shadow mode is the inner one.

**From Engineering** — one CSV row per machine: four tag addresses, what the
state values mean, and the rated cycle time. `docs/onboarding/GUIDE-ENGINEERING.md`
and `docs/onboarding/worksheet-template.csv` in the repository are for
forwarding too.

**And one decision, which matters more than any command on this page:** which
state values count as `down`. A changeover mapped to `down` wrecks every
availability figure this system will ever report, and nobody will be able to
say why afterwards. Make them decide it out loud, and write down what they
decided.

## The commands, in order, with what to paste back

Everything this MES is configured with is an environment variable starting
`MES_`, read from a `.env` file in the directory they run it from. There is no
other config format.

### 1. Turn shadow mode on, and prove it is on

In `.env`: `MES_SHADOW=true`. Then:

```
fsmes info
```

Paste it. You are looking for a line of the shape
`shadow mode   ON — n of m outbound paths closed`, followed by the banner. If
it says `shadow mode   off`, the setting did not take — wrong directory,
wrong file, or the process was not restarted. There is no runtime switch, on
purpose: change the setting and restart.

Two more ways to be sure, and use them, because this is the one thing worth
being sure about: every screen carries a hatched bar at the top that cannot be
dismissed, and `GET /health` answers `{"status": "ok", "shadow": true}`.
`GET /shadow` returns the whole register of what is closed.

**Shadow mode refuses to start on a live setting rather than quietly
overriding it.** If `MES_ERP_MODE` is `rest` or `erpnext`, or `MES_UNS_MODE`
is `mqtt`, it will not start and will say which setting and why. In shadow
mode `MES_ERP_MODE` may only be `off` or `file`, and `MES_UNS_MODE` only `off`
or `log`. Fix the setting; do not unset `MES_SHADOW`.

### 2. Find the tags, build the tag map, prove every tag reads

```
fsmes opc-browse --depth 1
fsmes opc-browse --contains Filler
fsmes make-tag-map worksheet.csv --out config/tag_map_plant.json
fsmes opc-verify --seconds 15
```

Run `opc-verify` **while the line is actually running**, or the state values it
sees are the ones a stopped machine shows. What you want back:

```text
PASS: every mapped tag is readable and every observed State value maps.
```

### 3. Bring it up

```
fsmes init-db
fsmes seed-line LINE1 --line-name "Filling Line 1"
fsmes run-opc-agent
fsmes run-api
```

`run-opc-agent` subscribes and records; `run-api` serves the dashboards on
`http://127.0.0.1:8000/dashboard`. Both run forever — two windows, or a
service. Put the agent under something that restarts it from the first day:
the window it was not watching is reported as unknown and is never
reconstructed, by design.

### 4. Orders in by file, confirmations out by file

In `.env`: `MES_ERP_MODE=file`, and `MES_ERP_INBOX`, `MES_ERP_OUTBOX`,
`MES_ERP_ARCHIVE` as absolute paths. Then:

```
fsmes erp check
fsmes run-erp-sync
```

`erp check` should end `Ready.` — or `Ready, as far as anything above was
checked.`, which means something could not be verified and the line above says
what.

Two rules here, and they are the difference between shadow mode and a live
integration:

- The inbox must be a **copy** of the schedule, not the folder their ERP
  writes to.
- The outbox must be one **no ERP is watching**. Writing a confirmation where
  an ERP's watcher picks it up is a live ERP connection with extra steps.

In shadow mode nothing moves: each inbox file is read and left exactly where
it was. Orders are either B2MML-lite `ProductionSchedule` XML or plain JSON —
one order or a list.

### 5. The scorecard

Ask their incumbent system for an export of what *it* booked over the same
window, as CSV, with a column per row for: `order` (required — it is the row's
identity), `operation`, `equipment`, `good_qty`, `scrap_qty`, `started_at`,
`completed_at`. A blank cell is **unknown**, never zero.

```
fsmes shadow scorecard --incumbent bookings-week-37.csv --map our-mapping.json --ours /var/lib/fsmes/erp/outbox --html scorecard.html --json scorecard.json
```

Smallest version that works:

```
fsmes shadow scorecard --incumbent theirs.csv --ours /var/lib/fsmes/erp/outbox
```

Leave `--ours` out and it reads this MES's own confirmations from its
database, which still holds every one after the folder was emptied. It
contacts nothing and changes nothing. `--html` writes one self-contained page
— no internet, no scripts, safe to mail around.

## What the output means

**`NOT OK  <path> does not exist.`** and the other `NOT OK` lines are the
scorecard refusing to guess. Read the path back to them.

**"Not one order code appears in both records, so nothing was compared."**
Their export's codes and this MES's are different sets of strings. Map them in
the mapping file's `orders` section. This is the most common first result and
it is not a fault in either system.

**The first OPC connection is rejected, and that is correct.**
`BadCertificateUntrusted` or `BadSecurityChecksFailed` is the certificate
handshake working: IT trusts the certificate once, and the second attempt
succeeds. A different failure — exit code 2 from `opc-browse` — means it could
not connect at all, which is credentials or the endpoint, not the tag map.

**This MES shows lower availability than their incumbent.** Usually not a
disagreement. This MES divides run time by the part of the window it actually
watched and reports that part as `coverage`; their incumbent almost certainly
divides by the whole window. Read coverage beside the figure, and run
`fsmes oee explain <machine> 8h` before anyone concludes the two disagree.

**Three things look wrong on day one and are correct:** OEE says *unknown*
rather than a number; the downtime pareto is nearly all *unlabelled*; a
machine with no scrap counter shows no quality figure. Unknown is not zero,
and unlabelled stops are reported as unlabelled rather than filed under
"other".

**The scorecard does not say which side is right.** Every difference is
stated as "the incumbent has X, this MES has Y". The tie-breaker is the
machine. Do not let it be read as a verdict.

## Done check

A scorecard report exists: they have `scorecard.html` (or the printed table)
with the four parts — what was read with totals, agreement per field, every
difference with both numbers, and what it cannot tell you. The line shape to
look for is `good_qty        41 agree, 3 differ, 2 not compared, of 46`.

Then `4-migration.md` — and read it before you answer any question about
switching the old system off.

**What to send us, once that exists:** `fsmes doctor --out doctor.md` on
**0.3.1 and newer** writes a file whose second half, *Performance, toward
migration*, is the part to send; `0-how-to-help.md` says how to ask for it.

*This page scripts `docs/operate/first-plant.md`,
`docs/operate/shadow-mode.md` and `docs/operate/shadow-scorecard.md` in the
project's repository. `first-plant.md` is written and untried: no real plant
has been through it yet. If something on it does not match what they see, that
is worth telling the project.*
