"""Asking the labelled set, and measuring what came back.

Two things have to exist before any probability this project stores may be
compared with a line: a **confusion matrix** - what was proposed against what
was actually scripted - and a **calibration plot** - stated confidence
against measured accuracy, in bins, with the count in every bin shown. Until
both exist, "0.8 seemed reasonable" is not a threshold, and decision 0031
holds: a judgment is a proposal, recorded and read by a person.

This module builds both, and chooses no threshold. It will say which bin a
threshold could be argued from, if any bin qualifies, and then stop; the
decision is a person's, made in the open, with the counts in front of them.

Three pieces:

* **P1** - one `Choice` over the reason vocabulary the scripted hours use,
  asked once per window in the labelled set. A choice answers with an
  option, so what it proposes is its own and needs no threshold to read,
  which is why the confusion matrix can be drawn at all.
* **D1 against truth** - the run-log battery's six recorded answers, paired
  with what the scripted hour says. Two of the six have a truth in the
  script. The other four do not, and are recorded as unlabelled rather than
  guessed at, because a guessed label measured against a probability
  produces a number that looks like evidence and is not.
* **The figures** - the matrix, the bins, the expected calibration error and
  a reliability diagram, written as a self-contained Markdown page and one
  SVG.

Nothing here runs a plant, and the asking only happens where a key is set.
Everything else - building the set, pairing D1 with the truth, drawing both
figures from answers already stored - runs with no key, no network and no
SDK, which is how all of it is tested.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fsmes.integrations.jev import Choice, QuestionSet, from_settings, reason
from fsmes.sim import labelled, truth

#: The survey's P1 question, as one typed choice over the vocabulary the
#: scripted hours actually use. The options come from
#: `fsmes.sim.labelled.REASONS`, which takes them from the generator's own
#: event types, so nothing here invents a reason the truth cannot carry.
#:
#: `state_class` is decision 0032's word for what the state *is* rather than
#: where it came from: a window of tag history is observation-shaped, even
#: though the plant that produced it is simulated.
P1 = QuestionSet(
    name="stop-reason",
    state_class="observation",
    choices=(
        Choice(
            "stop_reason",
            "One machine on a production line, and the tags it published either "
            "side of the moment it stopped making anything. Why did it stop? "
            "Choose the one reason that fits what the tags show. If the machine "
            "never stopped in this window, say so rather than finding a reason "
            "for it.",
            options=labelled.REASONS,
        ),
    ),
)

#: Rough tokens per character of state. Not a tokeniser: an estimate printed
#: before anything is asked, so the cost of a pass is known in the right
#: order of magnitude before it is spent. Round 5 measured 9,430 input
#: tokens for 36,000 characters of run log, which is about 3.8 characters a
#: token; 4 is the round number on the safe side of it.
CHARACTERS_PER_TOKEN = 3.8

#: Above this many calls the pass refuses without `--yes`. A few hundred is
#: the size of one round of experiments; more than that is a decision
#: somebody should make on purpose.
DEFAULT_MAXIMUM_CALLS = 300

#: How the reliability diagram is cut. Ten bins is the survey's own example,
#: and with a few hundred samples it leaves most bins countable.
DEFAULT_BINS = 10

#: What a bin has to manage before this module will even name it as
#: arguable: at least this many samples, and wrong no more than one time in
#: ten. Both are written down here rather than chosen in prose, and neither
#: is applied to anything - naming a bin is not choosing a threshold.
ARGUABLE_MINIMUM_SAMPLES = 10
ARGUABLE_ACCURACY = 0.9


# ------------------------------------------------------- what a pass costs

def estimate(records: list[dict]) -> dict:
    """What asking this set would cost, before anything is asked."""
    characters = sum(record["state_characters"] for record in records)
    return {
        "calls": len(records),
        "questions_per_call": len(P1.as_questions()),
        "state_characters": characters,
        "estimated_input_tokens": int(characters / CHARACTERS_PER_TOKEN),
        "estimated_input_tokens_per_call": (
            int(characters / CHARACTERS_PER_TOKEN / len(records)) if records else 0),
        "how": (f"{CHARACTERS_PER_TOKEN} characters to a token, measured from "
                f"round 5's own usage. Output is a handful of numbers per call "
                f"and is not estimated here."),
    }


# --------------------------------------------------------- asking the set

def ask(records: list[dict], *, transport=None, settings=None) -> dict:
    """Ask P1 about every record given, and store what came back beside it.

    Never raises. A record nobody could get an answer for is stored with the
    sentence saying why and no answer, because "not asked" and "answered
    wrongly" are two different facts and a confusion matrix that mixes them
    is worthless.
    """
    if settings is None:
        from fsmes.config import get_settings

        settings = get_settings()
    client, why = from_settings(settings, transport=transport)
    answers: list[dict] = []
    if client is None:
        return {"_what": "P1 asked of a labelled set, one call per record.",
                "asked": False, "note": f"not asked ({why})",
                "question": _question_record(),
                "answers": [], "answers_total": 0, "records_total": len(records),
                "usage": None}

    usage = {"input_tokens": 0, "output_tokens": 0, "reported_for": 0,
             "not_reported_for": 0}
    for record in records:
        stored = {"id": record["id"], "label": record["label"],
                  "scripted": record["scripted"],
                  "observable": record["observable"],
                  "state_view": record["state_view"],
                  "state_sha256": record["state_sha256"],
                  "state_characters": record["state_characters"]}
        try:
            judgment = client.ask(P1, record["state"])
        except Exception as exc:  # a judgment may not cost the thing it judges
            # The key is passed in even though the real transport has
            # already taken it out of anything it raises. This is the last
            # place a failure's own words are turned into a line somebody
            # stores, and "somebody else's code never puts it there" is an
            # assumption it costs nothing here to stop making.
            stored["asked"] = False
            stored["note"] = f"not asked ({reason(exc, secret=settings.jev_api_key or '')})"
            answers.append(stored)
            continue
        by_name = {a.question: a for a in judgment.answers}
        answer = by_name[P1.choices[0].name]
        stored.update({"asked": True, "note": "asked and answered",
                       "model_asked_for": client.model, "model": judgment.model,
                       "usage": judgment.usage,
                       "answer": answer.as_record()})
        _add_usage(usage, judgment.usage)
        answers.append(stored)

    return {"_what": "P1 asked of a labelled set, one call per record.",
            "asked": True,
            "note": (f"{sum(1 for a in answers if a.get('asked'))} of "
                     f"{len(records)} record(s) were answered"),
            "asked_at": datetime.now(UTC).isoformat(),
            "question": _question_record(),
            "state_class": P1.state_class,
            "thresholds": "none: a probability is recorded, not a verdict",
            "records_total": len(records),
            "answers_total": len(answers),
            "usage": usage,
            "answers": answers}


def _question_record() -> dict:
    """The question exactly as asked, so an answer can be read in a month."""
    asked = P1.as_questions()[0]
    return {"name": asked["name"], "kind": asked["kind"], "text": asked["text"],
            "options": asked["options"]}


def _add_usage(total: dict, usage: dict | None) -> None:
    """Add one call's usage, counting what was not reported rather than zero."""
    reported = usage or {}
    if reported.get("input_tokens") is None and reported.get("output_tokens") is None:
        total["not_reported_for"] += 1
        return
    total["reported_for"] += 1
    total["input_tokens"] += int(reported.get("input_tokens") or 0)
    total["output_tokens"] += int(reported.get("output_tokens") or 0)


