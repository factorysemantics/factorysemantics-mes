"""Quality depth tools: the control chart, the gauge register, and what a
failed calibration invalidates.

The Quality screen and the record_check tool cover inspections and
non-conformances; these are the second layer - is the process stable and
capable, and can the instrument be believed. Registering a gauge is master
data (masterdata.write) and calibrating one is a supervisor's disposition
(quality.close_nc): both human by default; the agent reads everything.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

#: What the model is handed instead of every bucket of every analog signal.
#:
#: The whole dossier is about 7,800 tokens of a conversation that may spend
#: $0.25, and 60 % of it is `tags.trends[].points` - 96 buckets a signal here,
#: up to 240 on a plant with a fast cadence, times however many signals the
#: station publishes. Those points exist to be DRAWN, and the thing that draws
#: them is the panel on the SPC screen; a model reading them one at a time
#: spends the conversation's budget on a picture it cannot see.
#:
#: So they are left out by default and `trends=True` asks for them. Nothing is
#: summarised in their place and no figure is worked out from them: every
#: trend keeps its own `samples`, `buckets`, `buckets_with_no_reading`,
#: `coverage` and `last_before_window`, which the plant computed, and the note
#: below says how many points were left out and how to ask for them. A list
#: that quietly came back shorter would be the one kind of trim this product
#: does not allow.
TRENDS_LEFT_OUT = (
    "{points} bucket readings of this signal are not in this answer - they are "
    "what the SPC screen's panel draws, and reading them one at a time would "
    "spend the conversation's budget on a picture. Every figure above is the "
    "plant's own: how many samples arrived, how many buckets hold one, and what "
    "share of the window that is. Call again with trends=true for the points "
    "themselves."
)


def _without_trend_points(dossier: Any) -> Any:
    """The dossier with each analog trend's drawable series left out, and said
    to be left out.

    Nothing else is touched - not the counts, not the coverage, not the
    markers' own total - because the blocks that answer *why a point is where
    it is* are the small ones, and they travel whole.
    """
    if not isinstance(dossier, dict):
        return dossier
    tags = dossier.get("tags")
    if not isinstance(tags, dict) or not isinstance(tags.get("trends"), list):
        return dossier
    trimmed = []
    for trend in tags["trends"]:
        if not isinstance(trend, dict) or "points" not in trend:
            trimmed.append(trend)
            continue
        points = trend.get("points") or []
        kept = {k: v for k, v in trend.items() if k != "points"}
        kept["points_left_out"] = len(points)
        kept["points_note"] = TRENDS_LEFT_OUT.format(points=len(points))
        trimmed.append(kept)
    return {**dossier, "tags": {**tags, "trends": trimmed}}


def _dossier_query(before_minutes: float | None, after_minutes: float | None,
                   neighbour_hours: float | None) -> str:
    """Only the windows the caller actually named. An omitted minute is the
    route's own default and not a number this layer picked."""
    asked = [(name, value) for name, value in
             (("before_minutes", before_minutes), ("after_minutes", after_minutes),
              ("neighbour_hours", neighbour_hours)) if value is not None]
    return ("?" + "&".join(f"{name}={value}" for name, value in asked)) if asked else ""


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def spc_chart(plant: str, material: str, characteristic: str, limit: int = 200) -> dict:
        """The control chart for one characteristic: control limits from the
        process's own variation, which Western Electric rules fired and where,
        capability (Cp, Cpk, Pp) - withheld while the process is out of control
        - and a one-sentence verdict. `kind` says which chart it is: `imr`,
        individuals and moving range, for a characteristic inspected one piece
        at a time, or `xbar_r` for one inspected n at a time, whose points are
        the sample means and which also carries the samples behind them."""
        return {"plant": plant, **call(plant, "GET", f"/quality/spc/{material}/{characteristic}?limit={limit}")}

    @mcp.tool()
    def spc_point(plant: str, material: str, characteristic: str, check_id: int,
                  before_minutes: float | None = None, after_minutes: float | None = None,
                  neighbour_hours: float | None = None, trends: bool = False) -> dict:
        """Why is this reading where it is - the records behind one point of a
        control chart, the same ones the SPC screen's panel shows when somebody
        clicks that point. Ask this whenever the question is *why* a reading or
        a point is high, low, or flagged: the chart says that a rule fired and
        this says what else was happening. `check_id` is the `check` on the
        point of an `spc_chart` answer.

        The blocks: `reading`, the one that was taken; `chart`, the limits and
        which rules fired; `recorded`, the signals this reading itself tripped
        and any non-conformance; `gauge`, the instrument and its calibration
        state; `neighbours`, the same characteristic by every other gauge in
        the hour either side; `machine`, what the station was doing and what it
        had just come out of, with the seconds since; `timeline`, its states
        across the window; `stops`; `line`, what the REST of the line was doing
        in the same window, which is the block the station's own answer cannot
        hold; `tags`, the station's analog signals; `maintenance`; and
        `findings`. Every block states its own coverage - what share of its own
        window anybody was watching - and the ones that are lists of records
        say they have no such figure rather than reporting nought.

        Nothing here is recomputed and nothing is a model's guess: the limits
        are the chart's, the trends are the tag history's, the timeline is the
        state history's and the gauge's due date is the register's. Two facts
        in the same window are two facts - a changeover that ended forty
        seconds before a high reading did not necessarily cause it, and this
        answer never says it did.

        `trends=true` adds every bucket of every analog signal. They are left
        out by default because they are what a chart is drawn from, and each
        trend already states how many arrived and what share of the window
        holds one.
        """
        path = (f"/quality/spc/{quote(material, safe='')}/"
                f"{quote(characteristic, safe='')}/point/{int(check_id)}"
                + _dossier_query(before_minutes, after_minutes, neighbour_hours))
        answer = call(plant, "GET", path)
        return {"plant": plant,
                **(answer if trends else _without_trend_points(answer))}

    @mcp.tool()
    def spc_sample(plant: str, material: str, characteristic: str, sample_id: int,
                   before_minutes: float | None = None, after_minutes: float | None = None,
                   neighbour_hours: float | None = None, trends: bool = False) -> dict:
        """Why is this sample where it is - the records behind one point of an
        X-bar chart. The same question `spc_point` answers, of a point that is
        an average rather than a reading, and the tool to use when an
        `spc_chart` answer has `kind` `xbar_r`: its points carry `sample`, and
        that is the `sample_id` here.

        Two blocks differ. The n `readings` behind the point come with it, each
        one's distance from the sample's own mean and which is furthest out -
        because an average above its limit because every bottle was high and an
        average above its limit because one bottle was very high are two
        different mornings on a filler. And the window is the stretch those n
        readings span plus the minutes before, with every one of them marked on
        each trend. Everything else is the same block: `chart`, `recorded`,
        `gauge`, `neighbours`, `machine`, `timeline`, `stops`, `line` - what
        the rest of the line was doing in the same window - `tags`,
        `maintenance` and `findings`, each with its own coverage.

        In any sample one reading is always the furthest from the mean, and on
        a settled process that means nothing; the answer says so itself. Say it
        too rather than reporting the furthest reading as a finding.

        `trends=true` adds every bucket of every analog signal, which are left
        out by default for the reason `spc_point` gives.
        """
        path = (f"/quality/spc/{quote(material, safe='')}/"
                f"{quote(characteristic, safe='')}/sample/{int(sample_id)}"
                + _dossier_query(before_minutes, after_minutes, neighbour_hours))
        answer = call(plant, "GET", path)
        return {"plant": plant,
                **(answer if trends else _without_trend_points(answer))}

    @mcp.tool()
    def gauges(plant: str) -> dict:
        """The gauge register: every measuring instrument with its status,
        last calibration and due date, and which are out of calibration yet
        still on the floor. A gauge never calibrated counts as overdue."""
        return {"plant": plant, **call(plant, "GET", "/quality/gauges")}

    @mcp.tool()
    def gauge_impact(plant: str, gauge: str) -> dict:
        """Every measurement taken with this gauge since its last calibration,
        and the orders they touched - the set a failed calibration invalidates."""
        return {"plant": plant, **call(plant, "GET", f"/quality/gauges/{gauge}/impact")}

    @mcp.tool()
    def gauge_resolution(plant: str, gauge: str, tolerance: float) -> dict:
        """Can this gauge judge this tolerance? The rule of ten: resolution
        should be about a tenth of the tolerance; below that the chart is
        charting the instrument."""
        return {"plant": plant, **call(plant, "GET", f"/quality/gauges/{gauge}/resolution?tolerance={tolerance}")}

    @mcp.tool()
    def create_spec(plant: str, material: str, characteristic: str, unit: str = "",
                    min_value: float | None = None, max_value: float | None = None,
                    sample_size: int | None = None,
                    dry_run: bool = False, on_behalf_of: str | None = None,
                    client_ref: str | None = None) -> dict:
        """Define a quality specification: a characteristic on a material with
        its limits. Out-of-spec checks then open non-conformances by themselves.
        `sample_size` is the sampling plan - how many pieces are inspected at a
        time. Leave it out (or 1) for one at a time, charted as individuals and
        moving range; 2 to 10 makes it X-bar and R, whose readings arrive
        together through record_sample."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/quality/specs",
                     {"material": material, "characteristic": characteristic, "unit": unit,
                      "min_value": min_value, "max_value": max_value,
                      "sample_size": sample_size},
                     dry_run, f"define {characteristic} on {material}: {min_value}-{max_value} {unit}".strip())

    @mcp.tool()
    def register_gauge(plant: str, code: str, name: str, kind: str = "general", interval_days: int = 365,
                       resolution: float | None = None, location: str | None = None,
                       dry_run: bool = False, on_behalf_of: str | None = None,
                       client_ref: str | None = None) -> dict:
        """Add a gauge to the register (master data - refused unless an admin
        has granted masterdata.write to the agent role)."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/quality/gauges",
                     {"code": code, "name": name, "kind": kind, "interval_days": interval_days,
                      "resolution": resolution, "location": location},
                     dry_run, f"register gauge {code}")

    @mcp.tool()
    def calibrate_gauge(plant: str, gauge: str, result: str, performed_by: str,
                        performed_on: str | None = None, certificate: str | None = None,
                        notes: str | None = None, dry_run: bool = False,
                        on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Record a calibration: pass, adjusted, or fail_as_found. A failure
        takes the gauge off the floor and the answer lists what it invalidated.
        A supervisor's disposition (quality.close_nc) - human by default."""
        identify(on_behalf_of, client_ref)
        return write(plant, f"/quality/gauges/{gauge}/calibrate",
                     {"result": result, "performed_by": performed_by, "performed_on": performed_on,
                      "certificate": certificate, "notes": notes},
                     dry_run, f"record calibration of {gauge}: {result}")

    return {f.__name__: f for f in (spc_chart, spc_point, spc_sample, gauges, gauge_impact,
                                    gauge_resolution, create_spec, register_gauge,
                                    calibrate_gauge)}
