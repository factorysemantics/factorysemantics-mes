"""ERPNext (Frappe) adapter — a real ERP on the other end of the interface.

ERPNext is a genuine ERP: sales, purchasing, stock, costing, BOMs, work orders,
and a large UI nobody should rewrite. What it is not is an MES — it has no
machine layer, no tag history, and no OEE, which is exactly the half MES-TWIN
provides. So the two meet at the only sensible boundary:

    ERPNext  plans and books the business   (work orders in, stock out)
    MES-TWIN runs and measures the floor    (OPC tags, states, counters, OEE)

Inbound, a submitted ERPNext Work Order becomes an MES work order. Its
`production_item` must be an MES material code — that shared vocabulary is the
whole contract, and `labs/erpnext/seed_erpnext.py` seeds both sides to match.

Outbound, a finished MES order posts a real Stock Entry of purpose "Manufacture"
against the ERPNext work order, so produced quantity, stock movement and
costing all land where ERPNext expects them. That is deliberately not a comment
or a custom field: production the MES has committed to should be visible to the
business as stock, not as a note somebody has to read.

Two custom fields on Work Order carry what ERPNext has nowhere to put — whether
the MES has taken the order, and what the MES actually counted. Both are
`allow_on_submit`, because a submitted work order is exactly when they change.
"""

import httpx
import structlog

from fsmes.integrations.erp.contract import as_payload

log = structlog.get_logger("erp.erpnext")

# Only orders that are released for production and not yet taken by the MES.
_OPEN_STATUSES = ("Not Started", "In Process")

MAKE_STOCK_ENTRY = "erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry"


class ErpNextError(RuntimeError):
    """An ERPNext call failed. Raised so sync marks the message and retries."""


class ErpNextClient:
    """Thin Frappe REST client: session login, and the few calls the MES needs.

    The `site` header matters on a multi-site bench. This one serves Shortline
    Filtration Works on the default site and the MES-TWIN simulation on
    `mes.localhost`, both on the same port, told apart only by Host — so the
    header is how we avoid writing into the wrong company's books.
    """

    def __init__(
        self,
        base_url: str,
        *,
        site: str = "",
        user: str = "",
        password: str = "",
        api_key: str = "",
        api_secret: str = "",
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ):
        headers = {"Accept": "application/json"}
        if site:
            headers["Host"] = site
        if api_key and api_secret:
            # Token auth survives restarts and needs no session; preferred for a
            # long-running worker.
            headers["Authorization"] = f"token {api_key}:{api_secret}"
        self.base_url = base_url.rstrip("/")
        self.site = site
        self._client = client or httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)
        self._token_auth = bool(api_key and api_secret)
        self._user, self._password = user, password
        if not self._token_auth and user:
            self.login()

    def login(self) -> None:
        response = self._client.post("/api/method/login", json={"usr": self._user, "pwd": self._password})
        if response.status_code != 200:
            raise ErpNextError(f"ERPNext login failed for {self._user!r}: HTTP {response.status_code}")

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = self._client.request(method, path, **kwargs)
        if response.status_code in (401, 403) and not self._token_auth and self._user:
            # Sessions expire; one silent re-login beats failing the cycle.
            self.login()
            response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ErpNextError(f"{method} {path} -> HTTP {response.status_code}: {_detail(response)}")
        return response

    def list(self, doctype: str, *, filters=None, fields=None, limit: int = 100, order_by: str = "") -> list[dict]:
        params: dict = {"limit_page_length": limit}
        if filters:
            params["filters"] = _json(filters)
        if fields:
            params["fields"] = _json(fields)
        if order_by:
            params["order_by"] = order_by
        return self._request("GET", f"/api/resource/{doctype}", params=params).json().get("data", [])

    def get(self, doctype: str, name: str) -> dict:
        return self._request("GET", f"/api/resource/{doctype}/{_esc(name)}").json()["data"]

    def exists(self, doctype: str, name: str) -> bool:
        try:
            self.get(doctype, name)
            return True
        except ErpNextError:
            return False

    def insert(self, doctype: str, doc: dict) -> dict:
        return self._request("POST", f"/api/resource/{doctype}", json=doc).json()["data"]

    def update(self, doctype: str, name: str, values: dict) -> dict:
        return self._request("PUT", f"/api/resource/{doctype}/{_esc(name)}", json=values).json()["data"]

    def call(self, method: str, **params) -> dict:
        return self._request("POST", f"/api/method/{method}", json=params).json().get("message")

    def comment(self, doctype: str, name: str, text: str) -> None:
        self.call(
            "frappe.desk.form.utils.add_comment",
            reference_doctype=doctype,
            reference_name=name,
            content=text,
            comment_email="mes-twin@local",
            comment_by="MES-TWIN",
        )

    def close(self) -> None:
        self._client.close()


def _json(value) -> str:
    import json

    return json.dumps(value)


def _esc(name: str) -> str:
    from urllib.parse import quote

    return quote(str(name), safe="")


