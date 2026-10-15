#!/usr/bin/env bash
# Kill one node of a plant that runs on two, and check the other one carried on.
#
#   bash scripts/kill-a-node.sh
#
# What it does, in words:
#
#   1. brings up one PostgreSQL, the machine layer, node a and the proxy
#   2. waits until node a holds all three roles and the counts are moving
#   3. brings up node b, which stands by holding nothing
#   4. `docker compose kill node-a` - no shutdown, no goodbye, like a power cut
#   5. watches /health *through the proxy* until node b reads the machines,
#      counting every request the one address refused while that happened
#   6. measures the takeover in lease units, from the database's own clock
#   7. checks the counters kept moving and that nothing was booked twice
#   8. starts node a again and shows it standing by, holding nothing
#
# It is a shell script and not an `fsmes` subcommand on purpose: everything it
# does is `docker compose` and SQL, and `docker compose` is not the product's
# business. The MES knows nothing about containers, nothing in the wheel would
# use this code, and a plant engineer reading it can see every command it runs.
# The checks themselves are the same ones
# `tests/test_a_plant_on_two_nodes_survives_one_of_them_dying.py` makes, in
# SQL instead of SQLAlchemy.
#
# Nothing but `docker compose` and `bash` is needed on the machine: every
# question is asked from inside a container.
#
# Options:
#   --build   rebuild the image first
#   --down    remove the containers and the volumes when it is over
#   --quiet   fewer lines while it waits

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FILE="$HERE/docker/docker-compose.redundant.yml"
DC=(docker compose -f "$FILE")

# The bound from decision 0042, and the one the test in CI holds: a role moves
# within three leases of its holder dying. Three and not one because the
# surviving node has to notice, take the lease, and connect to the machines.
BOUND_LEASE_UNITS=3.0

BUILD=0; DOWN=0; QUIET=0
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    --down) DOWN=1 ;;
    --quiet) QUIET=1 ;;
    -h|--help) sed -n '2,33p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
note() { [ "$QUIET" = 1 ] || printf '   %s\n' "$*"; }
fail() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- asking things

# The node block of /health, as five words: which node answered, who holds
# each of the three roles, and the lease length. A role nobody holds is "-".
# Asked from inside a container, so the host needs no curl and no jq.
health() {  # $1 = service to ask from, $2 = url
  "${DC[@]}" exec -T "$1" python -c '
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=5) as answer:
        body = json.load(answer)
except Exception:
    print("down - - - -")
    raise SystemExit(0)
node = body.get("node") or {}
held = node.get("holders") or {}
print(node.get("name") or "-",
      held.get("reader") or "-",
      held.get("floor") or "-",
      held.get("housekeeping") or "-",
      node.get("lease_seconds") or "-")
' "$2" 2>/dev/null || echo "down - - - -"
}

sql() {  # one row, fields separated by |
  "${DC[@]}" exec -T postgres psql -U mes -d mes_two_nodes -A -t -F'|' -c "$1" 2>/dev/null
}

# Everything this plant has booked from the machines, and everything its
# counters moved, read in one transaction so the two are of the same instant.
booked_and_counted() {
  # One statement, so both halves are of the same instant: a booking and the
  # baseline it leaves behind commit together, which is the whole reason a
  # second reader cannot book the same units twice.
  sql "select 'booked', e.code, coalesce(sum(p.good_qty + p.scrap_qty), 0)
         from equipment e left join production_logs p
           on p.equipment_id = e.id and p.source = 'opc'
        group by e.code
       union all
       select 'counted', equipment_code, sum(value) from counter_baselines group by 2"
}

total_booked() {
  sql "select coalesce(sum(good_qty + scrap_qty), 0) from production_logs where source = 'opc'" \
    | tr -dc '0-9'
}

# -------------------------------------------------------------------- the plant

step "bringing up PostgreSQL, the machine layer, node a and the proxy"
if [ "$BUILD" = 1 ]; then "${DC[@]}" build; fi
"${DC[@]}" up -d node-a proxy

step "waiting for node a to hold all three roles"
reader=""; lease=""
for _ in $(seq 1 120); do
  read -r who reader floor keeper lease <<<"$(health node-a http://127.0.0.1:8010/health)"
  note "node $who: reader $reader, floor $floor, housekeeping $keeper"
  [ "$reader" = "a" ] && [ "$floor" = "a" ] && [ "$keeper" = "a" ] && break
  sleep 2
done
[ "$reader" = "a" ] || fail "node a never took the three roles (reader: $reader)"
say "node a holds reader, floor and housekeeping; the lease is ${lease}s"

step "waiting for the counts to move, so the kill proves something"
before_total="$(total_booked)"
moved=0
for _ in $(seq 1 60); do
  now="$(total_booked)"
  note "booked from the machines: $now"
  if [ "${now:-0}" -gt "${before_total:-0}" ] && [ "${now:-0}" -gt 0 ]; then moved=1; break; fi
  sleep 2
done
[ "$moved" = 1 ] || fail "nothing was booked while node a was alone, so there is nothing to continue"
say "the plant is counting: $now units booked from the machines"

step "bringing up node b, which should stand by holding nothing"
"${DC[@]}" up -d node-b
for _ in $(seq 1 60); do
  read -r who r f k l <<<"$(health node-b http://127.0.0.1:8010/health)"
  note "node $who: reader $r, floor $f, housekeeping $k"
  [ "$who" = "b" ] && break
  sleep 2
