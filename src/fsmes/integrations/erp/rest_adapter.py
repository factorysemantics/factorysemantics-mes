"""REST adapter — polls an ERP HTTP API. Works against the mock ERP as-is;
a real ERP integration changes the three URLs, not the MES."""

import httpx

from fsmes.integrations.erp.base import CheckResult, ErpConnector, Requirement
from fsmes.integrations.erp.contract import Confirmation, ProductionRequest, as_payload


class RestErpAdapter(ErpConnector):
    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self.client = client or httpx.Client(base_url=base_url, timeout=10.0)

    def fetch_orders(self) -> list[ProductionRequest]:
        response = self.client.get("/orders", params={"status": "new"})
        response.raise_for_status()
        return [ProductionRequest.from_payload(row) for row in response.json()]

    def acknowledge(self, order_code: str) -> None:
        self.client.post(f"/orders/{order_code}/ack").raise_for_status()

    def send_confirmation(self, confirmation: Confirmation) -> None:
        self.client.post("/confirmations", json=as_payload(confirmation)).raise_for_status()

    # ------------------------------------------------------------- the far side
    def requirements(self) -> list[Requirement]:
        """Three endpoints, none of which this connector can create.

        `created_by_setup=False` is the point of the flag: `fsmes erp setup`
        must not report success for something only the ERP's own team can
        do. What it can do is print this list for them.
        """
        return [
            Requirement(
                name="GET /orders?status=new",
                where="the ERP's HTTP API",
                what="the orders not yet taken by the MES, as JSON objects carrying at least "
                     "a code (`code`, `order` or `id`) and a material (`material` or `product`), "
                     "plus quantity, due date and priority where the ERP has them",
                why="without it no order ever reaches the floor",
                created_by_setup=False,
            ),
            Requirement(
                name="POST /orders/{code}/ack",
                where="the ERP's HTTP API",
                what="mark one order as taken by the MES, so the next poll does not offer it again",
                why="without it every poll re-imports every order",
                created_by_setup=False,
            ),
            Requirement(
                name="POST /confirmations",
                where="the ERP's HTTP API",
                what="accept one confirmation — the JSON of `contract.OperationConfirmation` or "
                     "`contract.OrderCompletion` — and answer 2xx only if it was stored",
                why="this is the whole outbound half. A 2xx for a confirmation the ERP did not "
                    "keep makes the MES believe production was booked when it was not",
                created_by_setup=False,
            ),
        ]

    def check(self) -> CheckResult:
        """Reach the order list, and say plainly that the other two endpoints
        were not tried.

        `check` must have no side effects, and there is no way to prove the
        ack and confirmation endpoints work without acknowledging an order
        or posting a confirmation. Calling that `ok` would be inventing a
        fact about somebody's ERP.
        """
        base = str(self.client.base_url) or "the configured ERP"
        try:
            response = self.client.get("/orders", params={"status": "new"})
            response.raise_for_status()
            rows = response.json()
        except Exception as exc:
            return CheckResult.of(
                ("not ok", f"GET /orders at {base} failed: {type(exc).__name__}: {exc}"),
                ("note", "check MES_ERP_BASE_URL, and that the ERP's API is up and reachable from here."),
            )
        if not isinstance(rows, list):
            return CheckResult.of(
                ("not ok", f"GET /orders at {base} answered with {type(rows).__name__}, not a list of orders"),
            )
        unreadable = []
        for row in rows:
            try:
                ProductionRequest.from_payload(row)
            except Exception as exc:
                unreadable.append(str(exc))
        lines = [("ok", f"reached {base} and read {len(rows)} orders waiting")]
        if unreadable:
            lines.append(("not ok", f"{len(unreadable)} of those {len(rows)} orders would not import: "
                                    + "; ".join(unreadable[:3])))
        lines.append(("unknown", "POST /orders/{code}/ack was not tried — it acknowledges a real order"))
        lines.append(("unknown", "POST /confirmations was not tried — it books real production"))
        lines.append(("note", "the first confirmation this MES sends is the first proof those two work."))
        return CheckResult.of(*lines)
