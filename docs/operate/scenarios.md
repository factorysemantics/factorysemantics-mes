# Run a product through a line by writing tags

A scenario is a file of "at this time, this tag takes this value". `fsmes
scenario play` writes it into an OPC UA server — KEPServerEX, or any other —
so every system that reads that server sees the same thing happen: this MES,
the MES you run today, a script you are testing. One run, two MESs, the same
cans.

```bash
fsmes scenario check line-trial.json                       # is the file right?
fsmes scenario check line-trial.json --endpoint opc.tcp://kepware:49320 \
      --security Basic256Sha256,SignAndEncrypt --user mes --password ...
fsmes scenario play  line-trial.json --endpoint opc.tcp://kepware:49320 ...
```

`play` prints a line per write as the clock reaches it and stops at the end or
on Ctrl-C. `--speed 4` plays four times as fast; `--from 02:00` starts two
minutes in. The connection flags default to the agent's own settings
(`MES_OPC_ENDPOINT`, `MES_OPC_SECURITY`, `MES_OPC_USER`, `MES_OPC_PASSWORD`)
and the same client certificate, so a server that already trusts the agent
trusts this.

## The safety rule

Only tags on a **Simulator** or **Memory Based** channel are ever written. A
write to a tag on a PLC driver reaches the PLC, so before writing anything the
player reads every tag it will write and refuses the whole run if any one of
them fails:

- **Refused** — the tag's channel is not declared in the file's `drivers:`
  block as `Simulator` or `Memory Based`; or it is declared as anything else
  (a PLC driver, `Advanced Simulator`); or Kepware shows signs of real
  equipment behind it (see below), whatever the file says. The refusal names
  the channel and the reason. There is no flag to override it.
- **Read-only** — the server does not let this client write the tag. Reported,
  never written.
- **Missing** or **type** — the tag is not on the server, or a value in the
  file cannot be written as the tag's type (text into a number, 40000 into a
  Short, three values into an array of four).

`fsmes scenario check --endpoint` runs exactly these checks, writes nothing,
and prints one line per tag: type, access, node.

In shadow mode (`MES_SHADOW=true`) `play` writes nothing at all: shadow mode
promises this MES changes nothing outside its own database, and a scenario
changes tags on another server. Run the player from a shell without it.

