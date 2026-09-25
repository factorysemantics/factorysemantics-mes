# This plant's own administration numbers

Forty-four numbers in this MES used to be literals in the source: the role a
new account starts with, how big a list answer is, how often each screen
re-reads the plant, how long a recorded walkthrough may be, what one
conversation with a model may spend, how long a confirmation stays on screen,
how much log history is kept, and which model on the machine answers.

They are this plant's now. They are seeded from `[admin]`, `[screens]` and
`[system]` in the plant pack, owned by the plant's database from then on, and
**edited on Setup › Configuration** by somebody holding `users.manage` — with
the value the plant is actually running on beside each one. They follow the
same rule the [quality numbers](quality-numbers.md) do:

> **The literal that was in the source is the shipped default, unchanged.**
> A plant that writes none of these keys behaves exactly as it did.

## Three tables, because they are three different things

A pack table is a table in a file and a Configuration workspace is a place on
a screen, and the two are deliberately not the same. All three of these tables
are listed on one page.

| Table | What is in it | Edited on the page? |
|---|---|---|
| `[admin]` | The plant's own administration: who a new account is, how big an answer is, what a model may spend, the shape of a drafted document | Yes, except the two list bounds |
| `[screens]` | Everything the browser reads: three refresh clocks, four page sizes, a ceiling, a toast duration, a debounce, the assistant's three | Yes |
| `[system]` | The plant's plumbing: the fleet probe, log rotation, which local model answers | Only the model name |

## `[admin]` — the plant's own administration

| Key | Ships | What it decides |
|---|---|---|
| `default_new_account_role` | `"operator"` | The role an account created without one is given. One plant onboards everyone as a viewer and grants upward; another starts them on the floor |
| `list_default_limit` | `50` | How many rows a list returns when nobody asks for a size. **Read at start-up** |
| `list_max_limit` | `500` | The most any caller may ask one list for. Must not be below the default. **Read at start-up** |
| `pending_approvals_page_size` | `20` | How many waiting items the Floor screen's approvals panel answers with. The panel has no pager, so item twenty-one is not on the screen at all |
| `walkthrough_max_steps` | `60` | The most steps one recorded walkthrough may have |
| `walkthrough_title_chars` | `120` | Where a step's title is shortened |
| `walkthrough_body_chars` | `1000` | Where a step's explanation is shortened |
| `walkthrough_fill_chars` | `200` | How long a value a step types into a control may be |
| `walkthrough_tab_chars` | `60` | How long a step's tab or open-this name may be |
| `walkthrough_default_capability` | `"plant.read"` | What a recording asks of a viewer when nobody says otherwise |
| `agent_max_rounds` | `12` | Turns the floor agent may take on one message before it must stop and say something |
| `agent_session_ttl_seconds` | `1800` | How long a conversation lives without a message |
| `agent_result_limit` | `6000` | How much of one tool result the agent is shown; the rest is marked truncated |
| `assistant_context_chars` | `3000` | How much of the plant's own facts reach the local model |
| `design_compress_budget` | `2500` | How long the design chat's on-device summary of a screen may be |
| `design_compress_source_chars` | `12000` | How much of a screen is handed to that summary |
| `design_source_budget` | `14000` | How much of a screen's own source the design chat reads |
| `assistant_timeout_seconds` | `60` | How long the floor assistant waits for the local model |
| `drafting_timeout_seconds` | `180` | How long drafting a work instruction waits — the longest, because it is writing prose |
| `design_generate_timeout_seconds` | `120` | An ordinary design-chat generation |
| `design_classify_timeout_seconds` | `45` | The one-word *is this a design question* — the shortest thing asked of the model anywhere |
| `design_compress_timeout_seconds` | `90` | An on-device compression of a screen |
| `design_chat_timeout_seconds` | `240` | The design chat's own reply |
| `document_house_style` | Purpose / Steps / If it fails | The shape a drafted work instruction takes |
| `ai_rollup_stale_hours` | `40` | How old a daily AI artifact gets before the panel calls it late |

### What `document_house_style` may not say

A plant whose quality system mandates Scope / Hazards / Steps / Records writes
its own structure here, and should. Three clauses are **not** in this key and
cannot be put in it — they are added to whatever you write:

- use only the facts given;
- never invent a tolerance, a tool or a machine;
- **an operator is never told to adjust a reading toward the middle.**

The last one is not a house style. It is the difference between a measurement
and a fiction, and a plant that could edit it out of the prompt would be a
plant whose own drafted procedures quietly taught its people to falsify a
reading. It lives in `fsmes.services.drafting.INVARIANTS` and no setting
reaches it.

### The six timeouts are named, one per thing waited for

They were six anonymous numbers in five files. A plant on a slower GPU raises
all six — and can see which one it just raised, which is the whole reason they
have names rather than being one `local_model_timeout`.

## `[screens]` — what the browser reads

The browser asks for these once per page load, before it draws anything or
starts a clock, and keeps **no copy of any default**. A number saved here is in
force on the next page load.

