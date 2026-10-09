"""Maintenance tools: what has come due, what is open, the plans, and the
work - raise it, start it, complete it, and hand it to somebody.

Due-ness comes from the machine's own use (hours run, units made, days
since service), so every job carries a reason in the plant's terms:
"212.4 h against a 200 h plan". Writes take dry_run, on_behalf_of and
client_ref like every other write tool.

The crew side reads as well as writes. `maintenance_roster` and
`maintenance_rules` answer "who is free with the electrical skill right now"
and "what rule would give this away", and both are read tools, so the analysis
agent - whose bundle is `plant.read` and `audit.read` and nothing that writes -
is offered them. Dispatching and assigning are writes gated on
`maintenance.perform` and `maintenance.plan`, which that bundle does not hold:
the agent can say who should get the job and cannot give it to them.
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
        """Every preventive plan with how far through its interval it is, how
        long the job takes, and what it needs of the line - `needs_stop` and
        `window` - which is the other half of the cost of doing it now."""
        out = call(plant, "GET", "/maintenance/plans")
        if isinstance(out, dict) and "error" in out:
            return out
        plans = [p for p in out if not machine or p["equipment"] == machine]
        return {"plant": plant, "plans": plans}

    @mcp.tool()
    def maintenance_work(plant: str, machine: str | None = None, open_only: bool = True,
                         limit: int = 50) -> dict:
        """Maintenance orders, newest first: due, given to somebody, in
        progress, or done - with who did them, what they found, and the
        downtime they cost.

        Every row says what the job needs of the line: `needs_stop` and
        `window` (anytime, between_orders, end_of_shift). They are the answer
        to "why is this order still at `assigned` eight hours after it was
        raised" - a condenser clean that needs the line stopped on a line that
        ran to the end of its order was not ignored, it never had its chance -
        so read them before offering a reason of your own. `started_at` null
        with a named `assigned_to` is a job nobody has walked over to yet.

        open_only leaves out the finished work; pass it false to see what this
        shift actually got done, each with `performed_by`, `findings` and
        `downtime_minutes` (0 for a job the machine ran through)."""
        path = f"/maintenance/orders?limit={limit}" + (f"&equipment={machine}" if machine else "")
        if open_only:
            # Open means "not finished", and an order somebody has been given
            # is open work. Leaving `assigned` out of this filter hid the one
            # row a maintenance question most often turns on: the job that was
            # handed out and has not been started.
            path += "&status=due&status=assigned&status=in_progress"
        out = call(plant, "GET", path)
        if isinstance(out, dict) and "error" in out:
            return out
        # The API pages this list; the agent is told how much it did not see.
        return {"plant": plant, "work": out["items"], "shown": len(out["items"]), "total": out["total"],
                "has_more": out["has_more"]}

    @mcp.tool()
    def maintenance_roster(plant: str, shift: str | None = None) -> dict:
        """Who is on shift, which trades they hold, and how much they already
        have on - the answer to "who is free with the electrical skill right
        now". shift is a key like 2026-10-09/DAY, `current` or `previous`;
        left out it is the shift running now. A plant that has told this MES no
        shift patterns has nobody rostered and says so, which is a finding about
        the calendar and not an empty crew."""
        path = "/maintenance/roster" + (f"?shift={shift}" if shift else "")
        return {"plant": plant, **call(plant, "GET", path)}

    @mcp.tool()
    def maintenance_rules(plant: str) -> dict:
        """The supervisor's dispatch rules in the order they are tried, with
        the trades this plant recognises and how many people hold each. A plant
        that has written none is answered with the one house default it is
        actually running on, marked as not being a row of its own."""
        return {"plant": plant, **call(plant, "GET", "/maintenance/rules")}

    @mcp.tool()
    def maintenance_explain(plant: str, order: str) -> dict:
        """Why this maintenance order went where it went: which rules were
        tried, who was considered, who was skipped and for what reason, and who
        got it. Reported as the walk the rules would make now, with what was
        actually recorded beside it - an hour-old decision and this minute's
        are two different answers."""
        return {"plant": plant, **call(plant, "GET", f"/maintenance/dispatch/{order}/explain")}

    @mcp.tool()
    def create_maintenance_plan(plant: str, code: str, name: str, machine: str,
                                trigger: str = "runtime_hours", interval: float = 200.0,
                                expected_minutes: float | None = None,
                                document_code: str | None = None,
                                dry_run: bool = False, on_behalf_of: str | None = None,
                                client_ref: str | None = None) -> dict:
        """Define a preventive plan on a machine. trigger is runtime_hours,
        produced_qty or calendar_days; interval is in that unit.
        expected_minutes left out takes this plant's own default for a new
        plan. Needs the maintenance.plan capability, which the agent role does
        not hold by default - an admin grants it per plant."""
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
    def dispatch_maintenance(plant: str, dry_run: bool = False,
                             on_behalf_of: str | None = None,
                             client_ref: str | None = None) -> dict:
        """Run the supervisor's rules over every order at `due` and hand each
        one to a free person who holds the trade, highest priority first. The
        plant's own tick does this too, so this is the same pass asked for by
        hand. Orders nobody can take keep their reason - no rule covers it,
        nobody on shift holds the skill, or they are all busy. Work a person
        assigned by hand is never taken back."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/maintenance/dispatch", {}, dry_run,
                     "run the dispatch rules over every due maintenance order")

    @mcp.tool()
    def assign_maintenance_order(plant: str, order: str, person: str,
                                 scheduled_for: str | None = None,
                                 dry_run: bool = False, on_behalf_of: str | None = None,
                                 client_ref: str | None = None) -> dict:
        """Give one maintenance order to a named person, by hand, overruling the
        rules - and the rules never take it back. A person who does not hold the
        order's trade is allowed and the audit row says so: a supervisor on the
        floor at two in the morning knows something the skills table does not.
        Needs the maintenance.plan capability, which the agent role does not
        hold by default - an admin grants it per plant."""
        identify(on_behalf_of, client_ref)
        return write(plant, f"/maintenance/orders/{order}/assign",
                     {"person": person, "scheduled_for": scheduled_for},
                     dry_run, f"assign maintenance order {order} to {person}")

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
                                    maintenance_roster, maintenance_rules,
                                    maintenance_explain,
                                    create_maintenance_plan, raise_due_maintenance,
                                    raise_corrective_maintenance, dispatch_maintenance,
                                    assign_maintenance_order, perform_maintenance)}