Why the file has to name the driver: Kepware's address space does not say which
driver a channel uses — its System Tags list names none
([Kepware Server help, System Tags](https://support.ptc.com/help/kepware/kepware_server/en/kepware/server/system-tags.html)).
What it does show is the difference between driver classes: a serial driver's
channel has `_System._ComId`, an Ethernet driver's channel has
`_System._NetworkAdapter`, and an Ethernet device's `_System._DeviceId` is its
IP address. The player looks for those three on every Kepware channel it writes,
and any one of them refuses the channel even if the file says `Simulator`.
(A Kepware server is recognised by `KEPServerEX` in its namespace table.)

## The file

JSON, one object. Any key starting with `_` is a note and is ignored; any other
unknown key is a mistake and `check` names its line.

```json
{
  "name": "three cans through the line",
  "drivers": {"SimScratch": "Simulator"},
  "tags": {
    "ld.good":   "ns=2;s=SimScratch.LD.GoodCount",
    "ld.serial": "ns=2;s=SimScratch.LD.Serial",
    "qi.pass":   {"node": "ns=2;s=SimScratch.QI.Pass", "type": "Boolean"}
  },
  "steps": [
    {"at": "00:00", "tag": "ld.serial", "value": "TRIAL-1"},
    {"at": "00:10", "tag": "ld.good",   "ramp": {"from": 0, "to": 400, "over": 120, "every": 10}}
  ],
  "product": {
    "id": "CAN-{n:04d}", "units": 3, "every_s": 20, "advance_s": 8, "start": "00:02",
    "stations": [
      {"station": "LD", "count": "ld.good", "id": "ld.serial"},
      {"station": "QI", "set": {"qi.pass": true}}
    ]
  }
}
```

**`tags`** — a short name for each tag, so the rest of the file reads
`ld.good` and not a node id. A tag is a node id (`ns=2;s=<channel>.<device>.<tag>`
on Kepware) or a browse path from Objects (`LD/FeedRateSP`, in the file's
`namespace`). `type` is read only by `scenario serve`; against a real server
the tag's own type is read from the server.

**`drivers`** — each channel the scenario writes, and its driver. The channel is
the first part of the address: `SimScratch` in `ns=2;s=SimScratch.LD.GoodCount`,
`LD` in `LD/FeedRateSP`.

**`steps`** — `at` (seconds, `"mm:ss"` or `"h:mm:ss"` from the start), `tag`,
and either a `value` (a number, true/false, text, or a list for an array tag)
or a `ramp`: `from`, `to`, `over` seconds, a write `every` seconds (default 1).
A ramp between two whole numbers writes whole numbers.

**`product`** — one block, or a list of them. A product goes through
`stations` in order, `advance_s` seconds at each (a station can give its own
`advance_s`). `units` products enter `every_s` apart, the first at `start`.
At each station, on arrival:

| key | what is written |
|---|---|
| `count` | the tag goes up by `by` (default 1) **from what it holds now** — read from the server at that moment, never reset |
| `state` | set to `running` (default 1); set to `after`, if given, when the product leaves |
| `id` | set to the product's id: `id` with `{n}` filled in (`CAN-{n:04d}` → `CAN-0001`), numbered from `first` (default 1) |
| `set` | each tag: value given |

Two writes at the same second go in the order the file gives them. `fsmes
scenario check <file> --timeline` prints every write in order before anything
is played.

## Rehearse without Kepware

```bash
fsmes scenario serve labs/kepsim/scenarios/product-through-the-line.json
fsmes scenario play  labs/kepsim/scenarios/product-through-the-line.json \
      --endpoint opc.tcp://127.0.0.1:4945/fsmes/scenario
```

`serve` holds every tag of the file as a writable memory tag at the file's own
addresses, like a Kepware Memory Based channel, on loopback, anonymous and
unencrypted. Anything that reads OPC UA can watch it.

Two examples live in [`labs/kepsim/scenarios/`](https://github.com/factorysemantics/factorysemantics-mes/tree/main/labs/kepsim/scenarios):
`product-through-the-line.json` (three cans through the six KepSim stations,
for `serve` or a scratch Kepware channel) and `setpoint-drill.json` (steps and
a ramp on the setpoints of the KepSim replay server, `fsmes run-opc-sim
--replay` — the only tags that server lets a client write; its station tags
are replayed from a file and read-only, and `check` says so).

## Kepware: what can be written

From Kepware's own documentation:

- **Simulator driver** — register (`R`), constant (`K`) and string (`S`)
  addresses are Read/Write and hold what is written. **But an `R` register
  read as an integer type goes up by one on every read** — it is the driver's
  "live data". Put counts and states on `K` addresses (`K0000`, Short or
  DWord), text on `S` addresses, and arrays as `K0010 [4]`. The simulation
  functions — `RAMP`, `SINE`, `RANDOM`, `USER` — are read-only and keep
  generating; the player cannot write them. (Simulator Driver Help, "Address
  Descriptions" and "Simulation Functions are Read Only Objects".)
- **Memory Based driver** — a simulated device inside the server that keeps
  its tag values, even between server runs (Memory Based Driver Help,
  overview). Its help lists a "read only" address error but the access table
  was not found; `check --endpoint` reads each tag's real access level, so
  your server answers this.
- **Advanced Simulator driver** — tags come from rows of a database over ODBC
  (its product sheet). Whether they accept a write was not found in its
  documentation; refused here regardless, because the next row overwrites
  whatever a scenario writes.
- **Any PLC driver** (Siemens, Allen-Bradley, Modbus, …) — a write reaches the
  PLC. Refused here, always.

Two Kepware habits worth knowing:

- **The first connection is rejected** until the client certificate is trusted:
  OPC UA Configuration Manager → Trusted Clients → select the rejected
  certificate → Trust, then reconnect (some versions want the runtime
  reinitialised). The certificate is the agent's own
  (`certs/mes_twin_client.der`), so trusting it once covers both.
- **Writes carry no timestamp.** Kepware, like many servers, refuses a write
  with a source timestamp; the player sends value only.

## A scratch channel with your own tag names

So both MESs read the scenario where they already read the line, make a
channel that only the scenario writes:

1. In the Kepware configuration, **Add Channel → Simulator** (or Memory
   Based), named for what it is — `Scratch`, say.
2. Add a device under it with the device name your MESs expect, then a tag per
   tag the trial needs, on `K` and `S` addresses, with the data type the real
   tag has.
3. Point each MES at the scratch tags for the trial (a tag map in this MES; the
   equivalent in the other), and list the channel in the file's `drivers:`.
4. `fsmes scenario check <file> --endpoint ...` until every tag says `write`.
5. `fsmes scenario play <file> --endpoint ...`.
