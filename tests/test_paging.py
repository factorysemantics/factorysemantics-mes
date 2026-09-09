"""Every list this API returns.

There was no coverage of list *shapes* at all, which is exactly how an
endpoint came to return 5.5 MB and two and a half seconds against a year of a
sixty-machine plant without anybody noticing. These tests are the thing that
would have noticed.
"""

from datetime import datetime

import pytest

from fsmes.api import paging
from fsmes.services import auth, workorders

PAGED = [
    ("/workorders", "orders.create"),
    ("/execution/lots", None),
    ("/quality/checks", None),
]


@pytest.fixture()
def many_orders(session, client):
    from sqlalchemy import select

    from fsmes.domain import Material

    material = session.scalar(select(Material).where(Material.code == "FG-COLA"))
    for i in range(120):
        workorders.create(session, code=f"WO-BULK-{i:04d}",
                          material_code=material.code, quantity=10, actor="test")
    session.flush()
    return client


# ------------------------------------------------------------ the envelope

@pytest.mark.parametrize("path", [p for p, _ in PAGED])
def test_every_list_returns_the_same_envelope(client, path):
    """One shape, so a screen or an agent can page any list without learning a
    new convention each time."""
    body = client.get(path).json()
    assert set(body) >= {"items", "total", "limit", "offset", "has_more"}
    assert isinstance(body["items"], list)


def test_a_page_says_how_much_it_is_not_showing(many_orders):
    """'Showing 50 of 18,347' is the sentence that stops a truncated list
    looking like a complete one."""
    body = many_orders.get("/workorders?limit=10").json()
    assert len(body["items"]) == 10
    assert body["total"] >= 120
    assert body["has_more"] is True


def test_the_last_page_says_so(many_orders):
    total = many_orders.get("/workorders?limit=1").json()["total"]
    body = many_orders.get(f"/workorders?limit=10&offset={total - 5}").json()
    assert len(body["items"]) == 5
    assert body["has_more"] is False


def test_paging_does_not_repeat_or_skip(many_orders):
    seen = []
    for offset in range(0, 100, 25):
        seen += [o["code"] for o in
                 many_orders.get(f"/workorders?limit=25&offset={offset}").json()["items"]]
    assert len(seen) == len(set(seen)) == 100


def test_nobody_can_ask_for_everything(client):
    """The cap is the point: a caller must not be able to reproduce the 5.5 MB
    response by asking nicely."""
    assert client.get(f"/workorders?limit={paging.MAX_LIMIT + 1}").status_code == 422
    assert client.get("/workorders?limit=0").status_code == 422
    assert client.get("/workorders?offset=-1").status_code == 422


def test_the_default_page_is_bounded(many_orders):
    body = many_orders.get("/workorders").json()
    assert len(body["items"]) == paging.DEFAULT_LIMIT


# --------------------------------------------------------------- filtering

def test_orders_filter_by_status(many_orders):
    body = many_orders.get("/workorders?status=planned").json()
    assert body["total"] >= 120
    assert all(o["status"] == "planned" for o in body["items"])


def test_orders_search_by_code(many_orders):
    body = many_orders.get("/workorders?q=BULK-001").json()
    assert body["total"] == 10          # 0010..0019
    assert all("BULK-001" in o["code"] for o in body["items"])


def test_a_filter_narrows_the_total_not_just_the_page(many_orders):
    everything = many_orders.get("/workorders?limit=1").json()["total"]
    narrowed = many_orders.get("/workorders?limit=1&q=BULK-0001").json()["total"]
    assert narrowed < everything, "total must reflect the filter, not the table"


def test_orders_filter_by_when_they_are_due(client, session):
    """"What is due this week" is the supervisor's actual question, and the
    list could not answer it - though the model has carried a due_date all
    along, and the API was already handing it to the screen."""
    for day in (3, 10):
        workorders.create(session, code=f"WO-DUE-{day:02d}", material_code="FG-COLA",
                          quantity=1, due_date=datetime(2026, 9, day, 12, 0), actor="test")
    workorders.create(session, code="WO-DUE-NONE", material_code="FG-COLA",
                      quantity=1, actor="test")
    session.flush()

    before = [o["code"] for o in
              client.get("/workorders?due_before=2026-09-05T23:59:59").json()["items"]]
    assert "WO-DUE-03" in before
    assert "WO-DUE-10" not in before

    after = [o["code"] for o in
             client.get("/workorders?due_after=2026-09-05").json()["items"]]
    assert "WO-DUE-10" in after
    assert "WO-DUE-03" not in after


