"""Equipment tools: the plant's machines, their tags, their alarms, and the
work in progress between them.

Thin skins over the HTTP API, like every tool. `call(plant, method, path)`
is the one the server hands us; it signs in as the AGENT account and turns
an HTTP error into an `{"error": ...}` payload the agent can read.
"""

from __future__ import annotations


def register(mcp, call) -> dict:
    @mcp.tool()
    def equipment_tree(plant: str) -> dict:
        """The plant as a tree - site, lines, cells, machines - with the cost
        center each node bills to and the current state of every machine."""
        return {"plant": plant, **call(plant, "GET", "/equipment/tree")}

    @mcp.tool()
    def machine(plant: str, machine: str) -> dict:
        """One machine's head facts: where it sits, its cost center, its state
        and reason, and the order and operation it is working on."""
        return {"plant": plant, "machine": call(plant, "GET", f"/equipment/{machine}")}

    @mcp.tool()
    def machine_tags(plant: str, machine: str) -> dict:
        """Every tag the machine publishes with its latest value and age:
        state, counters, process values beside their setpoints (with bounds
        and whether they are writable), and the alarm word decoded into
        names. `stale` marks a tag that has stopped arriving."""
        return {"plant": plant, **call(plant, "GET", f"/equipment/{machine}/tags")}

    @mcp.tool()
    def machine_alarms(plant: str, machine: str | None = None, hours: float = 0) -> dict:
        """Active alarm bits, by name, on one machine or on every machine.
        With a machine and `hours`, also every change of its alarm word over
        that window - which bits it raised and when, for tying a quality
        problem found later to the process excursion that caused it."""
        path = f"/equipment/{machine}/alarms?hours={hours}" if machine else "/equipment/alarms"
        out = call(plant, "GET", path)
        return {"plant": plant, "alarms": out if isinstance(out, list) else [out]}

    @mcp.tool()
    def browse_tags(plant: str, machine: str | None = None, kind: str | None = None,
                    stale_only: bool = False, writable_only: bool = False) -> dict:
        """Every tag on every machine, flat, with a health line per machine
        (how long since it last spoke, stale count, active alarms). Filter
        by machine, by kind (pv, sp, counter, state, alarm), to stale tags
        only, or to writable setpoints only - the ones a recommendation
        could one day target, with the bounds any write must stay inside."""
        out = call(plant, "GET", "/equipment/tags")
        if isinstance(out, dict) and "error" in out:
            return out
        rows = [r for r in out["rows"]
                if (not machine or r["equipment"] == machine)
                and (not kind or r["kind"] == kind)
                and (not stale_only or r["stale"])
                and (not writable_only or r["writable"])]
        machines = [m for m in out["machines"] if not machine or m["code"] == machine]
        return {"plant": plant, "at": out["at"], "stale_after_seconds": out["stale_after_seconds"],
                "machines": machines, "rows": rows, "matched": len(rows), "of": len(out["rows"])}

    @mcp.tool()
    def line_wip(plant: str, line: str | None = None) -> dict:
        """Work in progress at each station of a line, summed over the orders
        on the floor, with the cost center it sits in. `consistent: false`
        means a station reports more finished than reached it - a miscounted
        PLC or a booking against the wrong operation, worth a look."""
        path = "/line/wip" + (f"?line={line}" if line else "")
        return {"plant": plant, **call(plant, "GET", path)}

    return {f.__name__: f for f in (equipment_tree, machine, machine_tags, machine_alarms,
                                    browse_tags, line_wip)}
