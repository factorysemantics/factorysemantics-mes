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

Tag `main` only when CI is green, push the tag to the public repository, then
promote:

```bash
git tag -a v0.2.1 -m "..." && git push public v0.2.1
~/Projects/fsmes-prod/deploy/promote.sh v0.2.1
```

`promote.sh` is the only way prod changes. In order:

1. **The tag is checked before anything stops.** It must be on the remote the
   promote fetches from (`FSMES_PROMOTE_REMOTE`, default `public`), and a
   local tag of that name pointing at something else is refused by name -
   `git fetch` will not overwrite one, and a checkout that carries tags from
   an older remote would otherwise promote the wrong commit under the right
   tag's name. The ordinary mistake here costs no downtime.
2. **It asks the product what the fleet is**, with `fsmes fleet plan --json`,
   rather than reading `fleet.toml` a second time in bash. Run
   `fsmes fleet plan` yourself to see exactly what it will act on.
3. **It stops every plant, then backs each one up**: a SQLite file copied
   with its write-ahead log to `<plant>.db.pre-migrate-<stamp>`, a PostgreSQL
   database dumped with `pg_dump -Fc` to `<plant>.pre-migrate-<stamp>.dump`
   in the fleet's data directory. A backup taken from a running plant is a
   backup of a half-written file, which is why the stop comes first. **A
   plant whose database this cannot copy refuses the whole promote** - a
   promote you could not undo is not one to run. The newest
   `FSMES_PROMOTE_KEEP` (default 3) are kept per plant.
4. **Then** the checkout, the venv, the deterministic line data, `fsmes pack
   apply` for each pack, and the start.
5. **It asks each plant itself whether it came back.** `/health` must answer
   `ok` and call itself by the name the fleet knows it by; `/pack` must say
   its schema is at head. Both are read from the plant over the endpoints it
   answers without a credential - never from a CLI in the promote's own
   shell, which can be pointed at a different database than the plant serves.
6. **Any failure after the checkout rolls back**: the previous tag, the
   previous venv, the backups *this run made* (`pg_restore --clean
   --if-exists --single-transaction` for a PostgreSQL plant), and the plants
   started again. Every PostgreSQL call carries
   `PGOPTIONS='-c statement_timeout=0'`, and nothing runs `CREATE DATABASE …
   TEMPLATE`: a step a statement timeout can cancel is not a rollback.

What it reads from the environment:

| | |
|---|---|
| `FSMES_PROD_ROOT` | the checkout (default `~/Projects/fsmes-prod`) |
| `FSMES_PROD_CONF` | fleet file, packs and `env` (default `~/.config/fsmes/prod`) |
| `FSMES_PLANT_REGISTRY` | the fleet file (default `$FSMES_PROD_CONF/fleet.toml`) |
| `FSMES_PROMOTE_REMOTE` | the git remote to promote from (default `public`) |
| `FSMES_PROMOTE_UNIT` | systemd unit prefix (default `fsmes-prod-plant@`) |
| `FSMES_PROMOTE_KEEP` | backups kept per plant (default 3) |
| `FSMES_PROMOTE_WAIT` | seconds to wait for a plant to answer (default 90) |

`tests/test_promote_script.py` runs the script against two fake plants - a
real git checkout with real tags and a real HTTP server for each plant, with
`systemctl`, `uv`, `pg_dump` and `pg_restore` replaced by recorders - so the
order, the refusals and the rollback are proven without a deployment.
