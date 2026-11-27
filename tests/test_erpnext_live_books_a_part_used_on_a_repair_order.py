"""A part used on a repair order, booked in a real ERPNext and read back.

`test_a_repair_order_closed_with_a_part_tells_the_erp_once.py` scripts the
transport. This asks ERPNext itself, and believes only what ERPNext then
shows: the stock ledger, the general ledger and the entry's remarks.

Two halves:

* **Any seeded site** (`labs/erpnext/seed_erpnext.py`, the CI site): a test
  part is received into the bottling company's stores at 10.00, issued for a
  repair through `ErpNextAdapter.send_part_used`, and the ledgers read back.
  It adds, if they are not there, one stock item (SP-TEST-PART) and one cost
  center (CC-PARTTEST) to that company; each test receives and issues one
  part, so the stock it leaves is what it found.
* **After the coil-out run** (`labs/erpnext/seed_cutlery.py`, then the scene
  on a scratch plant with the ERP sync running): the heater band that went
  in at 140 came out once, against CC-MOULD01, with CM-00001 in the remarks.
  On a site where that run has not been made it skips and says so.

Unlike `test_erpnext_live.py`, these skip when no ERPNext answers: the one
they are written for is the fixer's, which is down overnight.

    python -m pytest -m erpnext_live tests/test_erpnext_live_books_a_part_used_on_a_repair_order.py
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from fsmes.config import get_settings
from fsmes.integrations.erp.contract import PartUsed
from fsmes.integrations.erp.erpnext_adapter import ErpNextAdapter, ErpNextClient, ErpNextError

pytestmark = [pytest.mark.slow, pytest.mark.erpnext_live]

COMPANY, ABBR = "ACME Beverages", "ACME"
PART, RATE = "SP-TEST-PART", 10.0
COST_CENTER = "CC-PARTTEST"


@pytest.fixture(scope="module")
def client() -> ErpNextClient:
    settings = get_settings()
    try:
        connection = ErpNextClient(
            settings.erpnext_base_url, site=settings.erpnext_site, user=settings.erpnext_user,
            password=settings.erpnext_password, api_key=settings.erpnext_api_key,
            api_secret=settings.erpnext_api_secret, timeout=60.0)
        connection.list("Company", fields=["name"], limit=1)
    except (httpx.HTTPError, ErpNextError) as exc:
        pytest.skip(f"no ERPNext answering at {settings.erpnext_base_url}: {exc}")
    yield connection
    connection.close()


def ledger(client, doctype: str, voucher: str, fields: list[str]) -> list[dict]:
    return client.list(doctype, filters=[["voucher_no", "=", voucher], ["is_cancelled", "=", 0]],
                       fields=fields, limit=50)


def issues_named(client, marker: str) -> list[dict]:
    return client.list("Stock Entry", filters=[["purpose", "=", "Material Issue"], ["docstatus", "=", 1],
                                                ["remarks", "like", f"%{marker}%"]],
                       fields=["name", "remarks", "company"], limit=5)


# ----------------------------------------------------------- any seeded site

@pytest.fixture(scope="module")
def test_part(client) -> str:
    assert client.exists("Company", COMPANY), "no ACME Beverages; run labs/erpnext/seed_erpnext.py first"
    if not client.exists("Item", PART):
        client.insert("Item", {"item_code": PART, "item_name": "Test spare part", "item_group": "Consumable",
                               "stock_uom": "Nos", "is_stock_item": 1, "valuation_rate": RATE})
    name = f"{COST_CENTER} - {ABBR}"
    if not client.exists("Cost Center", name):
        client.insert("Cost Center", {"cost_center_name": COST_CENTER, "company": COMPANY,
                                      "parent_cost_center": f"{COMPANY} - {ABBR}", "is_group": 0})
    return name


@pytest.fixture
def one_in_stores(client, test_part) -> None:
    client.insert("Stock Entry", {
        "stock_entry_type": "Material Receipt", "company": COMPANY, "docstatus": 1,
        "items": [{"item_code": PART, "qty": 1, "uom": "Nos", "t_warehouse": f"Stores - {ABBR}",
                   "basic_rate": RATE, "cost_center": f"Main - {ABBR}"}]})


def a_repair(key: str) -> PartUsed:
    return PartUsed(message_key=key, repair_order="CM-TEST", part_code=PART, quantity=1,
                    equipment="FILLER01", work_center="LINE1", cost_center=COST_CENTER,
                    performed_by="MT-01", minutes_down=12.0)


def test_the_part_leaves_stores_and_its_cost_lands_on_the_machines_cost_center(client, test_part,
                                                                              one_in_stores):
    key = f"CM-TEST:part:{uuid.uuid4().hex[:8]}"
    ErpNextAdapter(client, company=COMPANY).send_part_used(a_repair(key))
    [entry] = issues_named(client, f"[{key}]")
    assert "CM-TEST" in entry["remarks"] and "12 min down" in entry["remarks"]
    [out] = ledger(client, "Stock Ledger Entry", entry["name"], ["item_code", "warehouse", "actual_qty"])
    assert (out["item_code"], out["warehouse"], out["actual_qty"]) == (PART, f"Stores - {ABBR}", -1.0)
    charged = ledger(client, "GL Entry", entry["name"], ["cost_center", "debit", "credit", "account"])
    # Both legs carry the cost center: the stock leaves Stock In Hand (a
    # credit) and the cost is the debit, on the company's default account for
    # an issue (Stock Adjustment unless the item or company names another).
    [cost] = [r for r in charged if r["debit"]]
    assert (cost["cost_center"], cost["debit"]) == (test_part, pytest.approx(RATE)), charged
    assert not cost["account"].startswith("Stock In Hand")


def test_sent_twice_erpnext_holds_one_issue(client, test_part, one_in_stores):
    key = f"CM-TEST:part:{uuid.uuid4().hex[:8]}"
    adapter = ErpNextAdapter(client, company=COMPANY)
    adapter.send_part_used(a_repair(key))
    adapter.send_part_used(a_repair(key))
    assert len(issues_named(client, f"[{key}]")) == 1


# ------------------------------------------------------ after the coil-out run

def test_after_the_coil_out_run_the_band_went_in_once_and_out_once_against_cc_mould01(client):
    marker = "[CM-00001:part:SP-HEATER-BAND]"
    issued = [e for e in issues_named(client, marker) if e["company"] == "Cutlery Works"]
    if not issued:
        pytest.skip("the coil-out run has not booked CM-00001 on this site")
    [entry] = issued
    assert "CM-00001" in entry["remarks"]
    moves = client.list("Stock Ledger Entry", filters=[["item_code", "=", "SP-HEATER-BAND"],
                                                       ["warehouse", "=", "Stores - CW"], ["is_cancelled", "=", 0]],
                        fields=["actual_qty", "voucher_no"], limit=50)
    assert sorted(m["actual_qty"] for m in moves) == [-1.0, 1.0], moves
    charged = client.list("GL Entry", filters=[["cost_center", "=", "CC-MOULD01 - CW"], ["is_cancelled", "=", 0]],
                          fields=["debit", "credit", "voucher_no", "account"], limit=50)
    assert {r["voucher_no"] for r in charged} == {entry["name"]}, charged
    assert sum(r["debit"] for r in charged) == pytest.approx(140.0), charged
