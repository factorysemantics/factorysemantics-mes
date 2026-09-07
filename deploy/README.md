# Environments on one machine: test and prod

Two environments run side by side on the same host from the same code, and
the only difference between them is a registry file.

| | test | prod |
|---|---|---|
| Checkout | `~/Projects/factorysemantics-mes` on `main` | `~/Projects/fsmes-prod` pinned to a release tag |
| Registry | `labs/multiplant/plants.toml` (in the repo) | `~/.config/fsmes/prod/plants.toml` (outside it) |
| Data | `labs/multiplant/.data/` | wherever the registry's `[environment] data_dir` says |
| Accounts | the lab list in `fsmes.plant.LAB_USERS`, lab passwords | the registry's `accounts`, passwords from `~/.config/fsmes/prod/env` |
| Secret key | `lab-<plant>-do-not-use-in-production` | `secret_key` in the registry |
| Units | `fsmes-plant@<name>` | `fsmes-prod-plant@<name>` |
| Changes | every merge to `main` | `deploy/promote.sh <tag>` only |

`FSMES_PLANT_REGISTRY` points `fsmes plant` at a registry outside the checkout.
When it is set, the registry may carry an `[environment]` table and each plant
may carry `secret_key` and `accounts`:

```toml
[environment]
data_dir = "~/.local/share/fsmes/prod"

[plants.bottling]
label      = "ACME Beverages / Kansas City - 6-station bottling line"
api_host   = "127.0.0.1"          # only the tunnel reaches it
api_port   = 9010
opc_port   = 4941
tag_map    = "config/tag_map_kepsim.json"
replay_dir = "labs/kepsim/out"
init       = "labs/multiplant/bottling/init.py"
secret_key = "<long random string>"
accounts = [
  { code = "DEMO",      name = "Demo visitor",         role = "viewer",   password_env = "FSMES_DEMO_PASSWORD" },
  { code = "FLOOR-SIM", name = "Simulated shop floor", role = "operator", password_env = "MES_OPERATOR_PASSWORD" },
  { code = "ADMIN",     name = "Administrator",        role = "admin",    password_env = "MES_ADMIN_PASSWORD" },
]
```

A plant with `accounts` never receives the lab accounts. A password variable
that is not set refuses the promote rather than creating an account with an
empty password.

## First-time setup

```bash
git clone https://github.com/factorysemantics/factorysemantics-mes.git ~/Projects/fsmes-prod
mkdir -p ~/.config/fsmes/prod && chmod 700 ~/.config/fsmes/prod
# write plants.toml (above) and env (MES_ADMIN_PASSWORD=..., MES_OPERATOR_PASSWORD=...,
# FSMES_DEMO_PASSWORD=..., FSMES_AGENT_PASSWORD=...); chmod 600 both
cp ~/Projects/fsmes-prod/deploy/fsmes-prod-plant@.service ~/.config/systemd/user/
systemctl --user daemon-reload
~/Projects/fsmes-prod/deploy/promote.sh v0.1.0
systemctl --user enable fsmes-prod-plant@bottling fsmes-prod-plant@machining
```

## Releasing

Tag `main` only when CI is green, then promote:

```bash
git tag -a v0.1.1 -m "..." && git push origin v0.1.1
~/Projects/fsmes-prod/deploy/promote.sh v0.1.1
```

The script backs up every database (the migrate command keeps
`<plant>.db.pre-migrate-<stamp>`), and any failure after the checkout puts the
previous tag and the backups back before it exits non-zero.