def _detail(response: httpx.Response) -> str:
    """The useful half of a Frappe error, which buries the message in JSON."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    for key in ("exception", "_server_messages", "message", "exc"):
        if body.get(key):
            return str(body[key])[:300]
    return str(body)[:300]


def from_settings(settings) -> "ErpNextAdapter":
    """The `fsmes.modules` entry point: build the connector from MES settings.

    Registered in pyproject.toml as `erpnext`, which is also the value of
    MES_ERP_MODE that selects it. A connector shipped as a separate package
    registers the same way and needs nothing changed here.
    """
    return ErpNextAdapter(
        ErpNextClient(
            settings.erpnext_base_url,
            site=settings.erpnext_site,
            user=settings.erpnext_user,
            password=settings.erpnext_password,
            api_key=settings.erpnext_api_key,
            api_secret=settings.erpnext_api_secret,
        ),
        post_stock_entry=settings.erpnext_post_stock_entry,
    )


class ErpNextAdapter:
    """Transport only — every MES-side rule stays in services.erp."""

    def __init__(self, client: ErpNextClient, *, post_stock_entry: bool = True):
        self.client = client
        self.post_stock_entry = post_stock_entry

    # ------------------------------------------------------------- inbound
    def fetch_orders(self) -> list[dict]:
        rows = self.client.list(
            "Work Order",
            filters=[
                ["docstatus", "=", 1],
                ["status", "in", list(_OPEN_STATUSES)],
                ["custom_mes_synced", "=", 0],
            ],
            fields=["name", "production_item", "qty", "planned_end_date", "expected_delivery_date"],
            order_by="planned_start_date asc",
        )
        # No priority is sent: ERPNext Work Order has no priority field, and
        # inventing one here would outrank the MES's own dispatch ordering with
        # a number nobody set. services.erp applies its default instead.
        return [
            {
                "code": row["name"],
                "material": row["production_item"],
                "quantity": row.get("qty") or 0,
                "due_date": row.get("planned_end_date") or row.get("expected_delivery_date"),
                "erp_reference": row["name"],
            }
            for row in rows
        ]

    def acknowledge(self, order_code: str) -> None:
        """Mark the order as taken, so the next poll does not re-import it."""
        self.client.update("Work Order", order_code, {"custom_mes_synced": 1})

    # ------------------------------------------------------------ outbound
    def send_confirmation(self, confirmation) -> None:
        # Accepts the typed contract, or the plain dict older callers send.
        payload = confirmation if isinstance(confirmation, dict) else as_payload(confirmation)
        order = payload.get("order")
        if not order:
            raise ErpNextError("confirmation payload has no order code")
        if payload.get("kind") == "operation_confirmation":
            # One step's quantities, time and consumption, recorded on the
            # Work Order where a person reads it. The Job Card time-log
            # mapping is built against a live ERPNext, not guessed here.
            parts = [f"MES op {payload.get('seq')} {payload.get('operation') or ''} on {payload.get('equipment')}",
                     f"cc {payload.get('cost_center') or '-'}",
                     f"{float(payload.get('good_qty') or 0):g} good, {float(payload.get('scrap_qty') or 0):g} scrap",
                     f"{(payload.get('machine_seconds') or 0) / 60:.1f} min machine"]
            if payload.get("components"):
                parts.append("consumed " + ", ".join(
                    f"{c['quantity']:g} x {c['lot']}" for c in payload["components"]))
            self.client.comment("Work Order", order, "; ".join(parts))
            return
        good = float(payload.get("good_qty") or 0)
        scrap = float(payload.get("scrap_qty") or 0)
        over = float(payload.get("over_qty") or 0)

        # What the MES counted, recorded on the order itself. ERPNext has
        # nowhere native to put machine-counted scrap, and it is the number the
        # plant argues about, so it does not get to live only in a comment.
        self.client.update(
            "Work Order",
            order,
            {"custom_mes_good_qty": good, "custom_mes_scrap_qty": scrap,
             # An over-run reaches the ERP as its own number rather than as
             # a good quantity that happens to be larger than the order.
             "custom_mes_over_qty": over,
             "custom_mes_lot": payload.get("lot") or ""},
        )

        if good > 0 and self.post_stock_entry:
            self._manufacture(order, good, payload)

        self.client.comment(
            "Work Order",
            order,
            f"MES-TWIN: {good:g} good, {scrap:g} scrap"
            + (f", {over:g} over the ordered quantity" if over else "")
            + (f", lot {payload['lot']}" if payload.get("lot") else "")
            + (f" (completed {payload['completed_at']})" if payload.get("completed_at") else ""),
        )

    def _manufacture(self, order: str, quantity: float, payload: dict) -> None:
        """Post the finished quantity as a real Manufacture stock entry.

        ERPNext builds the entry itself, from the work order's BOM and
        warehouses — asking it to is far safer than assembling item rows here
        and guessing at valuation.
        """
        already = float(self.client.get("Work Order", order).get("produced_qty") or 0)
        remaining = quantity - already
        if remaining <= 0:
            # Already posted (a retry after a partial failure, most likely).
            log.info("stock entry already posted", order=order, produced=already)
            return

        doc = self.client.call(MAKE_STOCK_ENTRY, work_order_id=order, purpose="Manufacture", qty=remaining)
        if not isinstance(doc, dict):
            raise ErpNextError(f"ERPNext would not build a stock entry for {order}: {doc!r}")
        doc["docstatus"] = 1
        if payload.get("lot"):
            doc["remarks"] = f"MES-TWIN lot {payload['lot']}"
        entry = self.client.insert("Stock Entry", doc)
        log.info("manufacture posted to ERPNext", order=order, qty=remaining, stock_entry=entry.get("name"))
