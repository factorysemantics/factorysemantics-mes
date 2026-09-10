"""API surface: CRUD, error mapping, audit attribution, metrics, dashboard feed."""

import pytest


def test_health(anon):
    # Health also answers "may this MES act on its plant" - see
    # tests/test_shadow_mode.py for what the flag promises.
    assert anon.get("/health").json() == {"status": "ok", "shadow": False}


def test_seeded_equipment_visible(client):
    codes = [eq["code"] for eq in client.get("/masterdata/equipment").json()]
    assert {"ACME", "KC1", "LINE1", "MIX01", "PACK01"} <= set(codes)


def test_order_flow_via_api(client, supervisor):
    created = client.post("/workorders", json={"code": "WO-API-1", "material": "FG-COLA", "quantity": 10})
    assert created.status_code == 201
    assert client.post("/workorders/WO-API-1/release").status_code == 200

    # a second release must 409 (state machine enforced)
    assert client.post("/workorders/WO-API-1/release").status_code == 409

    audit = supervisor.get("/audit", params={"entity_id": "WO-API-1"}).json()
    assert {"workorder.created", "workorder.released"} <= {entry["action"] for entry in audit}


def test_unknown_material_404(client):
    assert client.post("/workorders", json={"code": "WO-X", "material": "NOPE", "quantity": 1}).status_code == 404


def test_metrics_exposes_counts(anon):
    body = anon.get("/metrics").text
    assert 'mes_work_orders{status="planned"}' in body
    assert "mes_audit_entries_max_id" in body


def test_dashboard_summary_shape(client):
    client.post("/workorders", json={"code": "WO-DASH", "material": "FG-COLA", "quantity": 4})
    data = client.get("/dashboard/summary").json()

    assert {"plant", "machines", "orders", "audit", "non_conformances"} <= data.keys()
    assert {m["code"] for m in data["machines"]} == {"MIX01", "PACK01"}
    assert data["plant"]["machines_total"] == 2
    order = next(o for o in data["orders"] if o["code"] == "WO-DASH")
    assert order["status"] == "planned" and order["progress"] == 0
    assert [op["equipment"] for op in order["operations"]] == ["MIX01", "PACK01"]


def test_dashboard_reflects_production(client):
    client.post("/workorders", json={"code": "WO-LIVE", "material": "FG-COLA", "quantity": 4})
    client.post("/workorders/WO-LIVE/release")
    client.post("/equipment/MIX01/state", json={"state": "running"})
    client.post("/workorders/WO-LIVE/operations/10/start")
    client.post("/execution/report", json={"order": "WO-LIVE", "seq": 10, "good": 2, "scrap": 1})

    data = client.get("/dashboard/summary").json()
    mixer = next(m for m in data["machines"] if m["code"] == "MIX01")
    assert mixer["state"] == "running"
    assert mixer["current_order"] == "WO-LIVE"
    assert mixer["oee"]["quality"] == pytest.approx(2 / 3, abs=1e-4)

    # Order-level "good" is finished goods — the output of the LAST operation.
    # Work in progress at the mixer shows on the operation, not on the order.
    order = next(o for o in data["orders"] if o["code"] == "WO-LIVE")
    assert order["good"] == 0 and order["progress"] == 0
    assert order["scrap"] == 1  # scrap counts wherever it happens
    mix_op = next(op for op in order["operations"] if op["seq"] == 10)
    assert mix_op["good"] == 2 and mix_op["status"] == "running"


def test_dashboard_page_and_assets_served(anon):
    assert anon.get("/dashboard").status_code == 200
    # The engine arrived from MES-TWIN and its branding came with it. This
    # asserted the old name until the screens were renamed on 2026-08-31.
    assert "FactorySemantics" in anon.get("/dashboard").text
    assert anon.get("/static/app.js").status_code == 200
    assert anon.get("/static/styles.css").status_code == 200