# ------------------------------------------- D1's conditions against truth

def _scripted_types(results: Path) -> dict[str, list[dict]]:
    """Every scripted event in a results directory, by type, across its plants."""
    out: dict[str, list[dict]] = {}
    for plant in labelled.plants_in(results):
        line_json = results / "line" / f"{plant}.json"
        tag_map = results / "packs" / plant / "tag_map.json"
        if not line_json.is_file() or not tag_map.is_file():
            continue
        for event in truth.load_truth(line_json, tag_map)["events"]:
            out.setdefault(event.type, []).append(
                {"plant": plant, "station": event.station,
                 "window_sim_s": [event.start_s, event.end_s],
                 "detail": event.detail})
    return out


def _disconnect_truth(scripted: dict) -> tuple[bool | None, str]:
    windows = scripted.get("disconnect") or []
    if not windows:
        return False, ("nothing in this hour was scripted to go silent: no "
                       "disconnect, so no component stopped reporting")
    said = ", ".join(f"{w['window_sim_s'][0]}-{w['window_sim_s'][1]}s"
                     for w in windows)
    return True, (f"{len(windows)} scripted disconnect(s) ({said}): the machine "
                  f"layer went silent while the rest of the run carried on. The "
                  f"question says \"stopped logging before the run ended\", and a "
                  f"disconnect that later reconnects only half meets that wording "
                  f"- the windows are printed so a reader can judge it")


