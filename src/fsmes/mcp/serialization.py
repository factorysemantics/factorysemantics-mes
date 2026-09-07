"""Serialisation tools: trace a thing, not a batch.

A serial answers what is inside it and what went into it; a lot answers
where it went, as the packages a warehouse can pull. Producing and packing
are floor work (production.book, which the agent holds); holding or
releasing a unit is a supervisor's disposition (quality.close_nc), human by
default.
"""

from __future__ import annotations


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def unit(plant: str, serial: str) -> dict:
        """One identified unit and everything packed inside it, as a tree:
        bottle in case on pallet, each with its status and the order that
        made it."""
        return {"plant": plant, **call(plant, "GET", f"/trace/units/{serial}")}

    @mcp.tool()
    def unit_trace(plant: str, serial: str) -> dict:
        """What went into this unit and everything inside it: every lot, and
        the machine and operation where it entered - the question asked when
        a customer complains about one pack."""
        return {"plant": plant, **call(plant, "GET", f"/trace/units/{serial}/trace")}

    @mcp.tool()
    def where_used(plant: str, lot: str) -> dict:
        """Every package carrying a lot - the recall question, answered as
        pallets and cases a warehouse can actually pull rather than a list of
        ten thousand bottle serials."""
        return {"plant": plant, **call(plant, "GET", f"/trace/where-used/{lot}")}

    @mcp.tool()
    def produce_units(plant: str, material: str, count: int = 1, order: str | None = None,
                      machine: str | None = None, serial: str | None = None, dry_run: bool = False,
                      on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Bring identified units into existence for a material, optionally
        against an order and on a machine. `serial` names a single unit;
        otherwise serials are minted."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/trace/units",
                     {"material": material, "order": order, "equipment": machine,
                      "serial": serial, "count": count},
                     dry_run, f"produce {count} unit(s) of {material}")

    @mcp.tool()
    def produce_batch(plant: str, serials: list[str], material: str, container: str | None = None,
                      container_material: str | None = None, into: str | None = None,
                      contains: list[str] | None = None, order: str | None = None,
                      machine: str | None = None, dry_run: bool = False,
                      on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Bring a batch of identified units into existence in one call, packed:
        the pieces in a stack with the stack's own serial (`container`), or
        existing units moved into a new or existing container (`contains`,
        `into`). What a marker or a palletizer sends; at most 5,000 units."""
        identify(on_behalf_of, client_ref)
        body = {"units": [{"serial": s} for s in serials], "material": material, "order": order,
                "equipment": machine, "into": into, "contains": contains or []}
        if container:
            body["container"] = {"serial": container, "material": container_material or material}
        what = f"produce {len(serials)} unit(s) of {material}"
        if container:
            what += f" in {container}"
        if contains:
            what += f", packing {len(contains)} unit(s)"
        return write(plant, "/trace/units/batch", body, dry_run, what)

    @mcp.tool()
    def pack_unit(plant: str, serial: str, into: str, dry_run: bool = False,
                  on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Put one unit inside another: bottle into case, case onto pallet.
        Loops are refused."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/trace/units/pack", {"serial": serial, "into": into},
                     dry_run, f"pack {serial} into {into}")

    @mcp.tool()
    def set_unit_status(plant: str, serial: str, status: str, note: str | None = None,
                        cascade: bool = True, dry_run: bool = False,
                        on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Quarantine, release or scrap a unit and, by default, everything
        inside it - holding a pallet without holding its cases holds nothing.
        A supervisor's disposition (quality.close_nc): human by default."""
        identify(on_behalf_of, client_ref)
        return write(plant, f"/trace/units/{serial}/status",
                     {"status": status, "note": note, "cascade": cascade},
                     dry_run, f"set {serial} to {status}" + (" with everything inside" if cascade else ""))

    return {f.__name__: f for f in (unit, unit_trace, where_used, produce_units, produce_batch,
                                    pack_unit, set_unit_status)}
