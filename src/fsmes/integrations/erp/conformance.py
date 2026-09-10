"""The conformance suite: what a connector must do before it is supported.

Every obligation in this file is something an ERP connector got wrong once,
in a way that looked like success. It ships inside the package rather than
in `tests/` so that a connector published on its own — SAP, Odoo, Oracle,
NetSuite — can be held to the same bar without vendoring this project's
test tree:

    from fsmes.integrations.erp import conformance

    def test_the_odoo_connector_meets_the_erp_contract():
        conformance.check_conformance(my_case())

`ConformanceCase` is what the connector's author supplies: the connector
itself, plus the three things only they can do — put an order on their ERP,
read back what their ERP received, and break their ERP so a failure can be
proved to fail. Everything else is here.

Passing is not the same as being tested against a real system. It says the
contract is honoured against whatever far side the case supplies; a fake
that lies in the same direction the connector does will still pass. That is
why `docs/develop/erp-connectors.md` asks for a live test as well, and why
`docs/operate/compatibility.md` records the version and the date.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fsmes.integrations.erp import base
from fsmes.integrations.erp.contract import OrderCompletion, ProductionRequest


@dataclass
class ConformanceCase:
    """One connector, and the far side it is being held against."""

    name: str
    """What to call it in a test report: `erpnext (against a scripted Frappe)`."""

    adapter: Any
    """The connector under test, already configured and pointed at the far side."""

    place_order: Callable[[str, str, float], None]
    """`(code, material, quantity)` — put one open order on the ERP side, as
    a planner would. The connector must then be able to fetch it."""

    delivered: Callable[[], list[dict]]
    """What the ERP side actually received, one dict per confirmation, each
    carrying at least `order` and `good_qty`. Read it from the ERP, not from
    anything the connector remembers having sent."""

    break_the_far_side: Callable[[], None]
    """Put the ERP side into the state a real deployment gets wrong: the
    field that was never created, the endpoint that is down, the folder
    nobody made. After this, `check()` must fail and a write must raise."""

    material: str = "FG-COLA"
    """A material code both sides know."""

    unacknowledged_only: bool = True
    """Can this transport express "I have taken this order"? True for every
    connector that ships. A transport that genuinely cannot must say so
    here rather than quietly failing the obligation — and then every poll
    re-imports every order, which the connector's page had better say."""

    notes: list[str] = field(default_factory=list)
    """Anything about this case a reader of a failure needs."""


def _completion(order: str, material: str, good: float = 6.0) -> OrderCompletion:
    return OrderCompletion(
        message_key=f"{order}:completion",
        order=order,
        erp_reference=order,
        material=material,
        ordered_qty=good,
        good_qty=good,
        scrap_qty=1.0,
        over_qty=0.0,
        lot=f"{order}-FG",
    )


# --------------------------------------------------------------- obligations
# Each takes a fresh case. They are ordinary functions raising AssertionError,
# so they read the same in pytest, in unittest, or called by hand.


def an_order_on_the_erp_arrives_as_a_typed_production_request(case: ConformanceCase) -> None:
    """`fetch_orders` returns the contract's model, not a dict that happens
    to have the right keys. A connector returning dicts type-checks fine and
    breaks the first time anything reads an attribute — which is how
    `'ProductionRequest' object is not subscriptable` was found on
    2026-09-09, in CI, after the shape had already shipped."""
    case.place_order("WO-CONF-1", case.material, 7.0)
    orders = case.adapter.fetch_orders()
    assert isinstance(orders, list), f"fetch_orders returned {type(orders).__name__}, not a list"
    for order in orders:
        assert isinstance(order, ProductionRequest), (
            f"fetch_orders returned a {type(order).__name__}; the contract is ProductionRequest"
        )
    mine = [o for o in orders if o.code == "WO-CONF-1"]
    assert mine, f"the order placed on the ERP was not fetched; got {[o.code for o in orders]}"
    assert mine[0].material == case.material
    assert mine[0].quantity == 7.0


def an_acknowledged_order_is_not_offered_again(case: ConformanceCase) -> None:
    """Otherwise every poll re-imports every open order, and the ERP's list
    of work is the MES's list of work over and over."""
    if not case.unacknowledged_only:
        return
    case.place_order("WO-CONF-2", case.material, 3.0)
    assert any(o.code == "WO-CONF-2" for o in case.adapter.fetch_orders())
    case.adapter.acknowledge("WO-CONF-2")
    again = [o.code for o in case.adapter.fetch_orders()]
    assert "WO-CONF-2" not in again, f"an acknowledged order was offered again; the ERP still lists {again}"


