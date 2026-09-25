# What this plant asks of its ERP link

Ten numbers in this MES used to be literals in the source: how many times a
confirmation is offered before somebody has to look at it, how long to wait
between attempts, which statuses the ERP puts an order in while it is waiting
to be made, how long to wait on one request, and when a number the ERP hands
back counts as the number that was sent. Each one was a judgment somebody
made once against one ERP, and several of them had a comment beside them
arguing for the number rather than stating it.

They are this plant's now. They are seeded from `[erp]` in the plant pack,
owned by the plant's database from then on, and — for nine of the ten —
**edited on Orders › Configuration** by somebody holding `erp.define`, with
the value the plant is actually running on beside each one. They follow the
same rule every setting in this product follows:

> **The literal that was in the source is the shipped default, unchanged.**
> A plant that writes none of these keys behaves exactly as it did.

## The keys

| Key | Ships | What it decides |
|---|---|---|
| `max_attempts` | `8` | How many times one confirmation is offered before it is dead and a person decides |
| `base_backoff_s` | `5` | The wait before the second attempt, in seconds. It doubles after each failure |
| `max_backoff_s` | `3600` | The longest this plant waits between two attempts. The doubling stops here |
| `open_statuses` | `["Not Started", "In Process"]` | Which statuses this plant's ERP puts an order in while it is waiting to be made |
| `float_rel_tol` | `0.001` | How far a number the ERP hands back may differ, as a fraction, and still be the same number |
| `float_abs_tol` | `0.01` | The same agreement as an absolute amount, for numbers near zero |
| `http_timeout` | `30.0` | How long the ERPNext connector waits on one request, in seconds |
| `rest_timeout` | `10.0` | The same, for the plain REST connector |
| `default_order_priority` | `50` | The priority an order arriving with none inherits. Lower is more urgent |
| `confirmation_seconds_tolerance` | `1.0` | How far `machine_seconds` may differ from the time the step was open before `fsmes erp validate` calls the document wrong |

```toml
[erp]
mode = "erpnext"
# A plant whose ERPNext bench is across a VPN, takes a maintenance window on
# Sunday mornings, and has added a status of its own.
max_attempts = 24
max_backoff_s = 21600
http_timeout = 120.0
open_statuses = ["Not Started", "In Process", "Material Transferred"]
```

`fsmes pack check` reads every one of them offline, and **the Configuration
page refuses the same values in the same sentences**. It refuses a value that
would leave the thing it decides unable to decide anything — a confirmation
offered no times at all, an empty list of open statuses, a timeout of
nothing — and it refuses nothing else. A plant that gives up after three
attempts because somebody watches the outbox is answering its own question.

### Two lists that are one judgment

`base_backoff_s` and `max_backoff_s` are one judgment written as two numbers:
a first wait longer than the ceiling on a wait makes every attempt the same
distance apart, which is a retry loop with extra words. Either half is
refused when it crosses the other, so raise the ceiling before you raise the
first wait.

## What stays the product's, and why

The test is
[decision 0035](../decisions/0035-configuration-is-authored-by-roles-and-selected-by-operators.md)'s:
**ask what breaks if two plants answer differently.**

- **The confirmation contract** — its field names, its shapes, and the
  [published JSON Schema](../reference/erp-confirmation.schema.json) an ERP
  team checks files against. A plant that changed one of those would send a
  document meaning something nobody else means by it.
- **What the MES does with a refusal.** An ERP that reads a message and says
  no is dead at once rather than retried, whatever `max_attempts` says,
  because retrying sends the identical message and gets the identical answer.
- **The words in `open_statuses`.** The list is the plant's; the vocabulary
  is the ERP's. This MES holds no list of what ERPNext, SAP or Odoo may call
  a status and does not judge the words — only that each one is a word, that
  the list says something, and that none of them has a comma in it, because
  the list travels to the product as a comma-separated setting.