def _counter_reset_truth(scripted: dict) -> tuple[bool | None, str]:
    resets = scripted.get("counter_reset") or []
    if not resets:
        return False, ("no counter reset was scripted, and the generator's "
                       "counters only ever go backwards when one is")
    where = ", ".join(f"{r['plant']}/{r['station']} at {r['detail'].get('at')}s"
                      for r in resets)
    return True, f"{len(resets)} scripted counter reset(s): {where}"


#: What the scripted hour can and cannot say about each of D1's six
#: questions. Two have a truth in the script. Four do not: nothing in a
#: scenario decides whether this MES retried in a storm, swallowed an
#: exception, deadlocked, or how bad its worst problem was. Those are
#: recorded as unlabelled, which is the only honest thing a set can say
#: about a question its truth does not reach.
CONDITION_TRUTH = {
    "component_stopped_reporting": _disconnect_truth,
    "counter_went_backwards": _counter_reset_truth,
}

UNLABELLED_BECAUSE = {
    "retry_storm": "nothing in a scenario scripts how this MES retries",
    "silent_exception": "nothing in a scenario scripts an exception in this MES",
    "deadlock": "nothing in a scenario scripts a deadlock in this MES",
    "worst_problem": "a scenario scripts events, not a severity for them",
}


def pair_with_truth(directories: list[Path | str]) -> dict:
    """D1's recorded answers for each run, beside what the hour was scripted to do."""
    pairs: list[dict] = []
    runs: list[dict] = []
    for directory in directories:
        results = Path(directory)
        scores_path = results / "scores.json"
        if not scores_path.is_file():
            runs.append({"results": results.name, "note": "no scores.json recorded",
                         "conditions": 0})
            continue
        scores = json.loads(scores_path.read_text(encoding="utf-8"))
        scripted = _scripted_types(results)
        counted = 0
        for plant in scores.get("plants") or []:
            record = plant.get("jev") or {}
            if not record.get("asked"):
                runs.append({"results": results.name, "plant": plant.get("plant"),
                             "note": record.get("note") or "no judgment recorded",
                             "conditions": 0})
                continue
            answers = list(record.get("conditions") or [])
            worst = record.get("worst_problem")
            if worst:
                answers.append(worst)
            for answer in answers:
                name = answer.get("question")
                rule = CONDITION_TRUTH.get(name)
                if rule is None:
                    holds, says = None, UNLABELLED_BECAUSE.get(
                        name, "nothing in the scripted hour decides this")
                else:
                    holds, says = rule(scripted)
                pairs.append({
                    "results": results.name,
                    "experiment": scores.get("experiment"),
                    "plant": plant.get("plant"),
                    "question": name,
                    "probability": answer.get("probability"),
                    "level": answer.get("level"),
                    "model": answer.get("model"),
                    "truth": holds,
                    "truth_says": says,
                })
                counted += 1
            runs.append({"results": results.name, "plant": plant.get("plant"),
                         "note": record.get("note"), "model": record.get("model"),
                         "conditions": counted})
    labelled_pairs = [p for p in pairs if p["truth"] is not None]
    return {
        "_what": ("What D1's run-log battery said about each run, beside what "
                  "the hour was scripted to do. Built by `fsmes jev calibrate`; "
                  "nothing was asked of any model here."),
        "runs": runs,
        "runs_total": len(runs),
        "pairs": pairs,
        "pairs_total": len(pairs),
        "labelled": len(labelled_pairs),
        "unlabelled": len(pairs) - len(labelled_pairs),
        "questions_with_a_truth": sorted(CONDITION_TRUTH),
        "questions_without_one": sorted(UNLABELLED_BECAUSE),
    }


