# Deploy

*Tutorial. Two ways: Docker Compose for a trial on one box, systemd units for a host that runs test and promoted environments side by side.*

!!! warning "Not hardened for the public internet"
    The MES is designed to sit on a plant network behind a firewall. Put a
    reverse proxy with TLS in front of it, set the passwords, and do not
    expose the MCP servers. See [security](security.md).

## Compose

```bash
git clone https://github.com/factorysemantics/factorysemantics-mes.git
cd factorysemantics-mes
MES_SECRET_KEY="$(openssl rand -hex 32)" MES_ADMIN_PASSWORD='choose-one' \
  docker compose -f docker/docker-compose.yml up --build
```

That starts PostgreSQL, a one-shot migration and seed job, the API behind
nginx on <http://localhost:8000>, the simulated OPC UA server, the OPC agent,
the mock ERP on port 8001 and the ERP sync worker. `--scale api=3` adds API
replicas; migrations run once in their own container so replicas never race.

To leave twin mode, point `MES_OPC_ENDPOINT` at your server and
`MES_TAG_MAP_FILE` at the tag map the worksheet produced, and set
`MES_ERP_MODE` to `erpnext`, `file`, or `off`.

The image is `ghcr.io/factorysemantics/fsmes` (amd64 and arm64), signed with
cosign and shipped with an SBOM from each release.

## systemd: test and promoted environments on one host

Two environments run from the same code on the same host; the only
difference is a registry file outside the checkout. The unit files and a
promote script are in `deploy/`.

| | test | promoted |
|---|---|---|
| Checkout | your working clone on `main` | a second clone pinned to a release tag |
| Registry | `labs/multiplant/plants.toml` in the repo | `~/.config/fsmes/prod/plants.toml`, mode 600 |
| Accounts | lab accounts, lab passwords | the registry's `accounts`, passwords from `~/.config/fsmes/prod/env` |
| Units | `fsmes-plant@<name>` | `fsmes-prod-plant@<name>` |
| Changes | every merge | `deploy/promote.sh <tag>` only |

First-time setup:

```bash
git clone https://github.com/factorysemantics/factorysemantics-mes.git ~/Projects/fsmes-prod
mkdir -p ~/.config/fsmes/prod && chmod 700 ~/.config/fsmes/prod
# write plants.toml and env (see the registry how-to); chmod 600 both
cp ~/Projects/fsmes-prod/deploy/fsmes-prod-plant@.service ~/.config/systemd/user/
systemctl --user daemon-reload
~/Projects/fsmes-prod/deploy/promote.sh v0.1.0
systemctl --user enable --now fsmes-prod-plant@<name>
```

`promote.sh` checks out the tag, backs up every database, migrates, and on
any failure puts the previous tag and the backups back before exiting
non-zero. A password variable that is not set refuses the promote rather
than creating an account with an empty password.

## Verify

- `curl http://127.0.0.1:<port>/health` answers.
- The log has **no** `well-known credentials in use` warning.
- `fsmes plant all status` (with `FSMES_PLANT_REGISTRY` set) lists every
  plant as alive.

## See also

- [Plants from a registry](registry.md)
- [Settings reference](../reference/settings.md)
- [Security](security.md)
