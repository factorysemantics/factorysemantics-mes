#!/usr/bin/env bash
# Run the lab's starter experiments and prove each one produced a readable run.
#
# The instrument is the thing being tested here, not the plant: an experiment
# that stops running is an experiment nobody notices has stopped, because it is
# only ever run by hand. So every starter runs on every pull request, and the
# check is that the directory it leaves behind holds what the page says it
# holds - a plan, the truth, what each plant recorded, the scores and a report.
#
# It does NOT assert any particular measurement. A scored run is an instrument
# reading and not a test: withholding a verdict because the runner fell behind
# is the harness working, and failing CI on it would teach everyone to stop
# reading the number. What fails this script is a run that did not happen.
#
# Usage: lab_starters.sh [plan ...]     (default: every plan in labs/experiments)
set -euo pipefail

plans=("$@")
if [ ${#plans[@]} -eq 0 ]; then
  mapfile -t plans < <(ls labs/experiments/*.toml)
fi

results="$(pwd)/lab-results"
rm -rf "$results"

for plan in "${plans[@]}"; do
  echo "::group::fsmes lab run $plan"
  fsmes lab run "$plan" --results "$results"
  echo "::endgroup::"
done

fsmes lab list --results "$results"

python - "$results" "${#plans[@]}" <<'PY'
import json
import sys
from pathlib import Path

results, expected = Path(sys.argv[1]), int(sys.argv[2])
runs = sorted(p for p in results.iterdir() if (p / "scores.json").is_file())
if len(runs) != expected:
    sys.exit(f"{expected} plan(s) ran and {len(runs)} run directory(ies) appeared in {results}.")

summary = ["| Run | Plants | Withheld | Measurements |", "|---|---|---|---|"]
for run in runs:
    scores = json.loads((run / "scores.json").read_text(encoding="utf-8"))
    missing = [name for name in ("plan.toml", "truth.json", "scores.json",
                                 "report.html", "notes.md", "recorded")
               if not (run / name).exists()]
    if missing:
        sys.exit(f"{run.name} is missing {', '.join(missing)}.")
    if scores["plants_run"] != scores["plants_total"]:
        sys.exit(f"{run.name}: {scores['plants_run']} of {scores['plants_total']} plants ran.")
    for plant in scores["plants"]:
        for name in scores["measurements_asked_for"]:
            if name not in plant["measurements"]:
                sys.exit(f"{run.name}: {plant['plant']} has no {name} measurement, and the plan "
                         f"asked for one.")
    page = (run / "report.html").read_text(encoding="utf-8")
    if "http://" in page or "https://" in page:
        sys.exit(f"{run.name}: report.html fetches something from the network. A results "
                 f"directory has to open on a machine that has none.")
    withheld = sum(1 for p in scores["plants"] if p["verdict_withheld"])
    summary.append(f"| {run.name} | {scores['plants_run']} | {withheld} | "
                   f"{', '.join(scores['measurements_asked_for'])} |")
    print(f"{run.name}: {scores['plants_run']} plant(s), {withheld} verdict(s) withheld")

step = Path(__import__("os").environ.get("GITHUB_STEP_SUMMARY", ""))
if str(step):
    with step.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(summary) + "\n")
PY
