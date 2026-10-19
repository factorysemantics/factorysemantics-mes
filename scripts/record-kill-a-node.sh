#!/usr/bin/env bash
# Record two nodes of one plant side by side while one of them is killed.
#
#   bash scripts/record-kill-a-node.sh
#
# What it does, in words:
#
#   1. brings up its own `docker compose` project - one PostgreSQL, the machine
#      layer, node a and node b - with each node on its own address on the host
#      so a browser can watch both at once
#   2. waits until both nodes answer, one of them holds the `reader` lease, the
#      counts are moving, and the plant has been watching long enough to report
#      over the window the dashboards are set to - a plant started ten minutes
#      ago withholds every figure the window needs, says so on every card, and
#      makes a dull picture
#   3. films one browser page: each node's Line view side by side, a caption
#      bar over them, and a terminal strip underneath carrying the output of
#      the real `docker` commands it runs
#   4. `docker kill` on whichever node holds the reader - not always a - then
#      `docker start` on it again
#   5. turns the raw video into an mp4 and a GIF, and prints the seconds it
#      measured and what each file weighs
#
# `bash scripts/kill-a-node.sh` is the one that *checks* a failover and is the
# one to trust; this one only shows it. They bring up the same plant from the
# same compose file and neither is in the product: the MES knows nothing about
# containers and nothing in the wheel uses this code.
#
# The recording is re-made after a release so the picture in the README is of
# the version people install. The published GIF's caption says which two
# things on screen are staged - the enlarged yellow header line and the grey
# "no answer" panel - and `scripts/record-kill-a-node.py` says why.
#
# Needed on the machine: `docker`, `bash`, `ffmpeg`, and a Python with
# Playwright and its Chromium (`pip install -e ".[dev]" && playwright install
# chromium`). Windows is not asked of it: it is a developer tool for making a
# picture, not part of the product, and the product's own Windows support is
# what CI proves.
#
# It runs under its own compose project name and never touches the plain
# `mes-two-nodes` demo. If the ports it wants are busy it says so and stops.
#
# Options:
#   --out DIR       where to write (default: recordings/kill-a-node)
#   --hours N       the window the two dashboards report over (default: 1)
#   --warm-up MIN   minutes the plant must have been watching before filming
#                   (default: the whole window and two minutes over, so every
#                   card reads "watched 100%"; 80% of the window is the least
#                   the screens will report a figure on at all, so a shorter
#                   wait than the default still works; 0 to film straight away)
#   --port-a N      host port for node a (default: 8001)
#   --port-b N      host port for node b (default: 8002)
#   --project NAME  compose project name (default: mes-record)
#   --python PATH   the Python that has Playwright (default: $PYTHON, or python3)
#   --build         rebuild the image first
#   --down          remove the containers and the volumes when it is over
#   --keep-raw      keep the raw video as well as the mp4 and the GIF
#   --quiet         fewer lines while it waits

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REDUNDANT="$HERE/docker/docker-compose.redundant.yml"
OVERRIDE="$HERE/docker/docker-compose.record.yml"

OUT="$HERE/recordings/kill-a-node"
PORT_A=8001
PORT_B=8002
# An hour, not the screens' usual eight: the recording is made on a plant
# somebody has just started, and a figure is reported only once the MES has
# watched 80% of the window it is asked about. An hour means a warm-up of
# about an hour instead of a working day.
HOURS=1
WARM_UP=
PROJECT=mes-record
PYTHON="${PYTHON:-python3}"
BUILD=0; DOWN=0; KEEP_RAW=0; QUIET=0

# The GIF the README can carry: GitHub renders an image up to 10 MB and refuses
# to animate one over it, so a GIF that comes out heavy is re-made smaller
# rather than committed broken. First setting that fits wins; the run says which.
GIF_LIMIT_BYTES=$((10 * 1024 * 1024))
GIF_SETTINGS=("7 1100" "6 1100" "6 1000" "5 900" "5 800")
# The first seconds are the two dashboards drawing themselves, which is nothing
# to look at. The mp4 keeps them; the GIF, which has to tell its story in a
# loop, does not.
GIF_SKIP_SECONDS=4

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --hours) HOURS="$2"; shift 2 ;;
    --warm-up) WARM_UP="$2"; shift 2 ;;
    --port-a) PORT_A="$2"; shift 2 ;;
    --port-b) PORT_B="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    --build) BUILD=1; shift ;;
    --down) DOWN=1; shift ;;
    --keep-raw) KEEP_RAW=1; shift ;;
    --quiet) QUIET=1; shift ;;
    -h|--help) sed -n '2,57p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

