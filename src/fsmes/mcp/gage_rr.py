"""Gauge study (gage R&R) tools: plan a study, record its readings, read its
verdict, and ask whether a characteristic's trouble is the gauge or the
process.

Planning a study is `quality.study`, which the agent's default role does not
hold: a study takes appraisers off the floor, and a person decides that. The
assistant can still plan one for a person who holds it - the same proposal
with dry_run first. Recording a study reading is `quality.record`, like any
other measurement. Everything else is a read.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def gage_studies(plant: str, material: str | None = None,
                     characteristic: str | None = None, gauge: str | None = None,
                     state: str | None = None) -> dict:
        """Every gauge study (gage R&R), newest first, with its total: the
        gauge, the characteristic, parts x appraisers x trials, its state
        (planned, collecting, complete) and how many readings are in."""
        query = urlencode({k: v for k, v in (("material", material),
                                              ("characteristic", characteristic),
                                              ("gauge", gauge), ("state", state)) if v})
        return {"plant": plant,
                **call(plant, "GET", "/quality/gage-studies" + (f"?{query}" if query else ""))}

    @mcp.tool()
    def gage_study(plant: str, study: str) -> dict:
        """One gauge study: its plan, every reading, and the ANOVA result -
        repeatability (the gauge), reproducibility (the people), part-to-part,
        as % of study variation and % of tolerance, distinct categories, and a
        verdict in words. `withheld` lists why a verdict is refused (readings
        missing, too few parts, parts that do not span the tolerance, a gauge
        out of calibration); then `verdict` is null and must be reported as
        not judged, never as passed."""
        return {"plant": plant,
                **call(plant, "GET", f"/quality/gage-studies/{quote(study, safe='')}")}

    @mcp.tool()
    def gauge_or_process(plant: str, material: str, characteristic: str) -> dict:
        """Is a characteristic's trouble the gauge or the process? The newest
        complete gauge study beside the control chart's capability. `answer`
        is gauge, process, neither, or not known - and not known (no study, or
        a withheld one) is said as not known, never as fine."""
        query = urlencode({"material": material, "characteristic": characteristic})
        return {"plant": plant,
                **call(plant, "GET", f"/quality/gage-studies/gauge-or-process?{query}")}

    @mcp.tool()
    def plan_gage_study(plant: str, gauge: str, material: str, characteristic: str,
                        appraisers: list[str], parts: int = 10, trials: int = 3,
                        purpose: str | None = None, code: str | None = None,
                        dry_run: bool = False, on_behalf_of: str | None = None,
                        client_ref: str | None = None) -> dict:
        """Plan a crossed gauge study: every appraiser measures every part,
        `trials` times. Ten parts, three appraisers, three trials is the usual
        plan. The tolerance is copied from the specification. A quality
        engineer's decision (quality.study) - not the agent's by default."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/quality/gage-studies",
                     {"gauge": gauge, "material": material,
                      "characteristic": characteristic, "appraisers": appraisers,
                      "parts": parts, "trials": trials, "purpose": purpose, "code": code},
                     dry_run, f"plan a gauge study of {gauge} on {material}/{characteristic}: "
                              f"{parts} parts x {len(appraisers)} appraisers x {trials} trials")

    @mcp.tool()
    def record_study_reading(plant: str, study: str, part: int, appraiser: str,
                             trial: int, value: float, dry_run: bool = False,
                             on_behalf_of: str | None = None,
                             client_ref: str | None = None) -> dict:
        """Record one reading of a gauge study: this part, by this appraiser,
        this trial. A study reading is never a production check and never on a
        control chart. One already taken is refused, never overwritten; the
        study completes itself when the last reading is in."""
        identify(on_behalf_of, client_ref)
        return write(plant, f"/quality/gage-studies/{quote(study, safe='')}/readings",
                     {"readings": [{"part": part, "appraiser": appraiser,
                                    "trial": trial, "value": value}]},
                     dry_run, f"record {study}: part {part}, {appraiser}, trial {trial} = {value}")

    return {f.__name__: f for f in (gage_studies, gage_study, gauge_or_process,
                                    plan_gage_study, record_study_reading)}
