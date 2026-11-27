r"""Seed an ERPNext site with the cutlery plant's coil-out order, as a second company.

    .venv/bin/python labs/erpnext/seed_cutlery.py              # seed, and submit the order
    .venv/bin/python labs/erpnext/seed_cutlery.py --no-order   # seed only
    .venv/bin/python labs/erpnext/seed_cutlery.py --work-order WO-FORK1-0003   # a later run

Every id comes from the `erp` section of `labs/cutlery/scenes/coil-out-erp.json`,
the one file the MES side of the scene and the film takes read too: the
company, the fork and its resin, FORK1's work center and cost center, the
spare heater band at its price, and the work order's number.

What it makes, each only if it is not there yet (so a second run changes
nothing and a run after a partial failure finishes the job):

* the company, beside the bottling demo's; its warehouses come with it;
* the fork, the resin and the heater band as stock items (items are the
  site's, not a company's, so the bottling company sees them in its lists);
* FORK1LINE as a workstation and CC-MOULD01 as a cost center of the company;
* the resin and the one band received into the company's stores, at the
  scene's prices; that is what puts "$140" on the band in ERPNext;
* a BOM for the fork that uses the scene's grams of resin a fork;
* the work order, submitted, under the series that gives it the scene's
  number (`WO-FORK1-.####` -> the next free number, which the scene's ids
  name; the seed stops if ERPNext gives another). The series is added to the
  Work Order's naming options with a Property Setter, the way a person does
  it in Customize Form; the bottling demo's series stays the default.

And one site setting: Manufacturing Settings' over-production allowance,
raised to the scene's percent if it is lower, because the floor finishes an
order a few forks past its quantity and ERPNext refuses the Manufacture
entry for the whole order past its allowance (that refusal is
`tests/test_erpnext_live.py`'s to measure, not this film's).

Nothing here touches the ERPNext connection: the MES takes the order with
`fsmes run-erp-sync` as it takes any other, filtered to this company with
`MES_ERPNEXT_COMPANY`. The login is the one in settings, as for
`seed_erpnext.py`; none is printed.

Like `seed_erpnext.py`, this expects a site whose setup wizard has been run,
and writes into the one site the Host header names (MES_ERPNEXT_SITE).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from fsmes.config import get_settings  # noqa: E402
from fsmes.integrations.erp import erpnext_setup  # noqa: E402
from fsmes.integrations.erp.erpnext_adapter import ErpNextClient, ErpNextError  # noqa: E402

SCENE = REPO / "labs" / "cutlery" / "scenes" / "coil-out-erp.json"
COUNTRY = "United States"
ALLOWANCE_FIELD = "overproduction_percentage_for_work_order"


def log(message: str) -> None:
    print(message, flush=True)


def ids(scene: Path = SCENE) -> dict:
    """The scene's `erp` section: the ids ERPNext and the MES share."""
    return json.loads(scene.read_text(encoding="utf-8"))["erp"]


def ensure_company(client: ErpNextClient, erp: dict) -> None:
    if client.exists("Company", erp["company"]):
        log(f"  company {erp['company']} exists")
        return
    client.insert("Company", {"company_name": erp["company"], "abbr": erp["abbr"],
                              "default_currency": erp["currency"], "country": COUNTRY})
    log(f"  company {erp['company']} created (warehouses come with it)")


def ensure_item(client: ErpNextClient, code: str, name: str, group: str, uom: str,
                rate: float | None = None) -> None:
    if client.exists("Item", code):
        # The item form shows its own valuation rate on its first tab; left
        # at 0 beside a band received at 140 it reads as a free part.
        if rate is not None and not client.get("Item", code).get("valuation_rate"):
            client.update("Item", code, {"valuation_rate": rate})
            log(f"  item {code:<16} exists; valuation rate set to {rate:.2f}")
            return
        log(f"  item {code:<16} exists")
        return
    body = {"item_code": code, "item_name": name, "item_group": group, "stock_uom": uom,
            "is_stock_item": 1, "include_item_in_manufacturing": 1,
            "default_material_request_type": "Manufacture" if group == "Products" else "Purchase"}
    if rate is not None:
        body["valuation_rate"] = rate
    client.insert("Item", body)
    log(f"  item {code:<16} created")


def ensure_uom(client: ErpNextClient, uom: str) -> None:
    if not client.exists("UOM", uom):
        client.insert("UOM", {"uom_name": uom})
        log(f"  unit {uom} created")