# ------------------------------------------------------------ the figures

def confusion(answers: list[dict]) -> dict:
    """What was scripted against what was proposed, as counts.

    Every label in the vocabulary is a row and every one is a column, even
    where nothing carries it: a row of zeroes says this set never tested
    that reason, which is exactly what a reader needs to know before
    believing a number somewhere else in the table.
    """
    names = list(labelled.REASON_NAMES)
    counts = {label: {proposed: 0 for proposed in names} for label in names}
    unanswered = 0
    outside = 0
    for stored in answers:
        if not stored.get("asked"):
            unanswered += 1
            continue
        proposed = (stored.get("answer") or {}).get("level")
        label = stored.get("label")
        if proposed not in counts.get(label, {}):
            outside += 1
            continue
        counts[label][proposed] += 1
    right = sum(counts[name][name] for name in names)
    total = sum(sum(row.values()) for row in counts.values())
    return {
        "labels": names,
        "counts": counts,
        "by_label": {name: sum(counts[name].values()) for name in names},
        "by_proposal": {name: sum(counts[label][name] for label in names)
                        for name in names},
        "answered": total,
        "right": right,
        "accuracy": round(right / total, 4) if total else None,
        "not_answered": unanswered,
        "proposed_outside_the_vocabulary": outside,
        "records_total": len(answers),
    }


def bins_of(samples: list[tuple[float, bool]], *, bins: int = DEFAULT_BINS) -> dict:
    """Stated probability against measured accuracy, in equal bins.

    A bin with no samples is reported with a count of zero and no accuracy
    at all. It is never interpolated, never joined across, and never left
    out of the table: an empty bin is a thing this set did not measure, and
    a plot that hides it is a plot that claims it did.
    """
    edges = [(index / bins, (index + 1) / bins) for index in range(bins)]
    rows = []
    for low, high in edges:
        inside = [s for s in samples
                  if (low <= s[0] < high) or (high >= 1.0 and s[0] == 1.0)]
        count = len(inside)
        rows.append({
            "from": round(low, 4), "to": round(high, 4), "samples": count,
            "mean_stated": round(sum(s[0] for s in inside) / count, 4) if count else None,
            "measured_accuracy": round(sum(1 for s in inside if s[1]) / count, 4)
            if count else None,
            "right": sum(1 for s in inside if s[1]),
        })
    counted = sum(row["samples"] for row in rows)
    error = sum(row["samples"] / counted * abs(row["mean_stated"] - row["measured_accuracy"])
                for row in rows if row["samples"]) if counted else None
    return {
        "bins": rows,
        "bins_total": len(rows),
        "empty_bins": sum(1 for row in rows if not row["samples"]),
        "samples": counted,
        "expected_calibration_error": round(error, 4) if error is not None else None,
        "arguable_from": _arguable_from(rows),
    }


def _arguable_from(rows: list[dict]) -> dict:
    """The lowest bin above which this set was reliably right, if there is one.

    Naming a bin is not choosing a threshold, and nothing in this package
    reads this field. It exists so that a person deciding whether a
    threshold could ever be defended has the one fact they would need, with
    the counts beside it, instead of an argument.
    """
    for index, row in enumerate(rows):
        if not row["samples"]:
            # An empty bin is trivially never wrong, and naming one would
            # say a threshold of zero is defensible. The lowest bin that
            # measured anything is the lowest bin there is anything to say
            # about.
            continue
        above = rows[index:]
        counted = sum(other["samples"] for other in above)
        if counted < ARGUABLE_MINIMUM_SAMPLES:
            continue
        if any(other["samples"] and other["measured_accuracy"] < ARGUABLE_ACCURACY
               for other in above):
            continue
        return {"bin": f"{row['from']}-{row['to']}", "samples_at_or_above": counted,
                "says": (f"at or above {row['from']} this set was right "
                         f"{ARGUABLE_ACCURACY:.0%} of the time or better, over "
                         f"{counted} sample(s). Whether that is a threshold is a "
                         f"person's decision, not this tool's.")}
    return {"bin": None, "samples_at_or_above": 0,
            "says": (f"no bin has {ARGUABLE_MINIMUM_SAMPLES} or more samples at "
                     f"or above it and is right {ARGUABLE_ACCURACY:.0%} of the "
                     f"time throughout. On this evidence no threshold is "
                     f"defensible anywhere on the scale.")}


