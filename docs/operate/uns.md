# The unified namespace (MQTT)

*How-to. Every event the MES records, on your broker, under an ISA-95 topic tree, with no code on the consumer's side.*

Plants that move data already have a broker. Node-RED, United Manufacturing
Hub, Ignition and half the Grafana stacks on a shop floor read MQTT, and
what they want from an MES is not an API to call — it is for the MES to
show up in the namespace they already have. `fsmes uns publish` is that: a
worker that relays the MES's own event stream to MQTT as JSON.

It is a relay, not a second source of truth. The events are the ones the
MES already wrote to its transactional outbox — the same stream the ERP
connector delivers from — so nothing published here was invented for the
broker's benefit, and turning the publisher off loses nothing.

One log, two readers, and neither owns it: the ERP sync takes only the
kinds its own contract can parse, so a machine going down sits in the same
table without ever being posted to an ERP as a production confirmation.

## Configure

```bash
MES_UNS_MODE=mqtt                     # off (default) | mqtt | log
MES_UNS_BROKER_URL=mqtt://broker.plant.example:1883   # mqtts:// for TLS
MES_UNS_USERNAME=mes
MES_UNS_PASSWORD=...                  # beats a password written into the URL
MES_UNS_CLIENT_ID=fsmes               # per MES instance; brokers evict a duplicate id
MES_UNS_TOPIC_PREFIX=umh/v1
MES_UNS_ENTERPRISE=                   # empty: use the enterprise the equipment model holds
MES_UNS_SITE=                         # empty: use the site the equipment model holds
MES_UNS_SCHEMA=_mes
MES_UNS_QOS=1
MES_UNS_RETAIN=false
MES_UNS_POLL_SECONDS=2
```

The MQTT client is an optional extra, because a plant PC that does not
speak MQTT should not carry an MQTT library:

```bash
pip install 'factorysemantics-mes[mqtt]'
```

Then run the worker:

```bash
fsmes uns publish            # forever; this is what systemd or Compose runs
fsmes uns publish --once     # one cycle, and say what it did
fsmes uns topics             # the topic tree, before a single event exists
fsmes uns queue              # what has been published and what is still owed
```

!!! tip "No broker yet?"

    `MES_UNS_MODE=log` builds every topic and payload and writes them to the
    log without contacting anything. It is the way to agree the topic tree
    with whoever owns the broker before asking them for credentials.

## The topic tree

```text
<prefix>/<enterprise>/<site>/<area>/<work centre>/…/<work unit>/<schema>/<event kind>
```

Only the prefix, the enterprise, the site and the schema are settings.
Everything between them is read out of the equipment model the MES already
holds, at whatever depth the plant modelled it — a site with cells between
its lines and its machines publishes the cells too.

For the demo plant (`ACME` → `KC1` → `PKG` → `LINE1` → `MIX01`):

| Event | Topic |
|---|---|
| operation confirmation on a machine | `umh/v1/ACME/KC1/PKG/LINE1/MIX01/_mes/operation_confirmation` |
| equipment state change on a machine | `umh/v1/ACME/KC1/PKG/LINE1/MIX01/_mes/equipment_state_change` |
| order completion (no machine) | `umh/v1/ACME/KC1/_mes/order_completion` |
| order hold (no machine) | `umh/v1/ACME/KC1/_mes/order_hold` |

Three rules that are house rules rather than MQTT rules:

- **A level the plant has not modelled is published as `unknown`.** It is
  not guessed from the level above and it is not left out, because a
  consumer counting topics would otherwise count a machine as belonging to
  the line above it. Two sites in one database and no `MES_UNS_SITE` set
  gives `unknown` for order-level events, and that is the honest answer.
- **An event that names no machine publishes at the site.** An order
  completion is an order-level fact; hanging it under a machine would tell
  the namespace that machine finished the order.
- **Nothing is dropped for being unrecognised.** A confirmation naming a
  machine the master data does not hold publishes under its own code, and
  an event kind the publisher has never seen gets its own topic.

Characters MQTT reserves — `/`, `+`, `#` — and anything outside
`A-Z a-z 0-9 _ . : @ = -` are replaced with `_` in every segment.

### Matching United Manufacturing Hub

UMH's convention is
`umh/v1/<enterprise>/<site>/<area>/<line>/<cell>/_<schema>/<tag>`. The
defaults are already that shape; naming the enterprise and site the way
UMH's own configuration does completes it:

```bash
MES_UNS_TOPIC_PREFIX=umh/v1
MES_UNS_ENTERPRISE=acme
MES_UNS_SITE=kansas-city
MES_UNS_SCHEMA=_historian
```

