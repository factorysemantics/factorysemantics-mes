#!/usr/bin/env bash
# Promote a fleet from one release tag to the next.
#
#   promote.sh v0.2.1
#
# prod is a second checkout of this repository pinned to a tag, with its own
# fleet file, packs, data and secrets under ~/.config/fsmes/prod (see
# deploy/README.md). This script is the only way prod changes.
#
# What it does, in the order it does it, and why that order:
#
#   1. Refuse a tag that is not on the remote it promotes from, and refuse a
#      local tag of that name that points somewhere else. Both happen before
#      anything stops, so the ordinary mistake costs no downtime.
#   2. Ask the product what this fleet is - `fsmes fleet plan --json` - rather
#      than reading the fleet file a second time in bash. The 0.2.0 script
#      read `plants`, a table the fleet file stopped having, and so could not
#      promote a fleet at all.
#   3. Stop every plant, *then* back it up: a SQLite file copied with its
#      write-ahead log, a PostgreSQL database dumped with `pg_dump -Fc`. A
#      backup taken from a running plant is a backup of a half-written file.
#      A plant whose database this cannot copy refuses the whole promote: a
#      promote it could not undo is not one to run.
#   4. Check out the tag, sync the venv, regenerate the deterministic line
#      data, apply each pack, start.
#   5. Ask each plant itself. `/health` must answer ok and call itself by the
#      name the fleet knows it by; `/pack` must say its schema is at head.
#      Read from the plant, never from a CLI in this shell that may be
#      pointed at a different database - a `db-status` that looked at the
#      wrong database is what rolled back a good promote on 2026-09-14 and
#      took the public demo dark for seventy seconds.
#   6. Any failure after the checkout puts the previous tag, the previous
#      venv and the backups *this run made* back, and starts the plants.
#
# Nothing here runs `CREATE DATABASE ... TEMPLATE`, and every PostgreSQL call
# carries `PGOPTIONS=-c statement_timeout=0`: the other half of that same
# incident was a rollback cancelled by the demo role's ten-second statement
# timeout. A step that a timeout can cancel is not a rollback.
#
# Environment:
#   FSMES_PROD_ROOT       the checkout            (default ~/Projects/fsmes-prod)
#   FSMES_PROD_CONF       fleet, packs, env       (default ~/.config/fsmes/prod)
#   FSMES_PLANT_REGISTRY  the fleet file          (default $FSMES_PROD_CONF/fleet.toml)
#   FSMES_PROMOTE_REMOTE  the git remote          (default public)
#   FSMES_PROMOTE_UNIT    systemd unit prefix     (default fsmes-prod-plant@)
#   FSMES_PROMOTE_KEEP    backups kept per plant  (default 3)
#   FSMES_PROMOTE_WAIT    seconds to wait for a plant to answer (default 90)
set -euo pipefail

# `git checkout <tag>` rewrites this file underneath the shell reading it, and
# bash reads a script from disk as it goes. Run from a copy of itself, once,
# so a promote is the script the operator started and not two halves of two.
if [ -z "${FSMES_PROMOTE_SELF:-}" ]; then
  self=$(mktemp "${TMPDIR:-/tmp}/fsmes-promote.XXXXXX")
  cat "$0" >"$self"
  FSMES_PROMOTE_SELF=$self exec bash "$self" "$@"
fi
trap 'rm -f "$FSMES_PROMOTE_SELF"' EXIT

[ "${BASH_VERSINFO[0]:-0}" -ge 4 ] || { echo "promote: this needs bash 4 or newer"; exit 2; }

TAG=${1:?usage: promote.sh <tag>}
PROD=${FSMES_PROD_ROOT:-$HOME/Projects/fsmes-prod}
CONF=${FSMES_PROD_CONF:-$HOME/.config/fsmes/prod}
export FSMES_PLANT_REGISTRY=${FSMES_PLANT_REGISTRY:-$CONF/fleet.toml}
REMOTE=${FSMES_PROMOTE_REMOTE:-public}
UNIT=${FSMES_PROMOTE_UNIT:-fsmes-prod-plant@}
KEEP=${FSMES_PROMOTE_KEEP:-3}
WAIT=${FSMES_PROMOTE_WAIT:-90}
STAMP=$(date +%Y%m%d-%H%M%S)
VENV_EXTRAS=".[dev,mcp,agent,postgres]"

FSMES=$PROD/.venv/bin/fsmes
PY=$PROD/.venv/bin/python
[ -x "$PY" ] || PY=$(command -v python3 || true)

say() { printf '%s\n' "promote: $*"; }
die() { say "$*"; exit 2; }