def samples_from_p1(answers: list[dict]) -> dict:
    """The P1 answers as `(stated, was it right)` pairs, two ways.

    A choice states two numbers: the probability it put on the option it
    chose, and a confidence in the choice. They are not the same number and
    they do not have to agree, so both are binned and both are printed.
    """
    by_probability: list[tuple[float, bool]] = []
    by_confidence: list[tuple[float, bool]] = []
    for stored in answers:
        if not stored.get("asked"):
            continue
        answer = stored.get("answer") or {}
        right = answer.get("level") == stored.get("label")
        if answer.get("probability") is not None:
            by_probability.append((float(answer["probability"]), right))
        if answer.get("confidence") is not None:
            by_confidence.append((float(answer["confidence"]), right))
    return {"probability_on_the_chosen_option": by_probability,
            "stated_confidence": by_confidence}


def samples_from_d1(pairs: list[dict]) -> dict[str, list[tuple[float, bool]]]:
    """The labelled D1 pairs as `(probability, did it hold)`, per question."""
    out: dict[str, list[tuple[float, bool]]] = {}
    for pair in pairs:
        if pair["truth"] is None or pair["probability"] is None:
            continue
        out.setdefault(pair["question"], []).append(
            (float(pair["probability"]), bool(pair["truth"])))
    return out


# ------------------------------------------------------------- the drawing

#: Colours chosen to be legible on a white page and on a dark one, because
#: the docs render both and an SVG in a Markdown page inherits neither.
_INK = "#8a8a8a"
_CURVE = "#1f77b4"
_PERFECT = "#b0b0b0"