DC=(docker compose -p "$PROJECT" -f "$REDUNDANT" -f "$OVERRIDE")
export FSMES_RECORD_PORT_A="$PORT_A" FSMES_RECORD_PORT_B="$PORT_B"

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
note() { [ "$QUIET" = 1 ] || printf '   %s\n' "$*"; }
fail() { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------ what it needs

step "checking what this needs"

command -v docker >/dev/null || fail "no \`docker\` on PATH"
docker info >/dev/null 2>&1 || fail \
  "\`docker info\` was refused. If this shell is not in the docker group, run the
         script through one:  printf 'bash scripts/record-kill-a-node.sh\\n' | newgrp docker"
command -v ffmpeg >/dev/null || fail "no \`ffmpeg\` on PATH; it makes the mp4 and the GIF"
command -v "$PYTHON" >/dev/null || fail "no \`$PYTHON\` on PATH; pass --python PATH"
"$PYTHON" -c 'import playwright' 2>/dev/null || fail \
  "\`$PYTHON\` has no Playwright. In a checkout:  python -m venv .venv &&
         .venv/bin/pip install -e \".[dev]\" && .venv/bin/playwright install chromium
         then pass  --python .venv/bin/python"
say "docker, ffmpeg and a Python with Playwright are all here"

# A port this script wants but does not own is the one thing that silently
# records the wrong plant. Say which port, and name the demo that usually has
# it, rather than letting `up` fail three screens later.
for pair in "a $PORT_A" "b $PORT_B"; do
  read -r which port <<<"$pair"
  if "$PYTHON" - "$port" <<'PY'
import socket, sys
probe = socket.socket()
probe.settimeout(1)
raise SystemExit(0 if probe.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PY
  then
    mine="$("${DC[@]}" ps -q node-"$which" 2>/dev/null || true)"
    [ -n "$mine" ] && continue  # it is this project's own node, from an earlier run
    fail "something already answers on 127.0.0.1:$port, which node $which wants.
         The plain two-node demo publishes 8001 and 8002 when it is run for a
         recording. Put that one down, or give this one other ports:
           bash scripts/record-kill-a-node.sh --port-a 8003 --port-b 8004"
  fi
done
say "127.0.0.1:$PORT_A and 127.0.0.1:$PORT_B are free for node a and node b"

# ---------------------------------------------------------------- the plant

step "bringing up project '$PROJECT': PostgreSQL, the machine layer, node a, node b"
if [ "$BUILD" = 1 ]; then "${DC[@]}" build; fi
"${DC[@]}" up -d node-a node-b

name_of() {  # $1 = service -> the container name a reader will see in `docker ps`
  local id
  id="$("${DC[@]}" ps -q "$1")"
  [ -n "$id" ] || fail "service $1 is not running in project '$PROJECT'"
  docker inspect --format '{{.Name}}' "$id" | sed 's|^/||'
}

health() {  # $1 = node port -> "<node> <reader> <floor> <housekeeping>", or "down - - -"
  "$PYTHON" - "$1" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=5) as answer:
        body = json.load(answer)
except Exception:
    print("down - - -")
    raise SystemExit(0)
node = body.get("node") or {}
held = node.get("holders") or {}
print(node.get("name") or "-", held.get("reader") or "-",
      held.get("floor") or "-", held.get("housekeeping") or "-")
PY
}

sql() {  # one value, asked from inside the database's own container
  "${DC[@]}" exec -T postgres psql -U mes -d mes_two_nodes -A -t -c "$1" 2>/dev/null | tr -d ' \r'
}

step "waiting for both nodes to answer and one of them to hold the reader"
reader="-"
for _ in $(seq 1 120); do
  read -r who_a reader_a _ _ <<<"$(health "$PORT_A")"
  read -r who_b reader_b _ _ <<<"$(health "$PORT_B")"
  note "node a says '$who_a' (reader $reader_a), node b says '$who_b' (reader $reader_b)"
  case "$who_a$who_b$reader_a" in
    aba|abb) reader="$reader_a"; break ;;
  esac
  sleep 2
done
case "$reader" in
  a|b) say "both nodes answer; node $reader reads the machines" ;;
  *) fail "both nodes did not answer with one of them holding the reader within four minutes" ;;
esac

step "waiting for the counts to move, so the kill interrupts something"
booked_before="$(sql "select coalesce(sum(good_qty + scrap_qty), 0) from production_logs where source = 'opc'")"
moved=0
for _ in $(seq 1 60); do
  booked_now="$(sql "select coalesce(sum(good_qty + scrap_qty), 0) from production_logs where source = 'opc'")"
  note "booked from the machines: ${booked_now:-?}"
  if [ "${booked_now:-0}" -gt "${booked_before:-0}" ] && [ "${booked_now:-0}" -gt 0 ]; then moved=1; break; fi
  sleep 2
done
[ "$moved" = 1 ] || fail "nothing was booked while both nodes stood up, so a kill would show nothing"
say "the plant is counting: $booked_now units booked from the machines"

