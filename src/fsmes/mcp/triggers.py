"""Trigger tools: read the catalog and the triggers, draft one, read what fired.

Approving a trigger is a person's capability (triggers.approve) and never a
tool: logic that fires against a live plant goes into force by a human
decision. The agent may propose - a draft - and may read every firing.
"""

from __future__ import annotations


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def trigger_catalog(plant: str) -> dict:
        """The actions a trigger may take (log_event, open_nc,
        create_maintenance_order, set_machine_down) with their parameters, and
        the conditions it may watch for (above, below, equals, bit_set,
        rises_above, falls_below)."""
        return {"plant": plant, **call(plant, "GET", "/triggers/catalog")}

    @mcp.tool()
    def triggers(plant: str, status: str | None = None) -> dict:
        """Every trigger with its condition, action, status (draft, approved,
        withdrawn), and how often it has fired. A trigger that never fires and
        one that fires every minute are both findings."""
        path = "/triggers" + (f"?status={status}" if status else "")
        return {"plant": plant, "triggers": call(plant, "GET", path)}

    @mcp.tool()
    def trigger_firings(plant: str, trigger: str | None = None, hours: float = 24.0, limit: int = 50) -> dict:
        """What fired: for one trigger, or every firing in the last `hours`,
        each with the value that caused it and what the action did."""
        if trigger:
            return {"plant": plant, "firings": call(plant, "GET", f"/triggers/{trigger}/firings?limit={limit}")}
        out = call(plant, "GET", f"/triggers/firings?hours={hours}&limit={limit}")
        if isinstance(out, dict) and "error" in out:
            return out
        return {"plant": plant, "firings": out["items"], "shown": len(out["items"]),
                "total": out["total"], "has_more": out["has_more"]}

    @mcp.tool()
    def draft_trigger(plant: str, code: str, name: str, tag: str, condition: str, threshold: float,
                      machine: str | None = None, sustained_seconds: float = 0.0,
                      cooldown_seconds: float = 300.0, action: str = "log_event",
                      action_params: dict | None = None, note: str | None = None,
                      dry_run: bool = False, on_behalf_of: str | None = None,
                      client_ref: str | None = None) -> dict:
        """Draft a trigger: when `tag` on `machine` (or any machine) meets
        `condition` against `threshold` for `sustained_seconds`, take `action`
        from the catalog, then stay quiet for `cooldown_seconds`. It watches
        nothing until a person approves it on the Engineering > Triggers
        screen."""
        identify(on_behalf_of, client_ref)
        body = {"code": code, "name": name, "tag": tag, "condition": condition, "threshold": threshold,
                "equipment": machine, "sustained_seconds": sustained_seconds,
                "cooldown_seconds": cooldown_seconds, "action": action, "action_params": action_params,
                "note": note}
        return write(plant, "/triggers", body, dry_run,
                     f"draft trigger {code}: {tag} {condition} {threshold:g} -> {action}")

    return {f.__name__: f for f in (trigger_catalog, triggers, trigger_firings, draft_trigger)}
