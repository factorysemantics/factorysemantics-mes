# Plant packs

*Reference and how-to. One directory says which plant this is. Check it before it touches anything, apply it, and see whether the plant has drifted from it since.*

```bash
fsmes pack check   packs/finewire    # refuse it before it reaches a plant
fsmes pack apply   packs/finewire    # make the plant match the pack
fsmes pack status  packs/finewire    # what it runs, and whether it has drifted
fsmes pack migrate plants.toml --plant finewire --out packs/finewire
```

## What a pack is

**A plant pack is the complete, versioned, checked answer to "which plant is this?", in one directory, owned by the plant and not by this repository.**

```text
packs/<name>/
  plant.toml        identity, clock, profile, modules, words   (required)
  tag_map.json      which machines exist and what their tags mean
  masterdata/       equipment, materials, routings — data, not a script
  mappings/         inbound columns, inbound SQL
  line_layout.json  optional geometry for the line view
  README.md         what this plant is, for the next engineer
```

Four things a pack may **not** contain ([decision 0022](../decisions/0022-what-a-plant-pack-may-contain.md)):

1. **No code.** No Python, no hook, no expression to evaluate. A pack that can run code is a pack nobody can review before it touches a plant. The escape hatch is a module, and a module is code and gets reviewed.
2. **No secrets.** A pack *names* where a secret lives — `secret_key_env`, `database_password_file`, an account's `password_env` — and never holds one. **A pack should be safe to paste into an issue.**
3. **Nothing that changes what a number means.** `[words]` renames a label on a screen. It may not rename a state, a KPI, a capability, a role, an event `kind` or any field a topic or an API response is built from.
4. **No schema changes.** A pack cannot add a table or a column. That is a module.

## The keys

Every key is enumerated in `fsmes.pack.format`, and **an unknown key is an error**. That is the whole difference between a pack and the registry it replaces: the registry was read into a plain dict, so nothing could tell a key from a typo, and it drifted from its own documentation in both directions inside two weeks.

| Table | Keys | What it is |
|---|---|---|
| `[pack]` | `format`, `requires` | Which format this file is, and which product versions can read it |
| `[plant]` | `name`, `label`, `timezone`, `profile`, `enterprise`, `site` | Who this plant is. `name` is one namespace topic segment; `profile` is `laptop`, `plant` or `fleet` |
| `[modules]` | one boolean per module | What this plant serves. Anything not named stays on |
| `[words]` | display term → this plant's word | Display only. See below |
| `[storage]` | `database_url`, `database_password_file` | Where the data lives |
| `[serve]` | `api_host`, `api_port`, `opc_endpoint`, `simulate`, `speed`, `secret_key_env` | What it serves, and where |
| `[files]` | `tag_map`, `replay_dir`, `line_layout`, `masterdata` | The rest of the pack, by relative path inside it |
| `[erp]` | `mode` | `off`, `file`, `rest` or `erpnext` |
| `[uns]` | `mode`, `topic_prefix` | `off`, `log` or `mqtt` |
| `[inbound]` | `mapping`, `sql`, `mqtt_mode` | What other systems tell this plant |
| `[floor]` | `inspect_every`, `issue_every`, `inspect_all` | A simulated plant's own cadence |
| `[[accounts]]` | `code`, `name`, `role`, `password_env` | Accounts `pack apply` creates, from passwords the environment holds |

Everything a person writes lives **inside** the pack directory, by relative path. The one exception is `[files] replay_dir`: generated line data, often shared between plants replaying one line and often enormous, so it may point outward and it is not part of the pack's fingerprint.

## `fsmes pack check`

No database, no network, no plant. It reads the directory and refuses, one sentence per problem — **every problem, not the first**, because a person fixing a pack beside a line should need one round trip:

- a key or a table this format does not have, with the ones it does;
- a `[plant] name` that is not a code, a profile that is not a profile, a time zone this machine does not know, or **no time zone at all**;
- a `[words]` entry that renames a state, an order status, a capability, a role, an event kind, a KPI or a field every reader is built on — the protected list is read out of the product itself, so a capability added next month is protected the day it lands;
- a module this version does not have, or a kernel module a plant tried to switch off;
- a `[pack] requires` this release does not satisfy, or a `format` from a later release;
- a file the pack names that is missing, points outside the pack, or that **its own reader** refuses — the tag map's loader, the inbound mapping's, the SQL poller's, so a pack this accepts cannot be one the OPC agent then rejects;
- a secret, or a script, refused by name rather than as an unknown key.

What it cannot prove without a plant — that the OPC endpoint answers, that the database is reachable, that `FSMES_…_PASSWORD` is set on the machine this will run on — it reports as **unknown**, never as passing.