# The screens report a figure only once this MES has watched most of the window
# it is being asked about, and say "withheld" until then - which is the right
# answer and the wrong picture. So wait for it. The plant's memory is its
# database, so how long it has been watching is how long its PostgreSQL has
# been up: a node killed and started again does not reset that, and a second
# run against a plant that is already warm waits for nothing.
# The whole window, and two minutes over. 80% of it is all the screens ask
# for before they report a figure, but the clock they count is their own -
# it starts when the nodes begin reading the machines, which is a minute or
# so after the database is up - and at exactly 80% the cards came out at
# 78.8%, withheld, on 2026-10-19. Waiting the window out also makes every
# card read "watched 100%", which is the picture that was approved.
[ -n "$WARM_UP" ] || WARM_UP="$("$PYTHON" -c 'import sys; print(round(float(sys.argv[1]) * 60) + 2)' "$HOURS")"
step "waiting until the plant has been watching $WARM_UP minutes, for a $HOURS-hour window"
note "that is most of an hour on the default window; nothing is wrong"
watching() {  # minutes since this plant's database came up
  "$PYTHON" - "$(docker inspect --format '{{.State.StartedAt}}' "$(name_of postgres)")" <<'PY'
import datetime, sys
up = datetime.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
print(round((datetime.datetime.now(datetime.UTC) - up).total_seconds() / 60))
PY
}
minutes="$(watching)"
while [ "$minutes" -lt "$WARM_UP" ]; do
  note "watching for $minutes of the $WARM_UP minutes it needs"
  sleep 30
  minutes="$(watching)"
done
say "the plant has been watching $minutes minutes; the screens report over $HOURS hour(s)"

# ------------------------------------------------------------- the filming

step "filming"
mkdir -p "$OUT"
rm -rf "$OUT/raw" "$OUT/stage"
"$PYTHON" "$HERE/scripts/record-kill-a-node.py" \
  --port-a "$PORT_A" --port-b "$PORT_B" \
  --container-a "$(name_of node-a)" --container-b "$(name_of node-b)" \
  --out "$OUT" --line "${FSMES_RECORD_LINE:-SIMLINE}" --hours "$HOURS"

MARKS="$OUT/marks.json"
[ -f "$MARKS" ] || fail "the recorder wrote no $MARKS"
read_mark() { "$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]) or "")' "$MARKS" "$1"; }
RAW="$(read_mark video)"
HOLDER="$(read_mark holder)"
SURVIVOR="$(read_mark survivor)"
TOOK="$(read_mark took)"
BACK="$(read_mark back)"
[ -s "$RAW" ] || fail "the recorder named a video that is not there: $RAW"

# --------------------------------------------------------------- the files

step "making the mp4 and the GIF"
MP4="$OUT/kill-a-node.mp4"
GIF="$OUT/kill-a-node.gif"

ffmpeg -nostdin -loglevel error -y -i "$RAW" \
  -vf "fps=15,scale=1280:-2" -c:v libx264 -crf 28 -pix_fmt yuv420p -movflags +faststart \
  "$MP4"
say "mp4: $MP4 ($(du -h "$MP4" | cut -f1))"

gif_fits=""
for setting in "${GIF_SETTINGS[@]}"; do
  read -r fps width <<<"$setting"
  ffmpeg -nostdin -loglevel error -y -ss "$GIF_SKIP_SECONDS" -i "$RAW" -filter_complex \
    "fps=$fps,scale=$width:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3" \
    "$GIF"
  bytes="$(wc -c < "$GIF")"
  note "$fps fps at ${width} wide: $((bytes / 1024 / 1024)) MB"
  if [ "$bytes" -le "$GIF_LIMIT_BYTES" ]; then
    gif_fits="$fps fps, ${width} wide"
    break
  fi
done
[ -n "$gif_fits" ] || fail "no setting brought the GIF under 10 MB; the recording may be too long"
say "gif: $GIF ($(du -h "$GIF" | cut -f1), $gif_fits)"

if [ "$KEEP_RAW" = 0 ]; then
  rm -rf "$OUT/raw"
else
  say "raw: $RAW"
fi
rm -rf "$OUT/stage"

# ------------------------------------------------------------ what it showed

step "what this recording shows"
say "  node $HOLDER held the reader and was killed; node $SURVIVOR took it after ${TOOK:-?} s"
say "  node $HOLDER answered again ${BACK:-?} s after \`docker start\`, holding nothing"
say "  $booked_now units were booked from the machines before the kill"
say "  watch it:  mpv --loop $MP4"
say "  the marks, second by second, are in $MARKS"
say ""
say "Two things on screen are staged, and the caption on any published copy has"
say "to say so: the yellow line is the dashboard's own \"served by ... machines"
say "read by ...\" header, enlarged; the grey \"no answer\" panel is drawn by the"
say "recorder over a page that is really greying itself and saying \"reconnecting\"."

if [ "$DOWN" = 1 ]; then
  step "removing project '$PROJECT' and its volumes"
  "${DC[@]}" down -v
else
  say ""
  say "The plant is still running. To stop it and throw the data away:"
  say "  docker compose -p $PROJECT -f docker/docker-compose.redundant.yml -f docker/docker-compose.record.yml down -v"
fi