def test_an_order_with_no_due_date_is_not_swept_into_a_date_range(client, session):
    """It is not due before anything. Answering as though it were would be
    inventing a fact about the plant, which principle 4 forbids."""
    workorders.create(session, code="WO-UNDATED", material_code="FG-COLA",
                      quantity=1, actor="test")
    session.flush()

    body = client.get("/workorders?due_before=2099-01-01").json()
    assert "WO-UNDATED" not in [o["code"] for o in body["items"]]


def test_orders_filter_on_several_statuses_at_once(many_orders, session):
    """"Open" means released or running, and a filter that takes one value
    cannot say it - which is what the Open orders tile has to ask for."""
    from fsmes.services import workorders as wo_service

    wo_service.release(session, "WO-BULK-0119", actor="test")
    session.flush()

    body = many_orders.get("/workorders?status=released&status=planned").json()
    seen = {o["status"] for o in body["items"]}
    assert seen <= {"released", "planned"}
    assert "released" in seen and "planned" in seen

    one = many_orders.get("/workorders?status=planned").json()
    assert one["total"] < body["total"], "a second status must widen the result"


def test_the_order_summary_counts_the_plant_not_the_page(many_orders):
    """The orders screen counted its own fifty rows. Harmless at fifty orders,
    a lie at eighteen thousand, and an obvious one once a tile became a filter
    you could click."""
    body = many_orders.get("/workorders/summary").json()
    assert body["by_status"]["planned"] >= 120
    assert body["total"] == sum(body["by_status"].values())

    page = many_orders.get("/workorders?limit=10").json()
    assert len(page["items"]) == 10
    assert body["total"] >= page["total"], "the tile must not be scoped to a page"


def test_the_summary_agrees_with_the_orders_themselves(client, session):
    """Two ways of computing the same number is two ways of being wrong. The
    endpoint aggregates in SQL; WorkOrder does it in Python; they are held to
    the same answer here."""
    from sqlalchemy import select

    from fsmes.domain import WorkOrder

    body = client.get("/workorders/summary").json()
    orders = list(session.scalars(select(WorkOrder)))

    assert body["good_qty"] == pytest.approx(sum(o.good_qty for o in orders))
    assert body["scrap_qty"] == pytest.approx(sum(o.scrap_qty for o in orders))


def test_a_plant_that_has_booked_nothing_reports_unknown_yield(client, session):
    """Not zero. A line that has not run is not a line that failed - principle
    4, the same rule OEE already follows."""
    from sqlalchemy import delete

    from fsmes.domain import WorkOrderOperation

    session.execute(delete(WorkOrderOperation))
    session.flush()

    assert client.get("/workorders/summary").json()["yield"] is None


def test_summary_is_not_read_as_an_order_code(client):
    """/summary sits before /{code} in the router, or FastAPI goes looking for
    an order called "summary"."""
    assert client.get("/workorders/summary").status_code == 200


def test_people_can_be_found_by_name_or_code(sign_in, session):
    admin = sign_in("ADMIN", "admin")
    for i in range(60):
        auth.create_user(session, code=f"EMP{i:03d}", name=f"Employee {i:03d}",
                         password="x", role="operator")
    session.flush()

    body = admin.get("/admin/users?q=EMP04").json()
    assert body["total"] == 10
    body = admin.get("/admin/users?role=quality_inspector").json()
    assert body["total"] == 0
    # And the default page does not ship all 60-odd.
    assert len(admin.get("/admin/users").json()["items"]) <= paging.DEFAULT_LIMIT


def test_quality_checks_filter_by_result(client, session):
    from fsmes.services import quality
    for value in (11.0, 11.0, 99.0):
        quality.record_check(session, material_code="FG-COLA",
                             characteristic="brix", value=value, actor="test")
    session.flush()
    body = client.get("/quality/checks?result=fail").json()
    assert body["total"] == 1
    assert body["items"][0]["result"] == "fail"


def test_quality_checks_filter_by_material(client, session):
    """`?material=` joined a table the router never imported and raised NameError.

    The filter shipped with the supervisor's Orders work and nothing exercised
    it: the MCP `quality` tool passes material only to the specs call.
    """
    from fsmes.services import quality
    quality.record_check(session, material_code="FG-COLA", characteristic="brix", value=11.0, actor="test")
    session.flush()
    r = client.get("/quality/checks?material=FG-COLA")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert {c["material"] for c in body["items"]} == {"FG-COLA"}
    assert client.get("/quality/checks?material=NO-SUCH").json()["total"] == 0