| Key | Ships | What it decides |
|---|---|---|
| `floor_refresh_ms` | `2000` | How often the Floor screen re-reads the plant |
| `floor_pending_refresh_ms` | `30000` | How often it re-reads the approvals panel |
| `admin_refresh_ms` | `8000` | How often the Admin screen re-reads people and routings |
| `floor_machine_page` | `24` | Machine cards on one page of the grid. The tiles above still count the whole plant |
| `floor_order_page` | `10` | Work orders on one page of the Floor card |
| `floor_spec_choices` | `200` | Characteristics the specification picker offers before it says how many more there are |
| `admin_user_page_size` | `25` | People on one page of the Admin screen |
| `admin_routing_page_size` | `25` | Routings on one page of the Admin screen |
| `all_pages_limit` | `500` | Page size when a screen reads a whole bounded list |
| `all_pages_cap` | `2000` | Where that read stops and says the list is incomplete. Must not be below `all_pages_limit` |
| `toast_ms` | `3500` | How long a confirmation stays on screen |
| `input_debounce_ms` | `250` | How long a search box waits for typing to settle |
| `assistant_log_entries` | `60` | Lines of the assistant's conversation that survive a page change |
| `assistant_fill_attempts` | `20` | How many times a walkthrough looks again for a control that has not appeared |
| `assistant_fill_wait_ms` | `150` | How long it waits between those looks |

### Three clocks, not one

A screen watching machines and a screen listing employees are not one cadence,
so they stay three numbers — set in one place, on one row, with one Save.

## `[system]` — the plumbing, and where IT's settings live

IT has no capability and no Configuration workspace of its own: [decision
0035](../design/config-assistance.md) keeps it outside the role model
deliberately. These are listed on **Setup › Configuration** with everything
else and written by whoever holds `users.manage` — which is already the only
role that can reach them.

| Key | Ships | What it decides |
|---|---|---|
| `fleet_health_probe_timeout` | `3.0` | How long the fleet console waits for one plant to answer a health check. **Read at start-up** |
| `log_rotation_max_bytes` | `5000000` | How large one component's log grows before it rotates. **Read at start-up** |
| `log_rotation_backups` | `5` | How many rotations are kept per component. **Read at start-up** |
| `local_model_name` | `"qwen3:8b"` | Which model on this machine answers and drafts |

A document records the model that actually wrote it, so changing
`local_model_name` changes nothing a past record means.

## What "read at start-up" means, and why five keys are

Most of these are in force the moment they are saved. Five are not, and the
page says so rather than offering an input that would half work:

- **`list_default_limit` and `list_max_limit`** are *published*. They are the
  default and the `le=` bound of every list endpoint in this plant's own
  OpenAPI document, which a client reads once and holds. A ceiling that moved
  under a caller holding that document would make the document a lie.
- **`log_rotation_max_bytes` and `log_rotation_backups`** are read before the
  plant's database is open. Logging is the thing that reports a database that
  will not open, so it cannot wait for one.
- **`fleet_health_probe_timeout`** is the console's number about every plant
  it watches, not any one plant's about itself, and the console has no plant
  database to read a row from.

For these, write the key in the pack and restart. `fsmes pack status` reports
the difference in the meantime.

## Writing them in a pack

```toml
[admin]
# A plant that onboards as viewers, records long changeovers, and runs a
# bigger model on better hardware.
default_new_account_role = "viewer"
walkthrough_max_steps = 120
assistant_context_chars = 8000
drafting_timeout_seconds = 600

[screens]
# A thin WAN link, and a bigger screen.
floor_refresh_ms = 10000
floor_machine_page = 48

[system]
local_model_name = "llama3:70b"
```

`fsmes pack check` reads all three tables offline and refuses a number that
would leave the thing it decides unable to decide anything — a poll interval
of nothing, an agent given no turns, a ceiling below the page it reads, a
capability nothing grants, a role this product does not ship. It refuses
nothing else: a plant that wants a ten-second floor poll is answering its own
question and is not being second-guessed.

## Seeded once, then owned by the plant

`fsmes pack apply` **seeds** each key the pack carries, once. After that the
database owns it and a later apply leaves it exactly as it is — the same rule
every other kind a pack seeds already keeps. Editing `plant.toml` and
re-applying does not move a number this plant has taken ownership of; change
it on the screen instead.

Underneath, a number is read in three layers:

1. the row in this plant's `plant_settings` table;
2. the setting the pack compiled into the environment (`MES_ADMIN_*`,
   `MES_SCREENS_*`, `MES_SYSTEM_*`);
3. the literal this version of the product ships.

Which is why a plant that has never touched the page and never applied a pack
behaves exactly as it did, and why upgrading moves no data.

## See also

- [This plant's own quality numbers](quality-numbers.md) — the same mechanism,
  one workspace over
- [Plant packs](packs.md) — where these keys live
- [What is still hard-coded](../design/config-audit-2026-09-21.md) — where
  these were rows A1 to A25 and I1 to I4
