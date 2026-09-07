"""Certificate tools: read a certificate, its data, or issue one."""

from __future__ import annotations


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def certificates(plant: str, limit: int = 50) -> dict:
        """Every certificate of analysis in force, newest first."""
        out = call(plant, "GET", f"/coa?limit={limit}")
        if isinstance(out, dict) and "error" in out:
            return out
        return {"plant": plant, "certificates": out["items"], "shown": len(out["items"]),
                "total": out["total"], "has_more": out["has_more"]}

    @mcp.tool()
    def pallet_certificate(plant: str, pallet: str, as_data: bool = False) -> dict:
        """A pallet's certificate of analysis: the Cpk of every dimensional
        characteristic over the window its contents were made, the automated
        inspections, and every wrap, stack, plate and piece on it - as the
        issued Markdown, or as data."""
        path = f"/coa/pallet/{pallet}/data" if as_data else f"/coa/pallet/{pallet}"
        return {"plant": plant, **call(plant, "GET", path)}

    @mcp.tool()
    def issue_pallet_certificate(plant: str, pallet: str, dry_run: bool = False,
                                 on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Issue a pallet's certificate of analysis - a supervisor's act
        (quality.close_nc), immutable once issued."""
        identify(on_behalf_of, client_ref)
        return write(plant, f"/coa/pallet/{pallet}", {}, dry_run, f"issue certificate of analysis for pallet {pallet}")

    @mcp.tool()
    def certificate(plant: str, order: str, as_data: bool = False) -> dict:
        """The certificate for an order - the revision in force as Markdown,
        or with as_data=True the facts it states: finished lot, genealogy,
        every characteristic against its spec, non-conformances, the process
        envelope each station held while the order ran, serials."""
        path = f"/coa/{order}/data" if as_data else f"/coa/{order}"
        return {"plant": plant, **call(plant, "GET", path)}

    @mcp.tool()
    def issue_certificate(plant: str, order: str, dry_run: bool = False, on_behalf_of: str | None = None,
                          client_ref: str | None = None) -> dict:
        """Issue or reissue the certificate for a completed order. Reissuing
        supersedes the earlier revision and names it; nothing is edited in
        place. A supervisor's act (quality.close_nc) - human by default."""
        identify(on_behalf_of, client_ref)
        return write(plant, f"/coa/{order}", {}, dry_run, f"issue certificate of analysis for {order}")

    return {f.__name__: f for f in (certificates, certificate, issue_certificate,
                                    pallet_certificate, issue_pallet_certificate)}
