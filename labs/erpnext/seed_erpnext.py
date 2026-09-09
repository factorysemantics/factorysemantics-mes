r"""Seed an ERPNext site with the MES-TWIN plant, so the two speak one language.

    .venv\Scripts\python.exe labs\erpnext\seed_erpnext.py
    .venv\Scripts\python.exe labs\erpnext\seed_erpnext.py --order 500   # + a work order

The contract between the two systems is vocabulary: an ERPNext Work Order's
`production_item` has to be a material code the MES knows, and its work-order
name becomes the MES order code. Everything here exists to make that true —
the same items, the same six stations, the same rated cycle times, read from
the same tag map the OPC agent reads.

Idempotent: every step checks before it creates, so running it twice is a
no-op and running it after a partial failure finishes the job.

This writes into ONE site of a possibly shared bench, chosen by the Host
header (MES_ERPNEXT_SITE, default mes.localhost). That is deliberate — the
same bench also serves Shortline Filtration Works for factorysemantics, and
seeding into the wrong one would scribble over another company's books.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from fsmes.config import get_settings  # noqa: E402
from fsmes.integrations.erp.erpnext_adapter import ErpNextClient, ErpNextError  # noqa: E402
from fsmes.integrations.opc.tag_map import load_tag_map  # noqa: E402
from fsmes.seed_kepsim import STATIONS, TAG_MAP  # noqa: E402

COMPANY = "ACME Beverages"
ABBR = "ACME"
COUNTRY = "United States"
CURRENCY = "USD"

FINISHED = "FG-BOTTLE"
RAW = [
    # (code, name, uom, qty per finished unit, stocked qty, unit cost)
    ("RAW-PREFORM", "Bottle Preform", "Nos", 1.0, 100000, 0.05),
    ("RAW-WATER", "Treated Water", "Litre", 0.5, 50000, 0.01),
]

# What ERPNext has nowhere to put: whether the MES has taken this order, and
# what the machines actually counted. allow_on_submit because a submitted work
# order is precisely when these change.
CUSTOM_FIELDS = [
    {
        "fieldname": "custom_mes_synced",
        "label": "Sent to MES",
        "fieldtype": "Check",
        "insert_after": "status",
        "allow_on_submit": 1,
        "read_only": 1,
        "description": "Set by MES-TWIN when it imports this order. Clear it to re-send.",
    },
    {
        "fieldname": "custom_mes_good_qty",
        "label": "MES Good Qty",
        "fieldtype": "Float",
        "insert_after": "custom_mes_synced",
        "allow_on_submit": 1,
        "read_only": 1,
    },
    {
        "fieldname": "custom_mes_scrap_qty",
        "label": "MES Scrap Qty",
        "fieldtype": "Float",
        "insert_after": "custom_mes_good_qty",
        "allow_on_submit": 1,
        "read_only": 1,
        "description": "Machine-counted scrap. ERPNext has no native field for this.",
    },
    {
        "fieldname": "custom_mes_over_qty",
        "label": "MES Over Qty",
        "fieldtype": "Float",
        "insert_after": "custom_mes_scrap_qty",
        "allow_on_submit": 1,
        "read_only": 1,
        "description": "How far past the ordered quantity the line actually ran. A counter "
                       "delta can carry more than one unit, so an order for 15 can finish at "
                       "16 good; this is the number that says so, rather than leaving a "
                       "reader to notice that the good qty exceeds the order.",
    },
    {
        "fieldname": "custom_mes_lot",
        "label": "MES Lot",
        "fieldtype": "Data",
        "insert_after": "custom_mes_over_qty",
        "allow_on_submit": 1,
        "read_only": 1,
    },
]


def log(message: str) -> None:
    print(message, flush=True)


def ensure_custom_fields(client: ErpNextClient) -> None:
    for field in CUSTOM_FIELDS:
        name = f"Work Order-{field['fieldname']}"
        if client.exists("Custom Field", name):
            log(f"  custom field {field['fieldname']:<22} exists")
            continue
        client.insert("Custom Field", {"dt": "Work Order", **field})
        log(f"  custom field {field['fieldname']:<22} created")


def ensure_company(client: ErpNextClient) -> None:
    if client.exists("Company", COMPANY):
        log(f"  company {COMPANY} exists")
        return
    client.insert(
        "Company",
        {
            "company_name": COMPANY,
            "abbr": ABBR,
            "default_currency": CURRENCY,
            "country": COUNTRY,
        },
    )
    log(f"  company {COMPANY} created (warehouses come with it)")


def ensure_item(client: ErpNextClient, code: str, name: str, group: str, uom: str) -> None:
    if client.exists("Item", code):
        log(f"  item {code:<14} exists")
        return
    client.insert(
        "Item",
        {
            "item_code": code,
            "item_name": name,
            "item_group": group,
            "stock_uom": uom,
            "is_stock_item": 1,
            "include_item_in_manufacturing": 1,
            "default_material_request_type": "Manufacture" if group == "Products" else "Purchase",
        },
    )
    log(f"  item {code:<14} created")


def ensure_workstations(client: ErpNextClient) -> dict[str, float]:
    """One workstation per MES station, with the tag map's rated cycle time."""
    cycles = {m.equipment: m.cycle_seconds for m in load_tag_map(REPO / TAG_MAP)}
    for code, name, _step in STATIONS:
        if client.exists("Workstation", code):
            log(f"  workstation {code:<8} exists")
            continue
        client.insert(
            "Workstation",
            {
                "workstation_name": code,
                "description": name,
                "hour_rate_electricity": 0,
                "hour_rate_labour": 0,
                "production_capacity": 1,
            },
        )
        log(f"  workstation {code:<8} created ({name})")
    return cycles


