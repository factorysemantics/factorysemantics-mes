# Plants from a registry

*How-to. Run several independent plants — each its own database, OPC UA server and dashboard — from one TOML file, and never touch code to add one.*

```bash
fsmes plant all init       # create every schema and seed every plant
fsmes plant all start      # bring them all up
fsmes plant all status     # who is alive and answering
fsmes plant cutlery run    # foreground supervisor for one plant (what systemd runs)
fsmes plant all migrate    # every stopped plant's database to the current schema, backup first
fsmes plant all stop
```

The registry is `labs/multiplant/plants.toml` in a checkout, or whatever
`FSMES_PLANT_REGISTRY` points at outside it. A plant is a table:

```toml
[environment]
data_dir = "~/.local/share/fsmes/prod"   # databases, logs, certificates live here

[plants.cutlery]
label      = "Cutlery Works — 60 million ids a day"
api_host   = "127.0.0.1"        # the interface to serve on; a LAN address to share it
api_port   = 9030
opc_port   = 4844
tag_map    = "labs/cutlery/tag_map.json"
replay_dir = "labs/cutlery/out"  # generated line data (fsmes sim-generate)
init       = "labs/cutlery/init.py"
simulate   = false               # serve the API only, over an existing database
secret_key = "<long random string>"
accounts = [
  { code = "DEMO",  name = "Demo visitor",  role = "viewer", password_env = "FSMES_DEMO_PASSWORD" },
  { code = "ADMIN", name = "Administrator", role = "admin",  password_env = "MES_ADMIN_PASSWORD" },
]
```

Keys the registry understands (2026-09-07): `label`, `api_host`, `api_port`,
`opc_port`, `tag_map`, `replay_dir`, `init`, `post_boot`, `speed`,
`simulate`, `secret_key`, `accounts`, `database_url`,
`database_password_file`, `agent`, and `[environment] data_dir`.

Rules worth knowing:

- A plant with `accounts` never receives the lab accounts. A `password_env`
  that is not set refuses to start rather than creating an account with an
  empty password.
- `database_url` switches a plant to PostgreSQL; the default is SQLite in
  `data_dir`.
- `simulate = false` runs the API alone over whatever the database holds —
  the way the public demo serves a copy of one scored hour.
- Adding a plant is an entry here plus a tag map and line data. If it needs
  a code change, that is a bug — house rule 4 — and there is an issue form
  for it.

## See also

- [Deploy](deploy.md)
- [Settings reference](../reference/settings.md)
- The cutlery lab, the largest registry entry: `labs/cutlery/README.md` in the repository