```
$ fsmes pack check labs/multiplant/finewire
Checking labs/multiplant/finewire against pack format 1.
  unknown the OPC endpoint opc.tcp://10.20.30.40:4840/FineWire/Hall2 - nothing here connects to it; `fsmes opc-verify` does
  unknown the database - nothing here connects to it; `fsmes db-status` does
  unknown the environment variable FSMES_FINEWIRE_SECRET_KEY - whether it is set where this plant runs is a fact about that machine, not about this pack
  unknown the environment variable FSMES_FINEWIRE_OPERATOR_PASSWORD - account HALL2 refuses to be created without it, where this plant runs
Usable, as far as a file can say: 9 file(s) read, nothing refused, 4 thing(s) only a running plant can answer.
```

## `fsmes pack apply`

Check, then the database, then the data, then the receipt. It is the one command that **becomes** the plant it acts on: it puts the pack's settings into its own environment, so the schema it upgrades and the rows it writes are that plant's.

**Pack before database.** A schema migration may need a value the pack now carries, so the pack is applied first — and `fsmes pack apply` runs the migrations itself, in that order.

Master data is seeded from `masterdata/`: `equipment.json`, `materials.json`, `routings.json`, `quality_specs.json`, `lots.json`, `work_orders.json`. An entry whose code already exists is counted as present and **left alone, never updated** — a pack that rewrote a routing an order has already run against would be rewriting history. A pack that carries no master data says so rather than reporting that it seeded nothing.

Rated cycle times are not repeated in the master data: leave `ideal_cycle_seconds` out and it is read from the pack's own tag map. OEE performance is ideal cycle × count ÷ runtime, so a rate that drifted from the line it describes produces a number that means nothing.

Applying is idempotent. Run it again and it makes nothing twice.

## `fsmes pack status`

```
$ fsmes pack status packs/finewire
  plant     finewire
  pack      packs/finewire
  applied   2026-09-13T18:22:04+00:00 by 0.1.2, format 1, 9 files
  drift     yes - the files in the pack have changed since it was applied. `fsmes pack apply` again to bring the plant to them.
  schema    c8b1e40d7a92 (head)
```

Drift is measured against the fingerprint `apply` recorded: a hash of every file a person wrote in the pack, names included, so a renamed file is a change. Generated line data is excluded.

A plant that has never been applied says **never** rather than answering "no drift" — those are different facts, and the second one is a lie about the first. Exits non-zero on drift or a database behind head, so a deployment script can act on the answer.

## `fsmes pack migrate`

One step exists, and it is the one every pack in this repository was written by: **a plant registry entry, format 0, to format 1**. Format 0 is not a file shape — it is what a plant was before packs, seventeen keys in a shared TOML file with no schema.

```bash
fsmes pack migrate ~/.config/fsmes/prod/plants.toml --plant bottling --out packs/bottling
```

It says what it **moved**, what it **dropped and why** — `init` and `post_boot` name code, `secret_key` is a secret, `opc_port` became a whole endpoint, `agent` was documented and read by nothing — and **what it could not know**. A registry never held a time zone, so the pack is written without one and the receipt says to set it. Guessing it from the machine's clock would tell a plant in Chicago it works in London.

## `[words]`: what a plant calls things

```toml
[words]
lot      = "coil"
material = "alloy"
```

Display only, and the boundary is load-bearing. The terms a plant may rename are enumerated (`fsmes.pack.check.RENAMEABLE`); everything a number is keyed on is refused, against a list read from the product itself. `equipment` and `operator` are deliberately **not** renameable — they are also the name of an event field and of a built-in role, and a word that is both a label and an identifier is the ambiguity the rule exists to stop. A plant that wants another word for a machine renames `machine`.

A checked pack compiles `[words]` into `MES_WORDS`, and the words ride on `/health`, `/shadow` and `fsmes info` beside the plant's name and clock — so anything about to show two plants' numbers side by side can tell which word is a rename and which is a different thing.

## The fleet file

The file that used to describe plants now lists packs:

```toml
packs = ["bottling", "machining", "finewire"]

[environment]
data_dir = "labs/multiplant/.data"
```

Paths are relative to the fleet file. `[environment] data_dir` stays here and not in a pack: where several plants keep their databases on one machine is a fact about the machine. `FSMES_PLANT_REGISTRY` still points at it — the variable kept its name; what it points at changed.

See [plants from a fleet file](registry.md) for running them.

## See also

- [Switch a module off](modules.md) — what `[modules]` compiles to
- [Plants from a fleet file](registry.md)
- [Beside your existing MES](first-plant.md) — the engineers' front door
- [Decision 0022 — what a plant pack may contain](../decisions/0022-what-a-plant-pack-may-contain.md)
- [The M8 design](../design/m8-packs-and-fleet.md) §6, which this is built from
