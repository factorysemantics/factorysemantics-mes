"""Quality depth tools: the control chart, the gauge register, and what a
failed calibration invalidates.

The Quality screen and the record_check tool cover inspections and
non-conformances; these are the second layer - is the process stable and
capable, and can the instrument be believed. Registering a gauge is master
data (masterdata.write) and calibrating one is a supervisor's disposition
(quality.close_nc): both human by default; the agent reads everything.
"""

from __future__ import annotations


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def spc_chart(plant: str, material: str, characteristic: str, limit: int = 200) -> dict:
        """The individuals control chart for one characteristic: control
        limits from the process's own variation, which Western Electric rules
        fired and where, capability (Cp, Cpk, Pp) - withheld while the process
        is out of control - and a one-sentence verdict."""
        return {"plant": plant, **call(plant, "GET", f"/quality/spc/{material}/{characteristic}?limit={limit}")}

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
                    dry_run: bool = False, on_behalf_of: str | None = None,
                    client_ref: str | None = None) -> dict:
        """Define a quality specification: a characteristic on a material with
        its limits. Out-of-spec checks then open non-conformances by themselves."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/quality/specs",
                     {"material": material, "characteristic": characteristic, "unit": unit,
                      "min_value": min_value, "max_value": max_value},
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

    return {f.__name__: f for f in (spc_chart, gauges, gauge_impact, gauge_resolution, create_spec,
                                    register_gauge, calibrate_gauge)}
