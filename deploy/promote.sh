#!/usr/bin/env bash
# Promote the prod environment to a release tag, with backup and rollback.
#
#   promote.sh v0.1.1
#
# prod is a second checkout of this repository pinned to a tag, with its own
# registry, data and secrets under ~/.config/fsmes/prod (see deploy/README.md).
# This script is the only way prod changes: fetch the tag, sync the venv,
# regenerate the deterministic line data, stop the plants, migrate every
# database (each migration keeps a backup), start, and check health. Anything
# failing after the checkout rolls back to the previous tag and the backups.
set -euo pipefail

TAG=${1:?usage: promote.sh <tag>}
PROD=${FSMES_PROD_ROOT:-$HOME/Projects/fsmes-prod}
CONF=${FSMES_PROD_CONF:-$HOME/.config/fsmes/prod}
export FSMES_PLANT_REGISTRY=${FSMES_PLANT_REGISTRY:-$CONF/plants.toml}
UNIT=fsmes-prod-plant@

cd "$PROD"
PREV=$(git describe --tags --exact-match 2>/dev/null || git rev-parse --short HEAD)
DATA=$(python3 - "$FSMES_PLANT_REGISTRY" <<'PY'
import sys, tomllib, pathlib
reg = tomllib.load(open(sys.argv[1], "rb"))
print(pathlib.Path(reg.get("environment", {}).get("data_dir", "labs/multiplant/.data")).expanduser())
PY
)
PLANTS=$(python3 - "$FSMES_PLANT_REGISTRY" <<'PY'
import sys, tomllib
print(" ".join(tomllib.load(open(sys.argv[1], "rb"))["plants"]))
PY
)
STAMP=$(date +%Y%m%d-%H%M%S)

say() { printf '%s\n' "promote: $*"; }

rollback() {
  say "FAILED at '$1' - rolling back to $PREV"
  systemctl --user stop "${UNIT}*" || true
  git -c advice.detachedHead=false checkout -q "$PREV"
  uv sync -q && uv pip install -q -e ".[dev,mcp,agent,postgres]"
  for p in $PLANTS; do
    latest=$(ls -t "$DATA/$p.db.pre-migrate-"* 2>/dev/null | head -1 || true)
    if [ -n "$latest" ]; then cp -f "$latest" "$DATA/$p.db"; say "restored $p from $(basename "$latest")"; fi
  done
  for p in $PLANTS; do systemctl --user start "${UNIT}$p"; done
  say "prod is back at $PREV"
  exit 1
}

say "prod is at $PREV; promoting to $TAG"
git fetch -q --tags origin
git -c advice.detachedHead=false checkout -q "$TAG" || { say "no such tag: $TAG"; exit 2; }
uv sync -q && uv pip install -q -e ".[dev,mcp,agent,postgres]" || rollback "venv sync"

# The line data is deterministic and gitignored: regenerate it for this tag.
for line in $(python3 - "$FSMES_PLANT_REGISTRY" <<'PY'
import sys, tomllib, pathlib
reg = tomllib.load(open(sys.argv[1], "rb"))
for name, cfg in reg["plants"].items():
    if cfg.get("simulate", True):
        print(pathlib.Path(cfg["replay_dir"]).parent / "line.json")
PY
); do
  .venv/bin/fsmes sim-generate "$line" >/dev/null || rollback "sim-generate $line"
done

set -a; . "$CONF/env"; set +a
systemctl --user stop "${UNIT}*"
# A plant that has never run has no database yet: initialise it first.
# A plant on a server database (registry `database_url`) was made by hand
# and is only migrated.
FILE_PLANTS=$(python3 - "$FSMES_PLANT_REGISTRY" <<'PY'
import sys, tomllib
reg = tomllib.load(open(sys.argv[1], "rb"))
print(" ".join(n for n, c in reg["plants"].items() if not c.get("database_url")))
PY
)
for p in $FILE_PLANTS; do
  [ -f "$DATA/$p.db" ] || .venv/bin/fsmes plant "$p" init --root "$PROD" || rollback "init $p"
done
.venv/bin/fsmes plant all migrate --root "$PROD" || rollback "migrate"
for p in $PLANTS; do systemctl --user start "${UNIT}$p"; done
sleep 25
if .venv/bin/fsmes plant all status --root "$PROD" | grep -q "not answering"; then rollback "health check"; fi

# Keep the three newest backups per plant; the rest are noise.
for p in $PLANTS; do ls -t "$DATA/$p.db.pre-migrate-"* 2>/dev/null | tail -n +4 | xargs -r rm -f; done
say "prod is at $TAG ($STAMP)"