def ensure_operations(client: ErpNextClient) -> None:
    for code, _name, step in STATIONS:
        if client.exists("Operation", step):
            log(f"  operation {step:<12} exists")
            continue
        client.insert("Operation", {"name": step, "operation_name": step, "workstation": code})
        log(f"  operation {step:<12} created (on {code})")


def _operation_rows(cycles: dict[str, float]) -> list[dict]:
    return [
        {
            "operation": step,
            "workstation": code,
            # ERPNext works in minutes per BOM quantity; the MES works in
            # seconds per unit. Same number, different clothes.
            "time_in_mins": round(cycles.get(code, 1.0) / 60.0, 6),
            "hour_rate": 0,
            "sequence_id": index + 1,
        }
        for index, (code, _name, step) in enumerate(STATIONS)
    ]


def ensure_routing(client: ErpNextClient, cycles: dict[str, float]) -> str:
    name = "Fill and Palletise Bottles"
    if client.exists("Routing", name):
        log(f"  routing {name!r} exists")
        return name
    client.insert(
        "Routing",
        {"routing_name": name, "name": name, "operations": _operation_rows(cycles), "disabled": 0},
    )
    log(f"  routing {name!r} created ({len(STATIONS)} operations)")
    return name


def ensure_stock(client: ErpNextClient) -> None:
    """Put raw material on hand, so Manufacture entries have something to consume.

    Without this the BOM has no valuation to work from and every stock entry the
    MES posts back would fail on insufficient stock — the integration would look
    broken when the real problem is an empty warehouse.
    """
    warehouse = f"Stores - {ABBR}"
    existing = client.list(
        "Stock Entry",
        filters=[["stock_entry_type", "=", "Material Receipt"], ["docstatus", "=", 1], ["company", "=", COMPANY]],
        fields=["name"],
        limit=1,
    )
    if existing:
        log(f"  opening stock exists ({existing[0]['name']})")
        return
    client.insert(
        "Stock Entry",
        {
            "stock_entry_type": "Material Receipt",
            "company": COMPANY,
            "docstatus": 1,
            "items": [
                {"item_code": code, "qty": qty, "t_warehouse": warehouse, "basic_rate": rate, "uom": uom}
                for code, _name, uom, _per, qty, rate in RAW
            ],
        },
    )
    log(f"  opening stock received into {warehouse}")


def ensure_bom(client: ErpNextClient, routing: str, cycles: dict[str, float]) -> str:
    existing = client.list(
        "BOM", filters=[["item", "=", FINISHED], ["docstatus", "=", 1]], fields=["name"], limit=1
    )
    if existing:
        log(f"  BOM {existing[0]['name']} exists")
        return existing[0]["name"]
    bom = client.insert(
        "BOM",
        {
            "item": FINISHED,
            "company": COMPANY,
            "quantity": 1,
            "currency": CURRENCY,
            "is_active": 1,
            "is_default": 1,
            "with_operations": 1,
            "routing": routing,
            "docstatus": 1,
            "operations": _operation_rows(cycles),
            "items": [
                {"item_code": code, "qty": per, "uom": uom, "rate": rate}
                for code, _name, uom, per, _qty, rate in RAW
            ],
        },
    )
    log(f"  BOM {bom['name']} created and submitted")
    return bom["name"]


def create_work_order(client: ErpNextClient, bom: str, quantity: float) -> str:
    order = client.insert(
        "Work Order",
        {
            "production_item": FINISHED,
            "bom_no": bom,
            "qty": quantity,
            "company": COMPANY,
            "wip_warehouse": f"Work In Progress - {ABBR}",
            "fg_warehouse": f"Finished Goods - {ABBR}",
            "source_warehouse": f"Stores - {ABBR}",
            "use_multi_level_bom": 0,
            "skip_transfer": 1,  # the MES tracks material issue; ERPNext need not gate on it
            "docstatus": 1,
        },
    )
    log(f"\nWork order {order['name']} submitted: {quantity:g} x {FINISHED}")
    log("MES-TWIN will pick it up on the next ERP sync poll.")
    return order["name"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--order", type=float, metavar="QTY", help="also submit a work order for QTY units")
    parser.add_argument("--url", default=None, help="ERPNext base URL (default from settings)")
    parser.add_argument("--site", default=None, help="site to seed, sent as Host (default from settings)")
    args = parser.parse_args()

    settings = get_settings()
    url = args.url or settings.erpnext_base_url
    site = args.site if args.site is not None else settings.erpnext_site

    log(f"Seeding {url} (site {site or 'default'}) with the MES-TWIN plant\n")
    try:
        client = ErpNextClient(
            url,
            site=site,
            user=settings.erpnext_user,
            password=settings.erpnext_password,
            api_key=settings.erpnext_api_key,
            api_secret=settings.erpnext_api_secret,
        )
    except ErpNextError as exc:
        log(f"Could not connect: {exc}")
        return 2

    try:
        ensure_custom_fields(client)
        ensure_company(client)
        ensure_item(client, FINISHED, "Filled Bottle 500ml", "Products", "Nos")
        for code, name, uom, _per, _qty, _rate in RAW:
            ensure_item(client, code, name, "Raw Material", uom)
        cycles = ensure_workstations(client)
        ensure_operations(client)
        routing = ensure_routing(client, cycles)
        ensure_stock(client)
        bom = ensure_bom(client, routing, cycles)
        if args.order:
            create_work_order(client, bom, args.order)
    except ErpNextError as exc:
        log(f"\nFAILED: {exc}")
        return 1
    finally:
        client.close()

    log(f"\nDone. ERPNext desk: {url}/app  (Host: {site})")
    log("Run the MES against it with:  set MES_ERP_MODE=erpnext")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