def ensure_workstation(client: ErpNextClient, erp: dict) -> None:
    code = erp["work_center"]
    if client.exists("Workstation", code):
        log(f"  workstation {code} exists")
        return
    client.insert("Workstation", {"workstation_name": code,
                                  "description": "Fork moulding line 1: " + ", ".join(erp["machines"]),
                                  "hour_rate_electricity": 0, "hour_rate_labour": 0,
                                  "production_capacity": 1})
    log(f"  workstation {code} created")


def cost_center_name(erp: dict) -> str:
    """ERPNext names a cost center after its company: `CC-MOULD01 - CW`."""
    return f"{erp['cost_center']} - {erp['abbr']}"


def ensure_cost_center(client: ErpNextClient, erp: dict) -> str:
    name = cost_center_name(erp)
    if client.exists("Cost Center", name):
        log(f"  cost center {name} exists")
        return name
    client.insert("Cost Center", {"cost_center_name": erp["cost_center"], "company": erp["company"],
                                  "parent_cost_center": f"{erp['company']} - {erp['abbr']}",
                                  "is_group": 0})
    log(f"  cost center {name} created")
    return name


def ensure_stock(client: ErpNextClient, erp: dict) -> None:
    """The resin and the spare band received into the company's stores, at the scene's prices.

    Booked to the company's own main cost center, not the mould's: opening
    stock is nobody's cost, and the film reads CC-MOULD01 to show what the
    breakdown did and did not put there. Anything on it must be the run's.
    """
    stores = f"Stores - {erp['abbr']}"
    have = client.list("Stock Entry", fields=["name"], limit=1,
                       filters=[["stock_entry_type", "=", "Material Receipt"], ["docstatus", "=", 1],
                                ["company", "=", erp["company"]]])
    if have:
        log(f"  opening stock exists ({have[0]['name']})")
        return
    rows = [(erp["material"], stores), (erp["spare"], stores)]
    client.insert("Stock Entry", {
        "stock_entry_type": "Material Receipt", "company": erp["company"], "docstatus": 1,
        "items": [{"item_code": row["code"], "qty": row["on_hand"], "uom": row["uom"],
                   "t_warehouse": where, "basic_rate": row["price"],
                   "cost_center": f"Main - {erp['abbr']}"}
                  for row, where in rows]})
    log(f"  opening stock received into {stores}: {erp['material']['on_hand']:g} "
        f"{erp['material']['uom']} {erp['material']['code']}, {erp['spare']['on_hand']:g} "
        f"{erp['spare']['code']} at {erp['spare']['price']:.2f}")


def ensure_bom(client: ErpNextClient, erp: dict) -> str:
    have = client.list("BOM", fields=["name"], limit=1,
                       filters=[["item", "=", erp["item"]], ["docstatus", "=", 1],
                                ["company", "=", erp["company"]]])
    if have:
        log(f"  BOM {have[0]['name']} exists")
        return have[0]["name"]
    material = erp["material"]
    bom = client.insert("BOM", {
        "item": erp["item"], "company": erp["company"], "quantity": 1, "currency": erp["currency"],
        "is_active": 1, "is_default": 1, "with_operations": 0, "docstatus": 1,
        "items": [{"item_code": material["code"], "qty": material["per_unit"], "uom": material["uom"],
                   "rate": material["price"]}]})
    log(f"  BOM {bom['name']} created and submitted ({material['per_unit']:g} {material['uom']} "
        f"{material['code']} a fork)")
    return bom["name"]


def ensure_series(client: ErpNextClient, erp: dict) -> None:
    """Offer the scene's series on the Work Order, beside whatever is already offered."""
    series = erp["naming_series"]
    # The options as the desk sees them, Property Setters applied. A GET, so
    # the client's own request is used: `call` posts.
    meta = client._request("GET", "/api/method/frappe.desk.form.load.getdoctype",
                           params={"doctype": "Work Order"}).json()
    field = next(f for f in meta["docs"][0]["fields"] if f["fieldname"] == "naming_series")
    options = [o for o in (field.get("options") or "").split("\n") if o]
    if series in options:
        log(f"  naming series {series} offered")
        return
    setter = client.list("Property Setter", fields=["name"], limit=1,
                         filters=[["doc_type", "=", "Work Order"], ["field_name", "=", "naming_series"],
                                  ["property", "=", "options"]])
    value = "\n".join([*options, series])
    if setter:
        client.update("Property Setter", setter[0]["name"], {"value": value})
    else:
        client.insert("Property Setter", {"doctype_or_field": "DocField", "doc_type": "Work Order",
                                          "field_name": "naming_series", "property": "options",
                                          "property_type": "Text", "value": value})
    log(f"  naming series {series} added to the Work Order's options")


