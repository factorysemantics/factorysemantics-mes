# Environments on one machine: test and prod

Two environments run side by side on the same host from the same code, and
the only difference between them is a fleet file and the packs it lists.

| | test | prod |
|---|---|---|
| Checkout | `~/Projects/factorysemantics-mes` on `main` | `~/Projects/fsmes-prod` pinned to a release tag |
| Fleet file | `labs/multiplant/fleet.toml` (in the repo) | `~/.config/fsmes/prod/fleet.toml` (outside it) |
| Packs | `labs/multiplant/<name>/` | `~/.config/fsmes/prod/<name>/` |
| Data | `labs/multiplant/.data/` | wherever the fleet file's `[environment] data_dir` says |
| Accounts | the lab list in `fsmes.plant.LAB_USERS`, lab passwords | the pack's `[[accounts]]`, passwords from `~/.config/fsmes/prod/env` |
| Secret key | `lab-<plant>-do-not-use-in-production` | the variable `[serve] secret_key_env` names |
| Units | `fsmes-plant@<name>` | `fsmes-prod-plant@<name>` |
| Changes | every merge to `main` | `deploy/promote.sh <tag>` only |

`FSMES_PLANT_REGISTRY` points `fsmes plant` at a fleet file outside the
checkout. It lists packs and says where the data goes:

```toml
packs = ["bottling", "machining"]

[environment]
data_dir = "~/.local/share/fsmes/prod"
```

and each pack is a directory beside it, with its own `plant.toml`:

```toml
[pack]
format   = 1
requires = ">=0.1.2"

[plant]
name     = "bottling"
label    = "ACME Beverages / Kansas City - 6-station bottling line"
timezone = "America/Chicago"
profile  = "plant"

[serve]
api_host       = "127.0.0.1"      # only the tunnel reaches it
api_port       = 9010
opc_endpoint   = "opc.tcp://127.0.0.1:4941/fsmes/bottling"
secret_key_env = "FSMES_BOTTLING_SECRET_KEY"

[files]
tag_map    = "tag_map.json"
replay_dir = "../../labs/kepsim/out"

[[accounts]]
code = "DEMO"
name = "Demo visitor"
role = "viewer"
password_env = "FSMES_DEMO_PASSWORD"
```

**A pack holds no secret**, which is what makes this directory safe to
version-control and to attach to a support thread: the token key and every
account password are environment variables the pack names. Check a pack
before promoting it: `fsmes pack check ~/.config/fsmes/prod/bottling`.

A plant with `[[accounts]]` never receives the lab accounts. A password
variable that is not set refuses the promote rather than creating an account
with an empty password.

Converting an old registry: `fsmes pack migrate plants.toml --plant bottling
--out ~/.config/fsmes/prod/bottling` writes the pack and prints what it
moved, what it dropped and why, and the one value it will not guess - the
time zone, which a registry never held.

## First-time setup

```bash
git clone https://github.com/factorysemantics/factorysemantics-mes.git ~/Projects/fsmes-prod
mkdir -p ~/.config/fsmes/prod && chmod 700 ~/.config/fsmes/prod
# write fleet.toml and the packs it lists (above), and env (MES_ADMIN_PASSWORD=..., MES_OPERATOR_PASSWORD=...,
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