## The one that is not edited on the page

`confirmation_seconds_tolerance` is a pack key and a setting, and it is **not**
a box you type in. It is read by `fsmes erp validate`, which reads a folder of
files and nothing else — no ERP, no connector, **no database**. That is the
whole point of that command: a plant's ERP team runs it on a shadow-mode
outbox, often on a laptop that has never had an MES database on it. There is
no session to read a live row through, so a box on the page saying *in force
the moment you save it* would have been false. Its row on Orders ›
Configuration says what is true instead: *it changes when the pack is applied
and the plant restarts.*

## Two retry policies, not one

`[uns]` has three numbers with these same three names and these same three
values, and they are deliberately separate settings. The namespace broker on
this site and an ERP across a VPN are two systems with two outages: a plant
that widened its ERP retries because the ERP takes a four-hour maintenance
window on Sundays did not mean to widen how long it holds namespace messages.

## Editing them

**Orders › Configuration** (`/dashboard/config/supply_chain`) lists all six
sections with their keys, the value this plant is running on, and whether
that value is the product's default or one the plant set.

1. Open **Orders › Configuration**. Each row's **Set to** column holds one box
   per key, with the key's name beside it.
2. Type the new value and press **Save**. There is one Save per row, because
   the two backoff numbers and the two read-back tolerances are each one
   judgment written as two numbers.
3. It is in force without a restart. The three a service applies —
   `max_attempts`, the two backoffs — and `default_order_priority` are in
   force on the very next message. The four a *connector* applies —
   `open_statuses`, the two tolerances and the two timeouts — reach the
   running sync worker at the start of its next cycle, which is
   `MES_ERP_POLL_SECONDS` later and five seconds by default. Nothing is
   restarted and no pack is re-applied either way.

You need the **`erp.define`** capability, which the built-in Administrator
and Agent roles hold. Without it the page shows you every value and no box.

Every change is written to the audit trail — who, when, what it was and what
it became. **Undo is typing the old number back.** There is no revision
history and no approval step here, because nothing in this MES records *the
timeout that was in force when this confirmation failed*: the message stores
its attempts and its error, not the policy that produced them.

### Why a connector is handed its settings rather than reading them

The ERP sync worker keeps every database transaction short and never lets one
span an HTTP call, so the ERP being slow cannot hold up the plant floor. An
adapter that opened a database session of its own to find out its timeout
would be the first thing to break that rule. So the worker reads this plant's
policy once per cycle, in a transaction that is closed before anything is
sent, and hands it to the connector. A connector published on its own and
written against the older port takes no policy at all and keeps the values it
was built with, which is exactly what it did before this existed.

### What the pack still does, and what it no longer does

`fsmes pack apply` **seeds** each key the pack carries, once. After that the
database owns it and a later apply leaves it exactly as it is — the same rule
every other kind a pack seeds already keeps. So editing `plant.toml` and
re-applying does not move a number this plant has already taken ownership of;
`fsmes pack status` reports the difference. A pack is how a *new* plant
starts, not how a running one is steered.

### Where the value lives

A number is read in three layers, in this order:

1. the row in this plant's `plant_settings` table — what its administrator or
   its pack wrote;
2. the setting the pack compiled into the environment (`MES_ERP_*`);
3. the literal this version of the product ships.

`confirmation_seconds_tolerance` has only the second and third of those, for
the reason above.

## One change to what the MES sends

An order arriving from the ERP with no priority now says so: the
[`ProductionRequest`](../reference/erp-confirmation.schema.json) it is read
into carries `priority: null` rather than `50`. Fifty was this MES's own
answer written into the border, so an order that had never carried a priority
and one that carried exactly fifty arrived as the same document. What an
order carrying none inherits is `default_order_priority`, applied by the MES
when the order is imported — so nothing about a plant that leaves that key
alone changes, and a plant that dispatches on a 1–9 scale can finally say so.
