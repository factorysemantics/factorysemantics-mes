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
business as stock, not as a note somebody has to read. When ERPNext refuses
that entry — an over-run past the site's over-production allowance is the
measured case — nothing is booked, the confirmation is not delivered, and the
refusal is permanent rather than retried (see `_refusal`).

Five custom fields on Work Order carry what ERPNext has nowhere to put — whether
the MES has taken the order, and what the MES actually counted. All are
`allow_on_submit`, because a submitted work order is exactly when they change.
They are defined in `erpnext_setup.py` and created by `fsmes erp setup`; a
site that lacks them is not a site this adapter can be honest on, which is why
every write is read back (see `_write`).
"""

import math
import re

import httpx
import structlog

from fsmes.integrations.erp.base import CheckResult, ErpConnector, Requirement, SetupOutcome
from fsmes.integrations.erp.contract import ProductionRequest, as_payload

log = structlog.get_logger("erp.erpnext")

# Only orders that are released for production and not yet taken by the MES.
_OPEN_STATUSES = ("Not Started", "In Process")

MAKE_STOCK_ENTRY = "erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry"


# Frappe answers a `frappe.throw()` with 417 and the message in the body. It
# is the status ERPNext uses to say "I read this and I will not do it".
VALIDATION_REFUSED = 417


class ErpNextError(RuntimeError):
    """An ERPNext call failed. Raised so sync marks the message and retries."""

    #: The outbox reads this. False — the default — means the failure might
    #: pass: the site was down, the session had gone, someone had not run
    #: `fsmes erp setup` yet. Retrying with backoff is the right answer.
    permanent = False

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ErpNextRefused(ErpNextError):
    """ERPNext read the request, understood it, and said no.

    Retrying sends the identical bytes and gets the identical answer, so the
    outbox must not spend eight attempts and an hour finding that out. The
    thing that has to change is in ERPNext or on the shop floor, and a person
    changes it — which is what `MessageStatus.DEAD` has always meant.
    """

    permanent = True


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
            raise ErpNextError(
                f"{method} {path} -> HTTP {response.status_code}: {_detail(response)}",
                status_code=response.status_code,
            )
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

    def delete(self, doctype: str, name: str) -> None:
        """Used by the live test to take a custom field away again. The
        connector itself never deletes anything in the ERP."""
        self._request("DELETE", f"/api/resource/{doctype}/{_esc(name)}")

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


def _same(sent, returned) -> bool:
    """Did the ERP keep the value the MES sent?

    Deliberately forgiving about form and strict about substance. Frappe
    returns a Check field as 0/1 and a Float rounded to the site's float
    precision, which is two decimals on some sites, so a number that agrees
    to a hundredth agrees. Anything else is compared as text, where an empty
    string and a null are the same absence.
    """
    if isinstance(sent, (int, float)) and not isinstance(sent, bool):
        try:
            return math.isclose(float(sent), float(returned), rel_tol=1e-3, abs_tol=0.01)
        except (TypeError, ValueError):
            return False
    return str(sent or "") == str(returned or "")


_FRAPPE_EXCEPTION = re.compile(r"^[A-Za-z_.]*(?:Error|Exception):\s*")


def _message_of(error: ErpNextError) -> str:
    """Frappe's own sentence, without the transport wrapped around it.

    `POST /api/resource/Stock Entry -> HTTP 417: frappe.exceptions.Validation
    Error: For quantity 500.0 should not be greater than allowed quantity
    440.0` is what the client raises. Only the last clause of that means
    anything to the person reading a comment in ERPNext, and it is ERPNext's
    own wording, so it is repeated rather than paraphrased.
    """
    text = str(error)
    _, marker, tail = text.partition("-> HTTP ")
    if marker:
        _, _, text = tail.partition(": ")
    return _FRAPPE_EXCEPTION.sub("", text).strip() or str(error)


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
        company=settings.erpnext_company,
    )


class ErpNextAdapter(ErpConnector):
    """Transport only — every MES-side rule stays in services.erp."""

    def __init__(self, client: ErpNextClient, *, post_stock_entry: bool = True, company: str = ""):
        self.client = client
        self.post_stock_entry = post_stock_entry
        # Empty means every company on the site. A bench that serves more than
        # one company needs this set, or one company's MES runs another's
        # orders; a single-company site is right to leave it alone.
        self.company = company

    # ------------------------------------------------------------- the far side
    # `erpnext_setup` is imported inside these three because it imports this
    # module for its client.

    def requirements(self) -> list[Requirement]:
        from fsmes.integrations.erp import erpnext_setup

        return erpnext_setup.requirements()

    def setup(self) -> list[SetupOutcome]:
        from fsmes.integrations.erp import erpnext_setup

        return erpnext_setup.ensure_custom_fields(self.client)

    def check(self) -> CheckResult:
        from fsmes.integrations.erp import erpnext_setup

        return erpnext_setup.check(self.client, company=self.company)

    # ------------------------------------------------------------- inbound
    def fetch_orders(self) -> list[ProductionRequest]:
        filters = [
            ["docstatus", "=", 1],
            ["status", "in", list(_OPEN_STATUSES)],
            ["custom_mes_synced", "=", 0],
        ]
        if self.company:
            filters.append(["company", "=", self.company])
        rows = self.client.list(
            "Work Order",
            filters=filters,
            fields=["name", "production_item", "qty", "planned_end_date", "expected_delivery_date"],
            order_by="planned_start_date asc",
        )
        # No priority is sent: ERPNext Work Order has no priority field, and
        # inventing one here would outrank the MES's own dispatch ordering with
        # a number nobody set. services.erp applies its default instead.
        return [
            ProductionRequest.from_payload(
                {
                    "code": row["name"],
                    "material": row["production_item"],
                    "quantity": row.get("qty") or 0,
                    "due_date": row.get("planned_end_date") or row.get("expected_delivery_date"),
                    "erp_reference": row["name"],
                }
            )
            for row in rows
        ]

    def acknowledge(self, order_code: str) -> None:
        """Mark the order as taken, so the next poll does not re-import it."""
        self._write(order_code, {"custom_mes_synced": 1})

    # -------------------------------------------------------------- writes
    def _write(self, order: str, values: dict) -> None:
        """Write custom fields to a Work Order and read back what landed.

        Frappe accepts a PUT naming a field its doctype does not have. It
        answers 200 and the value is gone. Without this check the MES would
        record a delivered confirmation for a number the ERP never stored,
        which is the exact shape of wrong the house rules exist to prevent —
        so the returned document is compared against what was sent, and a
        field that did not survive is an error the outbox retries and a
        person eventually reads.
        """
        returned = self.client.update("Work Order", order, values)
        lost = [name for name in values if name not in returned]
        if lost:
            raise ErpNextError(
                f"ERPNext accepted the write to {order} but kept nothing in {', '.join(sorted(lost))}: "
                f"the field does not exist on Work Order. Run `fsmes erp setup` to create it."
            )
        changed = [
            f"{name} was sent as {values[name]!r} and came back as {returned[name]!r}"
            for name in values
            if not _same(values[name], returned[name])
        ]
        if changed:
            raise ErpNextError(f"ERPNext did not store what the MES sent to {order}: " + "; ".join(changed))

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
        self._write(
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

        try:
            doc = self.client.call(MAKE_STOCK_ENTRY, work_order_id=order, purpose="Manufacture", qty=remaining)
            if not isinstance(doc, dict):
                raise ErpNextError(f"ERPNext would not build a stock entry for {order}: {doc!r}")
            doc["docstatus"] = 1
            if payload.get("lot"):
                doc["remarks"] = f"MES-TWIN lot {payload['lot']}"
            entry = self.client.insert("Stock Entry", doc)
        except ErpNextError as exc:
            if exc.status_code != VALIDATION_REFUSED:
                raise
            raise self._refusal(order, remaining, payload, exc) from exc
        log.info("manufacture posted to ERPNext", order=order, qty=remaining, stock_entry=entry.get("name"))

    def _refusal(self, order: str, quantity: float, payload: dict, exc: ErpNextError) -> ErpNextRefused:
        """ERPNext refused to book the quantity. Say so where a person will see it.

        Measured on v15.120.0: a Manufacture entry for more than the Work
        Order's quantity plus the site's over-production allowance is refused
        whole — `For quantity 500.0 should not be greater than allowed
        quantity 440.0` — and ERPNext books nothing at all. Its own
        `produced_qty` stays where it was.

        Nothing partial is posted in its place. What the MES counted and what
        the ERP will accept genuinely differ, and the gap is a decision
        somebody at the plant has to make: raise the allowance, or account for
        the surplus another way. The MES's own record does not change, the
        confirmation is not delivered, and the comment below is what tells the
        person in ERPNext that a decision is waiting.
        """
        good = float(payload.get("good_qty") or 0)
        over = float(payload.get("over_qty") or 0)
        note = (
            f"MES-TWIN: ERPNext refused a Manufacture entry of {quantity:g}. "
            f"Nothing was booked here. The MES counted {good:g} good"
            + (f", {over:g} over the ordered quantity" if over else "")
            + (f", lot {payload['lot']}" if payload.get("lot") else "")
            + f". ERPNext said: {_message_of(exc)}"
        )
        try:
            self.client.comment("Work Order", order, note)
        except ErpNextError:
            # The refusal is the news; losing the note about it must not
            # replace ERPNext's own words with a comment API error.
            log.warning("could not leave the refusal comment", order=order)
        log.warning("ERPNext refused the confirmation", order=order, qty=quantity,
                    good=good, over=over, error=str(exc))
        return ErpNextRefused(
            f"ERPNext refused to book {quantity:g} on {order} and posted nothing: {_message_of(exc)}. "
            f"The MES counted {good:g} good"
            + (f" ({over:g} over the ordered quantity)" if over else "")
            + ". Somebody has to decide what the ERP should hold; the confirmation is not delivered.",
            status_code=exc.status_code,
        )