# --------------------------------------------------------------------------
# Reading the plan
#
# The plan carries the database passwords `pg_dump` needs, so it is held in a
# shell variable and handed to these readers on stdin. Never a file, never a
# command line: on this machine both are readable by anyone.
# --------------------------------------------------------------------------
PLAN_READER='
import json, sys

plan = json.load(sys.stdin)
mode, args = sys.argv[1], sys.argv[2:]

if mode == "totals":
    print(plan["count"], plan["can_back_up"], plan["data_dir"], sep="\t")
elif mode == "rows":
    for p in plan["plants"]:
        print(p["name"], p["pack"], p["health"], "yes" if p["simulate"] else "no",
              p["line_data"] or "", sep="\t")
elif mode == "refused":
    for p in plan["plants"]:
        if p["storage"]["backup"] == "none":
            print(p["name"], p["storage"].get("why", "no reason given"), sep="\t")
elif mode == "files":
    for p in plan["plants"]:
        if p["storage"]["backup"] == "copy":
            print(p["name"], p["storage"]["path"], sep="\t")
elif mode == "postgres":
    for p in plan["plants"]:
        s = p["storage"]
        if s["backup"] == "pg_dump":
            print(p["name"], s["host"], s["port"], s["user"], s["dbname"],
                  s.get("secret_password", ""), sep="\t")
elif mode == "url":
    for p in plan["plants"]:
        if p["name"] == args[0]:
            print(p["storage"].get("secret_url") or p["storage"].get("url") or "")
else:
    raise SystemExit("unknown mode " + mode)
'

# plan_read <plan json> <mode> [arg]
plan_read() { local plan=$1; shift; printf '%s' "$plan" | "$PY" -c "$PLAN_READER" "$@"; }

# --------------------------------------------------------------------------
# Asking the plants themselves
#
# Two public endpoints, no credential. `/health` says which plant answered: a
# plant that calls itself something else is a plant on a port this fleet has
# wrong, and reading its "ok" as this plant's would be inventing the check.
# `/pack` gives the schema revision against head; `at_head` that is not
# exactly true is a failure, because null there means the database did not
# answer and null is not "fine".
# --------------------------------------------------------------------------
HEALTH_READER='
import json, sys, time, urllib.error, urllib.request

plan = json.load(sys.stdin)
deadline = time.time() + float(sys.argv[1])
rows = plan["plants"]
wrong = {}


