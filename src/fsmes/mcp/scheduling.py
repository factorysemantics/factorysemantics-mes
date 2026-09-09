"""Scheduling tools: the calendar the plant runs on, the board, what the
plan promises, and planning itself.

Planning is a scheduling.plan capability - a person's by default. The agent
can read the board and every promise, and say what it would plan; an admin
who grants the capability lets it plan for a named planner.
"""

from __future__ import annotations


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def plant_calendar(plant: str) -> dict:
        """When the plant runs: shift patterns (with the days they run and
        whether they cross midnight), shutdown and overtime exceptions. Every
        promised date rests on this; a plant with no shifts is treated as
        running continuously, and the answer says so."""
        return {"plant": plant, **call(plant, "GET", "/scheduling/calendar")}

    @mcp.tool()
    def schedule_board(plant: str, machine: str | None = None, hours: float = 24.0) -> dict:
        """The schedule machine by machine over the next `hours`: production
        slots with their order and operation, and the maintenance placed
        before them."""
        path = f"/scheduling/board?hours={hours}" + (f"&equipment={machine}" if machine else "")
        return {"plant": plant, **call(plant, "GET", path)}

    @mcp.tool()
    def order_promise(plant: str, order: str) -> dict:
        """When the plan says this order finishes, and whether that misses
        its due date - or that it has not been planned."""
        return {"plant": plant, **call(plant, "GET", f"/scheduling/promise/{order}")}

    @mcp.tool()
    def plan_order(plant: str, order: str, start: str | None = None, dry_run: bool = False,
                   on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Schedule one order's operations in route sequence through working
        time, replacing any earlier plan for it. `start` is an ISO datetime;
        omitted means now."""
        identify(on_behalf_of, client_ref)
        return write(plant, f"/scheduling/plan/{order}", {"start": start}, dry_run,
                     f"plan {order}" + (f" from {start}" if start else ""))

    @mcp.tool()
    def plan_all_orders(plant: str, start: str | None = None, dry_run: bool = False,
                        on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Schedule every open order, highest priority first."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/scheduling/plan", {"start": start}, dry_run, "plan every open order")

    @mcp.tool()
    def add_shift(plant: str, code: str, name: str, starts: str, ends: str, days: str = "1111100",
                  machine: str | None = None, dry_run: bool = False,
                  on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Add a shift pattern: starts/ends as HH:MM, days as seven 1/0 flags
        Monday to Sunday, optionally for one machine only."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/scheduling/calendar/shifts",
                     {"code": code, "name": name, "starts": starts, "ends": ends, "days": days,
                      "equipment": machine},
                     dry_run, f"add shift {code} {starts}-{ends} on {days}")

    @mcp.tool()
    def add_calendar_exception(plant: str, day: str, kind: str, reason: str, machine: str | None = None,
                               dry_run: bool = False, on_behalf_of: str | None = None,
                               client_ref: str | None = None) -> dict:
        """A shutdown day (capacity removed) or an overtime day (capacity
        added), for the plant or one machine. `day` is YYYY-MM-DD."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/scheduling/calendar/exceptions",
                     {"day": day, "kind": kind, "reason": reason, "equipment": machine},
                     dry_run, f"{kind} on {day}: {reason}")

    return {f.__name__: f for f in (plant_calendar, schedule_board, order_promise, plan_order,
                                    plan_all_orders, add_shift, add_calendar_exception)}
