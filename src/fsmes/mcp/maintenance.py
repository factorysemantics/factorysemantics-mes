"""Maintenance tools: what has come due, what is open, the plans, and the
work - raise it, start it, complete it.

Due-ness comes from the machine's own use (hours run, units made, days
since service), so every job carries a reason in the plant's terms:
"212.4 h against a 200 h plan". Writes take dry_run, on_behalf_of and
client_ref like every other write tool.
"""

from __future__ import annotations


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def maintenance_due(plant: str) -> dict:
        """Plans that have come due (and those past 80%, due soon), worst
        first, each with how much of its interval is used and why - plus the
        backlog: open work and the downtime clearing it will cost."""
        return {"plant": plant, **call(plant, "GET", "/maintenance/due")}

    @mcp.tool()
    def maintenance_plans(plant: str, machine: str | None = None) -> dict:
        """Every preventive plan with how far through its interval it is."""
        out = call(plant, "GET", "/maintenance/plans")
        if isinstance(out, dict) and "error" in out:
            return out
        plans = [p for p in out if not machine or p["equipment"] == machine]
        return {"plant": plant, "plans": plans}

    @mcp.tool()
    def maintenance_work(plant: str, machine: str | None = None, open_only: bool = True,
                         limit: int = 50) -> dict:
        """Maintenance orders, newest first: due, in progress, or done, with
        who did them, what they found, and the downtime they cost."""
        path = f"/maintenance/orders?limit={limit}" + (f"&equipment={machine}" if machine else "")
        if open_only:
            path += "&status=due&status=in_progress"
        out = call(plant, "GET", path)
        if isinstance(out, dict) and "error" in out:
            return out
        # The API pages this list; the agent is told how much it did not see.
        return {"plant": plant, "work": out["items"], "shown": len(out["items"]), "total": out["total"],
                "has_more": out["has_more"]}

    @mcp.tool()
    def create_maintenance_plan(plant: str, code: str, name: str, machine: str,
                                trigger: str = "runtime_hours", interval: float = 200.0,
                                expected_minutes: float = 30.0, document_code: str | None = None,
                                dry_run: bool = False, on_behalf_of: str | None = None,
                                client_ref: str | None = None) -> dict:
        """Define a preventive plan on a machine. trigger is runtime_hours,
        produced_qty or calendar_days; interval is in that unit. Needs the
        maintenance.plan capability, which the agent role does not hold by
        default - an admin grants it per plant."""
        identify(on_behalf_of, client_ref)
        body = {"code": code, "name": name, "equipment": machine, "trigger": trigger,
                "interval": interval, "expected_minutes": expected_minutes,
                "document_code": document_code}
        return write(plant, "/maintenance/plans", body, dry_run,
                     f"create plan {code} on {machine}: every {interval} {trigger}")

    @mcp.tool()
    def raise_due_maintenance(plant: str, dry_run: bool = False, on_behalf_of: str | None = None,
                              client_ref: str | None = None) -> dict:
        """Turn every due plan into a maintenance order, once - a plan with
        work already open is not raised again."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/maintenance/raise", {}, dry_run, "raise work for every due plan")

    @mcp.tool()
    def raise_corrective_maintenance(plant: str, machine: str, summary: str, reason: str | None = None,
                                     dry_run: bool = False, on_behalf_of: str | None = None,
                                     client_ref: str | None = None) -> dict:
        """Raise corrective work on a machine because something broke, with
        what needs doing and why."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/maintenance/corrective",
                     {"equipment": machine, "summary": summary, "reason": reason},
                     dry_run, f"raise corrective work on {machine}: {summary}")

    @mcp.tool()
    def perform_maintenance(plant: str, order: str, action: str, findings: str | None = None,
                            dry_run: bool = False, on_behalf_of: str | None = None,
                            client_ref: str | None = None) -> dict:
        """start or complete a maintenance order. Completing re-baselines its
        plan from the work actually done and records the findings."""
        if action not in ("start", "complete"):
            return {"error": f"action must be start or complete, not {action!r}"}
        identify(on_behalf_of, client_ref)
        body = {"findings": findings} if action == "complete" else {}
        return write(plant, f"/maintenance/orders/{order}/{action}", body, dry_run,
                     f"{action} maintenance order {order}")

    return {f.__name__: f for f in (maintenance_due, maintenance_plans, maintenance_work,
                                    create_maintenance_plan, raise_due_maintenance,
                                    raise_corrective_maintenance, perform_maintenance)}
