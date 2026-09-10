"""Adjustment tools: propose a setpoint change with evidence; read the queue.

Agents propose, people approve. propose_adjustment is the agent's half of
the write-back story: a tag the manifest declares writable, a value inside
its bounds, a rationale, the evidence it looked at. Approval is a human
capability (adjustments.approve) and never a tool.
"""

from __future__ import annotations


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def adjustments(plant: str, status: str | None = None, limit: int = 50) -> dict:
        """The recommendation queue: proposed, approved (waiting for the agent
        to write), written (verification pending), verified (the process
        followed), failed, rejected - each with its rationale, evidence, who
        decided, and what the process did afterwards."""
        path = f"/adjustments?limit={limit}" + (f"&status={status}" if status else "")
        out = call(plant, "GET", path)
        if isinstance(out, dict) and "error" in out:
            return out
        return {"plant": plant, "adjustments": out["items"], "shown": len(out["items"]),
                "total": out["total"], "has_more": out["has_more"]}

    @mcp.tool()
    def adjustment(plant: str, code: str) -> dict:
        """One recommendation in full, including the verification: the
        process value before and after the write, and whether it followed."""
        return {"plant": plant, **call(plant, "GET", f"/adjustments/{code}")}

    @mcp.tool()
    def propose_adjustment(plant: str, machine: str, tag: str, value: float, rationale: str,
                           evidence: dict | None = None, dry_run: bool = False,
                           on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Recommend a setpoint change on a machine. The tag must be one the
        tag manifest declares writable (see browse_tags with writable_only)
        and the value inside its declared bounds, or this refuses. Nothing is
        written: an engineer approves on Engineering > Adjustments, the OPC
        agent writes, and the outcome is verified and recorded. Say what you
        saw and why this value; attach the evidence you looked at.

        On a plant in shadow mode (see list_plants) the proposal is
        recorded and may be approved, but it is never written to the
        machine. Say so when you report what you did: nothing reached
        the process."""
        identify(on_behalf_of, client_ref)
        body = {"equipment": machine, "tag": tag, "value": value, "rationale": rationale, "evidence": evidence}
        return write(plant, "/adjustments", body, dry_run, f"propose {machine}.{tag} -> {value:g}")

    return {f.__name__: f for f in (adjustments, adjustment, propose_adjustment)}
