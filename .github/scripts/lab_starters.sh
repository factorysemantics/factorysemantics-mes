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

# The design store this run's plants and notes use. Pointed at the results
# directory so a scripted run proves the feedback loop without writing into
# anybody's real notes, and so the store travels with the runs as an artifact.
export MES_DESIGN_STORE="$results/design.db"
mkdir -p "$results"

for plan in "${plans[@]}"; do
  echo "::group::fsmes lab run $plan"
  fsmes lab run "$plan" --results "$results"
  echo "::endgroup::"
done

fsmes lab list --results "$results"

# The feedback loop, end to end and with no model anywhere: a note left
# against a run appears in that run's report beside the screen it names, and
# `fsmes lab review` rolls every run up into one cited findings.md.
first_run="$(ls -d "$results"/*/ | head -1)"
plant="$(python -c "import json,sys; print(json.load(open(sys.argv[1]))['plants'][0]['plant'])" \
         "${first_run}scores.json")"
note="the orders list does not say how many orders there are"
fsmes lab note "${first_run%/}" "$note" --screen /dashboard/orders --plant "$plant"
fsmes lab review --results "$results" --no-model --out "$results/findings.md"

python - "$results" "${#plans[@]}" "$note" <<'PY'
import json
import os
import sys
from pathlib import Path

results, expected, note = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
runs = sorted(p for p in results.iterdir() if (p / "scores.json").is_file())
if len(runs) != expected:
    sys.exit(f"{expected} plan(s) ran and {len(runs)} run directory(ies) appeared in {results}.")

summary = ["| Run | Plants | Withheld | Measurements |", "|---|---|---|---|"]
for run in runs:
    scores = json.loads((run / "scores.json").read_text(encoding="utf-8"))
    missing = [name for name in ("plan.toml", "truth.json", "scores.json",
                                 "report.html", "notes.md", "recorded",
                                 "feedback/conversations.jsonl")
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

# The note went to the first run; it has to be in that run's report, under the
# section for the screen it names rather than in an appendix somewhere.
first = runs[0]
page = (first / "report.html").read_text(encoding="utf-8")
if note not in page:
    sys.exit(f"{first.name}: the note left with `fsmes lab note` is not in report.html.")
if page.index("Booking honesty") > page.index(note):
    sys.exit(f"{first.name}: the note is not rendered beside the booking numbers it is about.")

findings = results / "findings.md"
if not findings.is_file():
    sys.exit("`fsmes lab review` wrote no findings.md.")
rolled = findings.read_text(encoding="utf-8")
for run in runs:
    if run.name not in rolled:
        sys.exit(f"findings.md does not cite {run.name}.")
if f"> {note}" not in rolled:
    sys.exit("findings.md does not quote the note verbatim.")
print(f"feedback: the note is in {first.name}'s report and findings.md cites "
      f"{len(runs)} run(s)")

step = os.environ.get("GITHUB_STEP_SUMMARY")
if step:
    with Path(step).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(summary) + "\n")
PY