def ensure_allowance(client: ErpNextClient, erp: dict) -> None:
    now = float(client.get("Manufacturing Settings", "Manufacturing Settings").get(ALLOWANCE_FIELD) or 0)
    want = float(erp["overproduction_percent"])
    if now >= want:
        log(f"  over-production allowance {now:g} % (needs {want:g} %)")
        return
    client.call("frappe.client.set_value", doctype="Manufacturing Settings",
                name="Manufacturing Settings", fieldname=ALLOWANCE_FIELD, value=want)
    log(f"  over-production allowance raised from {now:g} % to {want:g} %")


def ensure_work_order(client: ErpNextClient, erp: dict, bom: str) -> str:
    code = erp["work_order"]
    if client.exists("Work Order", code):
        log(f"  work order {code} exists")
        return code
    abbr = erp["abbr"]
    order = client.insert("Work Order", {
        "naming_series": erp["naming_series"], "production_item": erp["item"], "bom_no": bom,
        "qty": erp["quantity"], "company": erp["company"],
        "wip_warehouse": f"Work In Progress - {abbr}", "fg_warehouse": f"Finished Goods - {abbr}",
        "source_warehouse": f"Stores - {abbr}", "use_multi_level_bom": 0,
        "skip_transfer": 1, "docstatus": 1})
    if order["name"] != code:
        # The series had been used before (an order cancelled and deleted
        # keeps its number spent). Say it rather than film the wrong number.
        raise ErpNextError(f"ERPNext named the order {order['name']}, not {code}: the series "
                           f"{erp['naming_series']} has been used on this site before")
    log(f"  work order {code} submitted: {erp['quantity']:g} x {erp['item']}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-order", action="store_true", help="seed, but do not submit the order")
    parser.add_argument("--work-order", default=None,
                        help="the number this site's series will give the order, when it has spent the "
                             "ids file's on an earlier run (default: the ids file's)")
    parser.add_argument("--url", default=None, help="ERPNext base URL (default from settings)")
    parser.add_argument("--site", default=None, help="site to seed, sent as Host (default from settings)")
    args = parser.parse_args()

    settings = get_settings()
    url = args.url or settings.erpnext_base_url
    site = args.site if args.site is not None else settings.erpnext_site
    erp = ids()
    if args.work_order:
        erp["work_order"] = args.work_order

    log(f"Seeding {url} (site {site or 'default'}) with {erp['company']}\n")
    try:
        client = ErpNextClient(url, site=site, user=settings.erpnext_user,
                               password=settings.erpnext_password, api_key=settings.erpnext_api_key,
                               api_secret=settings.erpnext_api_secret)
    except ErpNextError as exc:
        log(f"Could not connect: {exc}")
        return 2
    try:
        for step in erpnext_setup.ensure_custom_fields(client):
            log(f"  custom field {step.name:<22} {step.outcome}")
        ensure_company(client, erp)
        for row in (erp["material"], erp["spare"]):
            ensure_uom(client, row["uom"])
        ensure_item(client, erp["item"], erp["item_name"], "Products", "Nos")
        ensure_item(client, erp["material"]["code"], erp["material"]["name"], "Raw Material",
                    erp["material"]["uom"], erp["material"]["price"])
        ensure_item(client, erp["spare"]["code"], erp["spare"]["name"], "Consumable", erp["spare"]["uom"],
                    erp["spare"]["price"])
        ensure_workstation(client, erp)
        ensure_cost_center(client, erp)
        ensure_stock(client, erp)
        bom = ensure_bom(client, erp)
        ensure_series(client, erp)
        ensure_allowance(client, erp)
        if not args.no_order:
            ensure_work_order(client, erp, bom)
    except ErpNextError as exc:
        log(f"\nFAILED: {exc}")
        return 1
    finally:
        client.close()
    log(f"\nDone. ERPNext desk: {url}/app  (Host: {site})")
    log(f"The MES takes the order with MES_ERP_MODE=erpnext MES_ERPNEXT_COMPANY='{erp['company']}' "
        "fsmes run-erp-sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
