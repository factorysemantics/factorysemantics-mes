"""The screens at plant scale: what Scott asked for on the 108-station
mega-factory through the design assistant (2026-09-04) - the Floor's
machines and work-order cards paginate and filter, the actions sit apart at
the top, Quality's measurements/specifications/history/non-conformances
filter and page, and every level of a machine's breadcrumb is clickable.
"""

import re
from pathlib import Path

from fsmes.domain import NonConformance

WEB = Path(__file__).resolve().parents[1] / "src" / "fsmes" / "web"


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_the_floor_lists_are_filtered_paged_and_counted():
    html, js = _read("index.html"), _read("app.js")
    for control in ("m-q", "m-line", "m-state", "m-pager", "m-count", "o-q", "o-status", "o-pager", "o-count"):
        assert f'id="{control}"' in html, control
    # Both cards state their total through the shared pager (STYLE.md rule 4).
    assert js.count("FS.pager(") >= 2
    assert "in the plant" in js
    # The work-order card reads the server's page, not a slice of the summary.
    assert "/workorders?" in js and 'params.append("status", status)' in js


def test_the_actions_sit_first_and_apart_on_the_floor():
    html = _read("index.html")
    panels = [m.start() for m in re.finditer(r'<section class="panel', html)]
    actions = html.index('<section class="panel actions')
    assert actions == panels[0], "the shop-floor actions are the first panel"
    assert 'class="forms"' in html


def test_a_filtered_floor_is_a_link():
    js = _read("app.js")
    assert "history.replaceState" in js
    for key in ('"line"', '"state"', '"q"'):
        assert key in js


def test_quality_lists_are_filtered_and_paged():
    html, js = _read("quality.html"), _read("quality.js")
    for control in ("q-material", "s-q", "s-material", "s-pager", "h-material", "h-char", "h-result",
                    "h-pager", "n-status", "n-q", "n-pager"):
        assert f'id="{control}"' in html, control
    # History and the chart are the server's answer for the chosen
    # characteristic, not a client-side slice of the last 200 checks.
    assert "material=" in js and "characteristic=" in js and "offset" in js
    assert js.count("FS.pager(") >= 3
    # The tab strip is one material's characteristics, never the plant's.
    assert "s.material === filters.material" in js


def test_the_pass_rate_kpi_says_what_it_covers():
    html = _read("quality.html")
    assert "Pass rate, all time" in html


def test_every_level_of_a_machines_breadcrumb_is_a_link():
    machine, common, machines = _read("machine.js"), _read("common.js"), _read("machines.js")
    assert 'FS.link("equipment"' in machine
    assert "/dashboard/machines?under=" in common
    assert 'searchParams.get("under")' in machines
    assert 'id="tree-crumbs"' in _read("machines.html")


def test_the_schedule_screen_asks_for_statuses_the_api_accepts(client):
    """The comma form was a 422 on every theme in ui-check."""
    js = _read("schedule.js")
    assert "status=released,running,planned" not in js
    match = re.search(r'api\("(/workorders\?[^"]+)"\)', js)
    assert match
    assert client.get(match.group(1)).status_code == 200


def test_a_nonconformance_names_its_order_and_can_be_capped(client, session):
    from sqlalchemy import select

    from fsmes.domain import Material, QualitySpec, WorkOrder

    material = session.scalar(select(Material).where(Material.type == "finished")) or session.scalar(select(Material))
    order = session.scalar(select(WorkOrder))
    spec = session.scalar(select(QualitySpec).where(QualitySpec.material_id == material.id))
    if spec is None:
        session.add(QualitySpec(material=material, characteristic="scale_test", unit="g",
                                min_value=1.0, max_value=2.0))
        session.flush()
        spec = session.scalar(select(QualitySpec).where(QualitySpec.material_id == material.id))
    body = {"material": material.code, "characteristic": spec.characteristic, "value": 999.0,
            "order": order.code if order else None}
    made = client.post("/quality/checks", json=body)
    assert made.status_code == 201 and made.json()["non_conformance"], made.text

    page = client.get("/quality/nonconformances?status=open").json()
    ncs = page["items"]
    assert page["total"] >= 1 and ncs
    assert {"order", "closed_at", "status", "created_at"} <= set(ncs[0])
    assert ncs[0]["order"] == (order.code if order else None)
    assert len(client.get("/quality/nonconformances?limit=1").json()["items"]) == 1
    assert client.get("/quality/nonconformances?limit=0").status_code == 422
    assert client.get("/quality/nonconformances?q=no-such-text").json()["total"] == 0
    assert NonConformance.__table__.c.work_order_id is not None
