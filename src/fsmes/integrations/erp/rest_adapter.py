"""REST adapter — polls an ERP HTTP API. Works against the mock ERP as-is;
a real ERP integration changes the three URLs, not the MES."""

import httpx

from fsmes.integrations.erp.contract import Confirmation, ProductionRequest, as_payload


class RestErpAdapter:
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