That publishes `umh/v1/acme/kansas-city/PKG/LINE1/MIX01/_historian/operation_confirmation`.
The area, line and machine segments stay the plant's own equipment codes,
because those are the names the plant uses on the floor and in the MES.

## What one event looks like

```json
{
  "schema_version": 1,
  "source": "factorysemantics-mes",
  "plant": "kc1",
  "event_id": 4182,
  "message_key": "WO-1004:op20",
  "kind": "operation_confirmation",
  "direction": "out",
  "recorded_at": "2026-09-09T11:04:07.221Z",
  "published_at": "2026-09-09T11:04:09.008Z",
  "payload": { "...": "the MES's own event, unchanged" }
}
```

`payload` is the outbox message as the MES recorded it, so a consumer that
reads [the ERP contract](https://github.com/factorysemantics/factorysemantics-mes/blob/main/src/fsmes/integrations/erp/contract.py)
reads this. `recorded_at` is when the MES recorded the event, not when the
broker heard about it — a backlog delivered after an outage must not read
as a burst of production that happened at reconnect time.

## Delivery

At-least-once, and deliberately not more:

- Every outbox event is enrolled for publication exactly once, and the
  publisher keeps its own delivery record, so it never consumes events the
  ERP connector is still waiting to send.
- A publish that fails backs off — 5 s, 10 s, 20 s, up to an hour — and
  after eight attempts the event is marked dead. Dead is not deleted: an
  event the namespace never received is a fact somebody has to decide
  about. `fsmes uns queue` lists them.
- A consumer **will** see the same event twice after a broker hiccup.
  `event_id` (with `plant`, if you merge several MES databases into one
  namespace) is what it dedupes on. There is no exactly-once here and there
  is not going to be; that promise cannot be kept across a network.
- Events are not retained by default. A retained event is replayed to every
  new subscriber as though it had just happened.

The worker outlives the broker. If the broker is down when it starts, or
goes away mid-shift, events queue in the database and go out in order when
it returns. The MES itself never blocks on the broker.

## What is published today

Four kinds, and this list is the whole of it:

| Kind | What it says | Where it hangs |
|---|---|---|
| `operation_confirmation` | what one operation of one order did — input, good, scrap, WIP, setup, machine and labour time, cost centre, lots consumed | the machine |
| `order_completion` | the order closed: ordered, good and scrap totals and the finished-goods lot | the site |
| `equipment_state_change` | a machine moved between running, idle, down and setup — the state it entered, the reason if there was one, the state it left and how long that had been open | the machine |
| `order_hold` / `order_resume` | an order stopped without being finished, with the reason, and later went back to work | the site |

Two things that list does not include, said plainly rather than left to be
discovered:

- **OEE windows are not published.** OEE is computed over a window when
  somebody asks for one; it is not a fact the MES records at a moment, so
  there is nothing in the log to relay. Publishing it would mean choosing a
  cadence and a window length on the plant's behalf, and an availability
  figure whose `unknown` share had been rounded into it is the worst kind
  of wrong number. Read it from the API for now.
- **Quality checks and non-conformances are not published yet.** They are
  recorded, and they are the obvious next kinds.

Nothing is dropped for being new: the publisher gives a kind it has never
seen its own topic, so a kind added to the MES reaches the broker without a
change here.

### Turning the plant events off

```bash
MES_OUTBOX_DOMAIN_EVENTS=false        # default: true
```

The MES's event log is one table. Confirmations go in it because the ERP
connector delivers from it; state changes, holds and resumes go in it
because this publisher does. A plant with no broker and no other reader can
set this to `false` and keep the log to what the ERP is owed — one row per
operation and per order instead of one per state change.

What it costs is exactly what this page promises: with it off, the
namespace shows the ERP's half of the plant and nothing else. The MES's own
state history, audit trail and OEE are unaffected either way; only the
event log is.

Sparkplug B is a later envelope over the same work — see the
[roadmap](https://github.com/factorysemantics/factorysemantics-mes/blob/main/ROADMAP.md).
MQTT-JSON first, because it is what a Node-RED flow can read in an
afternoon.

## Verified against

The test suite, against a fake broker — this repository does not start
brokers on the machine it is developed on. There is one test against a real
broker (`test_a_running_broker_receives_what_the_publisher_sent`), marked
slow and skipped unless `MES_UNS_TEST_BROKER` points at one. **Not yet
verified against a real Mosquitto, HiveMQ or UMH deployment**, as of
2026-09-09; see [compatibility](compatibility.md). If you run it against
one, an issue saying which broker and what the topics looked like is the
most useful thing you can send.

## See also

- [Settings reference](../reference/settings.md) — every `MES_UNS_*` value
- [CLI reference](../reference/cli.md) — `fsmes uns`
- The topic rules, in code: `src/fsmes/integrations/uns/topics.py`
