#!/usr/bin/env bash
# Prove `fsmes score bottling` has a line to read.
#
# It did not, from 2026-09-13 until 2026-09-14. The bottling pack carried no
# master data - its six stations are the product's own reference line, seeded
# by `fsmes seed-kepsim` - and the lab script that seeded them stopped being
# named by the registry. A scored run builds an ephemeral plant by applying
# the pack and nothing else, so that plant had no machines and the runner's
# first read answered
#
#     404 {"detail": "no line has any machines on it yet - seed a plant first"}
#
# Nothing in the test suite could catch it: the suite deliberately starts no
# plant, no OPC server and no port, and a scored run is all three. So it is
# here, beside the other check that needs real processes, and it asks the two
# questions `demo_from_wheel.sh` asks - did it exit cleanly, *and* does what it
# wrote say it actually did the work.
#
# Usage: score_bottling.sh [speed]
set -euo pipefail

# 60x replays the scripted hour in about a minute. The scorecard's own
# `observation.max_speed_for_full_resolution` is 60, so this is the fastest
# speed at which every scripted event is still resolvable - faster would score
# a run whose events the sampler could not have seen, which is not a score.
speed="${1:-60}"
out="$(mktemp -d)/bottling.score.json"

# The line data is generated, never committed: 1.2 MB of per-second CSVs that
# `fsmes sim-generate` rebuilds byte-identically from seed 42.
fsmes sim-generate labs/kepsim/line.json

fsmes score bottling --speed "$speed" --out "$out"

python - "$out" <<'PY'
import json, sys

card = json.load(open(sys.argv[1]))
metrics = card["metrics"]
problems = []

# The regression this file exists for. A plant with no machines scores
# nothing, so "nothing was scored" and "everything passed" must not look the
# same from here.
if metrics["faults_scripted"] < 2:
    problems.append(f"the run scored {metrics['faults_scripted']} scripted faults, "
                    "which means the line it replayed was not the bottling line")
if metrics["planned_stops_scripted"] < 1:
    problems.append("the run found no scripted planned stop to score")

# And the honesty the score is actually about, at the thresholds the lab
# measured on 2026-09-14: both breakdowns detected, the changeover not booked
# as downtime, and the pipeline keeping up.
if metrics["planned_stop_misclassified"]:
    problems.append(f"{metrics['planned_stop_misclassified']} planned stop(s) were "
                    "booked as downtime; every availability figure this plant "
                    "reports would be a lie")
if metrics["faults_scored"] < metrics["faults_scripted"]:
    problems.append(f"only {metrics['faults_scored']} of {metrics['faults_scripted']} "
                    "scripted breakdowns were detected")
if not metrics["pipeline_sustained"]:
    problems.append("the pipeline did not keep up at this speed, so the numbers "
                    "above are about a run that fell behind")

for line in problems:
    print(f"::error::{line}")
if problems:
    raise SystemExit(1)
print(f"bottling scored: {metrics['faults_scored']}/{metrics['faults_scripted']} "
      f"breakdowns detected, {metrics['planned_stops_scored']} planned stop not "
      "misclassified, pipeline sustained.")
PY
