# Plants from a fleet file

*How-to. Run several independent plants — each its own database, OPC UA server and dashboard — from one list, and never touch code to add one.*

```bash
fsmes plant all init       # apply every pack and create every schema
fsmes plant all start      # bring them all up
fsmes plant all status     # who is alive and answering
fsmes plant cutlery run    # foreground supervisor for one plant (what systemd runs)
fsmes plant all migrate    # every stopped plant's database to the current schema, backup first
fsmes plant all stop
```

## The list

The fleet file is `labs/multiplant/fleet.toml` in a checkout, or whatever `FSMES_PLANT_REGISTRY` points at outside one. It lists packs, and nothing else except where the databases go:

```toml
packs = ["bottling", "machining", "finewire"]

[environment]
data_dir = "~/.local/share/fsmes/prod"   # databases, logs, certificates live here
```

Paths are relative to the fleet file, so a fleet file and its packs move together. Each directory is a **plant pack** — one `plant.toml` and the files it names — and [plant packs](packs.md) is the page for what may be in one.

A plant's name comes from its pack, not from the directory: the pack is what a plant says it is.

## What changed, and why

Until 2026-09-13 this file *was* the plant. Each entry carried seventeen keys — `label`, `api_port`, `opc_port`, `tag_map`, `init`, `secret_key`, `accounts` and the rest — read into a plain dict with `tomllib` and no schema. Two consequences, and both of them happened:

- **Nothing could tell a key from a typo.** `inspec_every` was a default nobody saw.
- **The file and this page drifted apart in both directions inside two weeks.** Three keys the code read were undocumented; one key documented here was read by nothing.

Every one of those keys is now in the plant's own `plant.toml`, or gone with a reason:

| Registry key | Where it went |
|---|---|
| `label` | `[plant] label` |
| `api_host`, `api_port` | `[serve] api_host`, `[serve] api_port` |
| `opc_port` | `[serve] opc_endpoint` — the whole endpoint, so a plant can point at a server that is not ours |
| `tag_map`, `replay_dir` | `[files] tag_map`, `[files] replay_dir` |
| `simulate`, `speed` | `[serve] simulate`, `[serve] speed` |
| `database_url`, `database_password_file` | `[storage]` |
| `inspect_every`, `issue_every`, `inspect_all` | `[floor]` |
| `accounts` | `[[accounts]]` |
| `secret_key` | **gone.** A pack holds no secret. `[serve] secret_key_env` names the variable it lives in |
| `init` | **gone.** A pack carries no code. Master data is data in `[files] masterdata`, seeded by `fsmes pack apply`; a plant whose master data is generated runs its own generator |
| `post_boot` | **gone**, same reason. A scenario script belongs to the lab that wrote it |
| `agent` | **gone.** It was in this page's key list and in no code path — the drift a schema exists to stop |
| `[environment] data_dir` | stays here: it is a fact about the machine, not about a plant |

`fsmes pack migrate` does that conversion and prints the receipt. `FSMES_PLANT_REGISTRY` kept its name.

## Rules worth knowing

- A pack with `[[accounts]]` never receives the lab accounts. A `password_env` that is not set refuses to start rather than creating an account with an empty password.
- `[storage] database_url` switches a plant to PostgreSQL; the default is SQLite in `data_dir`.
- `[serve] simulate = false` runs the API alone over whatever the database holds — the way the public demo serves a copy of one scored hour.
- Adding a plant is a directory and a line in this file. If it needs a code change, that is a bug — house rule 4 — and there is an issue form for it.

## See also

- [Plant packs](packs.md) — what is in one, and the four commands that read it
- [Deploy](deploy.md)
- [Settings reference](../reference/settings.md)
- The cutlery lab, the largest pack: `labs/cutlery/README.md` in the repository
