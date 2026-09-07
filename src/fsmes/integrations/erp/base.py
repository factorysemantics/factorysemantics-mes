"""The ERP adapter contract, and the factory that picks one from settings.

An adapter is transport only: fetch production requests, acknowledge
receipt, deliver confirmations. The models it speaks are in `contract.py`;
all MES-side logic lives in services.erp, so adding an SAP/Odoo/NetSuite
adapter means implementing three methods against a typed contract.
"""

from importlib.metadata import entry_points
from typing import Protocol

from fsmes.config import Settings
from fsmes.integrations.erp.contract import Confirmation, ProductionRequest


class ErpAdapter(Protocol):
    def fetch_orders(self) -> list[ProductionRequest]:
        """New production requests from the ERP."""
        ...

    def acknowledge(self, order_code: str) -> None:
        """Tell the ERP an order was received (no-op where the transport implies it)."""
        ...

    def send_confirmation(self, confirmation: Confirmation) -> None:
        """Deliver one confirmation. Raise on failure - the outbox retries with backoff."""
        ...


def make_adapter(settings: Settings) -> ErpAdapter | None:
    if settings.erp_mode == "rest":
        from fsmes.integrations.erp.rest_adapter import RestErpAdapter

        return RestErpAdapter(settings.erp_base_url)
    if settings.erp_mode == "file":
        from fsmes.integrations.erp.file_adapter import FileErpAdapter

        return FileErpAdapter(settings.erp_inbox, settings.erp_outbox, settings.erp_archive)
    if settings.erp_mode == "off":
        return None
    # Every other mode is a module: a `fsmes.modules` entry point whose name is
    # the mode and whose target builds the adapter from settings. ERPNext ships
    # in this package and registers that way; a connector published on its own
    # (SAP, Odoo, NetSuite) plugs in identically.
    for ep in entry_points(group="fsmes.modules"):
        if ep.name == settings.erp_mode:
            return ep.load()(settings)
    known = ", ".join(sorted(ep.name for ep in entry_points(group="fsmes.modules")))
    raise ValueError(
        f"unknown ERP mode {settings.erp_mode!r} (built in: rest, file, off; installed modules: {known or 'none'})"
    )