def reliability_svg(bins: dict, *, title: str) -> str:
    """The calibration plot: stated against measured, with every bin's count.

    The perfect-calibration line is drawn because the whole point of the
    plot is the distance from it. An empty bin is drawn as an empty tick
    with a zero under it - visible, and impossible to mistake for a point on
    the line.
    """
    left, top, size = 70, 46, 320
    width, height = left + size + 30, top + size + 78
    bottom = top + size

    def x(value: float) -> float:
        return left + value * size

    def y(value: float) -> float:
        return bottom - value * size

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
           f'height="{height}" viewBox="0 0 {width} {height}" '
           f'font-family="system-ui, sans-serif" font-size="11">',
           f'<title>{_escape(title)}</title>',
           f'<text x="{left}" y="20" fill="{_INK}" font-size="12">'
           f'{_escape(title)}</text>',
           f'<text x="{left}" y="34" fill="{_INK}">'
           f'{bins["samples"]} sample(s) in {bins["bins_total"]} bin(s); '
           f'{bins["empty_bins"]} bin(s) empty</text>',
           f'<line x1="{x(0)}" y1="{y(0)}" x2="{x(1)}" y2="{y(1)}" '
           f'stroke="{_PERFECT}" stroke-width="1" stroke-dasharray="4 3"/>',
           f'<rect x="{left}" y="{top}" width="{size}" height="{size}" '
           f'fill="none" stroke="{_INK}" stroke-width="1"/>']

    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        out.append(f'<line x1="{left - 4}" y1="{y(tick)}" x2="{left}" '
                   f'y2="{y(tick)}" stroke="{_INK}"/>')
        out.append(f'<text x="{left - 8}" y="{y(tick) + 4}" fill="{_INK}" '
                   f'text-anchor="end">{tick:.2f}</text>')
        out.append(f'<line x1="{x(tick)}" y1="{bottom}" x2="{x(tick)}" '
                   f'y2="{bottom + 4}" stroke="{_INK}"/>')
        out.append(f'<text x="{x(tick)}" y="{bottom + 17}" fill="{_INK}" '
                   f'text-anchor="middle">{tick:.2f}</text>')

    points = []
    for row in bins["bins"]:
        middle = (row["from"] + row["to"]) / 2
        if not row["samples"]:
            out.append(f'<line x1="{x(middle)}" y1="{bottom}" x2="{x(middle)}" '
                       f'y2="{bottom - 5}" stroke="{_INK}"/>')
            out.append(f'<text x="{x(middle)}" y="{bottom + 30}" fill="{_INK}" '
                       f'text-anchor="middle" font-size="9">0</text>')
            continue
        at = (x(row["mean_stated"]), y(row["measured_accuracy"]))
        points.append(at)
        out.append(f'<circle cx="{at[0]:.1f}" cy="{at[1]:.1f}" r="3.5" '
                   f'fill="{_CURVE}"/>')
        out.append(f'<text x="{x(middle)}" y="{bottom + 30}" fill="{_INK}" '
                   f'text-anchor="middle" font-size="9">{row["samples"]}</text>')
    if len(points) > 1:
        path = " ".join(f'{"M" if index == 0 else "L"}{at[0]:.1f},{at[1]:.1f}'
                        for index, at in enumerate(points))
        out.append(f'<path d="{path}" fill="none" stroke="{_CURVE}" stroke-width="1.5"/>')

    error = bins["expected_calibration_error"]
    out.append(f'<text x="{left}" y="{bottom + 46}" fill="{_INK}">'
               f'stated confidence (bin midpoints; the count in each bin is '
               f'under the axis)</text>')
    out.append(f'<text x="{left}" y="{bottom + 62}" fill="{_INK}">'
               f'expected calibration error: '
               f'{"not measurable - no samples" if error is None else f"{error:.4f}"}'
               f'</text>')
    out.append(f'<text x="14" y="{top + size / 2}" fill="{_INK}" '
               f'transform="rotate(-90 14 {top + size / 2})" '
               f'text-anchor="middle">measured accuracy</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# -------------------------------------------------------------- the report

def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(out)


def _bins_table(bins: dict) -> str:
    rows = []
    for row in bins["bins"]:
        if row["samples"]:
            rows.append([f'{row["from"]:.1f}-{row["to"]:.1f}', str(row["samples"]),
                         f'{row["mean_stated"]:.3f}', str(row["right"]),
                         f'{row["measured_accuracy"]:.3f}'])
        else:
            rows.append([f'{row["from"]:.1f}-{row["to"]:.1f}', "0", "empty",
                         "0", "empty"])
    rows.append(["**total**", f'**{bins["samples"]}**', "", "", ""])
    return _table(["stated", "samples", "mean stated", "right", "measured accuracy"],
                  rows)


def report(*, set_file: dict, answers: dict, d1: dict, figures: list[str]) -> str:
    """The whole measurement as one self-contained page.

    Every number in it comes from the two files named at the top, and
    nothing in it needs a script to be re-read. That is the style the
    cutlery analysis reports are written in, and the reason is the same: a
    report whose numbers can only be checked by running something is a
    report nobody checks.
    """
    totals = set_file.get("totals") or {}
    stored = answers.get("answers") or []
    matrix = confusion(stored)
    samples = samples_from_p1(stored)
    by_probability = bins_of(samples["probability_on_the_chosen_option"])
    by_confidence = bins_of(samples["stated_confidence"])

    out = [
        "# Calibration of the judgment model on this project's own runs",
        "",
        f"Written {datetime.now(UTC).date().isoformat()} by `fsmes jev calibrate`. "
        "Every number below comes from the labelled set and the stored answers "
        "named in it, and nothing here chooses a threshold.",
        "",
        "## What was measured",
        "",
        f"- Runs read: **{set_file.get('runs_total', 0)}**",
        f"- Windows in the labelled set: **{totals.get('records', 0)}** "
        f"({totals.get('scripted', 0)} named by a scripted event, "
        f"{totals.get('not_scripted', 0)} produced by the line's own buffers)",
        f"- Windows the MES could see: **{totals.get('observable', 0)}**; "
        f"windows entirely inside a disconnect: **{totals.get('unobservable', 0)}**",
        f"- Windows asked about: **{answers.get('answers_total', 0)}**, of which "
        f"**{matrix['answered']}** were answered and **{matrix['not_answered']}** "
        f"were not",
        "",
        "### The set, by the reason it actually had",
        "",
        _table(["reason", "windows"],
               [[name, str(count)] for name, count in
                (totals.get("by_label") or {}).items()]
               + [["**total**", f"**{totals.get('records', 0)}**"]]),
        "",
        "## P1: what was proposed against what was scripted",
        "",
        f"One choice over {len(labelled.REASON_NAMES)} options, asked once per "
        f"window. A choice proposes an option of its own, so this table needs no "
        f"threshold to be drawn.",
        "",
        _confusion_table(matrix),
        "",
        f"Right on **{matrix['right']}** of **{matrix['answered']}** answered"
        + (f" (**{matrix['accuracy']:.3f}**)." if matrix["accuracy"] is not None
           else ", so no accuracy can be stated."),
        "",
        "## P1: stated confidence against measured accuracy",
        "",
        "### Binned by the probability the model put on the option it chose",
        "",
        _bins_table(by_probability),
        "",
        f"Expected calibration error: "
        f"**{by_probability['expected_calibration_error']}**"
        if by_probability["expected_calibration_error"] is not None
        else "Expected calibration error: not measurable - no samples.",
        "",
        by_probability["arguable_from"]["says"],
        "",
        "### Binned by the confidence the model stated",
        "",
        _bins_table(by_confidence),
        "",
        f"Expected calibration error: "
        f"**{by_confidence['expected_calibration_error']}**"
        if by_confidence["expected_calibration_error"] is not None
        else "Expected calibration error: not measurable - no samples.",
        "",
        by_confidence["arguable_from"]["says"],
        "",
        "## D1's run-log battery against the scripted hours",
        "",
        f"{d1.get('pairs_total', 0)} recorded condition(s) in total across "
        f"{d1.get('runs_total', 0)} run record(s): **{d1.get('labelled', 0)}** "
        f"the scripted hour can decide, **{d1.get('unlabelled', 0)}** it cannot. "
        f"The ones it cannot are printed as unlabelled rather than guessed at.",
        "",
        _d1_table(d1),
        "",
        "## No threshold is chosen here",
        "",
        "Decision 0031 stands: a judgment is a proposal. Nothing in this MES "
        "reads any number above, and nothing gates on one. Where a bin is named "
        "as arguable it is named with its count beside it, for a person to "
        "decide in the open.",
        "",
    ]
    if figures:
        out.extend(["## The figures", ""])
        out.extend(f"![{Path(name).stem}]({name})" for name in figures)
        out.append("")
    return "\n".join(str(line) for line in out)


def _confusion_table(matrix: dict) -> str:
    names = matrix["labels"]
    header = ["scripted \\ proposed", *names, "**total**"]
    rows = []
    for label in names:
        counts = matrix["counts"][label]
        rows.append([label, *[str(counts[name]) for name in names],
                     f"**{matrix['by_label'][label]}**"])
    rows.append(["**total**", *[f"**{matrix['by_proposal'][name]}**" for name in names],
                 f"**{matrix['answered']}**"])
    return _table(header, rows)


def _d1_table(d1: dict) -> str:
    rows = []
    for pair in d1.get("pairs") or []:
        rows.append([
            pair.get("results") or "",
            pair.get("question") or "",
            "not stated" if pair.get("probability") is None
            else f'{pair["probability"]}',
            "unlabelled" if pair["truth"] is None else ("true" if pair["truth"] else "false"),
            pair.get("truth_says") or "",
        ])
    rows.append([f'**{len(rows)} in total**', "", "", "", ""])
    return _table(["run", "question", "probability", "scripted truth", "why"], rows)