def a_confirmation_reaches_the_erp_with_its_quantities(case: ConformanceCase) -> None:
    """The round trip, asserted against what the ERP holds rather than what
    the connector believes it sent."""
    case.place_order("WO-CONF-3", case.material, 6.0)
    case.adapter.send_confirmation(_completion("WO-CONF-3", case.material))
    landed = [row for row in case.delivered() if row.get("order") == "WO-CONF-3"]
    assert landed, f"nothing for WO-CONF-3 reached the ERP; it holds {case.delivered()}"
    assert float(landed[-1]["good_qty"]) == 6.0, (
        f"the ERP kept good_qty={landed[-1]['good_qty']!r}; the MES sent 6.0"
    )


def a_write_the_erp_did_not_keep_raises_rather_than_returning_quietly(case: ConformanceCase) -> None:
    """The obligation that costs the most to get wrong.

    ERPNext answers 200 to a write naming a field its doctype does not have
    and drops the value — measured on v15.120.0 on 2026-09-09, not inferred.
    A connector that reports that as success makes the MES mark a
    confirmation delivered for a number no ERP ever stored, and the outbox
    never retries it. Raise, and the outbox does its job.
    """
    case.break_the_far_side()
    try:
        case.adapter.send_confirmation(_completion("WO-CONF-4", case.material))
    except Exception:
        return
    raise AssertionError(
        "send_confirmation returned normally against a far side that cannot keep the write. "
        "The outbox will mark this delivered and never retry it."
    )


def every_requirement_says_where_it_lives_what_it_is_and_why(case: ConformanceCase) -> None:
    """A requirement nobody can act on is documentation of a problem, not a
    fix. Empty is a valid answer — a transport can genuinely need nothing —
    but a requirement that exists has to be complete."""
    for requirement in base.requirements(case.adapter):
        for part in ("name", "where", "what", "why"):
            value = getattr(requirement, part)
            assert value and value.strip(), f"requirement {requirement.name!r} has no {part}"


def setup_is_idempotent(case: ConformanceCase) -> None:
    """`fsmes erp setup` is run by people who are not sure whether they ran
    it. The second run must change nothing and must not fail."""
    first = base.setup(case.adapter)
    second = base.setup(case.adapter)
    assert [step.name for step in first] == [step.name for step in second], (
        "setup reported different requirements on the second run"
    )
    created = [step.name for step in second if step.outcome == "created"]
    assert not created, f"the second setup created {created} again"


def check_passes_on_a_far_side_that_is_ready(case: ConformanceCase) -> None:
    """And says something. A `check` that prints nothing has told a person
    nothing, whatever its exit code."""
    base.setup(case.adapter)
    result = base.check(case.adapter)
    assert isinstance(result, base.CheckResult), (
        f"check returned {type(result).__name__}; the contract is CheckResult"
    )
    assert result.lines, "check found nothing to say about a connector that is about to run"
    assert result.ok, "check failed on a far side that is ready:\n" + "\n".join(result.render())
    for line in result.lines:
        assert line.text.strip(), "check produced a line with no text"


def check_fails_when_the_far_side_is_not_ready(case: ConformanceCase) -> None:
    """The whole point of `check`. A check that cannot fail is a green light
    wired to nothing."""
    case.break_the_far_side()
    result = base.check(case.adapter)
    assert not result.ok, (
        "check passed against a broken far side; it said:\n" + "\n".join(result.render())
    )
    assert any(line.status == "not ok" for line in result.lines)


OBLIGATIONS: list[Callable[[ConformanceCase], None]] = [
    an_order_on_the_erp_arrives_as_a_typed_production_request,
    an_acknowledged_order_is_not_offered_again,
    a_confirmation_reaches_the_erp_with_its_quantities,
    a_write_the_erp_did_not_keep_raises_rather_than_returning_quietly,
    every_requirement_says_where_it_lives_what_it_is_and_why,
    setup_is_idempotent,
    check_passes_on_a_far_side_that_is_ready,
    check_fails_when_the_far_side_is_not_ready,
]
"""Eight obligations, in the order a connector meets them. The list states
its total so a connector author can tell a suite that grew from a suite
that was skipped."""


def check_conformance(case_factory: Callable[[], ConformanceCase]) -> None:
    """Run every obligation, each against a freshly built case.

    A factory rather than a case, because half of these deliberately break
    the far side and the next obligation must not inherit the wreckage.
    """
    failures: list[str] = []
    for obligation in OBLIGATIONS:
        case = case_factory()
        try:
            obligation(case)
        except AssertionError as exc:
            failures.append(f"{case.name}: {obligation.__name__}\n    {exc}")
    if failures:
        raise AssertionError(
            f"{len(failures)} of {len(OBLIGATIONS)} conformance obligations failed:\n\n"
            + "\n\n".join(failures)
        )