done
[ "$who" = "b" ] || fail "node b never answered its own /health"
[ "$r" = "a" ] || fail "node b says the reader is '$r'; it should still be a"
say "node b serves the dashboard and holds nothing; a still reads the machines"

baseline="$(booked_and_counted)"

# --------------------------------------------------------------------- the kill

step "killing node a - no shutdown, like a power cut"
"${DC[@]}" kill node-a

# The lease row is frozen the moment a dies: it cannot renew, and b has not
# taken it yet. That renewal is the last thing the dead node ever said, and
# the takeover is measured from it.
killed_at="$(sql "select holder || '|' || extract(epoch from renewed_at) || '|' || renewed_at
                    from plant_leases where role = 'reader'")"
dead_holder="$(printf '%s' "$killed_at" | cut -d'|' -f1)"
last_renewal="$(printf '%s' "$killed_at" | cut -d'|' -f2)"
note "the reader lease was last renewed by '$dead_holder' at $(printf '%s' "$killed_at" | cut -d'|' -f3)"

step "watching /health through the one address until b reads the machines"
refusals=0; took=""
for _ in $(seq 1 90); do
  read -r who r f k l <<<"$(health node-b http://proxy/health)"
  if [ "$who" = "down" ]; then
    refusals=$((refusals + 1))
    note "the one address refused a request"
  else
    note "served by $who: reader $r, floor $f, housekeeping $k"
    [ "$r" = "b" ] && { took="yes"; break; }
  fi
  sleep 1
done
[ "$took" = "yes" ] || fail "node b never took the reader lease after a died"

units="$(sql "select round(((extract(epoch from acquired_at) - $last_renewal)
                           / ${lease:-6})::numeric, 2)
                from plant_leases where role = 'reader' and holder = 'b'" | tr -d ' ')"
if [ "$dead_holder" != "a" ]; then
  say "takeover: not measured - node b had taken the lease before the first look"
  units=""
else
  say "takeover: $units lease units (the bound is $BOUND_LEASE_UNITS, one lease is ${lease}s)"
  over="$(sql "select case when $units > $BOUND_LEASE_UNITS then 'over' else 'within' end" | tr -d ' ')"
  [ "$over" = "within" ] || fail "the takeover took $units lease units; the bound is $BOUND_LEASE_UNITS"
fi
say "the one address refused $refusals requests during the failover (of $((refusals + 1)) asked)"
[ "$refusals" -le 1 ] || fail "the one address refused $refusals requests; a dead node should cost at most one"

# ------------------------------------------------------------- exactly once

step "letting the survivor book for three leases, then checking the counts"
sleep "$(sql "select ceil(${lease:-6} * 3)" | tr -dc '0-9')"
after="$(booked_and_counted)"

"${DC[@]}" exec -T node-b python - "$baseline" "$after" <<'PY' || fail "the counts do not add up - see the lines above"
import sys

def rows(text):
    out = {}
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3 and parts[0] in ("booked", "counted"):
            out[(parts[0], parts[1])] = int(parts[2])
    return out

before, after = rows(sys.argv[1]), rows(sys.argv[2])
machines = sorted({code for kind, code in after if kind == "counted"})
if not machines:
    print("no machine counted anything: this proves nothing")
    raise SystemExit(1)
bad = 0
print(f"   {len(machines)} machines counted across the failover:")
for code in machines:
    booked = after.get(("booked", code), 0) - before.get(("booked", code), 0)
    counted = after.get(("counted", code), 0) - before.get(("counted", code), 0)
    mark = "ok"
    if counted <= 0:
        mark, bad = "counted nothing (the replay may have wrapped)", bad + 1
    elif booked != counted:
        mark, bad = "BOOKED TWICE" if booked > counted else "LOST A BASELINE", bad + 1
    print(f"   {code}: booked {booked}, counters moved {counted} - {mark}")
print(f"   {len(machines) - bad} of {len(machines)} machines booked exactly what they counted")
raise SystemExit(1 if bad else 0)
PY

duplicates="$(sql "select count(*) - count(distinct (equipment_id, tag, ts)) from tag_values" | tr -d ' ')"
say "$duplicates duplicate readings in tag_values (a reader taking over must not re-store a moment the dead one had)"
[ "$duplicates" = "0" ] || fail "$duplicates readings are in tag_values twice"

# ------------------------------------------------------------ and back again

step "starting node a again - it should stand by, holding nothing"
"${DC[@]}" start node-a
back=""
for _ in $(seq 1 60); do
  read -r who r f k l <<<"$(health node-a http://127.0.0.1:8010/health)"
  note "node $who: reader $r, floor $f, housekeeping $k"
  [ "$who" = "a" ] && { back="yes"; break; }
  sleep 2
done
[ "$back" = "yes" ] || fail "node a never came back"
[ "$r" = "b" ] || fail "node a came back and took the reader from b (it says '$r')"
say "node a serves the dashboard again and holds nothing; b still reads the machines"

step "what this run showed"
say "  the plant kept counting while one of its two nodes was killed"
say "  the reader moved to the survivor in ${units:-(not measured)} lease units (bound $BOUND_LEASE_UNITS)"
say "  $duplicates readings booked twice, and every machine booked what it counted"
say "  the one address refused $refusals requests"
say "  the dashboard is on http://localhost:8000/dashboard"

if [ "$DOWN" = 1 ]; then
  step "removing the containers and the volumes"
  "${DC[@]}" down -v
else
  say ""
  say "The plant is still running. To stop it and throw the data away:"
  say "  docker compose -f docker/docker-compose.redundant.yml down -v"
fi
