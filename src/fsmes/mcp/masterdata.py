"""Master data tools: equipment, materials and their bills of material,
specifications, people, roles, documents.

All of it is masterdata.write or users.manage - human by default. The agent
reads everything (the tree, the materials, a BOM, the people), and what it
proposes to write comes back as a readable refusal until an admin grants
the capability to the agent role for that plant.
"""

from __future__ import annotations


def register(mcp, call, write, identify) -> dict:
    @mcp.tool()
    def materials(plant: str, q: str | None = None, type: str | None = None) -> dict:
        """Every material with its unit and type (raw, intermediate, finished);
        `q` matches a code or name, `type` narrows to raw, intermediate or finished."""
        params = "&".join(f"{k}={v}" for k, v in (("q", q), ("type", type)) if v)
        path = "/masterdata/materials" + (f"?{params}" if params else "")
        return {"plant": plant, "materials": call(plant, "GET", path)}

    @mcp.tool()
    def bill_of_material(plant: str, material: str) -> dict:
        """What a material is made of: each component, its quantity per unit,
        and the operation it enters at (null means unmodelled - it enters
        somewhere, which is exactly why a plant reads as a job shop)."""
        return {"plant": plant, "material": material,
                "components": call(plant, "GET", f"/masterdata/materials/{material}/bom")}

    @mcp.tool()
    def people(plant: str, q: str | None = None, role: str | None = None) -> dict:
        """Everyone the plant knows, with their role - or, with `q` (a code
        or name) or `role`, only those. A real plant is hundreds of people;
        ask by name rather than reading them all."""
        params = "&".join(f"{k}={v}" for k, v in (("q", q), ("role", role)) if v)
        path = "/masterdata/personnel" + (f"?{params}" if params else "")
        return {"plant": plant, "people": call(plant, "GET", path)}

    @mcp.tool()
    def create_equipment(plant: str, code: str, name: str, level: str = "work_unit",
                         parent: str | None = None, ideal_cycle_seconds: float | None = None,
                         cost_center: str | None = None, dry_run: bool = False,
                         on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Add a node to the equipment tree: level is enterprise, site, area,
        work_center (a line) or work_unit (a machine); parent is the node it
        hangs off; cost_center is inherited by everything beneath unless
        overridden."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/masterdata/equipment",
                     {"code": code, "name": name, "level": level, "parent": parent,
                      "ideal_cycle_seconds": ideal_cycle_seconds, "cost_center": cost_center},
                     dry_run, f"add {level} {code}" + (f" under {parent}" if parent else ""))

    @mcp.tool()
    def create_material(plant: str, code: str, name: str, unit: str = "ea", type: str = "raw",
                        dry_run: bool = False, on_behalf_of: str | None = None,
                        client_ref: str | None = None) -> dict:
        """Add a material: raw, intermediate or finished."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/masterdata/materials",
                     {"code": code, "name": name, "unit": unit, "type": type},
                     dry_run, f"add {type} material {code}")

    @mcp.tool()
    def add_bom_component(plant: str, material: str, component: str, quantity: float,
                          operation_seq: int | None = None, dry_run: bool = False,
                          on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Put a component on a material's bill, with the operation it
        enters at - the thing that makes staging and genealogy name a station."""
        identify(on_behalf_of, client_ref)
        return write(plant, f"/masterdata/materials/{material}/bom",
                     {"component": component, "quantity": quantity, "operation_seq": operation_seq},
                     dry_run, f"add {quantity} x {component} to {material}"
                     + (f" at op {operation_seq}" if operation_seq is not None else ""))

    @mcp.tool()
    def add_person(plant: str, code: str, name: str, role: str = "operator", dry_run: bool = False,
                   on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Add a person without a sign-in (create_user makes an account).
        users.manage - human by default."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/masterdata/personnel", {"code": code, "name": name, "role": role},
                     dry_run, f"add person {code} as {role}")

    @mcp.tool()
    def update_role(plant: str, code: str, name: str, capabilities: list[str],
                    description: str | None = None, dry_run: bool = False,
                    on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Change what a role grants. Effective on everyone's next action.
        users.manage - human by default, and the agent never holds it."""
        identify(on_behalf_of, client_ref)
        body = {"code": code, "name": name, "description": description, "capabilities": capabilities}
        if dry_run:
            return {"dry_run": True, "plant": plant, "would": f"redefine role {code}",
                    "request": {"method": "PUT", "path": f"/admin/roles/{code}", "body": body}}
        result = call(plant, "PUT", f"/admin/roles/{code}", body)
        if isinstance(result, dict) and "error" in result:
            return result
        return {"done": f"redefine role {code}", "plant": plant, "response": result, "audited_as": "AGENT"}

    @mcp.tool()
    def create_document(plant: str, code: str, title: str, body: str = "",
                        anchors: dict | None = None, dry_run: bool = False,
                        on_behalf_of: str | None = None, client_ref: str | None = None) -> dict:
        """Create a controlled document as a draft (documents.write). It is not
        in force until a person approves it; approval is never a tool."""
        identify(on_behalf_of, client_ref)
        return write(plant, "/documents", {"code": code, "title": title, "body": body, "anchors": anchors},
                     dry_run, f"draft document {code}")

    return {f.__name__: f for f in (materials, bill_of_material, people, create_equipment, create_material,
                                    add_bom_component, add_person, update_role, create_document)}