def ask(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def bad(name, sentence):
    wrong.setdefault(name, []).append(sentence)


for p in rows:
    name, base = p["name"], p["health"]
    said, why = None, None
    while True:
        try:
            said = ask(base + "/health")
            break
        except (urllib.error.URLError, OSError, ValueError) as exc:
            why = exc
            if time.time() >= deadline:
                break
            time.sleep(2)
    if said is None:
        bad(name, "never answered at " + base + "/health (" + str(why) + ")")
        continue
    if said.get("status") != "ok":
        bad(name, "/health says status " + repr(said.get("status")))
    if said.get("plant") != name:
        bad(name, "the plant at " + base + " calls itself " + repr(said.get("plant")))
    try:
        given = ask(base + "/pack")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        bad(name, base + "/pack did not answer (" + str(exc) + ")")
        continue
    schema = given.get("schema") or {}
    if schema.get("at_head") is not True:
        bad(name, "/pack says schema " + repr(schema.get("revision")) +
            ", head is " + repr(schema.get("head")))
    if name not in wrong:
        print("  " + name + ": answered at " + base + ", pack " + repr(given.get("pack")) +
              ", schema " + str(schema.get("revision")) + " at head")

print(str(len(rows)) + " plants asked, " + str(len(rows) - len(wrong)) +
      " answered as expected, " + str(len(wrong)) + " did not.")
for name in wrong:
    for sentence in wrong[name]:
        print("  " + name + ": " + sentence)
sys.exit(1 if wrong else 0)
'

# --------------------------------------------------------------------------
# Plants, backups, rollback
# --------------------------------------------------------------------------
declare -A BACKUP=()   # plant -> the backup this run made
declare -A KIND=()     # plant -> file | postgres
declare -A TARGET=()   # plant -> the database file, for a file plant

each_plant() { plan_read "$1" rows | cut -f1; }

stop_all() {
  local n
  for n in $(each_plant "$1"); do systemctl --user stop "${UNIT}${n}" || true; done
}

start_all() {
  local n
  for n in $(each_plant "$1"); do systemctl --user start "${UNIT}${n}" || true; done
}

sync_venv() { uv sync -q && uv pip install -q -e "$VENV_EXTRAS"; }

restore() {
  local n backup side host port user db pw
  for n in "${!BACKUP[@]}"; do
    [ "${KIND[$n]}" = file ] || continue
    backup=${BACKUP[$n]}
    cp -f "$backup" "${TARGET[$n]}"
    # The write-ahead log belongs to the database file it was taken with. An
    # old file beside a newer -wal is not this plant as it was; it is a
    # database that will not open.
    for side in wal shm; do
      if [ -f "$backup-$side" ]; then cp -f "$backup-$side" "${TARGET[$n]}-$side"
      else rm -f "${TARGET[$n]}-$side"
      fi
    done
    say "  restored $n from $(basename "$backup")"
  done
  while IFS=$'\t' read -r n host port user db pw; do
    backup=${BACKUP[$n]:-}
    [ -n "$backup" ] || continue
    if ( export PGOPTIONS='-c statement_timeout=0'
         if [ -n "$pw" ]; then export PGPASSWORD=$pw; fi
         pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction \
                    -h "$host" -p "$port" -U "$user" -d "$db" "$backup" ); then
      say "  restored $n from $(basename "$backup")"
    else
      say "  COULD NOT restore $n from $backup - it is still on disk; put it back by hand"
    fi
  done < <(plan_read "$PLAN" postgres)
}

rollback() {
  set +e
  say "FAILED at '$1' - rolling back to $PREV"
  stop_all "$PLAN"
  git -c advice.detachedHead=false checkout -q "$PREV"
  sync_venv
  restore
  start_all "$PLAN"
  say "prod is back at $PREV. The backups this run made are still on disk."
  exit 1
}

# --------------------------------------------------------------------------
# 1. The tag, before anything stops
# --------------------------------------------------------------------------
[ -n "$PY" ] || die "no python3 on this machine and no venv at $PROD/.venv"
cd "$PROD" || die "no checkout at $PROD - set FSMES_PROD_ROOT"
[ -x "$FSMES" ] || die "no fsmes at $FSMES - this checkout has never been set up"
[ -f "$CONF/env" ] || die "no environment file at $CONF/env - set FSMES_PROD_CONF"

PREV=$(git describe --tags --exact-match 2>/dev/null || git rev-parse --short HEAD)
say "prod is at $PREV; promoting to $TAG"

git remote get-url "$REMOTE" >/dev/null 2>&1 || die \
  "this checkout has no remote called '$REMOTE'. Name the one it promotes from in FSMES_PROMOTE_REMOTE."

# The fetch is allowed to fail here rather than ending the run, because the
# most likely reason it fails is the very thing the next three lines are for:
# a local tag of this name, from an older remote, that git will not clobber.
# `ls-remote` asks the remote directly, so the comparison stands either way,
# and a fetch that failed for any other reason is named in whichever refusal
# follows.
FETCH_SAID=$(git fetch --tags "$REMOTE" 2>&1) || FETCH_SAID="git fetch --tags $REMOTE: ${FETCH_SAID%%$'\n'*}"

REMOTE_SHA=$(git ls-remote --tags "$REMOTE" "refs/tags/$TAG^{}" | awk 'NR==1 {print $1}')
[ -n "$REMOTE_SHA" ] || REMOTE_SHA=$(git ls-remote --tags "$REMOTE" "refs/tags/$TAG" | awk 'NR==1 {print $1}')
[ -n "$REMOTE_SHA" ] || die "$REMOTE has no tag $TAG. A tag that is not on the remote is not a release. $FETCH_SAID"
LOCAL_SHA=$(git rev-parse -q --verify "refs/tags/$TAG^{commit}" || true)
[ -n "$LOCAL_SHA" ] || die "$REMOTE has $TAG, but this checkout still cannot resolve it. $FETCH_SAID"
[ "$LOCAL_SHA" = "$REMOTE_SHA" ] || die \
  "the tag $TAG here is $LOCAL_SHA and ${REMOTE}'s $TAG is $REMOTE_SHA. A local tag of that name from
         another remote is shadowing the one being promoted, and git will not overwrite it. Delete it
         here (git tag -d $TAG), then run this again."
[ -z "$FETCH_SAID" ] || say "$TAG is the one $REMOTE has, but $FETCH_SAID"

# --------------------------------------------------------------------------
# 2. What this fleet is, from the product's own reader
# --------------------------------------------------------------------------
PLAN=$("$FSMES" fleet plan --json --with-password --root "$PROD") || die \
  "this checkout's fsmes could not read $FSMES_PLANT_REGISTRY as a fleet. Run
         \`$FSMES fleet plan --root $PROD\` to see what it says."
IFS=$'\t' read -r COUNT BACKABLE DATA < <(plan_read "$PLAN" totals)
say "$COUNT plants in $FSMES_PLANT_REGISTRY; $BACKABLE of them this can back up; data in $DATA"
[ "$COUNT" -gt 0 ] || die "the fleet lists no plants, so there is nothing to promote."

REFUSED=$(plan_read "$PLAN" refused)
if [ -n "$REFUSED" ]; then
  say "refusing: a promote it could not undo is not one to run."
  while IFS=$'\t' read -r n why; do say "  $n: $why"; done <<<"$REFUSED"
  exit 2
fi

# --------------------------------------------------------------------------
# 3. Stop, then back up
# --------------------------------------------------------------------------
say "stopping $COUNT plants"
for n in $(each_plant "$PLAN"); do
  systemctl --user stop "${UNIT}${n}" || { start_all "$PLAN"; die "could not stop ${UNIT}${n}"; }
done

say "backing up before anything migrates"
while IFS=$'\t' read -r n path; do
  if [ ! -f "$path" ]; then
    say "  $n: no database at $path yet - nothing to back up"
    continue
  fi
  out="$path.pre-migrate-$STAMP"
  cp -p "$path" "$out" || { start_all "$PLAN"; die "could not copy $path"; }
  for side in wal shm; do
    if [ -f "$path-$side" ]; then cp -p "$path-$side" "$out-$side"; fi
  done
  BACKUP[$n]=$out; KIND[$n]=file; TARGET[$n]=$path
  say "  $n: $out"
done < <(plan_read "$PLAN" files)

while IFS=$'\t' read -r n host port user db pw; do
  out="$DATA/$n.pre-migrate-$STAMP.dump"
  ( export PGOPTIONS='-c statement_timeout=0'
    if [ -n "$pw" ]; then export PGPASSWORD=$pw; fi
    pg_dump -Fc -h "$host" -p "$port" -U "$user" -d "$db" -f "$out" ) \
    || { start_all "$PLAN"; die "pg_dump of $n ($db) failed - nothing has been changed"; }
  BACKUP[$n]=$out; KIND[$n]=postgres
  say "  $n: $out"
done < <(plan_read "$PLAN" postgres)

# --------------------------------------------------------------------------
# 4. The tag, the venv, the line data, the packs
# --------------------------------------------------------------------------
git -c advice.detachedHead=false checkout -q "$TAG" || rollback "checkout $TAG"
sync_venv || rollback "venv sync"

PLAN_AT_TAG=$("$FSMES" fleet plan --json --with-password --root "$PROD") \
  || rollback "reading the fleet with ${TAG}'s fsmes"

# The line data a simulated plant replays is deterministic and gitignored, so
# it is regenerated for the tag being promoted to rather than carried across.
while IFS=$'\t' read -r n pack health sim line; do
  if [ "$sim" = yes ] && [ -n "$line" ]; then
    "$FSMES" sim-generate "$line" >/dev/null || rollback "sim-generate for $n"
  fi
done < <(plan_read "$PLAN_AT_TAG" rows)

set -a; . "$CONF/env"; set +a

# `fsmes pack apply` checks the pack, adopts its settings, brings the schema
# to head and seeds what the pack carries. The data directory and the
# resolved database URL are exported for it, because a pack that names
# neither would otherwise be applied to whatever this shell's default
# database is. A pack that *does* name `[storage] database_url` overrides
# what is exported here - which is why step 5 asks the plant rather than
# believing this step.
say "applying $COUNT packs"
while IFS=$'\t' read -r n pack health sim line; do
  url=$(plan_read "$PLAN_AT_TAG" url "$n")
  ( export FSMES_DATA_DIR=$DATA
    if [ -n "$url" ]; then export MES_DATABASE_URL=$url; fi
    "$FSMES" pack apply "$pack" ) || rollback "pack apply for $n"
done < <(plan_read "$PLAN_AT_TAG" rows)

for n in $(each_plant "$PLAN_AT_TAG"); do
  systemctl --user start "${UNIT}${n}" || rollback "starting ${UNIT}${n}"
done

# --------------------------------------------------------------------------
# 5. Ask the plants
# --------------------------------------------------------------------------
say "asking each plant who it is and what schema it is at (up to ${WAIT}s each)"
printf '%s' "$PLAN_AT_TAG" | "$PY" -c "$HEALTH_READER" "$WAIT" || rollback "health check"

# --------------------------------------------------------------------------
# 6. Keep the newest backups; the rest are noise
# --------------------------------------------------------------------------
prune() {
  local old
  while IFS= read -r old; do
    rm -f "$old" "$old-wal" "$old-shm"
  done < <(ls -t "$1"*[0-9] "$1"*.dump 2>/dev/null | tail -n "+$((KEEP + 1))")
}
while IFS=$'\t' read -r n path; do prune "$path.pre-migrate-"; done < <(plan_read "$PLAN" files)
while IFS=$'\t' read -r n rest; do prune "$DATA/$n.pre-migrate-"; done < <(plan_read "$PLAN" postgres)

say "prod is at $TAG ($STAMP), $COUNT plants answering."
